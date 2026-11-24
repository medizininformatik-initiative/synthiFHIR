"""Immutable uploaded inputs, structurally inspected by the Java converter."""
from contextlib import contextmanager
import fcntl
import json
import shutil
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
from uuid import UUID, uuid4
import zipfile

import store

MAX_UPLOAD = 64 * 1024 * 1024
MAX_UNCOMPRESSED = 256 * 1024 * 1024


def checked_name(name):
    name = unicodedata.normalize('NFC', name.strip())
    if not name.lower().endswith(('.xlsx', '.zip', '.json')) or len(name) > 200 or any(c in name for c in '/\\'):
        raise ValueError('Choose an .xlsx workbook, CSV .zip archive or Synthea .json / .zip input with a filename of at most 200 characters')
    if any(unicodedata.category(c).startswith('C') for c in name):
        raise ValueError('The filename contains control characters')
    return name


@contextmanager
def incoming():
    root = store.ROOT / 'inputs'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.incoming-', dir=root) as directory:
        yield Path(directory)


def input_filename(kind):
    try:
        return {'resource-addition': 'addition.ndjson', 'workbook-set': 'workbooks.json', 'workbook': 'input.xlsx', 'csv': 'input.zip', 'synthea': 'input.json', 'synthea-zip': 'input.zip', 'synthea-generation': 'generation.json'}[kind]
    except KeyError as error:
        raise ValueError('Unknown input kind') from error


def kind_for_name(name):
    return 'csv' if name.lower().endswith('.zip') else 'synthea' if name.lower().endswith('.json') else 'workbook'


def zip_entries(archive):
    entries = archive.infolist()
    if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED:
        raise ValueError('The input exceeds the supported expanded size (256 MiB)')
    if any(entry.flag_bits & 1 for entry in entries):
        raise ValueError('Encrypted archives are not supported')
    return entries


def extract_archive(path, destination, suffix):
    destination.mkdir()
    with zipfile.ZipFile(path) as archive:
        entries = zip_entries(archive)
        names = set()
        for entry in entries:
            name = entry.filename
            if (not name.endswith(suffix) or '/' in name or '\\' in name or len(name) > 200
                    or any(unicodedata.category(c).startswith('C') for c in name)
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError(f'Archives must contain only {suffix} files directly at the archive root')
            if name.casefold() in names:
                raise ValueError('Archive contains duplicate filenames')
            names.add(name.casefold())
        total = 0
        for entry in entries:
            with archive.open(entry) as source, (destination / entry.filename).open('xb') as output:
                while chunk := source.read(65536):
                    total += len(chunk)
                    if total > MAX_UNCOMPRESSED:
                        raise ValueError('The expanded input exceeds 256 MiB')
                    output.write(chunk)


def extract_csv(path, destination):
    extract_archive(path, destination, '.csv')


def inspect(path):
    archive_input = path.suffix == '.zip'
    kind = kind_for_name(path.name)
    expanded = path.parent / 'inspection-input'
    report = path.with_suffix('.inspection.json')
    try:
        if archive_input:
            with zipfile.ZipFile(path) as archive:
                entries = zip_entries(archive)
                kind = 'synthea-zip' if entries and all(e.filename.endswith('.json') for e in entries) else 'csv'
            extract_archive(path, expanded, '.csv' if kind == 'csv' else '.json')
        elif kind == 'workbook':
            with zipfile.ZipFile(path) as archive:
                zip_entries(archive)
                if not {'[Content_Types].xml', 'xl/workbook.xml'}.issubset(archive.namelist()):
                    raise ValueError('The file is not an Excel workbook')
        with (store.ROOT / 'inputs' / '.inspection.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            command = ([sys.executable, str(Path(__file__).with_name('synthea_input.py'))]
                       if kind.startswith('synthea') else
                       ['java', '-Xmx256m', '-cp', str(store.APP / 'synthifhir.jar'),
                        'de.uni_leipzig.life.synthifhir.' + ('CsvInputPreflight' if kind == 'csv' else 'WorkbookPreflight')])
            checked = subprocess.run([*command, str(expanded if archive_input else path), str(report)],
                                     capture_output=True, text=True, timeout=60)
        if checked.returncode:
            raise ValueError('The input could not be inspected. Check that it uses the supported template, CSV schema or Synthea R4 bundle structure.')
        result = json.loads(report.read_text())
        if not result['valid']:
            raise ValueError('Input structure is invalid: ' + '; '.join(result['issues']))
        result['kind'] = kind
        return result
    except zipfile.BadZipFile as error:
        raise ValueError('The file is not a readable .xlsx workbook or .zip archive') from error
    except subprocess.TimeoutExpired as error:
        raise ValueError('Input inspection exceeded 60 seconds') from error
    finally:
        report.unlink(missing_ok=True)
        if expanded.exists():
            shutil.rmtree(expanded)


def publish(directory, name):
    name = checked_name(name)
    kind = kind_for_name(name)
    path = directory / input_filename(kind)
    if not 0 < path.stat().st_size <= MAX_UPLOAD:
        raise ValueError('The input must be between 1 byte and 64 MiB')
    report = inspect(path)
    kind = report.get('kind', kind)
    identifier = str(uuid4())
    item = {'id': 'upload:' + identifier, 'name': name, 'kind': kind, 'created': time.time(), 'size': path.stat().st_size,
            'sha256': store.digest(path), 'inspection': report}
    (directory / 'metadata.json').write_text(json.dumps(item, ensure_ascii=False, indent=2))
    path.chmod(0o444)
    (directory / 'metadata.json').chmod(0o444)
    directory.rename(store.ROOT / 'inputs' / identifier)
    return item


@contextmanager
def library_lock():
    root = store.ROOT / 'inputs'
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.library.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def delete(identifier):
    identifier = str(UUID(identifier))
    with library_lock():
        folder = store.ROOT / 'inputs' / identifier
        if not (folder / 'metadata.json').is_file() or folder.is_symlink():
            raise FileNotFoundError('Saved input not found')
        shutil.rmtree(folder)


def summaries():
    with library_lock():
        root = store.ROOT / 'inputs'
        return sorted([json.loads(path.read_text()) for path in root.glob('*/metadata.json')
                       if not path.parent.name.startswith('.')], key=lambda item: item['created'], reverse=True)


def resolve(source):
    if not source.startswith('upload:'):
        raise ValueError('Unknown input')
    try:
        identifier = str(UUID(source.removeprefix('upload:')))
    except ValueError as error:
        raise ValueError('Unknown input') from error
    folder = store.ROOT / 'inputs' / identifier
    try:
        item = json.loads((folder / 'metadata.json').read_text())
    except FileNotFoundError as error:
        raise ValueError('The uploaded input is no longer available') from error
    path = folder / input_filename(item.get('kind', 'workbook'))
    if store.digest(path) != item['sha256']:
        raise ValueError('The uploaded input has changed')
    if item.get('kind') == 'workbook-set':
        import run_replay
        run_replay.verify(folder, json.loads(path.read_text()))
    return path, item['name'], item.get('kind', 'workbook')
