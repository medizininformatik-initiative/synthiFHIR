#!/usr/bin/env python3
"""Synthea R4 -> German tables -> FHIR, with import and validation reports.
Usage: run_synthea_cases.py [-i INPUT_DIRECTORY | -f INPUT_FILE] [-o OUTPUT_ROOT] [-v]
Requires Java/JDK 17+, LibreOffice and a freshly built target/synthifhir.jar.
Each invocation creates a fresh run below outputGlobal (or -o).
Source files stay unchanged.
"""
import argparse
from contextlib import nullcontext
import zipfile
from direct_csv import write_direct_csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from check_synthea_roundtrip import check_configured
from converter_options import resolve_config, selected_configs
from synthea_to_excel import prepare, write_workbook, WorkbookSession
from workbook_xml import read_sheets
from workflow_layout import create_run, write_status, add_converter_arguments, converter_settings, run_time
from fhir_output import read_output

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx'
JAR = ROOT / 'target/synthifhir.jar'


def run_conversion_batch(jobs, validate):
    if not jobs:
        return
    checkpoint = os.environ.get('SYNTHIFHIR_SERIES_DIRECTORY')
    if checkpoint:
        folder = Path(checkpoint)
        snapshot = json.loads((folder / 'snapshot.json').read_text())
        jobs[0].update(identifierCounters=snapshot.get('continuation', {}).get('identifierCounters', {}),
                       identifierCountersOutput=str(folder / 'series-counters.json'),
                       identifierRegistrationsOutput=str(folder / 'series-registrations.jsonl'))
    # Sequential workbook conversion benefits from SerialGC's smaller heap footprint.
    command = ['java', '-XX:+UseSerialGC', '-Xmx3g' if validate else '-Xmx1g', '-Duser.timezone=Europe/Berlin',
               '-Dsynthifhir.runOffset=' + run_time().strftime('%z'), '-cp', str(JAR),
               'de.uni_leipzig.imise.Excel2FhirBatch']
    batch_log = Path(jobs[0]['log']).parents[2] / 'reports/java-batch.log'
    with batch_log.open('w') as log:
        subprocess.run(command, input=json.dumps(jobs), text=True, stdout=log, stderr=subprocess.STDOUT)
    # Each completed job has its own durable result, including when a later job kills the JVM.


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def environment(table_format="excel", diagnostics=False):
    for required in (TEMPLATE, JAR):
        if not required.is_file():
            raise FileNotFoundError(required)
    office = '/Applications/LibreOffice.app/Contents/MacOS/soffice'
    if not Path(office).is_file():
        office = shutil.which('libreoffice') or shutil.which('soffice')
    if not office and table_format == 'excel':
        raise RuntimeError('LibreOffice is missing')
    versions = {}
    commands = [('java', ['java', '-version'])]
    if table_format == 'excel':
        commands += [('javac', ['javac', '-version']), ('libreoffice', [office, '--version'])]
    for name, command in commands:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        versions[name] = (result.stdout + result.stderr).strip()
    versions['python'] = sys.version
    files = [TEMPLATE, JAR, *sorted((ROOT / 'scripts').glob('*.py')),
             ROOT / 'scripts/ReadXmlBundles.java', ROOT / 'scripts/WorkbookUno.java', ROOT / 'scripts/WorkflowOptions.java', ROOT / 'src/main/resources/workbook-absent-reasons.json', *sorted((ROOT / 'scripts/mappings').glob('*')), *sorted((ROOT / 'src/main/resources/ucum').glob('*.map'))]
    return {'versions': versions, 'converterTimezone': 'Europe/Berlin', 'sha256': {
        str(p.relative_to(ROOT)): sha256(p) for p in files if p.is_file()}}


