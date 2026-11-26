import base64
import io
import json
from pathlib import Path
import sqlite3
import shutil
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import app
import resource_additions as additions
import store
from test_queue import QueueFixture


class AdditionTests(QueueFixture):
    def setUp(self):
        super().setUp()
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'input/FHIR_Testdatengenerator_Vorlage.xlsx',
                        store.APP / store.SOURCES['starter'])
        self.source = store.create('starter', 'default')
        store.finish(self.source, 'succeeded', 0)
        self.index = self.root / 'source.sqlite'
        with sqlite3.connect(self.index) as db:
            db.execute('CREATE TABLE resources (key TEXT PRIMARY KEY, kind TEXT, content TEXT)')
            db.execute('CREATE TABLE aliases (alias TEXT PRIMARY KEY, key TEXT)')
            resources = [
                {'resourceType': 'Patient', 'id': 'p'},
                {'resourceType': 'Patient', 'id': 'q'},
                {'resourceType': 'Encounter', 'id': 'e', 'subject': {'reference': 'urn:uuid:p'},
                 'period': {'start': '2024-01-02T12:00:00+01:00'}},
                {'resourceType': 'Encounter', 'id': 'f', 'subject': {'reference': 'urn:uuid:p'}},
                {'resourceType': 'Encounter', 'id': 'orphan', 'subject': {'reference': 'Patient/missing'}}]
            entries = [{'fullUrl': 'urn:uuid:' + r['id'], 'resource': r} for r in resources]
            additions.index_export(db, io.StringIO(json.dumps({'resourceType': 'Bundle', 'entry': entries})))
        self.fingerprint = 'a' * 64
        mocked = patch.object(additions, 'source_index', return_value=(self.index, self.fingerprint))
        mocked.start(); self.addCleanup(mocked.stop)

    def create(self, **values):
        return additions.create(self.source, additions.Settings(**values), self.fingerprint, str(uuid4()))

    def output(self, identifier):
        folder = store.ROOT / 'jobs' / identifier
        additions.execute(folder)
        return [json.loads(line) for line in (folder / 'output/run-additions/fhir/resources.ndjson').read_text().splitlines()]

    def test_document_generation_without_source_documents_uses_settings_and_existing_references(self):
        original = self.index.read_bytes()
        identifier = self.create(resourceType='DocumentReference', codingMode='replace', codings=[{'system': 'urn:new', 'code': 'new'}],
                                 nlpMode='replace', nlpCodes=['annotated', 'deid'], text='Testbericht', documentText='Neu erzeugter Inhalt äöü')
        resources = self.output(identifier)
        self.assertEqual(2, len(resources))
        self.assertEqual({'Encounter/e', 'Encounter/f'}, {r['context']['encounter'][0]['reference'] for r in resources})
        for resource in resources:
            self.assertEqual('Patient/p', resource['subject']['reference'])
            self.assertEqual('current', resource['status'])
            self.assertEqual('Testbericht', resource['content'][0]['attachment']['title'])
            self.assertEqual('Neu erzeugter Inhalt äöü', base64.b64decode(resource['content'][0]['attachment']['data']).decode())
            self.assertEqual([{'system': 'urn:new', 'code': 'new'}], resource['type']['coding'])
            self.assertEqual(['annotated', 'deid'], [c['code'] for c in resource['extension'][0]['valueCodeableConcept']['coding']])
        self.assertEqual('2024-01-02T12:00:00+01:00', resources[0]['date'])
        self.assertEqual(resources, self.output(identifier))
        self.assertTrue({r['id'] for r in resources}.isdisjoint(r['id'] for r in self.output(self.create(resourceType='DocumentReference'))))
        self.assertEqual(original, self.index.read_bytes())

    def test_patient_mode_generates_for_patients_without_contacts(self):
        resources = self.output(self.create(resourceType='DocumentReference', association='patient'))
        self.assertEqual({'Patient/p', 'Patient/q'}, {r['subject']['reference'] for r in resources})
        self.assertTrue(all('context' not in r for r in resources))
        for r in resources:
            self.assertIn(r['subject']['reference'], base64.b64decode(r['content'][0]['attachment']['data']).decode())

    def test_every_offered_type_can_be_generated_without_source_type(self):
        catalogue = additions.catalogue(self.source)
        self.assertEqual(set(additions.TYPES), {item['resourceType'] for item in catalogue['types']})
        for kind in additions.TYPES:
            with self.subTest(kind=kind):
                resources = self.output(self.create(resourceType=kind, association='patient', timestamp='2025-03-04T10:00:00Z', text='Neuer Testinhalt', value='Neuer Testwert'))
                self.assertEqual(2, len(resources))
                self.assertTrue(all(r['resourceType'] == kind for r in resources))
                field = 'patient' if kind in {'Consent', 'Immunization'} else 'subject'
                self.assertEqual({'Patient/p', 'Patient/q'}, {r[field]['reference'] for r in resources})
                self.assertTrue(all('identifier' not in r for r in resources))
        resource = self.output(self.create(resourceType='Observation', value='42 Testeinheiten', status='preliminary'))[0]
        self.assertEqual('42 Testeinheiten', resource['valueString'])
        self.assertEqual('preliminary', resource['status'])
        medication = self.output(self.create(resourceType='MedicationRequest', codingMode='replace', codings=[{'system': 'urn:test', 'code': 'med'}]))[0]
        self.assertEqual('med', medication['medicationCodeableConcept']['coding'][0]['code'])

    def test_preview_counts_retry_and_immutable_settings(self):
        catalogue = additions.catalogue(self.source)
        document = next(r for r in catalogue['types'] if r['resourceType'] == 'DocumentReference')
        self.assertEqual({'resources': 2, 'patients': 1, 'cases': 2, 'skipped': 1}, document['encounter'])
        self.assertEqual({'resources': 2, 'patients': 2, 'cases': 0, 'skipped': 0}, document['patient'])
        client = TestClient(app)
        body = {'requestId': str(uuid4()), 'fingerprint': self.fingerprint, 'settings': {'resourceType': 'DocumentReference', 'documentText': 'Gespeicherter Inhalt'}}
        first = client.post(f'/api/jobs/{self.source}/additions', json=body)
        self.assertEqual(201, first.status_code, first.text)
        second = client.post(f'/api/jobs/{self.source}/additions', json=body)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertFalse(first.json()['editor_available'])
        self.assertEqual(1, first.json()['addition']['generationVersion'])
        body['settings']['documentText'] = 'Änderung'
        self.assertEqual(409, client.post(f'/api/jobs/{self.source}/additions', json=body).status_code)

    def test_invalid_settings_stale_preview_and_orphan_contacts(self):
        for values in ({'resourceType':'Patient'}, {'resourceType':'Consent','association':'encounter'},
                       {'resourceType':'DocumentReference','status':'active'},
                       {'resourceType':'DocumentReference','timestamp':'2025-01-01'},
                       {'resourceType':'DocumentReference','timestamp':'2025-01-01T12:00:00'}):
            with self.assertRaises(ValueError): self.create(**values)
        with self.assertRaises(store.SubmissionConflict):
            additions.create(self.source, additions.Settings(resourceType='Observation'), 'b' * 64, str(uuid4()))
        with sqlite3.connect(self.index) as db:
            db.execute("DELETE FROM resources WHERE kind='Patient'")
            db.execute("DELETE FROM aliases WHERE key LIKE 'Patient/%'")
        with self.assertRaises(ValueError): self.create(resourceType='DocumentReference')

    def test_existing_clinical_resources_are_not_generation_templates(self):
        with sqlite3.connect(self.index) as db:
            additions.index_export(db, [json.dumps({'resourceType': 'DocumentReference', 'id':'old', 'subject':{'reference':'Patient/p'}, 'status':'superseded', 'content':[{'attachment':{'data':'T2xk'}}]})])
            self.assertEqual(0, db.execute("SELECT count(*) FROM resources WHERE kind='DocumentReference'").fetchone()[0])
        resources = self.output(self.create(resourceType='DocumentReference'))
        self.assertEqual(2, len(resources))
        self.assertTrue(all(r['status']=='current' for r in resources))
        self.assertTrue(all(base64.b64decode(r['content'][0]['attachment']['data']) != b'Old' for r in resources))

    def test_saved_copy_job_keeps_its_original_execution_semantics(self):
        identifier = self.create(resourceType='DocumentReference')
        folder = store.ROOT / 'jobs' / identifier
        source = folder / 'addition.ndjson'
        source.chmod(0o600)
        source.write_text(json.dumps({'resourceType':'DocumentReference','id':'old','status':'superseded',
                                     'subject':{'reference':'Patient/p'}, 'content':[{'attachment':{'data':'T2xk'}}]}) + '\n')
        snapshot_path = folder / 'snapshot.json'
        snapshot = json.loads(snapshot_path.read_text())
        snapshot['addition'].pop('generationVersion')
        snapshot['inputSha256'] = store.digest(source)
        snapshot_path.chmod(0o600)
        snapshot_path.write_text(json.dumps(snapshot))
        resources = self.output(identifier)
        self.assertEqual('superseded', resources[0]['status'])
        self.assertEqual('T2xk', resources[0]['content'][0]['attachment']['data'])
        self.assertNotEqual('old', resources[0]['id'])

    def test_addition_cleanup_preserves_input_results_and_provenance(self):
        import datasets, run_cleanup
        identifier = self.create(resourceType='DocumentReference')
        self.output(identifier)
        folder = store.ROOT / 'jobs' / identifier
        with patch.object(datasets, 'inspect', return_value={'patients': 0, 'resourceCounts': {'DocumentReference': 2}}):
            datasets.build(identifier, json.loads((folder / 'snapshot.json').read_text()))
        store.finish(identifier, 'succeeded', 0)
        run_cleanup.cleanup(identifier)
        self.assertTrue((folder / 'addition.ndjson').is_file())
        self.assertTrue((folder / 'output/run-additions/details/reports/additions.json').is_file())
        with store.run_lock(identifier), patch.object(store, 'run_lock', side_effect=AssertionError('Nested lock')):
            _, item, archive = datasets.resolve_dataset(identifier + '.0', already_locked=True)
        self.assertEqual(item['sha256'], store.digest(archive))


