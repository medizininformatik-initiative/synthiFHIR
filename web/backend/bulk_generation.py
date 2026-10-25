"""Bounded Synthea generation; publish each completed gzip JSON chunk by rename."""
import json
import gzip
from datetime import datetime
import re
import uuid
import unicodedata
from zoneinfo import ZoneInfo
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import bulk_archives
import bulk_ndjson
from bulk_identifiers import Registry

import bulk_plan
import bulk_sources

import generation
import store
import synthea_runtime
import result_exports

CHUNK_SIZE = 100
OUTPUT_ROOT = result_exports.ROOT
OUTPUT_LABEL = result_exports.LABEL


def output_directory_label(name):
    """Join the host directory using its native path syntax, even inside Linux Docker."""
    import ntpath
    import posixpath
    paths = ntpath if ntpath.splitdrive(OUTPUT_LABEL)[0] or OUTPUT_LABEL.startswith('\\\\') else posixpath
    return paths.join(OUTPUT_LABEL, name)


def output_directory_name(job_id, dataset_name, timestamp, population=None, continuation=None):
    """Portable single path component, bounded in length and unique per job."""
    text = (dataset_name or 'Massendaten').replace('ß', 'ss')
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    label = re.sub(r'[^A-Za-z0-9_-]+', '-', text).strip('-_')[-24:].strip('-_') or 'Massendaten'
    stamp = datetime.fromtimestamp(timestamp, ZoneInfo('Europe/Berlin')).strftime('%Y%m%d-%H%M%S')
    if population is not None:
        continuation = continuation or {}
        series_id = uuid.UUID(continuation.get('seriesRoot', job_id)).hex[:8]
        number = result_exports.series_number({'continuation': continuation})
        suffix = f'({number})' if number > 1 else ''
        return f'{stamp}_{label}_{population}Pat_{series_id}{suffix}'
    return f'{stamp}_{label}_{uuid.UUID(job_id).hex}'


def compact_output_names(directory):
    """Use short, chunk-local names; patient identifiers stay inside the bundles."""
    files = sorted(directory.glob('*.json.gz'))
    targets = [directory / f'p{index:03d}.json.gz' for index in range(1, len(files) + 1)]
    if len(files) > CHUNK_SIZE:
        raise ValueError('Unexpected number of bulk output files')
    if any(target.exists() for target in targets):
        raise FileExistsError('Compact output filename already exists')
    for source, target in zip(files, targets):
        source.rename(target)


def compress_native(source_directory, destination, expected, update, sources=None):
    """Keep original JSON bytes, including separate provider/organization bundles."""
    patients = 0
    supporting = 0
    for source in (sources if sources is not None else sorted(source_directory.glob('*.json'))):
        with source.open() as stream:
            bundle = json.load(stream)
        count = sum(entry.get('resource', {}).get('resourceType') == 'Patient'
                    for entry in bundle.get('entry', []))
        if count > 1:
            raise ValueError('Expected one patient per Synthea bundle')
        if count:
            patients += 1
            name = f'p{patients:03d}.json.gz'
        else:
            supporting += 1
            name = f'support-{supporting:03d}.json.gz'
        del bundle
        with source.open('rb') as original, (destination / name).open('wb') as output:
            with gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as compressed:
                shutil.copyfileobj(original, compressed)
        update(stageCompleted=patients)
    if patients != expected:
        raise RuntimeError(f'Synthea produced {patients} patients; expected {expected}')


def chunk_settings(settings, index, size):
    # Distinct reproducible signed 64-bit seeds, including at the integer boundary.
    seed = ((int(settings['patientSeed']) + index + 2**63) % 2**64) - 2**63
    return {**settings, 'executionMode': 'standard', 'population': size, 'patientSeed': str(seed)}


def progress(folder, **values):
    pending = folder / 'bulk-progress.tmp'
    pending.write_text(json.dumps(values))
    pending.replace(folder / 'bulk-progress.json')


