"""Worker-image smoke test: external authenticated uploads, real export and resumable packages."""
import base64
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import uuid

sys.path.insert(0, '/app')
import bulk_archives
import bulk_generation
import datasets
import fhir_servers
import fhir_uploads
import result_exports
import store
import worker

received = {}
calls = []
fail_next = False
expected = 'Basic ' + base64.b64encode(b'probe:integration-password').decode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value):
        self.send_response(status)
        self.send_header('Content-Type', 'application/fhir+json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def authorized(self):
        if self.headers.get('Authorization') == expected:
            return True
        self.respond(401, {'resourceType': 'OperationOutcome'})
        return False

    def do_GET(self):
        if not self.authorized():
            return
        if self.path.endswith('/metadata'):
            self.respond(200, {'resourceType': 'CapabilityStatement', 'fhirVersion': '4.0.1', 'rest': [
                {'mode': 'server', 'interaction': [{'code': 'transaction'}],
                 'resource': [{'type': 'Patient', 'interaction': [{'code': 'search-type'}]}]}]})
        else:
            self.respond(200, {'resourceType': 'Bundle', 'type': 'searchset', 'entry': [{'resource': resource} for resource in received.values()]})

    def do_POST(self):
        global fail_next
        raw = self.rfile.read(int(self.headers['Content-Length']))
        if not self.authorized():
            return
        bundle = json.loads(raw)
        if fail_next:
            fail_next = False
            self.respond(503, {'resourceType': 'OperationOutcome'})
            return
        for entry in bundle['entry']:
            resource = entry['resource']
            assert entry['request'] == {'method': 'PUT', 'url': resource['resourceType'] + '/' + resource['id']}
            received[resource['id']] = resource
            calls.append(resource['id'])
        self.respond(200, {'resourceType': 'Bundle', 'type': 'transaction-response', 'entry': [{'response': {'status': '200 OK'}} for _ in bundle['entry']]})


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with tempfile.TemporaryDirectory() as temporary:
        store.ROOT = Path(temporary) / 'workbench'
        result_exports.ROOT = Path(temporary) / 'output'
        bulk_generation.OUTPUT_ROOT = result_exports.ROOT
        profile = fhir_servers.save({'name': 'External integration', 'address': f'http://127.0.0.1:{server.server_port}/fhir',
                                     'auth': 'basic', 'username': 'probe', 'password': 'integration-password'})
        assert fhir_servers.check(profile['id'])['available']

        def publish(bulk=False):
            job = store.create('starter', 'default')
            folder = store.ROOT / 'jobs' / job
            snapshot = json.loads((folder / 'snapshot.json').read_text())
            bundles = [{'resourceType': 'Bundle', 'type': 'transaction', 'entry': [{'resource': {'resourceType': 'Patient', 'id': f'probe-{index}', 'gender': 'male' if bulk else 'female'}}]} for index in range(2)]
            if bulk:
                snapshot.update(bulkOutputName=job, bulkOutputDirectory='outputGlobal/' + job,
                                generation={'bulkGzip': True, 'executionMode': 'bulk', 'population': 2, 'patientsPerRun': 100, 'patientSeed': '1'}, bulkLayoutVersion=5)
                (folder / 'snapshot.json').chmod(0o600)
                (folder / 'snapshot.json').write_text(json.dumps(snapshot))
                root = bulk_archives.output_path(snapshot)
                part = root / 'run-00001/part-00001'
                part.mkdir(parents=True)
                for index, bundle in enumerate(bundles):
                    (part / f'p{index}.json.gz').write_bytes(gzip.compress(json.dumps(bundle).encode()))
                bulk_archives.package(part.parent, [part.name], root / 'packages/run-00001-package-00001.ndjson.gz', lambda *_: None)
                bulk_archives.publish(folder, snapshot)
            else:
                output = folder / 'output/run-probe/fhir'
                output.mkdir(parents=True)
                for index, bundle in enumerate(bundles):
                    (output / f'p{index}.json').write_text(json.dumps(bundle))
                (output / 'patients.ndjson').write_text('\n'.join(json.dumps(bundle) for bundle in bundles))
                datasets.build(job, snapshot)
            store.finish(job, 'succeeded', 0)
            return datasets.get(job)['datasets'][0]['id']

        def execute(selection):
            action = fhir_uploads.create(str(uuid.uuid4()), profile['id'], selection)
            assert 'integration-password' not in json.dumps(action)
            fhir_uploads.execute(action['id'], lambda: False, worker.terminate)
            assert 'integration-password' not in fhir_uploads.logs(action['id'])
            return fhir_uploads.get(action['id'])

        ordinary = publish()
        first = execute([ordinary])
        assert first['state'] == 'succeeded', first
        assert first['result']['acceptedBundles'] == 2, first
        assert len(calls) == 2, 'JSON and NDJSON must not be uploaded twice'
        assert all(resource['gender'] == 'female' for resource in received.values())
        bulk = publish(bulk=True)
        conflict = execute([ordinary, bulk])
        assert conflict['state'] == 'conflict' and len(calls) == 2
        fail_next = True
        failed = execute([bulk])
        assert failed['state'] == 'failed' and failed['resumable']
        fhir_uploads.resume(failed['id'])
        fhir_uploads.execute(failed['id'], lambda: False, worker.terminate)
        resumed = fhir_uploads.get(failed['id'])
        assert resumed['state'] == 'succeeded', resumed
        resources = fhir_servers.request(profile['id'], 'Patient')['entry']
        assert len(resources) == 2 and all(entry['resource']['gender'] == 'male' for entry in resources)
        print('PASS: external Basic Auth; real ordinary export; format deduplication; bulk NDJSON; conflict before writes; failed upload resumed; authenticated readback; secret-free logs and descriptors')
finally:
    server.shutdown()
    server.server_close()
    thread.join()
