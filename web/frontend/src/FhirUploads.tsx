import { FhirServers } from './FhirServers'
import { FhirResources } from './FhirResources'
import { uploadError, serverMessage, type FhirTarget } from './fhir-servers'
import { datasetLabel, type DatasetContext } from './dataset-label'
import { useEffect, useState } from 'react'
import { ListExpansion } from './ListExpansion'
import { Button } from './components/ui/button'
import { translate, type Language, type TextKey } from './i18n'

type Target = FhirTarget
type Dataset = DatasetContext & { id: string; name: string; state: string; created: number; sourceName?: string; source?: string; size?: number; error?: string }
type Upload = { resumable?: boolean; id: string; state: string; created: number; descriptor: { target: Target; datasets: (DatasetContext & { id: string; name: string })[] }; result?: { streaming?: boolean; checkedBundles?: number; packages?: number; completedPackages?: number; currentPackage?: number; error?: string; acceptedBundles?: number; rejectedBundles?: number; otherResponses?: number; statusCounts?: Record<string, number>; serverErrors?: { file: string; bundle: number; status?: string; code?: string; diagnostics: string[] }[]; resource?: string; resources?: number; bundles?: number; mayHaveWritten?: boolean } }
const stateKeys: Record<string, TextKey> = { queued: 'app.upload.queued', preparing: 'app.upload.preparing', uploading: 'app.upload.uploading', succeeded: 'app.upload.succeeded', conflict: 'app.upload.conflict', failed: 'app.upload.failed', interrupted: 'app.upload.interrupted', cancelled: 'app.upload.cancelled' }
export function FhirUploads({ language, requestedSelection }: { language: Language; requestedSelection?: { ids: string[] } | null }) {
  const t = (key: TextKey, params?: Record<string, string | number>) => translate(language, key, params)
  const [targets, setTargets] = useState<Target[]>([])
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [uploads, setUploads] = useState<Upload[]>([])
  const [historyLimit, setHistoryLimit] = useState(5)
  const [hasMoreHistory, setHasMoreHistory] = useState(false)
  const [completedCount, setCompletedCount] = useState(0)
  const [search, setSearch] = useState('')
  const [selectedOnly, setSelectedOnly] = useState(false)
  const [copyStatus, setCopyStatus] = useState('')
  const [datasetLimit, setDatasetLimit] = useState<number | null>(10)
  const [selection, setSelection] = useState<string[]>(() => { try { const saved: unknown = JSON.parse(sessionStorage.getItem('fhir-upload-selection') ?? '[]'); return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string') : [] } catch { return [] } })
  useEffect(() => { sessionStorage.setItem('fhir-upload-selection', JSON.stringify(selection)) }, [selection])
  const [target, setTarget] = useState(() => sessionStorage.getItem('fhir-selected') ?? '')
  const [loginNotice] = useState(() => { const query = new URLSearchParams(window.location.search); const notice = query.get('fhirLogin'); if (notice) { query.delete('fhirLogin'); window.history.replaceState(null, '', window.location.pathname + (query.size ? '?' + query : '')) } return notice })
  useEffect(() => { sessionStorage.setItem('fhir-selected', target) }, [target])
  const [pending, setPending] = useState(false)
  const [error, setError] = useState(false)
  const [connectionFailed, setConnectionFailed] = useState(false)
  const [request, setRequest] = useState<{ id: string; key: string } | null>(null)
  useEffect(() => {
    if (requestedSelection) { setSelection(requestedSelection.ids); setSearch(''); setSelectedOnly(true); setDatasetLimit(10) }
  }, [requestedSelection])
  async function refresh() {
    const responses = await Promise.all(['/api/fhir-targets', '/api/datasets', `/api/fhir-uploads?limit=${historyLimit}`].map(url => fetch(url)))
    if (responses.some(response => !response.ok)) throw new Error()
    const [nextTargets, nextDatasets, nextUploads] = await Promise.all(responses.map(response => response.json()))
    return { nextTargets, nextDatasets, nextUploads }
  }
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const data = await refresh()
        if (active) { setTargets(data.nextTargets); setTarget(current => data.nextTargets.some((server: Target) => server.id === current) ? current : ''); setDatasets(data.nextDatasets); setUploads(data.nextUploads.items); setHasMoreHistory(data.nextUploads.hasMore); setCompletedCount(data.nextUploads.completedCount); setConnectionFailed(false) }
      } catch { if (active) setConnectionFailed(true) }
      if (active) timer = setTimeout(() => void poll(), 5000)
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [historyLimit])
  async function removeHistory(id?: string) {
    if (!window.confirm(t(id ? 'app.upload.removeConfirm' : 'app.upload.clearConfirm'))) return
    setPending(true); setError(false)
    try {
      const response = await fetch(`/api/fhir-uploads${id ? `/${id}` : ''}`, { method: 'DELETE' })
      if (!response.ok) throw new Error()
      const data = await refresh()
      setUploads(data.nextUploads.items); setHasMoreHistory(data.nextUploads.hasMore); setCompletedCount(data.nextUploads.completedCount)
    } catch { setError(true) }
    finally { setPending(false) }
  }
  async function submit() {
    setPending(true); setError(false)
    const key = JSON.stringify([target, [...selection].sort()])
    const id = request?.key === key ? request.id : crypto.randomUUID()
    setRequest({ id, key })
    try {
      const response = await fetch('/api/fhir-uploads', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ requestId: id, target, datasets: selection }) })
      if (!response.ok) throw new Error()
      const upload: Upload = await response.json()
      setUploads(previous => [upload, ...previous.filter(item => item.id !== upload.id)])
      setSelection([]); setRequest(null)
    } catch { setError(true) }
    finally { setPending(false) }
  }
  async function cancel(id: string) {
    try {
      const response = await fetch(`/api/fhir-uploads/${id}/cancel`, { method: 'POST' })
      if (!response.ok) throw new Error()
    } catch { setError(true) }
  }
  async function resume(id: string) {
    setPending(true); setError(false)
    try {
      const response = await fetch(`/api/fhir-uploads/${id}/resume`, { method: 'POST' })
      if (!response.ok) throw new Error()
      const updated: Upload = await response.json()
      setUploads(previous => previous.map(item => item.id === id ? updated : item))
    } catch { setError(true) }
    finally { setPending(false) }
  }
  async function copyCommand(server: string) {
    try { await navigator.clipboard.writeText(`docker compose -f web/compose.yml --profile ${server} up -d ${server}`); setCopyStatus('app.upload.copied') }
    catch { setCopyStatus('app.upload.copyFailed') }
  }
  const eligible = datasets.filter(dataset => dataset.state === 'succeeded' && !dataset.error && dataset.size !== undefined).sort((a, b) => b.created - a.created)
  const filtered = eligible.filter(dataset => (!selectedOnly || selection.includes(dataset.id)) &&
    [datasetLabel(dataset, language), dataset.id].join(' ').toLocaleLowerCase(language).includes(search.toLocaleLowerCase(language)))
  return <section className="mt-8 space-y-4 rounded-2xl border border-slate-200 bg-white p-5">
    <h2 className="text-lg font-semibold">{language === 'de' ? 'FHIR-Server und Daten' : 'FHIR servers and data'}</h2>
    {loginNotice && <p role="status" className="text-sm">{loginNotice === 'success' ? (language === 'de' ? 'Anmeldung erfolgreich. Bitte die Verbindung prüfen.' : 'Signed in. Please check the connection.') : serverMessage(loginNotice, language)}</p>}
    <p className="text-sm text-slate-600">{language === 'de' ? 'Server verbinden, erzeugte Daten hochladen und Ressourcen ansehen.' : 'Connect servers, upload generated data and view resources.'}</p>
    {(error || connectionFailed) && <p role="alert" className="text-sm text-red-700">{t('app.upload.error')}</p>}
    <FhirServers language={language} targets={targets} target={target} onSelect={setTarget} onChange={async () => { const response = await fetch('/api/fhir-targets'); if (!response.ok) throw new Error('server_error'); setTargets(await response.json()) }}/>
    {targets.some(server => server.id === target && !server.external && !server.available) && <div className="rounded-lg bg-slate-50 p-3 text-sm">
      <p>{t('app.upload.startHint')}</p>
      <details className="mt-2"><summary className="cursor-pointer font-medium">{t('app.upload.startServers')}</summary>
        <p className="mt-2">{t('app.upload.commandsHint')}</p>
        {targets.filter(server => !server.external && !server.available).map(({ id: server }) => <div key={server} className="mt-3 space-y-2"><p className="font-medium">{t('app.upload.startServer', { server: server === 'blaze' ? 'Blaze' : 'HAPI' })}</p><div className="flex flex-wrap items-center gap-2"><code className="break-all">docker compose -f web/compose.yml --profile {server} up -d {server}</code><Button type="button" variant="outline" onClick={() => void copyCommand(server)}>{t('app.upload.copy')}</Button></div></div>)}
        <p role="status" className="mt-2">{copyStatus && t(copyStatus as TextKey)}</p>
      </details>
    </div>}
    <FhirResources target={targets.find(server => server.id === target)} language={language}/>
    <h3 className="font-semibold">{t('app.upload.title')}</h3>
    <p className="text-sm text-slate-600">{t('app.upload.hint')}</p>
    <fieldset disabled={pending} className="space-y-2"><legend className="mb-2 font-medium">{t('app.upload.datasets')}</legend>
      <input className="w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label={t('app.upload.search')} placeholder={t('app.upload.search')} value={search} onChange={e => { setSearch(e.target.value); setDatasetLimit(10) }}/>
      <div className="flex flex-wrap items-center gap-4 text-sm"><span>{t('app.upload.selected', { count: selection.length })}</span><label className="flex items-center gap-2"><input className="h-4 w-4 shrink-0 accent-teal-700" type="checkbox" checked={selectedOnly} onChange={e => { setSelectedOnly(e.target.checked); setDatasetLimit(10) }}/>{t('app.upload.selectedOnly')}</label></div>
      {filtered.length > 0 && <Button variant="outline" onClick={() => setSelection(previous => [...new Set([...previous, ...filtered.map(item => item.id)])])}>{t('app.upload.selectFiltered', { count: filtered.length })}</Button>}
      {eligible.length > 0 && filtered.length === 0 && <p className="text-sm text-slate-500">{t('app.upload.noMatches')}</p>}
      {eligible.length === 0 && <p className="text-sm text-slate-500">{t('app.upload.empty')}</p>}
      <div className="space-y-2">{filtered.slice(0, datasetLimit ?? filtered.length).map(dataset => <label key={dataset.id} className="flex items-start gap-2 text-sm"><input className="mt-0.5 h-4 w-4 shrink-0 accent-teal-700" type="checkbox" checked={selection.includes(dataset.id)} onChange={e => setSelection(previous => e.target.checked ? [...previous, dataset.id] : previous.filter(id => id !== dataset.id))}/><span>{datasetLabel(dataset, language)} · {new Date(dataset.created * 1000).toLocaleString(language === 'de' ? 'de-DE' : 'en-GB')} <span className="text-xs text-slate-500">({dataset.id.slice(0, 8)})</span></span></label>)}</div>
      <ListExpansion language={language} total={filtered.length} limit={datasetLimit} onChange={setDatasetLimit} countKey="app.list.datasetsCount"/>
    </fieldset>
    {target && <p className="text-sm">{language === 'de' ? 'Upload-Ziel' : 'Upload target'}: <strong>{targets.find(server => server.id === target)?.name}</strong> · <span className="break-all">{targets.find(server => server.id === target)?.address}</span></p>}
    <Button disabled={pending || !selection.length || !targets.some(server => server.id === target && server.available)} onClick={() => void submit()}>{t(pending ? 'app.upload.submitting' : 'app.upload.start', { count: selection.length })}</Button>
    {uploads.length > 0 && <div className="space-y-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{t('app.upload.history')}</h3>{completedCount > 0 && <Button variant="outline" disabled={pending} onClick={() => void removeHistory()}>{t('app.upload.clearHistory')}</Button>}</div>{uploads.map(upload => <article key={upload.id} className="rounded-lg border p-3 text-sm">
      <p className="font-medium">{upload.descriptor.target.name} · {t(stateKeys[upload.state] || 'app.upload.failed')} · {new Date(upload.created * 1000).toLocaleString(language === 'de' ? 'de-DE' : 'en-GB')}</p>
      <p>{upload.descriptor.datasets.map(dataset => `${datasetLabel(datasets.find(item => item.id === dataset.id) ?? dataset, language)} (${dataset.id.slice(0, 8)})`).join(', ')}</p>
      {(upload.result?.error || upload.result?.serverErrors?.length) ? <p role="alert" className="mt-2 line-clamp-2 break-words text-red-700">{uploadError(upload.result?.serverErrors?.[0]?.diagnostics[0] || upload.result?.serverErrors?.[0]?.code || upload.result?.error || '', language)}</p> : null}
      {upload.result?.streaming && <div className="mt-2" role="status">
        {upload.state === 'preparing' ? <p>{t('app.upload.bulkChecking', { count: upload.result.checkedBundles ?? 0 })}</p> : <><progress className="w-full accent-teal-700" max={upload.result.bundles || 1} value={upload.result.acceptedBundles ?? 0}/><p>{t('app.upload.bulkProgress', { count: upload.result.acceptedBundles ?? 0, total: upload.result.bundles ?? 0, packages: upload.result.completedPackages ?? 0, allPackages: upload.result.packages ?? 0 })}</p></>}
      </div>}
      {upload.resumable && <div className="mt-2"><Button variant="outline" disabled={pending} onClick={() => void resume(upload.id)}>{t('app.upload.resume')}</Button><p className="mt-1 text-xs text-slate-600">{t('app.upload.resumeHint')}</p></div>}
      <details className="mt-2"><summary className="cursor-pointer text-teal-800">{t('app.upload.details')}</summary>
      <a className="break-all text-xs text-teal-800 underline" href={upload.descriptor.target.address} target="_blank" rel="noreferrer">{upload.descriptor.target.address}</a>
      {upload.result?.resource && <p role="alert" className="mt-2 text-amber-800">{t('app.upload.conflictDetail', { resource: upload.result.resource })}</p>}
      {upload.result?.resources !== undefined && <p>{t('app.upload.counts', { resources: upload.result.resources, bundles: upload.result.bundles || 0 })}</p>}
      {upload.result?.acceptedBundles !== undefined && <p className="mt-2">{t('app.upload.outcomes', { accepted: upload.result.acceptedBundles, rejected: upload.result.rejectedBundles ?? 0 })}</p>}
      {upload.result?.statusCounts && <p className="text-xs text-slate-500">{Object.entries(upload.result.statusCounts).map(([status, count]) => `HTTP ${status}: ${count}`).join(' · ')}</p>}
      {upload.result?.error && !upload.result.serverErrors?.length && <p role="alert" className="mt-2 text-red-700">{uploadError(upload.result.error, language)}</p>}
      {!!upload.result?.serverErrors?.length && <details className="mt-2"><summary className="cursor-pointer font-medium">{t('app.upload.reasons')}</summary><ul className="mt-2 space-y-2">{upload.result.serverErrors.map((failure, index) => <li key={index} className="break-words rounded border border-red-200 p-2 text-red-800"><p>{failure.file} · Bundle {failure.bundle} · HTTP {failure.status ?? '?'}</p>{(failure.diagnostics.length ? failure.diagnostics : [failure.code ?? '']).map((diagnostic, i) => <p key={i}>{diagnostic}</p>)}</li>)}</ul></details>}
      {['failed', 'cancelled', 'interrupted'].includes(upload.state) && upload.result?.mayHaveWritten && <p className="text-amber-800">{t('app.upload.partial')}</p>}
      <div className="mt-2 flex items-center gap-3"><a className="text-teal-800 underline" href={`/api/fhir-uploads/${upload.id}/logs`} target="_blank" rel="noreferrer">{t('app.upload.logs')}</a>{['queued', 'preparing', 'uploading'].includes(upload.state) && <Button variant="outline" onClick={() => void cancel(upload.id)}>{t('app.cancel')}</Button>}</div>
      </details>
      {!['queued', 'preparing', 'uploading'].includes(upload.state) && <Button className="mt-2" variant="outline" disabled={pending} onClick={() => void removeHistory(upload.id)}>{t('app.upload.removeHistory')}</Button>}
    </article>)}{hasMoreHistory && <Button variant="outline" disabled={pending} onClick={() => setHistoryLimit(limit => limit + 5)}>{t('app.upload.loadOlder')}</Button>}</div>}
  </section>
}
