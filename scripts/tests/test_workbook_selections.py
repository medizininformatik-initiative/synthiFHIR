"""Regression checks for the actual shipped input contract, not just list strings."""
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbook_xml import read_sheets, NS
from workbook_absent import display, canonical, CODES

ROOT = Path(__file__).resolve().parents[2]

class WorkbookSelectionsTest(unittest.TestCase):
    def test_document_status_and_type_lists_allow_negative_test_inputs(self):
        from clinical_selections import validation_ops, LISTS
        expected = {'unprocessed', 'preprocessed', 'format-change', 'content-change',
                    'annotated', 'preanno', 'deid', 'semantic', 'surrogated'}
        self.assertEqual(expected, set(LISTS['BR'][1:]))
        for filename in ('FHIR_Testdatengenerator_Vorlage.xlsx', 'input/FHIR_Testdatengenerator_Vorlage.xlsx',
                         'FHIR_Testdatengenerator_Interpolar_Demo.xlsx'):
            sheets = read_sheets(ROOT / filename)
            headers = {v: k[:-1] for k, v in sheets['DocumentReference'].items() if re.fullmatch('[A-Z]+1', k)}
            self.assertIn('Dokument-ID', headers)
            operations = validation_ops(sheets)
            for field, source in [('NLPStatus', 'BR'), ('Dokumentcode', 'BS'), ('Dokumentcodesystem', 'BT')]:
                matching = [op for op in operations if op.startswith(f'validation\tDocumentReference\t{headers[field]}2:')]
                self.assertEqual(1, len(matching))
                self.assertTrue(matching[0].endswith('\tfalse'))
                for row, code in enumerate(LISTS[source][1:], 30):
                    self.assertEqual(code, sheets['Codes'][f'{source}{row}'])

    def test_presentation_does_not_rewrite_narratives_or_boolean_results(self):
        self.assertEqual('Unbekannt (Data Absent Reason)', display('Medikation', 'Einzeldosis', '!dar:unknown', {}))
        self.assertEqual('!dar:unknown', display('Medikation', 'Dosierungstext', '!dar:unknown', {}))
        self.assertEqual('Unbekannt', canonical('Laborbefund', {'Werttyp': 'Text', 'Messwert': 'Unbekannt'})['Messwert'])
        self.assertEqual('!dar:unknown', canonical('Laborbefund', {'Werttyp': 'Fehlend', 'Messwert': 'Unbekannt'})['Messwert'])

    def test_names_require_explicit_dar_and_keep_ordinary_words(self):
        for field in ('Vorname', 'Nachname'):
            for value in ('Unbekannt', 'Maskiert', 'Müller', ''):
                self.assertEqual(value, display('Person', field, value, {}))
                self.assertEqual(value, canonical('Person', {field: value})[field])
            self.assertEqual('!dar:unknown', canonical('Person', {field: 'Unbekannt (Data Absent Reason)'})[field])
            self.assertEqual('Unbekannt (Data Absent Reason)', display('Person', field, '!dar:unknown', {}))
        self.assertEqual('!dar:unknown', canonical('Person', {'Geburtsdatum': 'Unbekannt (Data Absent Reason)'})['Geburtsdatum'])

    def test_person_name_and_birth_date_dropdowns_allow_free_input(self):
        from clinical_selections import validation_ops
        for filename in ('FHIR_Testdatengenerator_Vorlage.xlsx', 'input/FHIR_Testdatengenerator_Vorlage.xlsx',
                         'FHIR_Testdatengenerator_Interpolar_Demo.xlsx'):
            sheets = read_sheets(ROOT / filename)
            headers = {v: k[:-1] for k, v in sheets['Person'].items() if re.fullmatch('[A-Z]+1', k)}
            operations = validation_ops(sheets)
            with ZipFile(ROOT / filename) as z:
                rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
                sheet = next(s for s in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS) if s.attrib['name'] == 'Person')
                target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
                target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                validations = ET.fromstring(z.read(target)).findall('.//s:dataValidation', NS)
                for field in ('Vorname', 'Nachname', 'Geburtsdatum'):
                    col = headers[field]
                    rule = next(v for v in validations if v.get('sqref') == f'{col}2:{col}1031')
                    self.assertEqual('list', rule.get('type'))
                    self.assertEqual('false', rule.get('showErrorMessage'))
                    self.assertEqual('false', rule.get('showDropDown'))
                    self.assertEqual('Codes!$BK$30:$BK$35', rule.findtext('s:formula1', namespaces=NS))
                    self.assertIn(f'validation\tPerson\t{col}2:{col}1031\t$Codes.$BK$30:$BK$35\tfalse', operations)

    def test_shipped_dropdowns_target_only_input_columns_and_existing_values(self):
        for filename in ('FHIR_Testdatengenerator_Vorlage.xlsx', 'FHIR_Testdatengenerator_Interpolar_Demo.xlsx'):
            path = ROOT / filename
            cells = read_sheets(path)
            self.assertEqual(list(cells)[-1], "Codes")
            self.assertNotIn("Hilfsmittel", cells)
            self.assertIn("Auswahllisten", cells["Codes"]["A1"])
            for value in cells["Codes"].values():
                if value in CODES:
                    self.assertTrue(value.endswith(" (Data Absent Reason)"), value)
            with ZipFile(path) as z:
                rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
                for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS):
                    name = sheet.attrib['name']
                    if name in ('Codes', 'Konvertierungsoptionen'): continue
                    target = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
                    target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
                    headers = {k[:-1]: v for k,v in cells[name].items() if re.fullmatch('[A-Z]+1',k)}
                    for d in ET.fromstring(z.read(target)).findall('.//s:dataValidation', NS):
                        if d.get('type') != 'list': continue
                        formula = d.findtext('s:formula1',namespaces=NS)
                        for area in d.get('sqref').split():
                            col, row = re.match(r'([A-Z]+)(\d+)', area).groups()
                            self.assertGreater(int(row),1,(name,area))
                            self.assertNotEqual('Erklärung/Ausfüllhilfe',headers[col])
                            if name == 'Klinische Dokumentation':
                                self.assertNotEqual('Bezeichner',headers[col])
                            if headers[col] in ('Code','Zusatzcode','Einzeldosis','Wirkstoffcode','Wert','Messwert','Prozedurencode','Version','Zusatzversion','ATC-Version'):
                                self.assertNotIn(d.get('showErrorMessage'), ('1', 'true'), (name,col))
                        for source_col, first, endcol, last in re.findall(r'Codes!\$([A-Z]+)\$(\d+)(?::\$([A-Z]+)\$(\d+))?',formula):
                            self.assertTrue(all(cells['Codes'].get(source_col+str(n)) for n in range(int(first),int(last or first)+1)),formula)
                        if name == 'Medikation' and headers[col] == 'Status':
                            kind_col = next(c for c, h in headers.items() if h == 'Medikationstyp')
                            self.assertIn(f'${kind_col}2=', formula)
                            self.assertIn('$BJ$30:$BJ$36',formula)
                            self.assertNotIn('$BH$',formula)
                            self.assertRegex(d.get('sqref'), rf'^{col}2:{col}[0-9]+$')
                            self.assertGreaterEqual(int(d.get('sqref').split(':'+col)[1]), 1000)
            self.assertEqual('in-progress',cells['Codes']['BJ30'])
            self.assertEqual('Unbekannt (Data Absent Reason)',cells['Codes']['AA36'])
            self.assertNotIn('Positive Unendlichkeit',[cells['Codes'].get('AA'+str(i)) for i in range(30,42)])
