"""Create independent FHIR additions from the immutable results of a completed run."""
import copy
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4, uuid5
import zipfile

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

import datasets
import addition_generation
from fhir_uploads import encode
import store

# Patient path, encounter path, CodeableConcept path.
TYPES = {
    'DocumentReference': ('subject', 'context.encounter', 'type'),
    'Condition': ('subject', 'encounter', 'code'),
    'Observation': ('subject', 'encounter', 'code'),
    'Procedure': ('subject', 'encounter', 'code'),
    'MedicationRequest': ('subject', 'encounter', None),
    'MedicationAdministration': ('subject', 'context', None),
    'MedicationStatement': ('subject', 'context', None),
    'Immunization': ('patient', 'encounter', 'vaccineCode'),
    'DiagnosticReport': ('subject', 'encounter', 'code'),
    'CarePlan': ('subject', 'encounter', None),
    'Consent': ('patient', None, None),
}
NLP_URL = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/StructureDefinition/mii-ex-dokument-nlp-processing-status'
NLP_SYSTEM = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/CodeSystem/mii-cs-dokument-nlp-processing-status'


class Coding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system: str = Field(default='', max_length=2000)
    code: str = Field(default='', max_length=1000)
    display: str = Field(default='', max_length=2000)


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resourceType: str
    association: Literal['patient', 'encounter'] = 'encounter'
    status: str = Field(default='', max_length=100)
    codingMode: Literal['input', 'fill', 'replace'] = 'input'
    codings: list[Coding] = Field(default_factory=list, max_length=100)
    nlpMode: Literal['input', 'fill', 'replace', 'omit'] = 'input'
    nlpCodes: list[str] = Field(default_factory=list, max_length=100)
    text: str = Field(default='', max_length=2000)
    documentText: str = Field(default='', max_length=100000)
    value: str = Field(default='', max_length=2000)
    timestamp: str = Field(default='', max_length=40)


def validate(settings, *, generated=False):
    if settings.resourceType not in TYPES:
        raise ValueError('Unsupported resource type')
    _, encounter, code = TYPES[settings.resourceType]
    if not generated and settings.resourceType == 'Condition' and settings.status:
        raise ValueError('Condition uses clinicalStatus, not status')
    if settings.association == 'encounter' and not encounter:
        raise ValueError('This resource type supports patient references only')
    if generated:
        addition_generation.validate(settings)
        if settings.resourceType.startswith('Medication'):
            code = 'medicationCodeableConcept'
    if not code and (settings.codingMode != 'input' or settings.codings):
        raise ValueError('This resource type has no editable code here')
    if settings.resourceType != 'DocumentReference' and (settings.nlpMode != 'input' or settings.nlpCodes):
        raise ValueError('NLP status requires DocumentReference')
    if any(not value.strip() or len(value) > 100 for value in settings.nlpCodes):
        raise ValueError('Invalid NLP status code')


def value_at(resource, path):
    value = resource
    for part in path.split('.') if path else []:
        value = value.get(part, {}) if isinstance(value, dict) else {}
    return value if path else {}


def references(value):
    if isinstance(value, dict):
        if isinstance(value.get('reference'), str):
            yield value
        for item in value.values():
            yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def key(resource):
    kind, identifier = resource.get('resourceType', ''), resource.get('id', '')
    if not re.fullmatch(r'[A-Z][A-Za-z0-9]+', kind) or not re.fullmatch(r'[A-Za-z0-9.-]{1,64}', identifier):
        raise ValueError('Source resources need stable FHIR IDs')
    return kind + '/' + identifier


