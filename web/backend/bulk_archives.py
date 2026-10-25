"""Atomic ZIP packages of already compressed patient bundles, without loose duplicates."""
import hashlib
import gzip
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time
import zipfile

import store
import bulk_ndjson


def patient_ids(path):
    with gzip.open(path, 'rt') as stream:
        resource = json.load(stream)
    entries = resource.get('entry', []) if resource.get('resourceType') == 'Bundle' else [{'resource': resource}]
    return {entry['resource']['id'] for entry in entries
            if entry.get('resource', {}).get('resourceType') == 'Patient' and entry['resource'].get('id')}


def patient_count(manifest):
    files = manifest['files'].values()
    if all('patientIds' in entry for entry in files):
        return len({pid for entry in files for pid in entry['patientIds']})
    # Keep published packages readable without rewriting their manifests.
    return sum(entry.get('role') != 'support' for entry in files)


def read_manifest(path, verify=True, update=None):
    if path.name.endswith(bulk_ndjson.SUFFIXES):
        return bulk_ndjson.read_manifest(path, verify, update)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        files = manifest['files']
        names = [item.filename for item in archive.infolist()]
        if len(names) != len(set(names)) or set(names) != {'manifest.json', *files}:
            raise ValueError('ZIP entries do not match the package manifest')
        if {PurePosixPath(name).parts[0] for name in files} != set(manifest['chunks']):
            raise ValueError('ZIP chunks differ from the manifest')
        for part, chunk in manifest['chunks'].items():
            if 'size' in chunk and sum(name.startswith(part + '/') and entry.get('role') != 'support' for name, entry in files.items()) != chunk['size']:
                raise ValueError('ZIP patient count differs from the checkpoint')
        last_update = time.monotonic()
        for index, (name, entry) in enumerate(files.items(), 1):
            parts = PurePosixPath(name).parts
            if len(parts) != 2 or any(part in {'.', '..'} for part in parts) or not name.endswith('.json.gz'):
                raise ValueError('Invalid patient package entry')
            if archive.getinfo(name).file_size != entry['size']:
                raise ValueError('ZIP entry size differs from its manifest')
            if verify:
                digest = hashlib.sha256()
                with archive.open(name) as stream:
                    while data := stream.read(1024 * 1024):
                        digest.update(data)
                if digest.hexdigest() != entry['sha256']:
                    raise ValueError('ZIP entry checksum differs from its manifest')
            if update and (index == len(files) or time.monotonic() - last_update >= 2):
                update(index, len(files))
                last_update = time.monotonic()
        return manifest


def package_path(directory, parts, target):
    """Keep published packages stable on resume; name new ones from actual files."""
    suffix = next((suffix for suffix in bulk_ndjson.SUFFIXES if target.name.endswith(suffix)), '.zip')
    stem = target.name[:-len(suffix)]
    candidates = [target, *target.parent.glob(stem + '-*Pat' + suffix)]
    existing = [path for path in candidates if path.is_file()]
    if len(existing) > 1:
        raise ValueError('Multiple published ZIPs exist for this package')
    if existing:
        return existing[0]
    count = len({pid for part in parts for path in (directory / part).glob('*.json.gz')
                 for pid in patient_ids(path)})
    return target.with_name(f'{stem}-{count}Pat{suffix}')


def package(directory, parts, destination, update, chunks=None):
    """Verify all bytes before publication and before removing recoverable loose copies."""
    if destination.name.endswith(bulk_ndjson.SUFFIXES):
        return bulk_ndjson.package(directory, parts, destination, update, chunks)
    if destination.exists():
        manifest = read_manifest(destination, update=update)
        if set(manifest['chunks']) != set(parts):
            raise ValueError('Existing ZIP contains different chunks')
        if chunks is not None and manifest['chunks'] != chunks:
            raise ValueError('Existing ZIP checkpoint differs from this run')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name('.' + destination.name + '.partial')
        manifest = {'version': 1, 'chunks': chunks or {part: {} for part in parts}, 'files': {}}
        file_count = sum(len(list((directory / part).glob('*.json.gz'))) for part in parts)
        written = 0
        last_update = time.monotonic()
        with zipfile.ZipFile(pending, 'w', zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, part in enumerate(parts):
                files = sorted((directory / part).glob('*.json.gz'))
                if not files:
                    raise ValueError('Cannot package an empty patient chunk')
                for source in files:
                    name = part + '/' + source.name
                    manifest['files'][name] = {'size': source.stat().st_size, 'sha256': store.digest(source),
                                             'patientIds': sorted(patient_ids(source))}
                    if source.name.startswith('support-'):
                        manifest['files'][name]['role'] = 'support'
                    archive.write(source, name)
                    written += 1
                    if written == file_count or time.monotonic() - last_update >= 2:
                        update(written, 2 * file_count)
                        last_update = time.monotonic()
            archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True))
        read_manifest(pending, update=lambda done, count: update(file_count + done, 2 * file_count))
        pending.replace(destination)
    # A crash during cleanup leaves the verified ZIP authoritative. Re-check remnants first.
    for part in parts:
        directory_part = directory / part
        for source in directory_part.glob('*'):
            entry = manifest['files'].get(part + '/' + source.name)
            if not entry or not source.is_file() or store.digest(source) != entry['sha256']:
                raise ValueError('Loose file differs from the verified ZIP; kept for inspection')
        if directory_part.exists():
            shutil.rmtree(directory_part)
    return destination.stat().st_size


