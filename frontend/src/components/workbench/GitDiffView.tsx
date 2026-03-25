import type { ReactNode } from 'react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import type { DiffDocument, DiffGroup, DiffMode, DiffRow } from '@/lib/diff'
import { buildGitDiff, formatDiffLineNumber } from '@/lib/diff'

type DiffComparisonStyle = 'git' | 'flat'

interface GitDiffViewProps {
  oldText: string
  newText: string
  title?: string
  leftLabel?: string
  rightLabel?: string
  comparisonStyle?: DiffComparisonStyle
  mode?: DiffMode
  defaultMode?: DiffMode
  onModeChange?: (mode: DiffMode) => void
  collapseEqualGroupAfter?: number
  showModeToggle?: boolean
  className?: string
  emptyStateLabel?: string
}

const MODE_LABELS: Record<DiffMode, string> = {
  'side-by-side': '并排',
  inline: '行内',
}

interface CharacterSegment {
  text: string
  changed: boolean
}

type CharacterOpKind = 'equal' | 'delete' | 'insert'

interface CharacterOp {
  kind: CharacterOpKind
  token: string
}

interface DiffCharacterStats {
  oldChars: number
  newChars: number
  addedChars: number
  deletedChars: number
}

interface FlatDiffDocument {
  left: CharacterSegment[]
  right: CharacterSegment[]
  addedChars: number
  deletedChars: number
}

interface HighlightUnit {
  text: string
  key: string
}

const MAX_INTRA_ROW_DIFF_COMPLEXITY = 120_000
const MAX_INTRA_ROW_DIFF_TOKENS = 2_000
const FLAT_SENTENCE_CLOSER_RE = /[”"’』」》）\])]/u

function cellClassName(row: DiffRow, side: 'left' | 'right'): string {
  if (row.kind === 'equal') return 'bg-white text-primary'

  const hasOld = Boolean((row.oldText ?? '').trim())
  const hasNew = Boolean((row.newText ?? '').trim())

  if (side === 'left') {
    if (hasOld) return 'bg-error/5 text-error border-error/20'
    return 'bg-white text-secondary'
  }

  if (hasNew) return 'bg-success/5 text-success border-success/20'
  return 'bg-white text-secondary'
}

function markerForRow(row: DiffRow): string {
  if (row.kind === 'equal') return ' '
  if (row.kind === 'delete' && row.newText) return '~'
  return row.kind === 'delete' ? '-' : '+'
}

function appendCharacterSegment(target: CharacterSegment[], text: string, changed: boolean): void {
  if (!text) return
  const last = target[target.length - 1]
  if (last && last.changed === changed) {
    last.text += text
    return
  }
  target.push({ text, changed })
}

function collectCharacterDiffOps(oldTokens: string[], newTokens: string[]): CharacterOp[] {
  const n = oldTokens.length
  const m = newTokens.length
  const max = n + m
  const offset = max

  if (max === 0) return []

  const trace: Int32Array[] = []
  const v = new Int32Array((max * 2) + 1)
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
      while (x < n && y < m && oldTokens[x] === newTokens[y]) {
        x += 1
        y += 1
      }

      v[kIndex] = x
      if (reachedEnd(x, y)) {
        trace.push(v.slice())
        return backtrackCharacterDiff(trace, oldTokens, newTokens, offset)
      }
    }

    trace.push(v.slice())
  }

  return backtrackCharacterDiff(trace, oldTokens, newTokens, offset)
}

function backtrackCharacterDiff(
  trace: Int32Array[],
  oldTokens: string[],
  newTokens: string[],
  offset: number
): CharacterOp[] {
  const ops: CharacterOp[] = []
  let x = oldTokens.length
  let y = newTokens.length

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
      ops.push({ kind: 'equal', token: oldTokens[x - 1] })
      x -= 1
      y -= 1
    }

    if (x === prevX) {
      ops.push({ kind: 'insert', token: newTokens[y - 1] })
      y -= 1
    } else {
      ops.push({ kind: 'delete', token: oldTokens[x - 1] })
      x -= 1
    }
  }

  while (x > 0 && y > 0) {
    ops.push({ kind: 'equal', token: oldTokens[x - 1] })
    x -= 1
    y -= 1
  }

  while (x > 0) {
    ops.push({ kind: 'delete', token: oldTokens[x - 1] })
    x -= 1
  }

  while (y > 0) {
    ops.push({ kind: 'insert', token: newTokens[y - 1] })
    y -= 1
  }

  return ops.reverse()
}

