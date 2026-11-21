#!/usr/bin/env python3
"""Synthea R4 bundle -> existing Excel template. Usage: script INPUT.json OUTPUT.xlsx.
Supported clinical resources and synthetic contacts are imported; unhandled source fields
are identified in a separate loss report. Requires LibreOffice, Java and Python 3.
"""
from workbook_records import record, ordered_values
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from workbook_xml import read_sheets, validation_ranges
from diagnosis_mapping import map_diagnosis, mapping_metadata
from clinical_import import prepare_clinical, SUPPORTED
from clinical_events import prepare_events, prepare_documents, SHEETS
from german_texts import localize_rows
from document_mapping import finalize_documents
from medication_products import require_external_output, require_external_path
from synthea_movements import enrich
from german_demographics import identity

ROOT = Path(__file__).resolve().parents[1]
SNOMED = 'http://snomed.info/sct'
CLINICAL = dict(zip(['active','recurrence','relapse','inactive','remission','resolved'],
                    ['Aktiv','Rezidiv','Rückfall','Inaktiv','Remission','Abgeklungen']))
VERIFICATION = dict(zip(['unconfirmed','provisional','differential','confirmed','refuted','entered-in-error'],
                        ['Unbestätigt','Vorläufig','Differentialdiagnose','Bestätigt','Widerlegt','Irrtümlich erfasst']))
# EMER follows the MII representation: AMB plus explicit admission reason 7.
CLASSES = {'EMER':'ambulant', 'AMB':'ambulant', 'IMP':'stationaer', 'HH':'home health', 'VR':'virtual', 'SS':'short stay', 'PRENC':'pre-admission'}