def process_progress(log):
    if not log.exists():
        return {}
    with log.open('rb') as stream:
        stream.seek(max(0, log.stat().st_size - 16384))
        lines = stream.read().decode(errors='replace').splitlines()
    for line in reversed(lines):
        try:
            if line.startswith('WORKBENCH_SELECTION '):
                data = json.loads(line.removeprefix('WORKBENCH_SELECTION '))
                return {'stageCompleted': data['matched'], 'attempts': data['attempts']}
            if line.startswith('WORKBENCH_CONVERSION '):
                return {'stageCompleted': int(line.removeprefix('WORKBENCH_CONVERSION '))}
        except (ValueError, KeyError):
            continue  # A partially flushed line is picked up on the next poll.
    return {}


def checked_process(command, log, update=None):
    with log.open('w') as stream:
        with subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT) as process:
            try:
                while True:
                    try:
                        code = process.wait(timeout=2)
                        break
                    except subprocess.TimeoutExpired:
                        if update:
                            update(process_progress(log))
                if update:
                    update(process_progress(log))
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
    if code:
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 8192))
            detail = stream.read().decode(errors='replace')
        raise RuntimeError(f'Process failed ({code}): {detail}')


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(data))
    pending.replace(path)


package = bulk_archives.package


def run(folder):
    seed = folder / 'series-identifiers.sqlite'
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    expected = snapshot.get('continuation', {}).get('identifierHistorySha256')
    if expected and (not seed.is_file() or store.digest(seed) != expected):
        raise ValueError('Series identifier history is missing or changed')
    registry = Registry(folder / 'bulk-identifiers.sqlite', seed if expected else None)
    try:
        _run(folder, registry)
    finally:
        registry.close()


