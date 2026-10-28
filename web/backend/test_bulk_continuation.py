import concurrent.futures
import json
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from api import app
import bulk_continuation as series
from bulk_identifiers import Registry
import generation
import store
import synthea_runtime
from test_generation import CATALOG
from test_queue import QueueFixture


class ContinuationTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        for mock in (patch.object(generation, 'catalogue', return_value=CATALOG),
                     patch.object(synthea_runtime, 'fingerprint', return_value='importer'),
                     patch('bulk_resources.require_available'),
                     patch.object(store, 'validate_configuration', return_value={'identifierCounterStarts': {'patient': 0, 'case': 4}})):
            mock.start(); self.addCleanup(mock.stop)
        self.job = store.create('synthea-generation', 'default', 'CONFIGURATION_VERSION=1',
                                generation_settings={'executionMode': 'bulk', 'population': 100, 'patientsPerRun': 100,
                                                     'patientSeed': '1000', 'clinicianSeed': '99'})
        self.complete(self.job, {'patient': 100, 'case': 1734})

    def complete(self, job, counters):
        folder = store.ROOT / 'jobs' / job
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        registry = Registry(folder / 'tmp.sqlite')
        registry.save(folder / 'bulk-identifier-history.sqlite'); registry.close()
        (folder / series.STATE_FILE).write_text(json.dumps(series.completion(folder, snapshot, counters)))
        store.finish(job, 'succeeded', 0)

    def test_preview_and_submission_restore_real_counts_and_frozen_settings(self):
        result = self.client.get(f'/api/jobs/{self.job}/continuation').json()
        self.assertEqual({'patient': '101', 'case': '1735'}, result['nextCounters'])
        self.assertEqual(('1001', '99'), (result['generation']['patientSeed'], result['generation']['clinicianSeed']))
        payload = {'population': 50000, 'requestId': str(uuid4())}
        path = f'/api/jobs/{self.job}/continuation'
        response = self.client.post(path, json=payload)
        self.assertEqual(201, response.status_code, response.text)
        job = response.json()
        saved = json.loads((store.ROOT / 'jobs' / job['id'] / 'snapshot.json').read_text())
        self.assertEqual({'patient': 100, 'case': 1734}, saved['continuation']['identifierCounters'])
        self.assertEqual(50000, saved['generation']['population'])
        self.assertEqual(100, saved['generation']['patientsPerRun'])
        self.assertEqual(job['id'], self.client.post(path, json=payload).json()['id'])
        self.assertEqual(409, self.client.post(path, json={'population': 100}).status_code)
        for changed in ({'patientSeed': '1'}, {'generation': {'maxAge': 120}}, {'configurationProperties': 'CONFIGURATION_VERSION=1'}, {'identifierCounters': {}}):
            self.assertEqual(422, self.client.post(path, json={**payload, **changed}).status_code)
        self.complete(job['id'], {'patient': 50100, 'case': 900000})
        tip = series.preview(self.job)
        self.assertEqual(job['id'], tip['sourceRun'])
        self.assertEqual('1501', tip['generation']['patientSeed'])
        self.assertEqual(50100, tip['completedBefore'])

    def test_concurrent_submissions_cannot_fork_and_deleted_child_remains_reserved(self):
        def submit():
            try: return series.create(self.job, 100, str(uuid4()))
            except store.SubmissionConflict: return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(1, len([item for item in results if item]))
        child = next(item for item in results if item)
        store.cancel(child); store.delete(child)
        with self.assertRaises(store.SubmissionConflict): series.create(self.job, 100)

    def test_rejects_missing_changed_counters_versions_and_incomplete_predecessors(self):
        folder = store.ROOT / 'jobs' / self.job
        state = folder / series.STATE_FILE
        original = state.read_text()
        for value in ({}, {**json.loads(original), 'identifierCounters': {}},
                      {**json.loads(original), 'completed': 99}):
            state.write_text(json.dumps(value))
            self.assertEqual(409, self.client.get(f'/api/jobs/{self.job}/continuation').status_code)
        state.write_text(original)
        with patch.object(generation, 'catalogue', return_value={**CATALOG, 'sha256': 'changed'}):
            with self.assertRaisesRegex(store.SubmissionConflict, 'changed'): series.create(self.job, 100)
        self.assertEqual(1, len(store.jobs()))
        store.finish(self.job, 'failed', 1)
        self.assertEqual(409, self.client.get(f'/api/jobs/{self.job}/continuation').status_code)

    def test_requires_saved_completion_for_partial_runs(self):
        job = store.create('synthea-generation', 'default', generation_settings={'executionMode': 'bulk', 'population': 275})
        store.finish(job, 'succeeded', 0)
        with self.assertRaisesRegex(store.SubmissionConflict, 'completion counters'): series.preview(job)

    def test_partial_continuation_copies_checked_sources_and_ignores_changed_defaults(self):
        import bulk_sources
        job = store.create('synthea-generation', 'default', 'CONFIGURATION_VERSION=1',
                           generation_settings={'executionMode': 'bulk', 'population': 275, 'patientSeed': '1000', 'maxAge': 90})
        folder = store.ROOT / 'jobs' / job
        records = [{'name': f'{i}.json', 'patient': True, 'id': str(i)} for i in range(75, 100)]
        bulk_sources.write(folder / bulk_sources.REMAINDER,
                           {'seed': '1002', 'offset': 75, 'patients': records, 'support': []},
                           ((r['name'], '{}') for r in records))
        self.complete(job, {'patient': 275, 'case': 4500})
        (store.APP / 'defaults.config').write_text('CONFIGURATION_VERSION=1\nIDENTIFIER_RULE_1_PATTERN=changed')
        preview = series.preview(job)
        self.assertEqual((75, '1002', '276'), (preview['sourceOffset'], preview['generation']['patientSeed'], preview['nextCounters']['patient']))
        child = series.create(job, 25)
        following = store.ROOT / 'jobs' / child
        snapshot = json.loads((following / 'snapshot.json').read_text())
        self.assertEqual(90, snapshot['generation']['maxAge'])
        self.assertEqual('CONFIGURATION_VERSION=1', (following / 'default.config').read_text())
        self.assertEqual((folder / bulk_sources.REMAINDER).read_bytes(), (following / bulk_sources.INHERITED).read_bytes())
        (folder / bulk_sources.REMAINDER).write_bytes(b'changed')
        self.assertEqual(25, bulk_sources.description(following / bulk_sources.INHERITED)['count'])
        with self.assertRaisesRegex(store.SubmissionConflict, 'completion counters'):
            series.state(job)

    def test_renamed_run_keeps_snapshot_downloads_and_continuation(self):
        import gzip
        import bulk_archives
        import datasets
        import result_exports
        folder = store.ROOT / 'jobs' / self.job
        original = (folder / 'snapshot.json').read_bytes()
        snapshot = json.loads(original)
        root = result_exports.path(snapshot)
        part = folder / 'part-00001'
        part.mkdir()
        (part / 'patient.json.gz').write_bytes(gzip.compress(b'{"resourceType":"Bundle","type":"transaction","entry":[{"resource":{"resourceType":"Patient","id":"test"}}]}'))
        archive = root / 'packages/run-00001-package-00001.ndjson.gz'
        bulk_archives.package(folder, [part.name], archive, lambda *_: None)
        bulk_archives.publish(folder, snapshot)
        item, = datasets.get(self.job)['datasets']
        original_zip = archive.read_bytes()
        result_exports.rename_bulk(self.job, 'synthiFHIR')
        self.assertEqual(original, (folder / 'snapshot.json').read_bytes())
        self.assertFalse(root.exists())
        new_root = result_exports.path(snapshot)
        self.assertFalse((new_root / 'packages').exists())
        self.assertEqual(original_zip, (new_root / archive.name).read_bytes())
        response = self.client.get('/api/datasets/' + item['id'] + '/download')
        self.assertEqual(200, response.status_code)
        self.assertEqual(original_zip, response.content)
        self.assertEqual('synthiFHIR', store.get(self.job)['dataset_name'])
        self.assertEqual('synthiFHIR', series.preview(self.job)['datasetName'])
        child = series.create(self.job, 100)
        self.assertEqual('synthiFHIR', store.get(child)['dataset_name'])
        self.assertTrue(new_root.name.endswith(f'_synthiFHIR_100Pat_{UUID(self.job).hex[:8]}'))
        child_snapshot = json.loads((store.ROOT / 'jobs' / child / 'snapshot.json').read_text())
        self.assertTrue(child_snapshot['resultOutputName'].endswith(f'_synthiFHIR_100Pat_{UUID(self.job).hex[:8]}(2)'))
        self.complete(child, {'patient': 200, 'case': 3000})
        grandchild = series.create(child, 200)
        third_snapshot = json.loads((store.ROOT / 'jobs' / grandchild / 'snapshot.json').read_text())
        self.assertTrue(third_snapshot['resultOutputName'].endswith(f'_synthiFHIR_200Pat_{UUID(self.job).hex[:8]}(3)'))
        self.assertEqual(3, result_exports.series_number(third_snapshot))
        legacy = dict(child_snapshot)
        legacy['continuation'] = {k: v for k, v in legacy['continuation'].items() if k != 'seriesNumber'}
        self.assertEqual(2, result_exports.series_number(legacy))
        result_exports.metadata_path(legacy).write_text(json.dumps({'seriesNumber': 2}))
        store.delete(self.job)
        self.assertFalse(new_root.exists())
        self.assertEqual(2, result_exports.series_number(legacy))
        self.assertFalse(result_exports.metadata_path(snapshot).exists())
        self.complete(grandchild, {'patient': 400, 'case': 5000})
        self.assertEqual(grandchild, series.preview(child)['sourceRun'])
        self.assertEqual(3, result_exports.series_number(third_snapshot))
