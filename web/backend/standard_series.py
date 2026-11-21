"""Publish a continuation checkpoint after a complete standard Synthea run."""
import json

from bulk_identifiers import Registry
import bulk_continuation
import store


def sources(folder, snapshot):
    if snapshot['generation'].get('outputMode') == 'synthea':
        return sorted((folder / 'output/run-synthea/fhir').glob('*.json'))
    return sorted((folder / 'output').glob('*/details/sources/synthea/fhir/*.json'))


def complete(folder, snapshot):
    native = snapshot['generation'].get('outputMode') == 'synthea'
    patients = []
    for path in sources(folder, snapshot):
        bundle = json.loads(path.read_text())
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            if resource.get('resourceType') == 'Patient':
                if not resource.get('id'):
                    raise ValueError('Generated patient has no ID; cannot continue this series')
                patients.append(resource['id'])
    if not patients or len(set(patients)) != len(patients):
        raise ValueError('Expected distinct generated patients for series continuation')
    if len(patients) < snapshot['generation']['population']:
        raise ValueError('Synthea did not generate the requested patient count')
    counters = {} if native else json.loads((folder / 'series-counters.json').read_text())
    inherited = snapshot.get('continuation', {}).get('identifierHistorySha256')
    seed = folder / 'series-identifiers.sqlite'
    if inherited and (not seed.is_file() or store.digest(seed) != inherited):
        raise ValueError('Series identifier history is missing or changed')
    registry = Registry(folder / 'series-working.sqlite', seed if inherited else None)
    try:
        registry.db.execute('CREATE TABLE IF NOT EXISTS source_patients (id TEXT PRIMARY KEY)')
        with registry.db:
            for patient in patients:
                if registry.db.execute('SELECT 1 FROM source_patients WHERE id=?', (patient,)).fetchone():
                    raise ValueError('Synthea generated a patient already present in this series')
                registry.db.execute('INSERT INTO source_patients VALUES (?)', (patient,))
        if not native:
            registry.include(folder / 'series-registrations.jsonl')
        registry.save(folder / 'bulk-identifier-history.sqlite')
    finally:
        registry.close()
    # Includes overflow patients; it is the actual number added to this series.
    (folder / 'series-patients.json').write_text(json.dumps(patients))
    checkpoint = bulk_continuation.completion(folder, snapshot, counters)
    pending = folder / 'series-completion.tmp'
    pending.write_text(json.dumps(checkpoint))
    pending.replace(folder / bulk_continuation.STATE_FILE)
