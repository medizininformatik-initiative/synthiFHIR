"""Native bulk output retains all generator bytes and resumes without a KDS config."""
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import zipfile

import bulk_archives
import bulk_generation as bulk
import generation


class NativeBulkTests(unittest.TestCase):
    def test_native_packages_preserve_bytes_providers_counts_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'job'
            folder.mkdir()
            settings = generation.Settings(executionMode='bulk', outputMode='synthea',
                                           population=101, patientsPerRun=100, patientSeed='1000').model_dump(mode='json')
            (folder / 'snapshot.json').write_text(json.dumps({'generation': settings,
                'bulkLayoutVersion': 3, 'bulkOutputName': 'native'}))
            originals = {}
            seeds = []
            fail = [True]
            def process(command, log, update=None):
                self.assertIn('--exporter.hospital.fhir.export=true', command)
                self.assertIn('--exporter.practitioner.fhir.export=true', command)
                self.assertNotIn('-cp', command)
                seed = command[command.index('-s') + 1]
                size = int(command[command.index('-p') + 1])
                seeds.append(seed)
                if seed == '1001' and fail[0]:
                    raise RuntimeError('deliberate interruption')
                target = log.parent / 'synthea/fhir'
                target.mkdir(parents=True)
                values = []
                for index in range(size):
                    values.append(('patient-' + str(index), ('{ "resourceType": "Bundle", "type":"transaction", "entry":['
                        '{"resource":{"resourceType":"Patient","id":"p-' + seed + '-' + str(index) + '",'
                        '"extension":[{"url":"urn:test:precision","valueDecimal":1.234567890123456789}]}}]}\n').encode()))
                for kind in ('Organization', 'Practitioner'):
                    values.append((kind, json.dumps({'resourceType': 'Bundle', 'type': 'collection',
                        'entry': [{'resource': {'resourceType': kind, 'id': kind.lower()}}]}).encode()))
                originals[seed] = sorted(raw for _, raw in values)
                for name, raw in values:
                    (target / (name + '.json')).write_bytes(raw)
            with patch.object(bulk, 'OUTPUT_ROOT', root / 'output'), patch.object(bulk, 'checked_process', side_effect=process), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'deliberate interruption'):
                    bulk.run(folder)
                archive = root / 'output/native/run-00001-package-00001-100Pat.zip'
                saved = archive.read_bytes()
                fail[0] = False
                bulk.run(folder)
                self.assertEqual(saved, archive.read_bytes())
                paths = sorted((root / 'output/native').glob('*.zip'))
                self.assertEqual(2, len(paths))
                for seed, path in zip(('1000', '1001'), paths):
                    manifest = bulk_archives.read_manifest(path)
                    with zipfile.ZipFile(path) as package:
                        values = sorted(gzip.decompress(package.read(name)) for name in manifest['files'])
                    self.assertEqual(originals[seed], values)
                    self.assertEqual(2, sum(v.get('role') == 'support' for v in manifest['files'].values()))
                # A completed job also resumes without touching either package.
                bulk.run(folder)
            self.assertEqual(['1000', '1001', '1001'], seeds)
            self.assertFalse((folder / 'default.config').exists())
            self.assertFalse((folder / 'bulk-work').exists())
            self.assertEqual([], list((root / 'output').rglob('*.json.gz')))
            published = json.loads((folder / 'datasets.json').read_text())['datasets']
            self.assertEqual([100, 1], [item['inspection']['patients'] for item in published])
            self.assertEqual(101, json.loads((folder / 'bulk-progress.json').read_text())['completed'])

    def test_package_boundary_follows_snapshot_and_subrun_size(self):
        for layout, population, per_run, expected in (
                (3, 2001, 2000, [1000, 1000, 1]),
                (4, 2001, 2000, [2000, 1]),
                (4, 10001, 10000, [10000, 1]),
                (5, 201, 200, [200, 1])):
            with self.subTest(layout=layout, per_run=per_run), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / 'job'
                folder.mkdir()
                settings = generation.Settings(executionMode='bulk', outputMode='synthea',
                    population=population, patientsPerRun=per_run, patientSeed='1000').model_dump(mode='json')
                (folder / 'snapshot.json').write_text(json.dumps({'generation': settings,
                    'bulkLayoutVersion': layout, 'bulkOutputName': 'native'}))
                seeds = []
                def process(command, log, update=None):
                    seed = command[command.index('-s') + 1]
                    size = int(command[command.index('-p') + 1])
                    seeds.append(seed)
                    target = log.parent / 'synthea/fhir'
                    target.mkdir(parents=True)
                    for i in range(size):
                        (target / f'p{i}.json').write_text(json.dumps({
                            'resourceType': 'Bundle', 'entry': [{'resource': {
                                'resourceType': 'Patient', 'id': f'p{seed}-{i}'}}]}))
                with patch.object(bulk, 'OUTPUT_ROOT', root / 'output'), patch.object(
                        bulk, 'checked_process', side_effect=process), redirect_stdout(io.StringIO()):
                    bulk.run(folder)
                    paths = sorted((root / 'output/native').glob('*.ndjson.gz' if layout >= 5 else '*.zip'))
                    saved = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
                    bulk.run(folder)
                    self.assertEqual(saved, [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths])
                self.assertEqual([str(1000 + i) for i in range((population + 99) // 100)], seeds)
                self.assertEqual(expected, [bulk_archives.patient_count(bulk_archives.read_manifest(p)) for p in paths])
                self.assertEqual(population, sum(len(bulk_archives.read_manifest(p)['files']) for p in paths))
                self.assertFalse(list((root / 'output').rglob('*.json.gz')))

    def test_native_compression_rejects_missing_patients(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'; source.mkdir()
            output = root / 'output'; output.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'expected 1'):
                bulk.compress_native(source, output, 1, lambda **_: None)
