import base64
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synthea_to_excel import prepare, workbook_operations, ROOT
from document_mapping import document_blocks, NLP_EXTENSION, NLP_SYSTEM, catalogue, text_hash
from direct_csv import write_direct_csv
from workbook_xml import read_sheets
from test_synthea_import import bundle

LOINC = 'http://loinc.org'
KNOWN = [{'system': LOINC, 'code': '34117-2', 'display': 'History and physical note'},
         {'system': LOINC, 'code': '51847-2', 'display': 'Evaluation + Plan note'}]


def add_document(source, identifier='d', text='Unveränderter deutscher Text.', codings=None, statuses=()):
    resource = {'resourceType': 'DocumentReference', 'id': identifier, 'status': 'current',
                'subject': {'reference': 'urn:uuid:p'}, 'context': {'encounter': [{'reference': 'urn:uuid:e'}]},
                'date': '2026-09-01T08:00:00+02:00', 'type': {'coding': copy.deepcopy(KNOWN if codings is None else codings)},
                'content': [{'attachment': {'contentType': 'text/plain; charset=utf-8',
                                           'data': base64.b64encode(text.encode()).decode()}}]}
    if statuses:
        resource['extension'] = [{'url': NLP_EXTENSION, 'valueCodeableConcept': {'coding': [
            {'system': NLP_SYSTEM, 'code': code} for code in statuses]}}]
    source['entry'].append({'fullUrl': 'urn:uuid:' + identifier, 'resource': resource})
    return resource