function buildIntraRowCharacterSegments(oldText: string, newText: string): {
  left: CharacterSegment[]
  right: CharacterSegment[]
} {
  const oldTokens = Array.from(oldText)
  const newTokens = Array.from(newText)
  const complexity = oldTokens.length * newTokens.length
  const totalTokens = oldTokens.length + newTokens.length

  if (complexity > MAX_INTRA_ROW_DIFF_COMPLEXITY || totalTokens > MAX_INTRA_ROW_DIFF_TOKENS) {
    return {
      left: oldText ? [{ text: oldText, changed: true }] : [],
      right: newText ? [{ text: newText, changed: true }] : [],
    }
  }

  const ops = collectCharacterDiffOps(oldTokens, newTokens)
  const left: CharacterSegment[] = []
  const right: CharacterSegment[] = []

  for (const op of ops) {
    if (op.kind === 'equal') {
      appendCharacterSegment(left, op.token, false)
      appendCharacterSegment(right, op.token, false)
      continue
    }
    if (op.kind === 'delete') {
      appendCharacterSegment(left, op.token, true)
      continue
    }
    appendCharacterSegment(right, op.token, true)
  }

  return {
    left: left.length > 0 ? left : (oldText ? [{ text: oldText, changed: true }] : []),
    right: right.length > 0 ? right : (newText ? [{ text: newText, changed: true }] : []),
  }
}

function renderCharacterSegments(segments: CharacterSegment[], side: 'left' | 'right'): ReactNode {
  if (segments.length === 0) return ' '
  return segments.map((segment, index) => (
    <span
      // eslint-disable-next-line react/no-array-index-key
      key={`${side}-${index}`}
      className={segment.changed ? (side === 'left' ? 'rounded bg-error/20' : 'rounded bg-success/20') : undefined}
    >
      {segment.text}
    </span>
  ))
}

function countVisibleCharacters(text: string): number {
  if (!text) return 0
  return Array.from(text.replace(/\r\n?/g, '\n').replace(/\n/g, '')).length
}

function countChangedCharacters(segments: CharacterSegment[]): number {
  return segments.reduce((total, segment) => {
    if (!segment.changed) return total
    return total + Array.from(segment.text).length
  }, 0)
}

function countChangedVisibleCharacters(segments: CharacterSegment[]): number {
  return segments.reduce((total, segment) => {
    if (!segment.changed) return total
    return total + countVisibleCharacters(segment.text)
  }, 0)
}

function isSentenceEndingChar(char: string, next: string): boolean {
  if (char === '。' || char === '！' || char === '？' || char === '!' || char === '?' || char === '；' || char === ';' || char === '…') {
    return true
  }
  if (char !== '.') return false
  if (next === '.') return false
  return next === '' || /\s/.test(next) || FLAT_SENTENCE_CLOSER_RE.test(next)
}

function normalizeHighlightUnit(text: string): string {
  if (!text) return ''
  return text
    .replace(/[\s\u3000]+/g, '')
    .trim()
}

function splitTextToHighlightUnits(text: string): HighlightUnit[] {
  if (!text) return []

  const units: HighlightUnit[] = []
  let buffer = ''

  const pushBuffer = () => {
    if (!buffer) return
    units.push({
      text: buffer,
      key: normalizeHighlightUnit(buffer),
    })
    buffer = ''
  }

  for (let index = 0; index < text.length; index += 1) {
    const current = text[index]
    const next = text[index + 1] ?? ''
    buffer += current

    if (!isSentenceEndingChar(current, next)) continue

    while (index + 1 < text.length && FLAT_SENTENCE_CLOSER_RE.test(text[index + 1])) {
      buffer += text[index + 1]
      index += 1
    }
    while (index + 1 < text.length && /[\s\u3000]/.test(text[index + 1])) {
      buffer += text[index + 1]
      index += 1
    }
    pushBuffer()
  }

  pushBuffer()
  return units
}

