"""Explicit, persistent dataset loading through the pinned blazectl executable."""
import concurrent.futures
from decimal import Decimal
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import uuid
import zipfile

import datasets
import store

TARGETS = {
    'blaze': {'name': 'Blaze', 'url': 'http://blaze:8080/fhir', 'address': 'http://localhost:5190/fhir', 'concurrency': 1},
    'hapi': {'name': 'HAPI', 'url': 'http://hapi:8080/fhir', 'address': 'http://localhost:5191/fhir', 'concurrency': 1},
}
BLAZECTL_VERSION = '1.6.0'


def target_status(identifier):
    target = TARGETS[identifier]
    result = {'id': identifier, **target, 'available': False}
    try:
        with urllib.request.urlopen(urllib.request.Request(target['url'] + '/metadata', headers={'Accept': 'application/fhir+json'}), timeout=2) as response:
            metadata = json.load(response)
        result['available'] = metadata.get('resourceType') == 'CapabilityStatement' and str(metadata.get('fhirVersion', '')).startswith('4.0') and any(
            interaction.get('code') == 'transaction' for rest in metadata.get('rest', [])
            if rest.get('mode') == 'server' for interaction in rest.get('interaction', []))
    except (OSError, ValueError):
        pass
    return result


def targets():
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(target_status, TARGETS))


def folder(identifier):
    return store.ROOT / 'uploads' / str(uuid.UUID(identifier))


