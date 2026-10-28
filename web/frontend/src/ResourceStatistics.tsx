import { Download } from 'lucide-react'
import { Button } from './components/ui/button'
import type { Language } from './i18n'

export type Statistics = {
  bundles: number
  resources: Record<string, number>
  distinctIds?: Record<string, number>
  missingIds?: Record<string, number>
  encounters: [string, string, string, number][]
  contacts: [string, string, string, string, number][]
}

export function ResourceStatistics({ datasetId, statistics, language }: {
  datasetId: string; statistics?: Statistics; language: Language
}) {
  const de = language === 'de'
  const number = (value: number) => value.toLocaleString(de ? 'de-DE' : 'en-US')
  if (!statistics) return <p className="mt-2 text-xs text-slate-500">{de
    ? 'Für diesen Datensatz ist keine vollständige Ressourcenstatistik gespeichert.'
    : 'No complete resource statistics are saved for this dataset.'}</p>
  const hierarchy = (value: string) => de ? ({ 'Main encounter': 'Hauptkontakt', 'Sub-encounter': 'Unterkontakt' }[value] ?? value) : value
  const level = (value: string) => de ? value.replaceAll('Facility contact', 'Einrichtungskontakt')
    .replaceAll('Department contact', 'Abteilungskontakt').replaceAll('Care unit contact', 'Versorgungsstellenkontakt')
    .replaceAll('not specified', 'Nicht angegeben') : value
  return <section className="mt-3 space-y-2">
    <Button variant="outline" asChild><a href={`/api/datasets/${datasetId}/statistics`}><Download size={14}/>{de ? 'Statistik herunterladen' : 'Download statistics'}</a></Button>
    <details className="text-sm">
      <summary className="cursor-pointer font-medium">{de ? 'Ressourcenstatistik' : 'Resource statistics'}</summary>
      <p className="mt-2 text-xs text-slate-500">{de
        ? 'Die Zahlen gelten für diesen Datensatz oder dieses Paket. Dieselbe Ressource kann mit derselben ID mehrfach vorkommen. Eingebettete Ressourcen werden nicht einzeln gezählt; verschiedene Ausgabeformate zählen nicht mehrfach.'
        : 'Counts apply to this dataset or package. The same resource may occur repeatedly with the same ID. Contained resources are not counted separately; different output formats are not counted multiple times.'}</p>
      <div className="overflow-x-auto"><table className="mt-3 w-full">
        <caption className="sr-only">{de ? 'Ressourcen nach Typ' : 'Resources by type'}</caption>
        <thead><tr><th scope="col" className="py-2 text-left">{de ? 'Ressourcentyp' : 'Resource type'}</th>
          <th scope="col" className="px-2 text-right">{de ? 'Vorkommen' : 'Occurrences'}</th>
          <th scope="col" className="px-2 text-right">{de ? 'Unterschiedliche IDs' : 'Distinct IDs'}</th>
          <th scope="col" className="text-right">{de ? 'Ohne ID' : 'Without ID'}</th></tr></thead>
        <tbody>{Object.entries(statistics.resources).sort(([a], [b]) => a.localeCompare(b)).map(([type, count]) => <tr key={type} className="border-t">
          <th scope="row" className="py-1 text-left font-normal">{type}</th><td className="px-2 text-right">{number(count)}</td>
          <td className="px-2 text-right">{statistics.distinctIds ? number(statistics.distinctIds[type] ?? 0) : '—'}</td>
          <td className="text-right">{statistics.missingIds ? number(statistics.missingIds[type] ?? 0) : '—'}</td>
        </tr>)}</tbody>
      </table></div>
      {!statistics.distinctIds && <p className="mt-2">{de ? 'Die Anzahl unterschiedlicher IDs wurde für dieses Paket nicht gespeichert.' : 'Distinct ID counts were not saved for this package.'}</p>}
      <p className="mt-2">{de ? 'Bundles als Transporthülle' : 'Transport bundles'}: {number(statistics.bundles)}</p>
      {!!statistics.contacts.length && <div className="mt-4 overflow-x-auto"><table className="w-full">
        <caption className="mb-2 text-left font-medium">{de ? 'Kontakte nach Ebene, Klasse und Hierarchie' : 'Encounters by level, class and hierarchy'}</caption>
        <thead><tr>{[de ? 'Kontaktebene' : 'Contact level', de ? 'Klasse' : 'Class', de ? 'Hierarchie' : 'Hierarchy', de ? 'Anzahl' : 'Count'].map(text => <th key={text} scope="col" className="py-1 pr-3 text-left">{text}</th>)}</tr></thead>
        <tbody>{statistics.contacts.map(([contact, system, code, tree, count], i) => <tr key={i} className="border-t">
          <td className="py-1 pr-3">{level(contact)}</td><td className="pr-3" title={system}>{level(code)}</td><td className="pr-3">{hierarchy(tree)}</td><td>{number(count)}</td>
        </tr>)}</tbody>
      </table></div>}
    </details>
  </section>
}
