"""Verify the Compose TORCH example with temporary synthetic FHIR resources.

Usage: python3 web/integration/smoke_torch_profile.py /absolute/NEW_EVIDENCE_DIRECTORY
Start Blaze, its proxy, TORCH and its file server first; see TORCH.md.
"""
import base64
from collections import Counter
import copy
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

FHIR = 'http://localhost:5190/fhir'
TORCH = 'http://localhost:5193'
FILES = 'http://localhost:5194'
CRTDL = Path(__file__).resolve().parents[1] / 'deployment/upstream/data-node/torch/queries/example-crtdl.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def call(url, data=None):
    request = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Content-Type': 'application/fhir+json'})
    return urllib.request.urlopen(request, timeout=60)


def transaction(entries):
    with call(FHIR, {'resourceType': 'Bundle', 'type': 'transaction', 'entry': entries}) as response:
        result = json.load(response)
    require(result.get('type') == 'transaction-response', f'Unexpected FHIR response: {result}')
    require(len(result.get('entry', [])) == len(entries), 'Incomplete transaction response')
    require(all(item['response']['status'].startswith('2') for item in result['entry']),
            f'FHIR transaction failed: {result}')
    return result


def extract(folder, crtdl, patients):
    folder.mkdir()
    save(folder / 'crtdl.json', crtdl)
    parameters = {'resourceType': 'Parameters', 'parameter': [
        {'name': 'crtdl', 'valueBase64Binary': base64.b64encode(json.dumps(crtdl).encode()).decode()},
        *[{'name': 'patient', 'valueString': patient} for patient in patients],
    ]}
    save(folder / 'parameters.json', parameters)
    with call(TORCH + '/fhir/$extract-data', parameters) as response:
        require(response.status == 202, f'Expected HTTP 202, got {response.status}')
        location = response.headers['Content-Location']
    require(location.startswith(TORCH + '/fhir/__status/'), f'Unexpected status URL: {location}')
    (folder / 'status-url.txt').write_text(location + '\n')
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with call(location) as response:
            if response.status == 200:
                result = json.load(response)
                break
            require(response.status == 202, f'Unexpected status HTTP {response.status}')
        time.sleep(1)
    else:
        raise RuntimeError(f'Extraction timed out; inspect {location}')
    save(folder / 'result.json', result)
    require(not result.get('error'), f'TORCH returned errors: {result.get("error")}')
    extensions = {item['url']: item for item in result.get('extension', [])}
    job = extensions['https://torch.mii.de/fhir/StructureDefinition/torch-job']['valueObject']
    require(job['status'] == 'COMPLETED', f'TORCH job failed: {job}')
    resources = []
    for index, output in enumerate(result.get('output', [])):
        require(output['url'].startswith(FILES + '/'), f'Unexpected output URL: {output["url"]}')
        with call(output['url']) as response:
            data = response.read()
        (folder / f'part-{index}.ndjson').write_bytes(data)
        for line in data.splitlines():
            if line.strip():
                bundle = json.loads(line)
                require(bundle.get('resourceType') == 'Bundle', 'Expected FHIR Bundle output')
                resources.extend(entry['resource'] for entry in bundle.get('entry', []))
    for name in ('torch-job-diagnostics-summary', 'torch-resource-exclusions', 'torch-patient-exclusions'):
        url = extensions[name]['valueUrl']
        require(url.startswith(FILES + '/'), f'Unexpected diagnostic URL: {url}')
        with call(url) as response:
            data = response.read()
        (folder / (name + ('.json' if name.endswith('summary') else '.csv'))).write_bytes(data)
    return resources, extensions['torch-job-diagnostics-summary']['valueObject']


def fixture():
    prefix = 'torch-example-' + uuid.uuid4().hex
    resources = []
    for index in range(3):
        patient = f'{prefix}-{index}'
        resources.extend([
            {'resourceType': 'Patient', 'id': patient, 'meta': {'profile': [
                'https://www.medizininformatik-initiative.de/fhir/core/modul-person/StructureDefinition/PatientPseudonymisiert']},
             'gender': 'female' if index == 0 else 'male', 'birthDate': '1980-01-01'},
            {'resourceType': 'Condition', 'id': patient + '-diagnosis', 'meta': {'profile': [
                'https://www.medizininformatik-initiative.de/fhir/core/modul-diagnose/StructureDefinition/Diagnose']},
             'clinicalStatus': {'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/condition-clinical', 'code': 'active'}]},
             'code': {'coding': [{'system': 'http://fhir.de/CodeSystem/bfarm/icd-10-gm', 'code': 'E11.9', 'version': '2024'}]},
             'subject': {'reference': 'Patient/' + patient}, 'recordedDate': '2024-01-15T12:00:00Z',
             'note': [{'text': 'Synthetic note not requested by the extraction'}]},
        ])
    return resources