def prepare(bundle, options=None, *, diagnostics=True):
    from terminology_year import settings, version_rows, metadata
    year, version_mode = settings(options)
    entries = bundle.get('entry', [])
    if bundle.get('resourceType') != 'Bundle':
        raise ValueError('Expected a Synthea FHIR R4 Bundle')
    patients = [e['resource'] for e in entries if e.get('resource', {}).get('resourceType') == 'Patient']
    if len(patients) != 1:
        raise ValueError('Expected exactly one Patient per Synthea bundle')
    patient = patients[0]
    pid = patient['id']
    index = {}
    for e in entries:
        r = e.get('resource', {})
        if r.get('id'):
            index[r['resourceType'] + '/' + r['id']] = r
            if e.get('fullUrl'): index[e['fullUrl']] = r
    rows = {'Person':[], 'Fall':[], 'Diagnose':[]}
    losses = []
    def loss(r, path, reason):
        if diagnostics:
            losses.append({'resourceType':r['resourceType'], 'id':r.get('id'), 'path':path, 'reason':reason})
    def fields(r, handled):
        # Complete top-level property accounting, with explicit nested losses below.
        for key in r.keys() - set(handled) - {'resourceType','id'}:
            loss(r, key, 'Generator unterstützt Sachverhalt noch nicht')
    def ref(r, field, expected):
        target = index.get(r.get(field, {}).get('reference'))
        if target is None or target['resourceType'] != expected:
            raise ValueError(f"Unresolved {r['resourceType']}/{r.get('id')}.{field}")
        if expected == 'Patient' and target['id'] != pid:
            raise ValueError('Cross-patient reference')
        return target
    demographics = identity(patient)
    name = demographics['name']
    born = datetime.strptime(patient['birthDate'], '%Y-%m-%d').strftime('%d.%m.%Y 00:00')
    address = demographics['address']
    rows['Person'].append(record('Person', {
        'Patient-ID': pid,
        'Vorname': ' '.join(name.get('given', [])),
        'Nachname': name.get('family', ''),
        'Geburtsdatum': born,
        'Geschlecht': {'male':'männlich','female':'weiblich','other':'divers','unknown':'unbekannt'}[patient['gender']],
        'Straße': ', '.join(address.get('line', [])),
        'Postleitzahl': address.get('postalCode', ''),
        'Ort': address.get('city', ''),
        'Bundesland': address.get('state', ''),
        'Land': address.get('country', ''),
        'Sterbezeitpunkt': patient.get('deceasedDateTime', ''),
    }))
    fields(patient, {'name','birthDate','gender','address','deceasedDateTime'})
    for field in ('name','address'):
        loss(patient, field, 'Bewusst durch synthetische deutsche Personendaten ersetzt; Zuordnung unter demographics')
    if len(patient.get('address',[]))>1:loss(patient,'address[1:]','Weitere Anschriften nicht übernommen')
    if len(patient.get('name', [])) > 1: loss(patient, 'name[1:]', 'Generator unterstützt Sachverhalt noch nicht')
    encounter_numbers = {}
    encounter_mappings = []
    for e in entries:
        r = e.get('resource', {})
        if r.get('resourceType') != 'Encounter': continue
        ref(r, 'subject', 'Patient')
        if r.get('class', {}).get('system') != 'http://terminology.hl7.org/CodeSystem/v3-ActCode':
            raise ValueError('Unsupported encounter class system')
        code = r.get('class', {}).get('code')
        if code not in CLASSES:
            raise ValueError(f'Encounter class {code} is not supported by the current Excel generator; no relabelling applied')
        nr = str(len(encounter_numbers) + 1)
        encounter_numbers[r['id']] = nr
        def local_time(value):
            # Keep the offset: the autumn clock change has two distinct local 02:xx hours.
            if not value: return ''
            local = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone()
            # Historical local mean time can have offset seconds, which FHIR cannot encode.
            if local.utcoffset().total_seconds() % 60:
                local = local.astimezone(timezone.utc)
            return local.isoformat()
        period = r.get('period', {})
        rows['Fall'].append(record('Fall', {
            'Patient-ID': pid,
            'Fall-Nr': nr,
            'Start': local_time(period.get('start')),
            'Ende': local_time(period.get('end')),
            'Einrichtungskontaktklasse': CLASSES[code],
            'Aufnahmegrund (4. Stelle)': 'Notfall' if code == 'EMER' else '',
        }))
        if code == 'EMER':
            encounter_mappings.append({'sourceId':r['id'], 'sourceClass':'EMER', 'targetClass':'AMB',
                                      'admissionReasonFourthComponent':'7',
                                      'rule':'MII KDS Basis 2026.0.1: emergency encounter'})
        fields(r, {'subject','class','period'})
        for key in r.get('class', {}).keys() - {'system','code'}:loss(r,'class.'+key,'Generator unterstützt Sachverhalt noch nicht')
        for key in period.keys() - {'start','end'}:loss(r,'period.'+key,'Generator unterstützt Sachverhalt noch nicht')
        loss(r, 'id', 'Bewusst neu vergebene Fallnummer; Zuordnung im Bericht')
        loss(r, 'period', 'Zeitpunkte erhalten; lokale ISO-Darstellung einschließlich Zeitzonenoffset')
    rows['Fall'], movement_report = enrich(bundle, rows['Fall'], encounter_numbers)
    source_conditions = 0
    condition_rows = {}
    diagnosis_mappings = []
    for e in entries:
        r = e.get('resource', {});typ = r.get('resourceType')
        if typ in ['Patient','Encounter']: continue
        if typ in SUPPORTED or typ in SHEETS or typ == 'DocumentReference': continue
        if typ == 'AllergyIntolerance':
            loss(r, '$', 'Bewusst ausgeschlossen: Allergieunterstützung zurückgestellt; spätere IPS-Abbildung offen')
            continue
        if typ == 'Device':
            loss(r, '$', 'Bewusst ausgeschlossen: Geräte und Hilfsmittel werden nicht importiert')
            continue
        if typ != 'Condition':
            loss(r, '$', 'Noch nicht im klinischen Excel-Import umgesetzt')
            continue
        source_conditions += 1
        ref(r, 'subject', 'Patient')
        nr = ''
        if 'encounter' in r: nr = encounter_numbers[ref(r, 'encounter', 'Encounter')['id']]
        coding = r.get('code', {}).get('coding', [])
        if not coding: raise ValueError('Condition without coding; no empty clinical shell emitted')
        chosen = []
        for c in coding:
            if c.get('system') == SNOMED:
                selection = 'SNOMED CT (Version nicht angegeben)'
            elif c.get('system') == 'http://fhir.de/CodeSystem/bfarm/icd-10-gm' and c.get('version') in map(str,range(2009,2027)):
                selection = 'ICD-10-GM ' + c['version']
            else: selection = c.get('system', '')
            if not c.get('code'): raise ValueError('Source coding without code')
            chosen.append({'code': c['code'], 'system': selection, 'version': c.get('version', '')})
            for key in c.keys() - {'system','version','code'}:loss(r,'code.coding.'+key,'Bezeichner übernommen, Coding-Metadaten nicht separat darstellbar')
        if len(chosen)>2 or len({c.get('system')for c in coding}) != len(coding):
            raise ValueError('More codings than supported by the diagnosis sheet/profile')
        decision = map_diagnosis(r, year)
        diagnosis_mappings.append(decision)
        if decision['status'] == 'excluded':
            loss(r, '$', decision['reason'])
            continue
        condition_rows[r['id']] = len(rows['Diagnose']) + 2
        if decision['target'] is not None:
            target = decision['target']
            chosen.append({'code': target['code'], 'system': 'ICD-10-GM ' + target['version']})
        while len(chosen)<2:chosen.append({'code': '', 'system': ''})
        label = r.get('code',{}).get('text') or coding[0].get('display','')
        def status(field, system, mapping):
            cc=r.get(field,{})
            if not cc:return ''
            cs=cc.get('coding',[])
            if len(cs)!=1 or cs[0].get('system')!=system or cs[0].get('code')not in mapping:
                raise ValueError('Unsupported '+field)
            return mapping[cs[0]['code']]
        verification = status('verificationStatus','http://terminology.hl7.org/CodeSystem/condition-ver-status',VERIFICATION)
        if 'verificationStatusChange' in decision:
            verification = VERIFICATION[decision['verificationStatusChange']['to']]
        rows['Diagnose'].append(record('Diagnose', {
            'Patient-ID': pid,
            'Fall-Nr': nr,
            'Bezeichner': label,
            'Code': chosen[0]['code'],
            'Codesystem': chosen[0]['system'],
            'Zusatzcode': chosen[1]['code'],
            'Zusatzcodesystem': chosen[1]['system'],
            'Version': chosen[0].get('version', ''),
            'Zusatzversion': chosen[1].get('version', ''),
            'Dokumentationszeitpunkt': r.get('recordedDate',''),
            'Beginn': r.get('onsetDateTime',''),
            'Ende': r.get('abatementDateTime',''),
            'Klinischer Status': status('clinicalStatus','http://terminology.hl7.org/CodeSystem/condition-clinical',CLINICAL),
            'Verifikationsstatus': verification,
        }))
        fields(r, {'subject','encounter','code','recordedDate','onsetDateTime','abatementDateTime','clinicalStatus','verificationStatus'})
        for key in r.get('code',{}).keys() - {'coding','text'}:loss(r,'code.'+key,'Generator unterstützt Sachverhalt noch nicht')
        for field in ['clinicalStatus','verificationStatus']:
            for key in r.get(field,{}).keys() - {'coding'}:loss(r,field+'.'+key,'Generator unterstützt Sachverhalt noch nicht')
            for i,c in enumerate(r.get(field,{}).get('coding',[])):
                for key in c.keys() - {'system','code'}:loss(r,f'{field}.coding[{i}].'+key,'Generator unterstützt Sachverhalt noch nicht')
        for field in ['subject','encounter']:
            for key in r.get(field,{}).keys() - {'reference'}:loss(r,field+'.'+key,'Generator unterstützt Sachverhalt noch nicht')
    clinical_rows, clinical_report = prepare_clinical(entries, pid, encounter_numbers, year, diagnostics=diagnostics)
    rows.update(clinical_rows)
    event_rows, event_report = prepare_events(entries, pid, encounter_numbers,
        {r['sourceId'] for r in clinical_report['clinicalImports'] if r['resourceType']=='Observation'}, year, diagnostics=diagnostics)
    rows.update(event_rows)
    clinical_report['clinicalImports'].extend(event_report['clinicalImports'])
    clinical_report.update({k: v for k, v in event_report.items() if k not in ('clinicalImports', 'losses')})
    document_rows, document_report = prepare_documents(entries, pid, encounter_numbers, diagnostics=diagnostics)
    rows['DocumentReference'] = document_rows
    clinical_report['clinicalImports'].extend(document_report['clinicalImports'])
    clinical_report['documentIdentityChanges'] = document_report['documentIdentityChanges']
    clinical_report['documentMappings'] = document_report['documentMappings']
    losses.extend(document_report['losses'])
    losses.extend(event_report['losses'])
    losses.extend(clinical_report.pop('losses'))
    translation_report = localize_rows(rows, demographics['address'])
    finalize_documents(rows['DocumentReference'], clinical_report)
    from observation_mapping import decision as observation_decision, metadata as observation_metadata
    source_observations = {e['resource']['id']: e['resource'] for e in entries if e.get('resource', {}).get('resourceType') == 'Observation'}
    observation_mappings = []
    for sheet in ['Laborbefund', 'Klinische Dokumentation']:
        for row in rows[sheet]:
            # These source concepts are main observations, not components.
            if not row['Untersuchung ID'] or row['Komponente von']: continue
            mapped = observation_decision(source_observations[row['Untersuchung ID']])
            if mapped:
                code_field = 'LOINC' if sheet == 'Laborbefund' else 'Untersuchungscode'
                row['Zusatzcode'], row['Zusatzcodesystem'] = row[code_field], row['Codesystem']
                row[code_field], row['Codesystem'] = mapped['targetCodings'][0]['code'], 'LOINC'
                observation_mappings.append(mapped)
    clinical_report['observationMapping'] = observation_metadata()
    clinical_report['observationMappings'] = observation_mappings
    for decision in clinical_report['clinicalMappings']:
        for projected in decision.get('outputs', []):
            rows['Prozedur'][projected['row'] - 2]['Prozedurencode'] = projected['codings'][0]['code']
    for row, decision in zip(rows['Diagnose'], (d for d in diagnosis_mappings if d['status'] != 'excluded')):
        if row['Zusatzcodesystem'].startswith('ICD-10-GM '):
            row['Code'], row['Codesystem'], row['Zusatzcode'], row['Zusatzcodesystem'] = (
                row['Zusatzcode'], row['Zusatzcodesystem'], row['Code'], row['Codesystem'])
        if decision.get('target'):
            row['Bezeichner'] = decision['target']['display']
    version_rows(rows, year, version_mode)
    if not diagnostics:
        return rows, {'sourcePatient': pid}
    return rows, {'terminology': metadata(year, version_mode), **clinical_report, 'germanTexts':translation_report, 'demographics':demographics, 'movements': movement_report, 'sourcePatient':pid,'encounterNumbers':encounter_numbers,'sourceConditions':source_conditions,
                  'importedConditions':len(rows['Diagnose']),'conditionRows':condition_rows,'losses':losses,
                  'encounterMappings':encounter_mappings,
                  'diagnosisMapping': mapping_metadata(), 'diagnosisMappings': diagnosis_mappings,
                  'limitations':['Technical diagnosis case, not a complete clinical story',
                                 'Existing Patient converter inserts DAR for missing address',
                                 'Encounter status is derived from period; source identifiers are regenerated',
                                 'Additional ICD-10-GM mappings are provisional approximations for synthetic test data',
                                 'Target release ' + year + ' is explicit and independent of historical event dates',
                                 'No KDS conformance or complete SNOMED terminology validation performed']}


