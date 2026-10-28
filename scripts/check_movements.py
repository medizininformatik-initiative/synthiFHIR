"""Check selected contact hierarchy and independently derived location values."""
from check_contact_selection import checked_contacts, contact_selected
from check_output_selection import identity_key, place_id, descriptive_reference


def check_movements(source, target, report):
    contacts = checked_contacts(source, target, report)
    options = report.get('converterOptions', {})
    resources = [e['resource'] for e in target['entry']]
    encounters = {r['id']: r for r in resources if r['resourceType'] == 'Encounter'}
    locations = [r for r in resources if r['resourceType'] == 'Location']
    expected_locations = {}
    for contact in contacts.values():
        row = contact.get('row')
        wanted = []
        if row:
            path, parent = identity_key(row['Fachabteilung']), None
            for kind, name in zip(('wa', 'ro', 'bd'), [row['Station'], row['Zimmer'], row['Bett']]):
                if not name:
                    continue
                path += identity_key(kind, name)
                if kind == 'wa' and str(options.get('ENCOUNTER_STATION_LOCATION', True)).lower() == 'false': continue
                identifier = place_id('Location', identity_key(path, parent), contact['id'], options)
                expected_locations[identifier] = (kind, name, parent)
                reference = descriptive_reference('Location', identifier, name, options)
                if reference:
                    wanted.append((reference, kind))
                parent = 'Location/' + identifier
        if contact['id'] not in encounters:
            continue
        encounter = encounters[contact['id']]
        actual = encounter.get('location', [])
        assert [(p.get('location', {}), p.get('physicalType', {}).get('coding', [{}])[0].get('code'))
                for p in actual] == wanted, 'Configured contact locations differ: ' + contact['id']
        for place in actual:
            assert place.get('period') == encounter.get('period'), 'Location period differs'
            assert place.get('status') == ('completed' if encounter.get('period', {}).get('end') else 'active'), 'Location status differs'
    if options.get('LOCATION_MODE', 'generate-reference') != 'generate-reference':
        expected_locations = {}
    assert len(locations) == len(expected_locations) and {r['id'] for r in locations} == expected_locations.keys(), 'Configured location identities differ'
    for location in locations:
        kind, name, parent = expected_locations[location['id']]
        assert location.get('name') == name and location.get('status') == 'active', 'Location values differ'
        assert location.get('physicalType', {}).get('coding') == [
            {'system': 'http://terminology.hl7.org/CodeSystem/location-physical-type', 'code': kind}], 'Location type differs'
        assert location.get('partOf', {}).get('reference') == parent, 'Location hierarchy differs'
    return {'contacts': sum(c['level'] != 'facility' and contact_selected(c, options) for c in contacts.values()),
            'locations': len(locations), 'operationContacts': sum(c['kind'] == 'operation' and contact_selected(c, options) for c in contacts.values())}
