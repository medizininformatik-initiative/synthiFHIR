import type { Statistics } from './ResourceStatistics'

type Result = { kind?: string; statistics?: Statistics; inspection?: { patients: number; resourceInstances?: number } }

/** Count output occurrences, never add distinct-ID counts across packages or KDS variants. */
export function resultCounts(results: Result[]) {
  if (!results.length || (results.length > 1 && !results.every(item => item.kind === 'bulk'))) return {}
  const sumKnown = (values: (number | undefined)[]) => values.every(value => value !== undefined)
    ? values.reduce<number>((total, value) => total + value!, 0) : undefined
  return {
    patients: sumKnown(results.map(item => item.inspection?.patients)),
    cases: sumKnown(results.map(item => item.statistics?.encounters?.filter(([, , hierarchy]) => hierarchy === 'Main encounter').reduce((total, [, , , count]) => total + count, 0))),
    resources: sumKnown(results.map(item => item.statistics
      ? Object.values(item.statistics.resources).reduce((total, count) => total + count, 0)
      : item.inspection?.resourceInstances)),
  }
}