class BulkAdditionTests(QueueFixture):
    def test_published_bulk_formats_generate_documents_without_existing_documents(self):
        from test_bulk_uploads import StreamingTests
        import bulk_generation
        with patch.object(bulk_generation, 'OUTPUT_ROOT', self.root / 'bulk'):
            for layout, compressed in ((3, True), (5, True), (5, False)):
                with self.subTest(layout=layout, compressed=compressed):
                    resources = [{'resourceType':'Patient','id':'p'}, {'resourceType':'Encounter','id':'e','subject':{'reference':'Patient/p'}}]
                    dataset = StreamingTests.publish(self, resources, layout, compressed)
                    source = dataset.split('.')[0]
                    catalogue = additions.catalogue(source)
                    choice = next(item for item in catalogue['types'] if item['resourceType'] == 'DocumentReference')
                    self.assertEqual({'resources':1,'patients':1,'cases':1,'skipped':0}, choice['encounter'])
                    identifier = additions.create(source, additions.Settings(resourceType='DocumentReference'), catalogue['fingerprint'], str(uuid4()))
                    folder = store.ROOT / 'jobs' / identifier
                    additions.execute(folder)
                    output = [json.loads(line) for line in (folder / 'output/run-additions/fhir/resources.ndjson').read_text().splitlines()]
                    self.assertEqual(1, len(output))
                    self.assertEqual('Patient/p', output[0]['subject']['reference'])
                    self.assertEqual('Encounter/e', output[0]['context']['encounter'][0]['reference'])
                    self.assertTrue(output[0]['content'][0]['attachment']['data'])
