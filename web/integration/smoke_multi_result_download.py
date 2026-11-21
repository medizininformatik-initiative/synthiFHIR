"""Verify legacy multi-variant downloads using two real converter outputs.

Run in the Workbench worker image. All runs and outputs use temporary storage.
New Workbench jobs use one configuration; the combined fixture represents saved
results from versions that supported multiple variants in one run.
"""
from collections import Counter
import io
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

import datasets
import fhir_downloads
import result_exports
import store
import worker

with tempfile.TemporaryDirectory(prefix='multi-result-download-') as temporary:
    store.ROOT = Path(temporary) / 'data'
    result_exports.ROOT = Path(temporary) / 'results'
    outputs = []
    for prefix in ('A-', 'B-'):
        job = store.create('starter', 'default',
                           'CONFIGURATION_VERSION=1\nOUTPUT_FORMATS=JSON,NDJSON\nPID_PREFIX=' + prefix + '\n')
        assert store.claim() == job
        worker.execute(job)
        folder = store.ROOT / 'jobs' / job
        assert store.get(job)['state'] == 'succeeded', (folder / 'converter.log').read_text()[-6000:]
        outputs.append(next((folder / 'output').glob('run-*/fhir')))
    combined = store.create('starter', 'default')
    folder = store.ROOT / 'jobs' / combined
    root = folder / 'output/run-legacy'
    for name, source in zip(('variant-a', 'variant-b'), outputs):
        shutil.copytree(source, root / 'fhir' / name)
        (root / 'details/options/input.xlsx' / name).mkdir(parents=True)
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    manifest = datasets.build(combined, snapshot)
    store.finish(combined, 'succeeded', 0)
    assert len(manifest['datasets']) == 2
    expected = Counter(path.read_bytes() for source in outputs for path in source.rglob('*')
                       if path.suffix in ('.json', '.ndjson'))
    stream = fhir_downloads.stream(combined, fhir_downloads.plan(combined))
    with zipfile.ZipFile(io.BytesIO(b''.join(stream))) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert {name.split('/')[0] for name in names} == {'001-variant-a', '002-variant-b'}
        assert all(len(name.split('/')) == 2 for name in names)
        assert Counter(archive.read(name) for name in names) == expected
        for prefix, variant in (('A-', '001-variant-a/'), ('B-', '002-variant-b/')):
            patients = {entry['resource']['id'] for name in names
                        if name.startswith(variant) and name.endswith('.json')
                        for entry in json.loads(archive.read(name))['entry']
                        if entry['resource']['resourceType'] == 'Patient'}
            assert len(patients) == 10 and all(patient.startswith(prefix) for patient in patients)
    print('PASS: two real 10-patient variants, separate flat folders, unique filenames and byte-identical complete FHIR payloads')
