import { useState } from 'react'
import { Plus, Minus } from 'lucide-react'
import { Button } from './components/ui/button'
import { options, resourceEnabled, type Configuration, type Value, type DocumentCoding } from './configuration'
import { typePresets, nlpPresets, nlpCodes, nlpExtension, nlpSystem, statusHints } from './document-settings'

const inputClass = 'w-full min-w-0 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100'
export function DocumentSettings({ config, update, t }: {
  config: Configuration; update: (id: string, value: Value) => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const [typePreset, setTypePreset] = useState('combined')
  const [nlpPreset, setNlpPreset] = useState('original')
  const enabled = resourceEnabled('DocumentReference', config.values)
  return <div className="space-y-5 border-t border-slate-100 pt-4">
    {(['type', 'nlp'] as const).map(group => {
      const prefix = `document.${group}`
      const mode = String(config.values[`${prefix}.mode`])
      const inactive = !enabled || ['input', 'omit'].includes(mode)
      const rows = config.values[`${prefix}.values`] as DocumentCoding[] | string[]
      const preset = group === 'type' ? typePreset : nlpPreset
      const modeOption = options.find(o => o.id === `${prefix}.mode`)!
      const changeRow = (index: number, field: string, value: string) => update(`${prefix}.values`,
        group === 'nlp' ? (rows as string[]).map((row, i) => i === index ? value : row)
          : (rows as DocumentCoding[]).map((row, i) => i === index ? { ...row, [field]: value } : row))
      return <section key={group} aria-label={t(`option.${prefix}.values.label`)} className="rounded-lg border border-slate-200 p-4">
        <h4 className="mb-2 font-semibold">{t(`option.${prefix}.values.label`)}</h4>
        <label className="block text-sm" htmlFor={`${prefix}.mode`}>{t(modeOption.labelKey)}</label>
        <select id={`${prefix}.mode`} className={`${inputClass} mt-1`} disabled={!enabled} value={mode}
          onChange={event => update(`${prefix}.mode`, event.target.value)}>
          {modeOption.choices!.map(choice => <option key={String(choice)} value={choice}>{t(modeOption.choiceLabelKeys![String(choice)])}</option>)}
        </select>
        <p className="my-2 text-sm text-slate-600">{t(modeOption.helpKey)}</p>
        <fieldset disabled={inactive} className="space-y-3 disabled:opacity-60">
          <label htmlFor={`${prefix}.preset`} className="block text-sm">{t('document.preset')}</label>
          <div className="flex flex-wrap gap-2">
            <select id={`${prefix}.preset`} className={`${inputClass} flex-1`} value={preset}
              onChange={event => group === 'type' ? setTypePreset(event.target.value) : setNlpPreset(event.target.value)}>
              {(group === 'type' ? ['combined', 'kdl', 'xds', 'empty'] : Object.keys(nlpPresets)).map(key =>
                <option key={key} value={key}>{t(`document.preset.${key}`)}</option>)}
            </select>
            <Button type="button" variant="outline" onClick={() => update(`${prefix}.values`, structuredClone(group === 'type' ? typePresets[preset] : nlpPresets[preset]))}>{t('document.apply')}</Button>
          </div>
          <p className="text-sm text-slate-600">{t('document.free')}</p>
          {rows.map((row, index) => <div key={index} className="flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-3">
            {(group === 'type' ? ['system', 'code', 'display'] : ['code']).map(field => <label key={field} className={field === 'system' ? 'min-w-48 flex-[2] text-xs' : 'min-w-32 flex-1 text-xs'}>
              {t(`document.${field}`)} {index + 1}
              <input className={`${inputClass} mt-1`} value={typeof row === 'string' ? row : row[field as keyof DocumentCoding] ?? ''}
                list={`${prefix}.${field}.${index}`} onChange={event => changeRow(index, field, event.target.value)} />
              <datalist id={`${prefix}.${field}.${index}`}>
                {(group === 'nlp' ? nlpCodes : typePresets.combined.filter(c => field === 'system' || c.system === (row as DocumentCoding).system).map(c => c[field as keyof DocumentCoding]!)).map(value => <option key={value} value={value} />)}
              </datalist>
            </label>)}
            <Button type="button" variant="outline" className="w-[var(--control-height)] shrink-0 px-0" title={t('document.remove')} aria-label={`${t('document.remove')} ${index + 1}`} onClick={() => update(`${prefix}.values`, rows.filter((_, i) => i !== index) as Value)}><Minus className="h-4 w-4" aria-hidden="true" /></Button>
          </div>)}
          <Button type="button" variant="outline" className="w-[var(--control-height)] px-0" title={t('document.add')} aria-label={t('document.add')} onClick={() => update(`${prefix}.values`, group === 'type' ? [...rows as DocumentCoding[], { system: '', code: '', display: '' }] : [...rows as string[], ''])}><Plus className="h-4 w-4" aria-hidden="true" /></Button>
        </fieldset>
        {group === 'nlp' && <>
          {!inactive && statusHints(rows as string[]).map((hint, i) => <p key={i} role="status" className="mt-2 text-sm text-amber-800">{t(hint.key, hint.params)}</p>)}
          <details className="mt-3 text-xs text-slate-600"><summary>{t('document.fixed')}</summary><p className="mt-2 break-all">Extension: {nlpExtension}</p><p className="mt-1 break-all">System: {nlpSystem}</p></details>
        </>}
      </section>
    })}
  </div>
}
