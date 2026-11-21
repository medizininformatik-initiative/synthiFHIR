import io
import json
import zipfile
from unittest.mock import patch

from fastapi.testclient import TestClient
from api import app
import datasets
import fhir_downloads
import store
from test_queue import QueueFixture


class FhirDownloadTests(QueueFixture):
    def completed(self):
        job = store.create('starter', 'default')
        store.finish(job, 'succeeded', 0)
        root = store.ROOT / 'jobs' / job
        manifest = {'datasets': [], 'artifacts': []}
        for index in range(2):
            base = f'run-test/fhir/variant-{index}'
            files = []
            for subdir in ['patient-a', 'patient-b']:
                relative = f'{base}/{subdir}/bundle.json'
                path = root / 'output' / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'resourceType': 'Bundle', 'id': f'{index}-{subdir}'}))
                files.append({'id': datasets.file_id(relative), 'path': relative, 'size': path.stat().st_size})
            report = root / 'output' / base / 'resource-statistics.txt'
            report.write_text('not FHIR')
            files.append({'id': datasets.file_id(base + '/resource-statistics.txt'), 'path': base + '/resource-statistics.txt', 'size': 8})
            manifest['datasets'].append({'id': f'{job}.{index}', 'name': str(index), 'path': base, 'files': files})
            manifest['artifacts'].extend(files)
        (root / 'datasets.json').write_text(json.dumps(manifest))
        return job, root

    def test_all_variants_flatten_without_losing_same_named_files(self):
        job, root = self.completed()
        response = TestClient(app).get(f'/api/jobs/{job}/fhir-download')
        self.assertEqual(200, response.status_code)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(4, len(archive.namelist()))
            self.assertEqual(4, len(set(archive.namelist())))
            self.assertTrue(all(len(name.split('/')) == 2 for name in archive.namelist()))
            actual = {archive.read(name) for name in archive.namelist()}
            expected = {path.read_bytes() for path in (root / 'output').rglob('bundle.json')}
            self.assertEqual(expected, actual)
            self.assertIsNone(archive.testzip())

    def test_missing_or_escaped_file_fails_before_download(self):
        job, root = self.completed()
        target = next((root / 'output').rglob('bundle.json'))
        target.unlink()
        target.symlink_to(root / 'snapshot.json')
        self.assertEqual(404, TestClient(app).get(f'/api/jobs/{job}/fhir-download').status_code)

    def test_bulk_gzip_bytes_are_preserved(self):
        job, root = self.completed()
        package = root / 'test.ndjson.gz'
        import gzip
        package.write_bytes(gzip.compress(b'{"resourceType":"Bundle"}\n'))
        item = {'id': f'{job}.0', 'kind': 'bulk'}
        with patch.object(datasets, 'get', return_value={'datasets': [item]}), patch.object(datasets, 'resolve_dataset', return_value=({}, item, package)):
            entries = fhir_downloads.plan(job)
        with zipfile.ZipFile(io.BytesIO(b''.join(fhir_downloads.stream(job, entries)))) as archive:
            self.assertEqual(package.read_bytes(), archive.read(archive.namelist()[0]))

    def test_active_run_cannot_be_downloaded(self):
        job = store.create('starter', 'default')
        self.assertEqual(409, TestClient(app).get(f'/api/jobs/{job}/fhir-download').status_code)

    def test_legacy_bulk_members_stream_without_nested_archives(self):
        job, root = self.completed()
        package = root / 'legacy.zip'
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('part-1/patient.json.gz', b'first compressed payload')
            archive.writestr('part-2/patient.json.gz', b'second compressed payload')
        entries = [(package, 'part-1/patient.json.gz', '000001-patient.json.gz'),
                   (package, 'part-2/patient.json.gz', '000002-patient.json.gz')]
        with zipfile.ZipFile(io.BytesIO(b''.join(fhir_downloads.stream(job, entries)))) as archive:
            self.assertEqual(b'first compressed payload', archive.read(entries[0][2]))
            self.assertEqual(b'second compressed payload', archive.read(entries[1][2]))

    def test_source_download_retains_original_bytes(self):
        job, root = self.completed()
        original = (root / 'input.xlsx').read_bytes()
        response = TestClient(app).get(f'/api/jobs/{job}/source-download')
        self.assertEqual(200, response.status_code)
        self.assertEqual(original, response.content)
        (root / 'input.xlsx').unlink()
        (root / 'input.xlsx').symlink_to(root / 'snapshot.json')
        self.assertEqual(404, TestClient(app).get(f'/api/jobs/{job}/source-download').status_code)
