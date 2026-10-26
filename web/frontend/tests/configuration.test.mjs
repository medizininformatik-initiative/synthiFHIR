import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import {
  defaults,
  insertPatternToken,
  contract,
  resourceOptions,
  options,
  darFields,
  darResource,
  optionEnabled,
  resourceEnabled,
  resourceNavigationStatus,
  unmetDependencies,
  importConfiguration,
  restoreBrowserDraft,
  problems,
  patternTokens,
  previewIdentifier
} from '../src/configuration.ts'

test('browser drafts retain incomplete edits while executable imports reject them', () => {
  const config = defaults()
  config.values['output.patientsPerFile'] = null
  config.identifierRules.push({
    id: 'fb3fb97a-367b-4628-ad48-44f958f9f191',
    enabled: true, resources: [], system: '', pattern: '{unfinished'
  })
  const text = JSON.stringify(config)
  assert.deepEqual(restoreBrowserDraft(text), config)
  assert.throws(() => importConfiguration(text))
  assert.throws(() => restoreBrowserDraft('{"schemaVersion":2}'))
})

test('defaults cover every option and survive a configuration roundtrip', () => {
  const config = defaults()
  assert.equal(problems(config).length, 0)
  assert.equal(Object.keys(config.values).length, options.length)
  assert.deepEqual(importConfiguration(JSON.stringify(config)), config)
  assert.deepEqual(importConfiguration('{"schemaVersion":1}'), config)
})
test('dependency chains disable controls and choices without losing stored selections', () => {
  const config = defaults()
  config.values['contact.department.enabled'] = false
  assert.equal(
    unmetDependencies(
      [{ option: 'contact.department.enabled', equals: true }],
      config.values
    ).length,
    1
  )
  assert.equal(config.values['reference.Procedure.encounter'], 'department')
  config.values['resource.Encounter.enabled'] = false
  assert.equal(optionEnabled('contact.facility.enabled', config.values), false)
  assert.equal(optionEnabled('contact.diagnoses.levels', config.values), false)
  assert.equal(config.values['contact.facility.enabled'], true)
  config.values['resource.Encounter.enabled'] = true
  assert.equal(optionEnabled('contact.facility.enabled', config.values), true)
  config.values['resource.Medication.mode'] = 'reference-only'
  assert.equal(
    optionEnabled('resource.Medication.existingReference', config.values),
    true
  )
  assert.equal(resourceEnabled('Medication', config.values), false)
})
test('DAR codes accept free text; incomplete overwrite and unknown fields fail', () => {
  const config = defaults()
  const field = darFields.find((f) => !f.allowedCodes.includes('as-text'))
  config.dar[field.id] = { mode: 'overwrite', code: field.allowedCodes[0] }
  assert.equal(problems(config).length, 0)
  config.dar[field.id].code = 'made-up'
  assert.equal(problems(config).length, 0)
  assert.deepEqual(importConfiguration(JSON.stringify(config)), config)
  config.dar[field.id].code = ''
  assert.ok(problems(config).length)
  assert.throws(() =>
    importConfiguration(
      '{"schemaVersion":1,"dar":{"no-such-field":{"mode":"unchanged"}}}'
    )
  )
})
test('imports reject unsupported versions, wrong types, unknown and duplicate members', () => {
  for (const text of [
    '{"schemaVersion":2}',
    '{"schemaVersion":1,"values":{"no-such-option":true}}',
    '{"schemaVersion":1,"values":{"checks.fhirValidation":"false"}}',
    '{"schemaVersion":1,"schemaVersion":1}',
    '{"schemaVersion":1,"values":{"checks.fhirValidation":true,"checks.\\u0066hirValidation":false}}',
    '{"schemaVersion":1,"values":{"output.patientsPerFile":0}}'
  ])
    assert.throws(() => importConfiguration(text))
  const config = defaults()
  config.values['ids.patient.prefix'] = 'Braces {[]} and escaped "quotation"'
  assert.deepEqual(importConfiguration(JSON.stringify(config)), config)
})
const rule = {
  id: 'a152e771-3d5a-4cb1-9866-35fa6d91fd83',
  enabled: true,
  resources: ['Patient'],
  system: 'urn:example:generated',
  pattern:
    '{{id}}-{count:08}-{patientId}-{resourceType}-{resourceId}-{iteration}-{hash}'
}
test('identifier preview uses agreed padding, escaped braces and fixed hash encoding', async () => {
  const input = [rule.id, 'Patient', 'example-1', '0']
    .map((part) => `${Buffer.byteLength(part)}:${part}`)
    .join('')
  const hash = createHash('sha256').update(input).digest('hex').slice(0, 32)
  assert.equal(
    await previewIdentifier(rule),
    `{id}-00000001-patient-1-Patient-example-1-0-${hash}`
  )
  assert.notEqual(
    await previewIdentifier({
      ...rule,
      id: 'da0363a7-c29e-4308-b5df-e9bfc30c95da'
    }),
    await previewIdentifier(rule)
  )
  for (const pattern of ['{unknown}', '{count:8}', '{count:00}', '{count', '}'])
    assert.throws(() => patternTokens(pattern))
})
test('rule IDs are unique and identifier rules validate before saving', () => {
  const config = defaults()
  config.identifierRules = [rule]
  assert.equal(problems(config).length, 0)
  assert.deepEqual(
    importConfiguration(JSON.stringify(config)).identifierRules,
    [rule]
  )
  config.identifierRules.push({ ...rule, id: rule.id.toUpperCase() })
  assert.ok(problems(config).some((p) => p.key === 'app.config.duplicateRule'))
  config.identifierRules = [{ ...rule, pattern: '{bad}' }]
  assert.ok(
    problems(config).some((p) => p.key === 'config.error.invalidPattern')
  )
  config.identifierRules = [{ ...rule, resources: [], enabled: false }]
  assert.ok(problems(config).length)
})

