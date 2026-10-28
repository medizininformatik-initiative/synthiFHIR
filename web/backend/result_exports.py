"""Publish reusable Workbench datasets in the host output directory."""
import json
import os
from pathlib import Path
import shutil

import archive_names


ROOT = Path(os.environ.get('WORKBENCH_OUTPUT', '/global-output'))
LABEL = 'outputGlobal'


def path(snapshot):
    name = presentation(snapshot).get('resultOutputName', snapshot['resultOutputName'])
    if not name or Path(name).name != name:
        raise ValueError('Invalid result directory')
    return ROOT / name


def label(snapshot):
    return LABEL + '/' + path(snapshot).name


def publish(folder, snapshot, manifest):
    if 'resultOutputName' not in snapshot:
        return
    destination = path(snapshot)
    for index, item in enumerate(manifest['datasets']):
        source = folder / f'dataset-{index}.zip'
        if not source.is_file() or item.get('error'):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        name = archive_names.filename(item['name'], archive_names.patient_count(source), str(index))
        target = destination / f'{index + 1:02d}-{name}'
        pending = target.with_name('.' + target.name + '.partial')
        try:
            shutil.copyfile(source, pending)
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)


def metadata_path(snapshot):
    import store
    name = snapshot['resultOutputName']
    if not name or Path(name).name != name:
        raise ValueError('Invalid result directory')
    return store.ROOT / 'result-locations' / (name + '.json')


def presentation(snapshot):
    if not snapshot.get('resultOutputName'):
        return {}
    metadata = metadata_path(snapshot)
    return json.loads(metadata.read_text()) if metadata.is_file() else {}


def dataset_name(snapshot):
    return presentation(snapshot).get('datasetName', snapshot.get('datasetName', ''))


def series_number(snapshot):
    """Keep the series position stable, including when earlier runs are deleted."""
    import store
    cached = presentation(snapshot).get('seriesNumber')
    if cached is not None:
        return cached
    number = 1
    seen = set()
    while continuation := snapshot.get('continuation'):
        if 'seriesNumber' in continuation:
            return number - 1 + continuation['seriesNumber']
        parent = continuation['sourceRun']
        if parent in seen:
            raise ValueError('Invalid series chain')
        seen.add(parent)
        snapshot = json.loads((store.ROOT / 'jobs' / parent / 'snapshot.json').read_text())
        number += 1
        cached = presentation(snapshot).get('seriesNumber')
        if cached is not None:
            return number - 1 + cached
    return number


def rename_bulk(job_id, name):
    """Rename published results without changing any generation evidence or package bytes."""
    import bulk_archives
    import bulk_generation
    import bulk_resources
    import store
    if not name or not name.strip():
        raise ValueError('A dataset name is required')
    with store.run_lock(job_id):
        bulk_resources.require_idle()
        job = store.get(job_id)
        if not job or job['state'] != 'succeeded':
            raise ValueError('Only completed runs can be renamed')
        folder = store.ROOT / 'jobs' / job_id
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        if snapshot.get('generation', {}).get('executionMode') != 'bulk' or not snapshot.get('resultOutputName'):
            raise ValueError('This run has no published bulk results')
        source = path(snapshot)
        number = series_number(snapshot)
        continuation = {**snapshot.get('continuation', {}), 'seriesNumber': number}
        target = ROOT / bulk_generation.output_directory_name(
            job_id, name, job['created'], snapshot['generation']['population'], continuation)
        if source != target and target.exists():
            raise ValueError('The destination already exists')
        manifest_path = folder / 'datasets.json'
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        packages = source / 'packages'
        moves = []
        for item in manifest['datasets']:
            old = source / item['path']
            if not old.is_file() or store.digest(old) != item['sha256']:
                raise ValueError('Published package differs from its manifest')
            if old.parent == packages:
                new = source / old.name
                if new.exists():
                    raise ValueError('A package with that name already exists')
                moves.append((old, new))
                if old.name.endswith(('.ndjson.gz', '.ndjson')):
                    from bulk_ndjson import manifest_path as package_manifest
                    moves.append((package_manifest(old), package_manifest(new)))
                    from package_statistics import report_path
                    if report_path(old).exists():
                        moves.append((report_path(old), report_path(new)))
                item['path'] = old.name
        if packages.exists() and set(packages.iterdir()) != {old for old, _ in moves}:
            raise ValueError('Unexpected files in the package directory')
        metadata = metadata_path(snapshot)
        metadata.parent.mkdir(parents=True, exist_ok=True)
        moved = []
        renamed = False
        try:
            for old, new in moves:
                old.rename(new); moved.append((old, new))
            if source != target:
                source.rename(target); renamed = True
            bulk_generation.atomic_json(manifest_path, manifest)
            bulk_generation.atomic_json(metadata, {'datasetName': name, 'resultOutputName': target.name, 'seriesNumber': number})
        except BaseException:
            if renamed:
                target.rename(source)
            for old, new in reversed(moved):
                new.rename(old)
            manifest_path.write_bytes(original_manifest)
            raise
        if (target / 'packages').exists():
            (target / 'packages').rmdir()
        return store.get(job_id)


def remove(snapshot):
    """Remove the run's exported directory and its saved location override."""
    if not snapshot.get('resultOutputName') and not snapshot.get('bulkOutputName'):
        return
    import bulk_archives
    destination = path(snapshot) if snapshot.get('resultOutputName') else bulk_archives.output_path(snapshot)
    if destination.parent.resolve() != ROOT.resolve() or destination.name in {'.', '..'}:
        raise ValueError('Invalid result directory')
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    if snapshot.get('resultOutputName'):
        metadata_path(snapshot).unlink(missing_ok=True)
