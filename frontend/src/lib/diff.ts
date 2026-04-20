export type DiffMode = 'side-by-side' | 'inline'

export type DiffLineKind = 'equal' | 'delete' | 'insert'
export type DiffGroupKind = 'equal' | 'change'

export interface DiffLine {
  kind: DiffLineKind
  text: string
  oldLineNumber?: number | null
  newLineNumber?: number | null
}

export interface DiffRow {
  kind: DiffLineKind
  oldLineNumber: number | null
  newLineNumber: number | null
  oldText: string | null
  newText: string | null
}

export interface DiffGroup {
  id: string
  kind: DiffGroupKind
  lines: DiffLine[]
  rows: DiffRow[]
  oldCount: number
  newCount: number
  startOldLine: number | null
  startNewLine: number | null
  endOldLine: number | null
  endNewLine: number | null
  collapsible: boolean
  collapsedByDefault: boolean
  summary: string
}

export interface DiffStats {
  oldLines: number
  newLines: number
  equalLines: number
  deletedLines: number
  insertedLines: number
  equalGroups: number
  changeGroups: number
}

export interface DiffDocument {
  groups: DiffGroup[]
  stats: DiffStats
}

export interface BuildGitDiffOptions {
  collapseEqualGroupAfter?: number
}

interface DiffOp {
  kind: DiffLineKind
  text: string
  oldLineNumber?: number | null
  newLineNumber?: number | null
}

function splitLines(text: string): string[] {
  if (!text) return []
  return text.replace(/\r\n?/g, '\n').split('\n')
}

function createLine(kind: DiffLineKind, text: string, oldLineNumber?: number | null, newLineNumber?: number | null): DiffLine {
  return {
    kind,
    text,
    oldLineNumber: oldLineNumber ?? null,
    newLineNumber: newLineNumber ?? null,
  }
}

function collectDiffOps(oldLines: string[], newLines: string[]): DiffOp[] {
  const n = oldLines.length
  const m = newLines.length
  const max = n + m
  const offset = max

  const trace: Int32Array[] = []
  let v = new Int32Array((max * 2) + 1)
  v.fill(-1)
  v[offset + 1] = 0

  const reachedEnd = (x: number, y: number) => x >= n && y >= m

  for (let d = 0; d <= max; d += 1) {
    for (let k = -d; k <= d; k += 2) {
      const kIndex = offset + k
      let x: number

      if (k === -d || (k !== d && v[kIndex - 1] < v[kIndex + 1])) {
        x = v[kIndex + 1]
      } else {
        x = v[kIndex - 1] + 1
      }

      let y = x - k
      while (x < n && y < m && oldLines[x] === newLines[y]) {
        x += 1
        y += 1
      }

      v[kIndex] = x
      if (reachedEnd(x, y)) {
        trace.push(v.slice())
        return backtrackDiff(trace, oldLines, newLines, offset)
      }
    }

    trace.push(v.slice())
  }

  return backtrackDiff(trace, oldLines, newLines, offset)
}

function backtrackDiff(trace: Int32Array[], oldLines: string[], newLines: string[], offset: number): DiffOp[] {
  const ops: DiffOp[] = []
  let x = oldLines.length
  let y = newLines.length

  for (let d = trace.length - 1; d > 0; d -= 1) {
    const v = trace[d - 1]
    const k = x - y
    let prevK: number

    if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) {
      prevK = k + 1
    } else {
      prevK = k - 1
    }

    const prevX = v[offset + prevK]
    const prevY = prevX - prevK

    while (x > prevX && y > prevY) {
      ops.push(createLine('equal', oldLines[x - 1], x, y))
      x -= 1
      y -= 1
    }

    if (x === prevX) {
      ops.push(createLine('insert', newLines[y - 1], null, y))
      y -= 1
    } else {
      ops.push(createLine('delete', oldLines[x - 1], x, null))
      x -= 1
    }
  }

  while (x > 0 && y > 0) {
    ops.push(createLine('equal', oldLines[x - 1], x, y))
    x -= 1
    y -= 1
  }

  while (x > 0) {
    ops.push(createLine('delete', oldLines[x - 1], x, null))
    x -= 1
  }

  while (y > 0) {
    ops.push(createLine('insert', newLines[y - 1], null, y))
    y -= 1
  }

  return ops.reverse()
}

