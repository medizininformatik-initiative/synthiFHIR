import { numberRuns, continuationReference, groupRuns } from './run-numbering'
import { BulkProgress, type BulkStatus, type BulkSubrun } from './BulkProgress'
import { SelectionProgress } from './SelectionProgress'
import { datasetLabel, type DatasetContext } from './dataset-label'
import { resolveGenerationSeeds } from './generation-seeds'
import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Play, Square, Activity } from 'lucide-react'
import { Button } from './components/ui/button'
import { errorMessage, initialLanguage, InterfaceError, translate, type Language, type Message, type TextKey } from './i18n'
import './index.css'
import { ServiceLinks } from './ServiceLinks'
import { FhirUploads } from './FhirUploads'
import { Help, Tooltip } from './Help'
import { DatasetResults } from './DatasetResults'
import { GenerationSettings, type Generation } from './GenerationSettings'
import { RunOutcomes, type Outcomes } from './RunOutcomes'
import { InputSelection } from './InputSelection'
import { ConfigurationEditor } from './ConfigurationEditor'
import { problems, type Configuration } from './configuration'
import { exportPropertiesConfiguration, importRunProperties } from './configuration-properties'

type Continuation = { tableFormat?: 'csv' | 'excel'; diagnostics?: boolean; sourceOffset?: number; sourceRun: string; requestedRun: string; seriesRoot: string; completedBefore: number; generation: Generation; datasetName: string; configurationProperties?: string; nextCounters: Record<string, string> }