def index_export(db, stream):
    for line in stream:
        obj = json.loads(line, parse_float=Decimal)
        entries = obj.get('entry', []) if obj.get('resourceType') == 'Bundle' else [{'resource': obj}]
        for entry in entries:
            resource = entry.get('resource')
            if not resource or resource.get('resourceType') not in {'Patient', 'Encounter'}:
                continue
            resource = {field: resource[field] for field in ('resourceType', 'id', 'subject', 'period') if field in resource}
            if resource['resourceType'] == 'Patient':
                resource = {field: resource[field] for field in ('resourceType', 'id')}
            identity = key(resource)
            content = encode(resource)
            old = db.execute('SELECT content FROM resources WHERE key=?', (identity,)).fetchone()
            if old and old[0] != content:
                raise ValueError('Conflicting source versions for ' + identity)
            db.execute('INSERT OR IGNORE INTO resources VALUES (?,?,?)', (identity, resource['resourceType'], content))
            for alias in {identity, entry.get('fullUrl', identity)}:
                old = db.execute('SELECT key FROM aliases WHERE alias=?', (alias,)).fetchone()
                if old and old[0] != identity:
                    raise ValueError('Conflicting reference ' + alias)
                db.execute('INSERT OR IGNORE INTO aliases VALUES (?,?)', (alias, identity))


def resolve(db, reference):
    result = db.execute('SELECT key FROM aliases WHERE alias=?', (reference,)).fetchone()
    return result[0] if result else None


def normalize(db, resource):
    resource = copy.deepcopy(resource)
    for ref in references(resource):
        original = ref['reference']
        target = resolve(db, original)
        if target:
            ref['reference'] = target
        elif original.startswith('urn:'):
            raise ValueError('Unresolved bundle reference: ' + original)
    return resource


def source_index(job_id):
    """Called under the source run lock. Cache is keyed by published result hashes."""
    job = store.get(job_id)
    if not job or job['state'] not in {'succeeded', 'failed'}:
        raise ValueError('Choose a completed run')
    items = datasets.get(job_id).get('datasets', [])
    if not items or any(item.get('error') for item in items):
        raise ValueError('This run has no complete FHIR results')
    fingerprint = hashlib.sha256(('generation-targets-v1:' + json.dumps([(i['id'], i.get('sha256')) for i in items], sort_keys=True)).encode()).hexdigest()
    folder = store.ROOT / 'jobs' / job_id
    target = folder / 'addition-source.sqlite'
    if target.exists():
        with sqlite3.connect(target) as db:
            if db.execute('SELECT fingerprint FROM metadata').fetchone()[0] == fingerprint:
                return target, fingerprint
    with tempfile.TemporaryDirectory(dir=folder, prefix='addition-') as temp:
        temp = Path(temp)
        with sqlite3.connect(temp / 'index.sqlite') as db:
            db.execute('CREATE TABLE resources (key TEXT PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL)')
            db.execute('CREATE TABLE aliases (alias TEXT PRIMARY KEY, key TEXT NOT NULL)')
            db.execute('CREATE TABLE metadata (fingerprint TEXT)')
            db.execute('INSERT INTO metadata VALUES (?)', (fingerprint,))
            for index, item in enumerate(items):
                _, item, archive_path = datasets.resolve_dataset(item['id'], already_locked=True)
                if store.digest(archive_path) != item['sha256']:
                    raise ValueError('Source result checksum changed')
                if item.get('kind') == 'bulk':
                    import bulk_ndjson, bulk_archives
                    if archive_path.name.endswith(bulk_ndjson.SUFFIXES):
                        with bulk_ndjson.open_stream(archive_path) as stream:
                            index_export(db, stream)
                    else:
                        manifest = bulk_archives.read_manifest(archive_path)
                        with zipfile.ZipFile(archive_path) as archive:
                            for name in sorted(manifest['files']):
                                with archive.open(name) as compressed, gzip.GzipFile(fileobj=compressed) as stream:
                                    resource = json.load(stream, parse_float=Decimal)
                                    index_export(db, [encode(resource)])
                    continue
                expanded = temp / str(index)
                expanded.mkdir()
                with zipfile.ZipFile(archive_path) as archive:
                    for member in archive.infolist():
                        if not (expanded / member.filename).resolve().is_relative_to(expanded.resolve()):
                            raise ValueError('Invalid source archive path')
                    archive.extractall(expanded)
                exported = temp / 'resources.ndjson'
                result = subprocess.run(['java', '-Xmx512m', '-cp', str(store.APP / 'synthifhir.jar'),
                    'de.uni_leipzig.life.synthifhir.DatasetUploadExport', str(expanded), str(exported)],
                    capture_output=True, text=True, timeout=600)
                if result.returncode:
                    raise ValueError('Could not read the FHIR results')
                with exported.open() as stream:
                    index_export(db, stream)
                shutil.rmtree(expanded)
            items = datasets.get(job_id).get('datasets', [])
            fingerprint = hashlib.sha256(('generation-targets-v1:' + json.dumps([(i['id'], i.get('sha256')) for i in items], sort_keys=True)).encode()).hexdigest()
            db.execute('UPDATE metadata SET fingerprint=?', (fingerprint,))
            db.execute('CREATE INDEX resource_kind ON resources(kind)')
        (temp / 'index.sqlite').replace(target)
    return target, fingerprint