def main(folder):
    folder.mkdir(parents=True, exist_ok=False)
    resources = fixture()
    patients = [r['id'] for r in resources if r['resourceType'] == 'Patient'][:2]
    entries = [{'resource': r, 'request': {'method': 'PUT', 'url': r['resourceType'] + '/' + r['id']}}
               for r in resources]
    save(folder / 'input.json', {'resourceType': 'Bundle', 'type': 'transaction', 'entry': entries})
    save(folder / 'target.json', {'fhir': FHIR, 'torch': TORCH, 'files': FILES, 'selectedPatients': patients})
    imported = False
    try:
        save(folder / 'import-result.json', transaction(entries))
        imported = True
        crtdl = json.loads(CRTDL.read_text())
        extracted, summary = extract(folder / 'without-consent-criteria', crtdl, patients)
        selected = set(patients)
        require(Counter(r['id'] for r in extracted if r['resourceType'] == 'Patient') == Counter(patients),
                'Extracted patient IDs/counts differ from explicit selection')
        diagnoses = [r for r in extracted if r['resourceType'] == 'Condition']
        require(Counter(r['id'] for r in diagnoses) == Counter(p + '-diagnosis' for p in patients),
                'Extracted diagnosis IDs/counts differ from expected selection')
        sources = {r['id']: r for r in resources}
        for diagnosis in diagnoses:
            source = sources[diagnosis['id']]
            require(all(diagnosis.get(field) == source[field] for field in ('code', 'recordedDate', 'subject')),
                    f'Requested diagnosis values changed: {diagnosis["id"]}')
            require('note' not in diagnosis, 'Unrequested diagnosis note was retained')
        for patient in (r for r in extracted if r['resourceType'] == 'Patient'):
            require('gender' not in patient and 'birthDate' not in patient, 'Unrequested patient fields were retained')
        require(summary['Num-Final-Patients'] == 2, 'Unexpected final patient count')
        require(summary['Patient-Exclusions']['CONSENT_FETCH'] == 0, 'Unexpected consent exclusions')
        require(set(r['resourceType'] for r in extracted) <= {'Patient', 'Condition', 'Provenance'},
                'Unexpected resource types in extraction')
        consent_crtdl = copy.deepcopy(crtdl)
        for code in ('6', '8'):
            consent_crtdl['cohortDefinition']['inclusionCriteria'].append([{
                'context': {'code': 'Einwilligung', 'system': 'fdpg.mii.cds'},
                'termCodes': [{'code': '2.16.840.1.113883.3.1937.777.24.5.3.' + code,
                               'system': 'urn:oid:2.16.840.1.113883.3.1937.777.24.5.3'}],
            }])
        denied, consent_summary = extract(folder / 'with-consent-criteria', consent_crtdl, patients)
        require(not denied, 'Patients without consent were extracted with consent criteria enabled')
        require(consent_summary['Num-Final-Patients'] == 0 and
                consent_summary['Patient-Exclusions']['CONSENT_FETCH'] == 2,
                'Missing expected consent exclusion evidence')
        save(folder / 'verification.json', {
            'status': 'passed', 'selectedPatients': sorted(selected),
            'resources': dict(Counter(r['resourceType'] for r in extracted)),
            'unselectedPatientExcluded': True, 'requestedValuesPreserved': True,
            'unrequestedFieldsRemoved': True, 'patientsExcludedForMissingConsent': 2,
        })
    finally:
        if imported:
            # Delete only this run's UUID-named fixtures, dependants before Patients.
            cleanup = [{'request': {'method': 'DELETE', 'url': r['resourceType'] + '/' + r['id']}}
                       for r in sorted(resources, key=lambda r: r['resourceType'] == 'Patient')]
            save(folder / 'cleanup-result.json', transaction(cleanup))
    print(f'PASS: 2 Patients and 2 Conditions verified; control patient excluded; '
          f'2 patients excluded when consent is required. Fixtures removed. Evidence: {folder}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    try:
        main(Path(sys.argv[1]).resolve())
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            print(error.read().decode(errors='replace'), file=sys.stderr)
        raise SystemExit(f'FAIL: {error}. Inspect the evidence directory and Compose logs.')
