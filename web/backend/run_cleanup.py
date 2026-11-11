"""Retain reusable run inputs and results, removing expendable local artifacts."""
import csv
import io
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import unquote
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

import datasets
import store
import run_outcomes


def strings(path):
    """Read potential local references without opening their targets."""
    def values(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from values(item)
        elif isinstance(value, list):
            for item in value:
                yield from values(item)
    if path.suffix == '.xlsx':
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name == 'xl/sharedStrings.xml' or name.startswith('xl/worksheets/') and name.endswith('.xml'):
                    with archive.open(name) as member:
                        document = ET.parse(member)
                    for element in document.iter():
                        if element.tag.endswith(('}si', '}is')):
                            yield ''.join(element.itertext())
    elif path.name in {'input.zip', 'csv.zip'}:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                with archive.open(name) as member:
                    if name.endswith('.csv'):
                        for row in csv.reader(io.TextIOWrapper(member, encoding='utf-8-sig')):
                            yield from row
                    elif name.endswith('.json'):
                        yield from values(json.load(member))
    elif path.suffix in {'.ndjson', '.jsonl'}:
        with path.open() as source:
            for line in source:
                if line.strip():
                    yield from values(json.loads(line))
    elif path.suffix == '.json':
        yield from values(json.loads(path.read_text()))
    elif path.suffix == '.xml':
        for _, element in ET.iterparse(path, events=('end',)):
            yield from element.attrib.values()
            if element.text:
                yield element.text
            element.clear()
    elif path.suffix in {'.csv', '.config'}:
        # Conservative: keep a candidate mentioned anywhere in tabular/config text.
        yield path.read_text()


def retained(root, manifest):
    keep = {root / name for name in ('snapshot.json', 'default.config', 'datasets.json',
            'bulk-remainder.zip', 'series-remainder.zip', 'bulk-completion.json', 'bulk-identifier-history.sqlite', 'series-identifiers.sqlite',
            'input.xlsx', 'input.zip', 'addition.ndjson', 'generation.json', 'generation-result.json', 'timing.json', 'failure.json', 'outcomes.json')
            if (root / name).is_file()}
    snapshot = json.loads((root / 'snapshot.json').read_text())
    if snapshot.get('inputKind') == 'workbook-set':
        keep.add(root / 'workbooks.json')
        keep.update((root / 'workbooks').glob('*.xlsx'))
    if snapshot.get('inputKind') == 'synthea-zip':
        keep.discard(root / 'input.zip')
    for path in (root / 'output').rglob('*'):
        parts = path.relative_to(root / 'output').parts
        if path.is_file() and (len(parts) >= 3 and parts[1] in {'fhir', 'excel'}
                or path.suffix == '.config' or path.name in {'environment.json', 'csv.zip', 'additions.json'}):
            keep.add(path)
    for item in manifest['datasets']:
        for file in item['files']:
            path = root / 'output' / file['path']
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise store.SubmissionConflict('A saved result file is unavailable')
            keep.add(path)
    return keep


def cleanup(job_id):
    job_id = str(uuid.UUID(job_id))
    with store.run_lock(job_id), store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise FileNotFoundError('Run not found')
        if row['state'] not in {'succeeded', 'failed'}:
            raise store.SubmissionConflict('Only completed runs can be cleaned up')
        for upload in db.execute("SELECT descriptor FROM uploads WHERE state IN ('queued','preparing','uploading')"):
            if any(item['jobId'] == job_id for item in json.loads(upload['descriptor'])['datasets']):
                raise store.SubmissionConflict('An active upload still uses this run')
        root = store.ROOT / 'jobs' / job_id
        manifest = datasets.get(job_id)
        if manifest.get('pending'):
            raise store.SubmissionConflict('Wait until dataset inspection has finished')
        all_files = {path for path in root.rglob('*') if path.is_file() or path.is_symlink()}
        if any(path.is_symlink() for path in root.rglob('*')):
            raise store.SubmissionConflict('Run contains linked files; cleanup cannot safely determine dependencies')
        snapshot = json.loads((root / 'snapshot.json').read_text())
        required = ('snapshot.json', 'addition.ndjson') if snapshot.get('inputKind') == 'resource-addition' else ('snapshot.json', 'default.config')
        for name in required:
            if not (root / name).is_file():
                raise store.SubmissionConflict('Saved configuration is unavailable')
        keep = retained(root, manifest)
        # Inputs in other runs/uploads may refer to absolute files in this run.
        # The write transaction prevents new submissions while dependencies are checked.
        candidates = all_files - keep
        by_name = {}
        for path in candidates:
            by_name.setdefault(path.name, []).append(path)
        sources = {path for path in keep if path.name in {'input.zip', 'csv.zip'} or path.suffix in {'.xlsx', '.csv'} or 'fhir' in path.relative_to(root).parts}
        for path in (store.ROOT / 'jobs').rglob('*'):
            if path.is_symlink() and not path.is_relative_to(root):
                target = path.resolve()
                keep.update(file for file in all_files if file.resolve() == target or file.resolve().is_relative_to(target))
            if path.is_file() and not path.is_relative_to(root) and (path.name in {'input.zip', 'input.json', 'csv.zip'} or path.suffix == '.xlsx' or 'fhir' in path.parts):
                sources.add(path)
        sources.update((store.ROOT / 'inputs').glob('*/workbooks/*.xlsx'))
        for path in (store.ROOT / 'inputs').glob('*/input.*'):
            if path.suffix in {'.xlsx', '.zip', '.json'}:
                sources.add(path)
        checked = set()
        while sources - checked:
            path = (sources - checked).pop()
            checked.add(path)
            try:
                for value in strings(path):
                    # Match filenames conservatively, including paths embedded in CSV/config strings.
                    for token in re.split(r'''[/\\\s"'=;:,<>]+''', unquote(value)):
                        for match in by_name.get(token, []):
                            if match not in keep:
                                keep.add(match)
                                sources.add(match)
            except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as error:
                raise store.SubmissionConflict(f'Cannot verify file dependencies in {path.name}') from error
        if not (root / 'outcomes.json').is_file():
            run_outcomes.save(root, json.loads((root / 'snapshot.json').read_text()), row['state'])
            keep.add(root / 'outcomes.json')
        removed = all_files - keep - {root / 'cleanup.json'}
        record = {'completed': time.time(), 'removedBytes': sum(path.stat().st_size for path in removed),
                  'removedFiles': len(removed), 'files': [
                      {'path': path.relative_to(root).as_posix(), 'sha256': store.digest(path)}
                      for path in sorted(keep) if path.name != 'datasets.json']}
        manifest['artifacts'] = [item for item in manifest['artifacts'] if root / 'output' / item['path'] in keep]
        # Publish the retained inventory before deleting expendable files; retries are safe.
        temporary = root / 'datasets.tmp'
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        temporary.replace(root / 'datasets.json')
        marker = root / 'cleanup.tmp'
        marker.write_text(json.dumps(record, indent=2))
        marker.replace(root / 'cleanup.json')
        for path in removed:
            path.unlink()
        for path in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    return store.get(job_id)


def archive(job_id):
    """Download the retained run without storing another redundant archive."""
    with store.run_lock(job_id):
        root = store.ROOT / 'jobs' / job_id
        if not (root / 'cleanup.json').is_file():
            raise FileNotFoundError('Cleaned run not found')
        handle = tempfile.NamedTemporaryFile(prefix='synthifhir-run-', suffix='.zip', delete=False)
        destination = Path(handle.name)
        handle.close()
        try:
            with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as output:
                inventory = json.loads((root / 'cleanup.json').read_text())['files']
                names = {item['path'] for item in inventory} | {'datasets.json', 'cleanup.json'}
                for name in sorted(names):
                    path = root / name
                    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                        raise store.SubmissionConflict('Retained file is unavailable')
                    output.write(path, name)
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
