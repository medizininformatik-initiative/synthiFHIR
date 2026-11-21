from fastapi.testclient import TestClient
from unittest.mock import patch
import json
import io
import zipfile

from api import app
import store
from test_queue import QueueFixture


class ApiTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)

    def test_submission_snapshot_and_cancel(self):
        result = self.client.post('/api/jobs', json={'source': 'starter'})
        self.assertEqual(201, result.status_code)
        job_id = result.json()['id']
        snapshot = self.client.get(f'/api/jobs/{job_id}/snapshot')
        self.assertEqual('starter', snapshot.json()['source'])
        self.assertEqual(409, self.client.get(f'/api/jobs/{job_id}/download').status_code)
        self.assertEqual(202, self.client.post(f'/api/jobs/{job_id}/cancel').status_code)
        self.assertEqual('cancelled', self.client.get('/api/jobs').json()[0]['state'])
        self.assertEqual(snapshot.content, self.client.get(f'/api/jobs/{job_id}/snapshot').content)

    def test_failed_run_download_requires_published_archive(self):
        job_id = store.create('starter', 'default')
        store.finish(job_id, 'failed', 1)
        self.assertEqual(409, self.client.get(f'/api/jobs/{job_id}/download').status_code)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('output/run-test/fhir/p.json', json.dumps({'resourceType': 'Patient', 'id': 'p'}))
        (store.ROOT / 'jobs' / job_id / 'result.zip').write_bytes(buffer.getvalue())
        (store.ROOT / 'jobs' / job_id / 'datasets.json').write_text(json.dumps(
            {'datasets': [{'inspection': {'patients': 1}}], 'artifacts': []}))
        self.assertTrue(self.client.get('/api/jobs').json()[0]['download_available'])
        response = self.client.get(f'/api/jobs/{job_id}/download')
        self.assertEqual(buffer.getvalue(), response.content)
        self.assertIn('synthifhir-1Pat-', response.headers['content-disposition'])

    def test_input_configuration_source_is_not_a_start_option(self):
        response = self.client.post('/api/jobs', json={'profile': 'workbook'})
        self.assertEqual(422, response.status_code)
        self.assertEqual([], store.jobs())

    def test_rejects_untrusted_origin_host_and_unknown_input(self):
        self.assertEqual(403, self.client.post('/api/jobs', json={}, headers={'Origin': 'https://other.invalid'}).status_code)
        self.assertEqual(400, self.client.get('/api/jobs', headers={'Host': 'other.invalid'}).status_code)
        self.assertEqual(422, self.client.post('/api/jobs', json={'source': '/etc/passwd'}).status_code)
        self.assertEqual(422, self.client.post('/api/jobs', json={'command': 'echo injected'}).status_code)
        self.assertEqual(404, self.client.get('/api/jobs/not-a-uuid/snapshot').status_code)
        self.assertEqual([], store.jobs())

    def test_editor_settings_are_validated_and_captured_per_job(self):
        text = "CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=XML\nTIME_SHIFT_ENABLED=true\nTIME_SHIFT_BASE_DAYS=1\n"
        metadata = {"formats": ["XML"], "validation": False, "patientsPerFile": 2}
        with patch.object(store, 'validate_configuration', return_value=metadata) as validate:
            result = self.client.post('/api/jobs', json={'source': 'starter', 'configurationProperties': text})
        self.assertEqual(201, result.status_code)
        job_id = result.json()['id']
        path = store.ROOT / 'jobs' / job_id / 'default.config'
        validate.assert_called_once_with(path)
        snapshot = self.client.get(f'/api/jobs/{job_id}/snapshot').json()
        self.assertEqual(text, snapshot['profile']['optionsProperties'])
        self.assertEqual('editor', snapshot['profile']['id'])
        self.assertEqual(['XML'], snapshot['formats'])
        self.assertEqual(2, snapshot['patientsPerFile'])
        self.assertEqual(text, path.read_text())
        with patch.object(store, 'validate_configuration', side_effect=ValueError('Invalid encounter policy')):
            rejected = self.client.post('/api/jobs', json={'configurationProperties': 'bad'})
        self.assertEqual(422, rejected.status_code)
        self.assertEqual('Invalid encounter policy', rejected.json()['detail'])
        self.assertEqual(1, len(store.jobs()))
        self.assertEqual([job_id], [p.name for p in (store.ROOT / 'jobs').iterdir()])
