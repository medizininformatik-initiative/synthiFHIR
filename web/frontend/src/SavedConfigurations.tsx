import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Button } from './components/ui/button'
import { importConfiguration, type Configuration } from './configuration'
import { exportPropertiesConfiguration } from './configuration-properties'
import { translate, type Language, type TextKey } from './i18n'

type SavedConfiguration = { id: string; name: string; revision: number; configurationProperties?: string }
type Action = 'create' | 'load' | 'replace' | 'rename' | 'duplicate' | 'delete'
const actionKeys: Record<Action, TextKey> = {
  create: 'app.saved.create', load: 'app.saved.load', replace: 'app.saved.replace',
  rename: 'app.saved.rename', duplicate: 'app.saved.duplicate', delete: 'app.saved.delete'
}
class RequestFailure extends Error {
  constructor(public status: number) { super() }
}
async function request(path = '', method = 'GET', body?: unknown) {
  const response = await fetch('/api/configurations' + path, {
    method, ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  })
  if (!response.ok) throw new RequestFailure(response.status)
  return response.status === 204 ? null : response.json()
}

export function SavedConfigurations({ language, configuration, valid, onLoad, editorActions }: {
  editorActions: ReactNode; language: Language; configuration: Configuration; valid: boolean; onLoad: (value: Configuration) => void
}) {
  const t = (key: TextKey, params?: Record<string, string>) => translate(language, key, params)
  const [items, setItems] = useState<SavedConfiguration[]>([])
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<TextKey | null>(null)
  const [listFailed, setListFailed] = useState(false)
  const refreshSequence = useRef(0)
  const [error, setError] = useState<TextKey | null>(null)
  const [edit, setEdit] = useState<{ action: Action; item?: SavedConfiguration } | null>(null)
  const [name, setName] = useState('')
  const item = items.find(value => value.id === selected)
  const refresh = useCallback(async () => {
    const sequence = ++refreshSequence.current
    try {
      const values = await request()
      if (sequence === refreshSequence.current) {
        setItems(values)
        setListFailed(false)
      }
    } catch {
      if (sequence === refreshSequence.current) setListFailed(true)
    }
  }, [])
  function report(error: unknown) {
    setError(error instanceof RequestFailure && error.status === 409 ? 'app.saved.conflict'
      : error instanceof RequestFailure && error.status === 404 ? 'app.saved.missing'
      : error instanceof RequestFailure && error.status === 422 ? 'app.saved.invalid' : 'app.saved.failed')
  }
  useEffect(() => {
    const refreshVisible = () => { if (!document.hidden) void refresh() }
    refreshVisible()
    const timer = window.setInterval(refreshVisible, 5000)
    window.addEventListener('focus', refreshVisible)
    document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refreshVisible)
      document.removeEventListener('visibilitychange', refreshVisible)
      refreshSequence.current++
    }
  }, [refresh])
  function begin(action: Action) {
    setEdit({ action, item }); setName(action === 'rename' ? item?.name ?? '' : '')
    setError(null); setMessage(null)
  }
  async function submit() {
    if (!edit) return
    setBusy(true); setError(null)
    try {
      const { action, item } = edit
      const path = item ? '/' + item.id : ''
      let result: SavedConfiguration | null = null
      if (action === 'create') result = await request('', 'POST', { name: name.trim(), configurationProperties: exportPropertiesConfiguration(configuration, language) })
      else if (action === 'load') {
        const loaded: SavedConfiguration = await request(path)
        onLoad(importConfiguration(loaded.configurationProperties!))
      } else if (action === 'replace') result = await request(path, 'PATCH', { revision: item!.revision, configurationProperties: exportPropertiesConfiguration(configuration, language) })
      else if (action === 'rename') result = await request(path, 'PATCH', { revision: item!.revision, name: name.trim() })
      else if (action === 'duplicate') result = await request(path + '/duplicate', 'POST', { revision: item!.revision, name: name.trim() })
      else await request(path, 'DELETE', { revision: item!.revision })
      setEdit(null)
      if (result) setSelected(result.id)
      else if (action === 'delete') setSelected('')
      setMessage(action === 'load' ? 'app.saved.loaded' : 'app.saved.done')
      await refresh()
    } catch (error) {
      report(error)
      // Refresh revisions for the next action without replacing the open confirmation.
      await refresh()
    } finally { setBusy(false) }
  }
  const requiresName = edit && ['create', 'rename', 'duplicate'].includes(edit.action)
  const requiresValid = edit && ['create', 'replace'].includes(edit.action)
  return <>
    <div className="mt-5 flex flex-wrap gap-2">
      <Button tooltip={t('tooltip.saved.create')} onClick={() => begin('create')} disabled={!valid || busy}>{t('app.saved.create')}</Button>
      {editorActions}
    </div>
    <section aria-label={t('app.saved.title')} className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
    <h3 className="font-semibold text-slate-800">{t('app.saved.title')}</h3>
    <p className="mt-1 text-sm text-slate-600">{t('app.saved.hint')}</p>
    <fieldset disabled={busy} className="mt-3 min-w-0">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-sm sm:w-72">{t('app.saved.selection')}
          <select className="rounded-lg border border-slate-300 bg-white p-2.5" value={item ? selected : ''} onChange={e => { setSelected(e.target.value); setEdit(null); setMessage(null) }}>
            <option value="">{t('app.saved.choose')}</option>
            {items.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}
          </select>
        </label>
      </div>
      {item && <div className="mt-3 flex flex-wrap gap-2">
        {(['load', 'replace', 'rename', 'duplicate', 'delete'] as Action[]).map(action =>
          <Button key={action} tooltip={t(`tooltip.saved.${action}`)} variant="outline" disabled={action === 'replace' && !valid} onClick={() => begin(action)}>{t(actionKeys[action])}</Button>)}
      </div>}
      {edit && <form className="mt-4 rounded-lg border border-slate-300 bg-white p-4" onSubmit={e => { e.preventDefault(); void submit() }}>
        <p className="text-sm font-medium">{t(actionKeys[edit.action])}{edit.item && edit.action !== 'create' ? `: ${edit.item.name}` : ''}</p>
        {edit.action === 'load' && <p className="mt-2 text-sm text-slate-600">{t('app.saved.loadHint')}</p>}
        {edit.action === 'replace' && <p className="mt-2 text-sm text-slate-600">{t('app.saved.replaceHint')}</p>}
        {edit.action === 'delete' && <p className="mt-2 text-sm text-slate-600">{t('app.saved.deleteHint')}</p>}
        {requiresName && <label className="mt-3 flex max-w-md flex-col gap-1 text-sm">{t('app.saved.name')}
          <input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} className="rounded-lg border border-slate-300 p-2"/>
        </label>}
        <div className="mt-3 flex gap-2"><Button type="submit" disabled={Boolean(requiresName && !name.trim()) || Boolean(requiresValid && !valid)}>{t('app.saved.confirm')}</Button>
          <Button type="button" variant="outline" onClick={() => setEdit(null)}>{t('app.saved.cancel')}</Button></div>
      </form>}
    </fieldset>
    {listFailed && <p role="alert" className="mt-3 text-sm text-red-700">{t('app.saved.failed')}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{t(error)}</p>}
    {message && <p role="status" className="mt-3 text-sm text-teal-800">{t(message)}</p>}
  </section>
  </>
}