def remove_legacy_tar(path, zip_path):
    if not path.exists():
        return
    manifest = read_manifest(zip_path, verify=False)
    found = set()
    with tarfile.open(path) as archive:
        for member in archive:
            if member.isdir():
                continue
            entry = manifest['files'].get(member.name)
            if not member.isfile() or not entry or member.size != entry['size'] or member.name in found:
                raise ValueError('Legacy TAR differs from the ZIP; TAR retained')
            with archive.extractfile(member) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != entry['sha256']:
                raise ValueError('Legacy TAR differs from the ZIP; TAR retained')
            found.add(member.name)
    if found != set(manifest['files']):
        raise ValueError('Legacy TAR differs from the ZIP; TAR retained')
    path.unlink()


def output_path(snapshot):
    if snapshot.get('resultOutputName'):
        import result_exports
        return result_exports.path(snapshot)
    from bulk_generation import OUTPUT_ROOT
    name = snapshot.get('bulkOutputName')
    if not name or Path(name).name != name:
        raise ValueError('Invalid bulk output directory')
    return OUTPUT_ROOT / name


def publish(folder, snapshot):
    """Expose packages as ordinary selectable/downloadable datasets without copying them."""
    destination = output_path(snapshot)
    items = []
    for path in package_files(snapshot):
        manifest = read_manifest(path, verify=False)
        items.append({'id': folder.name + '.' + str(len(items)), 'name': path.stem,
                      'filename': path.name, 'kind': 'bulk', 'path': path.relative_to(destination).as_posix(),
                      'size': path.stat().st_size, 'sha256': store.digest(path),
                      'inspection': {'patients': patient_count(manifest)}, 'files': []})
    from bulk_generation import atomic_json
    atomic_json(folder / 'datasets.json', {'schemaVersion': 1, 'datasets': items, 'artifacts': []})


def migrate_completed(folder, snapshot):
    """Package completed existing runs, retaining originals until their ZIP is verified."""
    from bulk_generation import atomic_json
    if snapshot.get('resultOutputName'):
        publish(folder, snapshot)
        return
    destination = output_path(snapshot)
    if not destination.is_dir():
        raise FileNotFoundError('Bulk results directory is unavailable')
    run_dirs = sorted(destination.glob('run-*')) or [destination]
    global_index = 0
    for run_number, directory in enumerate(run_dirs, 1):
        parts = sorted(p.name for p in directory.glob('part-*') if p.is_dir())
        # Published ZIPs already own their parts after an interrupted migration.
        existing = {}
        for archive in sorted(package_directory(snapshot).glob(f'run-{run_number:05d}-package-*.zip')):
            manifest = read_manifest(archive)
            existing.update(manifest['chunks'])
            package(directory, list(manifest['chunks']), archive, lambda *_: None)
        parts = sorted(set(parts) | set(existing))
        for start in range(0, len(parts), 10):
            group = parts[start:start + 10]
            target = package_directory(snapshot) / f'run-{run_number:05d}-package-{start // 10 + 1:05d}.zip'
            legacy_tar = target.with_suffix('.tar')
            target = package_path(directory, group, target)
            chunks = {}
            for part in group:
                checkpoint = folder / 'bulk-checkpoints' / f'{global_index:06d}.json'
                chunks[part] = json.loads(checkpoint.read_text()) if checkpoint.exists() else existing.get(part, {})
                global_index += 1
            package(directory, group, target, lambda *_: None, chunks)
            # This TAR is redundant only after the corresponding ZIP has been verified.
            remove_legacy_tar(legacy_tar, target)
    publish(folder, snapshot)
    progress_path = folder / 'bulk-progress.json'
    if progress_path.exists():
        data = json.loads(progress_path.read_text())
        data.update(archiveBytes=sum(p.stat().st_size for p in package_directory(snapshot).glob('*.zip')), archiveFormat='zip')
        atomic_json(progress_path, data)


def package_directory(snapshot):
    destination = output_path(snapshot)
    existing = destination / 'packages'
    return existing if existing.is_dir() else destination


def package_files(snapshot, prefix=''):
    suffix = bulk_ndjson.suffix(snapshot)
    return sorted(package_directory(snapshot).glob(prefix + '*' + suffix))
