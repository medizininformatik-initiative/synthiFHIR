import { type Language } from './i18n'

export type Outcomes = {
  generation: string
  validation: { status: string; reports: number; findings: number; incomplete: number }
  transfer: { status: string; checked: number; differences: number; unavailable: number; importNotes: number; configuredOmissions: number
    notes: { resourceType: string; path: string; reason: string; count: number }[]
    details: { source: string; variant: string; status: string; message: string }[] }
}

export function RunOutcomes({ outcomes, language }: { outcomes: Outcomes; language: Language }) {
  const { transfer, validation } = outcomes
  const de = language === 'de'
  const notices: string[] = []
  if (validation.findings) notices.push(de ? `${validation.findings} Prüfberichte enthalten Auffälligkeiten.` : `${validation.findings} validation reports contain findings.`)
  if (validation.incomplete || validation.status === 'unavailable') notices.push(de ? 'Die Datenprüfung ist unvollständig.' : 'Data validation is incomplete.')
  if (transfer.differences) notices.push(de ? 'Bei der Datenübernahme wurden Abweichungen festgestellt.' : 'Differences were found during data transfer.')
  if (transfer.importNotes || transfer.configuredOmissions) notices.push(de ? 'Zur Datenübernahme liegen Hinweise oder vorgesehene Auslassungen vor.' : 'Data transfer has notes or configured omissions.')
  if (transfer.unavailable || transfer.status === 'unavailable') notices.push(de ? 'Die Vollständigkeit der Datenübernahme konnte nicht vollständig geprüft werden.' : 'Transfer completeness could not be fully checked.')
  if (!notices.length) return null
  return <div role="status" className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
    <p>{notices.join(' ')}</p>
    <p className="mt-1">{de ? 'Details stehen unter „Berichte und weitere Dateien“ bei den Downloads.' : 'See “Reports and other files” in the downloads for details.'}</p>
  </div>
}
