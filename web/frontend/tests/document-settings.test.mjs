import test from 'node:test'
import assert from 'node:assert/strict'
import { defaults, importConfiguration, restoreBrowserDraft } from '../src/configuration.ts'
import { exportPropertiesConfiguration, parsePropertiesConfiguration } from '../src/configuration-properties.ts'
import { statusHints, typePresets, nlpPresets } from '../src/document-settings.ts'

test('document lists preserve free entries, order, duplicates and inactive values in all exports', () => {
  const config = defaults()
  config.values['resource.DocumentReference.enabled'] = false
  config.values['document.type.values'] = [...typePresets.combined, { system: 'urn:free', code: 'a,b', display: 'A\nB\\C' }]
  config.values['document.nlp.values'] = ['deid', 'custom,code', 'deid']
  for (const language of ['de', 'en']) assert.deepEqual(parsePropertiesConfiguration(exportPropertiesConfiguration(config, language)), config)
  assert.deepEqual(importConfiguration(JSON.stringify(config)), config)
  assert.deepEqual(restoreBrowserDraft(JSON.stringify(config)), config)
})
test('status hints preserve unknown and incomplete status combinations', () => {
  const codes = ['deid', 'custom']
  assert.deepEqual(statusHints(codes).map(h => h.key), ['document.parent', 'document.unknown'])
  assert.deepEqual(codes, ['deid', 'custom'])
  assert.deepEqual(statusHints(nlpPresets.surrogated), [])
})
