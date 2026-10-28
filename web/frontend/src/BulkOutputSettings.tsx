import { translate, type Language } from './i18n'

export function BulkOutputSettings({ language, gzip, onChange, disabled = false }: {
  language: Language; gzip: boolean; onChange: (value: boolean) => void; disabled?: boolean
}) {
  return <div className="space-y-3 rounded-lg bg-teal-50 p-4 text-sm">
    <p className="font-medium">{translate(language, 'app.bulk.fixedFormat')}</p>
    <label className="flex items-center gap-2"><input type="checkbox" checked={gzip} disabled={disabled} onChange={event => onChange(event.target.checked)}/>{translate(language, 'app.bulk.gzip')}</label>
    <p>{translate(language, gzip ? 'app.bulk.format' : 'app.bulk.formatPlain')}</p>
    <p className="text-slate-600">{translate(language, 'app.bulk.checksDisabled')}</p>
  </div>
}