class DocumentMappingTest(unittest.TestCase):
    def projection(self, source):
        rows, report = prepare(source)
        return rows, report, list(document_blocks(rows['DocumentReference']))

    def test_known_note_preserves_source_codings_and_adds_german_classification(self):
        source = bundle()
        add_document(source)
        before = copy.deepcopy(source)
        rows, report, blocks = self.projection(source)
        block = blocks[0]
        self.assertEqual(source, before)
        self.assertEqual([r['Dokumentcode'] for r in block], ['34117-2', '51847-2', 'AD010110', 'BERI'])
        self.assertEqual(block[2]['Dokumentbezeichner'], 'Ärztlicher Verlaufsbericht')
        self.assertEqual(block[3]['Dokumentbezeichner'], 'Arztberichte')
        self.assertEqual(block[0]['NLPStatus'], 'unprocessed')
        self.assertEqual(block[0]['Patient-ID'], 'p')
        self.assertEqual(block[0]['Fall-Nr'], '1')
        self.assertEqual(block[0]['Embed'], 'ja')
        for row in block[1:]:
            for key in ['Dokument-ID', 'Patient-ID', 'Fall-Nr', 'Dokumenttext', 'Embed', 'Status', 'Ausgabezeitpunkt']:
                self.assertEqual('', row[key], key)
        decision = report['documentMappings'][0]
        self.assertEqual(decision['sourceTypeCodings'], KNOWN)
        self.assertFalse(decision['contentChanged'])
        self.assertTrue(decision['typeRules'][0]['approximation'])
        self.assertEqual(report['documentMapping']['sha256'], catalogue()[1])
        self.assertEqual((rows, report), prepare(source))

    def test_translation_and_actual_identity_replacements_determine_statuses(self):
        source = bundle()
        add_document(source, 'translated', '# Chief Complaint\nNo complaints.\n')
        add_document(source, 'names', 'Test Example')
        add_document(source, 'unchanged')
        _, report, blocks = self.projection(source)
        statuses = [[r['NLPStatus'] for r in b if r['NLPStatus']] for b in blocks]
        self.assertEqual(statuses, [['preprocessed', 'content-change'],
                                   ['preprocessed', 'content-change', 'surrogated'], ['unprocessed']])
        self.assertTrue(report['documentMappings'][0]['translationChanged'])
        self.assertEqual(report['documentMappings'][1]['identityReplacements'], 1)
        for block, decision in zip(blocks, report['documentMappings']):
            self.assertEqual(text_hash(block[0]['Dokumenttext']), decision['targetTextSha256'])
            self.assertNotIn('format-change', decision['outputNlpStatuses'])
            self.assertNotIn('deid', decision['outputNlpStatuses'])

    def test_equal_name_replacement_does_not_mark_surrogation(self):
        source = bundle()
        add_document(source, text='Test Example')
        with patch('german_demographics.identity', return_value={'name': {'given': ['Test'], 'family': 'Example'}}):
            _, report, blocks = self.projection(source)
        self.assertEqual(report['documentMappings'][0]['identityReplacements'], 0)
        self.assertEqual(blocks[0][0]['NLPStatus'], 'unprocessed')

    def test_existing_statuses_are_preserved_and_original_marker_removed_only_on_change(self):
        source = bundle()
        add_document(source, 'changed', '# Chief Complaint\n', statuses=['unprocessed', 'annotated', 'semantic', 'custom'])
        add_document(source, 'unchanged', statuses=['annotated', 'semantic', 'custom'])
        _, report, blocks = self.projection(source)
        self.assertEqual(report['documentMappings'][0]['outputNlpStatuses'],
                         ['annotated', 'semantic', 'custom', 'preprocessed', 'content-change'])
        self.assertEqual(report['documentMappings'][0]['removedNlpStatuses'], ['unprocessed'])
        self.assertEqual(report['documentMappings'][1]['outputNlpStatuses'], ['annotated', 'semantic', 'custom'])
        self.assertEqual(len(blocks[0]), 5)  # Status and coding lists have independent lengths.

    def test_unknown_or_partial_types_are_retained_without_approximation(self):
        source = bundle()
        add_document(source, 'unknown', codings=[{'system': 'urn:custom', 'code': '001', 'display': 'Freitext', 'version': 'v1'}])
        add_document(source, 'partial', codings=KNOWN[:1])
        _, report, blocks = self.projection(source)
        self.assertEqual([len(b) for b in blocks], [1, 1])
        self.assertEqual(blocks[0][0]['Dokumentcodesystem'], 'urn:custom')
        self.assertEqual(blocks[0][0]['Dokumentcode'], '001')
        self.assertEqual(report['documentMappings'][0]['sourceTypeCodings'][0]['version'], 'v1')
        self.assertTrue(any(loss['path'] == 'type.coding[0].version' for loss in report['losses']))
        self.assertTrue(all(not d['typeRules'] for d in report['documentMappings']))

    def test_existing_german_coding_is_not_added_twice_and_type_text_is_retained(self):
        source = bundle()
        resource = add_document(source, codings=KNOWN + catalogue()[0]['rules'][0]['add'])
        resource['type']['text'] = 'Eigene Beschreibung'
        _, _, blocks = self.projection(source)
        self.assertEqual([r['Dokumentcode'] for r in blocks[0]], ['34117-2', '51847-2', 'AD010110', 'BERI', ''])
        self.assertEqual(blocks[0][-1]['Dokumentbezeichner'], 'Eigene Beschreibung')

    def test_document_order_does_not_change_mapping_or_status_decisions(self):
        source = bundle()
        add_document(source, 'one', 'Test Example')
        add_document(source, 'two', '# Chief Complaint\n')
        _, report, blocks = self.projection(source)
        source['entry'][-2:] = reversed(source['entry'][-2:])
        _, reversed_report, reversed_blocks = self.projection(source)
        self.assertEqual({b[0]['Dokument-ID']: b for b in blocks},
                         {b[0]['Dokument-ID']: b for b in reversed_blocks})
        self.assertEqual({d['sourceId']: d for d in report['documentMappings']},
                         {d['sourceId']: d for d in reversed_report['documentMappings']})

    def test_csv_and_excel_operations_preserve_identical_document_blocks(self):
        source = bundle()
        add_document(source, 'one', 'Test Example\n# Chief Complaint\n')
        add_document(source, 'two', codings=[{'system': 'urn:custom', 'code': '002'}])
        rows, _, _ = self.projection(source)
        documents = rows['DocumentReference']
        with tempfile.TemporaryDirectory() as directory:
            write_direct_csv({'DocumentReference': documents}, Path(directory) / 'csv', 'case')
            with (Path(directory) / 'csv/case_DocumentReference.csv').open(newline='') as stream:
                csv_rows = list(csv.DictReader(stream))[:len(documents)]
        for wanted, actual in zip(documents, csv_rows):
            self.assertEqual(wanted, {key: actual[key] for key in wanted})
        operations = list(workbook_operations({'DocumentReference': documents}, read_sheets(ROOT / 'FHIR_Testdatengenerator_Vorlage.xlsx')))
        writes = [op.split('\t')[3:] for op in operations if op.startswith('row\tDocumentReference\t')]
        self.assertEqual([[base64.b64decode(value).decode() for value in row] for row in writes],
                         [list(row.values()) for row in documents])


if __name__ == '__main__':
    unittest.main()