function pairRows(lines: DiffLine[]): DiffRow[] {
  const rows: DiffRow[] = []
  let index = 0

  while (index < lines.length) {
    const current = lines[index]
    if (current.kind === 'equal') {
      rows.push({
        kind: current.kind,
        oldLineNumber: current.oldLineNumber ?? null,
        newLineNumber: current.newLineNumber ?? null,
        oldText: current.text,
        newText: current.text,
      })
      index += 1
      continue
    }

    if (current.kind === 'delete') {
      const next = lines[index + 1]
      if (next && next.kind === 'insert') {
        rows.push({
          kind: 'delete',
          oldLineNumber: current.oldLineNumber ?? null,
          newLineNumber: next.newLineNumber ?? null,
          oldText: current.text,
          newText: next.text,
        })
        index += 2
        continue
      }

      rows.push({
        kind: 'delete',
        oldLineNumber: current.oldLineNumber ?? null,
        newLineNumber: null,
        oldText: current.text,
        newText: null,
      })
      index += 1
      continue
    }

    rows.push({
      kind: 'insert',
      oldLineNumber: null,
      newLineNumber: current.newLineNumber ?? null,
      oldText: null,
      newText: current.text,
    })
    index += 1
  }

  return rows
}

function summarizeGroup(lines: DiffLine[], kind: DiffGroupKind): string {
  if (kind === 'equal') {
    return `${lines.length} unchanged lines omitted`
  }

  const deleted = lines.filter((line) => line.kind === 'delete').length
  const inserted = lines.filter((line) => line.kind === 'insert').length
  return `${deleted} deleted, ${inserted} inserted`
}

function countGroupLines(lines: DiffLine[], kind: DiffLineKind): number {
  return lines.filter((line) => line.kind === kind).length
}

export function buildGitDiff(
  oldText: string,
  newText: string,
  options: BuildGitDiffOptions = {}
): DiffDocument {
  const oldLines = splitLines(oldText)
  const newLines = splitLines(newText)
  const collapseThreshold = Math.max(2, options.collapseEqualGroupAfter ?? 8)
  const ops = collectDiffOps(oldLines, newLines)

  const groups: DiffGroup[] = []
  let current: DiffLine[] = []
  let currentKind: DiffGroupKind | null = null

  const flush = () => {
    if (!currentKind || current.length === 0) return
    const oldCount = countGroupLines(current, 'delete') + countGroupLines(current, 'equal')
    const newCount = countGroupLines(current, 'insert') + countGroupLines(current, 'equal')
    const startOldLine = current.find((line) => line.oldLineNumber != null)?.oldLineNumber ?? null
    const startNewLine = current.find((line) => line.newLineNumber != null)?.newLineNumber ?? null
    const endOldLine = [...current].reverse().find((line) => line.oldLineNumber != null)?.oldLineNumber ?? null
    const endNewLine = [...current].reverse().find((line) => line.newLineNumber != null)?.newLineNumber ?? null
    const collapsible = currentKind === 'equal' && current.length >= collapseThreshold

    groups.push({
      id: `group-${groups.length}`,
      kind: currentKind,
      lines: current,
      rows: pairRows(current),
      oldCount,
      newCount,
      startOldLine,
      startNewLine,
      endOldLine,
      endNewLine,
      collapsible,
      collapsedByDefault: collapsible,
      summary: summarizeGroup(current, currentKind),
    })

    current = []
    currentKind = null
  }

  for (const op of ops) {
    const groupKind: DiffGroupKind = op.kind === 'equal' ? 'equal' : 'change'
    if (currentKind !== groupKind) flush()
    currentKind = groupKind
    current.push({
      kind: op.kind,
      text: op.text,
      oldLineNumber: op.oldLineNumber ?? null,
      newLineNumber: op.newLineNumber ?? null,
    })
  }

  flush()

  const stats = groups.reduce<DiffStats>(
    (acc, group) => {
      acc.oldLines += group.oldCount
      acc.newLines += group.newCount
      acc.equalLines += group.kind === 'equal' ? group.lines.length : 0
      acc.deletedLines += group.lines.filter((line) => line.kind === 'delete').length
      acc.insertedLines += group.lines.filter((line) => line.kind === 'insert').length
      if (group.kind === 'equal') {
        acc.equalGroups += 1
      } else {
        acc.changeGroups += 1
      }
      return acc
    },
    {
      oldLines: 0,
      newLines: 0,
      equalLines: 0,
      deletedLines: 0,
      insertedLines: 0,
      equalGroups: 0,
      changeGroups: 0,
    }
  )

  return { groups, stats }
}

export function formatDiffLineNumber(lineNumber: number | null): string {
  return lineNumber == null ? '—' : String(lineNumber)
}
