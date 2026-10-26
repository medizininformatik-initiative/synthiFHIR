import { useEffect, useId, useState, type ReactNode } from 'react'
import { BulkOutputSettings } from './BulkOutputSettings'
import { Help } from './Help'
import { generationErrors, seedValid, type GenerationErrors } from './generation-validation'
import { generationHelp, selectionRule, moduleHelp, type HelpKey, type ModuleDescription } from './generation-help'
import { translate, type Language, type TextKey } from './i18n'

export type Generation = {
  bulkGzip?: boolean; executionMode?: 'standard' | 'bulk'; syntheticConsent?: boolean; patientsPerRun?: number;
  outputMode: 'kds' | 'synthea'; population: number; minAge: number; maxAge: number; gender: string;
  timestampSeeds?: boolean; patientSeed: string; clinicianSeed: string; singlePersonSeed: string;
  referenceDate: string; endDate: string; state: string; city: string;
  yearsOfHistory: number; patientFilter: string; overflow: boolean;
  inpatientOnly?: boolean; inpatientMinDays?: number;
  modules: string[]; keepModule: string; timestepDays: number; maxAttempts: number; veteranPopulation: boolean
}
type Catalogue = { bulkOutputDirectory: string; revision: string; defaults: Generation; locations: Record<string, string[]>; modules: ModuleDescription[]; keepModules: ModuleDescription[] }
export function GenerationSettings({ bulkGzip, onBulkGzipChange, language, onChange, initialValue, onExecutionModeChange, children, seriesControls, continuationLocked = false, sourceOffset = 0 }: { bulkGzip: boolean; onBulkGzipChange: (value: boolean) => void; sourceOffset?: number; seriesControls?: ReactNode; continuationLocked?: boolean; children?: ReactNode; onExecutionModeChange: (mode: 'standard' | 'bulk') => void; language: Language; onChange: (value: Generation | null) => void; initialValue?: Generation }) {
  const fieldId = useId()
  const t = (key: TextKey) => translate(language, key)
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null)
  const [value, setValue] = useState<Generation | null>(null)
  useEffect(() => { onExecutionModeChange(value?.executionMode ?? 'standard') }, [value?.executionMode, onExecutionModeChange])
  const [memory, setMemory] = useState<{ ready: boolean; availableBytes: number | null } | null>(null)
  useEffect(() => {
    if (value?.executionMode !== 'bulk') { setMemory(null); return }
    let active = true
    const refresh = () => fetch('/api/bulk-resources').then(r => { if (!r.ok) throw new Error(); return r.json() }).then(next => { if (active) setMemory(next) }).catch(() => { if (active) setMemory(null) })
    void refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [value?.executionMode])
  const [failed, setFailed] = useState(false)
  const [moduleSearch, setModuleSearch] = useState('')
  useEffect(() => {
    let active = true
    fetch('/api/generation-catalogue').then(async response => {
      if (!response.ok) throw new Error()
      const next: Catalogue = await response.json()
      if (!active) return
      setCatalogue(next)
      let saved = next.defaults
      try { const stored = JSON.parse(localStorage.getItem('syntheaGeneration') ?? 'null'); if (stored && Object.entries(next.defaults).every(([key, initial]) => stored[key] === undefined || (Array.isArray(initial) ? Array.isArray(stored[key]) && stored[key].every((item: unknown) => typeof item === 'string') : typeof stored[key] === typeof initial))) saved = { ...next.defaults, ...stored } } catch { /* Use the pinned defaults. */ }
      onBulkGzipChange((initialValue ?? saved).bulkGzip ?? true)
      setValue(initialValue ? { ...next.defaults, ...initialValue, timestampSeeds: false } : { ...saved, timestampSeeds: saved.timestampSeeds === true })
    }).catch(() => { if (active) setFailed(true) })
    return () => { active = false; onChange(null) }
  }, [onChange, initialValue, onBulkGzipChange])
  const errors: GenerationErrors = value && catalogue
    ? generationErrors(value, catalogue.locations, new Date().toLocaleDateString('sv-SE')) : {}
  const valid = value !== null && catalogue !== null && Object.keys(errors).length === 0 &&
    (value.executionMode !== 'bulk' || memory?.ready === true)
  useEffect(() => {
    if (value && Object.values(value).every(item => typeof item !== 'number' || Number.isFinite(item))) { try { localStorage.setItem('syntheaGeneration', JSON.stringify({ ...value, bulkGzip })) } catch { /* Submission still works. */ } }
    onChange(valid && value ? { ...value, bulkGzip } : null)
  }, [value, valid, onChange, bulkGzip])
  if (failed) return <p role="alert" className="mt-4 text-red-700">{t('app.generation.loadFailed')}</p>
  if (!value || !catalogue) return <p className="mt-4 text-sm">{t('app.generation.loading')}</p>
  const update = <K extends keyof Generation>(key: K, next: Generation[K]) => setValue(old => old && ({ ...old, [key]: next }))
  const info = (key: HelpKey, detail?: string) => <Help text={detail ?? generationHelp(language, key, value.executionMode === 'bulk')} t={key => t(key as TextKey)}/>
  const inputClass = 'mt-1 w-full rounded-lg border border-slate-300 bg-white p-2 disabled:cursor-not-allowed disabled:border-neutral-200 disabled:bg-neutral-50 disabled:text-neutral-400 disabled:shadow-none'
  const fieldProps = (key: keyof Generation) => ({
    className: errors[key] ? inputClass.replace('border-slate-300 bg-white', 'border-red-600 bg-red-50 outline-red-600') : inputClass,
    'aria-invalid': errors[key] ? true : undefined,
    'aria-describedby': errors[key] ? `${fieldId}-${key}-error` : undefined
  })
  const error = (key: keyof Generation) => errors[key] && <span id={`${fieldId}-${key}-error`} role="status" className="mt-1 block text-sm text-red-700">{translate(language, errors[key]!.key, errors[key]!.params)}</span>
  const numericValue = (amount: unknown) => typeof amount === 'number' && Number.isFinite(amount) ? amount : ''
  const readNumber = (raw: string) => raw === '' ? Number.NaN : Number(raw)
  const number = (key: keyof Generation, label: TextKey, min: number, max: number) => <label className="text-sm"><span className="field-caption"><span className="min-w-0">{t(label)}{key === 'population' && ` (${min}–${max})`}</span><span className="shrink-0">{info(key as HelpKey)}</span></span><input {...fieldProps(key)} type="number" min={min} max={max} step="1" value={numericValue(value[key])} onChange={e => update(key, readNumber(e.target.value))}/>{error(key)}</label>
  const text = (key: keyof Generation, label: TextKey, type = 'text') => {
    const disabled = value.timestampSeeds === true && ['patientSeed', 'clinicianSeed', 'singlePersonSeed'].includes(key)
    return <label className={`text-sm ${disabled ? 'text-neutral-400' : ''}`}><span className="field-caption"><span>{t(label)}</span>{info(key as HelpKey, key === 'patientSeed' && value.executionMode === 'bulk' ? t('app.generation.bulkSeedHelp') : undefined)}</span><input {...fieldProps(key)} type={type} disabled={disabled} value={String(value[key])} onChange={e => update(key, e.target.value)}/>{error(key)}</label>
  }
  return <section className="mt-6 border-t border-slate-200 pt-5">
    <h3 className="font-semibold">{t('app.generation.title')}</h3>
    <p className="mt-2 text-sm text-slate-600">{t('app.generation.hint')}</p>
    <label className="mt-4 block text-sm">{t('app.bulk.mode')}<Help text={t('app.bulk.modeHelp')} t={key => t(key as TextKey)}/><select disabled={continuationLocked} className={inputClass} value={value.executionMode ?? 'standard'} onChange={e => setValue({ ...value, executionMode: e.target.value as 'standard' | 'bulk', ...(e.target.value === 'bulk' ? { overflow: false } : {}) })}><option value="standard">{t('app.bulk.standard')}</option><option value="bulk">{t('app.bulk.title')}</option></select></label>
    {value.executionMode === 'bulk' && seriesControls}
    {continuationLocked && <div className="mt-4 max-w-xs">{number('population', 'app.generation.population', 1, 100000)}</div>}
    <fieldset disabled={continuationLocked}>
    {value.executionMode === 'bulk' ? <div className="mt-3 rounded-xl bg-teal-50 p-4 text-sm">
      <p className={`font-medium ${memory === null ? 'text-slate-600' : memory.ready ? 'text-teal-800' : 'text-red-700'}`} role="status">{t(memory === null ? 'app.bulk.memoryChecking' : memory.ready ? 'app.bulk.memoryReady' : 'app.bulk.memoryBlocked').replace('{available}', memory?.availableBytes == null ? '—' : (memory.availableBytes / 1024 ** 3).toFixed(1))}</p>
      <p className="mt-2">{t('app.bulk.directory')}: <code className="break-all">{catalogue.bulkOutputDirectory}</code></p>
      {value.outputMode === 'synthea' ? <BulkOutputSettings language={language} gzip={bulkGzip} onChange={onBulkGzipChange} disabled={continuationLocked}/> : <p className="mt-3">{t(bulkGzip ? 'app.bulk.format' : 'app.bulk.formatPlain')}</p>}
      <details className="mt-3 text-slate-600" open={memory !== null && !memory.ready || undefined}>
        <summary className="cursor-pointer">{t('app.bulk.memoryHelp')}</summary>
        <p className="mt-2">{t('app.bulk.prepare')}</p>
        <code className="mt-2 block select-all">bash web/prepare-bulk.sh</code>
        <p className="mt-2">{t('app.bulk.restore')}</p>
        <code className="mt-1 block select-all">bash web/restore-services.sh</code>
      </details>
    </div> : value.population >= 500 && <div className="mt-3 rounded-xl bg-amber-50 p-4 text-sm"><p>{t('app.bulk.recommend')}</p><button type="button" className="mt-2 underline" onClick={() => setValue({ ...value, executionMode: 'bulk', overflow: false })}>{t('app.bulk.switch')}</button></div>}
    <label className="mt-4 block text-sm">{t('app.generation.outputMode')}{info('outputMode')}<select className={inputClass} value={value.outputMode} onChange={e => setValue({ ...value, outputMode: e.target.value as Generation['outputMode'], syntheticConsent: e.target.value === 'kds' })}><option value="kds">{t('app.generation.kdsOutput')}</option><option value="synthea">{t('app.generation.nativeOutput')}</option></select></label>
    <div className="field-grid mt-4 grid gap-x-4 sm:grid-cols-2 lg:grid-cols-4">
      {!continuationLocked && number('population', 'app.generation.population', 1, value.executionMode === 'bulk' ? 100000 : 1000)}
      {value.executionMode === 'bulk' && <label className="text-sm"><span className="field-caption"><span>{t('app.bulk.patientsPerRun')}</span><Help text={t('app.bulk.subrunsHelp')} t={key => t(key as TextKey)}/></span><input {...fieldProps('patientsPerRun')} type="number" min={100} max={10000} step={100} value={numericValue(value.patientsPerRun ?? 10000)} onChange={e => update('patientsPerRun', readNumber(e.target.value))}/>{error('patientsPerRun')}</label>}
      {number('minAge', 'app.generation.minAge', 0, 140)}{number('maxAge', 'app.generation.maxAge', 0, 140)}
      <label className="text-sm sm:col-start-1"><span className="field-caption"><span>{t('app.generation.gender')}</span>{info('gender')}</span><select className={inputClass} value={value.gender} onChange={e => update('gender', e.target.value)}><option value="">{t('app.generation.anyGender')}</option><option value="F">{t('app.generation.female')}</option><option value="M">{t('app.generation.male')}</option></select></label>
      <label className="text-sm"><span className="field-caption"><span>{t('app.generation.patientFilter')}</span>{info('patientFilter')}</span><select className={inputClass} value={value.patientFilter} onChange={e => update('patientFilter', e.target.value)}><option value="all">{t('app.generation.allPatients')}</option><option value="alive">{t('app.generation.alive')}</option><option value="dead">{t('app.generation.dead')}</option></select></label>
      <label className={`flex min-h-[var(--control-height)] items-center gap-2 self-end text-sm ${value.patientFilter !== 'all' ? 'text-slate-400' : ''}`}><input type="checkbox" disabled={value.patientFilter !== 'all' || value.executionMode === 'bulk'} checked={value.overflow} onChange={e => update('overflow', e.target.checked)}/>{t('app.generation.overflow')}{info('overflow')}</label>
      {number('yearsOfHistory', 'app.generation.history', 0, 140)}
      <div className="flex items-center gap-1 text-sm sm:col-span-2 lg:col-span-4">
        <label className="flex items-center gap-2"><input type="checkbox" checked={value.timestampSeeds === true} onChange={e => update('timestampSeeds', e.target.checked)}/>{t('app.generation.timestampSeeds')}</label>
        <Help text={t('app.generation.timestampSeedsHint')} t={key => t(key as TextKey)}/>
      </div>
      {text('patientSeed', 'app.generation.patientSeed')}{text('clinicianSeed', 'app.generation.clinicianSeed')}
      {text('referenceDate', 'app.generation.referenceDate', 'date')}{text('endDate', 'app.generation.endDate', 'date')}
    </div>
    {value.executionMode === 'bulk' && !errors.population && !errors.patientsPerRun && <p className="mt-3 text-sm">{t('app.bulk.subrunsSummary').replace('{count}', String(Math.ceil(value.population / (value.patientsPerRun ?? 10000)))).replace('{size}', String(value.patientsPerRun ?? 10000))}</p>}
    {value.executionMode === 'bulk' && !value.timestampSeeds && seedValid(value.patientSeed) && !errors.population && <p className="mt-3 text-sm" aria-live="polite">{t('app.generation.seedRange').replace('{patients}', String(value.population)).replace('{count}', String(Math.ceil((sourceOffset + value.population) / 100))).replace('{first}', value.patientSeed).replace('{last}', String(BigInt.asIntN(64, BigInt(value.patientSeed) + BigInt(Math.ceil((sourceOffset + value.population) / 100) - 1))))}</p>}
    {value.outputMode === 'kds' && <label className="mt-4 flex items-center gap-2 text-sm"><input type="checkbox" checked={value.syntheticConsent === true} onChange={e => update('syntheticConsent', e.target.checked)}/>{t('app.generation.syntheticConsent')}<Help text={t('app.generation.syntheticConsentHelp')} t={key => t(key as TextKey)}/></label>}
    <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-3">
      <label className="flex min-h-[var(--control-height)] items-center gap-2 text-sm"><input type="checkbox" checked={value.inpatientOnly === true} onChange={e => update('inpatientOnly', e.target.checked)}/>{t('app.generation.inpatientOnly')}<Help text={t('app.generation.inpatientHelp')} t={key => t(key as TextKey)}/></label>
      <label className={`block w-full max-w-xs text-sm ${value.inpatientOnly ? '' : 'text-neutral-400'}`}>{t('app.generation.inpatientMinDays')}<input type="number" disabled={!value.inpatientOnly} {...fieldProps('inpatientMinDays')} min={1} max={36500} step={1} value={numericValue(value.inpatientMinDays ?? 2)} onChange={e => update('inpatientMinDays', readNumber(e.target.value))}/>{error('inpatientMinDays')}</label>
    </div>
    <details className="mt-5" open={!!(errors.state || errors.city || errors.singlePersonSeed || errors.timestepDays || errors.maxAttempts) || undefined}><summary className="cursor-pointer font-medium">{t('app.generation.advanced')}</summary>
      <div className="field-grid mt-4 grid gap-x-4 sm:grid-cols-2 lg:grid-cols-3">
        <label className="text-sm"><span className="field-caption"><span>{t('app.generation.state')}</span>{info('state')}</span><select {...fieldProps('state')} value={value.state} onChange={e => setValue({ ...value, state: e.target.value, city: '' })}>{Object.keys(catalogue.locations).map(state => <option key={state}>{state}</option>)}</select>{error('state')}</label>
        <label className="text-sm"><span className="field-caption"><span>{t('app.generation.city')}</span>{info('city')}</span><select {...fieldProps('city')} value={value.city} onChange={e => update('city', e.target.value)}><option value="">{t('app.generation.anyCity')}</option>{(catalogue.locations[value.state] ?? []).map(city => <option key={city}>{city}</option>)}</select>{error('city')}</label>
        <label className="text-sm"><span className="field-caption"><span>{t('app.generation.keepModule')}</span>{info('keepModule')}</span><select className={inputClass} value={value.keepModule} onChange={e => update('keepModule', e.target.value)}><option value="">{t('app.generation.noKeepModule')}</option>{catalogue.keepModules.map(module => <option key={module.id} value={module.id}>{selectionRule(language, module.id, module.name).name}</option>)}</select>{value.keepModule && <span className="mt-2 block text-xs text-slate-600">{selectionRule(language, value.keepModule, value.keepModule).description}</span>}</label>
        {text('singlePersonSeed', 'app.generation.singleSeed')}
        {number('timestepDays', 'app.generation.timestep', 1, 365)}{number('maxAttempts', 'app.generation.attempts', 1, 10000)}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.veteranPopulation} onChange={e => update('veteranPopulation', e.target.checked)}/>{t('app.generation.veterans')}{info('veteranPopulation')}</label>
      </div>
      <p className="mt-4 text-sm text-slate-600">{value.executionMode === 'bulk' ? generationHelp(language, 'population', true) : t('app.generation.selectionHint')}</p>
      <fieldset className="mt-5"><legend className="font-medium">{t('app.generation.modules')}{info('modules')}</legend>
        <p className="mt-2 text-sm text-slate-600">{t('app.generation.modulesHint')}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className="rounded-lg border border-slate-300 px-3 py-1 min-h-[var(--control-height)] text-sm hover:bg-slate-50" onClick={() => update('modules', catalogue.modules.map(module => module.id))}>{t('app.generation.selectAllModules')}</button>
          <button type="button" className="rounded-lg border border-slate-300 px-3 py-1 min-h-[var(--control-height)] text-sm hover:bg-slate-50" onClick={() => update('modules', [])}>{t('app.generation.clearModules')}</button>
        </div>
        <input className={inputClass} aria-label={t('app.generation.moduleSearch')} placeholder={t('app.generation.moduleSearch')} value={moduleSearch} onChange={e => setModuleSearch(e.target.value)}/>
        <div className="mt-2 grid max-h-64 gap-2 overflow-auto rounded-lg border p-3 sm:grid-cols-2">{catalogue.modules.filter(module => (module.name + module.id).toLowerCase().includes(moduleSearch.toLowerCase())).map(module => <label key={module.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.modules.includes(module.id)} onChange={e => update('modules', e.target.checked ? [...value.modules, module.id] : value.modules.filter(id => id !== module.id))}/>{module.name}{moduleHelp(language, module) && info('modules', moduleHelp(language, module))}</label>)}</div>
      </fieldset>
    </details>
    <p className="mt-4 break-all text-xs text-slate-500">Synthea: {catalogue.revision}</p>
    {children}
    </fieldset>
  </section>
}
