"""Downloads use persisted counts and send payloads in bounded chunks."""
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import zipfile

import api
import store
from test_queue import QueueFixture


class DownloadStreamingTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.job = store.create('starter', 'default', dataset_name='Large cohort')
        self.folder = store.ROOT / 'jobs' / self.job
        self.payload = self.folder / 'output/run-example/fhir/patients.ndjson'
        self.payload.parent.mkdir(parents=True)
        line = json.dumps({'resourceType': 'Patient', 'id': 'same', 'text': {'div': 'x' * 4096}}).encode() + b'\n'
        with self.payload.open('wb') as stream:
            for _ in range(2048):
                stream.write(line)
        for name in ['result.zip', 'dataset-0.zip']:
            with zipfile.ZipFile(self.folder / name, 'w') as archive:
                archive.write(self.payload, self.payload.relative_to(self.folder))
        self.item = {'id': self.job + '.0', 'name': 'default', 'inspection': {'patients': 1},
                     'files': [], 'size': (self.folder / 'dataset-0.zip').stat().st_size}
        self.manifest([self.item])
        store.finish(self.job, 'succeeded', 0)

    def manifest(self, items):
        (self.folder / 'datasets.json').write_text(json.dumps({'datasets': items, 'artifacts': []}))

    def check_response(self, response, expected_count):
        disposition = response.headers['content-disposition']
        if expected_count is None:
            self.assertNotIn('Pat.zip', disposition)
            self.assertIn('Large-cohort.zip', disposition)
        else:
            self.assertIn(f'Large-cohort-{expected_count}Pat.zip', disposition)
        path = Path(response.path)
        expected_size = path.stat().st_size
        expected_hash = store.digest(path)
        actual_hash = hashlib.sha256()
        size = 0
        chunks = 0
        async def send(message):
            nonlocal size, chunks
            if message['type'] == 'http.response.body':
                body = message.get('body', b'')
                self.assertLessEqual(len(body), 64 * 1024)
                actual_hash.update(body)
                size += len(body)
                chunks += 1
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        asyncio.run(response({'type': 'http', 'method': 'GET', 'headers': []}, receive, send))
        self.assertEqual(expected_size, size)
        self.assertEqual(expected_hash, actual_hash.hexdigest())
        self.assertGreater(chunks, 0)
        return chunks

    def test_large_run_and_dataset_downloads_use_saved_counts_without_reading_members(self):
        original = Path.read_bytes
        def no_payload_read(path):
            self.assertNotEqual(path, self.payload, 'FHIR payload must not be materialized')
            return original(path)
        with patch.object(zipfile.ZipFile, 'read', side_effect=AssertionError('No ZIP member reads during download')), \
             patch.object(Path, 'read_bytes', no_payload_read):
            self.assertGreater(self.check_response(api.download(self.job), 1), 100)
            self.assertGreater(self.check_response(api.dataset_download(self.item['id']), 1), 100)

    def test_missing_zero_and_overlapping_counts_do_not_trigger_payload_scans(self):
        with patch.object(zipfile.ZipFile, 'read', side_effect=AssertionError('No payload fallback')):
            self.item.pop('inspection')
            self.manifest([self.item])
            self.check_response(api.download(self.job), None)
            self.check_response(api.dataset_download(self.item['id']), None)
            self.item['inspection'] = {'patients': 0}
            self.manifest([self.item])
            self.check_response(api.download(self.job), 0)
            self.check_response(api.dataset_download(self.item['id']), 0)
            self.item['inspection'] = {'patients': 1}
            self.manifest([self.item, {**self.item, 'id': self.job + '.1'}])
            self.check_response(api.download(self.job), None)
            (self.folder / 'datasets.json').unlink()
            self.check_response(api.download(self.job), None)

    def test_cleaned_run_rebuilds_archive_without_rescanning_fhir_for_its_name(self):
        (self.folder / 'result.zip').unlink()
        (self.folder / 'cleanup.json').write_text(json.dumps({
            'completed': 1, 'removedBytes': 0, 'removedFiles': 1,
            'files': [{'path': self.payload.relative_to(self.folder).as_posix()}]}))
        with patch.object(zipfile.ZipFile, 'read', side_effect=AssertionError('No member reads')):
            response = api.download(self.job)
            path = Path(response.path)
            self.check_response(response, 1)
            self.assertFalse(path.exists(), 'Temporary archive is removed after streaming')
