import { OutputDirectory } from './OutputDirectory'
import { ResourceStatistics, type Statistics } from './ResourceStatistics'
import { type DatasetContext } from './dataset-label'
import { useEffect, useState } from 'react'
import { Download, Upload } from 'lucide-react'
import { Button } from './components/ui/button'
import { translate, type Language } from './i18n'

type Artifact = { id: string; path: string; size: number; report?: unknown }
type Dataset = { statistics?: Statistics; id: string; name: string; kind?: string; filename?: string; path?: string; error?: string; size?: number; files?: Artifact[]; inspection?: { patients: number; uniqueResources?: number; inspectedFormats?: string[] } }
type Manifest = { pending?: boolean; error?: string; datasets: Dataset[]; artifacts: Artifact[] }
const fhirFile = (path: string) => /\.(json|ndjson|jsonl|xml)(\.(gz|bz2|zip))?$/i.test(path)
export function DatasetResults({ jobId, state, language, context, onUpload, inputKind, downloadAvailable, outputDirectory }: { outputDirectory?: string; downloadAvailable: boolean; inputKind?: string; jobId: string; state: string; language: Language; context: DatasetContext; onUpload: (ids: string[]) => void }) {
  const de = language === 'de'
  const t = (key: Parameters<typeof translate>[1]) => translate(language, key)
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [failed, setFailed] = useState(false)
  const [limit, setLimit] = useState(10)
  const [search, setSearch] = useState('')
  const [fhirSearch, setFhirSearch] = useState('')
  const ended = ['succeeded', 'failed'].includes(state)
  const number = (value: number) => value.toLocaleString(language)
  const size = (value: number) => value < 1e6 ? `${number(Math.round(value / 1000))} kB` : `${(value / 1e6).toLocaleString(language, { maximumFractionDigits: 1 })} MB`
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    setManifest(null); setFailed(false); setLimit(10); setSearch(''); setFhirSearch('')
    if (!ended) return
    async function refresh() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/artifacts`)
        if (!response.ok) throw new Error()
        const value: Manifest = await response.json()
        if (!active) return
        setManifest(value); setFailed(false)
        if (value.pending) timer = setTimeout(() => void refresh(), 3000)
      } catch { if (active) { setFailed(true); timer = setTimeout(() => void refresh(), 5000) } }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [jobId, ended])
  const datasets = manifest?.datasets ?? []
  const artifacts = manifest?.artifacts ?? []
  const ready = datasets.filter(item => item.size !== undefined && !item.error)
  const bulk = datasets.some(item => item.kind === 'bulk')
  const files = datasets.flatMap(item => (item.files ?? []).filter(file => fhirFile(file.path)))
  const formats = [...new Set((bulk ? datasets.map(item => item.filename ?? item.path ?? '') : files.map(file => file.path)).map(path => /\.ndjson(\.(gz|bz2|zip))?$/i.test(path) ? 'NDJSON' : /\.xml(\.(gz|bz2|zip))?$/i.test(path) ? 'XML' : /\.jsonl$/i.test(path) ? 'NDJSON' : /\.json(\.(gz|bz2|zip))?$/i.test(path) || path.endsWith('.zip') ? 'JSON' : '').filter(Boolean))]
  const gzip = bulk && datasets.every(item => (item.filename ?? item.path ?? '').endsWith('.gz'))
  const csv = artifacts.filter(file => file.path.endsWith('/csv.zip'))
  const excel = ['workbook', 'workbook-set'].includes(inputKind ?? '') || artifacts.some(file => file.path.endsWith('.xlsx'))
  const sourceDownload = ['csv', 'synthea', 'synthea-zip'].includes(inputKind ?? '')
  const patients = bulk ? (datasets.length && datasets.every(item => item.inspection) ? datasets.reduce((total, item) => total + item.inspection!.patients, 0) : undefined) : context.summary?.patients ?? (datasets.length === 1 ? datasets[0].inspection?.patients : undefined)
  const resources = datasets.length === 1 ? datasets[0].inspection?.uniqueResources : undefined
  const visibleArtifacts = artifacts.filter(file => file.path.toLowerCase().includes(search.toLowerCase()))
  return <div className="space-y-5">
    <section className="rounded-xl border border-teal-200 bg-teal-50/60 p-5" aria-label={de ? 'FHIR-Daten' : 'FHIR data'}>
      <h3 className="text-xl font-semibold text-teal-950">{de ? 'FHIR-Daten' : 'FHIR data'}</h3>
      <p className="mt-1 text-sm text-slate-600">{de ? 'Die erzeugten Daten herunterladen oder auf einen FHIR-Server laden.' : 'Download the generated data or upload it to a FHIR server.'}</p>
      {!!datasets.length && <>
        <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm font-medium">
          {patients != null && <span><strong className="text-lg">{number(patients)}</strong> {de ? (patients === 1 ? 'Patient' : 'Patienten') : (patients === 1 ? 'patient' : 'patients')}</span>}
          {!bulk && context.summary?.cases != null && <span><strong className="text-lg">{number(context.summary.cases)}</strong> {de ? (context.summary.cases === 1 ? 'Fall' : 'Fälle') : (context.summary.cases === 1 ? 'case' : 'cases')}</span>}
          {resources != null && <span>{number(resources)} {de ? 'Ressourcen' : 'resources'}</span>}
        </div>
        <p className="mt-2 text-sm text-slate-600">{formats.join(' / ') || 'FHIR'}{gzip ? ' · gzip' : ''} · {number(bulk ? datasets.length : files.length)} {de ? (bulk ? (datasets.length === 1 ? 'Paket' : 'Pakete') : (files.length === 1 ? 'Datei' : 'Dateien')) : (bulk ? (datasets.length === 1 ? 'package' : 'packages') : (files.length === 1 ? 'file' : 'files'))}{ready.length > 0 && ` · ${size(bulk ? ready.reduce((total, item) => total + item.size!, 0) : files.reduce((total, file) => total + file.size, 0))} ${de ? (bulk ? 'in Paketen' : 'Dateiinhalt') : (bulk ? 'in packages' : 'file content')}`}</p>
      </>}
      {failed && <p role="alert" className="mt-3 text-sm text-red-700">{t('app.datasets.failed')}</p>}
      {manifest?.error && <p role="alert" className="mt-3 text-sm text-red-700">{manifest.error}</p>}
      {manifest?.pending && <p role="status" className="mt-3 text-sm">{t('app.datasets.pending')}</p>}
      {!ended && <p role="status" className="mt-3 text-sm">{de ? 'Die Ergebnisse erscheinen hier, sobald der Lauf abgeschlossen ist.' : 'Results appear here when the run has finished.'}</p>}
      {ended && manifest && !manifest.pending && !manifest.error && !datasets.length && <p className="mt-3 text-sm">{de ? 'Für diesen Lauf sind keine FHIR-Ergebnisse verfügbar.' : 'No FHIR results are available for this run.'}</p>}
      {(state === 'failed' || datasets.some(item => item.error)) && <p role="alert" className="mt-3 text-sm text-amber-900">{de ? 'Die Ergebnisse können unvollständig sein. Prüfe die verfügbaren Dateien vor der Verwendung.' : 'Results may be incomplete. Check the available files before using them.'}</p>}
      {ready.length > 0 && <>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button asChild><a href={`/api/jobs/${jobId}/fhir-download`}><Download size={16}/>{de ? 'Alle FHIR-Daten herunterladen' : 'Download all FHIR data'}</a></Button>
          {state === 'succeeded' && <Button variant="outline" onClick={() => onUpload(ready.map(item => item.id))}><Upload size={16}/>{t('app.upload.open')}</Button>}
        </div>
        <p className="mt-2 text-xs text-slate-600">{de ? 'ZIP mit den FHIR-Dateien dieses Laufs. Je Datensatz oder Paket liegen die Dateien zusammen in einem Ordner. Der Upload öffnet die Serverauswahl.' : 'ZIP containing this run’s FHIR files, grouped in one folder per dataset or package. Upload opens the server selection.'}</p>
      </>}
      {!!datasets.length && <details className="mt-4 border-t border-teal-200 pt-3 text-sm">
        <summary className="cursor-pointer font-medium">{de ? (bulk ? 'Einzelne Pakete und Inhalt' : 'Einzelne Dateien und Datensätze') : 'Individual files and datasets'}</summary>
        <p className="mt-2 text-xs text-slate-600">{de ? 'Downloads und Upload beziehen sich jeweils auf den angegebenen Datensatz. Mehrere KDS-Varianten bleiben getrennt.' : 'Downloads and uploads apply to the specified dataset. Multiple KDS variants remain separate.'}</p>
        {!bulk && <input className="mt-3 w-full rounded-lg border border-slate-300 bg-white p-2" aria-label={de ? 'FHIR-Dateien suchen' : 'Search FHIR files'} placeholder={de ? 'FHIR-Dateien nach Namen suchen' : 'Search FHIR files by name'} value={fhirSearch} onChange={event => setFhirSearch(event.target.value)}/>}
        <div className="mt-3 space-y-3">{datasets.slice(0, limit).map((item, index) => <div key={item.id} className="rounded-lg border border-slate-200 bg-white p-3">
          <h4 className="break-words font-medium">{bulk ? `${de ? 'Paket' : 'Package'} ${index + 1}` : `${de ? 'Datensatz' : 'Dataset'} ${index + 1} · ${item.name}`}</h4>
          {item.inspection && <p className="mt-1 text-xs text-slate-600">{number(item.inspection.patients)} {de ? 'Patienten' : 'patients'}{item.size != null && ` · ${size(item.size)}`}</p>}
          {item.error && <p role="alert" className="mt-2 text-red-700">{item.error}</p>}
          {item.size != null && <div className="mt-2 flex flex-wrap gap-3"><a className="text-teal-800 underline" href={`/api/datasets/${item.id}/download`}>{bulk ? item.filename ?? `${item.name}.zip` : de ? 'Datensatz herunterladen (ZIP)' : 'Download dataset (ZIP)'}</a>{state === 'succeeded' && !item.error && <button className="text-teal-800 underline" onClick={() => onUpload([item.id])}>{t('app.upload.open')}</button>}</div>}
          {!bulk && <details className="mt-2"><summary className="cursor-pointer">{de ? 'FHIR-Dateien anzeigen' : 'Show FHIR files'}</summary><ul className="mt-2 max-h-64 space-y-2 overflow-auto">{(item.files ?? []).filter(file => fhirFile(file.path) && file.path.toLowerCase().includes(fhirSearch.toLowerCase())).slice(0, 200).map(file => <li key={file.id}><a className="break-all text-teal-800 underline" title={file.path} href={`/api/jobs/${jobId}/artifacts/${file.id}`}>{file.path.slice((item.path?.length ?? -1) + 1)}</a> <span className="text-xs text-slate-500">{size(file.size)}</span></li>)}</ul>{(item.files ?? []).filter(file => fhirFile(file.path) && file.path.toLowerCase().includes(fhirSearch.toLowerCase())).length > 200 && <p className="mt-2 text-xs text-slate-500">{de ? 'Die ersten 200 Treffer werden angezeigt. Grenze die Suche ein oder lade alle FHIR-Daten herunter.' : 'Showing the first 200 matches. Refine your search or download all FHIR data.'}</p>}</details>}
          <details className="mt-3"><summary className="cursor-pointer text-slate-600">{de ? 'Ressourcenstatistik' : 'Resource statistics'}</summary><ResourceStatistics datasetId={item.id} statistics={item.statistics} language={language}/></details>
        </div>)}</div>
        {limit < datasets.length && <Button className="mt-3" variant="outline" onClick={() => setLimit(value => value + 20)}>{de ? 'Weitere anzeigen' : 'Show more'}</Button>}
      </details>}
      {outputDirectory && <details className="mt-3 text-sm text-slate-600"><summary className="cursor-pointer">{de ? 'Lokalen Ergebnisordner finden' : 'Find the local results folder'}</summary><div className="mt-2"><OutputDirectory path={outputDirectory} language={language}/></div></details>}
    </section>
    <section className="rounded-xl border border-slate-200 p-4">
      <h3 className="text-lg font-semibold">{de ? 'Eingabedaten' : 'Input data'}</h3>
      <p className="mt-1 text-sm text-slate-600">{inputKind?.startsWith('synthea') ? (de ? 'Die Synthea-Quelle und die für die Konvertierung erzeugten Tabellen, soweit für diesen Lauf gespeichert.' : 'The Synthea source and tables generated for conversion, where saved for this run.') : (de ? 'Die gespeicherten Eingaben dieses Laufs herunterladen und weiterbearbeiten.' : 'Download and edit the saved inputs for this run.')}</p>
      {ended && <div className="mt-3 flex flex-wrap gap-2">
        {excel && <Button variant="outline" asChild><a href={`/api/jobs/${jobId}/inputs-download`}><Download size={14}/>{de ? 'Excel und Einstellungen (ZIP)' : 'Excel and settings (ZIP)'}</a></Button>}
        {csv.map((file, index) => <Button key={file.id} variant="outline" asChild><a href={`/api/jobs/${jobId}/artifacts/${file.id}`}><Download size={14}/>{de ? 'CSV-Tabellen (ZIP)' : 'CSV tables (ZIP)'}{csv.length > 1 ? ` · ${index + 1}` : ''}</a></Button>)}
        {sourceDownload && <Button variant="outline" asChild><a href={`/api/jobs/${jobId}/source-download`}><Download size={14}/>{inputKind === 'csv' ? (de ? 'CSV-Eingabe (ZIP)' : 'CSV input (ZIP)') : (de ? 'Synthea-Eingabe' : 'Synthea input')}</a></Button>}
      </div>}
      {(!ended || (!excel && !csv.length && !sourceDownload)) && <p className="mt-2 text-sm text-slate-500">{de ? (bulk ? 'Massendatenläufe speichern die FHIR-Ergebnisse; die Zwischentabellen werden nach der Konvertierung entfernt.' : 'Für diesen Lauf sind derzeit keine Tabelleneingaben zum Download verfügbar.') : (bulk ? 'Bulk runs retain FHIR results; intermediate tables are removed after conversion.' : 'No input tables are currently available to download for this run.')}</p>}
    </section>
    {(artifacts.length > 0 || downloadAvailable) && <details className="text-sm text-slate-600"><summary className="cursor-pointer font-medium">{de ? 'Berichte und weitere Dateien' : 'Reports and other files'}</summary>
      <p className="mt-2">{de ? 'Technische Begleitdateien und Berichte zur Prüfung des Laufs.' : 'Supporting technical files and reports for inspecting this run.'}</p>
      {downloadAvailable && <a className="mt-3 inline-block text-teal-800 underline" href={`/api/jobs/${jobId}/download`}>{de ? 'Vollständiges Laufarchiv (ZIP)' : 'Complete run archive (ZIP)'}</a>}
      <input className="mt-3 w-full rounded-lg border border-slate-300 p-2" aria-label={t('app.datasets.search')} placeholder={t('app.datasets.search')} value={search} onChange={e => setSearch(e.target.value)}/>
      <ul className="mt-3 max-h-72 space-y-2 overflow-auto">{visibleArtifacts.slice(0, 200).map(file => <li key={file.id}><a className="break-all text-teal-800 underline" href={`/api/jobs/${jobId}/artifacts/${file.id}`}>{file.path}</a> <span className="text-xs">{size(file.size)}</span></li>)}</ul>
      {visibleArtifacts.length > 200 && <p className="mt-2">{t('app.datasets.moreFiles')}</p>}
    </details>}
  </div>
}
