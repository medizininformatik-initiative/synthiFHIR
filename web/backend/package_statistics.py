"""English package reports collected from bundles already parsed for export."""
from collections import Counter

CLASS_NAMES = {'AMB': 'ambulatory', 'IMP': 'inpatient', 'EMER': 'emergency',
               'OBSENC': 'observation', 'SS': 'short stay', 'HH': 'home health', 'VR': 'virtual'}
CONTACT_NAMES = {'einrichtungskontakt': 'Facility contact', 'abteilungskontakt': 'Department contact',
                 'versorgungsstellenkontakt': 'Care unit contact'}


class Statistics:
    def __init__(self):
        self.bundles = 0
        self.resources = Counter()
        self.encounters = Counter()
        self.contacts = Counter()

    def add(self, bundle):
        self.bundles += 1
        for entry in bundle.get('entry', []):
            resource = entry.get('resource')
            if not resource:
                continue
            kind = resource['resourceType']
            self.resources[kind] += 1
            if kind != 'Encounter':
                continue
            coding = resource.get('class') or {}
            system, code = coding.get('system', 'not specified'), coding.get('code', 'not specified')
            hierarchy = 'Sub-encounter' if resource.get('partOf') else 'Main encounter'
            self.encounters[system, code, hierarchy] += 1
            levels = sorted({c.get('code', 'not specified') for t in resource.get('type', [])
                             for c in t.get('coding', [])
                             if c.get('system') == 'http://fhir.de/CodeSystem/Kontaktebene'})
            level = ' + '.join(CONTACT_NAMES.get(code, code) for code in levels) or 'not specified'
            self.contacts[level, system, code, hierarchy] += 1

    def snapshot(self):
        return {'bundles': self.bundles, 'resources': dict(self.resources),
                'encounters': [[*key, value] for key, value in sorted(self.encounters.items())],
                'contacts': [[*key, value] for key, value in sorted(self.contacts.items())]}


def report_path(package):
    stem = package.name.removesuffix('.gz').removesuffix('.ndjson')
    return package.with_name(stem + '-statistics.txt')


def write(package, data):
    resources = data['resources']
    lines = ['Resource statistics', f'Package: {package.name}',
             f'Transaction bundles: {data["bundles"]:,}', f'Patient resources: {resources.get("Patient", 0):,}',
             f'Total resources: {sum(resources.values()):,}', '',
             'Counting method: Counts resource occurrences in Bundle.entry.resource, not distinct IDs.',
             'Transaction bundles are transport envelopes and are counted separately.',
             'Main encounters: without partOf. Sub-encounters: with partOf (all nested levels).',
             'Contained resources are not counted as standalone resources.', '', 'RESOURCE TYPES']
    lines.extend(f'{kind}: {count:,}' for kind, count in sorted(resources.items()))
    lines.extend(['', 'ENCOUNTERS BY CLASS (Total / Main encounters / Sub-encounters)'])
    encounters = {(system, code, hierarchy): n for system, code, hierarchy, n in data['encounters']}
    for system, code in sorted({(system, code) for system, code, _ in encounters}):
        main = encounters.get((system, code, 'Main encounter'), 0)
        sub = encounters.get((system, code, 'Sub-encounter'), 0)
        lines.append(f'{code} ({CLASS_NAMES.get(code, code)}; {system}): {main + sub:,} / {main:,} / {sub:,}')
    lines.extend(['', 'ENCOUNTERS BY CONTACT LEVEL, CLASS AND HIERARCHY'])
    lines.extend(f'{level} | {code} ({system}) | {hierarchy}: {count:,}'
                 for level, system, code, hierarchy, count in data['contacts'])
    destination = report_path(package)
    pending = destination.with_name('.' + destination.name + '.partial')
    pending.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    pending.replace(destination)