function buildUnitCounter(units: HighlightUnit[]): Map<string, number> {
  const counter = new Map<string, number>()
  units.forEach((unit) => {
    if (!unit.key) return
    counter.set(unit.key, (counter.get(unit.key) ?? 0) + 1)
  })
  return counter
}

function buildCommonUnitCounter(oldUnits: HighlightUnit[], newUnits: HighlightUnit[]): Map<string, number> {
  const oldCounter = buildUnitCounter(oldUnits)
  const newCounter = buildUnitCounter(newUnits)
  const common = new Map<string, number>()

  oldCounter.forEach((oldCount, key) => {
    const newCount = newCounter.get(key) ?? 0
    if (newCount <= 0) return
    common.set(key, Math.min(oldCount, newCount))
  })
  return common
}

function markUnitHighlights(units: HighlightUnit[], commonCounter: Map<string, number>): CharacterSegment[] {
  const usedCounter = new Map<string, number>()
  const segments: CharacterSegment[] = []

  units.forEach((unit) => {
    if (!unit.key) {
      appendCharacterSegment(segments, unit.text, false)
      return
    }

    const limit = commonCounter.get(unit.key) ?? 0
    const used = usedCounter.get(unit.key) ?? 0
    const changed = used >= limit
    if (!changed) {
      usedCounter.set(unit.key, used + 1)
    }
    appendCharacterSegment(segments, unit.text, changed)
  })

  return segments
}

function buildFlatDiffDocument(oldText: string, newText: string): FlatDiffDocument {
  const oldUnits = splitTextToHighlightUnits(oldText)
  const newUnits = splitTextToHighlightUnits(newText)
  const commonCounter = buildCommonUnitCounter(oldUnits, newUnits)
  const left = markUnitHighlights(oldUnits, commonCounter)
  const right = markUnitHighlights(newUnits, commonCounter)

  return {
    left: left.length > 0 ? left : (oldText ? [{ text: oldText, changed: true }] : []),
    right: right.length > 0 ? right : (newText ? [{ text: newText, changed: true }] : []),
    addedChars: countChangedVisibleCharacters(right),
    deletedChars: countChangedVisibleCharacters(left),
  }
}

function changeRows(group: DiffGroup): DiffRow[] {
  if (group.kind === 'equal') return group.rows

  const deletes = group.lines.filter((line) => line.kind === 'delete')
  const inserts = group.lines.filter((line) => line.kind === 'insert')
  const maxLength = Math.max(deletes.length, inserts.length)
  const rows: DiffRow[] = []

  for (let index = 0; index < maxLength; index += 1) {
    const left = deletes[index] ?? null
    const right = inserts[index] ?? null
    rows.push({
      kind: left && right ? 'delete' : left ? 'delete' : 'insert',
      oldLineNumber: left?.oldLineNumber ?? null,
      newLineNumber: right?.newLineNumber ?? null,
      oldText: left?.text ?? null,
      newText: right?.text ?? null,
    })
  }

  return rows
}

function computeCharacterStats(document: DiffDocument, oldText: string, newText: string): DiffCharacterStats {
  let addedChars = 0
  let deletedChars = 0

  for (const group of document.groups) {
    const rows = group.kind === 'equal' ? group.rows : changeRows(group)
    for (const row of rows) {
      if (row.kind === 'equal') continue

      const leftText = row.oldText ?? ''
      const rightText = row.newText ?? ''
      const hasOld = row.oldText != null
      const hasNew = row.newText != null

      if (!hasOld && hasNew) {
        addedChars += countVisibleCharacters(rightText)
        continue
      }
      if (hasOld && !hasNew) {
        deletedChars += countVisibleCharacters(leftText)
        continue
      }
      if (!hasOld || !hasNew) continue

      const segments = buildIntraRowCharacterSegments(leftText, rightText)
      deletedChars += countChangedCharacters(segments.left)
      addedChars += countChangedCharacters(segments.right)
    }
  }

  return {
    oldChars: countVisibleCharacters(oldText),
    newChars: countVisibleCharacters(newText),
    addedChars,
    deletedChars,
  }
}