test('resource groups cover each resource option exactly once', () => {
  const ids = contract.resources.flatMap((r) =>
    resourceOptions(r.id).map((o) => o.id)
  )
  assert.equal(new Set(ids).size, ids.length)
  assert.deepEqual(
    ids.sort(),
    options
      .filter((o) => o.section === 'resources')
      .map((o) => o.id)
      .sort()
  )
})

test('DAR distinguishes laboratory and vital-sign Observation selections', () => {
  const config = defaults()
  config.values['resource.Observation.laboratory.enabled'] = false
  config.values['resource.Observation.vitalSigns.enabled'] = true
  const laboratory = darFields.find((f) => f.id.startsWith('Laboratory.'))
  const vitalSigns = darFields.find((f) => f.id.startsWith('VitalSigns.'))
  assert.equal(resourceEnabled(darResource(laboratory), config.values), false)
  assert.equal(resourceEnabled(darResource(vitalSigns), config.values), true)
})

test('pattern tokens append, insert at the cursor and replace selected text', () => {
  assert.deepEqual(insertPatternToken('ID-', '{count}', null), {
    value: 'ID-{count}',
    cursor: 10
  })
  assert.deepEqual(
    insertPatternToken('ID--suffix', '{hash}', { start: 3, end: 3 }),
    { value: 'ID-{hash}-suffix', cursor: 9 }
  )
  assert.deepEqual(
    insertPatternToken('ID-old-suffix', '{patientId}', { start: 3, end: 6 }),
    { value: 'ID-{patientId}-suffix', cursor: 14 }
  )
  assert.equal(
    insertPatternToken('', '{count}', { start: 99, end: 99 }).value,
    '{count}'
  )
})


test('saved drafts retain their settings when the duplicate procedure assignment is removed', () => {
  const draft = defaults()
  draft.values['reference.Procedure.encounter'] = 'facility'
  draft.values['reference.procedureDiagnosis.encounter'] = 'department'
  draft.values['ids.patient.prefix'] = 'test-'
  const restored = importConfiguration(JSON.stringify(draft))
  assert.equal(restored.values['reference.Procedure.encounter'], 'facility')
  assert.equal(restored.values['ids.patient.prefix'], 'test-')
  assert.equal('reference.procedureDiagnosis.encounter' in restored.values, false)
  assert.equal(resourceOptions('Condition').some(o => o.id.includes('procedureDiagnosis')), false)
  draft.values['reference.procedureDiagnosis.encounter'] = 'invalid'
  assert.throws(() => importConfiguration(JSON.stringify(draft)))
})


