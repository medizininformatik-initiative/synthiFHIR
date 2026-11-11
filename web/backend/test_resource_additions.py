from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import shutil
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from api import app
import resource_additions as additions
import store
from test_queue import QueueFixture


class AdditionTests(QueueFixture):
    def setUp(self):
        super().setUp()
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'input/FHIR_Testdatengenerator_Vorlage.xlsx',
                        store.APP / store.SOURCES['starter'])
        self.source = store.create('starter', 'default')
        store.finish(self.source, 'succeeded', 0)
        self.index = self.root / 'source.sqlite'
        with sqlite3.connect(self.index) as db:
            db.execute('CREATE TABLE resources (key TEXT PRIMARY KEY, kind TEXT, content TEXT)')
            db.execute('CREATE TABLE aliases (alias TEXT PRIMARY KEY, key TEXT)')
            resources = [
                {'resourceType': 'Patient', 'id': 'p'},
                {'resourceType': 'Encounter', 'id': 'e', 'subject': {'reference': 'urn:uuid:p'}},
                {'resourceType': 'Medication', 'id': 'm', 'code': {'text': 'Original medication'}},
                {'resourceType': 'DocumentReference', 'id': 'd', 'status': 'current',
                 'subject': {'reference': 'urn:uuid:p'}, 'context': {'encounter': [{'reference': 'urn:uuid:e'}]},
                 'type': {'coding': [{'system': 'urn:source', 'code': 'source'}]},
                 'extension': [{'url': 'urn:other', 'valueString': 'keep'}],
                 'content': [{'attachment': {'data': 'VGVzdA==', 'contentType': 'text/plain'}}]},
                {'resourceType': 'DocumentReference', 'id': 'no-case', 'subject': {'reference': 'urn:uuid:p'}},
                {'resourceType': 'MedicationRequest', 'id': 'rx', 'subject': {'reference': 'urn:uuid:p'},
                 'encounter': {'reference': 'urn:uuid:e'}, 'medicationReference': {'reference': 'urn:uuid:m'},
                 'dosageInstruction': [{'doseAndRate': [{'doseQuantity': {'value': Decimal('0.1234567890123456789')}}]}]},
                {'resourceType': 'Observation', 'id': 'a', 'subject': {'reference': 'urn:uuid:p'},
                 'encounter': {'reference': 'urn:uuid:e'}, 'hasMember': [{'reference': 'urn:uuid:b'}]},
                {'resourceType': 'Observation', 'id': 'b', 'subject': {'reference': 'urn:uuid:p'},
                 'encounter': {'reference': 'urn:uuid:e'}, 'valueQuantity': {'value': Decimal('0.1234567890123456789')}}]
            entries = [{'fullUrl': 'urn:uuid:' + r['id'], 'resource': r} for r in resources]
            additions.index_export(db, io.StringIO(additions.encode({'resourceType': 'Bundle', 'entry': entries})))
        self.fingerprint = 'a' * 64
        mocked = patch.object(additions, 'source_index', return_value=(self.index, self.fingerprint))
        mocked.start(); self.addCleanup(mocked.stop)

    def create(self, **values):
        return additions.create(self.source, additions.Settings(**values), self.fingerprint, str(uuid4()))

    def output(self, identifier):
        folder = store.ROOT / 'jobs' / identifier
        additions.execute(folder)
        return [json.loads(line, parse_float=Decimal) for line in (folder / 'output/run-additions/fhir/resources.ndjson').read_text().splitlines()]

    def test_document_addition_references_existing_patient_and_encounter(self):
        identifier = self.create(resourceType='DocumentReference', codingMode='replace', codings=[{'system': 'urn:new', 'code': 'new'}],
                                 nlpMode='replace', nlpCodes=['annotated', 'deid'])
        resources = self.output(identifier)
        self.assertEqual(1, len(resources))
        resource = resources[0]
        self.assertEqual('Patient/p', resource['subject']['reference'])
        self.assertEqual('Encounter/e', resource['context']['encounter'][0]['reference'])
        self.assertNotEqual('d', resource['id'])
        self.assertEqual('VGVzdA==', resource['content'][0]['attachment']['data'])
        self.assertEqual([{'system': 'urn:new', 'code': 'new'}], resource['type']['coding'])
        self.assertEqual('urn:other', resource['extension'][0]['url'])
        self.assertEqual(['annotated', 'deid'], [c['code'] for c in resource['extension'][1]['valueCodeableConcept']['coding']])
        self.assertEqual(resources, self.output(identifier))
        self.assertNotEqual(resource['id'], self.output(self.create(resourceType='DocumentReference'))[0]['id'])
        with sqlite3.connect(self.index) as db:
            original = json.loads(db.execute("SELECT content FROM resources WHERE key='DocumentReference/d'").fetchone()[0])
        self.assertEqual('source', original['type']['coding'][0]['code'])

    def test_patient_mode_omits_encounter_and_includes_patient_only_resources(self):
        resources = self.output(self.create(resourceType='DocumentReference', association='patient'))
        self.assertEqual(2, len(resources))
        self.assertTrue(all(not r.get('context', {}).get('encounter') for r in resources))

    def test_dependencies_and_precise_values_are_preserved(self):
        resource = self.output(self.create(resourceType='MedicationRequest', status='active'))[0]
        self.assertEqual('Medication/m', resource['medicationReference']['reference'])
        self.assertEqual(Decimal('0.1234567890123456789'), resource['dosageInstruction'][0]['doseAndRate'][0]['doseQuantity']['value'])
        resources = self.output(self.create(resourceType='Observation'))
        parent = next(r for r in resources if r.get('hasMember'))
        child = next(r for r in resources if r.get('valueQuantity'))
        self.assertEqual('Observation/' + child['id'], parent['hasMember'][0]['reference'])

    def test_preview_counts_and_submission_retry(self):
        catalogue = additions.catalogue(self.source)
        document = next(r for r in catalogue['types'] if r['resourceType'] == 'DocumentReference')
        self.assertEqual({'resources': 1, 'patients': 1, 'cases': 1, 'skipped': 1}, document['encounter'])
        client = TestClient(app)
        body = {'requestId': str(uuid4()), 'fingerprint': self.fingerprint, 'settings': {'resourceType': 'DocumentReference'}}
        first = client.post(f'/api/jobs/{self.source}/additions', json=body)
        self.assertEqual(201, first.status_code, first.text)
        second = client.post(f'/api/jobs/{self.source}/additions', json=body)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertFalse(first.json()['editor_available'])
        self.assertEqual(self.source, first.json()['addition']['sourceRun'])
        self.assertEqual({'patients': 1, 'cases': 1}, {k: first.json()['addition']['counts'][k] for k in ('patients', 'cases')})

    def test_rejects_invalid_types_stale_preview_and_cross_patient_encounter(self):
        for kind in ('Patient', 'Encounter', 'Organization', 'Medication'):
            with self.assertRaises(ValueError): self.create(resourceType=kind)
        with self.assertRaises(store.SubmissionConflict):
            additions.create(self.source, additions.Settings(resourceType='Observation'), 'b' * 64, str(uuid4()))
        with sqlite3.connect(self.index) as db:
            db.execute("UPDATE resources SET content=? WHERE key='Encounter/e'", (json.dumps({'resourceType':'Encounter','id':'e','subject':{'reference':'Patient/other'}}),))
        with self.assertRaises(ValueError): self.create(resourceType='DocumentReference')

    def test_conflicting_resource_versions_and_unresolved_urn_are_rejected(self):
        with sqlite3.connect(self.index) as db:
            with self.assertRaises(ValueError):
                additions.index_export(db, io.StringIO('{"resourceType":"Patient","id":"p","active":false}\n'))
            with self.assertRaises(ValueError):
                additions.normalize(db, {'subject': {'reference': 'urn:uuid:missing'}})

    def test_addition_cleanup_preserves_input_results_and_provenance(self):
        import datasets
        import run_cleanup
        identifier = self.create(resourceType='DocumentReference')
        self.output(identifier)
        folder = store.ROOT / 'jobs' / identifier
        with patch.object(datasets, 'inspect', return_value={'patients': 0, 'resourceCounts': {'DocumentReference': 1}}):
            datasets.build(identifier, json.loads((folder / 'snapshot.json').read_text()))
        store.finish(identifier, 'succeeded', 0)
        run_cleanup.cleanup(identifier)
        self.assertTrue((folder / 'addition.ndjson').is_file())
        self.assertTrue((folder / 'output/run-additions/details/reports/additions.json').is_file())
        with store.run_lock(identifier), patch.object(store, 'run_lock', side_effect=AssertionError('Nested lock')):
            _, item, archive = datasets.resolve_dataset(identifier + '.0', already_locked=True)
        self.assertEqual(item['sha256'], store.digest(archive))