def get(identifier):
    with store.connect() as db:
        row = db.execute('SELECT * FROM uploads WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise FileNotFoundError('Upload not found')
    result = json.loads(row['result']) if row['result'] else None
    if row['state'] not in {'queued', 'preparing', 'uploading'}:
        evidence = server_responses(folder(identifier) / 'upload.log')
        if evidence:
            result = {**(result or {}), **evidence}
    descriptor = json.loads(row['descriptor'])
    return {**dict(row), 'descriptor': descriptor, 'result': result,
            'resumable': bool(descriptor.get('streaming')) and row['state'] in {'failed', 'cancelled', 'interrupted'}}


def server_responses(path):
    """Read blazectl's HTTP summary and original OperationOutcome diagnostics."""
    if not path.is_file():
        return {}
    counts = {}
    errors = []
    current = None
    with path.open(errors='replace') as stream:
        for raw in stream:
            line = raw.strip()
            if line.startswith('Status Codes'):
                counts = {code: int(count) for code, count in re.findall(r'(\d{3}):(\d+)', line)}
            match = re.match(r'File: (.*?) \[Bundle: (\d+)\]', line)
            if match:
                current = {'file': Path(match[1]).name, 'bundle': int(match[2]), 'diagnostics': []}
                errors.append(current)
            elif current is not None:
                match = re.match(r'(StatusCode|Severity|Code|Diagnostics)\s*:\s*(.*)', line)
                if match:
                    key, value = match.groups()
                    if key == 'Diagnostics':
                        current['diagnostics'].append(value)
                    else:
                        current[{'StatusCode': 'status', 'Severity': 'severity', 'Code': 'code'}[key]] = value
    result = {}
    if counts:
        result.update(statusCounts=counts,
                      acceptedBundles=sum(count for code, count in counts.items() if 200 <= int(code) < 300),
                      rejectedBundles=sum(count for code, count in counts.items() if int(code) >= 400),
                      otherResponses=sum(count for code, count in counts.items() if not 200 <= int(code) < 300 and int(code) < 400))
    if errors:
        result['serverErrors'] = errors
    return result


def history():
    with store.connect() as db:
        rows = db.execute('SELECT id FROM uploads ORDER BY created DESC').fetchall()
    return [get(row['id']) for row in rows]


def history_page(limit):
    with store.connect() as db:
        rows = db.execute("""SELECT id FROM uploads WHERE state IN ('queued','preparing','uploading')
            OR id IN (SELECT id FROM uploads WHERE state NOT IN ('queued','preparing','uploading')
                      ORDER BY created DESC, id DESC LIMIT ?)
            ORDER BY created DESC, id DESC""", (limit,)).fetchall()
        count = db.execute("SELECT COUNT(*) FROM uploads WHERE state NOT IN ('queued','preparing','uploading')").fetchone()[0]
    items = []
    for row in rows:
        try:
            items.append(get(row['id']))
        except FileNotFoundError:
            pass  # Another request may remove completed history while polling.
    return {'items': items, 'hasMore': count > limit, 'completedCount': count}


def delete_history(identifier=None):
    """Remove only local completed upload records and their working files."""
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if identifier is not None:
            row = db.execute('SELECT state FROM uploads WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise FileNotFoundError('Upload not found')
            if row['state'] in {'queued', 'preparing', 'uploading'}:
                raise store.SubmissionConflict('Active uploads cannot be removed')
        rows = db.execute("""SELECT id FROM uploads WHERE state NOT IN ('queued','preparing','uploading')
            AND (? IS NULL OR id=?)""", (identifier, identifier)).fetchall()
        for row in rows:
            path = folder(row['id'])
            if path.exists():
                shutil.rmtree(path)
            db.execute('DELETE FROM uploads WHERE id=?', (row['id'],))


def create(identifier, target, selection):
    if target not in TARGETS or not selection or len(set(selection)) != len(selection):
        raise ValueError('Invalid server or dataset selection')
    identifier = str(uuid.UUID(identifier))
    # A retry keeps the original immutable selection, even if the source is later unavailable.
    with store.connect() as db:
        existing = db.execute('SELECT descriptor FROM uploads WHERE id=?', (identifier,)).fetchone()
    if existing:
        previous = json.loads(existing['descriptor'])
        if previous['target']['id'] != target or [d['id'] for d in previous['datasets']] != sorted(selection):
            raise store.SubmissionConflict('This upload ID was already used for another selection')
        return get(identifier)
    selected = []
    archive_paths = {}
    for dataset_id in sorted(selection):
        job, item, path = datasets.resolve_dataset(dataset_id)
        if job['state'] != 'succeeded' or item.get('error') or not item.get('sha256') or not path.is_file():
            raise ValueError('Select complete datasets from successful runs')
        archive_paths[dataset_id] = path
        selected.append({'id': item['id'], 'name': item['name'], 'jobId': job['id'], 'sha256': item['sha256'],
                         'snapshotSha256': store.digest(store.ROOT / 'jobs' / job['id'] / 'snapshot.json'),
                         **({'kind': 'bulk', 'inspection': item.get('inspection'), 'size': item['size']} if item.get('kind') == 'bulk' else {}),
                         'datasetName': job.get('dataset_name'), 'source': job.get('source'),
                         'sourceName': job.get('source_name'), 'inputKind': job.get('input_kind'),
                         'generation': job.get('generation'), 'summary': job.get('summary')})
    descriptor = {'target': {'id': target, **TARGETS[target]}, 'datasets': selected,
                  'blazectlVersion': BLAZECTL_VERSION, 'converterSha256': store.digest(store.APP / 'synthifhir.jar')}
    if any(item.get('kind') == 'bulk' for item in selected):
        descriptor['streaming'] = True
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for item in selected:
            if db.execute('SELECT id FROM jobs WHERE id=?', (item['jobId'],)).fetchone() is None:
                raise ValueError('A selected run was deleted')
            archive_path = archive_paths[item['id']]
            if not archive_path.is_file() or (archive_path.stat().st_size != item['size'] if item.get('kind') == 'bulk' else store.digest(archive_path) != item['sha256']):
                raise store.SubmissionConflict('A selected run was cleaned up; select its dataset again')
        db.execute('INSERT OR IGNORE INTO uploads(id,state,created,descriptor) VALUES (?, ?, ?, ?)',
                   (identifier, 'queued', time.time(), json.dumps(descriptor)))
    current = get(identifier)
    if current['descriptor'] != descriptor:
        raise store.SubmissionConflict('This upload ID was already used for another selection')
    return current


def resume(identifier):
    upload = get(identifier)
    if not upload['descriptor'].get('streaming'):
        raise ValueError('This upload does not support continuation')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM uploads WHERE id=?', (identifier,)).fetchone()
        if row['state'] == 'queued':
            return upload
        if row['state'] not in {'failed', 'cancelled', 'interrupted'}:
            raise store.SubmissionConflict('Only stopped uploads can be continued')
        db.execute("UPDATE uploads SET state='queued',cancel=0 WHERE id=?", (identifier,))
    return get(identifier)


def claim():
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT id FROM uploads WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE uploads SET state='preparing' WHERE id=?", (row['id'],))
            return row['id']


def finish(identifier, state, result=None):
    with store.connect() as db:
        db.execute('UPDATE uploads SET state=?,result=? WHERE id=?', (state, json.dumps(result), identifier))


def cancel(identifier):
    get(identifier)
    with store.connect() as db:
        db.execute("UPDATE uploads SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE id=? AND state IN ('queued','preparing','uploading')", (identifier,))


def recover():
    with store.connect() as db:
        db.execute("UPDATE uploads SET state='interrupted' WHERE state IN ('preparing','uploading')")


def logs(identifier):
    get(identifier)
    path = folder(identifier) / 'upload.log'
    if not path.is_file():
        return ''
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 128 * 1024))
        return stream.read().decode('utf-8', errors='replace')


