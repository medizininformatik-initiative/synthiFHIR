#!/usr/bin/env python3
"""Apply field-specific central dropdown lists to an existing clinical workbook.

Usage: clinical_selections.py INPUT.xlsx OUTPUT.xlsx
The output must be new; clinical values and input-sheet formatting are preserved.
"""
import base64
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from synthea_to_excel import apply_workbook_edits, column_number
from workbook_xml import read_sheets, NS
from workbook_absent import LABELS, FIELDS, display

SNOMED = 'SNOMED CT (Version nicht angegeben)'

# The lists describe supported input choices, not exhaustive terminology bindings.
# Evidence and the distinction between measurement and answer: docs/clinical-selections.md.
LISTS = {
    'AB': ['Prozeduren', SNOMED, 'OPS'],
    'AD': ['Labor', 'laboratory'],
    'AR': ['Klinische Messwertkategorien', 'vital-signs', 'survey', 'social-history', 'exam',
           'imaging', 'procedure', 'therapy', 'activity'],
    'AS': ['Labor: Untersuchungscode', 'LOINC'],
    'AT': ['Klinische Untersuchungscodes', 'LOINC', SNOMED],
    'AU': ['Codierte Messwertantworten', 'LOINC', SNOMED],
    'AV': ['Medikamentencodes', 'PZN', SNOMED, 'RxNorm', 'CVX'],
    'BP': ['Einheitencodesystem', 'http://unitsofmeasure.org'],
    'BR': ['NLP-Verarbeitungsstatus', 'unprocessed', 'preprocessed', 'format-change', 'content-change',
           'annotated', 'preanno', 'deid', 'semantic', 'surrogated'],
    'BS': ['Dokumenttyp-Codes', 'AD010110', 'BERI'],
    'BT': ['Dokumenttyp-Systeme', 'http://dvmd.de/fhir/CodeSystem/kdl',
           'http://ihe-d.de/CodeSystems/IHEXDStypeCode', 'LOINC', SNOMED],
    'AW': ['Wirkstoffcodes', 'ASK', 'UNII', SNOMED, 'RxNorm'],
    'AY': ['Impfstoffcodes', 'ATC', SNOMED, 'RxNorm', 'CVX'],
    'AZ': ['Befund- und Dokumenttypen', 'LOINC', SNOMED],
    'BA': ['Behandlungsplancodes', 'http://snomed.info/sct'],
    'BB': ['Impfstatus', 'completed', 'entered-in-error', 'not-done'],
    'BC': ['Befundstatus', 'registered', 'partial', 'preliminary', 'final', 'amended',
           'corrected', 'appended', 'cancelled', 'entered-in-error', 'unknown'],
    'BG': ['Labor-Werttypen', 'Zahl', 'Text', 'Code', 'Komponenten', 'Fehlend'],
    'BD': ['Behandlungsplan: Absicht', 'proposal', 'plan', 'order', 'option'],
}

def ingredient_choices():
    """Complete selectable ingredient combinations from the pinned offline map."""
    entries = json.loads((Path(__file__).parent / 'mappings/synthea-medications-de-2026.json').read_text())['entries']
    return sorted({'; '.join(i['code'] for i in entry['ingredients']) for entry in entries})


LISTS['BH'] = ['Wirkstoffcodes (UNII; Kombinationen mit Semikolon)'] + ingredient_choices()

