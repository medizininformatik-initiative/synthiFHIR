"""Versioned synthetic identities for German review cases, not anonymization.

Municipality/postcode pairs are real; streets and households are invented.
No ethnicity, citizenship, consent or insurance is inferred or generated.
"""
import hashlib
from functools import lru_cache
import json
from pathlib import Path
import re

DATA_PATH = Path(__file__).with_name('mappings') / 'german-demographics.json'
# Name-pool updates must not change previously assigned addresses.
ADDRESS_VERSION = 'german-demographics-v1'


@lru_cache(maxsize=1)
def load_data(path, modified_ns, size):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def identity(patient):
    stat = DATA_PATH.stat()
    data, checksum = load_data(DATA_PATH, stat.st_mtime_ns, stat.st_size)
    def pick(label, values):
        version = data['version'] if label in ('given','family') else ADDRESS_VERSION
        digest = hashlib.sha256((version+'|'+patient['id']+'|'+label).encode()).digest()
        return values[int.from_bytes(digest, 'big') % len(values)]
    names = data['givenNames'].get(patient.get('gender'), data['givenNames']['unknown'])
    name = {'given':[pick('given', names)], 'family':pick('family', data['familyNames'])}
    place = pick('place', data['places'])
    address = {k:place[k] for k in ('postalCode','city','state')}
    address.update(country='DE', line=[pick('street', data['streets'])+' '+str(pick('number', list(range(1, 100))))])
    return {'version':data['version'], 'dataSha256':checksum,
            'sourcePatient':patient['id'], 'name':name, 'address':address,
            'placeSource':place['source'], 'status':'synthetic-replacement',
            'limitations':['Invented street/household; not a deliverable postal address',
                           'Unweighted names/places; not population-representative; name collisions remain possible',
                           'One current address; no residential history']}


def localize_document_identity(text, patient):
    """Replace full names and unambiguous Synthea digit-suffixed name tokens.

    Ordinary isolated words such as May/Brown are deliberately not replaced.
    This is not translation, redaction or a general de-identification routine.
    """
    replacement = identity(patient)['name']
    aliases = {}
    for name in patient.get('name', []):
        given = name.get('given', [])
        family = name.get('family', '')
        if given and family:
            aliases[' '.join([*given, family])] = ' '.join([*replacement['given'], replacement['family']])
        for value, target in [(v, replacement['given'][0]) for v in given] + [(family, replacement['family'])]:
            if re.search(r'\D\d+$', value): aliases[value] = target
    if not aliases: return text, 0
    pattern = r'(?<!\w)(?:'+'|'.join(re.escape(k) for k in sorted(aliases, key=len, reverse=True))+r')(?!\w)'
    replacements = 0
    def replace(match):
        nonlocal replacements
        value = aliases[match.group()]
        replacements += value != match.group()
        return value
    return re.sub(pattern, replace, text), replacements


def check_patient(source, target, report):
    expected = identity(source)
    assert report.get('demographics') == expected, 'Demographic replacement report changed'
    assert len(target.get('name', [])) == 1 and len(target.get('address', [])) == 1
    for key, value in expected['name'].items():
        if key == 'given':
            # Excel joins given names; PatientConverter emits each space-separated part.
            value = ' '.join(value).split(' ')
        assert target['name'][0].get(key) == value, 'Localized patient name changed: '+key
    for key, value in expected['address'].items():
        if key == 'state':
            # FHIR uses the ISO code; the workbook and identity report retain the name.
            states = dict(zip(('Baden-Württemberg','Bayern','Berlin','Brandenburg','Bremen','Hamburg',
                'Hessen','Mecklenburg-Vorpommern','Niedersachsen','Nordrhein-Westfalen','Rheinland-Pfalz',
                'Saarland','Sachsen','Sachsen-Anhalt','Schleswig-Holstein','Thüringen'),
                ('DE-BW','DE-BY','DE-BE','DE-BB','DE-HB','DE-HH','DE-HE','DE-MV','DE-NI','DE-NW',
                 'DE-RP','DE-SL','DE-SN','DE-ST','DE-SH','DE-TH')))
            value = states.get(value, value)
        assert target['address'][0].get(key) == value, 'Localized patient address changed'
    for key in ('birthDate','gender','deceasedDateTime','deceasedBoolean'):
        assert target.get(key) == source.get(key), 'Patient clinical fact changed: '+key
    return {'identity':'verified synthetic replacement', 'clinicalFacts':'preserved'}