def column_name(index):
    result = ''
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def column_number(name):
    value = 0
    for char in name: value = value * 26 + ord(char) - 64
    return value


def write_workbook(rows, output, options=None, *, session=None):
    if any(row['Präparatcodesystem'] == 'PZN' for row in rows.get('Medikation', [])):
        require_external_path(output)
    template = ROOT/'FHIR_Testdatengenerator_Vorlage.xlsx'
    sheets = read_sheets(template)
    apply_workbook_edits(template, workbook_operations(rows, sheets), output, session=session)


def workbook_operations(rows, sheets):
    """Encode one edit at a time without retaining a second copy of all rows."""
    def op(*args):
        return '\t'.join(map(str, args))
    for name, cells in sheets.items():
        if name in ['Codes','Konvertierungsoptionen']:continue
        last=max([int(''.join(filter(str.isdigit,k)))for k in cells]+[2])
        hint = next((cell[:-1] for cell,value in cells.items()
                     if cell.endswith('1') and cell[:-1].isalpha() and value == 'Erklärung/Ausfüllhilfe'), None)
        if hint is None:
            raise ValueError('Missing template help boundary on sheet ' + name)
        data_end = column_name(column_number(hint) - 1)
        yield op('clear',name,f'A2:{data_end}{last}')
    for name, values in rows.items():
        headers = [v for cell, v in sorted(sheets[name].items(), key=lambda item: (len(item[0]), item[0]))
                   if cell.endswith('1') and cell[:-1].isalpha() and v != 'Erklärung/Ausfüllhilfe']
        if len(values) > 1030:
            # Extend the existing input-row formatting, never rebuild the sheet.
            for i in range(1032, len(values) + 2):
                yield op('copy', name, f'A2:{column_name(len(headers))}2', 'A' + str(i))
        from workbook_absent import display
        for i, row in enumerate(values, 2):
            shown = [display(name, header, value, row)
                     for header, value in zip(headers, ordered_values(name, row, headers))]
            yield op('row', name, 'A'+str(i), *(base64.b64encode(str(value).encode()).decode() for value in shown))
        # Imported codes, IDs and FHIR dates are text, never floating point values.
        if values:yield op('text',name,f'A2:{column_name(len(headers))}{len(values)+1}')
    from clinical_selections import validation_ops
    yield from validation_ops(sheets, {name: len(values) for name, values in rows.items()})