class Conflict(ValueError):
    def __init__(self, key, first, second):
        super().__init__(key)
        self.details = {'code': 'conflict', 'resource': key, 'datasets': [first, second]}


def encode(value):
    """Preserve arbitrary-precision FHIR decimals when preparing upload copies."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('Non-finite FHIR number')
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key) + ':' + encode(value[key]) for key in sorted(value)) + '}'
    if isinstance(value, list):
        return '[' + ','.join(encode(item) for item in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def prepare_transactions(exports, destination, cancelled=lambda: False):
    """Compare all selected contents before any network write; keep bundle boundaries."""
    destination.mkdir()
    count = 0
    with sqlite3.connect(destination.parent / 'resource-index.sqlite') as db:
        db.execute('CREATE TABLE resources (key TEXT PRIMARY KEY, content TEXT NOT NULL, dataset TEXT NOT NULL)')
        for dataset_id, path in exports:
            with path.open() as stream, (destination / f'{len(list(destination.iterdir())):04d}.ndjson').open('w') as output:
                for line in stream:
                    if cancelled():
                        raise InterruptedError()
                    obj = json.loads(line, parse_float=Decimal)
                    entries = obj.get('entry', []) if obj.get('resourceType') == 'Bundle' else [{'resource': obj}]
                    transaction = {'resourceType': 'Bundle', 'type': 'transaction', 'entry': []}
                    seen = set()
                    for entry in entries:
                        resource = entry.get('resource', {})
                        kind, identifier = resource.get('resourceType', ''), resource.get('id', '')
                        if not re.fullmatch(r'[A-Z][A-Za-z0-9]+', kind) or not re.fullmatch(r'[A-Za-z0-9.-]{1,64}', identifier):
                            raise ValueError('Every uploaded resource needs a resource type and stable FHIR ID')
                        key = kind + '/' + identifier
                        content = encode(resource)
                        previous = db.execute('SELECT content,dataset FROM resources WHERE key=?', (key,)).fetchone()
                        if previous and previous[0] != content:
                            raise Conflict(key, previous[1], dataset_id)
                        if not previous:
                            db.execute('INSERT INTO resources VALUES (?,?,?)', (key, content, dataset_id))
                        if key in seen:
                            continue
                        seen.add(key)
                        item = {'resource': resource, 'request': {'method': 'PUT', 'url': key}}
                        if entry.get('fullUrl'):
                            item['fullUrl'] = entry['fullUrl']
                        transaction['entry'].append(item)
                    if transaction['entry']:
                        output.write(encode(transaction) + '\n')
                        count += 1
        resources = db.execute('SELECT COUNT(*) FROM resources').fetchone()[0]
    if not count:
        raise ValueError('No resources found in the selected datasets')
    return {'bundles': count, 'resources': resources}


def execute(identifier, stopping, terminate):
    root = folder(identifier)
    root.mkdir(parents=True, exist_ok=True)
    descriptor = get(identifier)['descriptor']
    (root / 'snapshot.json').write_text(json.dumps(descriptor, indent=2))
    summary = (get(identifier).get('result') or {'mayHaveWritten': False}) if descriptor.get('streaming') else {'mayHaveWritten': False}
    summary.pop('error', None)
    def cancelled():
        return stopping() or bool(get(identifier)['cancel'])
    def run(command, log, timeout):
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise InterruptedError()
                if time.monotonic() - start > timeout:
                    raise TimeoutError('Upload step timed out')
                time.sleep(0.2)
            if process.returncode:
                log.flush()
                text = (root / 'upload.log').read_text(errors='replace')
                if 'connection refused' in text or ' : EOF' in text:
                    raise RuntimeError('The connection to the FHIR server failed during upload. The server may have restarted. No complete upload was confirmed; see upload log.')
                if 'OutOfMemoryError' in text:
                    raise RuntimeError('The upload failed because memory was exhausted; see upload log.')
                raise RuntimeError('Command failed; see upload log')
        finally:
            terminate(process)
    try:
        with (root / 'upload.log').open('a' if descriptor.get('streaming') else 'w') as log:
            if store.digest(store.APP / 'synthifhir.jar') != descriptor['converterSha256']:
                raise ValueError('Converter image changed after submission')
            if descriptor.get('streaming'):
                import bulk_uploads
                bulk_uploads.execute(identifier, descriptor, root, summary, run, log, cancelled)
                return
            exports = []
            for index, item in enumerate(descriptor['datasets']):
                if cancelled():
                    raise InterruptedError()
                _, _, source = datasets.resolve_dataset(item['id'])
                archive_path = root / f'dataset-{index}.zip'
                shutil.copyfile(source, archive_path)
                if store.digest(archive_path) != item['sha256']:
                    raise ValueError('Dataset archive changed after submission')
                if store.digest(source.parent / 'snapshot.json') != item['snapshotSha256']:
                    raise ValueError('Run snapshot changed after submission')
                shutil.copyfile(source.parent / 'snapshot.json', root / f'run-{index}.json')
                expanded = root / f'dataset-{index}'
                expanded.mkdir()
                with zipfile.ZipFile(archive_path) as archive:
                    for entry in archive.infolist():
                        target = expanded / entry.filename
                        if not target.resolve().is_relative_to(expanded.resolve()):
                            raise ValueError('Invalid dataset archive path')
                    archive.extractall(expanded)
                exported = root / f'export-{index}.ndjson'
                run(['java', '-Xmx512m', '-cp', str(store.APP / 'synthifhir.jar'),
                     'de.uni_leipzig.life.synthifhir.DatasetUploadExport', str(expanded), str(exported)], log, 600)
                exports.append((item['id'], exported))
            summary.update(prepare_transactions(exports, root / 'transactions', cancelled))
            if cancelled():
                raise InterruptedError()
            if not target_status(descriptor['target']['id'])['available']:
                raise ValueError('Target FHIR R4 transaction server is unavailable')
            run(['blazectl', '--version'], log, 10)
            summary['mayHaveWritten'] = True
            finish(identifier, 'uploading', summary)
            run(['blazectl', 'upload', '--no-progress', '--server', descriptor['target']['url'],
                 '--concurrency', str(descriptor['target']['concurrency']), str(root / 'transactions')], log, 3600)
            finish(identifier, 'succeeded', summary)
    except Conflict as error:
        finish(identifier, 'conflict', {**summary, **error.details})
    except InterruptedError:
        finish(identifier, 'interrupted' if stopping() else 'cancelled', summary)
    except Exception as error:
        with (root / 'upload.log').open('a') as log:
            log.write('\n' + str(error) + '\n')
        finish(identifier, 'failed', {**summary, 'error': str(error)})
