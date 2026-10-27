"""Verify product reference identity and descriptive output from input rows."""
from check_output_selection import descriptive_reference, named_id, event_product_id


def product_id(row):
    # Java length prefixes count UTF-16 code units, including supplementary characters.
    parts = [str(part or '') for part in [row['Präparatbezeichnung'], row['Präparatcode'], row['Präparatcodesystem'], row['ATC-Code'], row['ATC-Version'], row['Darreichungsform'], row['Wirkstoffcode'], row['Wirkstoffcodesystem']]]
    return named_id('Medication', ''.join(str(len(p.encode('utf-16-le')) // 2) + ':' + p for p in parts))


def check_medication_references(rows, resources, options, pid):
    from check_medication_transformations import transformed_medications
    expected = {r['id']: r for r in transformed_medications(rows, options, pid)}
    actual = {r['id']: r for r in resources if r['resourceType'] in ('MedicationRequest', 'MedicationAdministration', 'MedicationStatement')}
    assert actual.keys() == expected.keys(), 'Medication event identities differ'
    for identifier, resource in actual.items():
        wanted = expected[identifier]
        product = wanted['medicationReference']['reference'].removeprefix('Medication/')
        label = next(row['Präparatbezeichnung'] for row in rows if product_id(row) == product)
        product = event_product_id(product, wanted, options)
        reference = descriptive_reference('Medication', product, label, options)
        if reference: wanted['medicationReference'] = reference
        else: wanted.pop('medicationReference')
        # Metadata and patient/contact assignments have their own checks. All clinical event facts are compared here.
        facts = {k: v for k, v in resource.items() if k not in ('meta', 'identifier', 'subject', 'context', 'encounter')}
        assert facts == wanted, {'medicationEvent': identifier, 'expected': wanted, 'actual': facts}
