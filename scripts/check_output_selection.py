"""Independent expectations for configured resource and reference selection."""
import hashlib
import re
import uuid


def enabled(options, key, default=True):
    return str(options.get(key, default)).lower() == 'true'


def resource_key(resource):
    kind = resource['resourceType']
    if kind == 'Observation':
        categories = {c.get('code') for category in resource.get('category', [])
                      for c in category.get('coding', [])
                      if c.get('system') == 'http://terminology.hl7.org/CodeSystem/observation-category'}
        if 'vital-signs' in categories: return 'OBSERVATION_VITAL_SIGNS'
        if 'laboratory' in categories: return 'OBSERVATION_LABORATORY'
        return 'OBSERVATION'
    return re.sub(r'(?<!^)(?=[A-Z])', '_', kind).upper()


def selected(resource, options):
    kind = resource['resourceType']
    if kind in ('Patient', 'Medication', 'Location'):
        return options.get(kind.upper() + '_MODE', 'generate-reference') == 'generate-reference'
    return enabled(options, resource_key(resource) + '_ENABLED')


def named_id(kind, key):
    return kind + '-' + str(uuid.UUID(bytes=hashlib.md5(key.encode('utf-8')).digest(), version=3))


def event_id(pid, kind, source_id):
    return named_id(kind, pid + '|' + kind + '|' + source_id)


def descriptive_reference(kind, identifier, display, options):
    mode = options.get(kind.upper() + '_MODE', 'generate-reference')
    if mode == 'neither':
        return {}
    if mode == 'generate-reference':
        return {'reference': kind + '/' + identifier}
    result = {}
    if enabled(options, kind.upper() + '_EXISTING_REFERENCE'):
        result['reference'] = kind + '/' + identifier
    if enabled(options, kind.upper() + '_DESCRIPTION', False):
        result['identifier'] = {'value': identifier}
        if display:
            result['display'] = display
    return result


def patient_owner(resource, patient_ids, source):
    """Patient omission keeps IDs; hashed event IDs also encode input identity."""
    kind, identifier = resource['resourceType'], resource['id']
    if kind in ('Immunization', 'DiagnosticReport', 'CarePlan', 'Observation'):
        candidates = [pid for pid in patient_ids for entry in source['entry']
                      if entry['resource']['resourceType'] == kind
                      and event_id(pid, kind, entry['resource']['id']) == identifier]
    else:
        candidates = [pid for pid in patient_ids if identifier.startswith(pid + '-')]
        # Prefixes may overlap; the longest exact patient prefix owns the ID.
        if candidates:
            candidates = [max(candidates, key=len)]
    assert len(candidates) == 1, 'Cannot attribute output resource: ' + identifier
    return candidates[0]


def identity_key(*parts):
    return ''.join(str(len(str(p or '').encode('utf-16-le')) // 2) + ':' + str(p or '') for p in parts)


def place_id(kind, path, encounter, options):
    facility = identity_key(options.get('PID_PREFIX', ''), options.get('PID_SUFFIX', ''))
    scope = encounter if options.get(kind.upper() + '_IDENTITY', 'shared') == 'separate' else ''
    return named_id(kind, identity_key(facility, kind, path, scope))


def event_product_id(product, event, options):
    if options.get('MEDICATION_IDENTITY', 'shared') != 'separate': return product
    return named_id('Medication', identity_key('Medication/' + product, event['resourceType'] + '/' + event['id']))
