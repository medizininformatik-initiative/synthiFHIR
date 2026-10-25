"""Atomic optionally gzip-compressed NDJSON packages with checkpoint metadata for resumable generation."""
from contextlib import nullcontext
from decimal import Decimal
import gzip
import hashlib
import json
import re
import shutil
import time

import store

SUFFIX = '.ndjson.gz'
SUFFIXES = ('.ndjson.gz', '.ndjson')

def suffix(snapshot):
    if snapshot.get('bulkLayoutVersion', 0) < 5:
        return '.zip'
    return SUFFIX if snapshot.get('generation', {}).get('bulkGzip', True) else '.ndjson'

def open_stream(path, mode='rt'):
    return gzip.open(path, mode) if path.name.endswith(SUFFIX) else path.open(mode)
# Retain decimal literals and all string contents when compacting generator JSON.
TOKENS = re.compile(rb'"(?:[^"\\]|\\.)*"|[ \t\r\n]+')


def manifest_path(path):
    return path.with_name('.' + path.name + '.manifest')


def read_manifest(path, verify=True, update=None):
    manifest = json.loads(manifest_path(path).read_text())
    if manifest.get('version') != 2 or path.stat().st_size != manifest['size']:
        raise ValueError('NDJSON package differs from its checkpoint')
    if verify and store.digest(path) != manifest['sha256']:
        raise ValueError('NDJSON package checksum differs from its checkpoint')
    return manifest


def line(raw):
    bundle = json.loads(raw, parse_float=Decimal)
    if bundle.get('resourceType') != 'Bundle':
        raise ValueError('Expected a FHIR Bundle')
    ids = [e['resource']['id'] for e in bundle.get('entry', [])
           if e.get('resource', {}).get('resourceType') == 'Patient']
    if bundle.get('type') != 'transaction':
        # Native Synthea provider collections need explicit transactions for blazectl.
        from fhir_uploads import encode
        bundle['type'] = 'transaction'
        for entry in bundle.get('entry', []):
            resource = entry['resource']
            entry['request'] = {'method': 'PUT', 'url': resource['resourceType'] + '/' + resource['id']}
        raw = encode(bundle).encode('utf-8')
    else:
        raw = TOKENS.sub(lambda match: match[0] if match[0].startswith(b'"') else b'', raw)
    return raw + b'\n', ids


class DigestWriter:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def write(self, data):
        self.digest.update(data)
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()


def package(directory, parts, destination, update, chunks=None):
    if destination.exists():
        manifest = read_manifest(destination)
        if set(manifest['chunks']) != set(parts) or (chunks is not None and manifest['chunks'] != chunks):
            raise ValueError('Existing NDJSON checkpoint differs from this run')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name('.' + destination.name + '.partial')
        manifest = {'version': 2, 'chunks': chunks or {part: {} for part in parts}, 'files': {}}
        count = sum(len(list((directory / part).glob('*.json.gz'))) for part in parts)
        written = 0
        last_update = time.monotonic()
        with pending.open('wb') as stream:
            sink = DigestWriter(stream)
            with (gzip.GzipFile(filename='', fileobj=sink, mode='wb', compresslevel=6, mtime=0)
                  if destination.name.endswith(SUFFIX) else nullcontext(sink)) as output:
                for part in parts:
                    sources = sorted((directory / part).glob('*.json.gz'))
                    if not sources:
                        raise ValueError('Cannot package an empty patient chunk')
                    for source in sources:
                        compressed = source.read_bytes()
                        data, ids = line(gzip.decompress(compressed))
                        entry = {'size': len(compressed), 'sha256': hashlib.sha256(compressed).hexdigest(), 'patientIds': ids}
                        if source.name.startswith('support-'):
                            entry['role'] = 'support'
                        manifest['files'][part + '/' + source.name] = entry
                        output.write(data)
                        written += 1
                        if written == count or time.monotonic() - last_update >= 2:
                            update(written, count)
                            last_update = time.monotonic()
            manifest.update(size=stream.tell(), sha256=sink.digest.hexdigest())
        # Metadata first; only the payload rename publishes a complete package.
        from bulk_generation import atomic_json
        atomic_json(manifest_path(destination), manifest)
        pending.replace(destination)
    # After a crash during cleanup, verify remaining chunks before removing them.
    for part in parts:
        directory_part = directory / part
        for source in directory_part.glob('*'):
            entry = manifest['files'].get(part + '/' + source.name)
            if not entry or not source.is_file() or store.digest(source) != entry['sha256']:
                raise ValueError('Loose file differs from the NDJSON checkpoint')
        if directory_part.exists():
            shutil.rmtree(directory_part)
    return destination.stat().st_size
