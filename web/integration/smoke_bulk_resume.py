"""Real worker cancellation/resumption with fixed sources and isolated temporary storage."""
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from uuid import UUID
import zipfile
import bulk_ndjson

import api
import bulk_generation
import generation
import store
import worker

# The child substitutes only source generation and adds a synchronization point.
# Mapping, Java conversion, checkpoints, NDJSON publication and process termination
# use production code. The gate prevents a timing-dependent cancellation race.
CHILD = r'''
import json
import sys
import time
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
sys.path.insert(0, BACKEND)
import bulk_generation as bulk
import generation
import synthea_runtime

folder = Path(sys.argv[1])
root = folder.parents[2]
gate = root / 'cancel-ready'
mode = (root / 'interrupt-mode').read_text()

def interrupt():
    if not gate.exists():
        gate.write_text(mode)
        # The real worker terminates this process group after api.cancel().
        while True:
            time.sleep(0.1)

import bulk_ndjson
real_write = bulk_ndjson.DigestWriter.write
def archive_write(self, *args, **kwargs):
    result = real_write(self, *args, **kwargs)
    state = json.loads((folder / 'bulk-progress.json').read_text())
    if mode == 'packaging' and state['phase'] == 'packaging' and state['completed'] == 400:
        interrupt()
    return result
bulk_ndjson.DigestWriter.write = archive_write
real_process = bulk.checked_process
fixture = json.loads((synthea_runtime.ROOT / 'scripts/tests/fixtures/synthea/female.json').read_text())
# Include an inpatient contact in every synthetic fixture.
for entry in fixture['entry']:
    if entry['resource']['resourceType'] == 'Encounter':
        entry['resource']['class']['code'] = 'IMP'
source = json.dumps(fixture)
ids = {entry['resource']['id'] for entry in fixture['entry']}
generation.native_command = lambda settings, destination: [
    'fixture', str(settings['population']), settings['patientSeed'], str(destination)]

def process(command, log, update=None):
    if command[0] == 'fixture':
        size, seed, destination = int(command[1]), command[2], Path(command[3])
        with (root / 'attempts.jsonl').open('a') as stream:
            stream.write(json.dumps(seed) + '\n')
        target = destination / 'fhir'
        target.mkdir(parents=True)
        for i in range(size):
            text = source
            for resource_id in sorted(ids):
                text = text.replace(resource_id, str(uuid5(NAMESPACE_URL, f'{seed}/{i}/{resource_id}')))
            (target / f'{i:03d}.json').write_text(text)
            if mode == 'generation' and seed == '103' and i == 49:
                interrupt()
        return
    def conversion_progress(values):
        if update:
            update(values)
        state = json.loads((folder / 'bulk-progress.json').read_text())
        if mode == 'conversion' and state['completed'] == 300 and values.get('stageCompleted', 0) > 0:
            interrupt()
    real_process(command, log, conversion_progress)

bulk.checked_process = process
bulk.run(folder)
'''


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def wait_until(predicate, future, folder, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if future.done():
            future.result()
            raise AssertionError((folder / 'converter.log').read_text()[-10000:])
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for the cancellation checkpoint')


def run(mode, compressed=True):
    suffix = '.ndjson.gz' if compressed else '.ndjson'
    with tempfile.TemporaryDirectory(prefix='bulk-resume-') as temporary:
        root = Path(temporary)
        (root / 'interrupt-mode').write_text(mode)
        backend = str(Path(worker.__file__).parent)
        shim = root / 'runner'
        shim.mkdir()
        (shim / 'bulk_generation.py').write_text('BACKEND = ' + repr(backend) + '\n' + CHILD)
        output = root / 'results'
        output.mkdir()
        sentinel = output / 'existing-output.zip'
        sentinel.write_bytes(b'Existing outputs must remain unchanged')
        sentinel_before = fingerprint(sentinel)
        environment = {'WORKBENCH_DATA': str(root / 'data'), 'WORKBENCH_OUTPUT': str(output)}
        config = ('CONFIGURATION_VERSION=1\nCHECKS_FHIR_VALIDATION=false\n'
                  'IDENTIFIER_RULE_1_ID=4797e210-671c-4200-9bfb-90faaee082f0\n'
                  'IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Encounter\n'
                  'IDENTIFIER_RULE_1_SYSTEM=urn:resume:case\n'
                  'IDENTIFIER_RULE_1_PATTERN={resourceType}-{count:08}\n'
                  'IDENTIFIER_RULE_2_ID=aef7e40e-9e7f-437b-9813-e8ffc86b9607\n'
                  'IDENTIFIER_RULE_2_ENABLED=true\nIDENTIFIER_RULE_2_RESOURCES=Patient\n'
                  'IDENTIFIER_RULE_2_SYSTEM=urn:resume:patient\nIDENTIFIER_RULE_2_PATTERN={count:08}\n')
        import result_exports
        with patch.object(result_exports, 'ROOT', output), patch.dict(os.environ, environment), patch.object(store, 'ROOT', root / 'data'), \
                patch.object(bulk_generation, 'OUTPUT_ROOT', output), \
                patch.object(worker, '__file__', str(shim / 'worker.py')):
            settings = generation.Settings(bulkGzip=compressed, executionMode='bulk', population=500, patientsPerRun=200,
                                           patientSeed='100', syntheticConsent=False)
            job = api.create(api.JobRequest(source='synthea-generation', datasetName='Resume integration',
                                            generation=settings, configurationProperties=config, tableFormat='csv'))
            job_id = job['id']
            folder = store.ROOT / 'jobs' / job_id
            snapshot = (folder / 'snapshot.json').read_bytes()
            destination = output / json.loads(snapshot)['resultOutputName']
            assert store.claim() == job_id
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(worker.execute, job_id)
                try:
                    wait_until(lambda: (root / 'cancel-ready').exists(), future, folder)
                    first, = (destination).glob('*' + suffix)
                    before = fingerprint(first)
                    loose = {str(p.relative_to(folder)): fingerprint(p) for p in (folder / 'bulk-staging').rglob('part-*/*.json.gz')}
                    assert len(loose) == (200 if mode == 'packaging' else 100), loose.keys()
                    completed = 400 if mode == 'packaging' else 300
                    assert json.loads((folder / 'bulk-progress.json').read_text())['completed'] == completed
                    if mode == 'packaging':
                        assert list(destination.glob('.*.partial')), 'Must interrupt an actual gzip write'
                    api.cancel(job_id)
                    future.result(timeout=30)
                    stopped = store.get(job_id)
                    assert stopped['state'] == 'cancelled', stopped
                    assert stopped['bulk_resumable']
                    assert fingerprint(first) == before
                    assert (folder / 'snapshot.json').read_bytes() == snapshot
                    assert all(fingerprint(folder / name) == saved for name, saved in loose.items())
                    print(f'PASS: {mode} cancellation after {completed} patients; checkpoints and first package unchanged', flush=True)
                    api.resume_job(UUID(job_id))
                    assert store.claim() == job_id
                    future = executor.submit(worker.execute, job_id)
                    future.result(timeout=180)
                    result = store.get(job_id)
                    assert result['state'] == 'succeeded', (result, (folder / 'converter.log').read_text()[-10000:])
                    assert result['bulk_progress']['completed'] == 500
                    assert result['bulk_progress']['finishedRuns'] == 3
                    assert fingerprint(first) == before
                    assert (folder / 'snapshot.json').read_bytes() == snapshot
                    assert not (folder / 'bulk-work').exists()
                    assert not list(destination.rglob('*.json.gz'))
                    assert not list(destination.rglob('*.partial'))
                finally:
                    # Also terminate the isolated process group if an assertion or timeout fails.
                    if not future.done():
                        api.cancel(job_id)
                        future.result(timeout=30)
            expected_seeds = ['100', '101', '102', '103'] + (['103'] if mode == 'generation' else []) + ['104']
            assert [json.loads(line) for line in (root / 'attempts.jsonl').read_text().splitlines()] == expected_seeds
            values = {'urn:resume:patient': [], 'urn:resume:case': []}
            patient_ids = []
            archives = sorted((destination).glob('*' + suffix))
            assert len(archives) == 3
            for archive, expected_count in zip(archives, [200, 200, 100]):
                manifest = bulk_ndjson.read_manifest(archive)
                for name, metadata in manifest['files'].items():
                    loose_name = 'bulk-staging/' + archive.name.split('-package-')[0] + '/' + name
                    if loose_name in loose:
                        assert metadata['sha256'] == loose[loose_name][0]
                count = 0
                with bulk_ndjson.open_stream(archive) as stream:
                    for line in stream:
                        bundle = json.loads(line)
                        assert bundle['type'] == 'transaction'
                        resources = [e['resource'] for e in bundle['entry']]
                        patients = [r for r in resources if r['resourceType'] == 'Patient']
                        assert len(patients) == 1
                        patient_ids.append(patients[0]['id'])
                        count += 1
                        for resource in resources:
                            for identifier in resource.get('identifier', []):
                                if identifier.get('system') in values:
                                    values[identifier['system']].append(identifier['value'])
                assert count == expected_count and archive.name.endswith(f'-{expected_count}Pat' + suffix)
                assert len(manifest['chunks']) == expected_count // 100
                checkpoint = manifest['chunks'][max(manifest['chunks'])]
                assert checkpoint['size'] == 100
                for rule, system in [('4797e210-671c-4200-9bfb-90faaee082f0', 'urn:resume:case'),
                                     ('aef7e40e-9e7f-437b-9813-e8ffc86b9607', 'urn:resume:patient')]:
                    assert checkpoint['identifierCounters'][rule] == len(values[system])
                # API download must retain the actual format and filename.
                items = json.loads((folder / 'datasets.json').read_text())['datasets']
                item = next(item for item in items if item['path'] == archive.name)
                response = api.dataset_download(item['id'])
                assert response.filename == archive.name
                assert Path(response.path) == archive
            assert len(patient_ids) == len(set(patient_ids)) == 500
            assert len(values['urn:resume:patient']) == 500
            assert len(values['urn:resume:case']) >= 500
            for system, identifiers in values.items():
                assert len(identifiers) == len(set(identifiers)), system
                assert sorted(int(value.rsplit('-', 1)[-1]) for value in identifiers) == list(range(1, len(identifiers) + 1)), system
            assert fingerprint(sentinel) == sentinel_before
            print('PASS: resumed identifiers unique and contiguous, checkpoints match, NDJSON valid, temporary work cleaned', flush=True)


if __name__ == '__main__':
    for compressed in (True, False):
        for mode in ('generation', 'conversion', 'packaging'):
            print(f'Testing gzip={compressed}, interruption={mode}', flush=True)
            run(mode, compressed)
