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
