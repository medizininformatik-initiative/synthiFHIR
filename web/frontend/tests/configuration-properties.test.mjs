import test from 'node:test'
import assert from 'node:assert/strict'
import { defaults, importConfiguration, options, darFields } from '../src/configuration.ts'
import { exportPropertiesConfiguration, importRunProperties } from '../src/configuration-properties.ts'

const rule = { id: 'a152e771-3d5a-4cb1-9866-35fa6d91fd83', enabled: true,
  resources: ['Patient'], system: 'urn:example:ä', pattern: '{{id}}-{count:08}-{hash}' }
const exported = (config, lang = 'en') => exportPropertiesConfiguration(config, lang)

test('both export languages preserve every option and have identical assignments', () => {
  const config = defaults()
  const de = exported(config, 'de'), en = exported(config)
  assert.deepEqual(importConfiguration(de), config)
  assert.deepEqual(importConfiguration(en), config)
  assert.match(de, /Standardwert/)
  assert.match(en, /Default/)
  assert.doesNotMatch(en, /Standardwert|Zulässige Werte|Inaktiv/)
  const assignments = text => text.split('\n').filter(l => /^(# )?[A-Z][A-Z0-9_]* =/.test(l))
  assert.deepEqual(assignments(de), assignments(en))
  for (const o of options) assert.match(en, new RegExp(`^(# )?${o.propertyName} =`, 'm'))
})

test('disabled controls and unavailable choices retain values; false and none stay active', () => {
  const config = defaults()
  config.values['resource.MedicationAdministration.enabled'] = false
  config.values['medication.MedicationAdministration.treatment'] = 'add-statement'
  config.values['medication.MedicationStatement.treatment'] = 'replace-administration'
  config.values['reference.Condition.encounter'] = 'none'
  config.values['resource.Encounter.enabled'] = false
  const text = exported(config)
  assert.match(text, /^MEDICATION_ADMINISTRATION_ENABLED = false$/m)
  assert.match(text, /^# MEDICATION_ADMINISTRATION_TREATMENT = add-statement$/m)
  assert.match(text, /^# MEDICATION_STATEMENT_TREATMENT = replace-administration$/m)
  assert.deepEqual(importConfiguration(text), config)
  config.values['resource.Encounter.enabled'] = true
  assert.match(exported(config), /^REFERENCE_CONDITION_ENCOUNTER = none$/m)
  config.values['resource.MedicationAdministration.enabled'] = true
  assert.match(exported(config), /^MEDICATION_ADMINISTRATION_TREATMENT = add-statement$/m)
})

test('DAR and identifiers survive disabled resources, rules, and escaped text', () => {
  const config = defaults()
  config.values['resource.Patient.mode'] = 'reference-only'
  config.values['ids.patient.prefix'] = '\u00a0  ä \\ folder\n= # !\t\r\f'
  config.dar['Patient.name.family'] = { mode: 'overwrite', code: 'masked' }
  config.identifierRules = [rule, { ...rule, id: 'da0363a7-c29e-4308-b5df-e9bfc30c95da', enabled: false,
    pattern: '\u2028 leading \\ slash\n{patientId}=x: ü ' }]
  const text = exported(config)
  assert.match(text, /^# DAR_PATIENT_NAME_FAMILY = masked$/m)
  assert.match(text, /^IDENTIFIER_RULE_2_ENABLED = false$/m)
  assert.match(text, /^# IDENTIFIER_RULE_1_PATTERN =/m)
  assert.deepEqual(importConfiguration(text), config)
  assert.deepEqual(importConfiguration(text.replaceAll('\n', '\r\n')), config)
  assert.equal(text.split('\n').filter(l => /^(# )?DAR_[A-Z0-9_]+ =/.test(l)).length, darFields.length)
})

test('rejects malformed or ambiguous properties before replacing the draft', () => {
  const prefix = 'CONFIGURATION_VERSION = 1\n'
  for (const text of ['', 'CONFIGURATION_VERSION = 2', '# CONFIGURATION_VERSION = 1',
    prefix + 'UNKNOWN = true', prefix + '# UNKNOWN = true',
    prefix + 'PATIENT_MODE = neither\n# PATIENT_MODE = reference-only',
    prefix + 'CHECK_INPUT_CONSISTENCY = yes', prefix + 'PATIENT_MODE = invalid',
    prefix + 'PID_PREFIX = broken\\', prefix + 'PID_PREFIX = \\uXYZW',
    prefix + 'DAR_PATIENT_NAME_FAMILY = ', prefix + 'IDENTIFIER_RULE_1_ENABLED = true',
    prefix + 'this is not an assignment', prefix + 'OUTPUT_FORMATS = JSON,JSON'])
    assert.throws(() => importConfiguration(text), text)
  assert.equal(importConfiguration(prefix + 'PID_PREFIX = \\u00e4').values['ids.patient.prefix'], 'ä')
})

test('complete allowed option values roundtrip and re-export deterministically', () => {
  for (const o of options) {
    for (const value of o.choices ?? [o.default]) {
      const config = defaults()
      config.values[o.id] = o.type === 'set' ? [value] : value
      const text = exported(config)
      assert.deepEqual(importConfiguration(text), config, o.id)
      assert.equal(exported(importConfiguration(text)), text, o.id)
    }
  }
})

test('diagnosis exports omit the retired inheritance switch and preserve inactive roles', () => {
  const config = defaults()
  for (const lang of ['de', 'en']) {
    const text = exported(config, lang)
    assert.doesNotMatch(text, /ADD_MISSING_DIAGNOSES_FROM_SUPER_ENCOUNTER/)
    assert.match(text, /^CONTACT_DIAGNOSES_ROLES = CC,CM,AD,DD,pre-op,post-op,billing$/m)
    assert.deepEqual(importConfiguration(text + '\nADD_MISSING_DIAGNOSES_FROM_SUPER_ENCOUNTER = true\n'), config)
  }
  config.values['resource.Condition.enabled'] = false
  assert.match(exported(config), /^# CONTACT_DIAGNOSES_ROLES = /m)
})

test('encounter policies, scoped DAR and identifier selectors roundtrip in both languages', () => {
  const config = defaults()
  config.values['resource.Encounter.ambulatory.endPolicy'] = 'quarter-end'
  config.values['resource.Encounter.ambulatory.endApplication'] = 'missing-input-end'
  config.dar['Encounter.ambulatory.period.end'] = {mode: 'overwrite', code: 'unknown'}
  config.identifierRules = [{...rule, resources: ['Encounter.ambulatory', 'Encounter.inpatient']}]
  for (const language of ['de', 'en']) {
    assert.deepEqual(importConfiguration(exported(config, language)), config)
    config.values['resource.Encounter.ambulatory.enabled'] = false
    const text = exported(config, language)
    assert.match(text, /^# ENCOUNTER_AMBULATORY_END_POLICY = quarter-end$/m)
    assert.match(text, /^# DAR_ENCOUNTER_AMBULATORY_PERIOD_END = unknown$/m)
    assert.deepEqual(importConfiguration(text), config)
    config.values['resource.Encounter.ambulatory.enabled'] = true
  }
})

test('stored default-run properties load into the editor with preserved settings', () => {
  const config = importRunProperties('PID_PREFIX=site-\nSET_REFERENCE_FROM_CONDITION_TO_ENCOUNTER=false\nSET_REFERENCE_FROM_PROCEDURE_CONDITION_TO_ENCOUNTER=false\nSYNTHEA_VERSION_OUTPUT=Jahr\nADD_MISSING_DIAGNOSES_FROM_SUPER_ENCOUNTER=false\n')
  assert.equal(config.values['ids.patient.prefix'], 'site-')
  assert.equal(config.values['reference.Condition.encounter'], 'none')
  assert.equal(config.values['reference.Procedure.encounter'], 'none')
  assert.equal(config.values['terminology.versionOutput'], 'catalogue-year')
  assert.deepEqual(importRunProperties(exported(config)), importConfiguration(exported(config)))
})

test('free identifier codings, use and conditional DAR round-trip in both languages', () => {
  const config = defaults()
  config.identifierRules = [{ ...rule, resources: ['Observation', 'Patient'], use: 'secondary', typeText: 'Aufnahme',
    typeCodings: [{ system: 'http://terminology.hl7.org/CodeSystem/v2-0203', code: 'VN', display: 'Visit number' }, { system: 'urn:test', code: 'wrong', display: 'Eigene Bezeichnung' }] }]
  config.dar['Patient.birthDate'] = { mode: 'overwrite', code: 'unknown', onlyWhenMissing: true }
  config.values['resource.Encounter.stationServiceProvider'] = 'contained'
  config.values['resource.Encounter.stationLocation'] = false
  for (const lang of ['de', 'en']) assert.deepEqual(importConfiguration(exported(config, lang)), config)
})

test('counter start survives properties export for a scoped contact rule', () => {
  const config = defaults()
  config.identifierRules = [{ id: 'a152e771-3d5a-4cb1-9866-35fa6d91fd83', enabled: true,
    resources: ['Encounter.inpatient.department'], system: 'urn:test', pattern: '{count:08}', countStart: 500 }]
  const text = exportPropertiesConfiguration(config, 'de')
  assert.match(text, /^IDENTIFIER_RULE_1_COUNT_START = 500$/m)
  assert.deepEqual(importConfiguration(text).identifierRules, config.identifierRules)
})

 test('custom DAR codes roundtrip through both export languages', () => {
  const config = defaults()
  for (const code of ['custom-code', 'unchanged', '!dar:custom']) {
  config.dar['Patient.name.family'] = { mode: 'overwrite', code, onlyWhenMissing: true }
  for (const language of ['de', 'en']) assert.deepEqual(importConfiguration(exported(config, language)), config)
  }
})

test('case identifiers roundtrip with scope and restricted pattern tokens', () => {
  const config = defaults()
  config.identifierRules = [{ ...rule, scope: 'case', resources: ['Encounter', 'Observation'],
    pattern: '{{case}}-{patientId}-{caseId}' }]
  for (const lang of ['de', 'en']) assert.deepEqual(importConfiguration(exported(config, lang)), config)
  for (const pattern of ['{count}', '{resourceId}', '{resourceType}', '{hash}', '{iteration}']) {
    const invalid = structuredClone(config)
    invalid.identifierRules[0].pattern = pattern
    assert.throws(() => importConfiguration(JSON.stringify(invalid)))
  }
})

test('support identities default to shared and roundtrip independently', () => {
  for (let mask = 0; mask < 8; mask++) {
    const config = defaults()
    ;['Organization', 'Location', 'Medication'].forEach((type, index) => {
      assert.equal(config.values[`resource.${type}.identity`], 'shared')
      config.values[`resource.${type}.identity`] = mask & (1 << index) ? 'separate' : 'shared'
    })
    assert.deepEqual(importConfiguration(exported(config)), config)
  }
})
