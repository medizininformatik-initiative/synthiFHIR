import json
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import app
import configurations
import generation
import store
import synthea_runtime
import worker
from test_queue import QueueFixture

CATALOG = {'revision': 'revision', 'sha256': 'generator', 'locations': {'Massachusetts': ['Boston']},
           'modules': [{'id': 'diabetes'}], 'keepModules': [{'id': 'keep_diabetes.json'}]}


class GenerationTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        for item in [patch.object(generation, 'catalogue', return_value=CATALOG),
                     patch.object(synthea_runtime, 'fingerprint', return_value='importer')]:
            item.start(); self.addCleanup(item.stop)

    def test_consent_is_optional_saved_and_requires_kds(self):
        self.assertTrue(generation.Settings().syntheticConsent)
        self.assertFalse(generation.Settings(outputMode='synthea').syntheticConsent)
        self.assertFalse(generation.Settings(syntheticConsent=False).syntheticConsent)
        job = store.create('synthea-generation', 'default', generation_settings={'syntheticConsent': True})
        self.assertTrue(store.editor_input(job)['generation']['syntheticConsent'])
        with self.assertRaises(ValueError):
            generation.Settings(outputMode='synthea', syntheticConsent=True)

    def test_table_format_snapshot_repeat_and_request_identity(self):
        for table_format in ('csv', 'excel'):
            request = {'source': 'synthea-generation', 'generation': {}, 'tableFormat': table_format, 'requestId': str(uuid4())}
            response = self.client.post('/api/jobs', json=request)
            self.assertEqual(201, response.status_code, response.text)
            job = response.json()['id']
            self.assertEqual(table_format, store.editor_input(job)['tableFormat'])
            self.assertEqual(table_format, store.get(job)['table_format'])
            self.assertEqual(409, self.client.post('/api/jobs', json={**request, 'tableFormat': 'excel' if table_format == 'csv' else 'csv'}).status_code)
        default = store.create('synthea-generation', 'default', generation_settings={})
        self.assertEqual('csv', store.editor_input(default)['tableFormat'])
        for request in ({'tableFormat': 'csv'}, {'source': 'synthea-generation', 'generation': {'outputMode': 'synthea'}, 'tableFormat': 'csv'}, {'tableFormat': 'invalid'}):
            self.assertEqual(422, self.client.post('/api/jobs', json=request).status_code)

    def test_diagnostics_default_repeat_and_request_identity(self):
        request = {'source': 'synthea-generation', 'generation': {}, 'requestId': str(uuid4())}
        response = self.client.post('/api/jobs', json=request)
        self.assertEqual(201, response.status_code, response.text)
        self.assertFalse(store.editor_input(response.json()['id'])['diagnostics'])
        self.assertEqual(409, self.client.post('/api/jobs', json={**request, 'diagnostics': True}).status_code)
        response = self.client.post('/api/jobs', json={**request, 'requestId': str(uuid4()), 'diagnostics': True})
        self.assertEqual(201, response.status_code, response.text)
        self.assertTrue(store.editor_input(response.json()['id'])['diagnostics'])
        self.assertEqual(422, self.client.post('/api/jobs', json={'diagnostics': True}).status_code)

    def test_inpatient_selection_is_validated_and_only_enabled_explicitly(self):
        for invalid in [0, -1, True, 1.5, 36501]:
            with self.assertRaises(ValueError):
                generation.Settings(inpatientMinDays=invalid)
        defaults = generation.arguments({})
        self.assertFalse(any('workbench_inpatient' in arg for arg in defaults))
        args = generation.arguments({'inpatientOnly': True, 'inpatientMinDays': 7, 'yearsOfHistory': 10, 'maxAttempts': 4})
        self.assertIn('--generate.workbench_inpatient=true', args)
        self.assertIn('--generate.workbench_inpatient_days=7', args)
        self.assertIn('--generate.max_attempts_to_keep_patient=4', args)
        self.assertIn('--exporter.years_of_history=10', args)

    def test_invalid_settings_do_not_create_jobs(self):
        for settings in [{'population': 0}, {'population': True}, {'minAge': 81}, {'patientSeed': str(2**63)},
                         {'state': '--some-option'}, {'city': 'Unknown'}, {'modules': ['../../file']},
                         {'keepModule': '/tmp/file'}, {'patientFilter': 'unknown'}, {'unknown': True},
                         {'population': 2, 'singlePersonSeed': '1'}, {'endDate': '2099-01-01'},
                         {'referenceDate': '2026-09-13', 'endDate': '2026-09-12'}]:
            response = self.client.post('/api/jobs', json={'source': 'synthea-generation', 'generation': settings})
            self.assertEqual(422, response.status_code, response.text)
        self.assertEqual([], store.jobs())

    @patch.object(store, 'validate_configuration', return_value={'formats': ['JSON'], 'validation': False, 'patientsPerFile': 1})
    def test_fixed_settings_are_snapshotted_and_repeat_preserves_them(self, validate):
        settings = generation.normalize({'patientSeed': '-9223372036854775808', 'clinicianSeed': '20260912', 'modules': ['diabetes']})
        request = {'source': 'synthea-generation', 'generation': settings, 'requestId': str(uuid4())}
        response = self.client.post('/api/jobs', json=request)
        self.assertEqual(201, response.status_code, response.text)
        job = response.json()['id']
        self.assertEqual(job, self.client.post('/api/jobs', json=request).json()['id'])
        folder = store.ROOT / 'jobs' / job
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        self.assertEqual(settings, snapshot['generation'])
        self.assertEqual('generator', snapshot['syntheaGeneratorSha256'])
        store.cancel(job)
        loaded = store.editor_input(job)
        repeated = store.create(loaded['source'], 'default', None if (loaded.get('generation') or {}).get('outputMode') == 'synthea' else loaded['configurationProperties'], generation_settings=loaded.get('generation'))
        self.assertEqual((folder / 'generation.json').read_bytes(), (store.ROOT / 'jobs' / repeated / 'generation.json').read_bytes())
        self.assertEqual(settings, store.editor_input(repeated)['generation'])
        for saved_job in (job, repeated):
            saved = json.loads((store.ROOT / 'jobs' / saved_job / 'generation.json').read_text())
            args = generation.arguments(saved)
            self.assertEqual(settings['patientSeed'], args[args.index('-s') + 1])
            self.assertEqual(settings['clinicianSeed'], args[args.index('-cs') + 1])

    def test_loading_generation_does_not_start_a_run(self):
        job = store.create('synthea-generation', 'default', generation_settings={})
        loaded = store.editor_input(job)
        self.assertEqual('synthea-generation', loaded['source'])
        self.assertEqual(1, len(store.jobs()))
        self.assertEqual(generation.normalize({}), loaded['generation'])

    def test_arguments_keep_paths_and_export_destinations_out_of_user_settings(self):
        values = generation.normalize({'modules': ['diabetes'], 'keepModule': 'keep_diabetes.json',
                                       'patientFilter': 'alive', 'city': 'Boston'})
        args = generation.arguments(values)
        self.assertIn('--generate.only_alive_patients=true', args)
        self.assertIn('--generate.only_dead_patients=false', args)
        self.assertEqual(['-m', 'diabetes'], args[args.index('-m'):args.index('-m') + 2])
        self.assertEqual(['Massachusetts', 'Boston'], args[-2:])
        self.assertNotIn('-c', args)
        with self.assertRaises(ValueError):
            store.create('starter', 'default', generation_settings={})

    def test_native_output_ignores_kds_defaults_and_repeats_without_importer(self):
        with patch.object(synthea_runtime, 'fingerprint', side_effect=AssertionError('Importer must not be used')):
            job = store.create('synthea-generation', 'default', generation_settings={'outputMode': 'synthea'})
            folder = store.ROOT / 'jobs' / job
            snapshot = json.loads((folder / 'snapshot.json').read_text())
            self.assertEqual('synthea', snapshot['profile']['id'])
            self.assertNotIn('syntheaImportSha256', snapshot)
            self.assertFalse((folder / 'default.config').exists())
            store.cancel(job)
            loaded = store.editor_input(job)
            repeated = store.create(loaded['source'], 'default', None if (loaded.get('generation') or {}).get('outputMode') == 'synthea' else loaded['configurationProperties'], generation_settings=loaded.get('generation'))
            self.assertEqual((folder / 'generation.json').read_bytes(),
                             (store.ROOT / 'jobs' / repeated / 'generation.json').read_bytes())
            self.assertEqual('synthea', store.get(repeated)['configuration']['id'])
        with self.assertRaisesRegex(ValueError, 'does not use a KDS configuration'):
            store.create('synthea-generation', 'default', 'CONFIGURATION_VERSION=1\n', generation_settings={'outputMode': 'synthea'})

    def test_native_worker_exports_original_resources_and_counts_only_patients(self):
        import datasets
        jar = self.root / 'runtime/target/synthea.jar'
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b'generator')
        catalogue = {**CATALOG, 'sha256': store.digest(jar)}
        with patch.object(generation, 'catalogue', return_value=catalogue), patch.object(synthea_runtime, 'ROOT', jar.parent.parent):
            job = store.create('synthea-generation', 'default', generation_settings={'outputMode': 'synthea'})
            store.claim()
            folder = store.ROOT / 'jobs' / job
            def launch(command, **kwargs):
                self.assertEqual(['java', '-Xmx1536m'], command[:2])
                self.assertIn(str(jar), command)
                self.assertNotIn('--converter-options', command)
                self.assertIn('--exporter.practitioner.fhir.export=true', command)
                fhir = folder / 'output/run-synthea/fhir'
                fhir.mkdir()
                for name, resource in [('patient', {'resourceType': 'Patient', 'id': 'p', 'name': [{'family': 'Original'}]}),
                                       ('clinician', {'resourceType': 'Practitioner', 'id': 'c'})]:
                    (fhir / (name + '.json')).write_text(json.dumps({'resourceType': 'Bundle', 'entry': [{'resource': resource}]}))
                process = Mock(returncode=0)
                process.poll.return_value = 0
                return process
            with patch.object(worker.subprocess, 'Popen', side_effect=launch), patch.object(datasets, 'inspect', return_value={'uniquePatients': 1}):
                worker.execute(job)
            self.assertEqual('succeeded', store.get(job)['state'], (folder / 'converter.log').read_text())
            self.assertEqual({'generatedPatients': 1, 'importedPatients': 0, 'failedPatients': 0}, store.get(job)['generation_result'])
            import zipfile
            with zipfile.ZipFile(folder / 'dataset-0.zip') as archive:
                self.assertEqual({'patient.json', 'clinician.json'}, set(archive.namelist()))
                self.assertIn('Original', archive.read('patient.json').decode())
