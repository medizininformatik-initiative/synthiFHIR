"""Persistent dataset manifests and streaming downloads of completed run artifacts."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import zipfile

import store
import package_statistics


class CancelledInspection(Exception):
    pass


def ensure_running(cancelled):
    if cancelled():
        raise CancelledInspection()


def file_id(relative):
    return hashlib.sha256(relative.encode()).hexdigest()[:24]


def variants(run, snapshot):
    fhir = run / 'fhir'
    if not fhir.is_dir():
        return []
    kind = snapshot.get('inputKind', 'workbook')
    if kind.startswith('synthea'):
        # The workbench submits one configuration per independent Synthea job.
        return [(snapshot['profile']['name'], fhir)]
    options = run / 'details/options'
    if kind == 'csv':
        names = sorted(path.name for path in options.iterdir() if path.is_dir()) if options.exists() else []
    else:
        names = sorted({path.name for path in options.glob('*/*') if path.is_dir()})
    if len(names) > 1:
        return [(name, fhir / name) for name in names if (fhir / name).is_dir()]
    return [(snapshot['profile']['name'] if snapshot['profile']['id'] != 'workbook' else (names[0] if names else 'default'), fhir)]


def inspect(folder, destination, cancelled):
    command = ['java', '-Xmx512m', '-cp', str(store.APP / 'synthifhir.jar'),
               'de.uni_leipzig.life.synthifhir.DatasetInspection', str(folder), str(destination)]
    with destination.with_suffix('.log').open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while process.poll() is None:
                ensure_running(cancelled)
                if time.monotonic() - started > 300:
                    raise ValueError('Dataset inspection exceeded five minutes')
                time.sleep(0.1)
            if process.returncode:
                raise ValueError('Dataset inspection failed; see the inspection log')
            return json.loads(destination.read_text())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()


def report_summary(path):
    if path.suffix != '.json' or path.stat().st_size > 2 * 1024 * 1024:
        return None
    if not (path.name.endswith(('.import.json', '.validation.json', '.loss.json')) or path.name == 'summary.json'):
        return None
    try:
        report = json.loads(path.read_text())
        if not isinstance(report, dict):
            return None
        issues = report.get('issues', [])
        losses = report.get('losses', [])
        failures = report.get('failures', [])
        summaries = {'status': report.get('status'), 'issues': len(issues), 'omissions': len(losses),
                     'failures': len(failures), 'derivations': len(report.get('contactEndDerivations', [])),
                     'selections': len(report.get('outputSelections', [])),
                     'validationErrors': sum(message.get('severity') in {'ERROR', 'FATAL'} for message in report.get('messages', [])),
                     'validationWarnings': sum(message.get('severity') == 'WARNING' for message in report.get('messages', [])),
                     'reasons': dict(Counter(item.get('category', item.get('reason', '')) for item in [*issues, *losses, *(item for item in report.get('outputSelections', []) if isinstance(item, dict) and item.get('action') == 'omit-identifier')] if isinstance(item, dict)))}
        return summaries
    except (ValueError, TypeError):
        return None


def build(job_id, snapshot, cancelled=lambda: False):
    with store.run_lock(job_id):
        if not store.get(job_id):
            raise CancelledInspection()
        return build_locked(job_id, snapshot, cancelled)


def build_locked(job_id, snapshot, cancelled=lambda: False):
    folder = store.ROOT / 'jobs' / job_id
    root = folder / 'output'
    manifest = {'schemaVersion': 1, 'inspectorSha256': store.digest(store.APP / 'synthifhir.jar'), 'datasets': [], 'artifacts': []}
    for path in sorted(root.rglob('*')):
        ensure_running(cancelled)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        relative = path.relative_to(root).as_posix()
        manifest['artifacts'].append({'id': file_id(relative), 'path': relative, 'size': path.stat().st_size,
                                      'report': report_summary(path)})
    for run in sorted(root.glob('run-*')):
        for name, data in variants(run, snapshot):
            ensure_running(cancelled)
            identifier = str(len(manifest['datasets']))
            relative = data.relative_to(root).as_posix()
            item = {'id': job_id + '.' + identifier, 'name': name, 'path': relative,
                    'files': [file for file in manifest['artifacts'] if file['path'].startswith(relative + '/')]}
            manifest['datasets'].append(item)
            try:
                item['inspection'] = inspect(data, folder / ('dataset-' + identifier + '.json'), cancelled)
                statistics = item['inspection'].get('statistics')
                if statistics is not None:
                    report = data / 'resource-statistics.txt'
                    report.write_text(package_statistics.render(name, statistics, kind="Dataset"), encoding='utf-8')
                    report_relative = report.relative_to(root).as_posix()
                    artifact = {'id': file_id(report_relative), 'path': report_relative,
                                'size': report.stat().st_size, 'report': None}
                    manifest['artifacts'] = [file for file in manifest['artifacts'] if file['path'] != report_relative]
                    manifest['artifacts'].append(artifact)
                    item['files'] = [file for file in item['files'] if file['path'] != report_relative] + [artifact]
                    item['statistics'] = statistics
                target = folder / ('dataset-' + identifier + '.zip')
                with zipfile.ZipFile(target.with_suffix('.tmp'), 'w', zipfile.ZIP_DEFLATED) as archive:
                    for file in item['files']:
                        ensure_running(cancelled)
                        with (root / file['path']).open('rb') as source, archive.open(file['path'][len(relative) + 1:], 'w', force_zip64=True) as output:
                            while chunk := source.read(1024 * 1024):
                                ensure_running(cancelled)
                                output.write(chunk)
                target.with_suffix('.tmp').rename(target)
                item['size'] = target.stat().st_size
                item['sha256'] = store.digest(target)
            except ValueError as error:
                item['error'] = str(error)
    import result_exports
    result_exports.publish(folder, snapshot, manifest)
    temporary = folder / 'datasets.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    temporary.replace(folder / 'datasets.json')
    return manifest


def get(job_id):
    path = store.ROOT / 'jobs' / job_id / 'datasets.json'
    manifest = json.loads(path.read_text()) if path.is_file() else {'datasets': [], 'artifacts': [], 'pending': True}
    # Read saved bulk counters only; never scan large payloads for a UI request.
    bulk = [item for item in manifest.get('datasets', []) if item.get('kind') == 'bulk' and 'statistics' not in item]
    if bulk:
        import bulk_archives
        snapshot = json.loads((path.parent / 'snapshot.json').read_text())
        root = bulk_archives.output_path(snapshot)
        for item in bulk:
            payload = root / item['path']
            if payload.is_symlink() or not payload.resolve().is_relative_to(root.resolve()):
                continue
            try:
                saved = bulk_archives.read_manifest(payload, verify=False)
                if 'statistics' in saved:
                    item['statistics'] = saved['statistics']
            except (OSError, ValueError, zipfile.BadZipFile):
                pass
    return manifest


def resolve_dataset(identifier):
    if not re.fullmatch(r'[0-9a-f-]{36}\.\d+', identifier):
        raise FileNotFoundError('Dataset not found')
    job_id, index = identifier.split('.')
    job = store.get(job_id)
    if not job or job['state'] not in {'succeeded', 'failed'}:
        raise FileNotFoundError('Dataset not available')
    item = next((item for item in get(job_id)['datasets'] if item['id'] == identifier), None)
    if item is None:
        raise FileNotFoundError('Dataset not found')
    folder = store.ROOT / 'jobs' / job_id
    if item.get('kind') == 'bulk':
        import bulk_archives
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        root = bulk_archives.output_path(snapshot)
        target = root / item['path']
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
            raise FileNotFoundError('Bulk package is unavailable')
        return job, item, target
    target = folder / ('dataset-' + index + '.zip')
    if not target.is_file() and (folder / 'cleanup.json').is_file():
        with store.run_lock(job_id):
            if not target.is_file():
                temporary = target.with_suffix('.tmp')
                try:
                    checksums = {entry['path']: entry['sha256'] for entry in json.loads((folder / 'cleanup.json').read_text())['files']}
                    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                        for file in item['files']:
                            source = folder / 'output' / file['path']
                            if source.is_symlink() or not source.resolve().is_relative_to((folder / 'output').resolve()):
                                raise FileNotFoundError('Result file is unavailable')
                            if store.digest(source) != checksums.get('output/' + file['path']):
                                raise store.SubmissionConflict('Retained result differs from its saved checksum')
                            name = file['path'][len(item['path']) + 1:]
                            with source.open('rb') as stream, archive.open(name, 'w', force_zip64=True) as output:
                                while chunk := stream.read(1024 * 1024):
                                    output.write(chunk)
                    item.update(sha256=store.digest(temporary), size=temporary.stat().st_size)
                    manifest = get(job_id)
                    manifest['datasets'] = [item if entry['id'] == identifier else entry for entry in manifest['datasets']]
                    manifest_path = folder / 'datasets.tmp'
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
                    # Publish metadata first so a concurrent uploader cannot accept stale bytes.
                    manifest_path.replace(folder / 'datasets.json')
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            item = next(entry for entry in get(job_id)['datasets'] if entry['id'] == identifier)
    return job, item, target


def resolve_artifact(job_id, identifier):
    item = next((item for item in get(job_id)['artifacts'] if item['id'] == identifier), None)
    if item is None:
        raise FileNotFoundError('Artifact not found')
    root = store.ROOT / 'jobs' / job_id / 'output'
    path = root / item['path']
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise FileNotFoundError('Artifact not available')
    return path


def backfill(cancelled):
    for job in store.jobs():
        folder = store.ROOT / 'jobs' / job['id']
        if (job.get('generation') or {}).get('executionMode') == 'bulk':
            if job['state'] == 'succeeded' and not (folder / 'datasets.json').exists():
                import bulk_archives
                try:
                    with store.run_lock(job['id']):
                        bulk_archives.migrate_completed(folder, json.loads((folder / 'snapshot.json').read_text()))
                except (OSError, ValueError, zipfile.BadZipFile) as error:
                    (folder / 'datasets.json').write_text(json.dumps({'datasets': [], 'artifacts': [], 'error': str(error)}))
                return
            continue
        if job['state'] not in {'succeeded', 'failed'} or (folder / 'datasets.json').exists():
            continue
        try:
            build(job['id'], json.loads((folder / 'snapshot.json').read_text()), cancelled)
        except CancelledInspection:
            return
        except (OSError, ValueError) as error:
            if not folder.exists():
                return
            (folder / 'datasets.json').write_text(json.dumps({'datasets': [], 'artifacts': [], 'error': str(error)}))
        return
