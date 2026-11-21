import test from 'node:test'
import assert from 'node:assert/strict'
import { nextResourceQuery, serverMessage } from '../src/fhir-servers.ts'

test('pagination accepts only the selected FHIR base and returns relative parameters', () => {
  assert.deepEqual(nextResourceQuery('https://example.org/fhir/Patient?_count=20&page=2', 'https://example.org/fhir'), { path: 'Patient', query: '_count=20&page=2' })
  assert.deepEqual(nextResourceQuery('https://example.org/fhir/?_getpages=opaque', 'https://example.org/fhir'), { path: '', query: '_getpages=opaque' })
  assert.deepEqual(nextResourceQuery('https://example.org/fhir?_getpages=opaque', 'https://example.org/fhir'), { path: '', query: '_getpages=opaque' })
  for (const link of ['https://other.org/fhir/Patient', 'http://example.org/fhir/Patient', 'https://example.org/admin', 'https://example.org/fhir-other/Patient', 'https://example.org/fhir/../admin', 'https://user:secret@example.org/fhir/Patient', 'javascript:alert(1)']) {
    assert.equal(nextResourceQuery(link, 'https://example.org/fhir'), null, link)
  }
})
test('authentication and capability failures have concrete messages in both languages', () => {
  assert.match(serverMessage('login_required', 'de'), /Browser/)
  assert.match(serverMessage('forbidden', 'de'), /Berechtigung/)
  assert.match(serverMessage('read_only', 'en'), /Upload not supported/)
  assert.match(serverMessage('untrusted secret', 'en'), /could not complete/)
  assert.doesNotMatch(serverMessage('untrusted secret', 'en'), /secret/)
})
