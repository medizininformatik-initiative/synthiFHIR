"""Stream flat FHIR-only ZIP downloads without staging large bulk results."""
from contextlib import ExitStack
from pathlib import PurePosixPath
import re
import zipfile

import datasets
import store


SUFFIXES = tuple(extension + compression for extension in ('.json', '.ndjson', '.jsonl', '.xml')
                 for compression in ('', '.gz', '.bz2', '.zip'))


def plan(job_id):
    job = store.get(job_id)
    if not job:
        raise FileNotFoundError('Run not found')
    if job['state'] not in {'succeeded', 'failed'}:
        raise store.SubmissionConflict('Run has not ended')
    manifest = datasets.get(job_id)
    root = store.ROOT / 'jobs' / job_id / 'output'
    resolved_root = root.resolve()
    entries = []
    for index, item in enumerate(manifest['datasets'], 1):
        label = re.sub(r'[^\w.-]+', '-', item.get('name', ''), flags=re.UNICODE).strip('.-')[:60] or 'FHIR'
        prefix = f'{index:03d}-{label}/' if len(manifest['datasets']) > 1 else ''
        sources = []
        if item.get('kind') == 'bulk':
            _, _, path = datasets.resolve_dataset(item['id'])
            if path.name.endswith('.zip'):
                import bulk_archives
                saved = bulk_archives.read_manifest(path, verify=False)
                sources = [(path, name) for name in sorted(saved['files'])]
            else:
                sources = [(path, None)]
        else:
            for file in item.get('files', []):
                if not file['path'].lower().endswith(SUFFIXES):
                    continue
                path = root / file['path']
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(resolved_root):
                    raise FileNotFoundError('FHIR file is unavailable')
                sources.append((path, None))
        # A numeric prefix avoids collisions when patient folders reuse filenames.
        for number, (path, member) in enumerate(sources, 1):
            name = PurePosixPath(member).name if member else path.name
            entries.append((path, member, f'{prefix}{number:06d}-{name}'))
    if not entries:
        raise store.SubmissionConflict('No FHIR files are available')
    return entries


class Sink:
    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(data)
        return len(data)

    def flush(self):
        pass

    def drain(self):
        data = b''.join(self.parts)
        self.parts.clear()
        return data


def stream(job_id, entries):
    with store.run_lock(job_id):
        sink = Sink()
        with ExitStack() as archives, zipfile.ZipFile(sink, 'w', compression=zipfile.ZIP_STORED) as output:
            current_path = None
            current_archive = None
            for path, member, name in entries:
                with ExitStack() as stack:
                    if member:
                        if path != current_path:
                            archives.close()
                            current_archive = archives.enter_context(zipfile.ZipFile(path))
                            current_path = path
                        source = stack.enter_context(current_archive.open(member))
                    else:
                        source = stack.enter_context(path.open('rb'))
                    target = stack.enter_context(output.open(name, 'w', force_zip64=True))
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                        yield sink.drain()
                yield sink.drain()
        yield sink.drain()
