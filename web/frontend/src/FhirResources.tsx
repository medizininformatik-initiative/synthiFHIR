import { useEffect, useRef, useState } from 'react'
import { Button } from './components/ui/button'
import type { Language } from './i18n'
import { nextResourceQuery, serverMessage, serverRequest, type FhirTarget } from './fhir-servers'

type Resource = { resourceType?: string; id?: string; [key: string]: unknown }
type Result = Resource & { total?: number; entry?: { resource?: Resource }[]; link?: { relation: string; url: string }[] }
export function FhirResources({ target, language }: { target?: FhirTarget; language: Language }) {
  const de = language === 'de'
  const [kind, setKind] = useState('Patient')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const requestId = useRef(0)
  useEffect(() => { requestId.current++; setResult(null); setError(''); setBusy(false) }, [target?.id, target?.address, target?.revision])
  async function load(path = kind, search = query || '_count=20') {
    if (!target) return
    const current = ++requestId.current
    setBusy(true); setError(''); setResult(null)
    try {
      const response = await serverRequest(`fhir-targets/${target.id}/resources?${new URLSearchParams({ path, query: search })}`, undefined, 'GET')
      if (current === requestId.current) setResult(response)
    } catch (error) { if (current === requestId.current) setError(serverMessage(error instanceof Error ? error.message : 'server_error', language)) }
    finally { if (current === requestId.current) setBusy(false) }
  }
  const nextLink = result?.link?.find(link => link.relation === 'next')?.url
  const next = nextLink && target ? nextResourceQuery(nextLink, target.address) ?? (target.url ? nextResourceQuery(nextLink, target.url) : null) : null
  const entries = result?.resourceType === 'Bundle' ? result.entry?.flatMap(entry => entry.resource ? [entry.resource] : []) ?? [] : result ? [result] : []
  return <details className="rounded-xl border border-slate-200 p-4">
    <summary className="cursor-pointer font-semibold">{de ? 'Ressourcen auf dem Server ansehen' : 'View resources on the server'}</summary>
    <p className="my-3 text-sm text-slate-600">{de ? 'Liest Ressourcen vom oben ausgewählten Server. Die Daten werden nicht verändert.' : 'Reads resources from the server selected above. Data is not changed.'}</p>
    {!target ? <p className="text-sm">{de ? 'Zuerst einen Server auswählen.' : 'Choose a server first.'}</p> : <>
      <p className="mb-3 text-sm font-medium">{target.name} · <span className="break-all font-normal">{target.address}</span></p>
      <div className="flex flex-wrap items-end gap-3"><label className="text-sm">{de ? 'Ressourcentyp oder ID' : 'Resource type or ID'}<input list="fhir-resource-types" className="mt-1 block rounded-lg border p-2" value={kind} onChange={event => setKind(event.target.value)}/><datalist id="fhir-resource-types">{(target.status?.resourceTypes ?? ['Patient', 'Encounter', 'DocumentReference']).map(type => <option key={type}>{type}</option>)}</datalist><span className="mt-1 block text-xs text-slate-600">{de ? 'Zum Beispiel Patient oder Patient/123.' : 'For example Patient or Patient/123.'}</span></label>
      <Button disabled={busy || !kind} onClick={() => void load()}>{de ? 'Ressourcen anzeigen' : 'Show resources'}</Button></div>
      <details className="my-3 text-sm"><summary className="cursor-pointer">{de ? 'Suche eingrenzen' : 'Narrow the search'}</summary><label className="mt-2 block">{de ? 'FHIR-Suchparameter' : 'FHIR search parameters'}<input className="mt-1 block w-full rounded-lg border p-2" value={query} onChange={event => setQuery(event.target.value)} placeholder="_count=20&name=Mustermann"/><span className="mt-1 block text-xs text-slate-600">{de ? 'Parameter ohne Fragezeichen, zum Beispiel name=Mustermann. Standardmäßig werden bis zu 20 Ergebnisse angefordert.' : 'Parameters without a question mark, for example name=Smith. Requests up to 20 results by default.'}</span></label></details>
    </>}
    {busy && <p role="status">{de ? 'Ressourcen werden geladen …' : 'Loading resources …'}</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {result && <div className="space-y-2"><p role="status" className="text-sm">{entries.length} {de ? 'angezeigt' : 'shown'}{result.total !== undefined ? ` · ${result.total} ${de ? 'insgesamt' : 'total'}` : ''}</p>
      {entries.map((resource, index) => <details key={`${resource.id}-${index}`} className="rounded-lg bg-slate-50 p-3 text-sm"><summary className="cursor-pointer">{resource.resourceType} · {resource.id ?? (de ? 'Ohne ID' : 'No ID')}</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(resource, null, 2)}</pre></details>)}
      {next && <Button variant="outline" disabled={busy} onClick={() => void load(next.path, next.query)}>{de ? 'Nächste Ergebnisse' : 'Next results'}</Button>}
      {nextLink && !next && <p className="text-sm text-amber-800">{de ? 'Der Link zur nächsten Seite passt nicht zur gespeicherten Serveradresse. Bitte die Suche eingrenzen.' : 'The next-page link does not match the saved server address. Please narrow the search.'}</p>}
    </div>}
  </details>
}
