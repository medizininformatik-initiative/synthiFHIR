import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bulk_ndjson as ndjson
import bulk_archives


class NdjsonTests(unittest.TestCase):
    def test_precision_native_support_and_blazectl_transactions(self):
        raw = b'{ "resourceType":"Bundle", "type":"transaction", "entry":[{"resource":{"resourceType":"Patient","id":"p1","x":1.234567890123456789,"text":"a \\n b"},"request":{"method":"PUT","url":"Patient/p1"}}]}\n'
        line, ids = ndjson.line(raw)
        self.assertEqual(['p1'], ids)
        self.assertIn(b'1.234567890123456789', line)
        self.assertEqual(1, line.count(b'\n'))
        self.assertEqual(json.loads(raw), json.loads(line))
        line, ids = ndjson.line(b'{"resourceType":"Bundle","type":"collection","entry":[{"resource":{"resourceType":"Organization","id":"o1","x":1.234567890123456789}}]}')
        bundle = json.loads(line)
        self.assertEqual('transaction', bundle['type'])
        self.assertEqual({'method': 'PUT', 'url': 'Organization/o1'}, bundle['entry'][0]['request'])
        self.assertIn(b'1.234567890123456789', line)

    def test_interrupted_pack_and_cleanup_resume_keep_completed_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); part = root / 'part-00001'; part.mkdir()
            source = part / 'p001.json.gz'
            raw = b'{"resourceType":"Bundle","type":"transaction","entry":[{"resource":{"resourceType":"Patient","id":"p1"}}]}'
            source.write_bytes(gzip.compress(raw))
            original = source.read_bytes()
            target = root / 'run-00001-1Pat.ndjson.gz'
            chunks = {part.name: {'size': 1}}
            def interrupt(*args):
                raise InterruptedError()
            with self.assertRaises(InterruptedError):
                ndjson.package(root, [part.name], target, interrupt, chunks)
            self.assertFalse(target.exists())
            self.assertEqual(original, source.read_bytes())
            with patch.object(ndjson.shutil, 'rmtree', side_effect=OSError('interrupted cleanup')):
                with self.assertRaises(OSError):
                    ndjson.package(root, [part.name], target, lambda *_: None, chunks)
            saved = target.read_bytes(), target.stat().st_mtime_ns
            ndjson.package(root, [part.name], target, lambda *_: None, chunks)
            self.assertEqual(saved, (target.read_bytes(), target.stat().st_mtime_ns))
            self.assertFalse(part.exists())
            self.assertEqual(raw + b'\n', gzip.decompress(target.read_bytes()))
            self.assertEqual(1, bulk_archives.patient_count(bulk_archives.read_manifest(target)))
            data = bytearray(target.read_bytes()); data[12] ^= 1; target.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                ndjson.read_manifest(target)

    def test_plain_and_gzip_match_and_resume(self):
        import generation
        self.assertTrue(generation.Settings().bulkGzip)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contents = []
            for compressed in (True, False):
                part = root / 'part-00001'; part.mkdir()
                raw = b'{"resourceType":"Bundle","type":"transaction","entry":[{"resource":{"resourceType":"Patient","id":"p1"}}]}'
                source = part / 'p001.json.gz'; source.write_bytes(gzip.compress(raw))
                suffix = ndjson.suffix({'bulkLayoutVersion': 5, 'generation': {'bulkGzip': compressed}})
                target = root / ('patients' + suffix)
                def fail(*args):
                    raise InterruptedError()
                with self.assertRaises(InterruptedError):
                    ndjson.package(root, [part.name], target, fail)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                ndjson.package(root, [part.name], target, lambda *_: None)
                before = target.read_bytes(), target.stat().st_mtime_ns
                ndjson.package(root, [part.name], target, lambda *_: None)
                self.assertEqual(before, (target.read_bytes(), target.stat().st_mtime_ns))
                with ndjson.open_stream(target, 'rb') as stream:
                    contents.append(stream.read())
            self.assertEqual(contents[0], contents[1])

    def test_statistics_count_while_writing_and_recover_after_report_failure(self):
        import package_statistics as stats
        resources = [
            {'resourceType': 'Patient', 'id': 'p1'},
            {'resourceType': 'Location', 'id': 'shared', 'name': 'Room 101'},
            {'resourceType': 'Location', 'id': 'shared', 'name': 'Updated name'},
            {'resourceType': 'Location', 'id': 'other'},
            {'resourceType': 'Medication', 'id': 'shared'},
            {'resourceType': 'Encounter', 'class': {'system': 'classes', 'code': 'IMP'},
             'type': [{'coding': [{'system': 'http://fhir.de/CodeSystem/Kontaktebene', 'code': 'einrichtungskontakt'}]}]},
            {'resourceType': 'Encounter', 'class': {'system': 'classes', 'code': 'IMP'},
             'partOf': {'reference': 'Encounter/parent'},
             'type': [{'coding': [{'system': 'http://fhir.de/CodeSystem/Kontaktebene', 'code': 'abteilungskontakt'}]}]},
            {'resourceType': 'Encounter', 'class': {'system': 'classes', 'code': 'AMB'}},
            {'resourceType': 'Observation', 'contained': [{'resourceType': 'Specimen'}]},
            {'resourceType': 'Encounter'}]
        raw = json.dumps({'resourceType': 'Bundle', 'type': 'transaction',
                          'entry': [{'resource': r} for r in resources]}).encode()
        for suffix in ndjson.SUFFIXES:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); part = root / 'part-00001'; part.mkdir()
                (part / 'p001.json.gz').write_bytes(gzip.compress(raw))
                (part / 'support-001.json.gz').write_bytes(gzip.compress(b'{"resourceType":"Bundle","type":"collection","entry":[{"resource":{"resourceType":"Organization","id":"o1"}},{"resource":{"resourceType":"Location","id":"shared"}}]}'))
                target = root / ('patients' + suffix)
                with patch.object(stats, 'write', side_effect=OSError('report interrupted')):
                    with self.assertRaisesRegex(OSError, 'report interrupted'):
                        ndjson.package(root, [part.name], target, lambda *_: None)
                self.assertTrue(target.exists())
                self.assertTrue(part.exists())
                saved = target.read_bytes(), target.stat().st_mtime_ns
                with patch.object(ndjson, 'line', side_effect=AssertionError('Must not parse bundles again')):
                    ndjson.package(root, [part.name], target, lambda *_: None)
                self.assertEqual(saved, (target.read_bytes(), target.stat().st_mtime_ns))
                self.assertFalse(part.exists())
                report = stats.report_path(target)
                text = report.read_text()
                self.assertIn('Total resources: 12', text)
                self.assertIn('Transport bundles: 2', text)
                snapshot = ndjson.read_manifest(target)['statistics']
                self.assertEqual(4, snapshot['resources']['Location'])
                self.assertEqual({'Patient': 1, 'Location': 2, 'Medication': 1, 'Encounter': 0, 'Observation': 0, 'Organization': 1}, snapshot['distinctIds'])
                self.assertEqual({'Encounter': 4, 'Observation': 1}, snapshot['missingIds'])
                self.assertIn('DISTINCT RESOURCE IDS BY TYPE', text)
                self.assertIn('Location: 2', text)
                self.assertIn('Medication: 1', text)
                self.assertIn('IMP (inpatient; classes): 2 / 1 / 1', text)
                self.assertIn('AMB (ambulatory; classes): 1 / 1 / 0', text)
                self.assertIn('Department contact | IMP (classes) | Sub-encounter: 1', text)
                self.assertNotIn('Specimen:', text)
                self.assertNotIn('abteilungskontakt', text)
                report.unlink()
                with patch.object(ndjson, 'line', side_effect=AssertionError('Must not parse bundles again')):
                    ndjson.package(root, [part.name], target, lambda *_: None)
                self.assertEqual(text, report.read_text())

    def test_older_checkpoint_does_not_claim_zero_distinct_ids(self):
        import package_statistics as stats
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'old.ndjson'
            stats.write(target, {'bundles': 1, 'resources': {'Location': 2}, 'encounters': [], 'contacts': []})
            text = stats.report_path(target).read_text()
            self.assertIn('Location: 2', text)
            self.assertIn('Not available in this package checkpoint.', text)
            self.assertNotIn('Location: 0', text)
