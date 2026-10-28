import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveGenerationSeeds } from '../src/generation-seeds.ts'

const manual = { patientSeed: '-9223372036854775808', clinicianSeed: '42', singlePersonSeed: '', population: 10 }
test('manual seeds remain exact, and UI-only seed mode is excluded from the request', () => {
  assert.deepEqual(resolveGenerationSeeds({ ...manual, timestampSeeds: false }, 123), manual)
})
test('disabling timestamp mode preserves the displayed seeds on subsequent starts', () => {
  const draft = { ...manual, timestampSeeds: true }
  resolveGenerationSeeds(draft, 1793222166069)
  draft.timestampSeeds = false
  for (const timestamp of [1793223172670, 1793224172670]) {
    assert.deepEqual(resolveGenerationSeeds(draft, timestamp), manual)
    assert.equal(draft.patientSeed, manual.patientSeed)
    assert.equal(draft.clinicianSeed, manual.clinicianSeed)
  }
})
test('timestamp resolves at submission without modifying the editor draft', () => {
  const draft = { ...manual, timestampSeeds: true }
  const result = resolveGenerationSeeds(draft, 1791374400123)
  assert.equal(result.patientSeed, '1791374400123')
  assert.equal(result.clinicianSeed, '1791374400123')
  assert.equal(result.singlePersonSeed, '')
  assert.equal(result.timestampSeeds, undefined)
  assert.deepEqual(draft, { ...manual, timestampSeeds: true })
  assert.deepEqual(resolveGenerationSeeds(draft, 1791374400123), result)
  assert.notEqual(resolveGenerationSeeds(draft, 1791374400124).patientSeed, result.patientSeed)
})
test('optional single-person seed is resolved only for an explicitly configured single person', () => {
  assert.equal(resolveGenerationSeeds({ ...manual, timestampSeeds: true, population: 1, singlePersonSeed: '7' }, 123).singlePersonSeed, '123')
  assert.equal(resolveGenerationSeeds({ ...manual, timestampSeeds: true, singlePersonSeed: '7' }, 123).singlePersonSeed, '')
  assert.equal(resolveGenerationSeeds({ ...manual, timestampSeeds: true, population: 1 }, 123).singlePersonSeed, '')
})
