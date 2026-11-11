"""Single supervisor; bounded parallel job processes can be added behind claim()."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import synthea_runtime
import generation
import memory_failures
import datasets
import fhir_uploads
import time
import zipfile

import store
import inputs
import run_outcomes

stopping = False


def stop(*_):
    global stopping
    stopping = True


def terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def execute(job_id):
    folder = store.ROOT / "jobs" / job_id
    command = ["java", "-Xmx1g"]
    process = None
    memory_before = memory_failures.oom_kills()
    (folder / "memory-watch.json").write_text(json.dumps({"oomKills": memory_before}))
    try:
        snapshot = json.loads((folder / "snapshot.json").read_text())
        if snapshot.get('validation', False):
            command[1] = '-Xmx3g'
        if store.digest(store.APP / "synthifhir.jar") != snapshot["converterSha256"]:
            raise RuntimeError("Converter image changed after submission; start a new run")
        kind = snapshot.get('inputKind', 'workbook')
        input_path = folder / inputs.input_filename(kind)
        if store.digest(input_path) != snapshot["inputSha256"]:
            raise RuntimeError("Input snapshot has changed")
        bulk = snapshot.get('generation', {}).get('executionMode') == 'bulk'
        native = kind == 'synthea-generation' and snapshot.get('generation', {}).get('outputMode') == 'synthea'
        if kind.startswith('synthea'):
            if not native and synthea_runtime.fingerprint() != snapshot['syntheaImportSha256']:
                raise RuntimeError('Synthea importer changed after submission; start a new run')
            if kind == 'synthea-generation' and store.digest(synthea_runtime.ROOT / 'target/synthea.jar') != snapshot['syntheaGeneratorSha256']:
                raise RuntimeError('Synthea generator changed after submission; start a new run')
            source = input_path
            if kind == 'synthea-zip':
                source = folder / 'input-synthea'
                inputs.extract_archive(input_path, source, '.json')
            command = ([sys.executable, str(synthea_runtime.ROOT / 'scripts/run_synthea_workflow.py')]
                       if kind == 'synthea-generation' else
                       [sys.executable, str(synthea_runtime.ROOT / 'scripts/run_synthea_cases.py'),
                        '-i' if kind == 'synthea-zip' else '-f', str(source)])
            command += [
                       '-r', ','.join(snapshot.get('formats', ['JSON', 'NDJSON'])),
                       '-p', str(snapshot.get('patientsPerFile', 1))]
            if snapshot.get('validation', False):
                command.append('-v')
        elif kind == 'resource-addition':
            from pathlib import Path
            command = [sys.executable, str(Path(__file__).with_name('resource_additions.py')), str(folder)]
        elif kind == 'workbook-set':
            import run_replay
            run_replay.verify(folder, json.loads(input_path.read_text()))
            if snapshot.get('clinicalTimeZone'):
                command.append('-Duser.timezone=' + snapshot['clinicalTimeZone'])
            command.extend(['-jar', str(store.APP / 'synthifhir.jar'), '-i', str(folder / 'workbooks')])
        elif kind == 'csv':
            expanded = folder / 'input-csv'
            inputs.extract_csv(input_path, expanded)
            command.extend(['-cp', str(store.APP / 'synthifhir.jar'), 'de.uni_leipzig.life.synthifhir.Main', '-i', str(expanded)])
        else:
            command.extend(['-jar', str(store.APP / 'synthifhir.jar'), '-f', str(input_path)])
        if kind != 'resource-addition':
            command.extend(['-o', str(folder / 'output')])
        if snapshot["profile"]["id"] not in {"synthea", "addition"}:
            if (folder / "default.config").read_text() != snapshot["profile"]["optionsProperties"]:
                raise RuntimeError("Configuration snapshot has changed")
            command.extend(["--converter-options", str(folder / "default.config")])
        if kind == 'synthea-generation':
            command += ['--', *generation.arguments(json.loads(input_path.read_text()))]
        if native and not bulk:
            destination = folder / 'output/run-synthea'
            destination.mkdir(parents=True)
            command = generation.native_command(snapshot['generation'], destination)
        if bulk:
            import bulk_resources
            bulk_resources.require_available(container=True)
            from pathlib import Path
            command = [sys.executable, str(Path(__file__).with_name('bulk_generation.py')), str(folder)]
        environment = dict(os.environ)
        environment.pop('SYNTHIFHIR_CONSENT_SEED', None)
        environment.pop('SYNTHIFHIR_CONSENT_CONTEXT', None)
        if kind == 'synthea-generation' and snapshot['generation'].get('syntheticConsent'):
            environment['SYNTHIFHIR_CONSENT_SEED'] = snapshot['generation']['patientSeed']
            environment['SYNTHIFHIR_CONSENT_CONTEXT'] = json.dumps({
                'end_date': snapshot['generation']['endDate'],
                'years_of_history': snapshot['generation']['yearsOfHistory']})
        if kind.startswith('synthea') and not native:
            table_format = snapshot.get('tableFormat', 'excel')
            if table_format not in {'csv', 'excel'}:
                raise ValueError('Unknown Synthea table format')
            environment['SYNTHIFHIR_TABLE_FORMAT'] = table_format
            environment['SYNTHIFHIR_DIAGNOSTICS'] = 'true' if snapshot.get('diagnostics', True) else 'false'
        with (folder / "converter.log").open("a" if bulk else "w") as log:
            process = subprocess.Popen(command, cwd=folder, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=environment)
            while process.poll() is None:
                if stopping or store.get(job_id)["cancel"]:
                    terminate(process)
                    break
                time.sleep(0.2)
        state = "interrupted" if stopping else "cancelled" if store.get(job_id)["cancel"] else "succeeded" if process.returncode == 0 else "failed"
        failure = None
        if state in {'succeeded', 'failed'}:
            failure = memory_failures.record(folder, memory_before, process.returncode)
            if failure:
                state = 'failed'
                with (folder / 'converter.log').open('a') as log:
                    log.write('\n' + failure['evidence'] + '\n')
        if not bulk and kind == 'synthea-generation' and state in {'succeeded', 'failed'} and not failure:
            generated = list((folder / 'output').glob('*/details/sources/synthea/fhir/*.json'))
            if native:
                generated = list((folder / 'output/run-synthea/fhir').glob('*.json'))
            summaries = list((folder / 'output').glob('*/details/reports/summary.json'))
            summary = json.loads(summaries[0].read_text()) if summaries else {}
            (folder / 'generation-result.json').write_text(json.dumps({
                'generatedPatients': generation.patient_count(generated), 'importedPatients': len(summary.get('results', [])),
                'failedPatients': len(summary.get('failures', []))}))
        if not bulk and state in {"succeeded", "failed"}:
            if not failure:
                datasets.build(job_id, snapshot, lambda: stopping or bool(store.get(job_id)["cancel"]))
            run_outcomes.save(folder, snapshot, state)
            with zipfile.ZipFile(folder / "result.tmp", "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted((folder / "output").rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(folder))
                if (folder / "generation-result.json").exists():
                    archive.write(folder / "generation-result.json", "generation-result.json")
                for name in ("datasets.json", "failure.json", "outcomes.json"):
                    if (folder / name).exists():
                        archive.write(folder / name, name)
                for path in folder.glob("dataset-*.log"):
                    archive.write(path, path.name)
                if kind == 'workbook-set':
                    for path in sorted((folder / 'workbooks').glob('*.xlsx')):
                        archive.write(path, path.relative_to(folder))
                    archive.write(folder / 'workbooks.json', 'workbooks.json')
                    archive.write(folder / 'default.config', 'default.config')
                archive.write(folder / "snapshot.json", "snapshot.json")
                archive.write(folder / "converter.log", "converter.log")
            (folder / "result.tmp").rename(folder / "result.zip")
        if stopping:
            state = "interrupted"
        elif store.get(job_id)["cancel"]:
            state = "cancelled"
        store.finish(job_id, state, process.returncode)
    except datasets.CancelledInspection:
        store.finish(job_id, "interrupted" if stopping else "cancelled")
    except Exception as error:
        if process:
            terminate(process)
        with (folder / "converter.log").open("a") as log:
            log.write(f"\n{type(error).__name__}: {error}\n")
        memory_failures.record(folder, memory_before, process.returncode if process else None)
        store.finish(job_id, "failed")


def main():
    store.ROOT.mkdir(parents=True, exist_ok=True)
    # One supervisor owns recovery. Queue claims are transactional for future slots.
    with (store.ROOT / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.connect() as db:
            for row in db.execute("SELECT id FROM jobs WHERE state='running'").fetchall():
                folder = store.ROOT / 'jobs' / row['id']
                watch = folder / 'memory-watch.json'
                before = json.loads(watch.read_text()).get('oomKills') if watch.exists() else None
                failure = memory_failures.record(folder, before)
                db.execute("UPDATE jobs SET state=? WHERE id=?", ('failed' if failure else 'interrupted', row['id']))
        fhir_uploads.recover()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopping:
            job_id = store.claim()
            if job_id:
                execute(job_id)
            elif upload_id := fhir_uploads.claim():
                fhir_uploads.execute(upload_id, lambda: stopping, terminate)
            else:
                datasets.backfill(lambda: stopping)
                time.sleep(0.5)


if __name__ == "__main__":
    main()
