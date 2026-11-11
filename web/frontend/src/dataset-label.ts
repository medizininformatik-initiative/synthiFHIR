import { translate, type Language } from './i18n'

export type DatasetContext = {
  kind?: string; inspection?: { patients?: number }; datasetName?: string; source?: string; sourceName?: string; inputKind?: string
  configuration?: { id: string }
  generation?: { outputMode?: 'kds' | 'synthea'; population: number; yearsOfHistory: number; minAge: number; maxAge: number } | null
  summary?: { patients?: number | null; cases?: number | null } | null
}
export function datasetLabel(dataset: DatasetContext & { name?: string }, language: Language): string {
  const t = (key: Parameters<typeof translate>[1], params?: Record<string, string | number>) => translate(language, key, params)
  const synthea = dataset.source === 'synthea-generation' || dataset.inputKind?.startsWith('synthea')
  const source = dataset.inputKind === 'resource-addition' ? t('app.additions.label') : synthea ? (dataset.generation?.outputMode === 'synthea' ? 'Synthea-FHIR' : 'Synthea') : dataset.inputKind === 'csv' ? 'CSV' : 'Excel'
  const name = dataset.datasetName || dataset.sourceName || (dataset.source === 'starter' ? 'FHIR_Testdatengenerator_Vorlage.xlsx'
    : dataset.source === 'demo' ? 'FHIR_Testdatengenerator_Interpolar_Demo.xlsx' : undefined)
  const facts: string[] = []
  const patients = dataset.kind === 'bulk' ? dataset.inspection?.patients : dataset.summary?.patients
  const requested = dataset.kind === 'bulk' ? undefined : dataset.generation?.population
  if (patients != null) facts.push(requested != null && requested !== patients
    ? t('app.dataset.patientsRequested', { actual: patients, requested }) : `Pat ${patients}`)
  else if (requested != null) facts.push(t('app.dataset.requested', { requested }))
  if (dataset.summary?.cases != null) facts.push(t('app.dataset.cases', { count: dataset.summary.cases }))
  if (dataset.generation) facts.push(`Hist ${dataset.generation.yearsOfHistory}`, `Age ${dataset.generation.minAge}–${dataset.generation.maxAge}`)
  // Old upload records may outlive their run and contain only its original label.
  if (!dataset.source && !dataset.inputKind && !name) return dataset.name || t('app.dataset.unnamed')
  return [name, source, dataset.kind === 'bulk' ? dataset.name : undefined].filter(Boolean).join(' · ') + (facts.length ? ` (${facts.join(' · ')})` : '')
}
