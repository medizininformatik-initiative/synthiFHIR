import json
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import app
import bulk_continuation as series
import generation
import standard_series
import store
import synthea_runtime
from test_generation import CATALOG
from test_queue import QueueFixture


class StandardSeriesTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)
        for mock in (patch.object(generation, 'catalogue', return_value=CATALOG),
                     patch.object(synthea_runtime, 'fingerprint', return_value='importer'),
                     patch.object(store, 'validate_configuration', return_value={'identifierCounterStarts': {'patient': 0}, 'formats': ['JSON', 'NDJSON']})):
            mock.start(); self.addCleanup(mock.stop)

    def create(self, **settings):
        return store.create('synthea-generation', 'default', 'CONFIGURATION_VERSION=1',
                            generation_settings={'population': 2, 'patientSeed': '41', **settings},
                            table_format='excel', diagnostics=True)

    def complete(self, job, patients, count):
        folder = store.ROOT / 'jobs' / job
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        source = folder / 'output/run-test/details/sources/synthea/fhir'
        source.mkdir(parents=True)
        for patient in patients:
            (source / (patient + '.json')).write_text(json.dumps({'entry': [{'resource': {'resourceType': 'Patient', 'id': patient}}]}))
        (folder / 'series-counters.json').write_text(json.dumps({'patient': count}))
        (folder / 'series-registrations.jsonl').write_text(''.join(json.dumps({'system': 'urn:test', 'value': str(count-len(patients)+i+1), 'owner': 'Patient/'+patient, 'rule': 'patient'})+'\n' for i,patient in enumerate(patients)))
        standard_series.complete(folder, snapshot)
        store.finish(job, 'succeeded', 0)

    def test_standard_chain_preserves_mode_settings_counters_and_actual_patient_count(self):
        job = self.create(overflow=True)
        self.complete(job, ['p1', 'p2', 'p3'], 7)
        self.assertTrue(store.get(job)['series_continuable'])
        preview = series.preview(job)
        self.assertEqual(3, preview['completedBefore'])
        self.assertEqual('excel', preview['tableFormat'])
        self.assertTrue(preview['diagnostics'])
        self.assertEqual('42', preview['generation']['patientSeed'])
        self.assertEqual('standard', preview['generation']['executionMode'])
        self.assertEqual({'patient': '8'}, preview['nextCounters'])
        child = series.create(job, 1, str(uuid4()))
        snapshot = json.loads((store.ROOT / 'jobs' / child / 'snapshot.json').read_text())
        self.assertEqual('excel', snapshot['tableFormat'])
        self.assertTrue(snapshot['diagnostics'])
        self.assertTrue(snapshot['generation']['overflow'])
        self.assertEqual(['JSON', 'NDJSON'], snapshot['formats'])
        self.assertEqual({'patient': 7}, snapshot['continuation']['identifierCounters'])
        self.complete(child, ['p4'], 8)
        self.assertEqual('43', series.preview(job)['generation']['patientSeed'])
        self.assertEqual(4, series.preview(job)['completedBefore'])
        self.assertEqual(job, store.get(child)['continuation']['sourceRun'])
        with self.assertRaises(store.SubmissionConflict): series.create(job, 1)
        with self.assertRaises(ValueError): series.create(child, 1001)

    def test_rejects_duplicate_source_patient_before_publishing_completion(self):
        job = self.create()
        self.complete(job, ['p1', 'p2'], 2)
        child = series.create(job, 1)
        with self.assertRaisesRegex(ValueError, 'already present'):
            self.complete(child, ['p1'], 3)
        self.assertFalse((store.ROOT / 'jobs' / child / series.STATE_FILE).exists())

    def test_rejects_identifier_collision_across_runs(self):
        job = self.create()
        self.complete(job, ['p1', 'p2'], 2)
        child = series.create(job, 1)
        with self.assertRaisesRegex(ValueError, 'Identifier collision'):
            self.complete(child, ['p3'], 2)
        self.assertFalse((store.ROOT / 'jobs' / child / series.STATE_FILE).exists())

    def test_missing_or_modified_completion_and_fixed_person_cannot_continue(self):
        job = self.create()
        store.finish(job, 'succeeded', 0)
        self.assertFalse(store.get(job)['series_continuable'])
        with self.assertRaises(store.SubmissionConflict): series.preview(job)
        self.complete(job, ['p1', 'p2'], 2)
        (store.ROOT / 'jobs' / job / 'series-patients.json').write_text('["changed"]')
        with self.assertRaises(store.SubmissionConflict): series.preview(job)
        fixed = self.create(population=1, singlePersonSeed='42')
        self.complete(fixed, ['fixed'], 1)
        self.assertFalse(store.get(fixed)['series_continuable'])
        with self.assertRaises(store.SubmissionConflict): series.preview(fixed)

    def test_legacy_standard_snapshot_is_not_silently_reconstructed(self):
        job = self.create()
        path = store.ROOT / 'jobs' / job / 'snapshot.json'
        snapshot = json.loads(path.read_text()); snapshot.pop('standardSeriesVersion')
        path.chmod(0o600); path.write_text(json.dumps(snapshot))
        store.finish(job, 'succeeded', 0)
        self.assertFalse(store.get(job)['series_continuable'])
        with self.assertRaises(store.SubmissionConflict): series.preview(job)
