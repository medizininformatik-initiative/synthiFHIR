import { useState } from 'react'
import { Button } from './components/ui/button'
import { DocumentSettings } from './DocumentSettings'
import { defaults, type Configuration, type DocumentCoding } from './configuration'
import { InterfaceError, translate, type Language, type TextKey } from './i18n'

type Counts = { resources: number; patients: number; cases: number; skipped: number }
type ResourceChoice = { resourceType: string; codePath: string | null; statusEditable: boolean; patient: Counts; encounter: Counts | null }
type Catalogue = { sourceRun: string; fingerprint: string; types: ResourceChoice[] }
export type AdditionSettings = { resourceType: string; association: 'patient' | 'encounter'; status: string; codingMode: string; codings: DocumentCoding[]; nlpMode: string; nlpCodes: string[] }
const field = 'w-full rounded-lg border border-slate-300 bg-white p-2 text-sm'

export function ResourceAdditions({ jobId, language, onCreate }: {
  jobId: string; language: Language; onCreate: (settings: AdditionSettings, fingerprint: string) => Promise<void>
}) {
  const t = (key: string, params?: Record<string, string | number>) => translate(language, key as TextKey, params)
  const [open, setOpen] = useState(false)
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null)
  const [kind, setKind] = useState('DocumentReference')
  const [association, setAssociation] = useState<'patient' | 'encounter'>('encounter')
  const [config, setConfig] = useState<Configuration>(() => defaults())
  const [status, setStatus] = useState('')
  const [codingMode, setCodingMode] = useState('input')
  const [codings, setCodings] = useState<DocumentCoding[]>([])
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const choice = catalogue?.types.find(item => item.resourceType === kind)
  const counts = choice?.[association]
  const document = kind === 'DocumentReference'
  const values = config.values
  async function load() {
    setOpen(true); setBusy(true); setError('')
    try {
      const response = await fetch(`/api/jobs/${jobId}/additions`)
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || t('app.additions.failed'))
      setCatalogue(result)
      const first = result.types.find((item: ResourceChoice) => item.resourceType === 'DocumentReference') ?? result.types[0]
      if (first) { setKind(first.resourceType); setAssociation(first.encounter?.resources ? 'encounter' : 'patient') }
    } catch (error) { setError(error instanceof InterfaceError ? t(error.messageKey, error.params) : error instanceof Error ? error.message : t('app.additions.failed')) }
    finally { setBusy(false) }
  }
  async function start() {
    if (!catalogue) return
    setBusy(true); setError('')
    try {
      await onCreate({ resourceType: kind, association, status,
        codingMode: document ? String(values['document.type.mode']) : codingMode,
        codings: document ? values['document.type.values'] as DocumentCoding[] : codings,
        nlpMode: document ? String(values['document.nlp.mode']) : 'input',
        nlpCodes: document ? values['document.nlp.values'] as string[] : [] }, catalogue.fingerprint)
      setOpen(false)
    } catch (error) { setError(error instanceof InterfaceError ? t(error.messageKey, error.params) : error instanceof Error ? error.message : t('app.additions.failed')) }
    finally { setBusy(false) }
  }
  return <section className="my-5 border-t border-slate-200 pt-4">
    <Button variant="outline" tooltip={t('app.additions.actionHelp')} onClick={() => open ? setOpen(false) : void load()} aria-expanded={open}>
      {t('app.additions.action')}
    </Button>
    {open && <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <h3 className="font-semibold">{t('app.additions.action')}</h3>
      <p className="mt-2 text-sm text-slate-600">{t('app.additions.intro')}</p>
      {busy && <p role="status" className="mt-3 text-sm">{t('app.additions.working')}</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      {catalogue?.types.length === 0 && <p className="mt-3 text-sm">{t('app.additions.empty')}</p>}
      {choice && <fieldset disabled={busy} onChange={() => setPreview(false)} className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm">{t('app.additions.type')}<select className={field} value={kind} onChange={event => {
            const next = catalogue!.types.find(item => item.resourceType === event.target.value)!
            setKind(next.resourceType); setStatus(''); setCodingMode('input'); setCodings([])
            if (!next.encounter) setAssociation('patient')
          }}>{catalogue!.types.map(item => <option key={item.resourceType}>{item.resourceType}</option>)}</select></label>
          <label className="text-sm">{t('app.additions.association')}<select className={field} value={association} onChange={event => setAssociation(event.target.value as 'patient' | 'encounter')}>
            <option value="patient">{t('app.additions.patient')}</option>
            {choice.encounter && <option value="encounter">{t('app.additions.encounter')}</option>}
          </select></label>
        </div>
        <p className="text-sm">{t(association === 'encounter' ? 'app.additions.encounterHelp' : 'app.additions.patientHelp')}</p>
        {choice.statusEditable && <label className="block text-sm">{t('app.additions.status')}<input className={field} value={status} onChange={event => setStatus(event.target.value)} placeholder={t('app.additions.keep')}/></label>}
        {document ? <DocumentSettings config={config} update={(id, value) => { setConfig(old => ({ ...old, values: { ...old.values, [id]: value } })); setPreview(false) }} t={t}/> : choice.codePath && <section className="space-y-3">
          <label className="block text-sm">{choice.resourceType}.{choice.codePath}<select className={field} value={codingMode} onChange={event => setCodingMode(event.target.value)}>
            <option value="input">{t('app.additions.keep')}</option><option value="replace">{t('document.mode.replace')}</option>
          </select></label>
          {codingMode === 'replace' && <>
            {codings.map((coding, index) => <div key={index} className="flex flex-wrap items-end gap-2">
              {(['system', 'code', 'display'] as const).map(name => <label key={name} className="min-w-32 flex-1 text-sm">{t(`document.${name}`)}<input className={field} value={coding[name] ?? ''} onChange={event => setCodings(old => old.map((item, i) => i === index ? { ...item, [name]: event.target.value } : item))}/></label>)}
              <Button type="button" variant="outline" tooltip={t('document.remove')} aria-label={t('document.remove')} onClick={() => { setCodings(old => old.filter((_, i) => i !== index)); setPreview(false) }}>−</Button>
            </div>)}
            <Button type="button" variant="outline" tooltip={t('document.add')} aria-label={t('document.add')} onClick={() => { setCodings(old => [...old, {system:'',code:'',display:''}]); setPreview(false) }}>+</Button>
          </>}
        </section>}
        <Button type="button" variant="outline" disabled={!counts?.resources} onClick={() => setPreview(true)}>{t('app.additions.preview')}</Button>
        {counts?.skipped ? <p className="text-sm text-amber-800">{t('app.additions.skipped', { count: counts.skipped })}</p> : null}
        {preview && counts && <div className="rounded-lg border border-teal-200 bg-teal-50 p-4">
          <p className="font-medium">{t('app.additions.summary', { count: counts.resources, type: kind, patients: counts.patients, cases: counts.cases })}</p>
          <p className="mt-2 text-sm">{t('app.additions.references')}</p>
          <p className="mt-2 text-sm">{t('app.additions.copyHelp')}</p>
          <Button className="mt-3" onClick={() => void start()}>{t('app.additions.start')}</Button>
        </div>}
      </fieldset>}
    </div>}
  </section>
}
