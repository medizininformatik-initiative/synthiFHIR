"""300 vs 275 + 25 through the continuation API, real mapping/conversion/archives.

Only Synthea source generation is replaced with deterministic seeded fixtures.
The test creates no jobs or datasets in the user's Workbench storage.
"""
import gzip
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5
import zipfile

import api
import bulk_continuation
import bulk_generation as bulk
import generation
import result_exports
import store
import synthea_runtime


def run():
    fixture = json.loads((synthea_runtime.ROOT / 'scripts/tests/fixtures/synthea/female.json').read_text())
    for entry in fixture['entry']:
        if entry['resource']['resourceType'] == 'Encounter':
            entry['resource']['class']['code'] = 'IMP'
    source = json.dumps(fixture)
    ids = {entry['resource']['id'] for entry in fixture['entry']}
    real_process = bulk.checked_process
    generated = []

    def process(command, log, update=None):
        if command[0] != 'fixture':
            return real_process(command, log, update)
        size, seed, destination = int(command[1]), command[2], Path(command[3])
        generated.append((size, seed))
        target = destination / 'fhir'
        target.mkdir(parents=True)
        for i in range(size):
            text = source
            for resource_id in sorted(ids):
                text = text.replace(resource_id, str(uuid5(NAMESPACE_URL, f'{seed}/{i}/{resource_id}')))
            (target / f'{i:03d}.json').write_text(text)

    with tempfile.TemporaryDirectory(prefix='bulk-continuation-') as temporary:
        root = Path(temporary)
        config = ('CONFIGURATION_VERSION=1\nCHECKS_FHIR_VALIDATION=false\n'
                  'IDENTIFIER_RULE_1_ID=4797e210-671c-4200-9bfb-90faaee082f0\n'
                  'IDENTIFIER_RULE_1_ENABLED=true\nIDENTIFIER_RULE_1_RESOURCES=Patient\n'
                  'IDENTIFIER_RULE_1_SYSTEM=https://example.org/fhir/sid/patient-id\n'
                  'IDENTIFIER_RULE_1_PATTERN={resourceType}-{count:08}\n'
                  'IDENTIFIER_RULE_1_COUNT_START=1\n'
                  'IDENTIFIER_RULE_1_TYPE_CODINGS=[{"system":"http://terminology.hl7.org/CodeSystem/v2-0203","code":"MR"}]\n'
                  'IDENTIFIER_RULE_2_ID=aef7e40e-9e7f-437b-9813-e8ffc86b9607\n'
                  'IDENTIFIER_RULE_2_ENABLED=true\nIDENTIFIER_RULE_2_RESOURCES=Encounter\n'
                  'IDENTIFIER_RULE_2_SYSTEM=https://example.org/fhir/sid/case-id\n'
                  'IDENTIFIER_RULE_2_PATTERN={resourceType}-{count:08}\n'
                  'IDENTIFIER_RULE_2_TYPE_CODINGS=[{"system":"http://terminology.hl7.org/CodeSystem/v2-0203","code":"VN"}]\n')
        def execute(job):
            assert store.claim() == job['id']
            bulk.run(store.ROOT / 'jobs' / job['id'])
            store.finish(job['id'], 'succeeded', 0)
            return store.get(job['id'])

        def bundles(job):
            snapshot = json.loads((store.ROOT / 'jobs' / job['id'] / 'snapshot.json').read_text())
            data = {}
            for path in result_exports.path(snapshot).glob('*.ndjson.gz'):
                with gzip.open(path, 'rb') as stream:
                    for raw in stream:
                        bundle = json.loads(raw)
                        patient, = [e['resource']['id'] for e in bundle['entry'] if e['resource']['resourceType'] == 'Patient']
                        assert patient not in data
                        data[patient] = raw
            return data

        with patch.object(store, 'ROOT', root / 'data'), patch.object(result_exports, 'ROOT', root / 'output'), \
                patch.object(bulk, 'checked_process', side_effect=process), patch('bulk_resources.require_available'), \
                patch.object(generation, 'native_command', side_effect=lambda settings, destination: [
                    'fixture', str(settings['population']), settings['patientSeed'], str(destination)]):
            settings = generation.Settings(executionMode='bulk', population=300, patientsPerRun=100,
                                           patientSeed='1791440724390', clinicianSeed='1791440724390',
                                           minAge=18, maxAge=90, yearsOfHistory=10,
                                           referenceDate='2026-09-12', endDate='2026-09-12', syntheticConsent=True)
            def start(count):
                return execute(api.create(api.JobRequest(source='synthea-generation',
                               generation=settings.model_copy(update={'population': count}), configurationProperties=config)))
            whole = start(300)
            first = start(275)
            original_snapshot = (store.ROOT / 'jobs' / first['id'] / 'snapshot.json').read_bytes()
            result_exports.rename_bulk(first['id'], 'synthiFHIR')
            assert (store.ROOT / 'jobs' / first['id'] / 'snapshot.json').read_bytes() == original_snapshot
            preview = api.continuation_preview(UUID(first['id']))
            assert preview['datasetName'] == 'synthiFHIR'
            assert preview['generation']['patientSeed'] == '1791440724392'
            assert preview['generation']['clinicianSeed'] == '1791440724390'
            assert preview['nextCounters']['4797e210-671c-4200-9bfb-90faaee082f0'] == '276'
            assert preview['sourceOffset'] == 75
            before = list(generated)
            from pydantic import ValidationError
            for changed in ({'generation': {'maxAge': 120}}, {'configurationProperties': config.replace('{count:08}', 'constant')},
                            {'identifierCounters': {}}, {'patientSeed': '7'}):
                try:
                    api.ContinuationRequest.model_validate({'population': 25, **changed})
                    raise AssertionError('Changed settings must be rejected')
                except ValidationError:
                    pass
            following = execute(api.continue_job(UUID(first['id']), api.ContinuationRequest(population=25)))
            assert following['dataset_name'] == 'synthiFHIR'
            assert generated == before, 'Continuation regenerated already saved patients'
            left, first_data, second_data = bundles(whole), bundles(first), bundles(following)
            assert len(left) == 300 and len(first_data) == 275 and len(second_data) == 25
            assert not first_data.keys() & second_data.keys()
            assert left == {**first_data, **second_data}, 'FHIR bytes differ between whole and continued series'
            owners = {}
            for raw in left.values():
                for entry in json.loads(raw)['entry']:
                    resource = entry['resource']
                    for identifier in resource.get('identifier', []):
                        system = identifier.get('system', '')
                        if not system.startswith('https://example.org/fhir/sid/'):
                            continue
                        key = (system, identifier['value'])
                        owner = (resource['resourceType'], resource['id'])
                        assert key not in owners or owners[key] == owner, (key, owner, owners.get(key))
                        owners[key] = owner
            endings = []
            for job in (whole, following):
                saved = json.loads((store.ROOT / 'jobs' / job['id'] / bulk_continuation.STATE_FILE).read_text())
                endings.append(saved['identifierCounters'])
            assert endings[0] == endings[1]
            assert api.continuation_preview(UUID(first['id']))['sourceRun'] == following['id']
            print('PASS: 300 == 275 + 25 byte-identical FHIR including consent, patient IDs and case IDs', flush=True)
            print('PASS: no regeneration for the final 25; changed settings and identifier rules rejected', flush=True)
            print('PASS: unique identifiers, identical final counters:', endings[0], flush=True)


if __name__ == '__main__':
    run()