def targets(db, association):
    """Yield each existing patient or contact once, regardless of clinical resources."""
    kind = 'Patient' if association == 'patient' else 'Encounter'
    for identity, content in db.execute('SELECT key,content FROM resources WHERE kind=? ORDER BY key', (kind,)):
        resource = json.loads(content, parse_float=Decimal)
        if association == 'patient':
            yield identity, None, None
        else:
            patient = resolve(db, resource.get('subject', {}).get('reference', ''))
            if patient and patient.startswith('Patient/'):
                yield patient, identity, resource.get('period', {}).get('start')
            else:
                yield None, identity, None


def summary(db, settings):
    count = skipped = 0
    patients = set()
    for patient, encounter, _ in targets(db, settings.association):
        if patient:
            count += 1
            patients.add(patient)
        else:
            skipped += 1
    return {'resources': count, 'patients': len(patients),
            'cases': count if settings.association == 'encounter' else 0, 'skipped': skipped}


def catalogue(job_id):
    with store.run_lock(job_id):
        path, fingerprint = source_index(job_id)
        with sqlite3.connect(path) as db:
            patient = summary(db, Settings(resourceType='DocumentReference', association='patient'))
            encounter = summary(db, Settings(resourceType='DocumentReference'))
            options = [{'resourceType': kind,
                        'codePath': 'medicationCodeableConcept' if kind.startswith('Medication') else TYPES[kind][2],
                        'statuses': addition_generation.STATUSES[kind],
                        'patient': patient, 'encounter': encounter if TYPES[kind][1] else None} for kind in TYPES]
        return {'sourceRun': job_id, 'fingerprint': fingerprint, 'types': options if patient['resources'] else []}


