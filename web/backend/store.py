"""Persistent prototype queue. Each job owns its files and converter process."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import uuid
import zipfile
import run_summary
import run_outcomes

ROOT = Path(os.environ.get("WORKBENCH_DATA", "/data"))
APP = Path(os.environ.get("CONVERTER_HOME", "/converter"))
SOURCES = {"starter": "input/FHIR_Testdatengenerator_Vorlage.xlsx", "demo": "FHIR_Testdatengenerator_Interpolar_Demo.xlsx"}


@contextmanager
def connect():
    ROOT.mkdir(parents=True, exist_ok=True)
    db = None
    try:
        # Serialize first-time WAL/schema initialization across API threads and worker.
        with (ROOT / '.database-init.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(ROOT / 'jobs.sqlite', timeout=15)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL, cancel INTEGER NOT NULL DEFAULT 0, exit_code INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL, descriptor TEXT NOT NULL, result TEXT, cancel INTEGER NOT NULL DEFAULT 0)")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for column in ('started', 'finished'):
                if column not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {column} REAL')
            if "elapsed" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN elapsed REAL NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS bulk_series_links (parent TEXT PRIMARY KEY, child TEXT UNIQUE NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, jobs TEXT NOT NULL)")
        with db:
            yield db
    finally:
        if db is not None:
            db.close()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_configuration(path):
    metadata = path.with_suffix(".validated.json")
    try:
        result = subprocess.run(["java", "-Xmx256m", "-cp", str(APP / "synthifhir.jar"),
                                 "de.uni_leipzig.life.synthifhir.ConfigurationPreflight", str(path), str(metadata)],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Configuration validation failed")
        return json.loads(metadata.read_text())
    except subprocess.TimeoutExpired as error:
        raise ValueError("Configuration validation timed out") from error
    finally:
        metadata.unlink(missing_ok=True)


class SubmissionConflict(ValueError):
    pass


def submitted(request_id, fingerprint, db):
    if not request_id:
        return None
    row = db.execute("SELECT fingerprint,jobs FROM submissions WHERE id=?", (request_id,)).fetchone()
    if row:
        if row['fingerprint'] != fingerprint:
            raise SubmissionConflict('This submission ID was already used for different settings')
        ids = json.loads(row['jobs'])
        if any(db.execute('SELECT id FROM jobs WHERE id=?', (identifier,)).fetchone() is None for identifier in ids):
            raise SubmissionConflict('This submission belongs to a deleted run; start a new run')
        return ids
    return None


def submit(descriptor, request_id, prepare, *, register=None):
    """Publish all prepared snapshots in one transaction; retries return the same jobs."""
    fingerprint = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
    with connect() as db:
        existing = submitted(request_id, fingerprint, db)
    if existing is not None:
        return existing
    prepared = []
    accepted = False
    try:
        prepare(prepared)
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = submitted(request_id, fingerprint, db)
            if existing is not None:
                return existing
            now = time.time()
            for index, job_id in enumerate(prepared):
                db.execute("INSERT INTO jobs(id,state,created) VALUES (?, 'queued', ?)", (job_id, now + index * 0.000001))
            if register:
                register(db, prepared)
            if request_id:
                db.execute("INSERT INTO submissions(id,fingerprint,jobs) VALUES (?,?,?)",
                           (request_id, fingerprint, json.dumps(prepared)))
        accepted = True
        return prepared
    finally:
        if not accepted:
            for job_id in prepared:
                shutil.rmtree(ROOT / 'jobs' / job_id)


def prepare_job(prepared, source, profile, configuration_properties=None, *, input_path=None,
                source_name=None, input_kind='workbook', generation_settings=None, dataset_name=None, table_format=None, diagnostics=False, continuation=None):
    specification = None
    if input_path is None and source == 'synthea-generation':
        import generation
        specification = generation.normalize(generation_settings)
        input_kind = 'synthea-generation'
    elif generation_settings is not None:
        raise ValueError('Generation settings require the Synthea generation source')
    if input_path is None and specification is None:
        if source in SOURCES:
            input_path = APP / SOURCES[source]
        else:
            from inputs import resolve
            input_path, source_name, input_kind = resolve(source)
    if profile != 'default':
        raise ValueError('Unknown configuration source')
    job_id = str(uuid.uuid4())
    directory = ROOT / 'jobs' / job_id
    directory.mkdir(parents=True)
    prepared.append(job_id)
    from inputs import input_filename
    filename = input_filename(input_kind)
    if specification is None:
        shutil.copyfile(input_path, directory / filename)
        if input_kind == 'workbook-set':
            shutil.copytree(input_path.parent / 'workbooks', directory / 'workbooks')
            import run_replay
            run_replay.verify(directory, json.loads((directory / filename).read_text()))
    else:
        (directory / filename).write_text(json.dumps(specification, sort_keys=True, indent=2))
    bulk = specification is not None and specification.get('executionMode') == 'bulk'
    if bulk and (diagnostics or table_format == 'excel'):
        raise ValueError('Bulk generation uses CSV without diagnostics')
    if bulk:
        import bulk_resources
        bulk_resources.require_available()
    native = input_kind == 'synthea-generation' and json.loads((directory / filename).read_text()).get('outputMode') == 'synthea'
    if table_format is not None and (table_format not in {'csv', 'excel'} or not input_kind.startswith('synthea') or native):
        raise ValueError('Table format requires Synthea KDS conversion and must be csv or excel')
    if diagnostics and (not input_kind.startswith('synthea') or native):
        raise ValueError('Source diagnostics require Synthea KDS conversion')
    if native:
        if configuration_properties is not None:
            raise ValueError('Synthea FHIR output does not use a KDS configuration')
        execution = {'formats': ['JSON'], 'validation': False}
        config = {'id': 'synthea', 'name': 'Synthea FHIR'}
    else:
        if configuration_properties is None:
            shutil.copyfile(APP / 'defaults.config', directory / 'default.config')
            execution = {'formats': ['JSON', 'NDJSON'], 'validation': False, 'patientsPerFile': 1}
        else:
            if not isinstance(configuration_properties, str) or not 1 <= len(configuration_properties) <= 1_000_000:
                raise ValueError('Invalid configuration size')
            (directory / 'default.config').write_text(configuration_properties)
            execution = validate_configuration(directory / 'default.config')
        config = {'id': 'default' if configuration_properties is None else 'editor',
                  'name': 'Converter defaults' if configuration_properties is None else 'Submitted configuration',
                  'optionsProperties': (directory / 'default.config').read_text()}
    counter_starts = {}
    if bulk:
        if not native:
            counter_starts = execution.get('identifierCounterStarts', {})
        if execution.get('additionalRepetitions', 0):
            raise ValueError('Bulk generation requires zero additional patient copies')
        execution = {'formats': ['JSONGZIP'], 'validation': False, 'patientsPerFile': 1}
    snapshot = {'schemaVersion': 1, 'source': source, 'profile': config,
                'inputKind': input_kind, 'inputSha256': digest(directory / filename), 'converterSha256': digest(APP / 'synthifhir.jar'), **execution}
    if input_kind == 'workbook-set':
        manifest = json.loads((directory / filename).read_text())
        snapshot['repeatedFrom'] = {'id': manifest['sourceRun'], 'snapshotSha256': manifest['sourceSnapshotSha256']}
        snapshot['clinicalTimeZone'] = manifest.get('clinicalTimeZone')
    if input_kind.startswith('synthea'):
        import synthea_runtime
        if not native:
            snapshot['tableFormat'] = table_format or 'csv'
            snapshot['diagnostics'] = diagnostics
            snapshot['syntheaImportSha256'] = synthea_runtime.fingerprint()
        if input_kind == 'synthea-generation':
            import generation
            generation.normalize(json.loads((directory / filename).read_text()))
            snapshot['syntheaGeneratorSha256'] = generation.catalogue()['sha256']
            snapshot['syntheaRevision'] = generation.catalogue()['revision']
            snapshot['generation'] = json.loads((directory / filename).read_text())
    import bulk_generation
    snapshot['resultOutputName'] = bulk_generation.output_directory_name(
        job_id, dataset_name or 'Datensatz', time.time(),
        specification['population'] if bulk else None, continuation)
    if bulk:
        snapshot['bulkLayoutVersion'] = 5
        if not specification.get('singlePersonSeed'):
            snapshot['bulkSourceVersion'] = 1
        snapshot['identifierCounterStarts'] = counter_starts
        if continuation is not None:
            snapshot['continuation'] = continuation
            if continuation.get('remainder'):
                import bulk_sources
                shutil.copyfile(ROOT / 'jobs' / continuation['sourceRun'] / bulk_sources.REMAINDER, directory / bulk_sources.INHERITED)
                if digest(directory / bulk_sources.INHERITED) != continuation['remainder']['sha256']:
                    raise SubmissionConflict('Saved source remainder changed')
                (directory / bulk_sources.INHERITED).chmod(0o444)
            shutil.copyfile(ROOT / 'jobs' / continuation['sourceRun'] / 'bulk-identifier-history.sqlite', directory / 'series-identifiers.sqlite')
            if digest(directory / 'series-identifiers.sqlite') != continuation['identifierHistorySha256']:
                raise SubmissionConflict('Series identifier history changed')
            (directory / 'series-identifiers.sqlite').chmod(0o444)
        snapshot['bulkOutputName'] = snapshot['resultOutputName']
        import result_exports
        snapshot['bulkOutputDirectory'] = result_exports.label(snapshot)
    if dataset_name:
        snapshot['datasetName'] = dataset_name
    if source_name:
        snapshot['sourceName'] = source_name
    (directory / 'snapshot.json').write_text(json.dumps(snapshot, indent=2))
    for name in (filename, 'default.config', 'snapshot.json'):
        if (directory / name).exists():
            (directory / name).chmod(0o444)


def normalize_dataset_name(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError('Dataset name must be at most 200 characters')
    return value.strip() or None


def create(source, profile, configuration_properties=None, request_id=None, generation_settings=None, dataset_name=None, *, table_format=None, diagnostics=False):
    dataset_name = normalize_dataset_name(dataset_name)
    descriptor = {'kind': 'single', 'source': source, 'profile': profile, 'configuration': configuration_properties}
    if diagnostics:
        descriptor['diagnostics'] = True
    if table_format is not None:
        descriptor['tableFormat'] = table_format
    if generation_settings is not None:
        descriptor['generation'] = generation_settings
    if dataset_name:
        descriptor['datasetName'] = dataset_name
    import inputs
    # Complete the run's independent copies before an input can be removed.
    with inputs.library_lock():
        return submit(descriptor, request_id,
                      lambda prepared: prepare_job(prepared, source, profile, configuration_properties, generation_settings=generation_settings, dataset_name=dataset_name, table_format=table_format, diagnostics=diagnostics))[0]


def editor_input(job_id):
    import inputs
    folder = ROOT / 'jobs' / job_id
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    kind = snapshot.get('inputKind', 'workbook')
    if kind == 'workbook-set':
        import run_replay
        return run_replay.editor(job_id)
    path = folder / inputs.input_filename(kind)
    if not path.is_file():
        raise SubmissionConflict('The source was removed during cleanup. Download the saved Excel workbooks and upload one to repeat the conversion.')
    if digest(path) != snapshot['inputSha256']:
        raise ValueError('The saved input has changed')
    source = snapshot['source']
    if kind != 'synthea-generation':
        # Reuse the immutable saved bytes, even if the original upload was removed.
        with inputs.incoming() as incoming:
            shutil.copyfile(path, incoming / inputs.input_filename(kind))
            suffix = {'workbook': '.xlsx', 'csv': '.zip', 'synthea': '.json', 'synthea-zip': '.zip'}[kind]
            name = snapshot.get('sourceName') or ('Run-' + job_id[:8] + suffix)
            source = inputs.publish(incoming, name)['id']
    import result_exports
    return {'source': source, 'generation': snapshot.get('generation'),
            **({'tableFormat': snapshot.get('tableFormat', 'excel'), 'diagnostics': snapshot.get('diagnostics', True)} if kind.startswith('synthea') and snapshot['profile']['id'] != 'synthea' else {}),
            'datasetName': result_exports.dataset_name(snapshot),
            'configurationProperties': snapshot['profile'].get('optionsProperties')
                or (APP / 'defaults.config').read_text()}


def jobs():
    with connect() as db:
        return [job_result(row) for row in db.execute("SELECT * FROM jobs ORDER BY created DESC")]


def job_result(row):
    job = dict(row)
    job['duration_seconds'] = (job.get('elapsed', 0) + max(0, (job.get('finished') or time.time()) - job['started'])
                               if job.get('started') is not None and (job.get('finished') is not None or job['state'] == 'running') else (job.get('elapsed') or None))
    job["download_available"] = job["state"] in {"succeeded", "failed"} and (ROOT / "jobs" / job["id"] / "result.zip").is_file()
    cleanup_path = ROOT / 'jobs' / job['id'] / 'cleanup.json'
    if cleanup_path.is_file():
        cleanup = json.loads(cleanup_path.read_text())
        job['cleanup'] = {key: cleanup[key] for key in ('completed', 'removedBytes', 'removedFiles')}
        job['download_available'] = True
    snapshot_path = ROOT / 'jobs' / job['id'] / 'snapshot.json'
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text())
        import inputs, run_replay, result_exports
        job['workbook_count'] = len(run_replay.workbooks(snapshot_path.parent, snapshot))
        job['editor_available'] = (snapshot_path.parent / inputs.input_filename(snapshot.get('inputKind', 'workbook'))).is_file()
        config = snapshot['profile']
        job['summary'] = run_summary.summary(snapshot_path.parent, snapshot, job['state'])
        job['outcomes'] = run_outcomes.read(snapshot_path.parent, snapshot, job['state'])
        job['table_format'] = snapshot.get('tableFormat')
        job.update(input_kind=snapshot.get('inputKind', 'workbook'), dataset_name=result_exports.dataset_name(snapshot), source=snapshot['source'], source_name=snapshot.get('sourceName'), configuration={'id': config['id'], 'name': config['name'],
                   'revision': config.get('revision')}, batch_id=snapshot.get('batchId'),
                   repeated_from=snapshot.get('repeatedFrom', {}).get('id'), generation=snapshot.get('generation'))
        if snapshot.get('resultOutputName'):
            import result_exports
            job['output_directory'] = result_exports.label(snapshot)
        if snapshot.get('generation', {}).get('executionMode') == 'bulk':
            import bulk_generation
            job['bulk_output_directory'] = job.get('output_directory') or (bulk_generation.output_directory_label(snapshot['bulkOutputName'])
                                            if snapshot.get('bulkOutputName') else snapshot['bulkOutputDirectory'])
            job['continuation'] = snapshot.get('continuation')
            job['bulk_continuable'] = job['state'] == 'succeeded' and not snapshot['generation'].get('singlePersonSeed') and (snapshot.get('bulkSourceVersion') or snapshot['generation']['population'] % 100 == 0) and (snapshot_path.parent / 'bulk-completion.json').is_file()
            progress = snapshot_path.parent / 'bulk-progress.json'
            job['bulk_progress'] = json.loads(progress.read_text()) if progress.is_file() else None
            if snapshot.get('bulkLayoutVersion', 0) >= 2:
                import bulk_plan
                job['bulk_subruns'] = bulk_plan.subruns(snapshot['generation'], job['bulk_progress'] or {}, job['state'], snapshot.get('continuation', {}).get('sourceOffset', 0))
                job['bulk_resumable'] = job['state'] in {'failed', 'cancelled', 'interrupted'}
        result = snapshot_path.parent / 'generation-result.json'
        if job['state'] in {'succeeded', 'failed'} and result.is_file():
            job['generation_result'] = json.loads(result.read_text())
    failure = ROOT / 'jobs' / job['id'] / 'failure.json'
    if failure.is_file():
        job['failure'] = json.loads(failure.read_text())
    return job


def get(job_id):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return job_result(row) if row else None


def cancel(job_id):
    with connect() as db:
        db.execute("UPDATE jobs SET cancel=1, state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE id=? AND state IN ('queued','running')", (job_id,))



def resume(job_id):
    """Requeue the same immutable bulk snapshot; published chunks remain committed."""
    job_id = str(uuid.UUID(job_id))
    with run_lock(job_id):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Run not found')
            if row['state'] == 'queued':
                return  # An identical retry must not create or restart anything.
            if row['state'] not in {'failed', 'cancelled', 'interrupted'}:
                raise SubmissionConflict('Only stopped bulk jobs can be resumed')
            folder = ROOT / 'jobs' / job_id
            snapshot = json.loads((folder / 'snapshot.json').read_text())
            if snapshot.get('bulkLayoutVersion', 0) < 2:
                raise SubmissionConflict('This run has no resumable subrun checkpoints')
            import bulk_resources
            bulk_resources.require_available()
            elapsed = max(0, (row['finished'] or time.time()) - row['started']) if row['started'] else 0
            failure = folder / 'failure.json'
            if failure.exists():
                failure.rename(folder / f'failure-{time.time_ns()}.json')
            db.execute("UPDATE jobs SET state='queued',cancel=0,exit_code=NULL,elapsed=elapsed+?,started=NULL,finished=NULL WHERE id=?", (elapsed, job_id))


def claim():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT id FROM jobs WHERE state='queued' AND cancel=0 ORDER BY created LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET state='running',started=? WHERE id=?", (time.time(), row['id']))
            return row['id']


def finish(job_id, state, code=None):
    # Publish terminal state only after timing is saved, so deletion cannot race final writes.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT started,elapsed FROM jobs WHERE id=?', (job_id,)).fetchone()
        ended = time.time()
        if row and row['started'] is not None:
            timing = {'started': row['started'], 'finished': ended, 'duration_seconds': row['elapsed'] + max(0, ended - row['started'])}
            folder = ROOT / 'jobs' / job_id
            (folder / 'timing.json').write_text(json.dumps(timing, indent=2))
            with (folder / 'converter.log').open('a') as log:
                log.write(f"\nRun duration: {timing['duration_seconds']:.3f} seconds\n")
            if (folder / 'result.zip').is_file():
                with zipfile.ZipFile(folder / 'result.zip', 'a', zipfile.ZIP_DEFLATED) as archive:
                    if 'timing.json' not in archive.namelist():
                        archive.write(folder / 'timing.json', 'timing.json')
        db.execute("UPDATE jobs SET state=CASE WHEN cancel=1 AND ?='succeeded' THEN 'cancelled' ELSE ? END,exit_code=?,finished=? WHERE id=?", (state, state, code, ended, job_id))


def delete(job_id):
    """Remove a completed local run; upload records and server contents are retained."""
    job_id = str(uuid.UUID(job_id))
    with run_lock(job_id):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Run not found')
            if row['state'] in {'queued', 'running'}:
                raise SubmissionConflict('Cancel the run before deleting it')
            for upload in db.execute("SELECT descriptor FROM uploads WHERE state IN ('queued','preparing','uploading')"):
                if any(item['jobId'] == job_id for item in json.loads(upload['descriptor'])['datasets']):
                    raise SubmissionConflict('An active upload still uses this run')
            folder = ROOT / 'jobs' / job_id
            if (folder / 'snapshot.json').exists():
                import result_exports
                result_exports.remove(json.loads((folder / 'snapshot.json').read_text()))
            if folder.exists():
                shutil.rmtree(folder)
            db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
            # Keep submission IDs as tombstones: retries must never recreate a deleted run.


@contextmanager
def run_lock(job_id):
    locks = ROOT / 'locks'
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / (str(uuid.UUID(job_id)) + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