class WorkbookSession:
    """One isolated Office process and compiled helper for a sequential import."""

    def __init__(self):
        self.process = None
        self.temp = None
        self.compiled = False

    def __enter__(self):
        if self.temp is not None:
            raise RuntimeError('WorkbookSession is already open')
        self.compiled = False
        self.temp = tempfile.TemporaryDirectory(prefix='synthea-excel-')
        self.directory = Path(self.temp.name)
        return self

    def __exit__(self, *_):
        try:
            self.stop()
        finally:
            self.temp.cleanup()
            self.temp = None

    def stop(self):
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            finally:
                self.process = None

    def start(self):
        if self.temp is None:
            raise RuntimeError('WorkbookSession must be used as a context manager')
        if self.process is not None and self.process.poll() is None:
            return
        self.stop()
        office = Path('/Applications/LibreOffice.app/Contents')
        if office.exists():
            soffice = office / 'MacOS/soffice'
            jars = office / 'Resources/java/*'
        else:
            executable = shutil.which('libreoffice') or shutil.which('soffice')
            if not executable:
                raise RuntimeError('LibreOffice is required')
            soffice = Path(executable)
            jars = Path('/usr/share/java/*')
        classes = self.directory / 'classes'
        if not self.compiled:
            classes.mkdir(exist_ok=True)
            subprocess.run(['javac', '-cp', str(jars), '-d', str(classes),
                            str(ROOT / 'scripts/WorkbookUno.java')], check=True, timeout=60)
            self.compiled = True
        self.classpath = os.pathsep.join([str(classes), str(jars)])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.process = subprocess.Popen([
            str(soffice), '-env:UserInstallation=' + (self.directory / 'profile').as_uri(),
            '--headless', '--norestore', '--nodefault',
            f'--accept=socket,host=localhost,port={self.port};urp;StarOffice.ComponentContext'],
            # Parallel XLSX import can split validation rules nondeterministically,
            # including their relative formula anchors. Keep UNO workbook edits serial.
            # Bound native allocator retention across documents as well.
            env=dict(os.environ, MAX_CONCURRENCY='1', MALLOC_ARENA_MAX='2'),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for attempt in range(100):
                try:
                    with socket.create_connection(('localhost', self.port), timeout=.2):
                        return
                except OSError:
                    if self.process.poll() is not None:
                        raise RuntimeError('LibreOffice failed to start')
                    time.sleep(.1)
            raise RuntimeError('LibreOffice startup timed out')
        except BaseException:
            self.stop()
            raise

    def apply(self, template, ops, output, preview=None):
        self.start()
        with tempfile.TemporaryDirectory(prefix='workbook-', dir=self.directory) as temp:
            temp = Path(temp)
            commands = temp / 'edits.tsv'
            with commands.open('w', encoding='utf-8') as stream:
                # Calc can fragment imported validations and their relative anchors.
                # Reassign the existing UNO rule before applying any requested edits.
                for sheet, ranges in validation_ranges(template):
                    stream.write('reuseValidation\t' + sheet + '\t' + ranges + '\n')
                for operation in ops:
                    stream.write(operation)
                    stream.write('\n')
            candidate = temp / 'case.xlsx'
            shutil.copy2(template, candidate)
            try:
                subprocess.run(['java', '-cp', self.classpath, 'WorkbookUno',
                                str(candidate), str(commands)] + (preview or []),
                               # Full lifetime cohorts can contain tens of thousands of rows.
                               check=True, timeout=600,
                               env=dict(os.environ, SYNTHIFHIR_UNO_PORT=str(self.port)))
            except BaseException:
                # A failed/timed-out helper may have left an open document in Office.
                self.stop()
                raise
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise FileExistsError(output)
            shutil.copy2(candidate, output)


def apply_workbook_edits(template, ops, output, preview=None, *, session=None):
    if session is not None:
        session.apply(template, ops, output, preview)
    else:
        with WorkbookSession() as standalone:
            standalone.apply(template, ops, output, preview)


def main():
    if len(sys.argv)!=3:raise SystemExit('Usage: synthea_to_excel.py INPUT.json OUTPUT.xlsx')
    source=Path(sys.argv[1]);output=Path(sys.argv[2]);report=output.with_suffix('.loss.json')
    if output.exists() or report.exists():raise FileExistsError('Output/report already exists')
    data=source.read_bytes();rows,loss=prepare(json.loads(data))
    require_external_output(loss, output)
    require_external_output(loss, report)
    loss['sourceSha256']=hashlib.sha256(data).hexdigest()
    write_workbook(rows,output)
    report.write_text(json.dumps(loss,ensure_ascii=False,indent=2)+'\n')
    print(f'{output}: {len(rows["Diagnose"])} diagnoses; report: {report}')

if __name__=='__main__':main()
