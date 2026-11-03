"""Verify DAR replacements, then restore source-derived values for the fact comparison."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from check_medication_transformations import DAR, absent
from synthea_expected_fields import expected_fields
from check_output_selection import resource_key


@lru_cache(maxsize=1)
def catalogue():
    root = Path(__file__).resolve().parents[1] / 'web/catalog'
    fields = json.loads((root / 'dar/generated/catalog.json').read_text())['fields']
    contract = json.loads((root / 'options/contract.json').read_text())
    return fields, contract['propertiesFormat']['darProperties']


def checked_original_dar(source, target, report):
    options = report.get('converterOptions', {})
    if not any(key.startswith('DAR_') and value != 'unchanged' for key, value in options.items()):
        return target
    fields, properties = catalogue()
    baseline = expected_fields(source, report)
    result = deepcopy(target)
    for entry in result['entry']:
        resource = entry['resource']
        assert resource['id'] in baseline, 'No independent DAR expectation: ' + resource['id']
        original = baseline[resource['id']]
        output_period = deepcopy(resource.get('period'))
        for field in fields:
            if field['resourceType'] != resource['resourceType']: continue
            identifier = field['id']
            property_name = properties[identifier]
            code = options.get(property_name, 'unchanged')
            if resource['resourceType'] == 'Encounter':
                scope = {'AMB': 'ambulatory', 'IMP': 'inpatient'}.get(resource.get('class', {}).get('code'))
                scoped = identifier.replace('Encounter.', 'Encounter.' + str(scope) + '.')
                if options.get(properties.get(scoped), 'unchanged') != 'unchanged':
                    property_name = properties[scoped]
                    code = options[property_name]
            if resource['resourceType'] == 'Observation':
                kind = resource_key(original)
                if kind == 'OBSERVATION_OTHER' or identifier.startswith('VitalSigns.') != (kind == 'OBSERVATION_VITAL_SIGNS'): continue
            if code == 'unchanged': continue
            if code.startswith('!dar:'): code = code[5:]
            only_missing = str(options.get(property_name + '_ONLY_WHEN_MISSING', False)).lower() == 'true'
            if field['representation'] == 'dataAbsentReason':
                values = list(zip(original.get('component', []), resource.get('component', []))) if field['field'].startswith('component.') else [(original, resource)]
                if field['field'].startswith('component.'):
                    assert len(original.get('component', [])) == len(resource.get('component', [])), 'DAR component count differs'
                for before, after in values:
                    keys = [k for k in before if k.startswith('value')]
                    if only_missing and (any(has_content(before[k]) for k in keys) or before.get('dataAbsentReason')):
                        assert after == before, 'Missing-only DAR changed an existing measurement or DAR'
                        continue
                    if not keys:
                        if before.get('dataAbsentReason') or only_missing:
                            assert after.get('dataAbsentReason') == {'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/data-absent-reason', 'code': code}]}, 'Wrong measurement DAR'
                            after.pop('dataAbsentReason')
                            if 'dataAbsentReason' in before: after['dataAbsentReason'] = deepcopy(before['dataAbsentReason'])
                        continue
                    assert len(keys) == 1
                    key = keys[0]
                    if (key == 'valueQuantity') != (field['semanticGroup'] == 'numeric-measurement'): continue
                    assert not any(k.startswith('value') for k in after), 'DAR measurement still contains a value'
                    assert after.get('dataAbsentReason') == {'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/data-absent-reason', 'code': code}]}, 'Wrong measurement DAR'
                    after.pop('dataAbsentReason')
                    after[key] = deepcopy(before[key])
                    if 'dataAbsentReason' in before: after['dataAbsentReason'] = deepcopy(before['dataAbsentReason'])
            else:
                path = field['field'].split('.')
                if identifier == 'Encounter.admissionReasonFourthDigit':
                    path = ['extension:Aufnahmegrund', 'extension:VierteStelle', 'valueCoding', 'code']
                restore_path(original, resource, path, field, code, only_missing)
            if identifier == 'DocumentReference.content.attachment.data' and not only_missing:
                for content in resource.get('content', []):
                    attachment = content['attachment']
                    assert 'size' not in attachment and 'hash' not in attachment, 'DAR attachment leaks size or hash'
                    assert not attachment.get('url', '').lower().startswith('data:'), 'DAR attachment leaks embedded data URL'
        if resource['resourceType'] == 'Encounter' and output_period != resource.get('period'):
            ended = bool(resource.get('period', {}).get('end'))
            assert resource.get('status') == ('finished' if ended else 'in-progress'), 'DAR contact status differs'
            for place in resource.get('location', []):
                assert place.get('period') == output_period, 'DAR location period differs'
                assert place.get('status') == ('completed' if ended else 'active'), 'DAR location status differs'
                place['period'] = deepcopy(resource['period'])
                place['status'] = 'completed' if resource['period'].get('end') else 'active'
            resource['status'] = 'finished' if resource['period'].get('end') else 'in-progress'
    return result


def has_content(value):
    if isinstance(value, dict):
        if any(e.get('url') == DAR for e in value.get('extension', [])): return True
        return any(has_content(v) for k, v in value.items() if k not in ('extension', 'id'))
    if isinstance(value, list): return any(has_content(v) for v in value)
    return value is not None and value != ''


def restore_path(before, after, parts, field, code, only_missing=False):
    key, *rest = parts
    if ':' in key:
        name, suffix = key.split(':')
        originals = [e for e in before.get(name, []) if e.get('url', '').rsplit('/', 1)[-1] == suffix]
        actuals = [e for e in after.get(name, []) if e.get('url', '').rsplit('/', 1)[-1] == suffix]
        assert len(originals) == len(actuals), 'DAR extension count differs'
        for a, b in zip(originals, actuals): restore_path(a, b, rest, field, code, only_missing)
        return
    if key.endswith('[x]'):
        prefix = key[:-3]
        choices = [k for k in before if k.startswith(prefix) and k[len(prefix):len(prefix)+1].isupper()]
        if not choices:
            if field.get('supportedChoices', [])[:1] != ['dateTime']: return
            key = prefix + 'DateTime'
        else:
            assert len(choices) == 1
            key = choices[0]
        assert not any(k.startswith(prefix) and k[len(prefix):len(prefix)+1].isupper() and k != key for k in after), 'Unexpected DAR choice type'
    value = before.get(key)
    if rest:
        if isinstance(value, list):
            actual = after.get(key, [])
            assert len(actual) == len(value), 'DAR repeated element count differs: ' + key
            for a, b in zip(value, actual): restore_path(a, b, rest, field, code, only_missing)
        elif isinstance(value, dict) or field['applyTo'] == 'existing-or-missing-scalar':
            actual = after.get(key, {})
            restore_path(value or {}, actual, rest, field, code, only_missing)
            if not actual: after.pop(key, None)
        return
    if only_missing and (has_content(value) or has_content(before.get('_' + key))):
        assert after.get(key) == value and after.get('_' + key) == before.get('_' + key), 'Missing-only DAR changed an existing value or DAR: ' + key
        return
    repeated = key in ('given', 'line')
    if repeated and value is None: return
    complex_value = isinstance(value, dict) or field['representation'] == 'complex-extension'
    if isinstance(value, list):
        expected = [absent(code) for _ in value]
        assert after.get('_' + key) == expected, 'Wrong repeated primitive DAR: ' + key
        assert key not in after or after[key] == [None] * len(value), 'DAR primitive array still contains values'
    elif complex_value:
        expected = {'extension': [e for e in (value or {}).get('extension', []) if e.get('url') != DAR] + absent(code)['extension']}
        if (value or {}).get('id'): expected['id'] = value['id']
        assert after.get(key) == expected, 'Wrong complex DAR: ' + key
    else:
        expected = deepcopy(before.get('_' + key, {}))
        expected['extension'] = [e for e in expected.get('extension', []) if e.get('url') != DAR] + absent(code)['extension']
        assert key not in after, 'DAR primitive still contains value: ' + key
        assert after.get('_' + key) == expected, 'Wrong primitive DAR: ' + key
    for name in (key, '_' + key):
        if name in before: after[name] = deepcopy(before[name])
        else: after.pop(name, None)
