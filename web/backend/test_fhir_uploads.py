import json
from pathlib import Path
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
from api import app
import datasets
import fhir_uploads as uploads
import store
from test_queue import QueueFixture


class UploadTests(QueueFixture):
    def test_server_outcomes_report_partial_success_and_original_diagnostics(self):
        path = self.root / 'upload.log'
        path.write_text("""Status Codes     [code:count]             200:4, 409:7
Non-OK Responses:
File: /data/transactions/0000.ndjson [Bundle: 9]
    StatusCode  : 409
    Severity    : Error
    Code        : conflict
    Diagnostics : Referential integrity violated. Resource `Location/missing` doesn't exist.
""")
        result = uploads.server_responses(path)
        self.assertEqual(4, result['acceptedBundles'])
        self.assertEqual(7, result['rejectedBundles'])
        self.assertEqual(9, result['serverErrors'][0]['bundle'])
        self.assertIn('Location/missing', result['serverErrors'][0]['diagnostics'][0])
        path.write_text('connection refused')
        self.assertEqual({}, uploads.server_responses(path))

    def exported(self, name, resources):
        path = self.root / name
        path.write_text(json.dumps({'resourceType': 'Bundle', 'entry': [
            {'resource': r, 'request': {'method': 'POST', 'url': r['resourceType']}} for r in resources]}) + '\n')
        return name, path

    def patient(self, gender='female'):
        return {'resourceType': 'Patient', 'id': 'same', 'gender': gender}

    def test_conflicts_across_and_within_datasets_stop_preparation(self):
        for number, selection in enumerate([
            [self.exported('one', [self.patient()]), self.exported('two', [self.patient('male')])],
            [self.exported('three', [self.patient(), self.patient('male')])],
        ]):
            destination = self.root / str(number) / 'transactions'
            destination.parent.mkdir()
            with self.assertRaises(uploads.Conflict) as error:
                uploads.prepare_transactions(selection, destination)
            self.assertEqual('Patient/same', error.exception.details['resource'])

    def test_identical_duplicates_and_put_ids_and_exact_decimal(self):
        first = self.exported('one', [self.patient(), self.patient()])
        result = uploads.prepare_transactions([first], self.root / 'transactions')
        self.assertEqual({'bundles': 1, 'resources': 1}, result)
        bundle = json.loads(next((self.root / 'transactions').glob('*.ndjson')).read_text())
        self.assertEqual(1, len(bundle['entry']))
        self.assertEqual({'method': 'PUT', 'url': 'Patient/same'}, bundle['entry'][0]['request'])
        from decimal import Decimal
        self.assertEqual('{"value":0.1234567890123456789}', uploads.encode({'value': Decimal('0.1234567890123456789')}))

    def test_same_ids_in_separate_actions_are_allowed(self):
        for index, gender in enumerate(['female', 'male']):
            directory = self.root / str(index)
            directory.mkdir()
            uploads.prepare_transactions([self.exported(f'patient{index}', [self.patient(gender)])], directory / 'transactions')

    def test_missing_ids_and_empty_selection_rejected(self):
        for number, resources in enumerate([[{'resourceType': 'Patient'}], []]):
            directory = self.root / str(number)
            directory.mkdir()
            with self.assertRaises(ValueError):
                uploads.prepare_transactions([self.exported(str(number) + '.ndjson', resources)], directory / 'transactions')

    def publish(self):
        job = store.create('starter', 'default')
        root = store.ROOT / 'jobs' / job
        data = root / 'output/run-example/fhir'
        data.mkdir(parents=True)
        (data / 'patient.json').write_text(json.dumps(self.patient()))
        with patch.object(datasets, 'inspect', return_value={'patients': 1}):
            manifest = datasets.build(job, json.loads((root / 'snapshot.json').read_text()))
        store.finish(job, 'succeeded', 0)
        return manifest['datasets'][0]['id']

    def test_api_submission_retry_cancel_and_restart(self):
        client = TestClient(app)
        dataset = self.publish()
        body = {'requestId': str(uuid.uuid4()), 'target': 'hapi', 'datasets': [dataset]}
        for _ in range(2):
            result = client.post('/api/fhir-uploads', json=body)
            self.assertEqual(202, result.status_code)
        self.assertEqual(1, len(uploads.history()))
        self.assertEqual(1, result.json()['descriptor']['target']['concurrency'])
        self.assertEqual(409, client.post('/api/fhir-uploads', json={**body, 'target': 'blaze'}).status_code)
        uploads.cancel(body['requestId'])
        self.assertIsNone(uploads.claim())
        second = uploads.create(str(uuid.uuid4()), 'blaze', [dataset])
        self.assertEqual(second['id'], uploads.claim())
        uploads.finish(second['id'], 'uploading', {'mayHaveWritten': True})
        uploads.recover()
        self.assertEqual('interrupted', uploads.get(second['id'])['state'])
        self.assertTrue(uploads.get(second['id'])['result']['mayHaveWritten'])

    def test_archive_tampering_fails_before_network_and_external_targets_rejected(self):
        dataset = self.publish()
        with self.assertRaises(ValueError):
            uploads.create(str(uuid.uuid4()), 'http://example.org', [dataset])
        action = uploads.create(str(uuid.uuid4()), 'blaze', [dataset])
        _, _, archive = datasets.resolve_dataset(dataset)
        archive.write_bytes(b'changed')
        with patch.object(uploads, 'target_status') as status, patch.object(uploads.subprocess, 'Popen') as process:
            uploads.execute(action['id'], lambda: False, lambda _: None)
        status.assert_not_called()
        process.assert_not_called()
        result = uploads.get(action['id'])
        self.assertEqual('failed', result['state'])
        self.assertFalse(result['result']['mayHaveWritten'])

    def test_history_pages_keep_all_active_and_delete_only_completed_local_files(self):
        client = TestClient(app)
        identifiers = []
        for index in range(9):
            identifier = str(uuid.uuid4())
            identifiers.append(identifier)
            with store.connect() as db:
                db.execute('INSERT INTO uploads(id,state,created,descriptor) VALUES (?,?,?,?)',
                           (identifier, 'queued' if index == 0 else 'succeeded', index, '{}'))
            uploads.folder(identifier).mkdir(parents=True)
            (uploads.folder(identifier) / 'upload.log').write_text('local log')
        page = client.get('/api/fhir-uploads').json()
        self.assertEqual(6, len(page['items']))
        self.assertEqual(identifiers[0], page['items'][-1]['id'])
        self.assertTrue(page['hasMore'])
        self.assertEqual(8, page['completedCount'])
        self.assertEqual(9, len(client.get('/api/fhir-uploads?limit=10').json()['items']))
        self.assertEqual(422, client.get('/api/fhir-uploads?limit=0').status_code)
        self.assertEqual(409, client.delete('/api/fhir-uploads/' + identifiers[0]).status_code)
        with patch.object(uploads.fhir_servers, 'http_json') as network:
            self.assertEqual(204, client.delete('/api/fhir-uploads/' + identifiers[1]).status_code)
            self.assertFalse(uploads.folder(identifiers[1]).exists())
            self.assertEqual(404, client.delete('/api/fhir-uploads/' + identifiers[1]).status_code)
            self.assertEqual(204, client.delete('/api/fhir-uploads').status_code)
            network.assert_not_called()
        self.assertEqual([identifiers[0]], [item['id'] for item in uploads.history()])
        self.assertTrue((uploads.folder(identifiers[0]) / 'upload.log').exists())
        self.assertFalse(uploads.folder(identifiers[-1]).exists())