def create(job_id, settings, fingerprint, request_id):
    validate(settings, generated=True)
    descriptor = {'kind': 'resource-addition', 'sourceRun': job_id, 'settings': settings.model_dump(), 'fingerprint': fingerprint}
    with store.run_lock(job_id):
        def prepare(prepared):
            path, current = source_index(job_id)
            if current != fingerprint:
                raise store.SubmissionConflict('Source results changed; reload the preview')
            identifier = str(uuid4())
            folder = store.ROOT / 'jobs' / identifier
            folder.mkdir(parents=True)
            prepared.append(identifier)
            created = time.time()
            with sqlite3.connect(path) as db, (folder / 'addition.ndjson').open('w') as output:
                counts = summary(db, settings)
                if not counts['resources']:
                    raise ValueError('No existing patients or encounters with resolvable patient references')
                for patient, encounter, timestamp in targets(db, settings.association):
                    if patient:
                        # Instant fields require a complete timestamp; partial contact dates use the saved creation time.
                        date = timestamp if timestamp and 'T' in timestamp and (timestamp.endswith('Z') or '+' in timestamp[10:] or '-' in timestamp[10:]) else addition_generation.fallback_timestamp(created)
                        resource = addition_generation.generate(settings, patient, encounter, date, UUID(identifier))
                        output.write(encode(resource) + '\n')
            import bulk_generation
            source = store.get(job_id)
            snapshot = {'schemaVersion': 1, 'inputKind': 'resource-addition', 'source': 'resource-addition', 'created': time.time(),
                'profile': {'id': 'addition', 'name': settings.resourceType},
                'converterSha256': store.digest(store.APP / 'synthifhir.jar'),
                'inputSha256': store.digest(folder / 'addition.ndjson'),
                'addition': {**descriptor, 'generationVersion': 1, 'counts': counts, 'sourceName': source.get('dataset_name') or job_id[:8]},
                'formats': ['JSON', 'NDJSON'], 'validation': False,
                'resultOutputName': bulk_generation.output_directory_name(identifier, 'Additions', time.time()),
                'datasetName': (source.get('dataset_name') or job_id[:8]) + ' · ' + settings.resourceType}
            (folder / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
            for name in ('snapshot.json', 'addition.ndjson'):
                (folder / name).chmod(0o444)
        return store.submit(descriptor, request_id, prepare)[0]


def transform(resource, settings, new_ids):
    resource = copy.deepcopy(resource)
    resource['id'] = new_ids[key(resource)].split('/')[1]
    if 'meta' in resource:
        resource['meta'].pop('versionId', None)
        resource['meta'].pop('lastUpdated', None)
    for ref in references(resource):
        if ref['reference'] in new_ids:
            ref['reference'] = new_ids[ref['reference']]
    if settings.association == 'patient':
        path = TYPES[settings.resourceType][1]
        if path == 'context.encounter':
            resource.get('context', {}).pop('encounter', None)
        elif path:
            resource.pop(path, None)
    if settings.status:
        resource['status'] = settings.status
    code_path = TYPES[settings.resourceType][2]
    if code_path and (settings.codingMode == 'replace' or settings.codingMode == 'fill' and not resource.get(code_path)):
        codings = [{k: v for k, v in coding.model_dump().items() if v} for coding in settings.codings]
        resource[code_path] = {'coding': [c for c in codings if c]}
        if not resource[code_path]['coding']:
            resource.pop(code_path)
    if settings.resourceType == 'DocumentReference':
        present = any(e.get('url') == NLP_URL for e in resource.get('extension', []))
        if settings.nlpMode in {'replace', 'omit'} or settings.nlpMode == 'fill' and not present:
            extensions = [e for e in resource.get('extension', []) if e.get('url') != NLP_URL]
            if settings.nlpMode != 'omit' and settings.nlpCodes:
                extensions.append({'url': NLP_URL, 'valueCodeableConcept': {'coding': [
                    {'system': NLP_SYSTEM, 'code': c} for c in dict.fromkeys(settings.nlpCodes)]}})
            if extensions:
                resource['extension'] = extensions
            else:
                resource.pop('extension', None)
    return resource


def execute(folder):
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    settings = Settings(**snapshot['addition']['settings'])
    generated = snapshot['addition'].get('generationVersion') == 1
    validate(settings, generated=generated)
    source = folder / 'addition.ndjson'
    if store.digest(source) != snapshot['inputSha256']:
        raise ValueError('Input snapshot changed')
    namespace = UUID(folder.name)
    new_ids = {}
    with source.open() as stream:
        for line in stream:
            resource = json.loads(line, parse_float=Decimal)
            identity = key(resource)
            new_ids[identity] = identity if generated else resource['resourceType'] + '/' + str(uuid5(namespace, identity))
    output = folder / 'output/run-additions/fhir'
    output.mkdir(parents=True, exist_ok=True)
    with source.open() as stream, (output / 'resources.ndjson').open('w') as ndjson, (output / 'resources.json').open('w') as bundle:
        bundle.write('{"resourceType":"Bundle","type":"collection","entry":[')
        for index, line in enumerate(stream):
            resource = json.loads(line, parse_float=Decimal)
            if not generated:
                resource = transform(resource, settings, new_ids)
            ndjson.write(encode(resource) + '\n')
            if index:
                bundle.write(',')
            bundle.write(encode({'resource': resource}))
        bundle.write(']}')
    report = folder / 'output/run-additions/details/reports/additions.json'
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({**snapshot['addition'], 'newIds': new_ids}, ensure_ascii=False, indent=2))
    print(f"Created {len(new_ids)} additional {settings.resourceType} resources")


if __name__ == '__main__':
    execute(Path(sys.argv[1]))
