import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { Button } from './components/ui/button'

export function RunName({ name, de, onSave }: { name: string; de: boolean; onSave: (name: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  if (!editing) return <div className="flex min-w-0 items-start gap-2">
    <h3 className="min-w-0 break-words text-xl font-semibold">{name}</h3>
    <button type="button" className="rounded p-1.5 text-slate-500 hover:bg-slate-100 hover:text-teal-800" aria-label={de ? 'Lauf umbenennen' : 'Rename run'} title={de ? 'Lauf umbenennen' : 'Rename run'} onClick={() => { setDraft(name); setError(''); setEditing(true) }}><Pencil size={16}/></button>
  </div>
  return <form className="min-w-0 flex-1" onSubmit={async event => {
    event.preventDefault()
    if (!draft.trim() || saving) return
    setSaving(true); setError('')
    try { await onSave(draft.trim()); setEditing(false) }
    catch (error) { setError(error instanceof Error ? error.message : String(error)) }
    finally { setSaving(false) }
  }}>
    <label className="block text-sm">{de ? 'Laufname' : 'Run name'}<input autoFocus className="mt-1 w-full rounded-lg border border-slate-300 p-2" value={draft} maxLength={200} disabled={saving} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Escape' && !saving) setEditing(false) }}/></label>
    <p className="mt-1 text-xs text-slate-600">{de ? 'Name für die Laufübersicht. Daten und Ergebnisordner bleiben unverändert.' : 'Name shown in the run overview. Data and result folders stay unchanged.'}</p>
    <div className="mt-2 flex gap-2"><Button type="submit" disabled={saving || !draft.trim()}>{de ? 'Speichern' : 'Save'}</Button><Button type="button" variant="outline" disabled={saving} onClick={() => setEditing(false)}>{de ? 'Abbrechen' : 'Cancel'}</Button></div>
    {error && <p role="alert" className="mt-2 text-sm text-red-700">{error}</p>}
  </form>
}