def _run(folder, registry):
    os.environ['TZ'] = 'Europe/Berlin'
    time.tzset()
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    settings = snapshot['generation']
    native = settings.get('outputMode') == 'synthea'
    if not native:
        sys.path.insert(0, str(synthea_runtime.ROOT / 'scripts'))
        from synthea_to_excel import prepare
        from direct_csv import write_direct_csv
        from converter_options import resolve_config
        from synthea_consent import assignments, enrich
        options = resolve_config(folder / 'default.config')['values']
        if int(options.get('PID_LAST_NUMBER_INCREASE_LOOP_COUNT', 0)):
            raise ValueError('Bulk generation requires zero additional patient copies')
    destination = bulk_archives.output_path(snapshot) if snapshot.get('resultOutputName') else OUTPUT_ROOT / snapshot.get('bulkOutputName', folder.name)
    grouped = snapshot.get('bulkLayoutVersion', 0) >= 2
    destination.mkdir(parents=True, exist_ok=grouped)
    identifier_counters = dict(snapshot.get('continuation', {}).get('identifierCounters', {}))
    total = settings['population']
    runs = bulk_plan.plan(settings, snapshot.get('continuation', {}).get('sourceOffset', 0)) if grouped else [{'number': 1, 'start': 0, 'total': total}]
    completed = 0
    generated_bytes = 0
    archive_bytes = 0
    measured_seconds = 0
    finished_runs = 0
    packaged_patients = 0
    package_patients = 0
    checkpoint_root = folder / 'bulk-checkpoints'
    identity = store.digest(folder / 'snapshot.json') + ('' if native else store.digest(folder / 'default.config'))
    state = {'completed': 0, 'total': total, 'chunk': 0, 'run': 1, 'runs': len(runs),
             'finishedRuns': 0, 'archiveFormat': bulk_ndjson.suffix(snapshot).lstrip('.'), 'phase': 'starting', 'stageCompleted': 0, 'stageTotal': 0}
    current_started = time.monotonic()
    pending = None

    def update(**values):
        state.update(values)
        current_bytes = sum(p.stat().st_size for p in pending.glob('*.json.gz')) if pending and pending.exists() else 0
        state.update(completed=completed, generatedBytes=generated_bytes + current_bytes,
                     archiveBytes=archive_bytes, finishedRuns=finished_runs, updatedAt=time.time(),
                     estimatedTotalBytes=round(generated_bytes * total / completed) if completed else None,
                     remainingSeconds=(max(1, round(measured_seconds / completed * (total - completed)
                                                  - (time.monotonic() - current_started)))
                                       if completed and completed < total else (0 if completed == total else None)))
        work_done, work_total = bulk_plan.work_progress(
            total, completed, state['phase'], state['stageCompleted'], state['stageTotal'],
            native=native, packaged=packaged_patients, package_patients=package_patients, grouped=grouped)
        state.update(workCompleted=work_done, workTotal=work_total)
        progress(folder, **state)

    # Recover only atomically published chunks belonging to this immutable snapshot.
    for run_info in runs:
        run_number = run_info['number']
        # Preserve the package boundaries recorded by older immutable snapshots.
        package_size = run_info['total'] if snapshot.get('bulkLayoutVersion', 0) >= 4 else bulk_plan.PACKAGE_SIZE
        staging_root = folder / 'bulk-staging' if snapshot.get('resultOutputName') else destination
        run_directory = staging_root / f'run-{run_number:05d}' if grouped else staging_root
        run_directory.mkdir(parents=True, exist_ok=True)
        update(run=run_number, phase='starting', stageCompleted=0, stageTotal=0)
        print(f'Subrun {run_number}/{len(runs)}: {run_info["total"]} patients', flush=True)
        part_names = []
        packed_chunks = {}
        for archive in bulk_archives.package_files(snapshot, f'run-{run_number:05d}-package-'):
            saved_manifest = bulk_archives.read_manifest(archive)
            packed_chunks.update(saved_manifest['chunks'])
        for local_index, start in enumerate(range(run_info['start'], run_info['start'] + run_info['total'], CHUNK_SIZE)):
            index = start // CHUNK_SIZE
            size = min(CHUNK_SIZE, total - start, run_info['start'] + run_info['total'] - start)
            name = f'part-{local_index + 1:05d}' if grouped else f'part-{index + 1:05d}'
            final = run_directory / name
            part_names.append(name)
            checkpoint = checkpoint_root / f'{index:06d}.json'
            journal = checkpoint_root / f'{index:06d}.identifiers.jsonl'
            if grouped and (name in packed_chunks or final.exists()):
                saved = packed_chunks[name] if name in packed_chunks else json.loads(checkpoint.read_text())
                if saved['identity'] != identity or saved['size'] != size or (name not in packed_chunks and len(list(final.glob('*.json.gz'))) != saved.get('files', size)):
                    raise ValueError('Completed chunk no longer matches its checkpoint')
                if not native:
                    identifier_counters = saved.get('identifierCounters')
                    if not journal.is_file() or store.digest(journal) != saved.get('identifierJournalSha256'):
                        raise ValueError('Completed chunk identifier journal is missing or changed')
                    registry.include(journal)
                completed += size
                generated_bytes += saved['bytes']
                measured_seconds += saved['seconds']
            else:
                if not native and identifier_counters is None:
                    raise ValueError('This interrupted run has no identifier counter checkpoint; start a new run to preserve existing outputs')
                current_started = time.monotonic()
                work = folder / 'bulk-work'
                pending = run_directory / ('.' + name + '.partial')
                # A resume reruns only unpublished temporary work with its original seed.
                if grouped:
                    if work.exists(): shutil.rmtree(work)
                    if pending.exists(): shutil.rmtree(pending)
                work.mkdir()
                pending.mkdir()
                chunk = chunk_settings(settings, index, size)
                update(chunk=index + 1, phase='generating', stageCompleted=0, stageTotal=size, attempts=0)
                selected_sources = None
                if snapshot.get('bulkSourceVersion'):
                    def generate_source(source_index, destination):
                        update(stageTotal=CHUNK_SIZE, stageCompleted=0)
                        source_settings = chunk_settings(settings, source_index, CHUNK_SIZE)
                        command = generation.native_command(source_settings, destination)
                        if not native:
                            command = [arg.replace('hospital.fhir.export=true', 'hospital.fhir.export=false')
                                       .replace('practitioner.fhir.export=true', 'practitioner.fhir.export=false') for arg in command]
                        checked_process(command, work / 'generation.log', lambda values: update(**values))
                    selected_sources = bulk_sources.select(folder, snapshot, start, size, work, generate_source)
                else:
                    command = generation.native_command(chunk, work / 'synthea')
                    command = command if native else [arg.replace('hospital.fhir.export=true', 'hospital.fhir.export=false')
                               .replace('practitioner.fhir.export=true', 'practitioner.fhir.export=false') for arg in command]
                    def generating(values):
                        if 'stageCompleted' not in values:
                            values['stageCompleted'] = len(list((work / 'synthea/fhir').glob('*.json')))
                        update(**values)
                    if grouped:
                        checked_process(command, work / 'generation.log', generating)
                    else:
                        checked_process(command, work / 'generation.log')
                if native:
                    update(phase='compressing', stageCompleted=0, stageTotal=size, attempts=0)
                    compress_native(work / 'synthea/fhir', pending, size, update,
                                    [r['path'] for r in selected_sources] if selected_sources is not None else None)
                else:
                    update(phase='mapping', stageCompleted=0, stageTotal=size, attempts=0)
                    jobs = []
                    consent = {}
                    if selected_sources is not None:
                        consent = {r['id']: r['consent'] for r in selected_sources if 'consent' in r}
                    elif settings.get('syntheticConsent'):
                        patient_ids = []
                        for source in sorted((work / 'synthea/fhir').glob('*.json')):
                            with source.open() as stream:
                                patient_ids.extend(e['resource']['id'] for e in json.load(stream).get('entry', [])
                                                   if e.get('resource', {}).get('resourceType') == 'Patient')
                        consent = assignments(patient_ids, chunk['patientSeed'])
                    last_update = time.monotonic()
                    sources = selected_sources if selected_sources is not None else [
                        {'path': p, 'seed': chunk['patientSeed']} for p in sorted((work / 'synthea/fhir').glob('*.json'))]
                    for source_index, source_record in enumerate(sources):
                        source = source_record['path']
                        bundle = json.loads(source.read_text())
                        if not any(e.get('resource', {}).get('resourceType') == 'Patient' for e in bundle.get('entry', [])):
                            continue
                        rows, _ = prepare(bundle, options, diagnostics=False)
                        if consent:
                            pid = rows['Person'][0]['Patient-ID']
                            enrich(rows, bundle, consent[pid], source_record['seed'],
                                   end_date=chunk.get('endDate'),
                                   years_of_history=chunk.get('yearsOfHistory', 0))
                        tables = work / 'tables' / str(source_index)
                        prefix = 'Fall-' + source.stem
                        write_direct_csv(rows, tables, prefix)
                        jobs.append({'input': str(tables), 'output': str(pending), 'prefix': prefix})
                        del rows, bundle
                        if time.monotonic() - last_update >= 2:
                            update(stageCompleted=len(jobs))
                            last_update = time.monotonic()
                    if len(jobs) != size:
                        raise RuntimeError(f'Synthea produced {len(jobs)} patients; expected {size}')
                    counters_output = work / 'identifier-counters.json'
                    registrations_output = work / 'identifier-registrations.jsonl'
                    jobs[0].update(identifierCounters=identifier_counters, identifierCountersOutput=str(counters_output),
                                   identifierRegistrationsOutput=str(registrations_output))
                    manifest = work / 'conversion.json'
                    manifest.write_text(json.dumps(jobs))
                    update(phase='converting', stageCompleted=0)
                    command = ['java', '-Xmx1g', '-Duser.timezone=Europe/Berlin', '-cp',
                               str(store.APP / 'synthifhir.jar'), 'de.uni_leipzig.imise.SyntheaBulkConverter',
                               str(manifest), str(folder / 'default.config')]
                    if grouped:
                        checked_process(command, work / 'conversion.log', lambda values: update(**values))
                    else:
                        checked_process(command, work / 'conversion.log')
                    identifier_counters = json.loads(counters_output.read_text())
                    registry.include(registrations_output)
                    compact_output_names(pending)
                files = list(pending.glob('*.json.gz'))
                if not native and len(files) != size:
                    raise RuntimeError(f'Converted {len(files)} patients; expected {size}; disable patient copies and enable Patient output')
                size_bytes = sum(p.stat().st_size for p in files)
                seconds = time.monotonic() - current_started
                # Metadata first, directory rename second: only the final directory commits a chunk.
                if grouped:
                    if not native:
                        checkpoint_root.mkdir(parents=True, exist_ok=True)
                        registrations_output.replace(journal)
                    atomic_json(checkpoint, {'identity': identity, 'size': size, 'bytes': size_bytes, 'seconds': seconds, 'files': len(files),
                                             **({'identifierCounters': identifier_counters,
                                                 'identifierJournalSha256': store.digest(journal)} if not native else {})})
                pending.rename(final)
                pending = None
                completed += size
                generated_bytes += size_bytes
                measured_seconds += seconds
                shutil.rmtree(work)
                if snapshot.get('bulkSourceVersion'):
                    bulk_sources.prune(folder, snapshot, completed)
                print(f'Completed {completed}/{total} patients', flush=True)
            current_started = time.monotonic()
            update(chunk=index + 1, phase='between-chunks', stageCompleted=size, stageTotal=size)
            if grouped and (len(part_names) * CHUNK_SIZE >= package_size or completed == run_info['start'] + run_info['total']):
                package_number = local_index * CHUNK_SIZE // package_size + 1
                suffix = bulk_ndjson.suffix(snapshot)
                target = bulk_archives.package_directory(snapshot) / f'run-{run_number:05d}-package-{package_number:05d}{suffix}'
                legacy_tar = target.with_suffix('.tar')
                target = bulk_archives.package_path(run_directory, part_names, target)
                package_patients = min(len(part_names) * CHUNK_SIZE, run_info['total'] - (package_number - 1) * package_size)
                update(phase='packaging', stageCompleted=0, stageTotal=0)
                archive_bytes += package(run_directory, part_names, target,
                                         lambda done, count: update(stageCompleted=done, stageTotal=count),
                                         {part: packed_chunks.get(part) or json.loads((checkpoint_root / f'{run_info["start"] // CHUNK_SIZE + int(part.split("-")[1]) - 1:06d}.json').read_text()) for part in part_names})
                bulk_archives.remove_legacy_tar(legacy_tar, target)
                packaged_patients += package_patients
                package_patients = 0
                part_names = []
        finished_runs = run_number
        update(phase='between-runs', stageCompleted=0, stageTotal=0)
        print(f'Subrun {run_number}/{len(runs)} complete', flush=True)
    if grouped:
        bulk_archives.publish(folder, snapshot)
    if 'identifierCounterStarts' in snapshot:
        import bulk_continuation
        if snapshot.get('bulkSourceVersion'):
            bulk_sources.finish(folder, snapshot)
        registry.save(folder / 'bulk-identifier-history.sqlite')
        atomic_json(folder / bulk_continuation.STATE_FILE, bulk_continuation.completion(folder, snapshot, identifier_counters))
    update(phase='complete', stageCompleted=0, stageTotal=0, remainingSeconds=0)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve())