function ModeToggle({
  value,
  onChange,
}: {
  value: DiffMode
  onChange: (mode: DiffMode) => void
}) {
  return (
    <div className="inline-flex rounded-full border border-border bg-subtle p-1">
      {(Object.keys(MODE_LABELS) as DiffMode[]).map((mode) => {
        const active = value === mode
        return (
          <button
            key={mode}
            type="button"
            aria-label={`切换到${MODE_LABELS[mode]}模式`}
            aria-pressed={active}
            onClick={() => onChange(mode)}
            className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors ${
              active
                ? 'bg-white text-primary shadow-xs'
                : 'text-secondary hover:text-primary'
            }`}
          >
            {MODE_LABELS[mode]}
          </button>
        )
      })}
    </div>
  )
}

function DiffRowBadge({
  label,
  value,
}: {
  label: string
  value: string
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-caption text-secondary shadow-xs">
      <span className="font-medium text-primary">{label}</span>
      <span>{value}</span>
    </span>
  )
}

function renderCollapsedGroup({
  group,
  onToggle,
}: {
  group: DiffGroup
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      aria-label={`展开或收起 ${group.summary}`}
      aria-expanded={false}
      onClick={onToggle}
      className="flex w-full items-center justify-between gap-3 rounded-2xl border border-dashed border-border bg-subtle px-4 py-3 text-left transition-colors hover:border-accent/40 hover:bg-accent/5"
    >
      <div className="space-y-1">
        <p className="text-callout font-medium text-primary">{group.summary}</p>
        <p className="text-caption text-secondary">
          原文 {group.startOldLine ?? '—'} - {group.endOldLine ?? '—'} · 改写 {group.startNewLine ?? '—'} - {group.endNewLine ?? '—'}
        </p>
      </div>
      <span className="rounded-full bg-white px-3 py-1 text-caption text-secondary shadow-xs">
        点击展开
      </span>
    </button>
  )
}

function renderSideBySideRow(row: DiffRow, index: number) {
  const leftText = row.oldText ?? ''
  const rightText = row.newText ?? ''
  const hasPairChange = row.kind !== 'equal' && row.oldText != null && row.newText != null
  const characterSegments = hasPairChange ? buildIntraRowCharacterSegments(leftText, rightText) : null
  const rowKey = `row-${index}-${row.oldLineNumber ?? 'x'}-${row.newLineNumber ?? 'y'}`

  return (
    <div key={rowKey} className="grid grid-cols-2 border-b border-border last:border-b-0">
      <div className={`min-h-12 border-r border-border px-3 py-2 ${cellClassName(row, 'left')}`}>
        <div className="mb-1 flex items-center justify-between gap-2 text-[11px] font-mono text-secondary">
          <span>{formatDiffLineNumber(row.oldLineNumber)}</span>
          <span>{(row.oldText ?? '').trim() ? '-' : ' '}</span>
        </div>
        <p className="whitespace-pre-wrap break-words text-[13px] leading-6">
          {row.oldText == null
            ? '—'
            : characterSegments
              ? renderCharacterSegments(characterSegments.left, 'left')
              : leftText || ' '}
        </p>
      </div>
      <div className={`min-h-12 px-3 py-2 ${cellClassName(row, 'right')}`}>
        <div className="mb-1 flex items-center justify-between gap-2 text-[11px] font-mono text-secondary">
          <span>{formatDiffLineNumber(row.newLineNumber)}</span>
          <span>{(row.newText ?? '').trim() ? '+' : ' '}</span>
        </div>
        <p className="whitespace-pre-wrap break-words text-[13px] leading-6">
          {row.newText == null
            ? '—'
            : characterSegments
              ? renderCharacterSegments(characterSegments.right, 'right')
              : rightText || ' '}
        </p>
      </div>
    </div>
  )
}

function renderInlineRow(row: DiffRow, index: number) {
  const text = row.oldText ?? row.newText ?? ''
  const key = `inline-${index}-${row.oldLineNumber ?? 'x'}-${row.newLineNumber ?? 'y'}`
  const borderColor =
    row.kind === 'equal'
      ? 'border-border'
      : row.kind === 'delete'
        ? 'border-error/20'
        : 'border-success/20'
  const tone =
    row.kind === 'equal'
      ? 'bg-white text-primary'
      : row.kind === 'delete'
        ? 'bg-error/5 text-error'
        : 'bg-success/5 text-success'

  return (
    <div key={key} className={`rounded-xl border px-3 py-2 ${borderColor} ${tone}`}>
      <div className="mb-1 flex items-center gap-2 text-[11px] font-mono text-secondary">
        <span className="rounded-full bg-white px-2 py-0.5 shadow-xs">
          {markerForRow(row)}
        </span>
        <span>
          {formatDiffLineNumber(row.oldLineNumber)} / {formatDiffLineNumber(row.newLineNumber)}
        </span>
      </div>
      <p className="whitespace-pre-wrap break-words text-[13px] leading-6">{text || ' '}</p>
    </div>
  )
}

function renderFlatPane({
  label,
  side,
  segments,
}: {
  label: string
  side: 'left' | 'right'
  segments: CharacterSegment[]
}) {
  return (
    <article className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-white shadow-xs">
      <header className="border-b border-border px-3 py-2">
        <p className="text-caption font-medium text-primary">{label}</p>
      </header>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        <p className="whitespace-pre-wrap break-words text-[13px] leading-7 text-primary">
          {segments.length > 0 ? renderCharacterSegments(segments, side) : ' '}
        </p>
      </div>
    </article>
  )
}

function renderGroupContent(
  group: DiffGroup,
  mode: DiffMode,
  isCollapsed: boolean,
  onToggle: () => void
) {
  if (group.collapsible && isCollapsed) {
    return renderCollapsedGroup({ group, onToggle })
  }

  if (mode === 'side-by-side') {
    const rows = group.kind === 'equal' ? group.rows : changeRows(group)
    return (
      <div className="overflow-hidden rounded-2xl border border-border bg-white shadow-xs">
        {rows.map((row, index) => renderSideBySideRow(row, index))}
      </div>
    )
  }

  const rows = group.kind === 'equal' ? group.rows : changeRows(group)
  return (
    <div className="space-y-2">
      {group.kind === 'change' && (
        <div className="flex flex-wrap gap-2 text-caption">
          <span className="rounded-full bg-error/10 px-2 py-1 text-error">
            删除 {group.lines.filter((line) => line.kind === 'delete').length} 行
          </span>
          <span className="rounded-full bg-success/10 px-2 py-1 text-success">
            新增 {group.lines.filter((line) => line.kind === 'insert').length} 行
          </span>
        </div>
      )}
      <div className="space-y-2">
        {rows.map((row, index) => renderInlineRow(row, index))}
      </div>
      {group.collapsible && (
        <button
          type="button"
          aria-label={`折叠 ${group.summary}`}
          onClick={onToggle}
          className="inline-flex items-center gap-1 rounded-full border border-border bg-white px-3 py-1 text-caption text-secondary transition-colors hover:border-accent/40 hover:text-primary"
        >
          收起未变内容
        </button>
      )}
    </div>
  )
}

export function GitDiffView({
  oldText,
  newText,
  title = 'Git 风格 Diff',
  leftLabel = '原文',
  rightLabel = '改写稿',
  comparisonStyle = 'git',
  mode: controlledMode,
  defaultMode = 'side-by-side',
  onModeChange,
  collapseEqualGroupAfter = 8,
  showModeToggle = true,
  className,
  emptyStateLabel = '没有可比较的内容。',
}: GitDiffViewProps) {
  const document = useMemo<DiffDocument>(
    () => buildGitDiff(oldText, newText, { collapseEqualGroupAfter }),
    [oldText, newText, collapseEqualGroupAfter]
  )
  const flatDocument = useMemo(
    () => (comparisonStyle === 'flat' ? buildFlatDiffDocument(oldText, newText) : null),
    [comparisonStyle, oldText, newText]
  )
  const [uncontrolledMode, setUncontrolledMode] = useState<DiffMode>(defaultMode)
  const mode = controlledMode ?? uncontrolledMode

  useEffect(() => {
    if (controlledMode) return
    setUncontrolledMode(defaultMode)
  }, [controlledMode, defaultMode])

  const initialCollapsedIds = useMemo(
    () => new Set(document.groups.filter((group) => group.collapsedByDefault).map((group) => group.id)),
    [document]
  )
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(initialCollapsedIds)

  useEffect(() => {
    setCollapsedIds(new Set(initialCollapsedIds))
  }, [initialCollapsedIds])

  const stats = document.stats
  const characterStats = useMemo(
    () => (comparisonStyle === 'flat' && flatDocument
      ? {
          oldChars: countVisibleCharacters(oldText),
          newChars: countVisibleCharacters(newText),
          addedChars: flatDocument.addedChars,
          deletedChars: flatDocument.deletedChars,
        }
      : computeCharacterStats(document, oldText, newText)),
    [comparisonStyle, flatDocument, document, oldText, newText]
  )
  const hasContent = comparisonStyle === 'flat' ? Boolean(oldText || newText) : document.groups.length > 0

  const handleModeChange = (nextMode: DiffMode) => {
    if (controlledMode == null) {
      setUncontrolledMode(nextMode)
    }
    onModeChange?.(nextMode)
  }

  const toggleGroup = (groupId: string) => {
    setCollapsedIds((current) => {
      const next = new Set(current)
      if (next.has(groupId)) {
        next.delete(groupId)
      } else {
        next.add(groupId)
      }
      return next
    })
  }

  return (
    <section className={`flex min-h-0 flex-col rounded-2xl border border-border bg-subtle/40 ${className ?? ''}`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-white px-4 py-3">
        <div className="space-y-1">
          <h3 className="text-title-3 font-semibold text-primary">{title}</h3>
          <div className="flex flex-wrap gap-2">
            <DiffRowBadge label={`${leftLabel}字数`} value={`${characterStats.oldChars.toLocaleString()} 字`} />
            <DiffRowBadge label={`${rightLabel}字数`} value={`${characterStats.newChars.toLocaleString()} 字`} />
            <DiffRowBadge label="新增字数" value={`+${characterStats.addedChars.toLocaleString()} 字`} />
            <DiffRowBadge label="删除字数" value={`-${characterStats.deletedChars.toLocaleString()} 字`} />
            {comparisonStyle === 'git' && (
              <>
                <DiffRowBadge label={`${leftLabel}行数`} value={`${stats.oldLines} 行`} />
                <DiffRowBadge label={`${rightLabel}行数`} value={`${stats.newLines} 行`} />
              </>
            )}
          </div>
        </div>

        {showModeToggle && comparisonStyle === 'git' && (
          <div className="flex items-center gap-2">
            <ModeToggle value={mode} onChange={handleModeChange} />
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-4">
        {!hasContent ? (
          <div className="flex min-h-[220px] items-center justify-center rounded-2xl border border-dashed border-border bg-white px-6 py-10 text-center text-callout text-secondary">
            {emptyStateLabel}
          </div>
        ) : comparisonStyle === 'flat' && flatDocument ? (
          <div className="grid min-h-0 gap-4 md:grid-cols-2">
            {renderFlatPane({
              label: leftLabel,
              side: 'left',
              segments: flatDocument.left,
            })}
            {renderFlatPane({
              label: rightLabel,
              side: 'right',
              segments: flatDocument.right,
            })}
          </div>
        ) : (
          <div className="space-y-4">
            {document.groups.map((group) => {
              const isCollapsed = collapsedIds.has(group.id)
              return (
                <Fragment key={group.id}>
                  {group.collapsible && isCollapsed ? (
                    renderGroupContent(group, mode, true, () => toggleGroup(group.id))
                  ) : (
                    <div className="space-y-2">
                      {group.kind === 'equal' && (
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex flex-wrap gap-2">
                            <span className="rounded-full bg-white px-2.5 py-1 text-caption text-secondary shadow-xs">
                              未变 {group.lines.length} 行
                            </span>
                            <span className="rounded-full bg-white px-2.5 py-1 text-caption text-secondary shadow-xs">
                              原文 {group.startOldLine ?? '—'} - {group.endOldLine ?? '—'}
                            </span>
                          </div>
                          {group.collapsible && (
                            <button
                              type="button"
                              aria-label={`折叠 ${group.summary}`}
                              onClick={() => toggleGroup(group.id)}
                              className="rounded-full border border-border bg-white px-3 py-1 text-caption text-secondary transition-colors hover:border-accent/40 hover:text-primary"
                            >
                              折叠未变内容
                            </button>
                          )}
                        </div>
                      )}
                      {renderGroupContent(group, mode, false, () => toggleGroup(group.id))}
                    </div>
                  )}
                </Fragment>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

export default GitDiffView
