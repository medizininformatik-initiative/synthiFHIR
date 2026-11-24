import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import type { Language } from './i18n'
import { Help } from './Help'

export function OutputDirectory({ path, language }: { path: string; language: Language }) {
  const de = language === 'de'
  const [status, setStatus] = useState<'copied' | 'manual' | null>(null)
  const text = useRef<HTMLElement>(null)
  useEffect(() => {
    if (status !== 'copied') return
    const timer = window.setTimeout(() => setStatus(null), 2000)
    return () => window.clearTimeout(timer)
  }, [status])
  const absolute = /^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(path)
  async function copy() {
    try {
      await navigator.clipboard.writeText(path)
      setStatus('copied')
    } catch {
      const selection = window.getSelection()
      if (text.current && selection) {
        const range = document.createRange()
        range.selectNodeContents(text.current)
        selection.removeAllRanges()
        selection.addRange(range)
      }
      setStatus('manual')
    }
  }
  const hint = (!absolute ? (de ? 'Pfad relativ zum Projektordner: Öffne zuerst den Projektordner im Dateimanager. ' : 'Path relative to the project directory: open the project directory in your file manager first. ') : '')
    + (de ? 'macOS: im Finder ⌘⇧G. Windows/Linux: im Dateimanager Strg+L. Pfad einfügen und mit Enter öffnen.' : 'macOS: press ⌘⇧G in Finder. Windows/Linux: press Ctrl+L in your file manager. Paste the path and press Enter.')
  return <div className="space-y-1 text-sm">
    <p>{de ? 'Lokales Ergebnisverzeichnis' : 'Local output directory'}:</p>
    <div className="flex min-w-0 items-center gap-1">
      <code ref={text} title={path} className="min-w-0 truncate select-all">{path}</code>
      <button type="button" onClick={() => void copy()}
        aria-label={status === 'copied' ? (de ? 'Pfad kopiert' : 'Path copied') : (de ? 'Ordnerpfad kopieren' : 'Copy folder path')}
        title={status === 'copied' ? (de ? 'Pfad kopiert' : 'Path copied') : (de ? 'Ordnerpfad kopieren' : 'Copy folder path')}
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-teal-100 hover:text-teal-800 focus-visible:outline-2 focus-visible:outline-teal-700">
        {status === 'copied' ? <Check className="h-4 w-4 text-teal-700" aria-hidden="true"/> : <Copy className="h-4 w-4" aria-hidden="true"/>}
      </button>
      <span className="inline-flex shrink-0"><Help text={hint} t={() => de ? 'Anleitung: Ordner im Dateimanager öffnen' : 'How to open the folder in a file manager'}/></span>
    </div>
    {status === 'manual' && <p role="status" className="text-xs text-slate-600">{de ? 'Bitte den markierten Pfad mit Strg+C bzw. ⌘C kopieren.' : 'Copy the selected path with Ctrl+C or ⌘C.'}</p>}
  </div>
}
