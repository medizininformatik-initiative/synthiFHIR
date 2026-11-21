"""Exercise real UNO row writes across batching and operation boundaries."""
import base64
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synthea_to_excel import ROOT, WorkbookSession, apply_workbook_edits, write_workbook
from workbook_xml import NS, read_sheets
from workbook_records import SCHEMAS, record


@unittest.skipUnless(shutil.which('java') and (
    Path('/Applications/LibreOffice.app/Contents/MacOS/soffice').exists()
    or shutil.which('libreoffice') or shutil.which('soffice')), 'Requires Java and LibreOffice')
class WorkbookUnoTest(unittest.TestCase):
    def test_unedited_relative_validation_keeps_its_original_range_and_formula(self):
        def relative_rules(path):
            found = []
            with ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                        root = ET.fromstring(archive.read(name))
                        for rule in root.findall('s:dataValidations/s:dataValidation', NS):
                            formula = rule.findtext('s:formula1', '', NS)
                            if '$C2' in formula:
                                found.append((name, rule.attrib['sqref'], formula))
            return sorted(found)

        template = ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx'
        expected = relative_rules(template)
        self.assertTrue(expected, 'Template must contain a row-relative dropdown')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'preserved.xlsx'
            apply_workbook_edits(template, ['set\tPerson\tA2\tdGVzdA=='], output)
            # Splitting sqref without translating the relative formula changes its anchor.
            self.assertEqual(expected, relative_rules(output))

    def test_session_reuses_office_and_compilation_without_leaking_workbook_edits(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('synthea_to_excel.subprocess.run', wraps=subprocess.run) as run:
            with WorkbookSession() as session:
                first = Path(directory) / 'first.xlsx'
                second = Path(directory) / 'second.xlsx'
                apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                     ['set\tPerson\tA2\tZmlyc3Q='], first, session=session)
                process = session.process
                temporary = session.directory
                apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                     ['set\tPerson\tB2\tc2Vjb25k'], second, session=session)
                self.assertIs(process, session.process)
                self.assertIsNone(process.poll())
            self.assertIsNotNone(process.poll())
            self.assertFalse(temporary.exists())
            self.assertEqual('first', read_sheets(first)['Person']['A2'])
            self.assertEqual('second', read_sheets(second)['Person']['B2'])
            self.assertEqual(read_sheets(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx')['Person'].get('A2'),
                             read_sheets(second)['Person'].get('A2'))
            self.assertEqual(1, sum(call.args[0][0] == 'javac' for call in run.call_args_list))

    def test_failed_edit_restarts_office_and_exception_closes_session(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('synthea_to_excel.subprocess.run', wraps=subprocess.run) as run:
            with self.assertRaisesRegex(RuntimeError, 'caller failed'):
                with WorkbookSession() as session:
                    session.start()
                    failed_process = session.process
                    temporary = session.directory
                    with self.assertRaises(subprocess.CalledProcessError):
                        apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                             ['clear\tMissingSheet\tA2'], Path(directory) / 'failed.xlsx',
                                             session=session)
                    self.assertIsNotNone(failed_process.poll())
                    self.assertFalse((Path(directory) / 'failed.xlsx').exists())
                    output = Path(directory) / 'recovered.xlsx'
                    apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                         ['set\tPerson\tA2\tcmVjb3ZlcmVk'], output, session=session)
                    process = session.process
                    self.assertNotEqual(failed_process.pid, process.pid)
                    self.assertEqual('recovered', read_sheets(output)['Person']['A2'])
                    raise RuntimeError('caller failed')
            self.assertIsNotNone(process.poll())
            self.assertFalse(temporary.exists())
            self.assertEqual(1, sum(call.args[0][0] == 'javac' for call in run.call_args_list))

    def test_named_records_reach_matching_workbook_columns(self):
        rows = {name: [dict(reversed(list(record(name, {field: 'value:' + field for field in fields}).items())))]
                for name, fields in SCHEMAS.items()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'named.xlsx'
            write_workbook(rows, output)
            sheets = read_sheets(output)
            for name, fields in SCHEMAS.items():
                headers = {value: cell[:-1] for cell, value in sheets[name].items()
                           if cell.endswith('1') and cell[:-1].isalpha()}
                for field in fields:
                    self.assertEqual('value:' + field, sheets[name].get(headers[field] + '2'), (name, field))

    def test_adjacent_copies_match_individual_copies_across_boundaries(self):
        def encoded(value):
            return base64.b64encode(value.encode()).decode()

        ops = ['clear\tDiagnose\tA2:AD10',
               'formula\tDiagnose\tA2\t' + encoded('=ROW()'),
               'set\tDiagnose\tB2\t' + encoded('source 2'),
               'formula\tDiagnose\tA3\t' + encoded('=ROW()'),
               'set\tDiagnose\tB3\t' + encoded('source 3')]
        ops += [f'copy\tDiagnose\tA2:B2\tAA{row}' for row in (2, 3, 5, 4)]
        ops += [f'copy\tDiagnose\tA3:B3\tAA{row}' for row in (6, 7)]
        ops += [f'copy\tDiagnose\tA2:B2\tAC{row}' for row in (8, 9)]
        ops += ['set\tPerson\tA2\t' + encoded('other sheet'),
                'copy\tPerson\tA2:A2\tC2', 'copy\tPerson\tA2:A2\tC3']
        with tempfile.TemporaryDirectory() as directory, WorkbookSession() as session:
            grouped = Path(directory) / 'grouped.xlsx'
            individual = Path(directory) / 'individual.xlsx'
            apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                 iter(ops), grouped, session=session)
            # Blank operations end each group without changing document contents.
            apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx',
                                 (line for op in ops for line in (op, '')),
                                 individual, session=session)
            sheets = read_sheets(grouped)
            for row in (2, 3, 4, 5, 6, 7):
                self.assertEqual(str(row), sheets['Diagnose']['AA' + str(row)])
                self.assertEqual('source 2' if row < 6 else 'source 3',
                                 sheets['Diagnose']['AB' + str(row)])
            self.assertEqual('other sheet', sheets['Person']['C3'])
            with ZipFile(grouped) as a, ZipFile(individual) as b:
                self.assertEqual(set(a.namelist()), set(b.namelist()))
                for name in a.namelist():
                    if name != 'docProps/core.xml':
                        left, right = a.read(name), b.read(name)
                        if name.startswith('xl/worksheets/') and name.endswith('.xml'):
                            # Calc also reorders untouched validation rules in the baseline.
                            def normalized(data):
                                root = ET.fromstring(data)
                                rules = root.find('s:dataValidations', NS)
                                if rules is not None:
                                    rules[:] = sorted(rules, key=ET.tostring)
                                return ET.tostring(root)
                            left, right = normalized(left), normalized(right)
                        self.assertEqual(left, right, name)

    def test_rows_preserve_text_styles_and_interleaved_operations(self):
        def encoded(value):
            return base64.b64encode(value.encode()).decode()

        def row(sheet, number, *values):
            return '\t'.join(['row', sheet, 'A' + str(number), *map(encoded, values)])

        ops = ['clear\tDiagnose\tA2:D1052']
        ops += [f'copy\tDiagnose\tA2:D2\tA{i}' for i in range(1032, 1053)]
        expected = {}
        for number in range(2, 1052):
            values = (f'{number:06}', 'Größe\tÄÖÜ\nß', '=1+1' if number % 2 else '')
            ops.append(row('Diagnose', number, *values))
            expected.update({f'{column}{number}': value for column, value in zip('ABC', values) if value})
        ops += ['', row('Diagnose', 1051, '001051', 'Größe\tÄÖÜ\nß', '=1+1')]
        # Width change, overwrite, gap, reverse order, and a sheet change must stop a block.
        ops += [row('Diagnose', 1052, 'last', 'short'),
                row('Diagnose', 1052, 'overwritten'),
                row('Diagnose', 1054, 'gap'), row('Diagnose', 1053, 'reverse'),
                row('Person', 2, 'other sheet'), row('Diagnose', 1054, 'back'),
                'set\tDiagnose\tA512\t' + encoded('interleaved'),
                row('Diagnose', 512, 'after set'),
                'clear\tDiagnose\tA513:A513', row('Diagnose', 513, 'after clear'),
                'formula\tDiagnose\tD2\t' + encoded('=1+1'),
                'text\tDiagnose\tA2:C1054']
        expected.update(A1052='overwritten', B1052='short', A1053='reverse', A1054='back',
                        A512='after set', A513='after clear', D2='2')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'test.xlsx'
            apply_workbook_edits(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx', iter(ops), output)
            sheets = read_sheets(output)
            for cell, value in expected.items():
                self.assertEqual(value, sheets['Diagnose'][cell], cell)
            self.assertEqual('other sheet', sheets['Person']['A2'])
            self.assertNotIn('C2', sheets['Diagnose'])
            with ZipFile(output) as archive:
                cells = {}
                for name in archive.namelist():
                    if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                        sheet = ET.fromstring(archive.read(name))
                        candidate = {c.attrib['r']: c for c in sheet.findall('.//s:sheetData/s:row/s:c', NS)}
                        if 'A1054' in candidate:
                            cells = candidate
                            break
                self.assertEqual(cells['A2'].attrib['s'], cells['A1051'].attrib['s'])
                self.assertEqual(cells['B2'].attrib['s'], cells['B1051'].attrib['s'])
                self.assertIsNone(cells['C3'].find('s:f', NS), 'Formula-like row values stay text')
                self.assertIsNotNone(cells['D2'].find('s:f', NS))


if __name__ == '__main__':
    unittest.main()