# Header-based addressing works for both templates and filled cases.
SELECTIONS = {
    'Laborbefund': {'Werttyp': 'BG', 'Codesystem': 'AS', 'Zusatzcodesystem': 'AT', 'Wertcodesystem': 'AU', 'Kategorie': 'AD', 'Einheitencodesystem': 'BP'},
    'Klinische Dokumentation': {'Codesystem': 'AT', 'Zusatzcodesystem': 'AT', 'Wertcodesystem': 'AU', 'Kategorie': 'AR', 'Einheitencodesystem': 'BP'},
    'Prozedur': {'Codesystem': 'AB', 'Zusatzcodesystem': 'AB'},
    'Medikation': {'Präparatcodesystem': 'AV', 'Wirkstoffcodesystem': 'AW', 'Wirkstoffcode': 'BH', 'Dosiereinheitcodesystem': 'BP'},
    'Impfung': {'Codesystem': 'AY', 'Status': 'BB'},
    'Befundbericht': {'Codesystem': 'AZ', 'Status': 'BC'},
    'DocumentReference': {'Dokumentcodesystem': 'BT', 'Dokumentcode': 'BS', 'NLPStatus': 'BR'},
    'Behandlungsplan': {'Kategoriecodesystem': 'BA', 'Aktivitätscodesystem': 'BA', 'Absicht': 'BD', 'Aktivitätsstatus': 'BQ'},
}


# Context-specific input aids, not claims that DAR overrides profile invariants.
COMMON = [LABELS[k] for k in ('unknown', 'asked-unknown', 'temp-unknown',
                             'not-asked', 'asked-declined', 'masked')]
LISTS.update({
    'X': ['Diagnose: klinischer Status', 'Aktiv', 'Rezidiv', 'Rückfall', 'Inaktiv', 'Remission', 'Abgeklungen'] + COMMON,
    'Y': ['Diagnose: Verifikationsstatus', 'Unbestätigt', 'Vorläufig', 'Differentialdiagnose', 'Bestätigt', 'Widerlegt', 'Irrtümlich erfasst'] + COMMON,
    'Z': ['Fehlende Angabe: Code'] + COMMON + [LABELS['unsupported'], LABELS['not-permitted']],
    'AA': ['Aufnahmegrund (4. Stelle)', 'Normalfall', 'Arbeitsunfall/Berufskrankheit',
           'Verkehrsunfall/Sportunfall/Sonstiger Unfall', 'Hinweis auf Einwirkung von äußerer Gewalt',
           'Kriegsbeschädigten-Leiden/BVG-Leiden', 'Notfall'] + COMMON,
    'BJ': ['Status Verabreichung', 'in-progress', 'not-done', 'on-hold', 'completed',
           'entered-in-error', 'stopped', 'unknown'],
    'BK': ['Fehlende Angabe: Zeitpunkt oder Dosis'] + COMMON,
    'BL': ['Fehlender Messwert'] + COMMON + [LABELS[k] for k in
           ('not-applicable', 'unsupported', 'error', 'not-a-number',
            'negative-infinity', 'positive-infinity', 'not-performed', 'not-permitted')],
})
LISTS['BQ'] = ['Aktivitätsstatus', 'not-started', 'scheduled', 'in-progress', 'on-hold', 'completed', 'cancelled', 'stopped', 'unknown', 'entered-in-error']
LISTS['BH'] += COMMON
LISTS['BM'] = ['Version', '2025', '2026'] + list(LABELS.values())
LISTS['BN'] = ['Synthea: Mappingjahr', '2025', '2026']
LISTS['BO'] = ['Synthea: Versionsausgabe', 'Jahr'] + list(LABELS.values())
LISTS['W'] = ['Diagnosecodesystem', SNOMED, 'ICD-10-GM']

SELECTIONS.update({
    'Person': {'Geschlecht': 'A4:A6', **{h: 'L4:L5' for h in
        ('PDAT Einwilligung', 'KKDAT retro Einwilligung', 'KKDAT Einwilligung', 'BIOMAT Einwilligung', 'BIOMAT Zusatz Einwilligung')}},
    'Fall': {'Einrichtungskontaktklasse': 'B4:B13', 'Fachabteilung': 'C4:C42',
             'Aufnahmegrund (4. Stelle)': 'AA', 'Kontaktart': 'BF30:BF34'},
    'Diagnose': {'Codesystem': 'W', 'Zusatzcodesystem': 'W',
                'Klinischer Status': 'X', 'Verifikationsstatus': 'Y', 'Typ': 'D4:D13'},
})
for sheet, fields in {
    'Prozedur': {'Status': 'AE30:AE37'},
    'Medikation': {'Medikationstyp': 'M4:M6', 'Darreichungsform': 'K4:K252', 'Absicht': 'AH30:AH37'},
    'Laborbefund': {'Status': 'AF30:AF37'},
    'Klinische Dokumentation': {'Werttyp': 'AC30:AC35', 'Status': 'AF30:AF37'},
    'DocumentReference': {'Embed': 'L4:L5', 'Status': 'AQ30:AQ32'},
    'Impfung': {'Primärquelle': 'AN30:AN31'},
    'Behandlungsplan': {'Planstatus': 'AP30:AP36'},
}.items():
    SELECTIONS[sheet].update(fields)

