"""Verify configured clinical day shifts against independently prepared input facts."""
from copy import deepcopy
from datetime import date, timedelta
from check_output_selection import enabled, event_id


def shift_date(value, days):
    if not value or value.startswith('!dar:'):
        return None
    shifted = date.fromisoformat(value[:10]) + timedelta(days=days)
    return shifted.isoformat() + value[10:]


def shift_days(report):
    options = report.get('converterOptions', {})
    if not enabled(options, 'TIME_SHIFT_ENABLED', False):
        return 0
    return int(options.get('TIME_SHIFT_BASE_DAYS', 0)) + int(report.get('iteration', 0)) * int(options.get('TIME_SHIFT_ITERATION_DAYS', 0))


def temporal_expectations(source, report):
    from synthea_to_excel import prepare
    settings = report.get('terminology', {})
    rows, evidence = prepare(source, {'SYNTHEA_MAPPING_YEAR': str(settings.get('mappingYear', '2026')), 'SYNTHEA_VERSION_OUTPUT': settings.get('versionOutput', 'Jahr')})
    pid = report.get('outputPatient', report['sourcePatient'].replace('_', '-'))
    options = report.get('converterOptions', {})
    resources = [e['resource'] for e in source['entry']]
    patient = next(r for r in resources if r['resourceType'] == 'Patient')
    result = {pid: {key: patient.get(key) for key in ('birthDate', 'deceasedDateTime')}}
    def identifier(row, suffix, key, number):
        return pid + ('-E-' + row['Fall-Nr'] if row['Fall-Nr'] else '') + '-' + suffix + '-' + str(int(options.get('START_ID_' + key, 1)) + number)
    for number, row in enumerate(rows['Diagnose']):
        result[identifier(row, 'C', 'CONDITION', number)] = {'recordedDate': row['Dokumentationszeitpunkt'],
                                                                        'onsetDateTime': row['Beginn'], 'abatementDateTime': row['Ende']}
    for number, row in enumerate(rows['Prozedur']):
        result[identifier(row, 'P', 'PROCEDURE', number)] = ({'performedPeriod.start': row['Durchführungsbeginn'], 'performedPeriod.end': row['Ende']}
                                                               if row['Ende'] else {'performedDateTime': row['Durchführungsbeginn']})
    for number, row in enumerate(row for row in rows['DocumentReference'] if row['Dokument-ID']):
        result[identifier(row, 'DR', 'DOCUMENT_REFERENCE', number)] = {'date': row['Ausgabezeitpunkt']}
    from check_medication_transformations import transformed_medications
    for medication in transformed_medications(rows['Medikation'], options, pid):
        fields = {k: medication.get(k) for k in ('authoredOn', 'dateAsserted', 'effectiveDateTime')}
        if 'effectivePeriod' in medication:
            fields.update({'effectivePeriod.' + k: medication['effectivePeriod'].get(k) for k in ('start', 'end')})
        result[medication['id']] = fields
    for r in resources:
        kind = r['resourceType']
        if kind == 'Observation': fields = {k: r.get(k) for k in ('effectiveDateTime', 'issued')}
        elif kind == 'Immunization': fields = {'occurrenceDateTime': r.get('occurrenceDateTime')}
        elif kind == 'DiagnosticReport': fields = {k: r.get(k) for k in ('effectiveDateTime', 'issued')}
        elif kind == 'CarePlan': continue  # Plan and activity dates are checked below from named input fields.
        else: continue
        result[event_id(pid, kind, r['id'])] = fields
    from check_careplans import expected_plans
    for plan in expected_plans(rows['Behandlungsplan'], pid):
        fields = {'created': plan.get('created')}
        fields.update({'period.' + k: plan.get('period', {}).get(k) for k in ('start', 'end')})
        for i, activity in enumerate(plan.get('activity', [])):
            for key, value in activity['detail'].get('scheduledPeriod', {}).items():
                fields[f'activity.{i}.detail.scheduledPeriod.{key}'] = value
        result[plan['id']] = fields
    return result


def checked_original_times(source, target, report):
    days = shift_days(report)
    if not days:
        return target
    expected = temporal_expectations(source, report)
    result = deepcopy(target)
    for entry in result['entry']:
        resource = entry['resource']
        if resource['resourceType'] in ('Encounter', 'Location', 'Medication', 'Organization', 'Consent'):
            continue  # Contacts and synthetic consent have separate date checks; shared definitions have no clinical dates.
        assert resource['id'] in expected, 'Missing temporal expectation: ' + resource['id']
        for path, original in expected[resource['id']].items():
            parent = resource
            *parts, leaf = path.split('.')
            for part in parts:
                parent = parent[int(part)] if isinstance(parent, list) else parent.get(part, {})
            wanted = shift_date(original, days)
            actual = parent.get(leaf)
            assert (actual or '').replace('Z', '+00:00') == (wanted or '').replace('Z', '+00:00'), {
                'resource': resource['id'], 'field': path, 'shiftDays': days, 'expected': wanted, 'actual': actual}
            if wanted:
                parent[leaf] = original
    return result