test('omitted requests in saved drafts become deselected requests without losing other settings', () => {
  const draft = defaults()
  draft.values['medication.requestTreatment'] = 'omit'
  draft.values['resource.MedicationAdministration.enabled'] = true
  draft.values['ids.patient.prefix'] = 'demo-'
  const restored = importConfiguration(JSON.stringify(draft))
  assert.equal(restored.values['medication.requestTreatment'], 'retain')
  assert.equal(restored.values['resource.MedicationRequest.enabled'], false)
  assert.equal(restored.values['resource.MedicationAdministration.enabled'], true)
  assert.equal(restored.values['ids.patient.prefix'], 'demo-')
  assert.deepEqual(options.find(o => o.id === 'medication.requestTreatment').choices,
    ['retain', 'replace-administration', 'replace-statement'])
})

test('medication actions preserve saved additions and disable unavailable targets', () => {
  const draft = defaults()
  delete draft.values['medication.MedicationAdministration.treatment']
  delete draft.values['medication.MedicationStatement.treatment']
  draft.values['medication.MedicationAdministration.addMedicationStatement'] = true
  draft.values['medication.MedicationStatement.addMedicationAdministration'] = false
  const restored = importConfiguration(JSON.stringify(draft))
  assert.equal(restored.values['medication.MedicationAdministration.treatment'], 'add-statement')
  assert.equal(restored.values['medication.MedicationStatement.treatment'], 'retain')
  assert.equal('medication.MedicationAdministration.addMedicationStatement' in restored.values, false)
  for (const [source, target] of [
    ['MedicationAdministration', 'MedicationStatement'],
    ['MedicationStatement', 'MedicationAdministration']
  ]) {
    const option = options.find(o => o.id === `medication.${source}.treatment`)
    restored.values[`resource.${target}.enabled`] = false
    assert.equal(optionEnabled(option.id, restored.values), true)
    for (const choice of option.choices.filter(v => v !== 'retain'))
      assert.equal(unmetDependencies(option.choiceDependencies[choice], restored.values).length, 1)
    restored.values[`resource.${target}.enabled`] = true
  }
  restored.values['medication.MedicationAdministration.treatment'] = 'replace-statement'
  restored.values['medication.MedicationStatement.treatment'] = 'replace-administration'
  assert.deepEqual(importConfiguration(JSON.stringify(restored)), restored)
  draft.values['medication.MedicationAdministration.addMedicationStatement'] = 'yes'
  assert.throws(() => importConfiguration(JSON.stringify(draft)))
})

test('document encounter strategies roundtrip and preserve saved checkbox choices', () => {
  const strategyKey = 'reference.DocumentReference.assignmentStrategy'
  const legacyKey = 'reference.DocumentReference.knownInputContact'
  assert.equal(defaults().values[strategyKey], 'fill-missing')
  for (const strategy of ['explicit-only', 'fill-missing', 'timestamp-only']) {
    const config = defaults()
    config.values[strategyKey] = strategy
    assert.equal(importConfiguration(JSON.stringify(config)).values[strategyKey], strategy)
  }
  for (const [legacy, strategy] of [[true, 'fill-missing'], [false, 'timestamp-only']]) {
    const config = defaults()
    delete config.values[strategyKey]
    config.values[legacyKey] = legacy
    config.values['ids.patient.prefix'] = 'keep-'
    const imported = importConfiguration(JSON.stringify(config))
    assert.equal(imported.values[strategyKey], strategy)
    assert.equal(imported.values['ids.patient.prefix'], 'keep-')
    assert.equal(legacyKey in imported.values, false)
  }
  const invalid = defaults()
  invalid.values[strategyKey] = 'unknown'
  assert.throws(() => importConfiguration(JSON.stringify(invalid)))
  const option = options.find(o => o.id === strategyKey)
  invalid.values['resource.DocumentReference.enabled'] = false
  assert.equal(optionEnabled(option.id, invalid.values), false)
})

test('diagnosis roles depend on Conditions; saved inheritance flags are retired', () => {
  const config = defaults()
  assert.equal(optionEnabled('contact.diagnoses.roles', config.values), true)
  config.values['resource.Condition.enabled'] = false
  assert.equal(optionEnabled('contact.diagnoses.roles', config.values), false)
  config.values['contact.inheritDiagnoses'] = true
  const restored = importConfiguration(JSON.stringify(config))
  assert.equal('contact.inheritDiagnoses' in restored.values, false)
  assert.equal(restored.values['resource.Condition.enabled'], false)
})

