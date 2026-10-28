import { ResourceStatistics, type Statistics } from './ResourceStatistics'
import { datasetLabel, type DatasetContext } from './dataset-label'
import { useEffect, useState, type ReactNode } from 'react'
import { Help } from './Help'
import { Download } from 'lucide-react'
import { Button } from './components/ui/button'
import { translate, type Language, type TextKey } from './i18n'

type Report = { validationErrors: number; validationWarnings: number; status: string | null; issues: number; omissions: number; failures: number; derivations: number; selections: number; reasons: Record<string, number> }
type Artifact = { id: string; path: string; size: number; report?: Report | null }
type Dataset = { statistics?: Statistics; id: string; name: string; path?: string; error?: string; size?: number; inspection?: { patients: number; uniqueResources: number; resourceInstances: number; repeatedIds: number; missingIds: number; resourceCounts: Record<string, number>; inspectedFormats: string[] } }
type Manifest = { pending?: boolean; error?: string; datasets: Dataset[]; artifacts: Artifact[] }
export function DatasetResults({ jobId, state, language, context, onUpload, inputKind, downloadAvailable, children }: { children?: ReactNode; downloadAvailable: boolean; inputKind?: string; jobId: string; state: string; language: Language; context: DatasetContext; onUpload: (ids: string[]) => void }) {
  const t = (key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) => translate(language, key, params)
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [failed, setFailed] = useState(false)
  const [search, setSearch] = useState('')
  useEffect(() => {
    let active = true
    setManifest(null); setFailed(false); setSearch('')
    if (!['succeeded', 'failed'].includes(state)) return
    let timer: ReturnType<typeof setTimeout> | undefined
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
  }, [jobId, state])
  const csvArchive = manifest?.artifacts.find(file => file.path.endsWith('/csv.zip'))
  const runDownloads = <>
    {csvArchive && <Button variant="outline" asChild><a href={`/api/jobs/${jobId}/artifacts/${csvArchive.id}`}><Download size={14}/>{t('app.tableFormat.download')}</a></Button>}
    {['succeeded', 'failed'].includes(state) && (['workbook', 'workbook-set'].includes(inputKind ?? '') || manifest?.artifacts.some(file => file.path.endsWith('.xlsx'))) && <div className="flex items-center gap-2"><Button variant="outline" asChild><a href={`/api/jobs/${jobId}/inputs-download`} aria-label={t('app.datasets.inputsDownload')}><Download size={14}/>{t('app.datasets.inputsShort')}</a></Button><Help text={t('app.datasets.inputsHint')} t={key => t(key as TextKey)}/></div>}
    {downloadAvailable && <div className="flex items-center gap-2"><Button variant="outline" asChild><a href={`/api/jobs/${jobId}/download`} aria-label={t('app.download')}><Download size={14}/>{t('app.datasets.runShort')}</a></Button><Help text={t('app.downloadHelp')} t={key => t(key as TextKey)}/></div>}
  </>
  const frame = (content: ReactNode, withDatasets = false, files?: ReactNode) => <div className="mb-6 space-y-4">
    <h3 className="font-semibold">{t('app.datasets.title')}</h3>
    {!withDatasets && <div className="flex flex-wrap items-center gap-3">{runDownloads}</div>}
    {content}
    {children}
    {files}
  </div>
  if (failed) return frame(<p role="alert" className="text-sm text-red-700">{t('app.datasets.failed')}</p>)
  if (manifest?.error) return frame(<p role="alert" className="text-sm text-red-700">{manifest.error}</p>)
  if (manifest?.pending) return frame(<p className="text-sm text-slate-500">{t('app.datasets.pending')}</p>)
  if (!manifest || (manifest.datasets.length === 0 && manifest.artifacts.length === 0)) return downloadAvailable ? frame(null) : <>{children}</>
  const artifacts = manifest.artifacts.filter(file => file.path.toLowerCase().includes(search.toLowerCase()))
  const fileGroups = [
    { id: 'json', label: 'app.datasets.jsonFiles' },
    { id: 'ndjson', label: 'app.datasets.ndjsonFiles' },
    { id: 'xml', label: 'app.datasets.xmlFiles' },
    { id: 'excel', label: 'app.datasets.excelFiles' },
    { id: 'logs', label: 'app.datasets.logFiles' },
    { id: 'csv', label: 'app.datasets.csvFiles' },
    { id: 'other', label: 'app.datasets.otherFiles' },
  ] as const
  const groupOf = (file: Artifact) => {
    const extension = file.path.split('.').pop()?.toLowerCase()
    if (extension === 'json') return 'json'
    if (['ndjson', 'jsonl'].includes(extension ?? '')) return 'ndjson'
    if (extension === 'xml') return 'xml'
    if (['xlsx', 'xls', 'xlsm'].includes(extension ?? '')) return 'excel'
    if (extension === 'log' || /(^|\/)logs?\//i.test(file.path)) return 'logs'
    if (extension === 'csv' || file.path.endsWith('/csv.zip')) return 'csv'
    return 'other'
  }
  const isFhir = (file: Artifact) => manifest.datasets.some(dataset => dataset.path && file.path.startsWith(`${dataset.path}/`))
  const download = (file: Artifact) => <a className="break-all text-teal-800 underline" href={`/api/jobs/${jobId}/artifacts/${file.id}`}>{file.path}</a>
  return frame(<>
    {state === 'succeeded' && manifest.datasets.some(dataset => dataset.size !== undefined && !dataset.error) && <Button onClick={() => onUpload(manifest.datasets.filter(dataset => dataset.size !== undefined && !dataset.error).map(dataset => dataset.id))}>{t('app.upload.open')}</Button>}
    {state === 'failed' && <p className="text-sm text-amber-800">{t('app.datasets.incomplete')}</p>}
    {manifest.datasets.map(dataset => <section key={dataset.id} className="rounded-xl border border-slate-200 p-4">
      <h4 className="font-semibold">{datasetLabel({ ...dataset, ...context }, language)}</h4>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {dataset.size !== undefined && <div className="flex items-center gap-2"><Button variant="outline" asChild><a href={`/api/datasets/${dataset.id}/download`} aria-label={t('app.datasets.download')}><Download size={14}/>{t('app.datasets.downloadShort')}</a></Button><Help text={t('app.datasets.downloadHint')} t={key => t(key as TextKey)}/></div>}
        {runDownloads}
      </div>
      {dataset.error && <p role="alert" className="mt-2 text-sm text-red-700">{dataset.error}</p>}
      {!dataset.statistics && dataset.inspection && <>
        <p className="mt-2 text-sm">{t('app.datasets.counts', { patients: dataset.inspection.patients, resources: dataset.inspection.uniqueResources })}</p>
        <p className="mt-2 text-xs text-slate-500">{t('app.datasets.countHint', { formats: dataset.inspection.inspectedFormats.join(', '), repeated: dataset.inspection.repeatedIds, missing: dataset.inspection.missingIds })}</p>
        <details className="mt-3 text-sm"><summary className="cursor-pointer">{t('app.datasets.resources')}</summary><table className="mt-2 w-full"><tbody>{Object.entries(dataset.inspection.resourceCounts).map(([type, count]) => <tr key={type} className="border-t"><th className="py-1 text-left font-normal">{type}</th><td className="text-right">{count}</td></tr>)}</tbody></table></details>
      </>}
      <ResourceStatistics datasetId={dataset.id} statistics={dataset.statistics} language={language}/>
    </section>)}
  </>, manifest.datasets.length > 0, <>
    <details className="text-sm"><summary className="cursor-pointer font-medium">{t('app.datasets.files', { count: manifest.artifacts.length })}</summary>
      <p className="mt-2 text-xs text-slate-500">{t('app.datasets.fileHint')}</p>
      <input className="mt-3 w-full rounded-lg border border-slate-300 p-2" aria-label={t('app.datasets.search')} placeholder={t('app.datasets.search')} value={search} onChange={e => setSearch(e.target.value)}/>
      <div className="mt-3 space-y-3">{fileGroups.map(group => {
        const files = artifacts.filter(file => groupOf(file) === group.id)
        if (group.id === 'json') files.sort((a, b) => Number(isFhir(b)) - Number(isFhir(a)))
        if (files.length === 0) return null
        return <details key={group.id} open={group.id === 'json'} className="rounded-lg border border-slate-200 p-3">
          <summary className="cursor-pointer font-medium">{t(group.label, { count: files.length })}</summary>
          <ul className="mt-3 max-h-72 space-y-2 overflow-auto">{files.slice(0, 200).map(file => <li key={file.id}>{download(file)} <span className="whitespace-nowrap text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KiB</span></li>)}</ul>
          {files.length > 200 && <p className="mt-2 text-xs text-slate-500">{t('app.datasets.moreFiles')}</p>}
        </details>
      })}</div>
      {artifacts.length === 0 && <p className="mt-3 text-slate-500">{t('app.datasets.noFiles')}</p>}
    </details>
    <details className="text-sm"><summary className="cursor-pointer font-medium">{t('app.datasets.reports')}</summary>
      <div className="mt-3 space-y-3">{manifest.artifacts.filter(file => file.report).map(file => <div key={file.id} className="rounded-lg border p-3">
        {download(file)}
        <p className="mt-2">{file.report!.status ? `${file.report!.status} · ` : ''}{t('app.datasets.reportCounts', { issues: file.report!.issues, omissions: file.report!.omissions, failures: file.report!.failures, derivations: file.report!.derivations, selections: file.report!.selections, errors: file.report!.validationErrors, warnings: file.report!.validationWarnings })}</p>
        {Object.keys(file.report!.reasons).length > 0 && <ul className="mt-2 space-y-1 text-slate-600">{Object.entries(file.report!.reasons).map(([reason, count]) => <li key={reason}>{reason}: {count}</li>)}</ul>}
      </div>)}</div>
    </details>
  </>)
}
