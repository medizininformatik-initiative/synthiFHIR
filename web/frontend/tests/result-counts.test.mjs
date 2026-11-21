import { test } from 'node:test'
import assert from 'node:assert/strict'
import { resultCounts } from '../src/result-counts.ts'

const statistics = {
  resources: { Patient: 1, Encounter: 3, Organization: 2 },
  distinctIds: { Patient: 1, Encounter: 3, Organization: 1 },
  encounters: [['system', 'IMP', 'Main encounter', 1], ['system', 'IMP', 'Sub-encounter', 2]],
}
const dataset = { inspection: { patients: 1, uniqueResources: 5, resourceInstances: 6 }, statistics }

test('standard and bulk count main encounters and resource occurrences identically', () => {
  assert.deepEqual(resultCounts([dataset]), { patients: 1, cases: 1, resources: 6 })
  assert.deepEqual(resultCounts([{ ...dataset, kind: 'bulk' }]), resultCounts([dataset]))
})
test('bulk packages add occurrences rather than claiming globally distinct IDs', () => {
  assert.deepEqual(resultCounts([{ ...dataset, kind: 'bulk' }, { ...dataset, kind: 'bulk' }]), { patients: 2, cases: 2, resources: 12 })
})
test('KDS variants are not summed and unavailable counts stay unknown', () => {
  assert.deepEqual(resultCounts([dataset, dataset]), {})
  assert.deepEqual(resultCounts([]), {})
  assert.deepEqual(resultCounts([{ inspection: { patients: 1, uniqueResources: 5 } }]), { patients: 1, cases: undefined, resources: undefined })
  assert.equal(resultCounts([{ ...dataset, kind: 'bulk' }, { kind: 'bulk', inspection: { patients: 1 } }]).resources, undefined)
})
test('saved occurrence count is used when detailed statistics are unavailable', () => {
  assert.deepEqual(resultCounts([{ inspection: dataset.inspection }]), { patients: 1, cases: undefined, resources: 6 })
})