test('encounter classes keep common settings and disable inactive end scopes', async () => {
  const config = defaults()
  for (const scope of ['ambulatory', 'inpatient']) {
    const prefix = `resource.Encounter.${scope}`
    assert.equal(optionEnabled(`${prefix}.endApplication`, config.values), false)
    config.values[`${prefix}.endPolicy`] = 'open'
    assert.equal(optionEnabled(`${prefix}.endApplication`, config.values), true)
    assert.equal(resourceEnabled(`Encounter.${scope}`, config.values), true)
    assert.ok(darFields.some(f => darResource(f) === `Encounter.${scope}`))
    config.values['resource.Encounter.enabled'] = false
    assert.equal(resourceEnabled(`Encounter.${scope}`, config.values), false)
    config.values['resource.Encounter.enabled'] = true
  }
  const rule = { id: 'a152e771-3d5a-4cb1-9866-35fa6d91fd83', enabled: true, resources: ['Encounter'], system: 'urn:test', pattern: '{resourceType}-{hash}' }
  for (const selector of ['Encounter.ambulatory', 'Encounter.inpatient.facility', 'Encounter.inpatient.department', 'Encounter.inpatient.ward-service']) {
    const scoped = {...rule, resources: [selector]}
    config.identifierRules = [scoped]
    assert.deepEqual(importConfiguration(JSON.stringify(config)).identifierRules, [scoped])
    assert.equal(await previewIdentifier(scoped), await previewIdentifier(rule))
  }
  for (const selector of ['Observation.laboratory', 'Observation.vitalSigns']) {
    const scoped = {...rule, resources: [selector]}
    config.identifierRules = [scoped]
    assert.deepEqual(importConfiguration(JSON.stringify(config)).identifierRules, [scoped])
    assert.equal(await previewIdentifier(scoped), await previewIdentifier({...rule, resources: ['Observation']}))
  }
  config.dar['Encounter.ambulatory.period.end'] = {mode: 'overwrite', code: ''}
  assert.deepEqual(restoreBrowserDraft(JSON.stringify(config)), config)
  assert.throws(() => importConfiguration(JSON.stringify(config)))
})

test('navigation status uses effective resource selections without mutating them', () => {
  const config = defaults()
  for (const resource of ['Patient', 'Location', 'Medication']) {
    assert.equal(resourceNavigationStatus(resource, config.values), 'generated')
    config.values[`resource.${resource}.mode`] = 'reference-only'
    assert.equal(resourceNavigationStatus(resource, config.values), 'referenced')
    config.values[`resource.${resource}.mode`] = 'neither'
    assert.equal(resourceNavigationStatus(resource, config.values), 'disabled')
  }
  config.values['resource.Observation.laboratory.enabled'] = false
  assert.equal(resourceNavigationStatus('Observation.laboratory', config.values), 'disabled')
  assert.equal(resourceNavigationStatus('Observation.vitalSigns', config.values), 'generated')
  config.values['resource.Encounter.enabled'] = false
  assert.equal(resourceNavigationStatus('Encounter.inpatient', config.values), 'parentDisabled')
  assert.equal(config.values['resource.Encounter.inpatient.enabled'], true)
  config.values['resource.Encounter.enabled'] = true
  assert.equal(resourceNavigationStatus('Encounter.inpatient', config.values), 'generated')
})

test('counter preview uses the configured starting value with literal braces', async () => {
  const rule = { id: 'a152e771-3d5a-4cb1-9866-35fa6d91fd83', enabled: true,
    resources: ['Patient'], system: 'urn:test', pattern: '{{{count:08}}}-{count}', countStart: 500 }
  assert.equal(await previewIdentifier(rule), '{00000500}-500')
})

test('organization navigation follows the encounter provider setting', () => {
  const config = defaults()
  for (const [mode, status] of [['none', 'disabled'], ['generate-reference', 'generated'], ['reference-only', 'referenced'], ['contained', 'generated']]) {
    config.values['resource.Encounter.stationServiceProvider'] = mode
    assert.equal(resourceNavigationStatus('Organization', config.values), status)
    config.values['resource.Encounter.enabled'] = false
    assert.equal(resourceNavigationStatus('Organization', config.values), 'parentDisabled')
    config.values['resource.Encounter.enabled'] = true
  }
})

test('all support identity controls are reachable in the resource editor', () => {
  assert.ok(resourceOptions('Encounter').some(o => o.id === 'resource.Organization.identity'))
  for (const type of ['Location', 'Medication'])
    assert.ok(resourceOptions(type).some(o => o.id === `resource.${type}.identity`))
})
