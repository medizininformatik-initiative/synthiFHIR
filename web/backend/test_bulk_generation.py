import json
from uuid import uuid4
from unittest.mock import patch
from fastapi.testclient import TestClient
from api import app
import generation
import store
import synthea_runtime
import bulk_generation
from test_generation import CATALOG
from test_queue import QueueFixture


class BulkTests(QueueFixture):
    def setUp(self):
        super().setUp()
        import bulk_resources
        memory = patch.object(bulk_resources, 'status', return_value={'ready': True})
        memory.start()
        self.addCleanup(memory.stop)

    def test_mode_is_explicit_and_saved_with_effective_execution(self):
        with patch.object(generation, 'catalogue', return_value=CATALOG), patch.object(synthea_runtime, 'fingerprint', return_value='importer'):
            client = TestClient(app)
            request = {'source': 'synthea-generation', 'generation': {'population': 2000, 'executionMode': 'bulk'}, 'requestId': str(uuid4())}
            response = client.post('/api/jobs', json=request)
            self.assertEqual(201, response.status_code, response.text)
            job = response.json()
            saved = json.loads((store.ROOT / 'jobs' / job['id'] / 'snapshot.json').read_text())
            self.assertEqual(['JSONGZIP'], saved['formats'])
            self.assertFalse(saved['validation'])
            self.assertFalse(saved['diagnostics'])
            self.assertTrue(job['bulk_output_directory'].endswith(job['id'].replace('-', '')[:8]))
            self.assertEqual('bulk', store.editor_input(job['id'])['generation']['executionMode'])
            self.assertEqual(job['id'], client.post('/api/jobs', json=request).json()['id'])
            self.assertEqual(409, client.post('/api/jobs', json={**request, 'generation': {'population': 2}}).status_code)
            for changes in ({'tableFormat': 'excel'}, {'diagnostics': True}, {'generation': {'population': 2000}},
                            {'generation': {'executionMode': 'bulk', 'overflow': True}}):
                self.assertEqual(422, client.post('/api/jobs', json={**request, 'requestId': str(uuid4()), **changes}).status_code)

    def test_native_bulk_submission_has_no_kds_configuration(self):
        with patch.object(generation, 'catalogue', return_value=CATALOG):
            client = TestClient(app)
            settings = {'population': 2000, 'executionMode': 'bulk', 'outputMode': 'synthea'}
            response = client.post('/api/jobs', json={'source': 'synthea-generation', 'generation': settings})
            self.assertEqual(201, response.status_code, response.text)
            folder = store.ROOT / 'jobs' / response.json()['id']
            snapshot = json.loads((folder / 'snapshot.json').read_text())
            self.assertEqual('synthea', snapshot['profile']['id'])
            self.assertFalse((folder / 'default.config').exists())
            self.assertEqual('synthea', store.editor_input(response.json()['id'])['generation']['outputMode'])
            self.assertEqual(['JSONGZIP'], snapshot['formats'])
            with self.assertRaises(ValueError):
                generation.Settings(**settings, syntheticConsent=True)

    def test_seeds_are_distinct_reproducible_and_bounded(self):
        settings = generation.Settings(executionMode='bulk', population=10000, patientSeed=str(2**63-1)).model_dump(mode='json')
        first = bulk_generation.chunk_settings(settings, 0, 100)
        second = bulk_generation.chunk_settings(settings, 1, 100)
        self.assertEqual(str(2**63-1), first['patientSeed'])
        self.assertEqual(str(-2**63), second['patientSeed'])
        self.assertEqual(second, bulk_generation.chunk_settings(settings, 1, 100))
        generation.Settings.model_validate(second)
        self.assertEqual(10000, settings['population'])

    def test_process_failure_is_not_silently_accepted(self):
        import sys
        with self.assertRaisesRegex(RuntimeError, 'Process failed'):
            bulk_generation.checked_process([sys.executable, '-c', 'raise SystemExit(7)'], self.root / 'failed.log')

    def test_output_names_are_portable_bounded_and_identifiable(self):
        job_id = str(uuid4())
        name = bulk_generation.output_directory_name(job_id, '../Stufe 3: Ä Ö Ü ß / \\ ?*<>|"', 0)
        self.assertTrue(name.startswith('19700101-010000_Stufe-3-A-O-U-ss_'))
        self.assertTrue(name.endswith(job_id.replace('-', '')))
        self.assertRegex(name, r'^[A-Za-z0-9_-]+$')
        self.assertLess(len(bulk_generation.output_directory_name(job_id, 'a' * 1000, 0)), 75)
        self.assertIn('_Massendaten_', bulk_generation.output_directory_name(job_id, '...???', 0))
        self.assertNotEqual(name, bulk_generation.output_directory_name(str(uuid4()), '../Stufe 3: Ä Ö Ü ß /', 0))

    def test_compact_output_names_preserve_contents_and_refuse_collisions(self):
        folder = self.root / 'outputs'
        folder.mkdir()
        (folder / 'long-b.json.gz').write_bytes(b'b')
        (folder / 'long-a.json.gz').write_bytes(b'a')
        bulk_generation.compact_output_names(folder)
        self.assertEqual(b'a', (folder / 'p001.json.gz').read_bytes())
        self.assertEqual(b'b', (folder / 'p002.json.gz').read_bytes())
        with self.assertRaises(FileExistsError):
            bulk_generation.compact_output_names(folder)
        self.assertEqual(2, len(list(folder.iterdir())))