def inspect_conversion(directory, exit_code, reports=None, log=None, *, validate=False, expected_imports=1):
    reports = directory if reports is None else reports
    if exit_code < 0 or exit_code >= 128:
        raise ValueError(f'Converter process terminated (exit code {exit_code}); '
                         'see conversion.log. For SIGKILL/-9/137, check available '
                         'Docker/system memory.')
    bundles = [p for p in directory.rglob('*') if p.is_file()
               and p.name.endswith(('.json', '.ndjson', '.xml', '.gz', '.bz2', '.zip'))
               and not p.name.endswith(('.import.json', '.validation.json'))]
    imports = list(reports.rglob('*.import.json'))
    validations = list(reports.rglob('*.validation.json'))
    if not bundles or len(imports) != expected_imports or (validate and not validations):
        log = directory.parent / 'conversion.log' if log is None else log
        if log.is_file():
            with log.open(encoding='utf-8', errors='replace') as lines:
                if any('java.lang.OutOfMemoryError' in line for line in lines):
                    raise ValueError(f'Java heap exhausted (exit code {exit_code}); '
                                     'Allocate more memory to Docker or the local process. '
                                     'See conversion.log.')
        raise ValueError(f'FHIR bundle, import report or requested validation report is missing '
                         f'(converter exit code {exit_code}); see conversion.log')
    for path in imports:
        if json.loads(path.read_text())['status'] != 'COMPLETE':
            raise ValueError('Import incomplete; see ' + str(path))
    if not validate:
        if exit_code != 0:
            raise ValueError(f'Unexpected converter exit code {exit_code}')
        return bundles[0], {'importStatus': 'COMPLETE', 'validationStatus': 'NOT_VALIDATED',
                            'converterExitCode': exit_code}
    statuses = []
    for path in validations:
        validation = json.loads(path.read_text())
        status = validation['status']
        if status not in {'VALID', 'WARNING', 'IGNORED', 'NOT_CHECKED', 'ERROR', 'FATAL'}:
            raise ValueError('Unknown validation status: ' + status)
        if validation.get('referencesWithoutTargetInBundle'):
            status = 'ERROR'
        statuses.append(status)
    status = next((state for state in ('FATAL', 'ERROR', 'NOT_CHECKED', 'WARNING', 'IGNORED', 'VALID') if state in statuses), 'NOT_CHECKED')
    if exit_code != 0:
        raise ValueError(f'Unexpected converter exit code {exit_code} with status {status}')
    return bundles[0], {'importStatus': 'COMPLETE', 'validationStatus': status,
                        'converterExitCode': exit_code}


def prepare_workbook(source, out, defaults, workbook_session, table_format="excel", diagnostics=False, consent=None):
    """Persist one case; keep only paths, counts and timing between phases."""
    started = time.monotonic()
    bundle = json.loads(source.read_text(encoding='utf-8'))
    if not any(e.get('resource', {}).get('resourceType') == 'Patient'
               for e in bundle.get('entry', [])):
        return None
    case = out / 'details/cases' / source.stem
    case.mkdir()
    saved_source = source if source.is_relative_to(out / 'details/sources') else out / 'details/sources' / source.name
    if saved_source != source:
        shutil.copy2(source, saved_source)
    rows, report = prepare(bundle, defaults, diagnostics=diagnostics)
    if consent is not None:
        from synthea_consent import enrich
        report['syntheticConsent'] = enrich(rows, bundle, consent[report['sourcePatient']],
                                             os.environ['SYNTHIFHIR_CONSENT_SEED'],
                                             **json.loads(os.environ.get('SYNTHIFHIR_CONSENT_CONTEXT', '{}')))
    write_json(case / 'source.json', {'file': str(source), 'sha256': sha256(source)})
    source_patient = report['sourcePatient']
    if diagnostics:
        write_json(case / 'Fall.loss.json', report)
    # Mapping and reporting are complete; Office needs only the projected rows.
    del bundle, report
    book = (out / 'excel' / ('Fall-' + source.stem + '.xlsx') if table_format == 'excel'
            else case / ('Fall-' + source.stem + '.csv'))
    if table_format == 'excel':
        write_workbook(rows, book, options=defaults, session=workbook_session)
    else:
        write_direct_csv(rows, case / 'csv-input', book.stem)
    return {'source': source, 'saved_source': saved_source, 'case': case, 'book': book,
            'sourcePatient': source_patient,
            'rows': {name: len(values) for name, values in rows.items()},
            'seconds': time.monotonic() - started}


def archive_csv(out):
    """Stream tables into an atomic ZIP, then release their unpacked working copies."""
    files = sorted(path for path in (out / 'details/cases').rglob('*.csv') if path.is_file())
    if not files:
        return
    names = [path.name.casefold() for path in files]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate CSV archive filenames')
    pending = out / 'csv.zip.tmp'
    with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.name)
    with zipfile.ZipFile(pending) as archive:
        if archive.testzip() is not None:
            raise ValueError('CSV archive integrity check failed')
    pending.replace(out / 'csv.zip')
    for path in files:
        path.unlink()


