"""Continue an immutable bulk series from its committed terminal counters."""
import json
import zipfile
from uuid import UUID

import bulk_sources
import bulk_plan
import store
import result_exports

STATE_FILE = 'bulk-completion.json'


def completion(folder, snapshot, counters):
    starts = snapshot.get('identifierCounterStarts', {})
    if set(counters) != set(starts) or any(type(value) is not int or value < starts[key] or value >= 2**63 - 1
                                          for key, value in counters.items()):
        raise ValueError('Incomplete or exhausted identifier counters')
    remainder = None
    if snapshot.get('bulkSourceVersion'):
        position = snapshot.get('continuation', {}).get('sourceOffset', 0) + snapshot['generation']['population']
        if position % 100:
            remainder = bulk_sources.description(folder / bulk_sources.REMAINDER)
            if remainder['offset'] != position % 100 or remainder['seed'] != bulk_plan.seed_at(snapshot['generation']['patientSeed'], position // 100):
                raise ValueError('Source remainder does not match completed patients')
    return {**({'remainder': remainder} if snapshot.get('bulkSourceVersion') else {}), 'schemaVersion': 1, 'snapshotSha256': store.digest(folder / 'snapshot.json'),
            'completed': snapshot['generation']['population'], 'identifierCounters': counters,
            'identifierHistorySha256': store.digest(folder / 'bulk-identifier-history.sqlite')}


def state(job_id):
    folder = store.ROOT / 'jobs' / job_id
    snapshot = json.loads((folder / 'snapshot.json').read_text())
    settings = snapshot.get('generation', {})
    if settings.get('executionMode') != 'bulk' or settings.get('singlePersonSeed') or (not snapshot.get('bulkSourceVersion') and settings['population'] % 100):
        raise store.SubmissionConflict('This run has no saved source remainder for continuation')
    try:
        saved = json.loads((folder / STATE_FILE).read_text())
        expected = completion(folder, snapshot, saved['identifierCounters'])
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        raise store.SubmissionConflict('This run has no valid completion counters') from error
    if saved != expected:
        raise store.SubmissionConflict('The saved completion state does not match this run')
    return snapshot, saved


def latest(job_id):
    job_id = str(UUID(job_id))
    with store.connect() as db:
        seen = set()
        while True:
            if job_id in seen:
                raise store.SubmissionConflict('Invalid series chain')
            seen.add(job_id)
            link = db.execute('SELECT child FROM bulk_series_links WHERE parent=?', (job_id,)).fetchone()
            if not link:
                break
            job_id = link['child']
        job = db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        raise store.SubmissionConflict('The latest series block is no longer available')
    if job['state'] != 'succeeded':
        raise store.SubmissionConflict('Complete or resume the latest series block before continuing')
    return job_id


def preview(job_id):
    latest_id = latest(job_id)
    with store.run_lock(latest_id):
        snapshot, saved = state(latest_id)
        settings = dict(snapshot['generation'])
        settings.update(patientSeed=bulk_plan.seed_at(settings['patientSeed'], (snapshot.get('continuation', {}).get('sourceOffset', 0) + settings['population']) // 100), timestampSeeds=False)
        previous = snapshot.get('continuation', {})
        return {'sourceRun': latest_id, 'requestedRun': job_id, 'seriesRoot': previous.get('seriesRoot', latest_id),
                'completedBefore': previous.get('completedBefore', 0) + saved['completed'],
                'sourceOffset': (saved.get('remainder') or {}).get('offset', 0),
                'generation': settings, 'datasetName': result_exports.dataset_name(snapshot),
                'configurationProperties': snapshot['profile'].get('optionsProperties'),
                'nextCounters': {key: str(value + 1) for key, value in saved['identifierCounters'].items()}}


def create(job_id, population, request_id=None):
    job_id = str(UUID(job_id))
    descriptor = {'kind': 'bulk-continuation', 'sourceRun': job_id, 'population': population}
    # The exact previewed predecessor must still be the tip at submission time.
    # A unique SQL link is committed together with the new job and retry token.
    with store.run_lock(job_id):
        def prepare(prepared):
            if latest(job_id) != job_id:
                raise store.SubmissionConflict('This block was already continued; reload the series')
            snapshot, saved = state(job_id)
            settings = dict(snapshot['generation'])
            settings.update(population=population,
                            patientSeed=bulk_plan.seed_at(settings['patientSeed'], (snapshot.get('continuation', {}).get('sourceOffset', 0) + saved['completed']) // 100))
            previous = snapshot.get('continuation', {})
            continuation = {'seriesNumber': result_exports.series_number(snapshot) + 1, 'sourceRun': job_id, 'seriesRoot': previous.get('seriesRoot', job_id),
                            'completedBefore': previous.get('completedBefore', 0) + saved['completed'],
                            'identifierCounters': saved['identifierCounters'],
                            'identifierHistorySha256': saved['identifierHistorySha256'],
                            'sourceOffset': (saved.get('remainder') or {}).get('offset', 0),
                            'remainder': saved.get('remainder')}
            store.prepare_job(prepared, 'synthea-generation', 'default', snapshot['profile'].get('optionsProperties'),
                              generation_settings=settings, dataset_name=result_exports.dataset_name(snapshot), continuation=continuation)
            new = json.loads((store.ROOT / 'jobs' / prepared[-1] / 'snapshot.json').read_text())
            for key in ('converterSha256', 'syntheaImportSha256', 'syntheaGeneratorSha256', 'syntheaRevision'):
                if new.get(key) != snapshot.get(key):
                    raise store.SubmissionConflict('The generator or converter changed; start a new series')

        def register(db, prepared):
            if db.execute('SELECT child FROM bulk_series_links WHERE parent=?', (job_id,)).fetchone():
                raise store.SubmissionConflict('This block was already continued; reload the series')
            db.execute('INSERT INTO bulk_series_links(parent,child) VALUES (?,?)', (job_id, prepared[0]))

        return store.submit(descriptor, request_id, prepare, register=register)[0]
