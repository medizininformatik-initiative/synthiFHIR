import { ResourceStatistics, type Statistics } from './ResourceStatistics'
import { useEffect, useState } from 'react'
import { Button } from './components/ui/button'
import { translate, type Language } from './i18n'

type Package = { statistics?: Statistics; id: string; name: string; filename?: string; size: number; inspection: { patients: number } }
export function BulkPackages({ jobId, language, onUpload }: { jobId: string; language: Language; onUpload: (ids: string[]) => void }) {
  const [packages, setPackages] = useState<Package[]>([])
  const [failed, setFailed] = useState(false)
  const [limit, setLimit] = useState(10)
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/artifacts`)
        if (!response.ok) throw new Error()
        const manifest = await response.json()
        if (manifest.error) throw new Error()
        if (active) {
          setPackages(manifest.datasets); setFailed(false)
          if (manifest.pending) timer = setTimeout(() => void refresh(), 3000)
        }
      } catch { if (active) setFailed(true) }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [jobId])
  const t = (key: Parameters<typeof translate>[1]) => translate(language, key)
  return <div className="space-y-2">
    {failed && <p role="alert" className="text-red-700">{t('app.datasets.failed')}</p>}
    {!!packages.length && <><Button onClick={() => onUpload(packages.map(item => item.id))}>{t('app.upload.open')}</Button>
      <details><summary className="cursor-pointer">{packages.every(item => item.filename?.endsWith('.ndjson.gz')) ? 'NDJSON Gzip' : packages.every(item => item.filename?.endsWith('.ndjson')) ? 'NDJSON' : 'ZIP'} · {packages.length} {language === 'de' ? 'Pakete' : 'packages'}</summary>
        <ul className="mt-2 space-y-1 text-sm">{packages.slice(0,limit).map(item => <li key={item.id}><a className="text-teal-800 underline" href={`/api/datasets/${item.id}/download`}>{item.filename ?? `${item.name}.zip`}</a> · {item.inspection.patients} {language === 'de' ? 'Patienten' : 'patients'} · {(item.size / 1e6).toLocaleString(language, {maximumFractionDigits:1})} MB<ResourceStatistics datasetId={item.id} statistics={item.statistics} language={language}/></li>)}</ul>
        {limit < packages.length && <Button variant="outline" onClick={() => setLimit(value => value + 20)}>{language === 'de' ? 'Weitere Pakete anzeigen' : 'Show more packages'}</Button>}
      </details></>}
  </div>
}
