import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbook_records import record
from check_contact_selection import checked_contacts
from check_clinical_references import matching_contact
from check_medication_selection import check_medication_references
from check_observation_units import checked_observation_units
from check_output_selection import resource_key, selected, event_id, descriptive_reference
from check_synthea_roundtrip import check_configured
from converter_options import resolve_config
from synthea_movements import enrich

START, END = '2026-01-01T10:00:00+00:00', '2026-01-01T11:00:00+00:00'
LEVEL_SYSTEM = 'http://fhir.de/CodeSystem/Kontaktebene'


class OutputSelectionChecks(unittest.TestCase):
    def contacts(self):
        source = {'entry': [{'resource': {'resourceType': 'Encounter', 'id': 'stay',
                  'class': {'code': 'IMP'}, 'period': {'start': START, 'end': END}}}]}
        _, movement = enrich(source, [record('Fall', {'Patient-ID': 'p', 'Fall-Nr': '1', 'Einrichtungskontaktklasse': 'stationaer'})], {'stay': '1'})
        report = {'sourcePatient': 'p', 'encounterNumbers': {'stay': '1'}, 'movements': movement, 'converterOptions': {}}
        entries = []
        for identifier, level, parent in [('p-E-1', 'einrichtungskontakt', None),
                                          ('p-E-1-A-1', 'abteilungskontakt', 'p-E-1'),
                                          ('p-E-1-V-1', 'versorgungsstellenkontakt', 'p-E-1-A-1')]:
            r = {'resourceType': 'Encounter', 'id': identifier, 'class': {'code': 'IMP'},
                 'period': {'start': START, 'end': END}, 'type': [{'coding': [{'system': LEVEL_SYSTEM, 'code': level}]}]}
            if parent:
                r['partOf'] = {'reference': 'Encounter/' + parent}
            if level == 'versorgungsstellenkontakt':
                r['type'].append({'coding': [{'system': 'http://fhir.de/CodeSystem/kontaktart-de', 'code': 'normalstationaer'}]})
            entries.append({'resource': r})
        return source, {'entry': entries}, report

    def test_observation_groups_require_explicit_category_and_system(self):
        system = 'http://terminology.hl7.org/CodeSystem/observation-category'
        options = {'OBSERVATION_LABORATORY_ENABLED': 'false', 'OBSERVATION_VITAL_SIGNS_ENABLED': 'false',
                   'OBSERVATION_LABORATORY_UCUM_CODE_IN_UNIT': 'true', 'OBSERVATION_VITAL_SIGNS_UCUM_CODE_IN_UNIT': 'true'}
        for category in ([], [{'coding': [{'system': system, 'code': 'social-history'}]}],
                         [{'coding': [{'system': 'urn:custom', 'code': 'laboratory'}]}]):
            resource = {'resourceType': 'Observation', 'id': 'o', 'category': category,
                        'valueQuantity': {'value': 1, 'system': 'http://unitsofmeasure.org', 'code': 'mg', 'unit': 'entered'}}
            self.assertEqual('OBSERVATION_OTHER', resource_key(resource))
            self.assertTrue(selected(resource, options))
            self.assertFalse(selected(resource, {**options, 'OBSERVATION_OTHER_ENABLED': 'false'}))
            self.assertEqual([resource], checked_observation_units([resource], options, {'o': resource}))
            from check_dar import checked_original_dar
            target = {'entry': [{'resource': resource}]}
            with patch('check_dar.expected_fields', return_value={'o': resource}):
                self.assertEqual(target, checked_original_dar({}, target, {'converterOptions': {
                    'DAR_LABORATORY_VALUE_X_NUMERIC_MEASUREMENT': 'masked',
                    'DAR_VITAL_SIGNS_VALUE_X_NUMERIC_MEASUREMENT': 'masked'}}))
        for code, key in (('laboratory', 'OBSERVATION_LABORATORY'), ('vital-signs', 'OBSERVATION_VITAL_SIGNS')):
            resource = {'resourceType': 'Observation', 'category': [{'coding': [{'system': system, 'code': code}]}]}
            self.assertEqual(key, resource_key(resource))
            self.assertFalse(selected(resource, options))

    def test_contact_selection_and_parent_mutations(self):
        for options, omitted, parent in [({}, None, 'p-E-1-A-1'),
                ({'CONTACT_DEPARTMENT_ENABLED': 'false'}, 'p-E-1-A-1', None),
                ({'CONTACT_FACILITY_ENABLED': 'false'}, 'p-E-1', 'p-E-1-A-1'),
                ({'CONTACT_WARD_SERVICE_PART_OF': 'facility'}, None, 'p-E-1'),
                ({'CONTACT_WARD_SERVICE_PART_OF': 'none'}, None, None)]:
            with self.subTest(options=options):
                source, target, report = self.contacts()
                report['converterOptions'] = options
                target['entry'] = [e for e in target['entry'] if e['resource']['id'] != omitted]
                for entry in target['entry']:
                    r = entry['resource']
                    if r['id'].endswith('-V-1'):
                        r.pop('partOf', None)
                        if parent: r['partOf'] = {'reference': 'Encounter/' + parent}
                    elif r['id'].endswith('-A-1') and omitted == 'p-E-1':
                        r.pop('partOf', None)
                checked_contacts(source, target, report)
                wrong = copy.deepcopy(target)
                wrong['entry'][-1]['resource']['partOf'] = {'reference': 'Encounter/wrong'}
                with self.assertRaisesRegex(AssertionError, 'parent'):
                    checked_contacts(source, wrong, report)
                wrong = copy.deepcopy(target)
                wrong['entry'].pop()
                with self.assertRaisesRegex(AssertionError, 'identities'):
                    checked_contacts(source, wrong, report)

    def test_generic_contact_and_disabled_encounters(self):
        source, target, report = self.contacts()
        report['converterOptions'] = {f'CONTACT_{level}_ENABLED': 'false' for level in ('FACILITY', 'DEPARTMENT', 'WARD_SERVICE')}
        target['entry'] = target['entry'][:1]
        target['entry'][0]['resource']['type'] = []
        checked_contacts(source, target, report)
        report['converterOptions']['ENCOUNTER_ENABLED'] = 'false'
        with self.assertRaises(AssertionError): checked_contacts(source, target, report)
        checked_contacts(source, {'entry': []}, report)

    def test_changed_interval_is_rejected_even_when_child_matches_parent(self):
        source, target, report = self.contacts()
        for entry in target['entry']:
            entry['resource']['period']['end'] = '2026-01-01T12:00:00+00:00'
        with self.assertRaisesRegex(AssertionError, 'end'):
            checked_contacts(source, target, report)

    def test_contact_match_uses_requested_level_and_inpatient_preference(self):
        source, target, report = self.contacts()
        contacts = checked_contacts(source, target, report)
        contacts['ambulatory'] = dict(contacts['p-E-1-A-1'], id='ambulatory', cls='AMB', start='2026-01-01T10:15:00+00:00')
        self.assertEqual('Encounter/p-E-1-A-1', matching_contact(contacts, ['2026-01-01T10:30:00Z'], 'department', {}))
        self.assertEqual('', matching_contact(contacts, [START], 'department', {'CONTACT_DEPARTMENT_ENABLED': 'false'}))
        self.assertEqual('', matching_contact(contacts, [START], 'none', {}))

    def test_observation_categories_are_selected_separately(self):
        vital = {'resourceType': 'Observation', 'category': [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/observation-category', 'code': 'vital-signs'}]}]}
        options = {'OBSERVATION_LABORATORY_ENABLED': 'false'}
        self.assertTrue(selected(vital, options))
        self.assertTrue(selected({'resourceType': 'Observation'}, options))

    def test_ucum_relocation_checks_components_without_changing_target(self):
        observation = {'resourceType': 'Observation', 'id': 'o', 'component': [{'valueQuantity': {'value': 1, 'system': 'http://unitsofmeasure.org', 'unit': 'mg'}}]}
        observation['category'] = [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/observation-category', 'code': 'laboratory'}]}]
        options = {'OBSERVATION_LABORATORY_UCUM_CODE_IN_UNIT': 'true'}
        original = copy.deepcopy(observation)
        original['component'][0]['valueQuantity'].update(code='mg', unit='milligrams')
        originals = {'o': original}
        result = checked_observation_units([observation], options, originals)
        self.assertEqual('mg', result[0]['component'][0]['valueQuantity']['code'])
        self.assertNotIn('code', observation['component'][0]['valueQuantity'])
        self.assertEqual('milligrams', result[0]['component'][0]['valueQuantity']['unit'])
        for key, value in [('code', 'mg'), ('system', 'wrong')]:
            wrong = copy.deepcopy(observation)
            wrong['component'][0]['valueQuantity'][key] = value
            with self.assertRaises(AssertionError): checked_observation_units([wrong], options, originals)

    def test_unit_error_check_preserves_custom_empty_systems_and_missing_codes(self):
        for quantity in ({'value': 1, 'system': 'custom', 'code': 'c', 'unit': 'u'},
                         {'value': 1, 'code': 'c'}, {'value': 1, 'system': 'http://unitsofmeasure.org', 'unit': 'u'}):
            observation = {'resourceType': 'Observation', 'id': 'o', 'valueQuantity': quantity,
                           'category': [{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/observation-category', 'code': 'laboratory'}]}]}
            result = checked_observation_units([observation], {'OBSERVATION_LABORATORY_UCUM_CODE_IN_UNIT': 'true'}, {'o': observation})
            self.assertEqual([observation], result)

    def test_descriptive_reference_has_exact_requested_fields(self):
        self.assertEqual({'identifier': {'value': 'm'}, 'display': 'Product'}, descriptive_reference('Medication', 'm', 'Product', {
            'MEDICATION_MODE': 'reference-only', 'MEDICATION_EXISTING_REFERENCE': 'false', 'MEDICATION_DESCRIPTION': 'true'}))
        self.assertEqual({}, descriptive_reference('Location', 'l', 'Ward', {'LOCATION_MODE': 'neither'}))

    def test_medication_event_and_reference_cannot_disappear(self):
        row = record('Medikation', {
            'Patient-ID': 'p',
            'Fall-Nr': '1',
            'Medikationstyp': 'Verordnung',
            'Status': 'active',
            'Absicht': 'order',
            'Präparatbezeichnung': 'Product',
            'Wirkstoffcode': '123',
            'Wirkstoffcodesystem': 'SNOMED CT',
        })
        resource = {'resourceType': 'MedicationRequest', 'id': 'p-E-1-MR-1', 'status': 'active', 'intent': 'order'}
        options = {'MEDICATION_MODE': 'neither'}
        check_medication_references([row], [resource], options, 'p')
        with self.assertRaisesRegex(AssertionError, 'identities'):
            check_medication_references([row], [], options, 'p')
        resource['medicationReference'] = {'reference': 'Medication/wrong'}
        with self.assertRaises(AssertionError):
            check_medication_references([row], [resource], options, 'p')

    def test_patient_neither_checks_hashed_copy_identity_and_rejects_references(self):
        source = {'entry': [{'resource': {'resourceType': 'Observation', 'id': 'obs'}}]}
        target = {'entry': [{'resource': {'resourceType': 'Observation', 'id': event_id('p', 'Observation', 'obs')}}]}
        options = {'PATIENT_MODE': 'neither'}
        with patch('check_synthea_roundtrip.check', return_value={'conditions': 0, 'encounters': 0}) as check:
            check_configured(source, target, {'sourcePatient': 'p'}, options, ['p'])
            self.assertEqual('p', check.call_args.args[2]['outputPatient'])
            target['entry'][0]['resource']['subject'] = {'reference': 'Patient/p'}
            with self.assertRaisesRegex(AssertionError, 'neither'):
                check_configured(source, target, {'sourcePatient': 'p'}, options, ['p'])

    @unittest.skipUnless((Path(__file__).resolve().parents[2] / 'target/synthifhir.jar').exists(), 'Build the converter JAR to test the Java workflow bridge')
    def test_workflow_bridge_exposes_effective_selection_and_reference_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'selection.config'
            path.write_text('CONFIGURATION_VERSION=1\nPATIENT_MODE=neither\nENCOUNTER_ENABLED=false\nLOCATION_MODE=reference-only\nLOCATION_DESCRIPTION=true\n')
            values = resolve_config(path)['values']
            self.assertEqual('neither', values['PATIENT_MODE'])
            self.assertEqual('false', values['CONTACT_DEPARTMENT_ENABLED'])
            self.assertEqual('none', values['REFERENCE_PROCEDURE_ENCOUNTER'])
            self.assertEqual('true', values['LOCATION_DESCRIPTION'])
            self.assertEqual('reference-only', values['LOCATION_MODE'])