# Codes and dates remain freely enterable, including intentional invalid test
# codes. A dropdown of missing reasons must never restrict ordinary input.
OPEN_FIELDS = {}
for sheet, fields in FIELDS.items():
    OPEN_FIELDS[sheet] = {}
    for header in sorted(fields):
        if header not in SELECTIONS[sheet]:
            OPEN_FIELDS[sheet][header] = 'BK' if header in (
                'Dokumentationszeitpunkt', 'Beginn', 'Ende', 'Durchführungsbeginn',
                'Zeitstempel (Abnahme)', 'Zeitstempel', 'Zeitpunkt', 'Erstellungsdatum', 'Aktivitätsbeginn', 'Aktivitätsende', 'Einzeldosis', 'Geburtsdatum', 'Vorname', 'Nachname') else 'Z'
for sheet, fields in {'Diagnose': ['Version', 'Zusatzversion'], 'Prozedur': ['Version', 'Zusatzversion'],
                      'Impfung': ['Version'], 'Medikation': ['ATC-Version']}.items():
    for header in fields: OPEN_FIELDS[sheet][header] = 'BM'
OPEN_FIELDS['Laborbefund']['Messwert'] = 'BL'
OPEN_FIELDS['Klinische Dokumentation']['Wert'] = 'BL'