def run(source_dir, output_dir, *, directory=None, validate=False, option_files=(),
        formats=None, patients_per_bundle=2147483647, validation_log_level='ERROR',
        log_layout='DATE_LEVEL_SOURCE_LINENUMBER', temp_directory=None, table_format=None, diagnostics=None):
    # The workbook contains local contact times; Python, Office and Java must agree.
    os.environ['TZ'] = 'Europe/Berlin'
    time.tzset()
    source_dir = Path(source_dir).resolve()
    if not source_dir.exists():
        raise ValueError('Source directory is missing: ' + str(source_dir))
    table_format = table_format or os.environ.get('SYNTHIFHIR_TABLE_FORMAT', 'excel')
    if table_format not in {'excel', 'csv'}:
        raise ValueError('Unknown Synthea table format: ' + table_format)
    if diagnostics is None:
        diagnostics = os.environ.get('SYNTHIFHIR_DIAGNOSTICS', 'false') == 'true'
    metadata = environment(table_format)
    metadata['diagnostics'] = diagnostics
    metadata['tableFormat'] = table_format
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = [source_dir] if source_dir.is_file() else sorted(source_dir.glob('*.json'))
    source_patients = []
    patient_sources = []
    for path in sources:
        try:
            candidate = json.loads(path.read_text(encoding='utf-8'))
            if any(e.get('resource', {}).get('resourceType') == 'Patient' for e in candidate.get('entry', [])):
                patient_sources.append(path)
            source_patients.extend(e['resource']['id'] for e in candidate.get('entry', [])
                                   if e.get('resource', {}).get('resourceType') == 'Patient')
        except (ValueError, KeyError, TypeError, AttributeError):
            pass  # The per-file conversion below records malformed inputs individually.
        finally:
            candidate = None
    consent = None
    if 'SYNTHIFHIR_CONSENT_SEED' in os.environ:
        from synthea_consent import assignments
        consent = assignments(source_patients, os.environ['SYNTHIFHIR_CONSENT_SEED'])
    selected = selected_configs(option_files, source_patients)
    defaults = resolve_config()['values'] if option_files else selected[0]['values']
    from terminology_year import settings
    import_settings = settings(selected[0]['values'])
    if any(settings(item['values']) != import_settings for item in selected):
        raise ValueError('One Synthea import uses one mapping year and version output; run differing settings separately')
    for key in ('SYNTHEA_MAPPING_YEAR', 'SYNTHEA_VERSION_OUTPUT'):
        defaults[key] = selected[0]['values'].get(key, '2026' if key == 'SYNTHEA_MAPPING_YEAR' else 'Jahr')
    out = create_run(output, 'synthea-import') if directory is None else Path(directory)
    snapshots = []
    for index, config in enumerate(option_files):
        snapshot = out / 'details/options-input' / str(index) / Path(config).name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if Path(config).resolve() != snapshot.resolve():
            shutil.copy2(config, snapshot)
        snapshots.append(snapshot)
    metadata['validationEnabled'] = validate
    metadata['converterOptions'] = [{'name': item['name'], 'values': item['values']} for item in selected]
    metadata['formats'] = formats or ['JSON', 'NDJSON']
    write_json(out / 'details/reports/environment.json', metadata)
    codes = read_sheets(TEMPLATE)['Codes'] if diagnostics and table_format == 'excel' else None
    summary = {'schemaVersion': 1, 'status': 'RUNNING', 'results': [], 'failures': [], 'skipped': []}
    summary_file = out / 'details/reports/summary.json'
    write_json(summary_file, summary)
    def conversion_job(task):
        case, book = task['case'], task['book']
        command = ['-f', str(book), '-o', str(case), '-r', ','.join(formats or ['JSON', 'NDJSON']),
                   '-p', str(patients_per_bundle), '-vll', validation_log_level, '-l', log_layout]
        if validate:
            command.append('-v')
        if temp_directory:
            command.extend(['-t', str(Path(temp_directory).resolve())])
        for config in snapshots:
            command.extend(['--converter-options', str(config)])
        return {'arguments': command, 'log': str(case / 'conversion.log'),
                'result': str(case / 'conversion-result.json'),
                **({'csvDirectory': str(case / 'csv-input')} if table_format == 'csv' else {})}

    def convert_workbook(task):
        started = time.monotonic()
        source, case, book = task['source'], task['case'], task['book']
        result_file = case / 'conversion-result.json'
        if not result_file.is_file():
            raise ValueError('Converter did not finish this case; see details/reports/java-batch.log')
        conversion = json.loads(result_file.read_text(encoding='utf-8'))
        runs = list(case.glob('run-*-' + ('excel' if table_format == 'excel' else 'csv') + '-to-fhir*'))
        if len(runs) != 1:
            raise ValueError('Converter run is missing; see ' + str(case / 'conversion.log'))
        converted = runs[0]
        generated = converted / 'fhir'
        destinations = {}
        try:
            _, statuses = inspect_conversion(generated, conversion['exitCode'],
                converted / 'details/reports', case / 'conversion.log', validate=validate,
                expected_imports=len(selected))
        finally:
            for item in selected:
                origin = generated / item['name'] if len(selected) > 1 else generated
                final = out / 'fhir'
                if len(selected) > 1:
                    final /= item['name']
                if len(patient_sources) > 1:
                    final /= book.name
                if origin.is_dir():
                    final.mkdir(parents=True, exist_ok=True)
                    for artifact in origin.iterdir():
                        shutil.move(str(artifact), final / artifact.name)
                destinations[item['name']] = final
        variants = [dict(name=item['name'], outputPatients=item['patients'][task['sourcePatient']],
                         comparisonStatus='not-requested') for item in selected]
        if diagnostics:
            # Release Office before starting Java, and load clinical data only after Java exits.
            bundle = json.loads(task['saved_source'].read_text(encoding='utf-8'))
            report = json.loads((case / 'Fall.loss.json').read_text(encoding='utf-8'))
            variants = []
            for item in selected:
                target = read_output(destinations[item['name']])
                patients = item['patients'][report['sourcePatient']]
                try:
                    result = check_configured(bundle, target, report, item['values'], patients)
                    result['comparisonStatus'] = 'passed'
                except AssertionError as error:
                    result = {'outputPatients': patients, 'comparisonStatus': 'differences',
                              'comparisonDetails': str(error) or 'Source comparison assertion failed'}
                except Exception as error:
                    result = {'outputPatients': patients, 'comparisonStatus': 'unavailable',
                              'comparisonDetails': type(error).__name__ + ': ' + str(error)}
                from transfer_findings import notes
                result.update(notes(bundle, report, item['values']))
                variants.append(dict(result, name=item['name']))
            if table_format == 'excel' and read_sheets(book)['Codes'] != codes:
                raise ValueError('The Codes sheet was modified during import')
        result = dict(statuses, variants=variants, source=str(source),
                      **({'workbook': str(book)} if table_format == 'excel' else {'csvArchive': str(out / 'csv.zip')}),
                      outputPatients=[pid for v in variants for pid in v['outputPatients']],
                      rows=task['rows'],
                      elapsedSeconds=round(task['seconds'] + conversion['seconds'] + time.monotonic() - started, 2))
        return result

    prepared = []
    with (WorkbookSession() if table_format == 'excel' else nullcontext()) as workbook_session:
        for source in sources:
            try:
                task = prepare_workbook(source, out, defaults, workbook_session, table_format, diagnostics, consent)
                if task is None:
                    summary['skipped'].append({'source': str(source), 'reason': 'No Patient in the bundle'})
                else:
                    prepared.append(task)
            except Exception as ex:
                summary['failures'].append({'source': str(source), 'error': repr(ex)})
                print('FAILED', source.name, repr(ex), flush=True)
            finally:
                write_json(summary_file, summary)
    # WorkbookSession has stopped Office; only lightweight task metadata remains.
    run_conversion_batch([conversion_job(task) for task in prepared], validate)
    for task in prepared:
        source = task['source']
        try:
            result = convert_workbook(task)
            summary['results'].append(result)
            print(result['validationStatus'], source.name, flush=True)
        except Exception as ex:
            summary['failures'].append({'source': str(source), 'error': repr(ex)})
            print('FAILED', source.name, repr(ex), flush=True)
        finally:
            write_json(summary_file, summary)
    if table_format == 'csv':
        archive_csv(out)
    if not summary['results'] and not summary['failures']:
        summary['failures'].append({'error': 'No Synthea patient bundles found'})
    summary['status'] = 'FAILED' if summary['failures'] else 'COMPLETE' if validate else 'NOT_VALIDATED'
    write_json(summary_file, summary)
    write_status(out, summary['status'])
    return 0 if summary['status'] in {'COMPLETE', 'NOT_VALIDATED'} else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument('-i', '--input-directory', default='input')
    inputs.add_argument('-f', '--input-file')
    add_converter_arguments(parser)
    args = parser.parse_args()
    raise SystemExit(run(args.input_file or args.input_directory, args.output_directory, **converter_settings(args)))
