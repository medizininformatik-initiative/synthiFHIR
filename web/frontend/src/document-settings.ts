import type { DocumentCoding } from './configuration.ts'
export const nlpExtension = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/StructureDefinition/mii-ex-dokument-nlp-processing-status'
export const nlpSystem = 'https://www.medizininformatik-initiative.de/fhir/ext/modul-dokument/CodeSystem/mii-cs-dokument-nlp-processing-status'
export const nlpCodes = ['unprocessed', 'preprocessed', 'format-change', 'content-change', 'annotated', 'preanno', 'deid', 'semantic', 'surrogated']
export const typePresets: Record<string, DocumentCoding[]> = {
  kdl: [{ system: 'http://dvmd.de/fhir/CodeSystem/kdl', code: 'AD010110', display: 'Ärztlicher Verlaufsbericht' }],
  xds: [{ system: 'http://ihe-d.de/CodeSystems/IHEXDStypeCode', code: 'BERI', display: 'Arztberichte' }],
  empty: []
}
typePresets.combined = [...typePresets.kdl, ...typePresets.xds]
export const nlpPresets: Record<string, string[]> = {
  original: ['unprocessed'], format: ['preprocessed', 'format-change'],
  surrogated: ['preprocessed', 'format-change', 'surrogated']
}
export function statusHints(codes: string[]) {
  return codes.flatMap(code => {
    if (!code.trim()) return []
    if (!nlpCodes.includes(code)) return [{ key: 'document.unknown', params: { code } }]
    const parent = ['format-change', 'content-change'].includes(code) ? 'preprocessed'
      : ['preanno', 'deid', 'semantic'].includes(code) ? 'annotated' : undefined
    return parent && !codes.includes(parent) ? [{ key: 'document.parent', params: { code, parent } }] : []
  })
}
