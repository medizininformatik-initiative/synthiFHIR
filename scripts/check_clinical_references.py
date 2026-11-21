"""Check clinical assignments against source-derived, selected contact intervals."""
from check_contact_selection import contact_expectations, contact_selected
from check_encounter_policies import instant
from check_output_selection import event_id, resource_key, selected


def matching_contact(contacts, times, level, options):
    candidates = [c for c in contacts.values() if c['level'] == level and contact_selected(c, options)
                  and c['kind'] not in ('operation', 'ub', 'konsil')]
    def match(pool):
        for value in times:
            time = instant(value)
            if time is None:
                continue
            matches = [c for c in pool if instant(c['start']) <= time
                       and (not c['end'] or time <= instant(c['end']))]
            if matches:
                return max(matches, key=lambda c: instant(c['start']))
        return None
    found = match(candidates)
    if found and found['cls'] == 'AMB':
        found = match([c for c in candidates if c['cls'] == 'IMP']) or found
    return 'Encounter/' + found['id'] if found else ''


def check_clinical_references(source, resources, report, medication_rows, document_rows):
    options = report.get('converterOptions', {})
    # Legacy workbooks retain their own assignment convention.
    if 'REFERENCE_PROCEDURE_ENCOUNTER' not in options:
        return
    contacts = contact_expectations(source, report)
    pid = report.get('outputPatient', report['sourcePatient'].replace('_', '-'))
    plan_rows = {r['sourceId']: str(r['inputRow']) for r in report.get('clinicalImports', []) if r['resourceType'] == 'CarePlan'}
    events = {event_id(pid, e['resource']['resourceType'], plan_rows.get(e['resource']['id'], e['resource']['id'])): e['resource']
              for e in source['entry'] if e['resource']['resourceType'] in ('CarePlan', 'DiagnosticReport')}
    sources = {e.get('fullUrl', e['resource']['resourceType'] + '/' + e['resource']['id']): e['resource'] for e in source['entry']}
    sources.update({e['resource']['resourceType'] + '/' + e['resource']['id']: e['resource'] for e in source['entry']})
    imported_observations = {r['sourceId'] for r in report.get('clinicalImports', []) if r['resourceType'] == 'Observation'}
    documents = {}
    for number, row in enumerate((row for row in document_rows if row['Dokument-ID']), int(options.get('START_ID_DOCUMENT_REFERENCE', 1))):
        documents[pid + ('-E-' + row['Fall-Nr'] if row['Fall-Nr'] else '') + '-DR-' + str(number)] = row
    from check_medication_transformations import transformed_medications
    medications = {r['id']: r for r in transformed_medications(medication_rows, options, pid)}
    for resource in resources:
        kind = resource['resourceType']
        key = 'REFERENCE_' + resource_key(resource) + '_ENCOUNTER'
        if key not in options or kind == 'Condition':
            continue
        level = options[key]
        times, explicit = [], None
        if kind == 'Procedure':
            times = [resource.get('performedDateTime') or resource.get('performedPeriod', {}).get('start')]
        elif kind == 'Observation':
            times = [resource.get('effectiveDateTime')]
        elif kind == 'Immunization':
            times = [resource.get('occurrenceDateTime')]
        elif kind.startswith('Medication'):
            expected = medications[resource['id']]
            times = [expected.get('authoredOn') or expected.get('effectiveDateTime') or expected.get('effectivePeriod', {}).get('start')]
        elif kind in ('CarePlan', 'DiagnosticReport'):
            original = events[resource['id']]
            if kind == 'DiagnosticReport':
                expected_results = [event_id(pid, 'Observation', r['id']) for ref in original.get('result', [])
                                    if (r := sources.get(ref.get('reference'))) and r['id'] in imported_observations and selected(r, options)]
                actual_results = [ref.get('reference') for ref in resource.get('result', [])]
                assert actual_results == ['Observation/' + identifier for identifier in expected_results], 'Diagnostic report results differ'
            times = ([original.get('period', {}).get('start')] if kind == 'CarePlan'
                     else [original.get('effectiveDateTime'), original.get('issued')])
        elif kind == 'DocumentReference':
            row = documents[resource['id']]
            times = [row['Ausgabezeitpunkt']]
            strategy = options.get('REFERENCE_DOCUMENT_REFERENCE_ASSIGNMENT_STRATEGY', 'fill-missing')
            if strategy != 'timestamp-only' and row['Fall-Nr']:
                contact = contacts[pid + '-E-' + row['Fall-Nr']]
                explicit = 'Encounter/' + contact['id'] if contact_selected(contact, options) else ''
            elif strategy == 'explicit-only':
                explicit = ''
        wanted = '' if level == 'none' else explicit if explicit is not None else matching_contact(contacts, times, level, options)
        if kind == 'DocumentReference':
            actual = [r.get('reference') for r in resource.get('context', {}).get('encounter', [])]
        else:
            field = 'context' if kind in ('MedicationAdministration', 'MedicationStatement') else 'encounter'
            ref = resource.get(field, {})
            actual = [ref.get('reference')] if ref else []
        assert actual == ([wanted] if wanted else []), {'resource': resource['id'], 'expectedEncounters': wanted, 'actualEncounters': actual}
