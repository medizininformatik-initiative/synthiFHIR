import json
from unittest.mock import patch
import zipfile
from fastapi.testclient import TestClient
from api import app
import datasets
import store
import result_exports
from test_queue import QueueFixture


class DatasetTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        self.job = store.create('starter', 'default')
        self.folder = store.ROOT / 'jobs' / self.job
        self.run = self.folder / 'output/run-example'
        for name in ['One', 'Two']:
            data = self.run / 'fhir' / name
            data.mkdir(parents=True)
            (data / 'patient.json').write_text(json.dumps({'resourceType': 'Patient', 'id': name}))
            (self.run / 'details/options/input.xlsx' / name).mkdir(parents=True)
        report = self.run / 'details/reports/input.import.json'
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({'status': 'COMPLETE', 'issues': [{'category': 'REFERENCE', 'reason': 'missing'}], 'outputSelections': [{}]}))

    def build(self):
        with patch.object(datasets, 'inspect', return_value={'patients': 1, 'uniqueResources': 2}):
            return datasets.build(self.job, json.loads((self.folder / 'snapshot.json').read_text()))

    def test_variants_have_separate_archives_and_reports_are_downloadable(self):
        manifest = self.build()
        store.finish(self.job, 'succeeded', 0)
        self.assertEqual(['One', 'Two'], [item['name'] for item in manifest['datasets']])
        for index, name in enumerate(['One', 'Two']):
            response = self.client.get(f'/api/datasets/{self.job}.{index}/download')
            self.assertEqual(200, response.status_code)
            with zipfile.ZipFile(self.folder / f'dataset-{index}.zip') as archive:
                self.assertEqual([ 'patient.json' ], archive.namelist())
                self.assertEqual(name, json.loads(archive.read('patient.json'))['id'])
        reports = [item for item in manifest['artifacts'] if item['report']]
        self.assertEqual(1, reports[0]['report']['issues'])
        response = self.client.get(f"/api/jobs/{self.job}/artifacts/{reports[0]['id']}")
        self.assertEqual(200, response.status_code)
        self.assertEqual('COMPLETE', response.json()['status'])
        self.assertEqual(2, len(self.client.get('/api/datasets').json()))

    def test_host_exports_contain_only_dataset_zips_and_are_removed_with_run(self):
        self.build()
        store.finish(self.job, 'succeeded', 0)
        snapshot = json.loads((self.folder / 'snapshot.json').read_text())
        destination = result_exports.path(snapshot)
        files = sorted(destination.iterdir())
        self.assertEqual(2, len(files))
        for index, file in enumerate(files):
            self.assertTrue(file.name.endswith('-1Pat.zip'))
            self.assertEqual((self.folder / f'dataset-{index}.zip').read_bytes(), file.read_bytes())
        self.assertEqual(result_exports.label(snapshot), store.get(self.job)['output_directory'])
        store.delete(self.job)
        self.assertFalse(destination.exists())

    def test_downloads_require_terminal_jobs_and_manifest_membership(self):
        manifest = self.build()
        self.assertEqual(404, self.client.get(f'/api/datasets/{self.job}.0/download').status_code)
        self.assertEqual(409, self.client.get(f"/api/jobs/{self.job}/artifacts/{manifest['artifacts'][0]['id']}").status_code)
        store.finish(self.job, 'succeeded', 0)
        self.assertEqual(404, self.client.get(f'/api/jobs/{self.job}/artifacts/unknown').status_code)
        self.assertEqual(404, self.client.get(f'/api/datasets/{self.job}.99/download').status_code)
        path = self.run / manifest['artifacts'][0]['path'].removeprefix('run-example/')
        path.unlink()
        path.symlink_to(store.APP / 'synthifhir.jar')
        self.assertEqual(404, self.client.get(f"/api/jobs/{self.job}/artifacts/{manifest['artifacts'][0]['id']}").status_code)

    def test_finished_runs_are_indexed_without_changing_snapshots(self):
        original = (self.folder / 'snapshot.json').read_bytes()
        store.finish(self.job, 'succeeded', 0)
        with patch.object(datasets, 'inspect', return_value={'patients': 1}):
            datasets.backfill(lambda: False)
        self.assertEqual(original, (self.folder / 'snapshot.json').read_bytes())
        self.assertEqual(2, len(datasets.get(self.job)['datasets']))
        with patch.object(datasets, 'build') as build:
            datasets.backfill(lambda: False)
        build.assert_not_called()

    def test_cancelled_inspection_does_not_publish_partial_manifest(self):
        with self.assertRaises(datasets.CancelledInspection):
            datasets.build(self.job, {}, lambda: True)
        self.assertFalse((self.folder / 'datasets.json').exists())