type Job = { addition?: { sourceRun: string } | null; continuation?: { sourceRun: string } | null; series_continuable?: boolean; series_unavailable_reason?: 'single-person' | 'missing-state'; output_directory?: string; bulk_output_directory?: string; bulk_progress?: BulkStatus; bulk_subruns?: BulkSubrun[]; bulk_resumable?: boolean; table_format?: 'csv' | 'excel'; outcomes?: Outcomes; workbook_count?: number; cleanup?: { completed: number; removedBytes: number; removedFiles: number }; editor_available?: boolean; summary?: DatasetContext['summary']; input_kind?: string; duration_seconds?: number | null; failure?: { kind: 'memory' | 'killed'; evidence: string }; dataset_name?: string; id: string; state: string; created: number; cancel: number; exit_code: number | null; download_available: boolean; source?: string; source_name?: string; configuration?: { id: string; name: string; revision: number | null }; batch_id?: string | null; repeated_from?: string | null; generation?: Generation; generation_result?: { generatedPatients: number; importedPatients: number; failedPatients: number } }
async function fetchResponse(path: string, init?: RequestInit): Promise<Response> {
  let response: Response
  try { response = await fetch('/api' + path, init) }
  catch { throw new InterfaceError('app.error.network') }
  if (!response.ok) {
    if (response.status === 422 || response.status === 409) {
      const body = await response.json().catch(() => null)
      if (typeof body?.detail === 'string') throw new InterfaceError('app.error.configuration', { detail: body.detail })
    }
    const key = response.status === 404 ? 'app.error.notFound'
      : response.status === 422 ? 'app.error.invalidInput'
      : response.status === 403 ? 'app.error.crossOrigin' : 'app.error.http'
    throw new InterfaceError(key, { status: response.status })
  }
  return response
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchResponse(path, init)
  try { return await response.json() }
  catch { throw new InterfaceError('app.error.unexpected') }
}
async function submitRuns<T>(path: string, payload: { generation?: Generation | null; [key: string]: unknown }): Promise<T> {
  const key = 'pendingSubmission:' + path
  const signature = JSON.stringify(payload)
  let previous: { signature: string; id: string; timestamp?: number } | null = null
  try { previous = JSON.parse(sessionStorage.getItem(key) ?? 'null') } catch { /* Start a fresh submission. */ }
  const id = previous?.signature === signature ? previous.id : crypto.randomUUID()
  const timestamp = previous?.signature === signature ? previous.timestamp ?? Date.now() : Date.now()
  const resolved = payload.generation ? { ...payload, generation: resolveGenerationSeeds(payload.generation, timestamp) } : payload
  sessionStorage.setItem(key, JSON.stringify({ signature, id, timestamp }))
  const result = await request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...resolved, requestId: id }) })
  sessionStorage.removeItem(key)
  return result
}
function stateKey(state: string): TextKey {
  switch (state) {
    case 'queued': return 'app.state.queued'
    case 'running': return 'app.state.running'
    case 'succeeded': return 'app.state.succeeded'
    case 'failed': return 'app.state.failed'
    case 'cancelled': return 'app.state.cancelled'
    case 'interrupted': return 'app.state.interrupted'
    default: return 'app.state.unknown'
  }
}
function App() {
  const [activeTab, setActiveTab] = useState<'generate' | 'runs' | 'services'>('generate')
  const [uploadSelection, setUploadSelection] = useState<{ ids: string[] } | null>(null)
  function openUpload(ids: string[]) { setUploadSelection({ ids }); setActiveTab('services') }
  const [language, setLanguage] = useState<Language>(initialLanguage)
  const t = (key: TextKey, params?: Message['params']) => translate(language, key, params)
  const locale = language === 'de' ? 'de-DE' : 'en-GB'
  const [runSearch, setRunSearch] = useState('')
  const [runLimit, setRunLimit] = useState<number | null>(10)
  const [jobs, setJobs] = useState<Job[]>([])
  const [selected, setSelected] = useState<string | null>(localStorage.getItem('selectedJob'))
  const [datasetName, setDatasetName] = useState(() => {
    try { return localStorage.getItem('workbenchDatasetName') ?? '' }
    catch { return '' }
  })
  useEffect(() => {
    try { localStorage.setItem('workbenchDatasetName', datasetName) }
    catch { /* Naming remains usable when browser storage is unavailable. */ }
  }, [datasetName])
  const [source, setSource] = useState(() => {
    try { return localStorage.getItem('workbenchInputSource') || 'synthea-generation' }
    catch { return 'synthea-generation' }
  })
  useEffect(() => {
    try { localStorage.setItem('workbenchInputSource', source) }
    catch { /* Selection remains usable when browser storage is unavailable. */ }
  }, [source])
  const [inputKind, setInputKind] = useState<string | undefined>()
  const [diagnostics, setDiagnostics] = useState(() => {
    try { return localStorage.getItem('workbenchDiagnostics') === 'true' }
    catch { return false }
  })
  const [tableFormat, setTableFormat] = useState<'csv' | 'excel'>(() => {
    try { return localStorage.getItem('workbenchTableFormat') === 'excel' ? 'excel' : 'csv' }
    catch { return 'csv' }
  })
  useEffect(() => {
    try {
      localStorage.setItem('workbenchDiagnostics', String(diagnostics))
      localStorage.setItem('workbenchTableFormat', tableFormat)
    } catch { /* Settings remain usable when browser storage is unavailable. */ }
  }, [diagnostics, tableFormat])
  const [seriesMode, setSeriesMode] = useState(() => !!localStorage.getItem('workbenchContinuation'))
  const [continuation, setContinuation] = useState<Continuation | null>(null)
  const [bulkGzip, setBulkGzip] = useState(true)
  const [generation, setGeneration] = useState<Generation | null>(null)
  const [executionMode, setExecutionMode] = useState<'standard' | 'bulk'>('standard')
  const [uploadingInput, setUploadingInput] = useState(false)
  const [editorLoad, setEditorLoad] = useState<{ configuration: Configuration; generation?: Generation; key: number } | null>(null)
  const [configuration, setConfiguration] = useState<Configuration | null>(null)
  const configurationReady = configuration !== null && problems(configuration).length === 0
  const nativeOutput = source === 'synthea-generation' && generation?.outputMode === 'synthea'
  const bulkOutput = source === 'synthea-generation' && executionMode === 'bulk'
  const syntheaTables = !nativeOutput && (source === 'synthea-generation' || inputKind?.startsWith('synthea'))
  const canStart = (!seriesMode || continuation !== null) && (nativeOutput || configurationReady) && (source !== 'synthea-generation' || generation !== null)
  const generationPayload = { ...(source === 'synthea-generation' ? { generation } : {}), datasetName: datasetName.trim() || undefined }
  const [logs, setLogs] = useState<string | null>(null)
  const [error, setError] = useState<Message | null>(null)
  const [connectionError, setConnectionError] = useState<Message | null>(null)
  const deleteDialog = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)
  const job = jobs.find(j => j.id === selected)
  useEffect(() => {
    document.documentElement.lang = language
    document.title = translate(language, 'app.title')
    localStorage.setItem('workbenchLanguage', language)
  }, [language])
  useEffect(() => {
    let active = true
    async function refresh() {
      try {
        const next = await request<Job[]>('/jobs')
        if (active) { setJobs(next); setConnectionError(null) }
        if (selected && next.some(item => item.id === selected)) {
          const response = await fetchResponse(`/jobs/${selected}/logs`)
          const text = await response.text()
          if (active) setLogs(text)
        }
      } catch (e) { if (active) setConnectionError(errorMessage(e)) }
    }
    void refresh()
    const timer = setInterval(() => void refresh(), 1500)
    return () => { active = false; clearInterval(timer) }
  }, [selected])
  function select(id: string) { setLogs(null); setSelected(id); localStorage.setItem('selectedJob', id) }
  async function start() {
    if (!canStart) return
    setBusy(true); setError(null)
    try {
      const next = [continuation ? await submitRuns<Job>(`/jobs/${continuation.sourceRun}/continuation`, { population: generation!.population }) : await submitRuns<Job>('/jobs', {
        source, ...generationPayload,
        ...(syntheaTables ? { tableFormat: bulkOutput ? 'csv' : tableFormat, diagnostics: bulkOutput ? false : diagnostics } : {}),
        ...(nativeOutput ? {} : { configurationProperties: exportPropertiesConfiguration(configuration!, language) })
      })]
      setJobs(old => [...next, ...old.filter(job => !next.some(value => value.id === job.id))]); select(next[0].id); setRunSearch(''); setRunLimit(10); setActiveTab('runs')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  useEffect(() => {
    const previous = localStorage.getItem('workbenchContinuation')
    if (previous && previous !== 'choose') void loadContinuation(previous)
  }, [])
  async function loadContinuation(id: string) {
    setSeriesMode(true)
    localStorage.setItem('workbenchContinuation', id)
    setBusy(true); setError(null); setContinuation(null)
    try {
      const loaded = await request<Continuation>(`/jobs/${id}/continuation`)
      const config = loaded.configurationProperties ? importRunProperties(loaded.configurationProperties) : configuration!
      const displayed = config && { ...config, identifierRules: config.identifierRules.map(rule => ({ ...rule,
        ...(loaded.nextCounters[rule.id] ? { countStart: Number(loaded.nextCounters[rule.id]) } : {}) })) }
      setEditorLoad({ configuration: displayed, generation: loaded.generation, key: Date.now() })
      setConfiguration(displayed); setGeneration(loaded.generation); setExecutionMode(loaded.generation.executionMode ?? 'standard')
      setTableFormat(loaded.tableFormat ?? 'csv'); setDiagnostics(loaded.diagnostics ?? false)
      localStorage.setItem('workbenchContinuation', loaded.sourceRun)
      setContinuation(loaded); setSeriesMode(true); setSource('synthea-generation')
      setDatasetName(loaded.datasetName); setActiveTab('generate')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  function newSeries() { localStorage.removeItem('workbenchContinuation'); setSeriesMode(false); setContinuation(null) }
  function chooseSeries() { localStorage.setItem('workbenchContinuation', 'choose'); setSeriesMode(true) }
  async function resumeBulk() {
    if (!job) return
    setBusy(true); setError(null)
    try {
      const next = await request<Job>(`/jobs/${job.id}/resume`, { method: 'POST' })
      setJobs(old => old.map(item => item.id === next.id ? next : item))
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  async function loadRun(fromExcel = false) {
    if (!job) return
    setBusy(true); setError(null)
    try {
      const loaded = await request<{ source: string; datasetName: string; tableFormat?: 'csv' | 'excel'; diagnostics?: boolean; generation?: Generation; configurationProperties: string }>(`/jobs/${job.id}/${fromExcel ? 'excel-editor' : 'editor'}`, { method: 'POST' })
      const config = importRunProperties(loaded.configurationProperties)
      newSeries()
      setEditorLoad({ configuration: config, generation: loaded.generation, key: Date.now() })
      setConfiguration(config); setGeneration(loaded.generation ?? null)
      setTableFormat(loaded.tableFormat ?? 'csv');
      setDiagnostics(loaded.diagnostics ?? false);
      setSource(loaded.source); setDatasetName(loaded.datasetName); setActiveTab('generate')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  async function deleteRun() {
    if (!job) return
    deleteDialog.current?.close()
    setBusy(true); setError(null)
    try {
      await fetchResponse(`/jobs/${job.id}`, { method: 'DELETE' })
      setJobs(old => old.filter(item => item.id !== job.id))
      setSelected(null); setLogs(null); localStorage.removeItem('selectedJob')
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  async function cleanRun() {
    if (!job) return
    deleteDialog.current?.close()
    setBusy(true); setError(null)
    try {
      const updated = await request<Job>(`/jobs/${job.id}/cleanup`, { method: 'POST' })
      setJobs(old => old.map(item => item.id === updated.id ? updated : item)); setLogs(null)
    } catch (e) { setError(errorMessage(e)) } finally { setBusy(false) }
  }
  function duration(job: Job) {
    if (job.duration_seconds == null) return null
    const seconds = Math.floor(job.duration_seconds)
    return t('app.duration', { time: `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` })
  }
  function runContext(job: Job): DatasetContext {
    return { datasetName: job.dataset_name, source: job.source, sourceName: job.source_name,
      inputKind: job.input_kind, generation: job.generation, summary: job.summary, configuration: job.configuration }
  }
  function configurationName(job: Job) { return datasetLabel(runContext(job), language) }
  async function cancel() {
    try { await request(`/jobs/${selected}/cancel`, { method: 'POST' }) } catch (e) { setError(errorMessage(e)) }
  }
  const { numbers: runNumbers } = numberRuns(jobs)
  const de = language === 'de'
  function runType(item: Job) {
    if (item.input_kind === 'resource-addition') return de ? 'Ergänzung' : 'Addition'
    const source = item.source === 'synthea-generation' || item.input_kind?.startsWith('synthea') ? 'Synthea' : item.input_kind === 'csv' ? 'CSV' : ['workbook', 'workbook-set'].includes(item.input_kind ?? '') ? 'Excel' : de ? 'Eingabe' : 'Input'
    return `${source} · ${item.bulk_output_directory ? (de ? 'Massendaten' : 'Bulk') : item.source === 'synthea-generation' ? (de ? 'Generierung' : 'Generation') : (de ? 'Konvertierung' : 'Conversion')}`
  }
  function runTitle(item: Job) { return item.dataset_name || item.source_name || (item.source === 'starter' ? (de ? 'Starter-Arbeitsmappe' : 'Starter workbook') : item.source === 'demo' ? 'INTERPOLAR Demo' : de ? 'Datensatz' : 'Dataset') }
  function runCount(item: Job) {
    const count = item.summary?.patients ?? item.bulk_progress?.completed
    return count != null ? `${count.toLocaleString(locale)} ${de ? (count === 1 ? 'Patient' : 'Patienten') : (count === 1 ? 'patient' : 'patients')}` : item.generation ? `${item.generation.population.toLocaleString(locale)} ${de ? 'Patienten angefordert' : 'patients requested'}` : ''
  }
  function continuationLabel(sourceRun: string) {
    const message = continuationReference(sourceRun, runNumbers)
    return t(message.key, message.params)
  }
  const runGroups = groupRuns(jobs).filter(group => group.items.some(j => [j.id, runTitle(j), runType(j), configurationName(j), t(stateKey(j.state))].join(' ').toLowerCase().includes(runSearch.toLowerCase())))
  const visibleGroups = runGroups.slice(0, runLimit ?? runGroups.length)

  const alert = error || connectionError
  // This is the API's queue placeholder, not converter-produced log content.
  const logText = !selected ? t('app.selectRun') : logs === null || logs === 'Waiting for worker…' ? t('app.waiting') : logs
  const tableOptions = syntheaTables && !bulkOutput && <div className="mt-5 rounded-xl border border-teal-200 bg-teal-50 p-4">
    <div className="max-w-xl text-sm font-medium">
      <div className="mb-2"><label htmlFor="synthea-table-format">{t('app.tableFormat')}</label><Help text={t('app.tableFormat.help')} t={key => t(key as TextKey)}/></div>
      <select id="synthea-table-format" className="w-full rounded-lg border border-slate-300 bg-white p-2.5" value={tableFormat} onChange={e => setTableFormat(e.target.value as 'csv' | 'excel')}>
        <option value="csv">{t('app.tableFormat.csv')}</option>
        <option value="excel">{t('app.tableFormat.excel')}</option>
      </select>
    </div>
    <div className="mt-4 flex items-center text-sm">
      <label className="flex items-center gap-2"><input type="checkbox" checked={diagnostics} onChange={e => setDiagnostics(e.target.checked)}/>{t('app.diagnostics')}</label>
      <Help text={t('app.diagnostics.help')} t={key => t(key as TextKey)}/>
    </div>
  </div>

  return <main className="mx-auto max-w-6xl px-6 py-12">
    <header className="mb-10 flex flex-wrap items-center gap-4">
      <div className="rounded-xl bg-teal-800 p-3 text-white"><Activity size={28}/></div>
      <div><p className="text-xs font-semibold uppercase tracking-widest text-teal-800">{t('app.subtitle')}</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">{t('app.title')}</h1></div>
      <label className="ml-auto flex flex-col gap-1 text-sm">{t('app.language')}<select className="rounded-lg border border-slate-300 bg-white p-2" value={language} onChange={e => setLanguage(e.target.value as Language)}><option value="de">{t('app.language.de')}</option><option value="en">{t('app.language.en')}</option></select></label>
    </header>
    <div role="tablist" aria-label={t('app.navigation')} className="workbench-tabs mb-6">
      {(['generate', 'runs', 'services'] as const).map((tab, index, tabs) => <Tooltip key={tab} text={t(`tooltip.tab.${tab}`)}><button type="button" role="tab" id={`main-tab-${tab}`} aria-controls={`main-panel-${tab}`} aria-selected={activeTab === tab} tabIndex={activeTab === tab ? 0 : -1} onClick={() => setActiveTab(tab)} onKeyDown={event => {
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
        if (next !== null) { event.preventDefault(); setActiveTab(tabs[next]); document.getElementById(`main-tab-${tabs[next]}`)?.focus() }
      }} className="workbench-tab">{t(tab === 'generate' ? 'app.tabs.generate' : tab === 'runs' ? 'app.runs' : 'app.tabs.services')}</button></Tooltip>)}
    </div>
    {alert && <p role="alert" className="mb-4 rounded-lg bg-red-50 p-4 text-red-800">{t(alert.key, alert.params)}</p>}
    <div role="tabpanel" id="main-panel-generate" aria-labelledby="main-tab-generate" hidden={activeTab !== 'generate'}>
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold">{t('app.create')}</h2><p className="mt-1 text-sm text-slate-500">{t('app.intro')}</p>
      <div className="mt-6 flex flex-wrap items-start gap-5">
        <fieldset disabled={!!continuation}><InputSelection key={editorLoad?.key} language={language} source={source} onChange={value => { newSeries(); setInputKind(undefined); setSource(value) }} onBusy={setUploadingInput} onKindChange={setInputKind}/></fieldset>
        <label className="flex w-full sm:w-80 flex-col gap-2 text-sm font-medium">{t('app.datasetName')}<input className="rounded-lg border border-slate-300 p-2.5" disabled={!!continuation} maxLength={200} value={datasetName} onChange={e => setDatasetName(e.target.value)} placeholder={t('app.datasetNameHint')}/></label>
        <Button className="sm:mt-7" onClick={() => void start()} disabled={busy || uploadingInput || !canStart}><Play size={16}/>{t(source === 'synthea-generation' ? 'app.generation.start' : 'app.start')}</Button>
      </div>
      {source === 'synthea-generation' ? <GenerationSettings bulkGzip={bulkGzip} onBulkGzipChange={setBulkGzip} sourceOffset={continuation?.sourceOffset ?? 0} continuationLocked={!!continuation} seriesControls={<div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-sm"><span className="flex min-h-6 items-center">{t('app.series.mode')}<Help text={t('app.series.help')} t={key => t(key as TextKey)}/></span><select className="mt-1 w-full rounded-lg border p-2" disabled={busy} value={seriesMode ? 'continue' : 'new'} onChange={e => e.target.value === 'new' ? newSeries() : chooseSeries()}><option value="new">{t('app.series.new')}</option><option value="continue">{t('app.series.continue')}</option></select></label>
        {seriesMode && <label className="text-sm"><span className="flex min-h-6 items-center">{t('app.series.previous')}</span><select className="mt-1 w-full rounded-lg border p-2" disabled={busy} value={continuation?.sourceRun ?? ''} onChange={e => { if (e.target.value) void loadContinuation(e.target.value); else setContinuation(null) }}><option value="">{t('app.series.choose')}</option>{jobs.filter(item => item.series_continuable).map(item => <option key={item.id} value={item.id}>{item.dataset_name || item.id.slice(0, 8)} · {item.generation?.population.toLocaleString(locale)} · {new Date(item.created * 1000).toLocaleString(locale)}</option>)}</select></label>}
        {continuation && <p className="text-sm sm:col-span-2">{t('app.series.range', { previous: continuation.completedBefore.toLocaleString(locale), first: (continuation.completedBefore + 1).toLocaleString(locale), last: (continuation.completedBefore + (generation?.population ?? 0)).toLocaleString(locale) })}<Help text={t('app.series.lockedHelp')} t={key => t(key as TextKey)}/></p>}
      </div>} key={editorLoad?.key} initialValue={editorLoad?.generation} language={language} onChange={setGeneration} onExecutionModeChange={setExecutionMode}>{tableOptions}</GenerationSettings> : tableOptions}
      <p className="mt-4 text-xs text-slate-500">{t(nativeOutput ? 'app.generation.nativeHint' : bulkOutput ? 'app.bulk.overrides' : 'app.outputHint')}</p>
    </section>
    <div hidden={nativeOutput}>
      <ConfigurationEditor bulkGzip={bulkGzip} onBulkGzipChange={setBulkGzip} readOnly={!!continuation} bulkMode={bulkOutput} key={editorLoad?.key} initialConfiguration={editorLoad?.configuration} language={language} onChange={setConfiguration}/>
    </div>
    </div>
    <div role="tabpanel" id="main-panel-services" aria-labelledby="main-tab-services" hidden={activeTab !== 'services'}>
      <FhirUploads language={language} requestedSelection={uploadSelection}/>
      <ServiceLinks language={language}/>
    </div>
    <div role="tabpanel" id="main-panel-runs" aria-labelledby="main-tab-runs" hidden={activeTab !== 'runs'}>
    <div className="mt-8 grid gap-6 md:grid-cols-[300px_1fr]">
      <section aria-label={de ? 'Laufübersicht' : 'Run overview'}>
        <h2 className="text-lg font-semibold">{t('app.runs')} <span className="text-slate-400">{jobs.length}</span></h2>
        <p className="mb-3 mt-1 text-sm text-slate-500">{de ? 'Lauf auswählen, um Daten zu öffnen. Folgeläufe stehen unter ihrem Ausgangslauf.' : 'Select a run to open its data. Related runs appear below their starting run.'}</p>
        <input className="mb-3 w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label={t('app.datasets.searchRuns')} placeholder={de ? 'Name, Laufart oder Status suchen' : 'Search name, run type or status'} value={runSearch} onChange={e => { setRunSearch(e.target.value); setRunLimit(10) }}/>
        {jobs.length === 0 && <p className="text-sm text-slate-500">{t('app.empty')}</p>}
        <ul className="space-y-3 md:max-h-[72vh] md:overflow-y-auto md:pr-2">{visibleGroups.map(group => <li key={group.root}><ul className="space-y-1">{group.items.map((j, index) => <li key={j.id} className={index ? 'ml-4 border-l-2 border-slate-200 pl-2' : ''}>
          <button aria-current={selected === j.id ? 'true' : undefined} onClick={() => select(j.id)} className={`w-full rounded-xl border p-3 text-left ${selected === j.id ? 'border-teal-700 bg-teal-50 ring-1 ring-teal-700' : 'border-slate-200 bg-white hover:border-slate-400'}`}>
            <div className="flex justify-between gap-2 text-xs"><span className="font-semibold text-slate-500">{de ? 'Lauf' : 'Run'} {runNumbers.get(j.id)}</span><span className={j.state === 'failed' ? 'font-semibold text-red-700' : j.state === 'running' ? 'font-semibold text-teal-800' : 'text-slate-500'}>{t(stateKey(j.state))}</span></div>
            <p title={runTitle(j)} className="mt-1 line-clamp-2 break-words font-semibold">{runTitle(j)}</p>
            <span className="mt-2 inline-block rounded bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">{runType(j)}</span>
            {(j.continuation || j.addition) && <p className="mt-2 text-xs text-teal-800">{j.addition ? (de ? 'Ergänzung zu Lauf' : 'Addition to run') : (de ? 'Weitere Patienten zu Lauf' : 'More patients for run')} {runNumbers.get((j.addition ?? j.continuation)!.sourceRun) ?? (j.addition ?? j.continuation)!.sourceRun.slice(0, 8)}</p>}
            <p className="mt-2 text-xs text-slate-500">{[runCount(j), new Date(j.created * 1000).toLocaleDateString(locale)].filter(Boolean).join(' · ')}</p>
          </button>
        </li>)}</ul></li>)}</ul>
        {runGroups.length === 0 && jobs.length > 0 && <p className="text-sm text-slate-500">{de ? 'Keine passenden Läufe.' : 'No matching runs.'}</p>}
        {visibleGroups.length < runGroups.length && <Button className="mt-3" variant="outline" onClick={() => setRunLimit(value => (value ?? 10) + 10)}>{de ? 'Weitere Läufe anzeigen' : 'Show more runs'}</Button>}
      </section>
      <section className="min-w-0 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{t('app.details')}</h2>{job && <div>{['queued','running'].includes(job.state) && <Button variant="outline" onClick={() => void cancel()} disabled={!!job.cancel}><Square size={14}/>{t(job.cancel ? 'app.cancelling' : 'app.cancel')}</Button>}</div>}</div>
        {job?.continuation && <p className="mb-3 text-sm font-medium text-teal-700">{continuationLabel(job.continuation.sourceRun)}</p>}
        {job?.failure && <p role="alert" className="mb-3 rounded-xl bg-red-50 p-3 text-sm text-red-800">{t(job.failure.kind === 'memory' ? 'app.failure.memory' : 'app.failure.killed')}</p>}
        {job && <>
          <div className="mb-5 border-b border-slate-200 pb-4">
            <p className="text-xs font-medium text-teal-800">{de ? 'Lauf' : 'Run'} {runNumbers.get(job.id)} · {runType(job)}</p>
            <h3 className="mt-1 break-words text-xl font-semibold">{runTitle(job)}</h3>
            <p className="mt-2 text-sm text-slate-500">{t(stateKey(job.state))} · {new Date(job.created * 1000).toLocaleString(locale)}{duration(job) ? ` · ${duration(job)}` : ''}</p>
            {(job.continuation || job.addition) && <button disabled={!jobs.some(item => item.id === (job.addition ?? job.continuation)!.sourceRun)} className="mt-2 text-sm text-teal-800 underline disabled:text-slate-400" onClick={() => select((job.addition ?? job.continuation)!.sourceRun)}>{de ? 'Ausgangslauf öffnen' : 'Open starting run'}</button>}
            {job.series_continuable && <div className="mt-4">
              <Button variant="outline" disabled={busy} onClick={() => void loadContinuation(job.id)} aria-describedby="continue-series-help">{t('app.series.continue')}</Button>
              <p id="continue-series-help" className="mt-2 text-xs text-slate-600">{de ? 'Weitere Patienten mit denselben Einstellungen erzeugen.' : 'Generate more patients with the same settings.'}</p>
            </div>}
            {job.series_unavailable_reason && <p className="mt-3 text-xs text-slate-600">{job.series_unavailable_reason === 'single-person'
              ? (de ? 'Dieser Lauf erzeugt eine festgelegte Einzelperson. Für weitere Patienten eine neue Serie starten.' : 'This run generates a fixed individual. Start a new series for more patients.')
              : (de ? 'Für diesen Lauf ist kein Fortsetzungsstand gespeichert. Eingabe und Einstellungen laden, um eine neue Serie zu starten.' : 'This run has no saved continuation state. Load its inputs and settings to start a new series.')}</p>}
          </div>
          {job.bulk_progress && ['queued', 'running'].includes(job.state) && <BulkProgress progress={job.bulk_progress} subruns={job.bulk_subruns} state={job.state} language={language}/>}
          <DatasetResults key={job.id + ':' + (job.cleanup?.completed ?? '')} outputDirectory={job.output_directory} downloadAvailable={job.download_available} inputKind={job.input_kind} onUpload={openUpload} context={runContext(job)} jobId={job.id} state={job.state} language={language}/>
          <section className="my-5 space-y-3 border-t border-slate-200 pt-4">
            <h3 className="font-semibold">{de ? 'Mit diesem Lauf weiterarbeiten' : 'Continue working with this run'}</h3>
            <p className="text-sm text-slate-600">{de ? 'Eingabe und Einstellungen in den Editor übernehmen, um einen weiteren Lauf vorzubereiten.' : 'Load inputs and settings into the editor to prepare another run.'}</p>
            <div className="flex flex-wrap gap-2">
              {!['queued', 'running'].includes(job.state) && <Button variant="outline" tooltip={t('tooltip.loadRun')} disabled={busy || job.editor_available === false} onClick={() => void loadRun()}>{t('app.loadRun')}</Button>}
              {['succeeded', 'failed'].includes(job.state) && !!job.workbook_count && <Button variant="outline" tooltip={t('tooltip.replay')} disabled={busy} onClick={() => void loadRun(true)}>{t('app.replay.action')}</Button>}
              {job.bulk_resumable && <Button variant="outline" disabled={busy} onClick={() => void resumeBulk()}>{t('app.bulk.resume')}</Button>}
            </div>
            {job.bulk_resumable && <p className="text-xs text-slate-500">{t('app.bulk.resumeHelp')}</p>}
          </section>
          <details className="mb-4 text-sm"><summary className="cursor-pointer font-medium text-slate-600">{de ? 'Einstellungen und Verwaltung' : 'Settings and management'}</summary>
            <div className="mt-3 space-y-3">
              <p className="break-words">{configurationName(job)}</p>
              <p className="break-all text-xs text-slate-500">ID: {job.id}{job.exit_code !== null ? ` · ${t('app.exitCode')}: ${job.exit_code}` : ''}</p>
              {job.bulk_progress && <BulkProgress progress={job.bulk_progress} subruns={job.bulk_subruns} state={job.state} language={language}/>}
              {job.cleanup && <p>{t('app.cleanup.done', { size: (job.cleanup.removedBytes / 1024 / 1024).toLocaleString(locale, { maximumFractionDigits: 1 }) })}</p>}
              {!['queued', 'running'].includes(job.state) && <Button variant="outline" tooltip={t('tooltip.deleteRun')} disabled={busy} onClick={() => deleteDialog.current?.showModal()}>{t('app.deleteRun')}</Button>}
            </div>
          </details>
          {job.outcomes && <details className="mb-4 text-sm"><summary className="cursor-pointer font-medium text-slate-600">{de ? 'Prüfung und Übernahmeberichte' : 'Validation and transfer reports'}</summary><div className="mt-3"><RunOutcomes outcomes={{ ...job.outcomes, generation: job.state }} language={language}/></div></details>}
        </>}
        <SelectionProgress log={logText} language={language} running={job?.state === 'running'}/>
        {!job?.cleanup && <details open={!!job && ['queued', 'running'].includes(job.state)}><summary className="cursor-pointer text-sm font-medium">{t('app.logs')}</summary><pre aria-label={t('app.logs')} className="mt-3 h-96 overflow-auto rounded-xl bg-slate-950 p-4 font-mono text-xs leading-5 whitespace-pre-wrap text-slate-200">{logText}</pre></details>}
      </section>
    </div>
    </div>
    <dialog ref={deleteDialog} aria-labelledby="delete-run-title" className="m-auto w-[min(36rem,calc(100%-2rem))] rounded-2xl border border-slate-200 p-6 shadow-xl backdrop:bg-slate-950/40">
      <h2 id="delete-run-title" className="mb-3 text-lg font-semibold">{t('app.cleanup.title')}</h2>
      {job && <p className="mb-4 break-words text-sm text-slate-600">{configurationName(job)}</p>}
      {job?.bulk_output_directory ? <p className="mb-3 text-sm">{t('app.bulk.deleteHelp')}</p> : <p className="mb-3 text-sm">{t('app.cleanup.keep')}</p>}
      {!job?.bulk_output_directory && <p className="mb-4 text-sm text-slate-600">{t('app.cleanup.remove')}</p>}
      <p className="mb-5 text-sm text-slate-600">{t('app.deleteRunConfirm')}</p>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" autoFocus onClick={() => deleteDialog.current?.close()}>{t('app.cleanup.cancel')}</Button>
        <Button disabled={busy || !job || !!job.bulk_output_directory || !['succeeded', 'failed'].includes(job.state)} onClick={() => void cleanRun()}>{t('app.cleanup.action')}</Button>
        <Button variant="outline" disabled={busy} onClick={() => void deleteRun()}>{t('app.cleanup.delete')}</Button>
      </div>
    </dialog>
  </main>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
