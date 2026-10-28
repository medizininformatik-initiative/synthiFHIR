import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store
import result_exports


class QueueFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        exports = patch.object(result_exports, 'ROOT', self.root / 'outputGlobal')
        exports.start()
        self.addCleanup(exports.stop)
        self.patch = patch.multiple(store, ROOT=self.root / 'data', APP=self.root / 'converter')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        store.APP.mkdir()
        (store.APP / 'input').mkdir()
        (store.APP / store.SOURCES['starter']).write_bytes(b'workbook')
        (store.APP / 'defaults.config').write_text('CHECK_INPUT_CONSISTENCY=true')
        (store.APP / 'synthifhir.jar').write_bytes(b'converter')


class QueueTests(QueueFixture):
    def test_duration_excludes_waiting_and_finishes_at_completion(self):
        with patch.object(store.time, 'time', return_value=10):
            job = store.create('starter', 'default')
        self.assertIsNone(store.get(job)['duration_seconds'])
        with patch.object(store.time, 'time', return_value=20):
            self.assertEqual(job, store.claim())
        with patch.object(store.time, 'time', return_value=25):
            self.assertEqual(5, store.get(job)['duration_seconds'])
            store.finish(job, 'failed', 1)
        self.assertEqual(5, store.get(job)['duration_seconds'])

    def test_delete_preserves_upload_history_and_blocks_active_work(self):
        import json
        import uuid
        job = store.create('starter', 'default', request_id=str(uuid.uuid4()))
        with self.assertRaises(store.SubmissionConflict):
            store.delete(job)
        store.finish(job, 'succeeded', 0)
        upload_id = str(uuid.uuid4())
        descriptor = json.dumps({'datasets': [{'jobId': job}]})
        with store.connect() as db:
            db.execute('INSERT INTO uploads(id,state,created,descriptor) VALUES (?,?,?,?)', (upload_id, 'uploading', 1, descriptor))
        with self.assertRaises(store.SubmissionConflict):
            store.delete(job)
        with store.connect() as db:
            db.execute("UPDATE uploads SET state='succeeded' WHERE id=?", (upload_id,))
        store.delete(job)
        self.assertIsNone(store.get(job))
        self.assertFalse((store.ROOT / 'jobs' / job).exists())
        with store.connect() as db:
            self.assertEqual(descriptor, db.execute('SELECT descriptor FROM uploads WHERE id=?', (upload_id,)).fetchone()[0])

    def test_delete_removes_empty_renamed_export_and_keeps_other_runs(self):
        import json
        job = store.create('starter', 'default')
        snapshot = json.loads((store.ROOT / 'jobs' / job / 'snapshot.json').read_text())
        metadata = result_exports.metadata_path(snapshot)
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({'resultOutputName': 'renamed-run'}))
        destination = result_exports.path(snapshot)
        destination.mkdir(parents=True)
        other = result_exports.ROOT / 'other-run'
        other.mkdir()
        (other / 'keep.txt').write_text('keep')
        store.finish(job, 'cancelled', 1)
        store.delete(job)
        self.assertFalse(destination.exists())
        self.assertFalse(metadata.exists())
        self.assertEqual('keep', (other / 'keep.txt').read_text())

    def test_snapshots_are_independent_of_source_and_profile_changes(self):
        first = store.create('starter', 'default')
        original = (store.ROOT / 'jobs' / first / 'snapshot.json').read_bytes()
        (store.APP / 'defaults.config').write_text('changed')
        (store.APP / store.SOURCES['starter']).write_bytes(b'changed')
        second = store.create('starter', 'default')
        self.assertNotEqual(first, second)
        self.assertEqual(original, (store.ROOT / 'jobs' / first / 'snapshot.json').read_bytes())
        self.assertEqual(b'workbook', (store.ROOT / 'jobs' / first / 'input.xlsx').read_bytes())

    def test_independent_claimers_never_receive_the_same_job(self):
        expected = {store.create('starter', 'default') for _ in range(8)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            actual = list(pool.map(lambda _: store.claim(), range(12)))
        claimed = [job for job in actual if job]
        self.assertEqual(expected, set(claimed))
        self.assertEqual(len(expected), len(claimed))

    def test_cancelled_queued_jobs_are_not_claimed(self):
        job = store.create('starter', 'default')
        store.cancel(job)
        self.assertIsNone(store.claim())
        self.assertEqual('cancelled', store.get(job)['state'])

    def test_rejects_unknown_paths_and_profiles(self):
        for source, profile in [('../../etc/passwd', 'default'), ('starter', 'unknown')]:
            with self.assertRaises(ValueError):
                store.create(source, profile)
        self.assertEqual([], store.jobs())


if __name__ == '__main__':
    unittest.main()
