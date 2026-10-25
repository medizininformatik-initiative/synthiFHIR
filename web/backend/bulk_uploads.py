"""Bounded, resumable FHIR transactions read directly from immutable dataset packages."""
from decimal import Decimal
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
import zipfile

import bulk_archives
import bulk_ndjson
import datasets
import store


def source(item):
    _, current, path = datasets.resolve_dataset(item['id'])
    snapshot = store.ROOT / 'jobs' / item['jobId'] / 'snapshot.json'
    if store.digest(snapshot) != item['snapshotSha256'] or store.digest(path) != item['sha256']:
        raise ValueError('Dataset or configuration changed after submission')
    if current.get('kind') != item.get('kind'):
        raise ValueError('Dataset format changed after submission')
    return path


def bundles(item, root, run, log, cancelled):
    path = source(item)
    if item.get('kind') == 'bulk':
        if path.name.endswith(bulk_ndjson.SUFFIXES):
            with bulk_ndjson.open_stream(path) as stream:
                for line in stream:
                    if cancelled():
                        raise InterruptedError()
                    yield json.loads(line, parse_float=Decimal)
            return
        manifest = bulk_archives.read_manifest(path, verify=False)
        with zipfile.ZipFile(path) as archive:
            for name in sorted(manifest['files']):
                if cancelled():
                    raise InterruptedError()
                with archive.open(name) as compressed, gzip.GzipFile(fileobj=compressed) as stream:
                    yield json.load(stream, parse_float=Decimal)
    else:
        # Ordinary datasets retain their format selection and XML support through the converter.
        work = root / 'ordinary-work'
        if work.exists():
            shutil.rmtree(work)
        work.mkdir()
        expanded = work / 'input'
        expanded.mkdir()
        try:
            with zipfile.ZipFile(path) as archive:
                for entry in archive.infolist():
                    if not (expanded / entry.filename).resolve().is_relative_to(expanded.resolve()):
                        raise ValueError('Invalid dataset archive path')
                archive.extractall(expanded)
            output = work / 'export.ndjson'
            run(['java', '-Xmx512m', '-cp', str(store.APP / 'synthifhir.jar'),
                 'de.uni_leipzig.life.synthifhir.DatasetUploadExport', str(expanded), str(output)], log, 600)
            with output.open() as stream:
                for line in stream:
                    if cancelled():
                        raise InterruptedError()
                    yield json.loads(line, parse_float=Decimal)
        finally:
            shutil.rmtree(work)


def transaction(obj, dataset, db=None):
    from fhir_uploads import Conflict, encode
    entries = obj.get('entry', []) if obj.get('resourceType') == 'Bundle' else [{'resource': obj}]
    output = {'resourceType': 'Bundle', 'type': 'transaction', 'entry': []}
    seen = set()
    for entry in entries:
        resource = entry.get('resource', {})
        kind, identifier = resource.get('resourceType', ''), resource.get('id', '')
        if not re.fullmatch(r'[A-Z][A-Za-z0-9]+', kind) or not re.fullmatch(r'[A-Za-z0-9.-]{1,64}', identifier):
            raise ValueError('Every uploaded resource needs a resource type and stable FHIR ID')
        key = kind + '/' + identifier
        if db is not None:
            digest = hashlib.sha256(encode(resource).encode()).hexdigest()
            previous = db.execute('SELECT digest,dataset FROM resources WHERE key=?', (key,)).fetchone()
            if previous and previous[0] != digest:
                raise Conflict(key, previous[1], dataset)
            if not previous:
                db.execute('INSERT INTO resources VALUES (?,?,?)', (key, digest, dataset))
        if key in seen:
            continue
        seen.add(key)
        item = {'resource': resource, 'request': {'method': 'PUT', 'url': key}}
        if entry.get('fullUrl'):
            item['fullUrl'] = entry['fullUrl']
        output['entry'].append(item)
    if not output['entry']:
        raise ValueError('An uploaded patient bundle contains no resources')
    return output


def send(url, bundle):
    from fhir_uploads import encode
    request = urllib.request.Request(url, data=encode(bundle).encode(), method='POST',
                                     headers={'Content-Type': 'application/fhir+json', 'Accept': 'application/fhir+json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(16384).decode(errors='replace')
        raise RuntimeError(f'FHIR server HTTP {error.code}: {detail}') from error
    entries = result.get('entry', [])
    if (result.get('resourceType') != 'Bundle' or result.get('type') != 'transaction-response'
            or len(entries) != len(bundle['entry'])
            or any(not re.match(r'^2\d\d(?:\s|$)', entry.get('response', {}).get('status', '')) for entry in entries)):
        raise RuntimeError('FHIR server did not confirm every resource in the transaction: ' + json.dumps(result)[:16384])


def execute(identifier, descriptor, root, summary, run, log, cancelled):
    from fhir_uploads import finish, target_status
    from bulk_generation import atomic_json
    checkpoint = root / 'upload-checkpoint.json'
    signature = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {'signature': signature, 'accepted': 0}
    if saved['signature'] != signature:
        raise ValueError('Upload checkpoint belongs to a different selection')
    accepted = saved['accepted']
    summary.update(acceptedBundles=accepted, checkedBundles=0, packages=len(descriptor['datasets']),
                   completedPackages=0, mayHaveWritten=bool(summary.get('mayHaveWritten') or accepted), streaming=True)
    last_update = 0
    def update(state, force=False):
        nonlocal last_update
        if force or time.monotonic() - last_update >= 2:
            finish(identifier, state, summary)
            last_update = time.monotonic()
    # Compare resource hashes across the entire selection before any server writes.
    index = root / 'resource-index.sqlite'
    index.unlink(missing_ok=True)
    try:
        with sqlite3.connect(index) as db:
            db.execute('CREATE TABLE resources (key TEXT PRIMARY KEY, digest TEXT NOT NULL, dataset TEXT NOT NULL)')
            for item in descriptor['datasets']:
                for obj in bundles(item, root, run, log, cancelled):
                    transaction(obj, item['id'], db)
                    summary['checkedBundles'] += 1
                    if summary['checkedBundles'] % 1000 == 0:
                        db.commit()
                    update('preparing')
            summary.update(bundles=summary['checkedBundles'], resources=db.execute('SELECT COUNT(*) FROM resources').fetchone()[0])
    finally:
        index.unlink(missing_ok=True)
    if not summary['bundles'] or accepted > summary['bundles']:
        raise ValueError('Invalid patient count or upload checkpoint')
    if cancelled():
        raise InterruptedError()
    if not target_status(descriptor['target']['id'])['available']:
        raise ValueError('Target FHIR R4 transaction server is unavailable')
    update('uploading', True)
    current = 0
    for package_index, item in enumerate(descriptor['datasets']):
        summary['currentPackage'] = package_index + 1
        for obj in bundles(item, root, run, log, cancelled):
            current += 1
            if current <= accepted:
                continue
            if cancelled():
                raise InterruptedError()
            summary['mayHaveWritten'] = True
            update('uploading', not accepted)
            send(descriptor['target']['url'], transaction(obj, item['id']))
            # A crash before this write retries a PUT with the same resource IDs and content.
            accepted = current
            atomic_json(checkpoint, {'signature': signature, 'accepted': accepted})
            summary['acceptedBundles'] = accepted
            update('uploading')
        summary['completedPackages'] = package_index + 1
        update('uploading', True)
        log.write(f'Package {package_index + 1}/{len(descriptor["datasets"])} complete; {accepted} patient bundles confirmed\n')
        log.flush()
    finish(identifier, 'succeeded', summary)