def validation_ops(sheets, row_counts=None):
    ops = []
    for sheet, selections in SELECTIONS.items():
        cells = sheets[sheet]
        headers = {v: k[:-1] for k, v in cells.items() if k[:-1].isalpha() and k.endswith('1')}
        last = max(1031, (row_counts or {}).get(sheet, 0) + 1,
                   max(int(k.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')) for k in cells))
        # Remove stale copied validation only; cell contents and styles are untouched.
        ops.append(f'noValidation\t{sheet}\tA1:{headers["Erklärung/Ausfüllhilfe"]}1048576')
        for header, source in {**OPEN_FIELDS.get(sheet, {}), **selections}.items():
            col = headers[header]
            if ':' in source:
                first, end = source.split(':')
                ref = '$Codes.' + ':'.join(re.sub(r'([A-Z]+)([0-9]+)', r'$\1$\2', cell) for cell in (first, end))
            else:
                ref = f'$Codes.${source}$30:${source}${28 + len(LISTS[source])}'
            strict = sheet not in ('Behandlungsplan', 'DocumentReference') and header not in OPEN_FIELDS.get(sheet, {}) and header not in ('Wirkstoffcode', 'Darreichungsform') and not header.lower().endswith('codesystem')
            ops.append(f'validation\t{sheet}\t{col}2:{col}{last}\t{ref}\t{str(strict).lower()}')
        if sheet == 'Medikation':
            kind_col = headers['Medikationstyp']
            status_col = headers['Status']
            formula = f'IF(${kind_col}2="Verordnung";$Codes.$AG$30:$AG$37;IF(${kind_col}2="Verabreichung";$Codes.$BJ$30:$BJ$36;$Codes.$BI$30:$BI$37))'
            ops.append(f'validation\t{sheet}\t{status_col}2:{status_col}{last}\t{formula}\ttrue')
    return ops


def selection_ops(sheets):
    ops = []

    def op(*args):
        ops.append('\t'.join(map(str, args)))

    def put(sheet, cell, value):
        op('set', sheet, cell, base64.b64encode(value.encode()).decode())

    for col, values in LISTS.items():
        if col + '29' not in sheets['Codes']:
            op('copy', 'Codes', 'AA29:AA60', col + '29')
            op('width', 'Codes', column_number(col) - 1, 9000)
        if col == 'BH':
            for row in range(61, 29 + len(values)):
                if col + str(row) not in sheets['Codes']:
                    op('copy', 'Codes', 'AA30:AA30', col + str(row))
        for row in range(29, max(61, 29 + len(values))):
            put('Codes', col + str(row), values[row - 29] if row - 29 < len(values) else '')

    ops.extend(validation_ops(sheets))
    for sheet, cells in sheets.items():
        hint = next((k[:-1] for k, v in cells.items() if v == 'Erklärung/Ausfüllhilfe'), None)
        if hint is None:
            continue
        for cell, value in cells.items():
            if cell.rstrip('0123456789') == hint:
                revised = value.replace('!dar:unknown', 'Unbekannt').replace('!dar:<Grund>', 'eine Fehlgrund-Auswahl')
                revised = revised.replace('DAR ausdrücklich wählen', 'einen Fehlgrund ausdrücklich wählen').replace('. eine Fehlgrund', '. Eine Fehlgrund')
                if revised != value:
                    put(sheet, cell, revised)
        notes = []
        if sheet in ('Laborbefund', 'Klinische Dokumentation'):
            notes = ['Fehlender Messwert: Werttyp Fehlend wählen, dann den Grund in der Wertzelle auswählen.',
                     'Bei Werttyp Text bleibt der Zellinhalt Text; eine leere Zelle ist kein Fehlgrund.']
        if sheet in ('Prozedur', 'Medikation', 'Diagnose', 'Fall'):
            notes = ['Fehlgründe stehen verständlich in der Auswahl; alte Eingaben mit !dar: bleiben lesbar.']
        last_hint = max(int(k[len(hint):]) for k in cells if k.rstrip('0123456789') == hint)
        for note in notes:
            if note not in cells.values():
                last_hint += 1
                op('copy', sheet, hint + '2:' + hint + '2', hint + str(last_hint))
                put(sheet, hint + str(last_hint), note)
    # Translate only explicit missing input values, never free text.
    for sheet, cells in sheets.items():
        if sheet in ('Codes', 'Konvertierungsoptionen'):
            continue
        headers = {k[:-1]: v for k, v in cells.items() if k[:-1].isalpha() and k.endswith('1')}
        for cell, value in cells.items():
            col = cell.rstrip('0123456789')
            number = cell[len(col):]
            if number == '1' or col not in headers:
                continue
            row = {header: cells.get(c + number, '') for c, header in headers.items()}
            shown = display(sheet, headers[col], value, row)
            if shown != value:
                put(sheet, cell, shown)
    return ops


def update(source, target):
    ops = selection_ops(read_sheets(source))
    # LibreOffice may recalculate automatic row heights while saving. Preserve
    # their displayed heights as well as the explicitly sized template rows.
    with ZipFile(source) as archive:
        rels = {r.attrib['Id']: r.attrib['Target'] for r in
                ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        for sheet in ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
            path = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
            path = path.lstrip('/') if path.startswith('/') else 'xl/' + path
            groups = []
            for row in ET.fromstring(archive.read(path)).findall('s:sheetData/s:row', NS):
                if 'ht' not in row.attrib:
                    continue
                number, height = int(row.attrib['r']), row.attrib['ht']
                if groups and groups[-1][1] + 1 == number and groups[-1][2] == height:
                    groups[-1][1] = number
                else:
                    groups.append([number, number, height])
            for first, last, height in groups:
                ops.append(f'rowHeight\t{sheet.attrib["name"]}\tA{first}:A{last}\t{height}')
    apply_workbook_edits(Path(source), ops, Path(target))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    update(*sys.argv[1:])
