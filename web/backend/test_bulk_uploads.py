import gzip
import io
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import zipfile

from fastapi.testclient import TestClient
from api import app
import bulk_archives
import bulk_generation
import bulk_uploads
import datasets
import fhir_uploads as uploads
import store
from test_queue import QueueFixture


class StreamingTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.output = self.root / 'mass'
        patcher = patch.object(bulk_generation, 'OUTPUT_ROOT', self.output)
        patcher.start(); self.addCleanup(patcher.stop)

    def publish(self, resources=None, layout=3, compressed=True):
        job = store.create('starter', 'default')
        folder = store.ROOT / 'jobs' / job
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        snapshot.update(bulkOutputName=job, bulkOutputDirectory='outputGlobal/' + job,
                        generation={'bulkGzip': compressed, 'executionMode': 'bulk', 'population': 2, 'patientsPerRun': 100, 'patientSeed': '1'}, bulkLayoutVersion=layout)
        (folder / 'snapshot.json').chmod(0o600)
        (folder / 'snapshot.json').write_text(json.dumps(snapshot))
        root = bulk_archives.output_path(snapshot)
        part = root / 'run-00001/part-00001'; part.mkdir(parents=True)
        resources = resources or [{'resourceType': 'Patient', 'id': 'one'}, {'resourceType': 'Patient', 'id': 'two'}]
        for index, resource in enumerate(resources):
            (part / f'p{index:03d}.json.gz').write_bytes(gzip.compress(json.dumps({'resourceType': 'Bundle', 'type': 'transaction', 'entry': [{'resource':resource}]}).encode()))
        suffix = ('.ndjson.gz' if compressed else '.ndjson') if layout >= 5 else '.zip'
        bulk_archives.package(part.parent, [part.name], root / ('packages/run-00001-package-00001' + suffix), lambda *_: None)
        bulk_archives.publish(folder, snapshot)
        store.finish(job, 'succeeded', 0)
        return datasets.get(job)['datasets'][0]['id']

    def test_failed_upload_resumes_after_last_ack_without_extracting_packages(self):
        dataset = self.publish()
        client = TestClient(app)
        action = uploads.create(str(uuid4()), 'blaze', [dataset])
        identifier = action['id']
        self.assertEqual(identifier, uploads.claim())
        sent = []
        fail = [True]
        def send(url, bundle):
            pid = bundle['entry'][0]['resource']['id']; sent.append(pid)
            self.assertEqual({'method':'PUT','url':'Patient/'+pid}, bundle['entry'][0]['request'])
            if pid == 'two' and fail[0]: raise OSError('connection lost')
        with patch.object(uploads, 'target_status', return_value={'available': True}), patch.object(bulk_uploads, 'send', side_effect=send), patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('Bulk must not extract')):
            uploads.execute(identifier, lambda: False, lambda _: None)
            result = uploads.get(identifier)
            self.assertEqual('failed', result['state']); self.assertTrue(result['resumable'])
            self.assertEqual(1, result['result']['acceptedBundles'])
            self.assertEqual(202, client.post(f'/api/fhir-uploads/{identifier}/resume').status_code)
            self.assertEqual(identifier, uploads.claim()); fail[0] = False
            uploads.execute(identifier, lambda: False, lambda _: None)
        self.assertEqual(['one','two','two'], sent)
        result = uploads.get(identifier)
        self.assertEqual('succeeded', result['state']); self.assertEqual(2, result['result']['acceptedBundles'])
        self.assertEqual(1, result['result']['completedPackages'])
        self.assertFalse(list(uploads.folder(identifier).rglob('*.json.gz')))
        self.assertFalse(list(uploads.folder(identifier).rglob('*.zip')))
        self.assertFalse((uploads.folder(identifier)/'resource-index.sqlite').exists())
        self.assertEqual(1,len(uploads.history()))

    def test_conflicting_resources_prevent_all_network_writes(self):
        first = self.publish([{'resourceType':'Patient','id':'same','gender':'female'}])
        second = self.publish([{'resourceType':'Patient','id':'same','gender':'male'}])
        action = uploads.create(str(uuid4()), 'blaze', [first,second])
        with patch.object(bulk_uploads, 'send') as send:
            uploads.execute(action['id'], lambda: False, lambda _: None)
        send.assert_not_called()
        result = uploads.get(action['id'])
        self.assertEqual('conflict',result['state'])
        self.assertFalse(result['result']['mayHaveWritten'])

    def test_package_changes_are_rejected_before_upload_and_download_uses_original(self):
        dataset = self.publish()
        action = uploads.create(str(uuid4()), 'blaze', [dataset])
        job,item,path = datasets.resolve_dataset(dataset)
        self.assertTrue(path.is_relative_to(self.root / 'outputGlobal'))
        response = TestClient(app).get('/api/datasets/'+dataset+'/download')
        self.assertEqual(path.read_bytes(),response.content)
        path.write_bytes(b'changed')
        with patch.object(bulk_uploads, 'send') as send:
            uploads.execute(action['id'],lambda:False,lambda _:None)
        send.assert_not_called()
        self.assertEqual('failed', uploads.get(action['id'])['state'])

    def test_transaction_response_must_confirm_every_resource(self):
        bundle = {'entry':[{'resource':{'resourceType':'Patient','id':'one'}}]}
        for response, succeeds in [({'resourceType':'Bundle','type':'transaction-response','entry':[{'response':{'status':'200 OK'}}]},True),
                                   ({'resourceType':'OperationOutcome'},False),
                                   ({'resourceType':'Bundle','type':'transaction-response','entry':[{'response':{'status':'409 Conflict'}}]},False)]:
            with patch.object(bulk_uploads.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
                if succeeds: bulk_uploads.send('http://blaze/fhir',bundle)
                else:
                    with self.assertRaisesRegex(RuntimeError,'did not confirm'):bulk_uploads.send('http://blaze/fhir',bundle)

    def test_cancelled_before_upload_resumes_and_keeps_manual_target(self):
        dataset = self.publish(); action=uploads.create(str(uuid4()),'hapi',[dataset])
        uploads.cancel(action['id']); self.assertIsNone(uploads.claim())
        resumed=uploads.resume(action['id'])
        self.assertEqual('hapi',resumed['descriptor']['target']['id'])
        self.assertEqual('queued',resumed['state'])

    def test_ndjson_streaming_download_and_cancellation(self):
        for compressed in (True, False):
            identifier = self.publish(layout=5, compressed=compressed)
            job, item, path = datasets.resolve_dataset(identifier)
            with patch.object(bulk_uploads, 'source', return_value=path):
                values = list(bulk_uploads.bundles(item, self.root, None, None, lambda: False))
            self.assertEqual(['one', 'two'], [value['entry'][0]['resource']['id'] for value in values])
            with patch.object(bulk_uploads, 'source', return_value=path), self.assertRaises(InterruptedError):
                list(bulk_uploads.bundles(item, self.root, None, None, lambda: True))
            response = TestClient(app).get('/api/datasets/' + identifier + '/download')
            self.assertEqual(200, response.status_code)
            self.assertIn('.ndjson.gz' if compressed else '.ndjson', response.headers['content-disposition'])
            self.assertEqual(path.read_bytes(), response.content)
