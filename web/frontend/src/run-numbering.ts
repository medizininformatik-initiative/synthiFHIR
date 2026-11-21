import type { Message } from './i18n'

type Run = { id: string; created: number }

export function numberRuns<T extends Run>(runs: T[]) {
  const newestFirst = [...runs].sort((a, b) => b.created - a.created || b.id.localeCompare(a.id))
  const numbers = new Map(newestFirst.map((run, index) => [run.id, newestFirst.length - index]))
  return { newestFirst, numbers }
}

export function continuationReference(sourceRun: string, numbers: Map<string, number>): Message {
  const number = numbers.get(sourceRun)
  return number === undefined
    ? { key: 'app.series.fromMissing', params: { id: sourceRun.slice(0, 8) } }
    : { key: 'app.series.from', params: { run: number, id: sourceRun.slice(0, 8) } }
}

// Keep related runs together, including through filtering and pagination.
export function groupRuns<T extends Run & { continuation?: { sourceRun: string } | null; addition?: { sourceRun: string } | null }>(runs: T[]) {
  const byId = new Map(runs.map(run => [run.id, run]))
  const rootOf = (run: T) => {
    const seen = new Set([run.id])
    let current = run
    for (;;) {
      const parentId = current.continuation?.sourceRun ?? current.addition?.sourceRun
      const parent = parentId ? byId.get(parentId) : undefined
      if (!parent || seen.has(parent.id)) return current.id
      seen.add(parent.id); current = parent
    }
  }
  const groups = new Map<string, T[]>()
  for (const run of runs) {
    const root = rootOf(run)
    const group = groups.get(root)
    if (group) group.push(run)
    else groups.set(root, [run])
  }
  return [...groups.entries()].map(([root, items]) => ({ root, items: items.sort((a, b) => a.id === root ? -1 : b.id === root ? 1 : a.created - b.created || a.id.localeCompare(b.id)) }))
    .sort((a, b) => Math.max(...b.items.map(run => run.created)) - Math.max(...a.items.map(run => run.created)) || a.root.localeCompare(b.root))
}
