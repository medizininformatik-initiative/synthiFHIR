#!/usr/bin/env python3
"""Compare the diagnosis contract after Synthea -> Excel -> FHIR.
Usage: INPUT.json OUTPUT.json OUTPUT.loss.json
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from diagnosis_mapping import map_diagnosis, mapping_metadata
from check_clinical_roundtrip import check_clinical
from check_movements import check_movements, check_station_providers, check_organizations
from german_demographics import check_patient
from check_output_selection import enabled, selected, patient_owner
from check_contact_selection import contact_expectations, contact_selected
from check_clinical_references import matching_contact


def check(source, target, report, bundle_stations=None):
    from check_dar import checked_original_dar
    target = checked_original_dar(source, target, report)
    from check_encounter_policies import checked_original_periods
    target = checked_original_periods(source, target, report)
    from check_time_shift import checked_original_times
    target = checked_original_times(source, target, report)
    src = [e['resource'] for e in source['entry']]
    dst = [e['resource'] for e in target['entry']]
    source_ids = {e.get('fullUrl'):e['resource'].get('id')for e in source['entry']}
    source_ids.update({r['resourceType']+'/'+r['id']:r['id']for r in src})
    target_ids = {r['resourceType']+'/'+r['id'] for r in dst}
    target_ids.update(e.get('fullUrl')for e in target['entry']if e.get('fullUrl'))
    pid = report.get('outputPatient', report['sourcePatient'].replace('_','-'))
    options = report.get('converterOptions', {})
    expected_contacts = contact_expectations(source, report)
    patients = [r for r in dst if r['resourceType'] == 'Patient']
    assert len(patients) == int(selected({'resourceType': 'Patient'}, options)), 'Configured patient output differs'
    demographic_check = (check_patient(next(r for r in src if r['resourceType']=='Patient'), patients[0], report)
                         if patients else {'output': 'omitted by configured patient mode'})
    assert report['diagnosisMapping'] == mapping_metadata(), 'Use the mapping version that produced this workbook'
    from terminology_year import emitted, signature as coding_signature, metadata as terminology_metadata
    terminology = report.get('terminology', {})
    year, mode = terminology.get('mappingYear', '2026'), terminology.get('versionOutput', 'Jahr')
    if terminology:
        assert terminology == terminology_metadata(year, mode), 'Annual catalogue evidence changed'
    decisions = [map_diagnosis(r, year) for r in src if r['resourceType'] == 'Condition']
    assert report['diagnosisMappings'] == decisions, 'Mapping report differs from the versioned decisions'
    excluded = {d['sourceId'] for d in decisions if d['status'] == 'excluded'}
    assert excluded == {l['id'] for l in report['losses']
                        if l['resourceType'] == 'Condition' and l['path'] == '$'}, 'Unreported diagnosis exclusion'
    def signature(r, original):
        expected_codings = list(r['code']['coding'])
        if original:
            decision = map_diagnosis(r, year)
            if decision['target'] is not None:
                expected_codings.append(decision['target'])
        if not original and any(c['system'] == 'http://fhir.de/CodeSystem/bfarm/icd-10-gm' for c in expected_codings):
            assert expected_codings[0]['system'] == 'http://fhir.de/CodeSystem/bfarm/icd-10-gm', 'ICD-10-GM must be first'
        codings = tuple(sorted(coding_signature(emitted(c, mode) if original else c) for c in expected_codings))
        encounter = r.get('encounter',{}).get('reference','')
        options = report.get('converterOptions', {})
        configured_level = options.get('REFERENCE_CONDITION_ENCOUNTER')
        if original and configured_level is not None:
            encounter = matching_contact(expected_contacts, [r.get('recordedDate')], configured_level, options)
        elif original and options.get('SET_REFERENCE_FROM_CONDITION_TO_ENCOUNTER', 'true') == 'false':
            encounter = ''
        if original and encounter and configured_level is None:
            encounter = 'Encounter/'+pid+'-E-'+report['encounterNumbers'][source_ids[encounter]]
        statuses = tuple(tuple(c['code']for c in r.get(field,{}).get('coding',[]))for field in ['clinicalStatus','verificationStatus'])
        if original and 'verificationStatusChange' in decision:
            statuses = (statuses[0], (decision['verificationStatusChange']['to'],))
        return (codings, r.get('recordedDate',''), r.get('onsetDateTime',''),r.get('abatementDateTime',''),encounter,statuses)
    original=Counter(signature(r,True)for r in src if r['resourceType']=='Condition' and r['id'] not in excluded and enabled(options, 'CONDITION_ENABLED'))
    converted=Counter(signature(r,False)for r in dst if r['resourceType']=='Condition')
    assert original==converted, {'missing':list((original-converted).elements())[:2], 'extra':list((converted-original).elements())[:2]}
    if options.get('PATIENT_MODE') == 'reference-only':
        target_ids.add('Patient/' + pid)
    for r in dst:
        if r['resourceType']=='Patient':continue
        for field in ['subject','patient','encounter','context']:
            if r.get(field,{}).get('reference'): assert r[field]['reference'] in target_ids
        for reference in r.get('result',[]): assert reference['reference'] in target_ids
    encounters={r['id']:r for r in dst if r['resourceType']=='Encounter'}
    for r in src:
        if r['resourceType']!='Encounter':continue
        identifier = pid+'-E-'+report['encounterNumbers'][r['id']]
        if not contact_selected(expected_contacts[identifier], options):
            assert identifier not in encounters, 'Deselected facility contact was emitted'
            continue
        found=encounters[identifier]
        if r['class']['code'] == 'EMER':
            assert found['class']['code'] == 'AMB'
            mapping = [m for m in report['encounterMappings'] if m['sourceId'] == r['id']]
            assert len(mapping) == 1 and mapping[0]['sourceClass'] == 'EMER' and mapping[0]['targetClass'] == 'AMB'
            assert mapping[0]['admissionReasonFourthComponent'] == '7'
            reasons = [e for e in found.get('extension', []) if e['url'] == 'http://fhir.de/StructureDefinition/Aufnahmegrund']
            assert len(reasons) == 1
            parts = reasons[0]['extension']
            assert len(parts) == 1 and parts[0]['url'] == 'VierteStelle'
            assert parts[0]['valueCoding']['system'] == 'http://fhir.de/CodeSystem/dkgev/AufnahmegrundVierteStelle'
            assert parts[0]['valueCoding']['code'] == '7'
        else:
            assert found['class']['code']==r['class']['code']
        for date in ['start','end']:
            if r.get('period',{}).get(date):
                assert datetime.fromisoformat(found['period'][date].replace('Z','+00:00'))==datetime.fromisoformat(r['period'][date].replace('Z','+00:00')), {
                    'encounter': r['id'], 'field': date, 'source': r['period'][date], 'target': found['period'][date]}
    movement_check = check_movements(source, target, report)
    source_encounters = sum(c['level'] == 'facility' and contact_selected(c, options) for c in expected_contacts.values())
    assert Counter(r['resourceType']for r in dst if r['resourceType'] in ('Patient','Encounter','Condition'))==Counter(Patient=len(patients),Encounter=source_encounters+movement_check['contacts'],Condition=sum(original.values()))
    stations = check_station_providers(expected_contacts, encounters, options)
    if bundle_stations is None:
        check_organizations(dst, stations)
    else:
        bundle_stations.update(stations)
    clinical = check_clinical(source, target, report, len(stations))
    if patients:
        demographic_check['identity'] = 'configured synthetic identity verified'
        demographic_check['clinicalFacts'] = 'verified against configured output settings'
    return {'demographics':demographic_check,'movements':movement_check,'clinical':clinical,'conditions':sum(original.values()),'excludedConditions':len(excluded),'encounters':len(encounters),
            'sourceDiagnosisValuesAndReferences': 'verified against configured mapping and output settings',
            'verificationStatusChanges':sum('verificationStatusChange' in d for d in decisions),
            'additionalIcd10GmCodings':sum(d['target'] is not None for d in decisions),
            'mappingDecisions':dict(Counter(d['status'] for d in decisions)),
            'emergencyMappings':len(report.get('encounterMappings',[])),
            'terminologyValidation':'not performed'}

def check_configured(source, target, report, options, patient_ids):
    """Check each requested patient copy without assuming the default ID scheme."""
    actual = [e['resource']['id'] for e in target['entry'] if e['resource']['resourceType'] == 'Patient']
    wanted_patients = patient_ids if selected({'resourceType': 'Patient'}, options) else []
    assert Counter(actual) == Counter(wanted_patients), 'Configured patient copies differ'
    keys = [(e['resource']['resourceType'], e['resource']['id']) for e in target['entry']]
    assert len(keys) == len(set(keys)), 'Duplicate output resource identity'
    derived_owners = {}
    if options.get('PATIENT_MODE') == 'neither' and any(e['resource']['id'].startswith('derived-') for e in target['entry']):
        from synthea_to_excel import prepare
        from check_medication_transformations import transformed_medications
        rows, _ = prepare(source, options)
        derived_owners = {r['id']: pid for pid in patient_ids for r in transformed_medications(rows['Medikation'], options, pid)}
    shared = {'Medication', 'Location', 'Organization'}
    groups = {pid: [] for pid in patient_ids}
    for entry in target['entry']:
        resource = entry['resource']
        if resource['resourceType'] in shared:
            for group in groups.values(): group.append(entry)
            continue
        if resource['resourceType'] == 'Patient':
            pid = resource['id']
        else:
            reference = resource.get('subject', resource.get('patient', {})).get('reference', '')
            if options.get('PATIENT_MODE') == 'neither':
                assert not reference, 'Patient reference emitted in neither mode'
                pid = derived_owners.get(resource['id'])
                if pid is None: pid = patient_owner(resource, patient_ids, source)
            else:
                assert reference.startswith('Patient/'), 'Resource has no patient attribution: ' + resource['resourceType']
                pid = reference.removeprefix('Patient/')
        assert pid in groups, 'Unexpected patient attribution: ' + pid
        groups[pid].append(entry)
    bundle_stations = {}
    copies = [check(source, {'entry': groups[pid]}, dict(report, outputPatient=pid, converterOptions=options, iteration=iteration), bundle_stations)
              for iteration, pid in enumerate(patient_ids)]
    check_organizations((entry['resource'] for entry in target['entry']), bundle_stations)
    if len(copies) == 1:
        result = copies[0]
    else:
        result = {'copies': copies, 'conditions': sum(c['conditions'] for c in copies),
                  'encounters': sum(c['encounters'] for c in copies)}
    result['outputPatients'] = patient_ids
    if options.get('REFERENCE_CONDITION_ENCOUNTER') == 'none' or ('REFERENCE_CONDITION_ENCOUNTER' not in options and options.get('SET_REFERENCE_FROM_CONDITION_TO_ENCOUNTER') == 'false'):
        result['sourceDiagnosisValuesAndReferences'] = 'values checked; encounter references omitted by explicit converter option'
    return result


if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit(__doc__)
    print(json.dumps(check(*(json.loads(Path(f).read_text())for f in sys.argv[1:])),indent=2))
