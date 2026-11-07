import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import archive_names
import bulk_archives


class ArchiveNameTests(unittest.TestCase):
    def test_saved_output_counts_and_unknown_or_overlapping_datasets(self):
        first = {'inspection': {'patients': 2}}
        self.assertEqual(2, archive_names.patient_count(first))
        self.assertEqual(2, archive_names.run_patient_count({'datasets': [first]}))
        self.assertEqual(0, archive_names.patient_count({'inspection': {'patients': 0}}))
        for value in [None, -1, True, '2']:
            self.assertIsNone(archive_names.patient_count({'inspection': {'patients': value}}))
        self.assertIsNone(archive_names.patient_count({}))
        self.assertIsNone(archive_names.run_patient_count({}))
        self.assertIsNone(archive_names.run_patient_count({'datasets': [first, first]}))

    def test_short_safe_names(self):
        self.assertEqual('synthifhir-17Pat-id.zip', archive_names.filename('', 17, 'id'))
        self.assertEqual('Kohorte.zip', archive_names.filename('Kohorte', None, 'id'))
        self.assertEqual('synthifhir-id.zip', archive_names.filename('', None, 'id'))
        self.assertEqual('Kohorte-17Pat.zip', archive_names.filename('Kohorte', 17, 'id'))
        self.assertEqual('Test-Name-0Pat.zip', archive_names.filename('../Test/Name', 0, 'id'))

    def test_partial_package_excludes_support_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / 'part-00001'
            part.mkdir()
            for name in ['p.json.gz', 'support-org.json.gz']:
                (part / name).write_bytes(gzip.compress(json.dumps({'resourceType': 'Organization' if name.startswith('support-') else 'Patient', 'id': name}).encode()))
            base = root / 'packages/run-00001-package-00001.zip'
            target = bulk_archives.package_path(root, [part.name], base)
            self.assertEqual('run-00001-package-00001-1Pat.zip', target.name)
            bulk_archives.package(root, [part.name], target, lambda *_: None)
            self.assertEqual(target, bulk_archives.package_path(root, [part.name], base))
            bulk_archives.package(root, [part.name], target, lambda *_: None)

    def test_package_counts_distinct_patient_resources_not_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / 'part-00001'
            part.mkdir()
            resources = [
                {'resourceType': 'Patient', 'id': 'same'},
                {'resourceType': 'Patient', 'id': 'same'},
                {'resourceType': 'Observation', 'id': 'observation', 'subject': {'reference': 'Patient/external'}},
            ]
            for index, resource in enumerate(resources):
                (part / f'p{index}.json.gz').write_bytes(gzip.compress(json.dumps({
                    'resourceType': 'Bundle', 'entry': [{'resource': resource}]}).encode()))
            target = bulk_archives.package_path(root, [part.name], root / 'packages/test.zip')
            self.assertEqual('test-1Pat.zip', target.name)
            bulk_archives.package(root, [part.name], target, lambda *_: None)
            self.assertEqual(1, bulk_archives.patient_count(bulk_archives.read_manifest(target)))
