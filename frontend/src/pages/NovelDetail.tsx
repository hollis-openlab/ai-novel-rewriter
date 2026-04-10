import type { ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Download,
  Check,
  CircleAlert,
  AlertCircle,
  Loader2,
  ChevronDown,
  Play,
  Pause,
  RotateCcw,
  RefreshCw,
  FileJson,
  FileText,
  Eye,
  Terminal,
  AlertTriangle,
  BookOpen,
  ArrowUpDown,
  ChevronUp,
  Pencil,
  Save,
  Trash2,
  Search,
  Split,
  ShieldAlert,
} from 'lucide-react'
import { ApiError, chapters as chaptersApi, getNovel, getNovelChapters, novels as novelsApi, providers as providersApi, stages as stagesApi } from '@/lib/api'
import {
  fetchQualityReport,
  fetchStageArtifact,
  fetchStageRunDetail,
  normalizeQualityReport,
  summarizeRewriteCoverage,
  type RewriteCoverageItem,
  type StageRunDetail,
} from '@/lib/stage-insights'
import { splitRulesApi, type SplitRuleSpec, type SplitRulesPreviewResponse } from '@/lib/split-rules'
import { detectPromptLayout, promptLogsApi, prettyPrintPromptPayload } from '@/lib/prompt-logs'
import { wsManager } from '@/lib/ws'
import { GitDiffView } from '@/components/workbench/GitDiffView'
import type { ChapterListItem, Provider, RewriteSegment, StageArtifactExportFormat, StageName, StageStatus, WSMessage } from '@/types'
import type { PromptLogEntry } from '@/types/prompt-log'

// ── Stage constants ───────────────────────────────────────────────────────────

const STAGE_NAMES: StageName[] = ['import', 'split', 'analyze', 'mark', 'rewrite', 'assemble']
const VISIBLE_STAGE_NAMES: StageName[] = ['import', 'split', 'analyze', 'rewrite', 'assemble']

const STAGE_LABELS: Record<StageName, string> = {
  import: '导入',
  split: '分章',
  analyze: '分析与标记',
  mark: '标记（自动）',
  rewrite: '改写',
  assemble: '组装',
}

const STAGE_EXPORT_FORMATS: Partial<Record<StageName, Array<{ label: string; format: StageArtifactExportFormat }>>> = {
  split: [
    { label: '导出 JSON', format: 'json' },
    { label: '导出 ZIP', format: 'zip' },
  ],
  analyze: [
    { label: '导出 JSON', format: 'json' },
    { label: '导出 Markdown', format: 'markdown' },
  ],
  mark: [
    { label: '导出 JSON', format: 'json' },
    { label: '导出 Markdown', format: 'markdown' },
  ],
  rewrite: [
    { label: '导出 JSON', format: 'json' },
    { label: '导出 Diff', format: 'diff' },
    { label: '导出 ZIP', format: 'zip' },
  ],
}

const SAVE_SPINNER_STYLE = { animationDuration: '2.2s' } as const
const SPLIT_MATCH_SAMPLE_SIZE = 10

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万字`
  return `${n.toLocaleString()}字`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric' })
}

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}分${s % 60}秒`
  const h = Math.floor(m / 60)
  return `${h}小时${m % 60}分`
}

function shortHash(value?: string | null): string {
  if (!value) return '—'
  if (value.length <= 16) return value
  return `${value.slice(0, 8)}…${value.slice(-6)}`
}

function formatSnapshotValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return '—'
}

function getApiErrorCode(error: unknown): string | undefined {
  return error instanceof ApiError ? error.code : undefined
}

// ── Pipeline Node ─────────────────────────────────────────────────────────────

interface StageNodeProps {
  stage: StageName
  status: StageStatus
  warningsCount?: number
  isSelected: boolean
  isLast: boolean
  onClick: () => void
}

function StageNode({ stage, status, warningsCount = 0, isSelected, isLast, onClick }: StageNodeProps) {
  const isCompleted = status === 'completed'
  const isRunning = status === 'running'
  const isFailed = status === 'failed'
  const isPaused = status === 'paused' || status === 'stale'
  const isPending = status === 'pending' || isPaused

  const circleClasses = (): string => {
    const base = 'w-10 h-10 rounded-full flex items-center justify-center transition-all duration-200 cursor-pointer'
    if (isCompleted) return `${base} bg-success`
    if (isRunning) return `${base} bg-accent animate-pulse`
    if (isFailed) return `${base} bg-error`
    if (isPaused) return `${base} bg-warning border-2 border-warning/40`
    return `${base} bg-subtle border-2 border-border`
  }

  const lineColor = (): string => {
    if (isCompleted) return 'bg-success'
    if (isRunning) return 'bg-accent'
    return 'bg-border'
  }

  return (
    <div className="flex items-center flex-1">
      <div className="flex flex-col items-center">
        {/* Circle node */}
        <button
          className={`${circleClasses()} ${isSelected ? 'ring-2 ring-offset-2 ring-accent' : ''} focus:outline-none`}
          onClick={onClick}
          title={STAGE_LABELS[stage]}
        >
          {isCompleted && <Check className="w-4 h-4 text-white" strokeWidth={2.5} />}
          {isRunning && <Loader2 className="w-4 h-4 text-white animate-spin" strokeWidth={1.5} />}
          {isFailed && <AlertCircle className="w-4 h-4 text-white" strokeWidth={1.5} />}
          {isPaused && <Pause className="w-4 h-4 text-white" strokeWidth={1.5} />}
          {isPending && <span className="w-2 h-2 rounded-full bg-secondary" />}
        </button>
        {/* Label */}
        <span className={`text-caption mt-2 whitespace-nowrap font-medium
          ${isCompleted ? 'text-success' : isRunning ? 'text-accent' : isFailed ? 'text-error' : isPaused ? 'text-warning' : 'text-secondary'}`}>
          {STAGE_LABELS[stage]}
        </span>
        {warningsCount > 0 && (
          <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-[10px] font-semibold text-warning">
            <CircleAlert className="h-3 w-3" />
            {warningsCount} 条告警
          </span>
        )}
      </div>

      {/* Connector line */}
      {!isLast && (
        <div className={`flex-1 h-0.5 mx-2 mb-5 rounded-full ${lineColor()}`} />
      )}
    </div>
  )
}

// ── Stage Detail Card ─────────────────────────────────────────────────────────

interface StageRunInfo {
  status: StageStatus
  run_seq?: number
  started_at?: string
  completed_at?: string
  error_message?: string
  warnings_count?: number
  artifact_path?: string
  config_snapshot?: StageRunDetail['config_snapshot']
  chapters_total: number
  chapters_done: number
}

interface StageDetailCardProps {
  novelId: string
  stage: StageName
  info: StageRunInfo
  allStages: Record<string, StageRunInfo>
  onActionDone: () => void
}

function StageDetailCard({ novelId, stage, info, allStages, onActionDone }: StageDetailCardProps) {
  const [loading, setLoading] = useState(false)
  const [showError, setShowError] = useState(false)
  const [showLogs, setShowLogs] = useState(false)
  const runDetailQuery = useQuery({
    queryKey: ['stage-run-detail', novelId, stage, info.run_seq],
    queryFn: () => fetchStageRunDetail(novelId, stage, info.run_seq ?? 0),
    enabled: Boolean(info.run_seq),
    staleTime: 15000,
  })
  const stageLogQuery = useQuery({
    queryKey: ['stage-log', novelId, stage, info.run_seq],
    queryFn: () => fetchStageArtifact(novelId, stage, info.run_seq),
    enabled: showLogs && info.status !== 'pending',
    staleTime: 5000,
    refetchInterval: showLogs && info.status === 'running' ? 5000 : false,
  })
  const qualityReportQuery = useQuery({
    queryKey: ['quality-report', novelId],
    queryFn: () => fetchQualityReport(novelId),
    enabled: stage === 'assemble',
    staleTime: 30_000,
  })

  const stageIndex = STAGE_NAMES.indexOf(stage)
  const prevStage = stageIndex > 0 ? STAGE_NAMES[stageIndex - 1] : null
  const prevCompleted = !prevStage || allStages[prevStage]?.status === 'completed'

  const runDetail = runDetailQuery.data?.run ?? null
  const qualityReport = useMemo(() => normalizeQualityReport(qualityReportQuery.data), [qualityReportQuery.data])
  const warningsCount = info.warnings_count ?? runDetail?.warnings_count ?? 0
  const pct = info.chapters_total > 0 ? Math.round((info.chapters_done / info.chapters_total) * 100) : 0

  const elapsed = info.started_at
    ? Date.now() - new Date(info.started_at).getTime()
    : null

  const eta = (info.status === 'running' && elapsed && info.chapters_done > 0 && info.chapters_total > 0)
    ? Math.round(elapsed / info.chapters_done * (info.chapters_total - info.chapters_done))
    : null

  const act = async (fn: () => Promise<void>) => {
    setLoading(true)
    try {
      await fn()
      onActionDone()
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const handleExportArtifact = (format: StageArtifactExportFormat) => {
    stagesApi.exportArtifact(novelId, stage, { format }).then(async (file) => {
      const blob = file.blob
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.filename || `${stage}-artifact.${format === 'markdown' ? 'md' : format}`
      a.click()
      URL.revokeObjectURL(url)
    })
  }

  const exportOptions = STAGE_EXPORT_FORMATS[stage] ?? []
  const snapshot = runDetail?.config_snapshot ?? info.config_snapshot
  const stageBadge =
    stage === 'assemble' && qualityReport?.blocked
      ? 'QUALITY_GATE_BLOCKED'
      : stage === 'rewrite' && warningsCount > 0
        ? 'ANCHOR_MISMATCH'
        : null

  return (
    <div className="bg-white rounded-2xl p-6 shadow-xs border border-subtle/60">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <h3 className="text-title-3 font-semibold text-primary">
            {STAGE_LABELS[stage]} 阶段
          </h3>
          {stageBadge && (
            <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2.5 py-1 text-caption font-semibold text-warning">
              <ShieldAlert className="h-3.5 w-3.5" />
              {stageBadge}
            </span>
          )}
          {warningsCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2.5 py-1 text-caption font-medium text-warning">
              <CircleAlert className="h-3.5 w-3.5" />
              {warningsCount} 条告警
            </span>
          )}
        </div>
        <span className={`text-caption font-medium px-2.5 py-1 rounded-full
          ${info.status === 'completed' ? 'bg-success/10 text-success' :
            info.status === 'running' ? 'bg-accent/10 text-accent' :
            info.status === 'failed' ? 'bg-error/10 text-error' :
            (info.status === 'stale' || info.status === 'paused') ? 'bg-warning/10 text-warning' :
            'bg-subtle text-secondary'}`}>
          {info.status === 'completed' ? '已完成' :
           info.status === 'running' ? '运行中' :
           info.status === 'failed' ? '失败' :
           info.status === 'stale' ? '已过期' : info.status === 'paused' ? '已暂停' : '待处理'}
        </span>
      </div>

      {runDetail && (
        <div className="mb-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-xl bg-subtle px-3 py-2">
            <p className="text-caption text-secondary">Run Seq</p>
            <p className="text-callout font-medium text-primary">#{runDetail.run_seq}</p>
          </div>
          <div className="rounded-xl bg-subtle px-3 py-2">
            <p className="text-caption text-secondary">告警来源</p>
            <p className="text-callout font-medium text-primary">{warningsCount > 0 ? 'run detail / artifact' : '无告警'}</p>
          </div>
          <div className="rounded-xl bg-subtle px-3 py-2">
            <p className="text-caption text-secondary">Artifact</p>
            <p className="truncate font-mono text-caption text-primary" title={runDetail.artifact_path ?? info.artifact_path ?? 'unknown'}>
              {runDetail.artifact_path ?? info.artifact_path ?? 'unknown'}
            </p>
          </div>
        </div>
      )}

      {qualityReport?.blocked && stage === 'assemble' && (
        <div className="flex items-start gap-2 p-3 mb-4 bg-error/5 rounded-xl border border-error/20">
          <ShieldAlert className="w-4 h-4 text-error flex-shrink-0 mt-0.5" strokeWidth={1.5} />
          <div className="space-y-1">
            <p className="text-callout font-medium text-error">质量闸门阻断导出</p>
            <p className="text-caption text-secondary">请先查看下方质量报告，再通过右上角导出菜单启用强制导出。</p>
          </div>
        </div>
      )}

      {/* Progress */}
      {info.chapters_total > 0 && (
        <div className="mb-4 space-y-1.5">
          <div className="flex justify-between">
            <span className="text-callout text-secondary">进度：{info.chapters_done} / {info.chapters_total} 章</span>
            <span className="text-callout font-medium text-primary">{pct}%</span>
          </div>
          <div className="h-2 bg-subtle rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300
                ${info.status === 'completed' ? 'bg-success' :
                  info.status === 'failed' ? 'bg-error' :
                  'bg-accent'}`}
              style={{ width: `${info.status === 'completed' ? 100 : pct}%` }}
            />
          </div>
        </div>
      )}

      {snapshot && (
        <div className="mb-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-xl border border-border bg-page px-3 py-2">
            <p className="text-caption text-secondary">Provider</p>
            <p className="text-callout font-medium text-primary truncate">{snapshot.provider_name ?? '未记录'}</p>
          </div>
          <div className="rounded-xl border border-border bg-page px-3 py-2">
            <p className="text-caption text-secondary">模型</p>
            <p className="text-callout font-medium text-primary truncate">{snapshot.model_name ?? '未记录'}</p>
          </div>
          <div className="rounded-xl border border-border bg-page px-3 py-2">
            <p className="text-caption text-secondary">温度 / MaxTokens</p>
            <p className="text-callout font-medium text-primary">
              {formatSnapshotValue(snapshot.generation_params?.temperature)} / {formatSnapshotValue(snapshot.generation_params?.max_tokens)}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-page px-3 py-2">
            <p className="text-caption text-secondary">规则快照</p>
            <p className="text-callout font-medium text-primary">已记录</p>
          </div>
        </div>
      )}

      {/* Timing */}
      {(elapsed || info.completed_at) && (
        <div className="flex gap-4 mb-4 text-callout text-secondary">
          {elapsed && (
            <span>耗时：{formatDuration(info.status === 'completed' && info.started_at && info.completed_at
              ? new Date(info.completed_at).getTime() - new Date(info.started_at).getTime()
              : elapsed
            )}</span>
          )}
          {eta && info.status === 'running' && (
            <span>预计剩余：{formatDuration(eta)}</span>
          )}
        </div>
      )}

      {/* Error detail */}
      {info.error_message && showError && (
        <div className="mb-4 p-3 bg-error/5 rounded-xl border border-error/20">
          <p className="text-mono text-error text-sm whitespace-pre-wrap">{info.error_message}</p>
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        {info.status === 'pending' && (
          <button
            className="button-primary flex items-center gap-1.5 text-callout disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            disabled={!prevCompleted || loading}
            onClick={() => act(() => stagesApi.run(novelId, stage))}
            title={!prevCompleted ? `请先完成「${STAGE_LABELS[prevStage!]}」阶段` : undefined}
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <Play className="w-4 h-4" strokeWidth={1.5} />}
            开始
          </button>
        )}

        {info.status === 'paused' && (
          <button
            className="button-primary flex items-center gap-1.5 text-callout cursor-pointer"
            disabled={loading}
            onClick={() => act(() => stagesApi.resume(novelId, stage))}
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <Play className="w-4 h-4" strokeWidth={1.5} />}
            继续
          </button>
        )}

        {info.status === 'running' && (
          <>
            <button
              className="button-secondary flex items-center gap-1.5 text-callout cursor-pointer"
              disabled={loading}
              onClick={() => act(() => stagesApi.pause(novelId, stage))}
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <Pause className="w-4 h-4" strokeWidth={1.5} />}
              暂停
            </button>
          </>
        )}

        {info.status === 'completed' && (
          <>
            <button
              className="button-secondary flex items-center gap-1.5 text-callout cursor-pointer"
              disabled={loading}
              onClick={() => act(() => stagesApi.run(novelId, stage))}
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <RotateCcw className="w-4 h-4" strokeWidth={1.5} />}
              重新运行
            </button>
            {exportOptions.map((option) => (
              <button
                key={`${stage}-${option.format}`}
                className="button-secondary flex items-center gap-1.5 text-callout cursor-pointer"
                onClick={() => handleExportArtifact(option.format)}
              >
                {option.format === 'json' ? (
                  <FileJson className="w-4 h-4" strokeWidth={1.5} />
                ) : (
                  <FileText className="w-4 h-4" strokeWidth={1.5} />
                )}
                {option.label}
              </button>
            ))}
          </>
        )}

        {info.status === 'failed' && (
          <>
            <button
              className="button-primary flex items-center gap-1.5 text-callout cursor-pointer"
              disabled={loading}
              onClick={() => act(() => stagesApi.retry(novelId, stage))}
            >
              {loading ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <RotateCcw className="w-4 h-4" strokeWidth={1.5} />}
              重试
            </button>
            {info.error_message && (
              <button
                className="button-secondary flex items-center gap-1.5 text-callout cursor-pointer"
                onClick={() => setShowError(v => !v)}
              >
                <Eye className="w-4 h-4" strokeWidth={1.5} />
                {showError ? '隐藏错误' : '查看错误'}
              </button>
            )}
          </>
        )}

        {info.status !== 'pending' && (
          <button
            type="button"
            className="button-secondary flex items-center gap-1.5 text-callout cursor-pointer"
            onClick={() => setShowLogs((prev) => !prev)}
          >
            <Terminal className="w-4 h-4" strokeWidth={1.5} />
            {showLogs ? '隐藏日志' : '查看日志'}
          </button>
        )}
      </div>

      {showLogs && (
        <div className="mt-4 rounded-xl border border-border bg-subtle p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-callout font-medium text-primary">
              运行日志快照 · run #{info.run_seq ?? '—'}
            </p>
            {stageLogQuery.isFetching && (
              <span className="inline-flex items-center gap-1 text-caption text-secondary">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                刷新中
              </span>
            )}
          </div>

          {stageLogQuery.isLoading ? (
            <div className="rounded-lg bg-white px-3 py-3 text-caption text-secondary">正在加载日志...</div>
          ) : stageLogQuery.error ? (
            <div className="rounded-lg border border-error/20 bg-error/5 px-3 py-3 text-caption text-error">
              {stageLogQuery.error instanceof Error ? stageLogQuery.error.message : '日志加载失败'}
            </div>
          ) : (
            <pre className="max-h-72 overflow-auto rounded-lg bg-white px-3 py-3 font-mono text-[12px] leading-5 text-primary">
{JSON.stringify(stageLogQuery.data?.artifact ?? stageLogQuery.data?.latest_artifact ?? stageLogQuery.data?.run ?? null, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

// ── Chapter Status Dots ───────────────────────────────────────────────────────

function formatStageTime(iso?: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function ChapterDots({ stageStatuses, stageTimings }: {
  stageStatuses: Partial<Record<StageName, StageStatus>>
  stageTimings?: Partial<Record<StageName, { started_at?: string | null; completed_at?: string | null }>>
}) {
  return (
    <div className="flex items-center gap-1">
      {STAGE_NAMES.map(s => {
        const status = stageStatuses[s] ?? 'pending'
        const color =
          status === 'completed' ? 'bg-success' :
          status === 'running' ? 'bg-accent animate-pulse' :
          status === 'failed' ? 'bg-error' :
          status === 'stale' || status === 'paused' ? 'bg-warning' :
          'bg-border'
        const timing = stageTimings?.[s]
        let label = status === 'stale' ? `${STAGE_LABELS[s]}（已过期）` : STAGE_LABELS[s]
        if (status === 'completed' && timing?.completed_at) {
          label += ` ${formatStageTime(timing.completed_at)}`
        } else if (status === 'running' && timing?.started_at) {
          label += ` 开始于 ${formatStageTime(timing.started_at)}`
        } else if (status === 'failed' && timing?.completed_at) {
          label += ` 失败于 ${formatStageTime(timing.completed_at)}`
        }
        return <span key={s} className={`w-2 h-2 rounded-full flex-shrink-0 ${color}`} title={label} />
      })}
    </div>
  )
}

// ── Chapter Status Badge ──────────────────────────────────────────────────────

function ChapterStatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    analyzed: { label: '已分析', cls: 'bg-success/10 text-success' },
    rewritten: { label: '已改写', cls: 'bg-ai/10 text-ai' },
    failed: { label: '失败', cls: 'bg-error/10 text-error' },
    pending: { label: '待处理', cls: 'bg-subtle text-secondary' },
    running: { label: '处理中', cls: 'bg-accent/10 text-accent' },
  }
  const { label, cls } = map[status] ?? { label: status, cls: 'bg-subtle text-secondary' }
  return <span className={`text-caption font-medium px-2.5 py-1 rounded-full ${cls}`}>{label}</span>
}

// ── Export Dropdown ───────────────────────────────────────────────────────────

function ExportDropdown({
  novelId,
  onRiskSignatureChange,
}: {
  novelId: string
  onRiskSignatureChange?: (value: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [format, setFormat] = useState<'txt' | 'epub' | 'compare'>('txt')
  const [scope, setScope] = useState<'all' | 'chapter_range' | 'rewritten_only'>('all')
  const [chapterStart, setChapterStart] = useState('')
  const [chapterEnd, setChapterEnd] = useState('')
  const [force, setForce] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [riskSignature, setRiskSignature] = useState<string | null>(null)

  const handleExport = async () => {
    setExporting(true)
    setErrorMessage(null)
    setRiskSignature(null)
    try {
      const file = await novelsApi.exportFinal(novelId, {
        format,
        scope,
        chapter_start: scope === 'chapter_range' ? Number(chapterStart || 0) : undefined,
        chapter_end: scope === 'chapter_range' ? Number(chapterEnd || 0) : undefined,
        force,
      })
      const suggestedFilename = file.filename || `novel-export.${format === 'compare' ? 'html' : format}`
      const triggerBrowserDownload = () => {
        const url = URL.createObjectURL(file.blob)
        const a = document.createElement('a')
        a.href = url
        a.download = suggestedFilename
        a.click()
        URL.revokeObjectURL(url)
      }

      // Prefer native "Save As" when available (desktop Chromium/Electron),
      // and gracefully fall back to regular browser download.
      const maybeWindow = window as Window & {
        showSaveFilePicker?: (options?: {
          suggestedName?: string
          types?: Array<{ description?: string; accept: Record<string, string[]> }>
        }) => Promise<{
          createWritable: () => Promise<{ write: (data: Blob) => Promise<void>; close: () => Promise<void> }>
        }>
      }
      const extension = format === 'compare' ? 'html' : format
      const mimeType = format === 'txt'
        ? 'text/plain'
        : format === 'epub'
          ? 'application/epub+zip'
          : 'text/html'
      if (window.isSecureContext && maybeWindow.showSaveFilePicker) {
        try {
          const handle = await maybeWindow.showSaveFilePicker({
            suggestedName: suggestedFilename,
            types: [
              {
                description: `${extension.toUpperCase()} 文件`,
                accept: { [mimeType]: [`.${extension}`] },
              },
            ],
          })
          const writable = await handle.createWritable()
          await writable.write(file.blob)
          await writable.close()
        } catch (pickerError) {
          if (pickerError instanceof DOMException && pickerError.name === 'AbortError') {
            setExporting(false)
            return
          }
          triggerBrowserDownload()
        }
      } else {
        triggerBrowserDownload()
      }
      setRiskSignature(file.risk_signature ?? null)
      onRiskSignatureChange?.(file.risk_signature ?? null)
      setOpen(false)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'QUALITY_GATE_BLOCKED') {
        setErrorMessage('质量闸门阻断导出，请修复后重试，或启用“强制导出”。')
      } else if (error instanceof ApiError) {
        setErrorMessage(error.message)
      } else {
        setErrorMessage('导出失败，请稍后重试。')
      }
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="relative">
      <button
        className="button-primary flex items-center gap-2 cursor-pointer"
        onClick={() => setOpen(v => !v)}
      >
        <Download className="w-4 h-4" strokeWidth={1.5} />
        <span>导出</span>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-150 ${open ? 'rotate-180' : ''}`} strokeWidth={1.5} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1 z-20 bg-white rounded-xl shadow-md border border-subtle/60 p-4 w-72 space-y-3">
            <div className="space-y-1">
              <label className="text-caption text-secondary">导出格式</label>
              <select
                className="w-full border border-border rounded-lg px-2 py-1.5 text-callout"
                value={format}
                onChange={(e) => setFormat(e.target.value as 'txt' | 'epub' | 'compare')}
              >
                <option value="txt">TXT</option>
                <option value="epub">EPUB</option>
                <option value="compare">对照导出</option>
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-caption text-secondary">导出范围</label>
              <select
                className="w-full border border-border rounded-lg px-2 py-1.5 text-callout"
                value={scope}
                onChange={(e) => setScope(e.target.value as 'all' | 'chapter_range' | 'rewritten_only')}
              >
                <option value="all">全书</option>
                <option value="chapter_range">章节范围</option>
                <option value="rewritten_only">仅已改写章节</option>
              </select>
            </div>
            {scope === 'chapter_range' && (
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="number"
                  className="border border-border rounded-lg px-2 py-1.5 text-callout"
                  placeholder="起始章"
                  min={1}
                  value={chapterStart}
                  onChange={(e) => setChapterStart(e.target.value)}
                />
                <input
                  type="number"
                  className="border border-border rounded-lg px-2 py-1.5 text-callout"
                  placeholder="结束章"
                  min={1}
                  value={chapterEnd}
                  onChange={(e) => setChapterEnd(e.target.value)}
                />
              </div>
            )}
            <label className="flex items-center gap-2 text-callout text-primary">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              强制导出（风险标记）
            </label>
            {errorMessage && (
              <p className="text-callout text-error bg-error/5 border border-error/20 rounded-lg px-2 py-1.5">{errorMessage}</p>
            )}
            <button
              className="button-primary w-full flex items-center justify-center gap-2 cursor-pointer"
              disabled={exporting}
              onClick={handleExport}
            >
              {exporting ? <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} /> : <Download className="w-4 h-4" strokeWidth={1.5} />}
              执行导出
            </button>
          </div>
        </>
      )}
      {riskSignature && (
        <p className="mt-2 text-caption text-warning">风险导出签名: {riskSignature}</p>
      )}
    </div>
  )
}

// ── Split Rules Panel ────────────────────────────────────────────────────────

function createBlankSplitRule(priority = 0): SplitRuleSpec {
  return {
    id: null,
    name: '',
    pattern: '',
    priority,
    enabled: true,
    builtin: false,
  }
}

function sortRulesByPriority(rules: SplitRuleSpec[]): SplitRuleSpec[] {
  return [...rules].sort((a, b) => a.priority - b.priority || a.name.localeCompare(b.name, 'zh-Hans-CN'))
}

interface SplitPreviewRuleOption {
  id: string
  name: string
  enabled: boolean
  builtin: boolean
  priority: number
}

function resolveSavedCustomRuleId(
  nextRules: SplitRuleSpec[],
  currentRule: SplitRuleSpec,
  payload: Pick<SplitRuleSpec, 'name' | 'pattern' | 'priority' | 'enabled'>
): string | null {
  if (currentRule.id) return currentRule.id
  const exact = nextRules.find((rule) =>
    rule.name === payload.name &&
    rule.pattern === payload.pattern &&
    rule.priority === payload.priority &&
    rule.enabled === payload.enabled
  )
  if (exact?.id) return exact.id
  const fallback = nextRules.find((rule) => rule.name === payload.name && rule.pattern === payload.pattern)
  return fallback?.id ?? null
}

function describeSplitPreviewFailure(reason: string | null | undefined, matchedCount: number): string {
  if (!reason) return ''
  if (reason === 'NO_MATCH') return '当前规则在正文中未命中任何章节标题。'
  if (reason === 'MATCH_COUNT_TOO_LOW') return `当前规则仅命中 ${matchedCount} 处标题，无法稳定切分。`
  return reason
}

function buildPreviewRuleOptions(builtinRules: SplitRuleSpec[], customRules: SplitRuleSpec[]): SplitPreviewRuleOption[] {
  return [...builtinRules, ...sortRulesByPriority(customRules)]
    .filter((rule): rule is SplitRuleSpec & { id: string } => typeof rule.id === 'string' && rule.id.length > 0)
    .map((rule) => ({
      id: rule.id,
      name: rule.name,
      enabled: rule.enabled,
      builtin: rule.builtin,
      priority: rule.priority,
    }))
}

function defaultPreviewRuleId(options: SplitPreviewRuleOption[]): string | null {
  return options.find((rule) => rule.enabled)?.id ?? options[0]?.id ?? null
}

function SplitRulesPanel({ novelId }: { novelId: string }) {
  const queryClient = useQueryClient()
  const { data: config, isLoading } = useQuery({
    queryKey: ['split-rules'],
    queryFn: splitRulesApi.get,
    staleTime: 30_000,
  })

  const [builtinRules, setBuiltinRules] = useState<SplitRuleSpec[]>([])
  const [customRules, setCustomRules] = useState<SplitRuleSpec[]>([])
  const [newRule, setNewRule] = useState<SplitRuleSpec>(createBlankSplitRule(100))
  const [preview, setPreview] = useState<SplitRulesPreviewResponse | null>(null)
  const [selectedPreviewRuleId, setSelectedPreviewRuleId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [savingAll, setSavingAll] = useState(false)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [ruleBusyId, setRuleBusyId] = useState<string | null>(null)
  const [panelCollapsed, setPanelCollapsed] = useState(false)

  useEffect(() => {
    setPreview(null)
    setMessage(null)
  }, [novelId])

  useEffect(() => {
    if (!config) return
    setBuiltinRules(config.builtin_rules.map((rule) => ({ ...rule })))
    setCustomRules(sortRulesByPriority(config.custom_rules.map((rule) => ({ ...rule }))))
  }, [config])

  const draft = useMemo(() => ({
    builtin_rules: builtinRules,
    custom_rules: customRules,
  }), [builtinRules, customRules])

  const previewRuleOptions = useMemo(
    () => buildPreviewRuleOptions(builtinRules, customRules),
    [builtinRules, customRules]
  )
  const previewRuleMap = useMemo(
    () => new Map(previewRuleOptions.map((rule) => [rule.id, rule])),
    [previewRuleOptions]
  )
  const hasEnabledPreviewRule = previewRuleOptions.some((rule) => rule.enabled)
  const selectedPreviewRule = selectedPreviewRuleId ? previewRuleMap.get(selectedPreviewRuleId) ?? null : null
  const previewValid = preview?.preview_valid ?? false
  const failureReason = preview?.failure_reason ?? null
  const matchedCount = preview?.matched_count ?? 0
  const ruleName = preview?.matched_lines[0]?.rule_name ?? selectedPreviewRule?.name ?? null

  useEffect(() => {
    setSelectedPreviewRuleId((current) => {
      if (current && previewRuleMap.has(current)) return current
      return defaultPreviewRuleId(previewRuleOptions)
    })
  }, [previewRuleMap, previewRuleOptions])

  const persistDraft = async () => {
    const next = await splitRulesApi.replace(draft)
    queryClient.setQueryData(['split-rules'], next)
    setBuiltinRules(next.builtin_rules.map((rule) => ({ ...rule })))
    setCustomRules(sortRulesByPriority(next.custom_rules.map((rule) => ({ ...rule }))))
    return next
  }

  const handlePreview = async () => {
    setPreviewBusy(true)
    setMessage(null)
    try {
      const result = await splitRulesApi.preview({
        novel_id: novelId,
        sample_size: SPLIT_MATCH_SAMPLE_SIZE,
        selected_rule_id: selectedPreviewRuleId || undefined,
        builtin_rules: draft.builtin_rules,
        custom_rules: draft.custom_rules,
      })
      setPreview(result)
      setMessage('已按选中规则生成预览，确认时会先保存草稿规则再执行切分。')
    } catch (error) {
      if (getApiErrorCode(error) === 'PREVIEW_STALE') {
        setPreview(null)
        setMessage('预览已过期，请重新按选中规则预览。')
      } else {
        setMessage(error instanceof Error ? error.message : '生成预览失败')
      }
    } finally {
      setPreviewBusy(false)
    }
  }

  const handleConfirm = async () => {
    if (!preview) return
    setConfirmBusy(true)
    setMessage(null)
    try {
      const next = await persistDraft()
      const nextOptions = buildPreviewRuleOptions(next.builtin_rules, next.custom_rules)
      const nextSelectedRuleId = selectedPreviewRuleId && nextOptions.some((rule) => rule.id === selectedPreviewRuleId)
        ? selectedPreviewRuleId
        : defaultPreviewRuleId(nextOptions)
      setSelectedPreviewRuleId(nextSelectedRuleId)

      const officialPreview = await splitRulesApi.runPreview(novelId, {
        split_rule_id: nextSelectedRuleId ?? undefined,
      })
      await splitRulesApi.confirm(novelId, officialPreview.preview_token)
      setMessage('章节切分确认完成。')
      setPreview(null)
      queryClient.invalidateQueries({ queryKey: ['novel', novelId] })
      queryClient.invalidateQueries({ queryKey: ['chapters', novelId] })
    } catch (error) {
      if (getApiErrorCode(error) === 'PREVIEW_STALE') {
        setMessage('预览已过期，请重新按选中规则预览后再确认。')
      } else {
        setMessage(error instanceof Error ? error.message : '确认切分失败')
      }
    } finally {
      setConfirmBusy(false)
    }
  }

  const handleSaveBuiltinToggle = (index: number, enabled: boolean) => {
    setBuiltinRules((prev) => prev.map((rule, currentIndex) => (currentIndex === index ? { ...rule, enabled } : rule)))
  }

  const handleUpdateCustom = (rule: SplitRuleSpec, patch: Partial<SplitRuleSpec>) => {
    setCustomRules((prev) => prev.map((item) => {
      if (rule.id) {
        return item.id === rule.id ? { ...item, ...patch } : item
      }
      return item === rule ? { ...item, ...patch } : item
    }))
  }

  const handleMoveCustom = (rule: SplitRuleSpec, direction: -1 | 1) => {
    setCustomRules((prev) => {
      const ordered = sortRulesByPriority(prev)
      const currentIndex = ordered.findIndex((item) => (rule.id ? item.id === rule.id : item === rule))
      const target = currentIndex + direction
      if (currentIndex < 0 || target < 0 || target >= ordered.length) return ordered
      const next = [...ordered]
      const [item] = next.splice(currentIndex, 1)
      next.splice(target, 0, item)
      return next.map((rule, currentIndex) => ({ ...rule, priority: (currentIndex + 1) * 10 }))
    })
  }

  const handleSaveCustom = async (rule: SplitRuleSpec) => {
    const index = customRules.findIndex((item) => (rule.id ? item.id === rule.id : item === rule))
    if (index < 0) return
    const current = customRules[index]
    if (!current.name.trim() || !current.pattern.trim()) {
      setMessage('自定义规则需要填写名称和正则。')
      return
    }

    setRuleBusyId(current.id ?? 'new')
    try {
      const payload = {
        name: current.name.trim(),
        pattern: current.pattern.trim(),
        priority: Math.max(0, Math.round(current.priority)),
        enabled: current.enabled,
      }
      const next = current.id
        ? await splitRulesApi.updateCustom(current.id, payload)
        : await splitRulesApi.createCustom(payload)
      queryClient.setQueryData(['split-rules'], next)
      setBuiltinRules(next.builtin_rules.map((item) => ({ ...item })))
      const nextCustomRules = sortRulesByPriority(next.custom_rules.map((item) => ({ ...item })))
      setCustomRules(nextCustomRules)
      const savedRuleId = resolveSavedCustomRuleId(nextCustomRules, current, payload)
      if (savedRuleId) setSelectedPreviewRuleId(savedRuleId)
      setMessage(current.id ? '自定义规则已更新。' : '自定义规则已创建。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存自定义规则失败')
    } finally {
      setRuleBusyId(null)
    }
  }

  const handleDeleteCustom = async (rule: SplitRuleSpec) => {
    const index = customRules.findIndex((item) => (rule.id ? item.id === rule.id : item === rule))
    if (index < 0) return
    const current = customRules[index]
    if (!current.id) {
      setCustomRules((prev) => prev.filter((_, currentIndex) => currentIndex !== index))
      return
    }

    setRuleBusyId(current.id)
    try {
      const next = await splitRulesApi.deleteCustom(current.id)
      queryClient.setQueryData(['split-rules'], next)
      setBuiltinRules(next.builtin_rules.map((item) => ({ ...item })))
      setCustomRules(sortRulesByPriority(next.custom_rules.map((item) => ({ ...item }))))
      setMessage('自定义规则已删除。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '删除自定义规则失败')
    } finally {
      setRuleBusyId(null)
    }
  }

  const handleAddCustom = () => {
    setCustomRules((prev) => [...prev, createBlankSplitRule((prev.length + 1) * 10)])
  }

  const handleSaveAll = async () => {
    setSavingAll(true)
    setMessage(null)
    try {
      const next = await persistDraft()
      setMessage('全部切分规则已保存。')
      queryClient.setQueryData(['split-rules'], next)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存切分规则失败')
    } finally {
      setSavingAll(false)
    }
  }

  const builtinEnabledCount = builtinRules.filter((rule) => rule.enabled).length

  return (
    <div className="rounded-2xl border border-border bg-white p-6 shadow-xs">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-title-3 font-semibold text-primary">章节切分规则</h2>
          <p className="mt-1 text-callout text-secondary">启用内置规则，调整自定义 regex，按选中规则预览，确认时再保存并执行切分。</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="rounded-xl bg-subtle px-3 py-2 text-right">
            <p className="text-caption text-secondary">规则版本</p>
            <p className="text-callout font-medium text-primary">{config?.rules_version ? shortHash(config.rules_version) : '加载中'}</p>
          </div>
          <button
            type="button"
            onClick={() => setPanelCollapsed((prev) => !prev)}
            className="button-secondary flex items-center gap-1.5 px-3 py-2 text-callout"
          >
            {panelCollapsed ? '展开' : '收起'}
            <ChevronDown className={`h-4 w-4 transition-transform duration-150 ${panelCollapsed ? '' : 'rotate-180'}`} />
          </button>
        </div>
      </div>

      {message && (
        <div className="mb-4 rounded-xl border border-border bg-subtle px-4 py-3 text-callout text-secondary">
          {message}
        </div>
      )}

      {panelCollapsed ? (
        <div className="rounded-xl border border-border bg-subtle px-4 py-3 text-callout text-secondary">
          内置启用 {builtinEnabledCount}/{builtinRules.length} · 自定义 {customRules.length} 条
          {preview ? ` · 已有预览（${preview.estimated_chapters} 章）` : ''}
        </div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-caption text-secondary">
              当前预览规则
              <select
                value={selectedPreviewRuleId ?? ''}
                onChange={(event) => setSelectedPreviewRuleId(event.target.value || null)}
                disabled={isLoading || previewRuleOptions.length === 0}
                className="min-w-56 rounded-lg border border-border bg-white px-2 py-1.5 text-callout text-primary outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {previewRuleOptions.length === 0 ? (
                  <option value="">暂无可选规则</option>
                ) : (
                  previewRuleOptions.map((rule) => (
                    <option key={rule.id} value={rule.id} disabled={!rule.enabled}>
                      {`${rule.builtin ? '内置' : '自定义'} · ${rule.name || '未命名规则'}${rule.enabled ? '' : '（未启用）'}`}
                    </option>
                  ))
                )}
              </select>
            </label>
            <button
              type="button"
              onClick={handlePreview}
              disabled={previewBusy || isLoading || !hasEnabledPreviewRule}
              className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {previewBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              按选中规则预览
            </button>
            <button
              type="button"
              onClick={handleSaveAll}
              disabled={savingAll || isLoading}
              className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {savingAll ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <Save className="h-4 w-4" />}
              保存全部规则
            </button>
            <span className="text-caption text-secondary">
              内置启用 {builtinEnabledCount}/{builtinRules.length} · 自定义 {customRules.length} 条
            </span>
          </div>
          <p className="mb-4 text-caption text-secondary">
            预览仅用于校验规则，不会写入章节；只有点击“确认切分”才会先保存草稿并执行正式切分。
          </p>

          <div className="grid gap-6 xl:grid-cols-2">
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Split className="h-4 w-4 text-accent" />
                <h3 className="text-title-3 font-semibold text-primary">内置规则</h3>
              </div>
              <div className="space-y-3">
                {isLoading && Array.from({ length: 3 }).map((_, index) => (
                  <div key={index} className="h-20 animate-pulse rounded-2xl bg-subtle" />
                ))}
                {builtinRules.map((rule, index) => (
                  <div key={rule.id ?? rule.name} className="rounded-2xl border border-border bg-page p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-callout font-medium text-primary">{rule.name}</span>
                          <span className="rounded-full bg-white px-2 py-0.5 text-caption text-secondary">priority {rule.priority}</span>
                        </div>
                        <p className="mt-1 font-mono text-caption text-secondary break-all">{rule.pattern}</p>
                      </div>
                      <label className="flex items-center gap-2 text-caption text-secondary">
                        <input
                          type="checkbox"
                          checked={rule.enabled}
                          onChange={(event) => handleSaveBuiltinToggle(index, event.target.checked)}
                        />
                        启用
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Pencil className="h-4 w-4 text-accent" />
                  <h3 className="text-title-3 font-semibold text-primary">自定义 regex 列表</h3>
                </div>
                <button
                  type="button"
                  onClick={handleAddCustom}
                  className="button-secondary flex items-center gap-2"
                >
                  <ArrowUpDown className="h-4 w-4" />
                  新增规则
                </button>
              </div>

              <div className="space-y-3">
                {customRules.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-border bg-subtle px-4 py-8 text-center text-callout text-secondary">
                    暂无自定义规则，点击“新增规则”开始编辑。
                  </div>
                )}

                {sortRulesByPriority(customRules).map((rule, sortedIndex) => {
                  const busyId = rule.id ?? 'new'
                  const busy = ruleBusyId === busyId
                  return (
                    <div key={rule.id ?? `draft-${sortedIndex}`} className="rounded-2xl border border-border bg-subtle p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 space-y-3">
                          <div className="grid gap-3 md:grid-cols-2">
                            <label className="space-y-1">
                              <span className="text-caption text-secondary">规则名称</span>
                              <input
                                value={rule.name}
                                onChange={(event) => handleUpdateCustom(rule, { name: event.target.value })}
                                className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                                placeholder="例如：第 0 章前言"
                              />
                            </label>
                            <label className="space-y-1">
                              <span className="text-caption text-secondary">优先级</span>
                              <input
                                type="number"
                                min="0"
                                step="1"
                                value={rule.priority}
                                onChange={(event) => handleUpdateCustom(rule, { priority: Number(event.target.value || 0) })}
                                className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                              />
                            </label>
                          </div>

                          <label className="block space-y-1">
                            <span className="text-caption text-secondary">regex</span>
                            <textarea
                              value={rule.pattern}
                              onChange={(event) => handleUpdateCustom(rule, { pattern: event.target.value })}
                              rows={3}
                              className="w-full rounded-xl border border-border bg-white px-3 py-2 font-mono text-caption text-primary outline-none focus:border-accent"
                              placeholder="例如：^第\\s*\\d+\\s*章"
                            />
                          </label>

                          <div className="flex items-center gap-4">
                            <label className="flex items-center gap-2 text-caption text-secondary">
                              <input
                                type="checkbox"
                                checked={rule.enabled}
                                onChange={(event) => handleUpdateCustom(rule, { enabled: event.target.checked })}
                              />
                              启用
                            </label>
                            <span className="text-caption text-secondary">ID: {rule.id ?? '新建中'}</span>
                          </div>
                        </div>

                        <div className="flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() => handleMoveCustom(rule, -1)}
                            className="button-secondary px-3 py-2 text-caption"
                          >
                            <ChevronUp className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleMoveCustom(rule, 1)}
                            className="button-secondary px-3 py-2 text-caption"
                          >
                            <ChevronDown className="h-4 w-4" />
                          </button>
                        </div>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => handleSaveCustom(rule)}
                          disabled={ruleBusyId !== null}
                          className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <Save className="h-4 w-4" />}
                          保存
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteCustom(rule)}
                          disabled={ruleBusyId !== null}
                          className="button-secondary flex items-center gap-2 text-error disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <Trash2 className="h-4 w-4" />
                          删除
                        </button>
                      </div>
                    </div>
                  )
                })}

                <div className="rounded-2xl border border-dashed border-accent/30 bg-accent/5 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-callout font-medium text-primary">快速添加</p>
                      <p className="text-caption text-secondary">先填名称、regex、优先级，再保存。</p>
                    </div>
                    <button
                      type="button"
                      onClick={async () => {
                        if (!newRule.name.trim() || !newRule.pattern.trim()) {
                          setMessage('新增规则需要先填写名称和 regex。')
                          return
                        }
                        setRuleBusyId('new-rule')
                        try {
                          const payload = {
                            name: newRule.name.trim(),
                            pattern: newRule.pattern.trim(),
                            priority: Math.max(0, Math.round(newRule.priority)),
                            enabled: newRule.enabled,
                          }
                          const next = await splitRulesApi.createCustom({
                            name: payload.name,
                            pattern: payload.pattern,
                            priority: payload.priority,
                            enabled: payload.enabled,
                          })
                          queryClient.setQueryData(['split-rules'], next)
                          setBuiltinRules(next.builtin_rules.map((item) => ({ ...item })))
                          const nextCustomRules = sortRulesByPriority(next.custom_rules.map((item) => ({ ...item })))
                          setCustomRules(nextCustomRules)
                          const created = resolveSavedCustomRuleId(nextCustomRules, createBlankSplitRule(), payload)
                          if (created) setSelectedPreviewRuleId(created)
                          setNewRule(createBlankSplitRule((next.custom_rules.length + 1) * 10))
                          setMessage('新规则已添加。')
                        } catch (error) {
                          setMessage(error instanceof Error ? error.message : '创建新规则失败')
                        } finally {
                          setRuleBusyId(null)
                        }
                      }}
                      disabled={ruleBusyId === 'new-rule'}
                      className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {ruleBusyId === 'new-rule' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      添加
                    </button>
                  </div>
                  <div className="mt-3 grid gap-3 md:grid-cols-3">
                    <input
                      value={newRule.name}
                      onChange={(event) => setNewRule((prev) => ({ ...prev, name: event.target.value }))}
                      className="rounded-xl border border-border bg-white px-3 py-2 text-body outline-none focus:border-accent"
                      placeholder="规则名称"
                    />
                    <input
                      value={newRule.pattern}
                      onChange={(event) => setNewRule((prev) => ({ ...prev, pattern: event.target.value }))}
                      className="rounded-xl border border-border bg-white px-3 py-2 font-mono text-caption outline-none focus:border-accent"
                      placeholder="regex"
                    />
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={newRule.priority}
                      onChange={(event) => setNewRule((prev) => ({ ...prev, priority: Number(event.target.value || 0) }))}
                      className="rounded-xl border border-border bg-white px-3 py-2 text-body outline-none focus:border-accent"
                      placeholder="priority"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>

          {preview && (
            <div className="mt-6 rounded-2xl border border-border bg-subtle p-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <p className="text-title-3 font-semibold text-primary">预览结果</p>
              <p className="text-caption text-secondary">
                按规则「{ruleName ?? '自动选择'}」预览 · 预计 {preview.estimated_chapters} 章 · 命中 {matchedCount} 处
              </p>
              {preview.estimated_chapters > matchedCount && (
                <p className="mt-1 text-caption text-secondary">
                  说明：正文前存在导语时，会额外生成“前言”章节。
                </p>
              )}
              <p className="mt-1 text-caption text-secondary">
                已展示全部预览章节（{preview.chapters.length} 章），可在下方滚动区域查看。
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleConfirm}
                disabled={confirmBusy}
                className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {confirmBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                确认切分
              </button>
              <button
                type="button"
                onClick={handlePreview}
                disabled={previewBusy}
                className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {previewBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                按选中规则重新预览
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl bg-white px-3 py-2">
              <p className="text-caption text-secondary">预览状态</p>
              <p className={`text-callout font-medium ${previewValid ? 'text-success' : 'text-warning'}`}>
                {previewValid ? '可确认' : '需要重新检查'}
              </p>
            </div>
            <div className="rounded-xl bg-white px-3 py-2">
              <p className="text-caption text-secondary">preview token</p>
              <p className="text-callout font-medium text-primary">{shortHash(preview.preview_token)}</p>
            </div>
            <div className="rounded-xl bg-white px-3 py-2">
              <p className="text-caption text-secondary">source revision</p>
              <p className="text-callout font-medium text-primary">{shortHash(preview.source_revision)}</p>
            </div>
            <div className="rounded-xl bg-white px-3 py-2">
              <p className="text-caption text-secondary">rules version</p>
              <p className="text-callout font-medium text-primary">{shortHash(preview.rules_version)}</p>
            </div>
          </div>

          {failureReason && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-warning/20 bg-warning/10 p-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 text-warning" />
              <div>
                <p className="text-callout font-medium text-warning">预览异常</p>
                <p className="text-caption text-secondary">{describeSplitPreviewFailure(failureReason, matchedCount)}</p>
              </div>
            </div>
          )}

          {preview.matched_lines.length > 0 && (
            <div className="mt-4 rounded-xl border border-border bg-white p-3">
              <p className="text-callout font-medium text-primary">命中样本（最多 {SPLIT_MATCH_SAMPLE_SIZE} 条）</p>
              <div className="mt-2 max-h-40 space-y-1 overflow-y-auto pr-1">
                {preview.matched_lines.map((line) => (
                  <p key={`${line.line_number}-${line.paragraph_index}`} className="font-mono text-caption text-secondary">
                    第 {line.line_number} 行 · {line.text}
                  </p>
                ))}
              </div>
            </div>
          )}

          <div className="mt-4 max-h-[60vh] space-y-2 overflow-y-auto pr-1">
            {preview.chapters.map((chapter) => (
              <div key={chapter.id} className="rounded-xl border border-border bg-white px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-callout font-medium text-primary">
                      第 {chapter.index} 章 · {chapter.title}
                    </p>
                    <p className="text-caption text-secondary">
                      {chapter.paragraph_count} 段 · {chapter.char_count.toLocaleString()} 字
                    </p>
                  </div>
                  <span className="rounded-full bg-subtle px-2 py-1 text-caption text-secondary">preview</span>
                </div>
                <p className="mt-2 line-clamp-2 text-caption leading-6 text-secondary">{chapter.content}</p>
              </div>
            ))}
          </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Rewrite Coverage Panel ───────────────────────────────────────────────────

function RewriteCoveragePanel({ novelId, chapters }: { novelId: string; chapters: ChapterListItem[] }) {
  const queries = useQueries({
    queries: chapters.map((chapter) => ({
      queryKey: ['chapter-rewrites', novelId, chapter.index],
      queryFn: () => chaptersApi.getRewrites(novelId, chapter.index),
      enabled: !!novelId && chapters.length > 0,
      staleTime: 30_000,
    })),
  })

  const summaries = useMemo(() => {
    return chapters.map((chapter, index) => ({
      chapterIndex: chapter.index,
      chapterTitle: chapter.title,
      rewrites: (queries[index]?.data ?? []) as RewriteCoverageItem[],
    }))
  }, [chapters, queries])

  const coverage = useMemo(() => summarizeRewriteCoverage(summaries), [summaries])
  const loading = queries.some((query) => query.isLoading)
  const hasData = queries.some((query) => (query.data?.length ?? 0) > 0)

  return (
    <div className="rounded-2xl border border-border bg-white p-6 shadow-xs">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-title-3 font-semibold text-primary">改写覆盖率与回退告警</h2>
          <p className="mt-1 text-callout text-secondary">展示已改写、保留、失败和回退原文的实际分布。</p>
        </div>
        <div className="rounded-xl bg-subtle px-3 py-2 text-right">
          <p className="text-caption text-secondary">章节数</p>
          <p className="text-callout font-medium text-primary">{chapters.length}</p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <div className="rounded-xl bg-page p-3">
          <p className="text-caption text-secondary">已改写</p>
          <p className="text-title-2 font-semibold text-primary">{coverage.rewrittenSegments}</p>
        </div>
        <div className="rounded-xl bg-page p-3">
          <p className="text-caption text-secondary">保留</p>
          <p className="text-title-2 font-semibold text-primary">{coverage.preservedSegments}</p>
        </div>
        <div className="rounded-xl bg-page p-3">
          <p className="text-caption text-secondary">失败</p>
          <p className="text-title-2 font-semibold text-error">{coverage.failedSegments}</p>
        </div>
        <div className="rounded-xl bg-page p-3">
          <p className="text-caption text-secondary">回退</p>
          <p className="text-title-2 font-semibold text-warning">{coverage.rollbackSegments}</p>
        </div>
      </div>

      {loading && (
        <div className="mt-4 space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div key={index} className="h-20 animate-pulse rounded-2xl bg-subtle" />
          ))}
        </div>
      )}

      {!loading && !hasData && (
        <div className="mt-4 rounded-2xl border border-dashed border-border bg-subtle px-4 py-8 text-center text-callout text-secondary">
          暂无改写结果，先完成 rewrite 阶段后再查看。
        </div>
      )}

      <div className="mt-4 space-y-3">
        {coverage.chapters.map((chapter) => {
          const hasAnchorMismatch = chapter.failureCodes.includes('ANCHOR_MISMATCH') || chapter.warningCodes.includes('ANCHOR_MISMATCH')
          return (
            <div key={chapter.chapterIndex} className="rounded-2xl border border-border bg-subtle p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-callout font-medium text-primary">
                    第 {chapter.chapterIndex} 章 · {chapter.chapterTitle}
                  </p>
                  <p className="text-caption text-secondary">
                    {chapter.totalSegments} 段 · 改写 {chapter.rewrittenSegments} · 保留 {chapter.preservedSegments} · 失败 {chapter.failedSegments}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {chapter.failedSegments > 0 && (
                    <span className="rounded-full bg-error/10 px-2 py-1 text-caption text-error">FAILED</span>
                  )}
                  {chapter.rollbackSegments > 0 && (
                    <span className="rounded-full bg-warning/10 px-2 py-1 text-caption text-warning">ROLLBACK</span>
                  )}
                  {hasAnchorMismatch && (
                    <span className="rounded-full bg-warning/10 px-2 py-1 text-caption text-warning">ANCHOR_MISMATCH</span>
                  )}
                </div>
              </div>

              <div className="mt-2 flex flex-wrap gap-2">
                {chapter.failureCodes.map((code) => (
                  <span key={`${chapter.chapterIndex}-${code}`} className="rounded-full bg-white px-2 py-1 text-caption text-error">
                    {code}
                  </span>
                ))}
                {chapter.warningCodes.map((code) => (
                  <span key={`${chapter.chapterIndex}-warn-${code}`} className="rounded-full bg-white px-2 py-1 text-caption text-warning">
                    {code}
                  </span>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Quality Gate Panel ───────────────────────────────────────────────────────

function QualityGatePanel({
  novelId,
  taskId,
  latestRiskSignature,
}: {
  novelId: string
  taskId?: string | null
  latestRiskSignature: string | null
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['quality-report', novelId, taskId],
    queryFn: () => fetchQualityReport(novelId, taskId),
    enabled: !!novelId && !!taskId,
    staleTime: 30_000,
  })

  const report = useMemo(() => normalizeQualityReport(data), [data])

  if (!taskId) {
    return (
      <div className="rounded-2xl border border-border bg-white p-6 shadow-xs">
        <h2 className="text-title-3 font-semibold text-primary">质量闸门</h2>
        <p className="mt-2 text-callout text-secondary">切分完成后、进入 assemble 阶段时会自动显示质量报告。</p>
      </div>
    )
  }

  return (
    <div className="rounded-2xl border border-border bg-white p-6 shadow-xs">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-title-3 font-semibold text-primary">质量闸门阻断面板</h2>
          <p className="mt-1 text-callout text-secondary">对比阈值、阻断原因和建议，必要时可在右上角导出菜单启用强制导出。</p>
        </div>
        {report?.blocked ? (
          <span className="rounded-full bg-error/10 px-3 py-1 text-caption font-semibold text-error">BLOCKED</span>
        ) : (
          <span className="rounded-full bg-success/10 px-3 py-1 text-caption font-semibold text-success">PASS</span>
        )}
      </div>

      {isLoading && <div className="h-24 animate-pulse rounded-2xl bg-subtle" />}

      {report && (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            {report.thresholdComparisons.map((item) => (
              <div key={item.label} className="rounded-2xl bg-page p-4">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-callout font-medium text-primary">{item.label}</p>
                  <span className={`rounded-full px-2 py-0.5 text-caption ${
                    item.status === 'blocked' ? 'bg-error/10 text-error' : item.status === 'warning' ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success'
                  }`}>
                    {item.status.toUpperCase()}
                  </span>
                </div>
                <p className="mt-2 text-body-bold text-primary">
                  {item.actual.toFixed(3)} / {item.threshold.toFixed(3)} {item.unit ?? ''}
                </p>
                {item.suggestion && <p className="mt-1 text-caption text-secondary">{item.suggestion}</p>}
              </div>
            ))}
          </div>

          <div className="mt-4 rounded-2xl border border-border bg-subtle p-4">
            <p className="text-callout font-medium text-primary">统计信息</p>
            <div className="mt-3 grid gap-3 md:grid-cols-4">
              <div className="rounded-xl bg-white p-3">
                <p className="text-caption text-secondary">失败段</p>
                <p className="text-title-2 font-semibold text-error">{report.stats.failed_segments}</p>
              </div>
              <div className="rounded-xl bg-white p-3">
                <p className="text-caption text-secondary">告警数</p>
                <p className="text-title-2 font-semibold text-warning">{report.stats.warning_count}</p>
              </div>
              <div className="rounded-xl bg-white p-3">
                <p className="text-caption text-secondary">保留段</p>
                <p className="text-title-2 font-semibold text-primary">{report.stats.preserved_segments}</p>
              </div>
              <div className="rounded-xl bg-white p-3">
                <p className="text-caption text-secondary">风险签名</p>
                <p className="truncate font-mono text-caption text-primary">{latestRiskSignature ?? '未执行强制导出'}</p>
              </div>
            </div>
          </div>

          {report.blockReasons.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {report.blockReasons.map((reason) => (
                <span key={reason} className="rounded-full bg-error/10 px-3 py-1 text-caption text-error">
                  {reason}
                </span>
              ))}
            </div>
          )}

          {report.warnings.length > 0 && (
            <div className="mt-4 space-y-2">
              {report.warnings.slice(0, 8).map((warning, index) => (
                <div key={`${warning.code}-${index}`} className="rounded-xl border border-border bg-white px-3 py-2">
                  <p className="text-callout font-medium text-primary">{warning.code}</p>
                  <p className="text-caption text-secondary">{warning.message}</p>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── NovelDetail ───────────────────────────────────────────────────────────────

type WorkbenchViewMode = 'rewrite' | 'diff'
type DiffTextMode = 'raw' | 'canonical' | 'sentence'
type WorkbenchRightTab = 'insights' | 'operations' | 'logs'
type LeftStageFilter = 'all' | 'pending' | 'running' | 'completed' | 'failed' | 'attention'
type ChapterRewriteDraftSnapshot = Record<number, { text: string; saved_at: string }>
type ChapterStageRuntimeOverrides = Partial<Record<StageName, Record<number, StageStatus>>>

type RewriteWindowAttemptView = {
  window_id: string
  attempt_seq?: number
  run_seq?: number | null
  finish_reason?: string | null
  action?: string | null
  guardrail?: {
    level?: string | null
    codes?: string[]
    details?: Record<string, unknown> | null
  } | null
}

type RewriteDisplaySegment = RewriteSegment & {
  original_text?: string | null
  rewritten_text?: string | null
  rewritten_chars?: number | null
  status?: string | null
  completion_kind?: 'normal' | 'noop'
  reason_code?: string | null
  has_warnings?: boolean
  warning_count?: number
  warning_codes?: string[]
  window_attempts?: RewriteWindowAttemptView[]
  anchor_verified?: boolean | null
  error_code?: string | null
  error_detail?: string | null
  provider_raw_response?: Record<string, unknown> | null
  validation_details?: Record<string, unknown> | null
  target_ratio?: number | null
  target_chars?: number | null
  target_chars_min?: number | null
  target_chars_max?: number | null
}

const CORE_STAGE_NAMES: StageName[] = ['split', 'analyze', 'rewrite', 'assemble']
const RISK_STAGE_NAMES: StageName[] = ['split', 'analyze', 'mark', 'rewrite', 'assemble']
const EMPTY_CHAPTER_ITEMS: ChapterListItem[] = []
const EMPTY_REWRITE_SEGMENTS: RewriteDisplaySegment[] = []
const CANONICAL_DIFF_HEADING_RE = /^(?:第[\d零一二三四五六七八九十百千万两〇]+(?:章|节|回|卷|部|篇|集)(?:\s*.+)?|序章|前言|楔子|尾声|后记|番外.*)$/u
const SENTENCE_CLOSER_RE = /[”"’』」》）\])]/u
const PARAGRAPH_SPLIT_RE = /(?:\r?\n\s*){2,}/g

function chapterTitle(chapter?: ChapterListItem | null): string {
  if (!chapter) return '未选择章节'
  return chapter.title?.trim() || `第 ${chapter.index} 章`
}

function chapterCharCount(chapter?: ChapterListItem | null): number {
  if (!chapter) return 0
  const value = (chapter as ChapterListItem & { char_count?: number; word_count?: number }).char_count
    ?? (chapter as ChapterListItem & { word_count?: number }).word_count
    ?? 0
  return Number(value) || 0
}

function stageStatusLabel(status: StageStatus): string {
  switch (status) {
    case 'completed':
      return '已完成'
    case 'running':
      return '运行中'
    case 'failed':
      return '失败'
    case 'paused':
      return '已暂停'
    case 'stale':
      return '已过期'
    default:
      return '待处理'
  }
}

function stageStatusClass(status: StageStatus): string {
  switch (status) {
    case 'completed':
      return 'bg-success/10 text-success'
    case 'running':
      return 'bg-accent/10 text-accent'
    case 'failed':
      return 'bg-error/10 text-error'
    case 'paused':
      return 'bg-warning/10 text-warning'
    case 'stale':
      return 'bg-warning/10 text-warning'
    default:
      return 'bg-subtle text-secondary'
  }
}

export function mergeAnalyzeAndMarkStatus(analyze: StageRunInfo, mark: StageRunInfo): StageRunInfo {
  const chaptersTotal = Math.max(analyze.chapters_total || 0, mark.chapters_total || 0)
  const completedDone = Math.max(analyze.chapters_done || 0, mark.chapters_done || 0)
  const warningsCount = Math.max(analyze.warnings_count ?? 0, mark.warnings_count ?? 0)
  const normalizedMarkStatus: StageStatus = mark.status === 'stale' ? 'paused' : mark.status
  const normalizedAnalyzeStatus: StageStatus = analyze.status === 'stale' ? 'paused' : analyze.status

  let mergedStatus: StageStatus = 'pending'
  let chaptersDone = mark.chapters_done ?? analyze.chapters_done ?? 0
  let errorMessage: string | undefined = mark.error_message ?? analyze.error_message

  if (normalizedMarkStatus === 'completed') {
    mergedStatus = 'completed'
    chaptersDone = completedDone
    errorMessage = undefined
  } else if (normalizedMarkStatus === 'running' || normalizedMarkStatus === 'paused' || normalizedMarkStatus === 'failed') {
    mergedStatus = normalizedMarkStatus
    chaptersDone = mark.chapters_done ?? analyze.chapters_done ?? 0
    errorMessage = mark.error_message ?? analyze.error_message
  } else if (normalizedAnalyzeStatus === 'running') {
    mergedStatus = 'running'
    chaptersDone = analyze.chapters_done ?? mark.chapters_done ?? 0
    errorMessage = analyze.error_message ?? mark.error_message
  } else if (normalizedAnalyzeStatus === 'paused') {
    mergedStatus = 'paused'
    chaptersDone = analyze.chapters_done ?? mark.chapters_done ?? 0
    errorMessage = analyze.error_message ?? mark.error_message
  } else if (normalizedAnalyzeStatus === 'failed') {
    mergedStatus = 'failed'
    chaptersDone = analyze.chapters_done ?? mark.chapters_done ?? 0
    errorMessage = analyze.error_message ?? mark.error_message
  } else {
    mergedStatus = 'pending'
    chaptersDone = mark.chapters_done ?? analyze.chapters_done ?? 0
    errorMessage = mark.error_message ?? analyze.error_message
  }

  return {
    ...analyze,
    status: mergedStatus,
    run_seq: analyze.run_seq,
    started_at: analyze.started_at ?? mark.started_at,
    completed_at: mark.completed_at ?? analyze.completed_at,
    error_message: errorMessage,
    warnings_count: warningsCount,
    artifact_path: analyze.artifact_path,
    config_snapshot: analyze.config_snapshot ?? mark.config_snapshot,
    chapters_total: chaptersTotal,
    chapters_done: mergedStatus === 'completed' ? completedDone : chaptersDone,
  }
}

function viewModeLabel(mode: WorkbenchViewMode): string {
  switch (mode) {
    case 'rewrite':
      return '改写稿预览'
    case 'diff':
      return 'Diff'
  }
}

function diffTextModeLabel(mode: DiffTextMode): string {
  switch (mode) {
    case 'raw':
      return '原文对齐'
    case 'canonical':
      return '规范化对齐'
    case 'sentence':
      return '句子对齐'
  }
}

function rightTabLabel(tab: WorkbenchRightTab): string {
  switch (tab) {
    case 'insights':
      return '洞察'
    case 'operations':
      return '操作'
    case 'logs':
      return '日志'
  }
}

function isAcceptedRewriteStatus(status?: string | null): boolean {
  return status === 'completed' || status === 'accepted' || status === 'accepted_edited'
}

function hasAnchorConflict(segment?: { error_code?: string | null; anchor_verified?: boolean | null; status?: string | null } | null): boolean {
  if (!segment) return false
  if (segment.status === 'pending') return false
  return segment.error_code === 'ANCHOR_MISMATCH' || segment.anchor_verified === false
}

function paragraphText(content?: string | null): string[] {
  return content
    ? content.split(/\n\s*\n+/).map((part) => part.trim()).filter(Boolean)
    : []
}

function rewriteTextForSegment(segment: {
  status?: string | null
  rewritten_text?: string | null
  manual_edited_text?: string | null
}): string | null {
  const preferred = segment.manual_edited_text?.trim() || segment.rewritten_text?.trim()
  return preferred ? preferred : null
}

function shortSegmentId(segmentId?: string | null): string {
  if (!segmentId) return 'unknown'
  return segmentId.length <= 12 ? segmentId : `${segmentId.slice(0, 8)}...${segmentId.slice(-4)}`
}

function formatOffsetRange(range?: [number, number] | null): string {
  if (!range || range.length !== 2) return '—'
  return `[${range[0]}, ${range[1]})`
}

function formatSentenceRange(range?: [number, number] | null): string {
  if (!range || range.length !== 2) return '—'
  return `S${range[0]} - S${range[1]}`
}

function parseJsonValue(raw?: string | null): unknown {
  if (!raw) return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return raw
  }
}

function formatAttemptAction(action?: string | null): string {
  if (!action) return 'accepted'
  if (action === 'rollback_original') return 'rollback_original'
  if (action === 'retry') return 'retry'
  return action
}

function guardrailLevelClass(level?: string | null): string {
  if (level === 'hard_fail') return 'bg-error/10 text-error'
  if (level === 'warning') return 'bg-warning/10 text-warning'
  return 'bg-subtle text-secondary'
}

function autoSplitPartsForSegment(segment: { validation_details?: Record<string, unknown> | null }): number {
  const details = segment.validation_details
  if (!details || typeof details !== 'object') return 1
  const autoSplit = (details as Record<string, unknown>).auto_split
  if (!autoSplit || typeof autoSplit !== 'object') return 1
  const partsTotal = Number((autoSplit as Record<string, unknown>).parts_total)
  if (!Number.isFinite(partsTotal) || partsTotal <= 1) return 1
  return Math.floor(partsTotal)
}

function normalizeDiffRawText(text: string): string {
  if (!text) return ''
  return text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/[ \t]+$/g, ''))
    .join('\n')
    .trimEnd()
}

function isLikelyCanonicalHeading(line: string): boolean {
  return CANONICAL_DIFF_HEADING_RE.test(line)
}

function isHeadingParagraph(text: string): boolean {
  const normalized = text.trim().replace(/\u3000/g, ' ')
  if (!normalized) return false
  if (normalized.length > 40) return false
  if (/[。！？!?；;，,:：]/.test(normalized)) return false
  return isLikelyCanonicalHeading(normalized)
}

function canonicalizeDiffText(text: string): string {
  const normalized = normalizeDiffRawText(text)
  if (!normalized) return ''

  const result: string[] = []
  let previousBlank = true

  normalized.split('\n').forEach((rawLine) => {
    const trimmed = rawLine
      .replace(/\u3000/g, ' ')
      .replace(/[ ]{2,}/g, ' ')
      .trim()

    if (!trimmed) {
      if (!previousBlank && result.length > 0) {
        result.push('')
      }
      previousBlank = true
      return
    }

    if (isLikelyCanonicalHeading(trimmed)) {
      if (result.length > 0 && result[result.length - 1] !== '') {
        result.push('')
      }
      result.push(trimmed)
      result.push('')
      previousBlank = true
      return
    }

    result.push(trimmed)
    previousBlank = false
  })

  while (result[0] === '') result.shift()
  while (result[result.length - 1] === '') result.pop()

  return result.join('\n')
}

function splitLineIntoSentences(line: string): string[] {
  const compact = line.replace(/\s+/g, ' ').trim()
  if (!compact) return []

  const result: string[] = []
  const chars = Array.from(compact)
  let buffer = ''

  const isSentenceEnding = (char: string, next: string): boolean => {
    if (char === '。' || char === '！' || char === '？' || char === '!' || char === '?' || char === '；' || char === ';' || char === '…') {
      return true
    }
    if (char !== '.') return false
    if (next === '.') return false
    return next === '' || /\s/.test(next) || SENTENCE_CLOSER_RE.test(next)
  }

  for (let index = 0; index < chars.length; index += 1) {
    const current = chars[index]
    const next = chars[index + 1] ?? ''
    buffer += current

    if (!isSentenceEnding(current, next)) continue

    while (index + 1 < chars.length && SENTENCE_CLOSER_RE.test(chars[index + 1])) {
      buffer += chars[index + 1]
      index += 1
    }

    const sentence = buffer.trim()
    if (sentence) result.push(sentence)
    buffer = ''
  }

  const trailing = buffer.trim()
  if (trailing) result.push(trailing)
  return result
}

function sentenceAlignedDiffText(text: string): string {
  const canonical = canonicalizeDiffText(text)
  if (!canonical) return ''

  const units: string[] = []
  canonical.split('\n').forEach((line) => {
    const trimmed = line.trim()
    if (!trimmed) return

    if (isLikelyCanonicalHeading(trimmed)) {
      units.push(trimmed)
      return
    }

    const sentences = splitLineIntoSentences(trimmed)
    if (sentences.length === 0) {
      units.push(trimmed)
      return
    }
    units.push(...sentences)
  })

  return units.join('\n')
}

function stripAllWhitespace(text: string): string {
  if (!text) return ''
  return text.replace(/[\s\u3000]+/g, '')
}

function countLineBreaks(text: string): number {
  if (!text) return 0
  return (text.match(/\n/g) ?? []).length
}

function _trimmedSubrange(text: string, start: number, end: number): [number, number] | null {
  const chunk = text.slice(start, end)
  if (!chunk) return null
  const left = chunk.length - chunk.replace(/^\s+/, '').length
  const right = chunk.replace(/\s+$/, '').length
  if (right <= left) return null
  return [start + left, start + right]
}

function paragraphRangesFromContent(content: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = []
  let cursor = 0
  const regex = new RegExp(PARAGRAPH_SPLIT_RE.source, PARAGRAPH_SPLIT_RE.flags)
  let match: RegExpExecArray | null = regex.exec(content)
  while (match) {
    const normalized = _trimmedSubrange(content, cursor, match.index)
    if (normalized) ranges.push(normalized)
    cursor = match.index + match[0].length
    match = regex.exec(content)
  }
  const tail = _trimmedSubrange(content, cursor, content.length)
  if (tail) ranges.push(tail)
  return ranges
}

function resolveSegmentCharRange(
  segment: {
    paragraph_range: [number, number]
    char_offset_range?: [number, number] | null
    rewrite_windows?: Array<{ start_offset?: number; end_offset?: number }> | null
  },
  options: {
    chapterLength: number
    paragraphRanges: Array<[number, number]>
  },
): [number, number] | null {
  const { chapterLength, paragraphRanges } = options
  const windows = Array.isArray(segment.rewrite_windows) ? segment.rewrite_windows : []
  if (windows.length > 0) {
    const sorted = [...windows]
      .map((item) => [Number(item.start_offset), Number(item.end_offset)] as [number, number])
      .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start)
      .sort((a, b) => a[0] - b[0])
    if (sorted.length > 0) {
      const start = sorted[0][0]
      const end = sorted[sorted.length - 1][1]
      if (end <= chapterLength) return [start, end]
    }
  }

  const range = segment.char_offset_range
  if (range && range.length === 2) {
    const start = Number(range[0])
    const end = Number(range[1])
    if (Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && end <= chapterLength) {
      return [start, end]
    }
  }

  const [startParagraph, endParagraph] = segment.paragraph_range
  if (
    startParagraph < 1
    || endParagraph < startParagraph
    || endParagraph > paragraphRanges.length
  ) {
    return null
  }
  const start = paragraphRanges[startParagraph - 1][0]
  const end = paragraphRanges[endParagraph - 1][1]
  return end > start ? [start, end] : null
}

export function buildChapterPreview(chapterContent: string, paragraphs: string[], rewrites: Array<{
  paragraph_range: [number, number]
  char_offset_range?: [number, number] | null
  rewrite_windows?: Array<{ start_offset?: number; end_offset?: number }> | null
  status?: string | null
  rewritten_text?: string | null
  manual_edited_text?: string | null
}>): string {
  if (!chapterContent) return ''
  const paragraphRanges = paragraphRangesFromContent(chapterContent)
  const candidates: Array<{ start: number; end: number; rewritten: string }> = []

  rewrites.forEach((segment) => {
    const rewritten = rewriteTextForSegment(segment)
    if (!rewritten || segment.status === 'rejected') return

    const [startParagraph, endParagraph] = segment.paragraph_range
    const originalRangeText = paragraphs.slice(startParagraph - 1, endParagraph).join('\n\n')
    const isHeadingOnlySegment = startParagraph === endParagraph && isHeadingParagraph(paragraphs[startParagraph - 1] ?? '')
    const rewriteLooksLikeHeadingExpansion = isHeadingOnlySegment
      && Boolean(rewritten)
      && (
        (rewritten?.includes('\n') ?? false)
        || (rewritten?.trim().length ?? 0) > Math.max(24, originalRangeText.trim().length * 4)
      )
    if (rewriteLooksLikeHeadingExpansion) return

    const charRange = resolveSegmentCharRange(
      segment,
      {
        chapterLength: chapterContent.length,
        paragraphRanges,
      }
    )
    if (!charRange) return

    candidates.push({
      start: charRange[0],
      end: charRange[1],
      rewritten,
    })
  })

  if (candidates.length === 0) return chapterContent
  candidates.sort((a, b) => (a.start - b.start) || (a.end - b.end))

  const assembledParts: string[] = []
  let cursor = 0
  candidates.forEach((candidate) => {
    if (candidate.start < cursor) {
      return
    }
    if (cursor < candidate.start) {
      assembledParts.push(chapterContent.slice(cursor, candidate.start))
    }
    assembledParts.push(candidate.rewritten)
    cursor = candidate.end
  })
  if (cursor < chapterContent.length) {
    assembledParts.push(chapterContent.slice(cursor))
  }
  return assembledParts.join('')
}

export function stageStatusForChapter(
  chapter: ChapterListItem,
  stage: StageName,
  runtimeOverrides?: ChapterStageRuntimeOverrides,
): StageStatus {
  if (stage === 'import') return 'completed'
  const runtimeStageMap = runtimeOverrides?.[stage]
  if (runtimeStageMap && chapter.index in runtimeStageMap) {
    return runtimeStageMap[chapter.index]
  }
  const persisted = chapter.stages?.[stage]
  if (persisted) {
    // Backend chapter status is source-of-truth.
    // We only allow direct runtime override when the user explicitly triggers an action.
    return persisted
  }
  return 'pending'
}

function chapterRiskLabels(
  chapter: ChapterListItem,
  stage: StageName,
  isSelected: boolean,
  selectedHasAnchorMismatch: boolean,
  runtimeOverrides?: ChapterStageRuntimeOverrides,
): string[] {
  const labels: string[] = []
  const currentStatus = stageStatusForChapter(chapter, stage, runtimeOverrides)
  if (currentStatus === 'failed') labels.push('FAILED')
  const hasFailedStage = RISK_STAGE_NAMES.some((item) => stageStatusForChapter(
    chapter,
    item,
    runtimeOverrides
  ) === 'failed')
  if (!labels.includes('FAILED') && hasFailedStage) labels.push('风险')
  const shouldShowAnchorMismatch = stage === 'rewrite' || stage === 'assemble'
  if (shouldShowAnchorMismatch && isSelected && selectedHasAnchorMismatch) labels.unshift('ANCHOR_MISMATCH')
  return labels.slice(0, 3)
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tagName = target.tagName.toLowerCase()
  return target.isContentEditable || tagName === 'input' || tagName === 'textarea' || tagName === 'select'
}

function ActionButton({
  label,
  onClick,
  disabled,
  loading,
  tone = 'primary',
  icon,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  loading?: boolean
  tone?: 'primary' | 'secondary' | 'warning' | 'danger'
  icon?: ReactNode
}) {
  const toneClass = tone === 'secondary'
    ? 'button-secondary'
    : tone === 'warning'
      ? 'bg-warning/10 text-warning hover:bg-warning/20'
      : tone === 'danger'
        ? 'bg-error/10 text-error hover:bg-error/15'
        : 'button-primary'

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || loading}
      className={`${toneClass} inline-flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50`}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.5} /> : icon}
      {label}
    </button>
  )
}

void StageNode
void StageDetailCard
void ChapterStatusBadge
void RewriteCoveragePanel
void QualityGatePanel

export function NovelDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [selectedStage, setSelectedStage] = useState<StageName>('split')
  const [selectedChapterIndex, setSelectedChapterIndex] = useState<number | null>(null)
  const [leftFilter, setLeftFilter] = useState<LeftStageFilter>('all')
  const [chapterSearch, setChapterSearch] = useState('')
  const [rightTab, setRightTab] = useState<WorkbenchRightTab>('insights')
  const [viewMode, setViewMode] = useState<WorkbenchViewMode>('rewrite')
  const [diffTextMode, setDiffTextMode] = useState<DiffTextMode>('raw')
  const [latestRiskSignature, setLatestRiskSignature] = useState<string | null>(null)
  const [stageActionBusy, setStageActionBusy] = useState<string | null>(null)
  const [chapterActionBusy, setChapterActionBusy] = useState<string | null>(null)
  const [chapterActionFeedback, setChapterActionFeedback] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null)
  const [chapterRewriteDrafts, setChapterRewriteDrafts] = useState<Record<number, string>>({})
  const [savedChapterRewriteDrafts, setSavedChapterRewriteDrafts] = useState<ChapterRewriteDraftSnapshot>({})
  const [promptLogScope, setPromptLogScope] = useState<'current' | 'all'>('current')
  const [rewriteTargetAddedCharsInput, setRewriteTargetAddedCharsInput] = useState('')
  const [rewriteProviderId, setRewriteProviderId] = useState('')

  const [wsStageProgress, setWsStageProgress] = useState<Record<string, { chapters_done: number; chapters_total: number }>>({})

  useEffect(() => {
    setWsStageProgress({})
  }, [id])

  const { data: novel, isLoading: novelLoading } = useQuery({
    queryKey: ['novel', id],
    queryFn: () => getNovel(id!),
    enabled: !!id,
    refetchInterval: 15_000,
  })

  const { data: chaptersData, isLoading: chaptersLoading } = useQuery({
    queryKey: ['chapters', id],
    queryFn: () => getNovelChapters(id!),
    enabled: !!id,
    refetchInterval: 15_000,
  })
  const { data: providersData = [], isLoading: providersLoading } = useQuery({
    queryKey: ['providers'],
    queryFn: providersApi.list,
    staleTime: 30_000,
  })
  const providers = providersData as Provider[]

  const rewriteTargetStorageKey = useMemo(
    () => (id ? `ai-novel:rewrite-target-added-chars:${id}` : null),
    [id]
  )
  const rewriteProviderStorageKey = useMemo(
    () => (id ? `ai-novel:rewrite-provider-id:${id}` : null),
    [id]
  )
  const rewriteDraftStorageKey = useMemo(
    () => (id ? `ai-novel:rewrite-chapter-drafts:${id}` : null),
    [id]
  )

  const persistRewriteDraftSnapshot = useCallback((snapshot: ChapterRewriteDraftSnapshot) => {
    if (!rewriteDraftStorageKey) return
    if (typeof window === 'undefined') return

    const entries = Object.entries(snapshot).filter(([, item]) => Boolean(item?.text))
    if (entries.length === 0) {
      window.localStorage.removeItem(rewriteDraftStorageKey)
      return
    }

    window.localStorage.setItem(rewriteDraftStorageKey, JSON.stringify(Object.fromEntries(entries)))
  }, [rewriteDraftStorageKey])

  useEffect(() => {
    if (!rewriteTargetStorageKey) {
      setRewriteTargetAddedCharsInput('')
      return
    }
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem(rewriteTargetStorageKey) ?? ''
    setRewriteTargetAddedCharsInput(stored)
  }, [rewriteTargetStorageKey])

  useEffect(() => {
    if (!rewriteTargetStorageKey) return
    if (typeof window === 'undefined') return
    const trimmed = rewriteTargetAddedCharsInput.trim()
    if (!trimmed) {
      window.localStorage.removeItem(rewriteTargetStorageKey)
      return
    }
    window.localStorage.setItem(rewriteTargetStorageKey, trimmed)
  }, [rewriteTargetStorageKey, rewriteTargetAddedCharsInput])

  useEffect(() => {
    if (!rewriteProviderStorageKey) {
      setRewriteProviderId('')
      return
    }
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem(rewriteProviderStorageKey) ?? ''
    setRewriteProviderId(stored)
  }, [rewriteProviderStorageKey])

  useEffect(() => {
    if (!rewriteProviderStorageKey) return
    if (typeof window === 'undefined') return
    const trimmed = rewriteProviderId.trim()
    if (!trimmed) {
      window.localStorage.removeItem(rewriteProviderStorageKey)
      return
    }
    window.localStorage.setItem(rewriteProviderStorageKey, trimmed)
  }, [rewriteProviderStorageKey, rewriteProviderId])

  useEffect(() => {
    if (providersLoading) return
    if (providers.length === 1 && !rewriteProviderId) {
      setRewriteProviderId(providers[0].id)
      return
    }
    if (rewriteProviderId && !providers.some((provider) => provider.id === rewriteProviderId)) {
      setRewriteProviderId('')
    }
  }, [providers, providersLoading, rewriteProviderId])

  useEffect(() => {
    if (!rewriteDraftStorageKey) {
      setChapterRewriteDrafts({})
      setSavedChapterRewriteDrafts({})
      return
    }
    if (typeof window === 'undefined') return

    const raw = window.localStorage.getItem(rewriteDraftStorageKey)
    if (!raw) {
      setChapterRewriteDrafts({})
      setSavedChapterRewriteDrafts({})
      return
    }

    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>
      const restoredDrafts: Record<number, string> = {}
      const restoredSnapshot: ChapterRewriteDraftSnapshot = {}

      Object.entries(parsed).forEach(([chapterKey, value]) => {
        const chapterIndex = Number(chapterKey)
        if (!Number.isInteger(chapterIndex) || chapterIndex <= 0) return
        if (!value || typeof value !== 'object') return

        const text = (value as { text?: unknown }).text
        if (typeof text !== 'string') return

        const savedAt = (value as { saved_at?: unknown }).saved_at
        const normalizedSavedAt = typeof savedAt === 'string' && savedAt ? savedAt : new Date().toISOString()

        restoredDrafts[chapterIndex] = text
        restoredSnapshot[chapterIndex] = {
          text,
          saved_at: normalizedSavedAt,
        }
      })

      setChapterRewriteDrafts(restoredDrafts)
      setSavedChapterRewriteDrafts(restoredSnapshot)
    } catch {
      setChapterRewriteDrafts({})
      setSavedChapterRewriteDrafts({})
      window.localStorage.removeItem(rewriteDraftStorageKey)
    }
  }, [rewriteDraftStorageKey])

  useEffect(() => {
    persistRewriteDraftSnapshot(savedChapterRewriteDrafts)
  }, [persistRewriteDraftSnapshot, savedChapterRewriteDrafts])

  useEffect(() => {
    if (!id) return
    wsManager.connect()
    wsManager.subscribe(id)

    const unsubscribe = wsManager.onMessage((msg: WSMessage) => {
      if ('novel_id' in msg && msg.novel_id !== id) return

      if (msg.type === 'stage_progress') {
        setWsStageProgress((prev) => ({
          ...prev,
          [msg.stage]: { chapters_done: msg.chapters_done, chapters_total: msg.chapters_total },
        }))
        queryClient.invalidateQueries({ queryKey: ['chapters', id] })
        queryClient.invalidateQueries({ queryKey: ['novel', id] })
        if (selectedChapterIndex !== null) {
          if (msg.stage === 'rewrite') {
            queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', id, selectedChapterIndex] })
          }
          if (msg.stage === 'analyze' || msg.stage === 'mark') {
            queryClient.invalidateQueries({ queryKey: ['chapter-analysis', id, selectedChapterIndex] })
          }
        }
      }

      if (msg.type === 'task_paused') {
        const pausedStage = (msg as { stage?: string }).stage
        setWsStageProgress((prev) => {
          if (!pausedStage) return prev
          if (!(pausedStage in prev)) return prev
          const next = { ...prev }
          delete next[pausedStage]
          return next
        })
      }

      if (msg.type === 'stage_completed' || msg.type === 'stage_failed' || msg.type === 'stage_stale') {
        setWsStageProgress((prev) => {
          if (!(msg.stage in prev)) return prev
          const next = { ...prev }
          delete next[msg.stage]
          return next
        })
        queryClient.invalidateQueries({ queryKey: ['novel', id] })
        queryClient.invalidateQueries({ queryKey: ['chapters', id] })
        queryClient.invalidateQueries({ queryKey: ['chapter', id] })
        queryClient.invalidateQueries({ queryKey: ['chapter-analysis', id] })
        queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', id] })
        queryClient.invalidateQueries({ queryKey: ['chapter-prompt-logs', id] })
        if (msg.type === 'stage_completed' && msg.stage === 'assemble') {
          queryClient.invalidateQueries({ queryKey: ['quality-report', id] })
        }
      }
    })

    return () => {
      unsubscribe()
      wsManager.unsubscribe(id)
    }
  }, [id, queryClient, selectedChapterIndex])

  const pipelineStatus = useMemo(() => {
    const base = STAGE_NAMES.reduce((acc, stage) => {
      const snapshot = (novel?.pipeline_status?.[stage] ?? {
        status: 'pending' as StageStatus,
        run_seq: undefined,
        chapters_total: 0,
        chapters_done: 0,
      }) as StageRunInfo
      const ws = wsStageProgress[stage]

      const chaptersTotal = ws?.chapters_total
        ?? snapshot.chapters_total
      const chaptersDone = ws?.chapters_done
        ?? snapshot.chapters_done

      acc[stage] = {
        status: snapshot.status,
        run_seq: snapshot.run_seq,
        started_at: snapshot.started_at,
        completed_at: snapshot.completed_at,
        error_message: snapshot.error_message,
        warnings_count: snapshot.warnings_count,
        artifact_path: snapshot.artifact_path,
        config_snapshot: snapshot.config_snapshot,
        chapters_total: chaptersTotal,
        chapters_done: chaptersDone,
      }
      return acc
    }, {} as Record<StageName, StageRunInfo>)
    base.analyze = mergeAnalyzeAndMarkStatus(base.analyze, base.mark)
    return base
  }, [novel, wsStageProgress])

  const chapterItems = useMemo(
    () => (Array.isArray(chaptersData) ? (chaptersData as ChapterListItem[]) : EMPTY_CHAPTER_ITEMS),
    [chaptersData]
  )

  useEffect(() => {
    if (!novel) return
    const preferredStage = CORE_STAGE_NAMES.find((stage) => {
      const status = pipelineStatus[stage].status
      return status === 'running' || status === 'failed'
    })
      ?? CORE_STAGE_NAMES.find((stage) => pipelineStatus[stage].status !== 'completed')
      ?? 'assemble'
    setSelectedStage((current) => (VISIBLE_STAGE_NAMES.includes(current) ? current : preferredStage))
  }, [novel, pipelineStatus])

  useEffect(() => {
    if (chapterItems.length === 0) {
      setSelectedChapterIndex(null)
      return
    }
    setSelectedChapterIndex((current) => {
      if (current !== null && chapterItems.some((chapter) => chapter.index === current)) return current
      return chapterItems[0].index
    })
  }, [chapterItems])

  const chapterStageRuntimeOverrides = useMemo<ChapterStageRuntimeOverrides>(() => ({}), [])

  const filteredChapters = useMemo(() => {
    const query = chapterSearch.trim().toLowerCase()
    return chapterItems.filter((chapter) => {
      const matchesSearch = !query
        || chapter.title?.toLowerCase().includes(query)
        || String(chapter.index).includes(query)
      const stageStatus = stageStatusForChapter(
        chapter,
        selectedStage,
        chapterStageRuntimeOverrides
      )
      const matchesFilter = leftFilter === 'all'
        ? true
        : leftFilter === 'attention'
          ? stageStatus === 'failed' || chapter.status === 'failed'
          : stageStatus === leftFilter
      return matchesSearch && matchesFilter
    })
  }, [chapterItems, chapterSearch, leftFilter, selectedStage, chapterStageRuntimeOverrides, pipelineStatus])

  useEffect(() => {
    if (filteredChapters.length === 0) return
    if (selectedChapterIndex !== null && filteredChapters.some((chapter) => chapter.index === selectedChapterIndex)) return
    setSelectedChapterIndex(filteredChapters[0].index)
  }, [filteredChapters, selectedChapterIndex])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
      if (isTypingTarget(event.target)) return
      if (filteredChapters.length === 0) return
      event.preventDefault()
      const currentIndex = filteredChapters.findIndex((chapter) => chapter.index === selectedChapterIndex)
      const safeIndex = currentIndex >= 0 ? currentIndex : 0
      const delta = event.key === 'ArrowDown' ? 1 : -1
      const nextIndex = Math.min(Math.max(safeIndex + delta, 0), filteredChapters.length - 1)
      setSelectedChapterIndex(filteredChapters[nextIndex].index)
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [filteredChapters, selectedChapterIndex])

  const selectedChapter = useMemo(
    () => chapterItems.find((chapter) => chapter.index === selectedChapterIndex) ?? null,
    [chapterItems, selectedChapterIndex]
  )

  const { data: chapterDetail, isLoading: chapterDetailLoading } = useQuery({
    queryKey: ['chapter', id, selectedChapterIndex],
    queryFn: () => chaptersApi.get(id!, selectedChapterIndex!),
    enabled: !!id && selectedChapterIndex !== null && selectedStage !== 'split',
    staleTime: 10_000,
  })

  const { data: chapterAnalysis, isLoading: chapterAnalysisLoading } = useQuery({
    queryKey: ['chapter-analysis', id, selectedChapterIndex],
    queryFn: () => chaptersApi.getAnalysis(id!, selectedChapterIndex!),
    enabled: !!id && selectedChapterIndex !== null && selectedStage !== 'split',
    staleTime: 10_000,
  })

  const { data: rewriteResultsData, isLoading: rewriteLoading } = useQuery<RewriteDisplaySegment[]>({
    queryKey: ['chapter-rewrites', id, selectedChapterIndex],
    queryFn: async () => (await chaptersApi.getRewrites(id!, selectedChapterIndex!)) as RewriteDisplaySegment[],
    enabled: !!id && selectedChapterIndex !== null && selectedStage !== 'split',
    staleTime: 10_000,
    refetchInterval: selectedStage === 'rewrite' && pipelineStatus.rewrite.status === 'running' ? 3_000 : false,
  })
  const rewriteResults = rewriteResultsData ?? EMPTY_REWRITE_SEGMENTS

  const stageInfo = pipelineStatus[selectedStage]
  const stageRunDetailQuery = useQuery({
    queryKey: ['stage-run-detail', id, selectedStage, stageInfo?.run_seq],
    queryFn: () => fetchStageRunDetail(id!, selectedStage, stageInfo.run_seq ?? 0),
    enabled: !!id && Boolean(stageInfo?.run_seq),
    staleTime: 15_000,
  })

  const stageLogQuery = useQuery({
    queryKey: ['stage-log', id, selectedStage, stageInfo?.run_seq],
    queryFn: () => fetchStageArtifact(id!, selectedStage, stageInfo.run_seq),
    enabled: !!id && rightTab === 'logs' && stageInfo.status !== 'pending',
    staleTime: 5_000,
    refetchInterval: rightTab === 'logs' && stageInfo.status === 'running' ? 5_000 : false,
  })

  const promptLogsQuery = useQuery({
    queryKey: ['chapter-prompt-logs', id, selectedChapterIndex],
    queryFn: () => promptLogsApi.list(id!, selectedChapterIndex!),
    enabled: !!id && rightTab === 'logs' && selectedStage === 'analyze' && selectedChapterIndex !== null,
    staleTime: 5_000,
    refetchInterval: rightTab === 'logs' && selectedStage === 'analyze' && stageInfo.status === 'running' ? 5_000 : false,
  })
  const promptLogEntries = useMemo(
    () => [...(promptLogsQuery.data?.data ?? [])].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    ),
    [promptLogsQuery.data]
  )
  const promptLogRunWindow = useMemo(() => {
    const startedAtMs = stageInfo.started_at ? new Date(stageInfo.started_at).getTime() : NaN
    const completedAtMs = stageInfo.completed_at ? new Date(stageInfo.completed_at).getTime() : NaN
    const hasWindow = selectedStage === 'analyze' && Number.isFinite(startedAtMs)
    if (!hasWindow) {
      return {
        hasWindow: false,
        filtered: false,
        entries: promptLogEntries,
      }
    }
    const endMs = Number.isFinite(completedAtMs) ? completedAtMs + 1000 : Number.POSITIVE_INFINITY
    const entries = promptLogEntries.filter((entry) => {
      const ts = new Date(entry.timestamp).getTime()
      return Number.isFinite(ts) && ts >= startedAtMs - 1000 && ts <= endMs
    })
    if (entries.length === 0) {
      return {
        hasWindow: true,
        filtered: false,
        entries: promptLogEntries,
      }
    }
    return {
      hasWindow: true,
      filtered: entries.length < promptLogEntries.length,
      entries,
    }
  }, [promptLogEntries, selectedStage, stageInfo.started_at, stageInfo.completed_at])
  const visiblePromptLogEntries = promptLogScope === 'all' ? promptLogEntries : promptLogRunWindow.entries
  const showPromptScopeSwitch = promptLogEntries.length > 0 && (promptLogRunWindow.hasWindow || promptLogRunWindow.filtered)

  const qualityReportQuery = useQuery({
    queryKey: ['quality-report', id, novel?.task_id ?? novel?.active_task_id ?? null],
    queryFn: () => fetchQualityReport(id!, novel?.task_id ?? novel?.active_task_id ?? null),
    enabled: !!id && selectedStage === 'assemble' && Boolean(novel?.task_id ?? novel?.active_task_id),
    staleTime: 30_000,
  })

  const qualityReport = useMemo(() => normalizeQualityReport(qualityReportQuery.data), [qualityReportQuery.data])
  const paragraphs = useMemo(() => paragraphText(chapterDetail?.content), [chapterDetail?.content])
  const rewriteCandidateScenes = useMemo(
    () => (chapterAnalysis?.scenes ?? []).filter((scene) => scene.rewrite_potential?.expandable || scene.rewrite_potential?.rewritable).length,
    [chapterAnalysis]
  )
  const rewriteCoverageStats = useMemo(() => {
    const stats = {
      total: rewriteResults.length,
      successful: 0,
      failed: 0,
      pending: 0,
      rejected: 0,
      acceptedEdited: 0,
    }

    rewriteResults.forEach((segment) => {
      const text = rewriteTextForSegment(segment)
      if (segment.status === 'accepted_edited') {
        stats.acceptedEdited += 1
        if (Boolean(text)) {
          stats.successful += 1
        }
      } else if (isAcceptedRewriteStatus(segment.status) && Boolean(text)) {
        stats.successful += 1
      } else if (segment.status === 'failed') {
        stats.failed += 1
      } else if (segment.status === 'rejected') {
        stats.rejected += 1
      } else {
        stats.pending += 1
      }
    })

    return stats
  }, [rewriteResults])
  const rewriteFailureSegments = useMemo(
    () => rewriteResults.filter((segment) => segment.status === 'failed' || Boolean(segment.error_code) || Boolean(segment.error_detail)),
    [rewriteResults]
  )
  const rewriteAutoSplitStats = useMemo(() => {
    let segments = 0
    let parts = 0
    rewriteResults.forEach((segment) => {
      const partCount = autoSplitPartsForSegment(segment)
      if (partCount <= 1) return
      segments += 1
      parts += partCount
    })
    return { segments, parts }
  }, [rewriteResults])
  const hasAnyRewrittenText = rewriteResults.some((segment) => Boolean(rewriteTextForSegment(segment)))
  const hasRewritePlan = rewriteResults.length > 0
  const originalText = useMemo(() => chapterDetail?.content ?? '', [chapterDetail?.content])
  const rewriteDraftBaseText = useMemo(
    () => buildChapterPreview(originalText, paragraphs, rewriteResults),
    [originalText, paragraphs, rewriteResults]
  )
  const selectedChapterKey = selectedChapterIndex ?? -1
  const hasLocalChapterDraft = Object.prototype.hasOwnProperty.call(chapterRewriteDrafts, selectedChapterKey)
  const selectedSavedDraftSnapshot = selectedChapterIndex === null ? undefined : savedChapterRewriteDrafts[selectedChapterIndex]
  const currentChapterDraftText = hasLocalChapterDraft
    ? chapterRewriteDrafts[selectedChapterKey]
    : rewriteDraftBaseText
  const rewritePreviewText = currentChapterDraftText ?? ''
  const hasUnsavedLocalChapterDraft = hasLocalChapterDraft
    && currentChapterDraftText !== (selectedSavedDraftSnapshot?.text ?? '')
  const savedDraftTimeLabel = selectedSavedDraftSnapshot?.saved_at
    ? new Date(selectedSavedDraftSnapshot.saved_at).toLocaleTimeString('zh-CN', { hour12: false })
    : null
  const diffRawOldText = useMemo(() => normalizeDiffRawText(originalText), [originalText])
  const diffRawNewText = useMemo(() => normalizeDiffRawText(rewritePreviewText), [rewritePreviewText])
  const diffCanonicalOldText = useMemo(() => canonicalizeDiffText(originalText), [originalText])
  const diffCanonicalNewText = useMemo(() => canonicalizeDiffText(rewritePreviewText), [rewritePreviewText])
  const diffSentenceOldText = useMemo(() => sentenceAlignedDiffText(originalText), [originalText])
  const diffSentenceNewText = useMemo(() => sentenceAlignedDiffText(rewritePreviewText), [rewritePreviewText])
  const diffOldText = diffTextMode === 'raw'
    ? diffRawOldText
    : diffTextMode === 'canonical'
      ? diffCanonicalOldText
      : diffSentenceOldText
  const diffNewText = diffTextMode === 'raw'
    ? diffRawNewText
    : diffTextMode === 'canonical'
      ? diffCanonicalNewText
      : diffSentenceNewText
  const diffHasChanges = diffOldText !== diffNewText
  const rawDiffHasChanges = diffRawOldText !== diffRawNewText
  const formatOnlyDiff = rawDiffHasChanges && stripAllWhitespace(diffRawOldText) === stripAllWhitespace(diffRawNewText)
  const rawLineBreakDelta = countLineBreaks(diffRawNewText) - countLineBreaks(diffRawOldText)
  const rawLineBreakDeltaLabel = rawLineBreakDelta > 0 ? `+${rawLineBreakDelta}` : String(rawLineBreakDelta)
  const currentStageIndex = CORE_STAGE_NAMES.indexOf(selectedStage)
  const previousStage = currentStageIndex > 0 ? CORE_STAGE_NAMES[currentStageIndex - 1] : null
  const previousStageCompleted = !previousStage || pipelineStatus[previousStage].status === 'completed'
  const availableRightTabs = useMemo<WorkbenchRightTab[]>(
    () => (selectedStage === 'analyze' ? ['operations', 'logs'] : ['insights', 'operations', 'logs']),
    [selectedStage]
  )

  useEffect(() => {
    if (selectedStage === 'analyze') {
      setRightTab('operations')
      return
    }
    setRightTab('insights')
  }, [selectedStage, selectedChapterIndex])

  useEffect(() => {
    if (availableRightTabs.includes(rightTab)) return
    setRightTab(availableRightTabs[0])
  }, [availableRightTabs, rightTab])

  useEffect(() => {
    setPromptLogScope('current')
  }, [selectedStage, selectedChapterIndex])
  useEffect(() => {
    setChapterActionFeedback(null)
  }, [selectedStage, selectedChapterIndex])

  const chapterHasAnchorMismatch = useMemo(
    () => rewriteResults.some((segment) => hasAnchorConflict(segment)),
    [rewriteResults]
  )
  const selectedChapterStageStatus = selectedChapter
    ? stageStatusForChapter(
      selectedChapter,
      selectedStage,
      chapterStageRuntimeOverrides
    )
    : stageInfo.status
  const selectedChapterPreviousStageStatus = selectedChapter && previousStage
    ? stageStatusForChapter(
      selectedChapter,
      previousStage,
      chapterStageRuntimeOverrides
    )
    : null
  const selectedChapterPreviousStageCompleted = !previousStage || selectedChapterPreviousStageStatus === 'completed'
  const chapterActionStageSupported = selectedStage === 'analyze' || selectedStage === 'rewrite'
  const chapterActionMode: 'run' | 'retry' = selectedChapterStageStatus === 'pending' ? 'run' : 'retry'
  const chapterActionLabel = chapterActionMode === 'run' ? '执行当前章节' : '重跑当前章节'
  const showAnchorMismatchByStage = selectedStage === 'rewrite' || selectedStage === 'assemble'
  const selectedChapterRiskLabels = selectedChapter
    ? chapterRiskLabels(
      selectedChapter,
      selectedStage,
      true,
      showAnchorMismatchByStage && chapterHasAnchorMismatch,
      chapterStageRuntimeOverrides
    )
    : []
  const rewriteChapterHasNoMarkedSegments = selectedStage === 'rewrite'
    && selectedChapterPreviousStageCompleted
    && selectedChapterStageStatus === 'completed'
    && !rewriteLoading
    && rewriteResults.length === 0
  const selectedChapterStageStatusClass = rewriteChapterHasNoMarkedSegments
    ? 'bg-subtle text-secondary'
    : stageStatusClass(selectedChapterStageStatus)
  const selectedChapterStageStatusLabel = rewriteChapterHasNoMarkedSegments
    ? '无需改写'
    : stageStatusLabel(selectedChapterStageStatus)

  const viewModes: WorkbenchViewMode[] = ['rewrite', 'diff']
  const leftFilters: LeftStageFilter[] = ['all', 'pending', 'running', 'completed', 'failed', 'attention']

  const saveLocalRewriteDraft = useCallback(() => {
    if (selectedChapterIndex === null) return
    if (!Object.prototype.hasOwnProperty.call(chapterRewriteDrafts, selectedChapterIndex)) return

    const draftText = chapterRewriteDrafts[selectedChapterIndex] ?? ''
    const savedAt = new Date().toISOString()
    setSavedChapterRewriteDrafts((prev) => ({
      ...prev,
      [selectedChapterIndex]: {
        text: draftText,
        saved_at: savedAt,
      },
    }))
  }, [chapterRewriteDrafts, selectedChapterIndex])

  const clearLocalRewriteDraft = useCallback((chapterIndex: number) => {
    setChapterRewriteDrafts((prev) => {
      if (!(chapterIndex in prev)) return prev
      const next = { ...prev }
      delete next[chapterIndex]
      return next
    })
    setSavedChapterRewriteDrafts((prev) => {
      if (!(chapterIndex in prev)) return prev
      const next = { ...prev }
      delete next[chapterIndex]
      return next
    })
  }, [])

  const refreshNovelContext = () => {
    queryClient.invalidateQueries({ queryKey: ['novel', id] })
    queryClient.invalidateQueries({ queryKey: ['chapters', id] })
    queryClient.invalidateQueries({ queryKey: ['chapter', id] })
    queryClient.invalidateQueries({ queryKey: ['chapter-analysis', id] })
    queryClient.invalidateQueries({ queryKey: ['chapter-rewrites', id] })
    queryClient.invalidateQueries({ queryKey: ['chapter-prompt-logs', id] })
    if (selectedStage === 'assemble') {
      queryClient.invalidateQueries({ queryKey: ['quality-report', id, novel?.task_id ?? novel?.active_task_id ?? null] })
    }
  }

  const rewriteTargetAddedCharsSetting = useMemo(() => {
    const trimmed = rewriteTargetAddedCharsInput.trim()
    if (!trimmed) {
      return { value: null as number | null, valid: true }
    }
    const parsed = Number(trimmed)
    if (Number.isInteger(parsed) && parsed >= 0) {
      return { value: parsed, valid: true }
    }
    return { value: null as number | null, valid: false }
  }, [rewriteTargetAddedCharsInput])
  const selectedRewriteProvider = useMemo(
    () => providers.find((provider) => provider.id === rewriteProviderId) ?? null,
    [providers, rewriteProviderId]
  )
  const rewriteProviderSelectionMissing = selectedStage === 'rewrite'
    && providers.length > 1
    && !selectedRewriteProvider
  const rewriteTargetSettingInvalid = selectedStage === 'rewrite' && !rewriteTargetAddedCharsSetting.valid
  const chapterActionDisabledReason = (() => {
    if (!chapterActionStageSupported) return '当前阶段不支持按章节执行。'
    if (selectedChapterIndex === null) return '请先在左侧章节列表选择一个章节。'
    if (!selectedChapterPreviousStageCompleted && previousStage) {
      return `当前章节需先完成上一步「${STAGE_LABELS[previousStage]}」。`
    }
    if (rewriteProviderSelectionMissing && selectedStage === 'rewrite') {
      return '已配置多个 Provider，请先在改写目标设置中选择本次改写使用的 Provider。'
    }
    if (rewriteTargetSettingInvalid && selectedStage === 'rewrite') {
      return '改写目标设置无效，请输入大于等于 0 的整数，或留空使用默认值。'
    }
    return null
  })()
  const showGenericChapterActionDisabledReason = Boolean(chapterActionDisabledReason)
    && !(rewriteTargetSettingInvalid && selectedStage === 'rewrite')
    && !(!selectedChapterPreviousStageCompleted && previousStage && chapterActionStageSupported)
    && !(rewriteProviderSelectionMissing && selectedStage === 'rewrite')
  const chapterActionDisabled = chapterActionDisabledReason !== null

  const rejectableChapterRewriteSegments = useMemo(
    () => rewriteResults.filter((segment) => segment.status !== 'rejected' && segment.segment_id.trim().length > 0),
    [rewriteResults]
  )

  const buildRewriteStagePayload = () => {
    if (selectedStage !== 'rewrite') return undefined
    const payload: { rewrite_target_added_chars?: number; provider_id?: string } = {}
    if (rewriteTargetAddedCharsSetting.value !== null) {
      payload.rewrite_target_added_chars = rewriteTargetAddedCharsSetting.value
    }
    if (selectedRewriteProvider) {
      payload.provider_id = selectedRewriteProvider.id
    }
    return Object.keys(payload).length > 0 ? payload : undefined
  }

  const executeStageAction = async (action: 'run' | 'pause' | 'resume' | 'retry') => {
    if (!id) return
    if (selectedStage === 'rewrite' && (action === 'run' || action === 'retry')) {
      if (!rewriteTargetAddedCharsSetting.valid || rewriteProviderSelectionMissing) return
    }
    setStageActionBusy(`${selectedStage}:${action}`)
    try {
      if (action === 'run') {
        await stagesApi.run(
          id,
          selectedStage,
          buildRewriteStagePayload()
        )
      }
      if (action === 'pause') await stagesApi.pause(id, selectedStage)
      if (action === 'resume') await stagesApi.resume(id, selectedStage)
      if (action === 'retry') {
        await stagesApi.retry(
          id,
          selectedStage,
          buildRewriteStagePayload()
        )
      }
      refreshNovelContext()
    } catch (error) {
      console.error(error)
    } finally {
      setStageActionBusy(null)
    }
  }

  const executeChapterAction = async (action: 'run' | 'retry') => {
    if (!id) return
    if (chapterActionDisabledReason) {
      setChapterActionFeedback({ tone: 'info', text: chapterActionDisabledReason })
      return
    }
    if (selectedChapterIndex === null) return
    setChapterActionBusy(`${selectedStage}:${selectedChapterIndex}:${action}`)
    try {
      const chapterRetryPayload = (() => {
        const basePayload = selectedStage === 'rewrite' ? (buildRewriteStagePayload() ?? {}) : {}
        if (action === 'retry') {
          return { ...basePayload, force_rerun: true }
        }
        return Object.keys(basePayload).length > 0 ? basePayload : undefined
      })()
      const response = await chaptersApi.retryChapter(
        id,
        selectedStage,
        selectedChapterIndex,
        chapterRetryPayload
      )
      const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      if (selectedStage === 'rewrite') {
        const segmentsTotal = Number(response?.segments_total ?? 0)
        const failedSegments = Number(response?.failed_segments ?? 0)
        if (segmentsTotal <= 0) {
          setChapterActionFeedback({
            tone: 'info',
            text: `本章未命中可改写段落（segments_total=0，${now}）。如需改写，请先在“分析与标记”补充命中规则后再重跑。`,
          })
        } else if (failedSegments > 0) {
          setChapterActionFeedback({
            tone: 'error',
            text: `本章已重跑，命中 ${segmentsTotal} 段，其中失败 ${failedSegments} 段（${now}）。`,
          })
        } else {
          setChapterActionFeedback({
            tone: 'success',
            text: `本章已重跑并完成，共处理 ${segmentsTotal} 段（${now}）。`,
          })
        }
        clearLocalRewriteDraft(selectedChapterIndex)
      } else {
        setChapterActionFeedback({
          tone: 'success',
          text: `已完成「${action === 'run' ? '执行当前章节' : '重跑当前章节'}」（${now}）。`,
        })
      }
      refreshNovelContext()
    } catch (error) {
      console.error(error)
      const message = error instanceof ApiError
        ? `${error.code ? `${error.code}：` : ''}${error.message}`
        : '章节操作失败，请查看日志后重试。'
      setChapterActionFeedback({ tone: 'error', text: message })
    } finally {
      setChapterActionBusy(null)
    }
  }

  const executeFallbackToOriginalForChapter = async () => {
    if (!id) return
    if (selectedStage !== 'rewrite') return
    if (selectedChapterIndex === null) return

    if (rewriteResults.length === 0) {
      setChapterActionFeedback({
        tone: 'success',
        text: '当前章节未命中可改写段，已默认采用原文（组装阶段直接使用原文）。',
      })
      refreshNovelContext()
      return
    }
    if (rejectableChapterRewriteSegments.length === 0) {
      setChapterActionFeedback({
        tone: 'info',
        text: '当前章节已经全部回退为原文，无需重复操作。',
      })
      return
    }

    setChapterActionBusy(`${selectedStage}:${selectedChapterIndex}:fallback`)
    try {
      for (const segment of rejectableChapterRewriteSegments) {
        await chaptersApi.reviewRewrite(id, selectedChapterIndex, segment.segment_id, {
          action: 'reject',
          note: 'chapter_fallback_to_original',
        })
      }
      clearLocalRewriteDraft(selectedChapterIndex)
      setChapterActionFeedback({
        tone: 'success',
        text: `已将本章 ${rejectableChapterRewriteSegments.length} 段标记为不接受，组装阶段将采用本章原文。`,
      })
      refreshNovelContext()
    } catch (error) {
      console.error(error)
      const message = error instanceof ApiError
        ? `${error.code ? `${error.code}：` : ''}${error.message}`
        : '回退到原文失败，请查看日志后重试。'
      setChapterActionFeedback({ tone: 'error', text: message })
    } finally {
      setChapterActionBusy(null)
    }
  }

  const stagePrimaryAction = useMemo(() => {
    if (stageInfo.status === 'pending') {
      return {
        key: 'run',
        label: '开始',
        tone: 'primary' as const,
        disabled: !previousStageCompleted,
      }
    }
    if (stageInfo.status === 'stale') {
      return { key: 'run', label: '重跑', tone: 'primary' as const, disabled: false }
    }
    if (stageInfo.status === 'paused') {
      return { key: 'resume', label: '继续', tone: 'primary' as const, disabled: false }
    }
    if (stageInfo.status === 'running') {
      return { key: 'pause', label: '暂停', tone: 'secondary' as const, disabled: false }
    }
    if (stageInfo.status === 'failed') {
      return { key: 'retry', label: '重试', tone: 'danger' as const, disabled: false }
    }
    return {
      key: 'run',
      label: '重新执行',
      tone: 'secondary' as const,
      disabled: false,
    }
  }, [previousStageCompleted, stageInfo.status])

  const currentStageExports = STAGE_EXPORT_FORMATS[selectedStage] ?? []
  const stagePrimaryActionRequiresRewriteTarget = selectedStage === 'rewrite'
    && (stagePrimaryAction.key === 'run' || stagePrimaryAction.key === 'retry')
  const stagePrimaryActionRequiresRewriteProvider = selectedStage === 'rewrite'
    && (stagePrimaryAction.key === 'run' || stagePrimaryAction.key === 'retry')
  const stagePrimaryActionDisabled = stagePrimaryAction.disabled
    || (stagePrimaryActionRequiresRewriteTarget && rewriteTargetSettingInvalid)
    || (stagePrimaryActionRequiresRewriteProvider && rewriteProviderSelectionMissing)
  const canUseTextViews = selectedStage !== 'split' && selectedStage !== 'analyze' && selectedChapterIndex !== null
  const contentLoading = selectedStage === 'analyze'
    ? chapterAnalysisLoading
    : chapterDetailLoading || rewriteLoading

  if (novelLoading) {
    return (
      <div className="space-y-6">
        <div className="h-14 rounded-2xl bg-subtle animate-pulse" />
        <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)_360px]">
          <div className="h-[68vh] rounded-2xl bg-subtle animate-pulse" />
          <div className="h-[68vh] rounded-2xl bg-subtle animate-pulse" />
          <div className="h-[68vh] rounded-2xl bg-subtle animate-pulse" />
        </div>
      </div>
    )
  }

  if (!novel) {
    return (
      <div className="flex flex-col items-center justify-center py-24 space-y-4">
        <BookOpen className="w-16 h-16 text-tertiary" strokeWidth={1.5} />
        <p className="text-title-3 font-semibold text-secondary">小说不存在</p>
        <button className="button-secondary cursor-pointer" onClick={() => navigate('/')}>返回首页</button>
      </div>
    )
  }

  return (
    <div className="-m-4 flex h-[calc(100vh-2rem)] flex-col overflow-hidden bg-page lg:-m-8 lg:h-[calc(100vh-2rem)] dark:bg-dark-page">
      <div className="border-b border-border bg-white/95 px-4 py-4 backdrop-blur dark:border-dark-border dark:bg-dark-card/95 lg:px-8">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="min-w-0 space-y-3">
            <div className="flex items-center gap-3">
              <button
                className="rounded-xl p-2 transition-colors duration-150 hover:bg-subtle cursor-pointer"
                onClick={() => navigate(-1)}
                aria-label="返回上一页"
              >
                <ArrowLeft className="h-5 w-5 text-secondary" strokeWidth={1.5} />
              </button>
              <div className="min-w-0">
                <h1 className="truncate text-display font-bold text-primary">《{novel.title}》</h1>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-caption text-secondary">
                  <span className="rounded-full bg-subtle px-2.5 py-1">{formatChars(novel.total_chars)}</span>
                  <span className="rounded-full bg-subtle px-2.5 py-1">
                    {chapterItems.length > 0 ? chapterItems.length : ((novel as { chapter_count?: number }).chapter_count ?? 0)} 章
                  </span>
                  <span className="rounded-full bg-subtle px-2.5 py-1 uppercase">{novel.file_format}</span>
                  <span className="rounded-full bg-subtle px-2.5 py-1">导入于 {formatDate(novel.imported_at)}</span>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {VISIBLE_STAGE_NAMES.map((stage) => {
                const status = pipelineStatus[stage].status
                const warningsCount = pipelineStatus[stage].warnings_count ?? 0
                return (
                  <button
                    key={stage}
                    type="button"
                    onClick={() => setSelectedStage(stage)}
                    className={`inline-flex items-center gap-2 rounded-2xl border px-3 py-2 text-callout font-medium transition-colors cursor-pointer ${selectedStage === stage ? 'border-accent bg-accent/10 text-accent' : 'border-border bg-white text-primary hover:border-accent/30 hover:bg-subtle'}`}
                  >
                    <span className={`h-2.5 w-2.5 rounded-full ${status === 'completed' ? 'bg-success' : status === 'running' ? 'bg-accent animate-pulse' : status === 'failed' ? 'bg-error' : status === 'paused' || status === 'stale' ? 'bg-warning' : 'bg-border'}`} />
                    {STAGE_LABELS[stage]}
                    {warningsCount > 0 && <span className="rounded-full bg-warning/10 px-2 py-0.5 text-[10px] font-semibold text-warning">{warningsCount}</span>}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="flex items-center gap-3 self-start xl:self-auto">
            <ExportDropdown novelId={novel.id} onRiskSignatureChange={setLatestRiskSignature} />
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden p-4 xl:flex-row xl:gap-0 xl:p-0">
        <aside className="flex w-full flex-col overflow-hidden rounded-3xl border border-border bg-white shadow-xs xl:m-4 xl:mr-2 xl:w-[280px] xl:min-w-[260px] dark:border-dark-border dark:bg-dark-card">
          <div className="border-b border-border px-4 py-4 dark:border-dark-border">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-title-3 font-semibold text-primary">章节导航</h2>
                <p className="mt-1 text-caption text-secondary">支持搜索、筛选与方向键切章</p>
              </div>
              <span className="rounded-full bg-subtle px-3 py-1 text-caption text-secondary">{filteredChapters.length}/{chapterItems.length}</span>
            </div>

            <div className="mt-4 flex items-center gap-2 rounded-2xl border border-border bg-page px-3 py-2 dark:border-dark-border dark:bg-dark-page">
              <Search className="h-4 w-4 text-secondary" />
              <input
                value={chapterSearch}
                onChange={(event) => setChapterSearch(event.target.value)}
                placeholder="搜索章节标题 / 编号"
                className="w-full bg-transparent text-callout text-primary outline-none placeholder:text-tertiary"
              />
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              {leftFilters.map((filter) => (
                <button
                  key={filter}
                  type="button"
                  onClick={() => setLeftFilter(filter)}
                  className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors cursor-pointer ${leftFilter === filter ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                >
                  {filter === 'all' ? '全部' : filter === 'pending' ? '待处理' : filter === 'running' ? '运行中' : filter === 'completed' ? '已完成' : filter === 'failed' ? '失败' : '需关注'}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
            {chaptersLoading && Array.from({ length: 8 }).map((_, index) => (
              <div key={index} className="mx-2 mb-2 h-20 animate-pulse rounded-2xl bg-subtle" />
            ))}

            {!chaptersLoading && filteredChapters.length === 0 && (
              <div className="rounded-2xl border border-dashed border-border bg-subtle px-4 py-10 text-center text-callout text-secondary dark:border-dark-border dark:bg-dark-subtle">
                当前筛选下没有章节
              </div>
            )}

            {!chaptersLoading && filteredChapters.map((chapter) => {
              const isSelected = chapter.index === selectedChapterIndex
              const stageStatus = stageStatusForChapter(
                chapter,
                selectedStage,
                chapterStageRuntimeOverrides
              )
              const selectedRewriteNoSegments = isSelected && selectedStage === 'rewrite' && rewriteChapterHasNoMarkedSegments
              const chapterStageStatusClass = selectedRewriteNoSegments
                ? 'bg-subtle text-secondary'
                : stageStatusClass(stageStatus)
              const chapterStageStatusLabel = selectedRewriteNoSegments
                ? '无需改写'
                : stageStatusLabel(stageStatus)
              const riskLabels = chapterRiskLabels(
                chapter,
                selectedStage,
                isSelected,
                showAnchorMismatchByStage && chapterHasAnchorMismatch && chapter.index === selectedChapterIndex,
                chapterStageRuntimeOverrides
              )
              return (
                <button
                  key={chapter.id ?? chapter.index}
                  type="button"
                  onClick={() => setSelectedChapterIndex(chapter.index)}
                  className={`mb-2 flex w-full flex-col gap-3 rounded-2xl border px-4 py-3 text-left transition-all cursor-pointer ${isSelected ? 'border-accent bg-accent/5 shadow-sm' : 'border-transparent bg-white hover:border-border hover:bg-page dark:bg-dark-card dark:hover:border-dark-border dark:hover:bg-dark-subtle'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-caption font-semibold text-secondary">第 {chapter.index} 章</p>
                      <p className="truncate text-callout font-medium text-primary">{chapterTitle(chapter)}</p>
                    </div>
                    <span className={`rounded-full px-2.5 py-1 text-caption font-medium ${chapterStageStatusClass}`}>
                      {chapterStageStatusLabel}
                    </span>
                  </div>

                  <div className="flex items-center justify-between gap-3">
                    <span className="text-caption text-secondary">{formatChars(chapterCharCount(chapter))}</span>
                    <ChapterDots stageStatuses={{ ...(chapter.stages ?? {}), [selectedStage]: stageStatus }} stageTimings={chapter.stage_timings} />
                  </div>

                  {selectedStage === 'split' && (
                    <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                      <button
                        type="button"
                        className="rounded-lg px-2 py-1 text-[11px] text-secondary hover:bg-error/10 hover:text-error transition-colors"
                        title="删除本章"
                        onClick={async (e) => {
                          e.stopPropagation()
                          if (!id) return
                          try {
                            await chaptersApi.deleteChapter(id, chapter.index)
                            queryClient.invalidateQueries({ queryKey: ['chapters', id] })
                          } catch (err) { console.error(err) }
                        }}
                      >
                        删除
                      </button>
                      <button
                        type="button"
                        className="rounded-lg px-2 py-1 text-[11px] text-secondary hover:bg-accent/10 hover:text-accent transition-colors"
                        title="与下一章合并"
                        onClick={async (e) => {
                          e.stopPropagation()
                          if (!id) return
                          try {
                            await chaptersApi.mergeNext(id, chapter.index)
                            queryClient.invalidateQueries({ queryKey: ['chapters', id] })
                          } catch (err) { console.error(err) }
                        }}
                      >
                        合并下一章
                      </button>
                    </div>
                  )}

                  {riskLabels.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {riskLabels.map((label) => (
                        <span key={`${chapter.index}-${label}`} className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${label === 'ANCHOR_MISMATCH' ? 'bg-warning/10 text-warning' : label === 'FAILED' ? 'bg-error/10 text-error' : 'bg-subtle text-secondary'}`}>
                          {label}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        </aside>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-3xl border border-border bg-white shadow-xs xl:my-4 xl:ml-2 xl:mr-2 dark:border-dark-border dark:bg-dark-card">
          <div className="border-b border-border px-5 py-4 dark:border-dark-border">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-title-3 font-semibold text-primary">{chapterTitle(selectedChapter)}</h2>
                  <span className={`rounded-full px-2.5 py-1 text-caption font-medium ${selectedChapterStageStatusClass}`}>
                    {STAGE_LABELS[selectedStage]} · {selectedChapterStageStatusLabel}
                  </span>
                  <span className="rounded-full bg-subtle px-2.5 py-1 text-caption text-secondary">
                    进度 {stageInfo.chapters_done} / {stageInfo.chapters_total || chapterItems.length || 0}
                  </span>
                  {selectedChapterRiskLabels.map((label) => (
                    <span key={`header-${label}`} className={`rounded-full px-2.5 py-1 text-caption font-medium ${label === 'ANCHOR_MISMATCH' ? 'bg-warning/10 text-warning' : label === 'FAILED' ? 'bg-error/10 text-error' : 'bg-subtle text-secondary'}`}>
                      {label}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                {canUseTextViews && viewModes.map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setViewMode(mode)}
                    className={`rounded-full px-3 py-2 text-caption font-medium transition-colors ${viewMode === mode ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                  >
                    {viewModeLabel(mode)}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
            {selectedStage === 'split' ? (
              <SplitRulesPanel novelId={novel.id} />
            ) : contentLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 6 }).map((_, index) => (
                  <div key={index} className="h-24 animate-pulse rounded-2xl bg-subtle" />
                ))}
              </div>
            ) : !selectedChapter ? (
              <div className="rounded-3xl border border-dashed border-border bg-subtle px-6 py-16 text-center text-callout text-secondary dark:border-dark-border dark:bg-dark-subtle">
                请选择左侧章节开始工作。
              </div>
            ) : selectedStage === 'analyze' ? (
              <div className="space-y-4">
                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <p className="text-caption font-semibold uppercase tracking-wide text-secondary">章节洞察</p>
                  {chapterAnalysisLoading ? (
                    <div className="mt-3 space-y-2">
                      {Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-2xl bg-subtle" />)}
                    </div>
                  ) : (
                    <div className="mt-3 grid gap-3 sm:grid-cols-4">
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">人物</p>
                        <p className="mt-1 text-title-3 font-semibold text-primary">{chapterAnalysis?.characters?.length ?? 0}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">事件</p>
                        <p className="mt-1 text-title-3 font-semibold text-primary">{chapterAnalysis?.key_events?.length ?? 0}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">场景</p>
                        <p className="mt-1 text-title-3 font-semibold text-primary">{chapterAnalysis?.scenes?.length ?? 0}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">可改写场景</p>
                        <p className="mt-1 text-title-3 font-semibold text-primary">{rewriteCandidateScenes}</p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <p className="text-caption font-semibold uppercase tracking-wide text-secondary">场景命中（整章）</p>
                  <p className="mt-1 text-caption text-secondary">本阶段按整章识别；`paragraph_range` 仅用于定位，不代表按段落拆分流程。</p>
                  {chapterAnalysisLoading ? (
                    <div className="mt-3 space-y-2">
                      {Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-20 animate-pulse rounded-2xl bg-subtle" />)}
                    </div>
                  ) : chapterAnalysis?.scenes?.length ? (
                    <div className="mt-3 space-y-3">
                      {chapterAnalysis.scenes.map((scene, sceneIndex) => (
                        <div key={`${scene.scene_type}-${sceneIndex}`} className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-callout font-medium text-primary">{scene.scene_type || `场景 ${sceneIndex + 1}`}</p>
                            <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                              定位 P{scene.paragraph_range[0]} - P{scene.paragraph_range[1]}
                            </span>
                          </div>
                          {scene.rule_hits && scene.rule_hits.length > 0 ? (
                            <div className="mt-2 space-y-2">
                              {scene.rule_hits.map((hit, hitIndex) => (
                                <div key={`${hit.trigger_condition}-${hitIndex}`} className="rounded-xl bg-page px-2.5 py-2 dark:bg-dark-page">
                                  <p className="text-caption text-secondary">触发条件：{hit.trigger_condition}</p>
                                  <p className="mt-1 whitespace-pre-wrap text-caption leading-6 text-primary">{hit.evidence_text}</p>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="mt-2 text-caption text-secondary">本场景暂无命中证据。</p>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-3 text-caption text-secondary">本章暂无场景命中记录。</p>
                  )}
                </div>
              </div>
            ) : viewMode === 'rewrite' ? (
              <div className="space-y-4">
                <div className="rounded-3xl border border-success/20 bg-success/5 px-5 py-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-callout font-semibold text-primary">改写稿预览</h3>
                      <p className="mt-1 text-caption text-secondary">
                        这里直接展示整章改写结果，并支持你手动微调整章文案。
                      </p>
                    </div>
                    <div className="flex flex-wrap justify-end gap-2">
                      {hasRewritePlan ? (
                        <>
                          <span className="rounded-full bg-success/10 px-2.5 py-1 text-caption text-success">成功 {rewriteCoverageStats.successful}</span>
                          <span className="rounded-full bg-error/10 px-2.5 py-1 text-caption text-error">失败 {rewriteCoverageStats.failed}</span>
                          <span className="rounded-full bg-warning/10 px-2.5 py-1 text-caption text-warning">待处理 {rewriteCoverageStats.pending}</span>
                          <span className="rounded-full bg-subtle px-2.5 py-1 text-caption text-secondary">拒绝 {rewriteCoverageStats.rejected}</span>
                        </>
                      ) : (
                        <span className="rounded-full bg-subtle px-2.5 py-1 text-caption text-secondary">未命中可改写段落</span>
                      )}
                    </div>
                  </div>

                  <div className="space-y-3">
                    <textarea
                      value={rewritePreviewText}
                      onChange={(event) => {
                        if (selectedChapterIndex === null) return
                        const nextText = event.target.value
                        setChapterRewriteDrafts((prev) => ({
                          ...prev,
                          [selectedChapterIndex]: nextText,
                        }))
                      }}
                      rows={18}
                      placeholder="改写稿将在这里展示，你可以直接微调整章内容。"
                      className="max-h-[56vh] min-h-[42vh] w-full overflow-y-auto whitespace-pre-wrap rounded-2xl border border-border bg-white px-4 py-3 text-body leading-7 text-primary outline-none focus:border-accent dark:border-dark-border dark:bg-dark-card"
                    />
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="space-y-1">
                        <p className="text-caption text-secondary">当前预览字数：{formatChars(rewritePreviewText.trim().length)}</p>
                        {rewriteAutoSplitStats.segments > 0 && (
                          <p className="text-caption text-secondary">
                            超长段自动拆分：{rewriteAutoSplitStats.segments} 段触发，共 {rewriteAutoSplitStats.parts} 子段处理，已自动合并展示。
                          </p>
                        )}
                        {hasLocalChapterDraft && (
                          <p className={`text-caption ${hasUnsavedLocalChapterDraft ? 'text-warning' : 'text-success'}`}>
                            {hasUnsavedLocalChapterDraft
                              ? '本地微调草稿未保存。'
                              : `本地微调草稿已保存${savedDraftTimeLabel ? `（${savedDraftTimeLabel}）` : ''}。`}
                          </p>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={saveLocalRewriteDraft}
                          disabled={selectedChapterIndex === null || !hasLocalChapterDraft || !hasUnsavedLocalChapterDraft}
                          className="rounded-full bg-accent/10 px-3 py-1.5 text-caption font-medium text-accent transition-colors hover:bg-accent/20 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          保存草稿
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (selectedChapterIndex === null) return
                            clearLocalRewriteDraft(selectedChapterIndex)
                          }}
                          disabled={selectedChapterIndex === null || !hasLocalChapterDraft}
                          className="rounded-full bg-subtle px-3 py-1.5 text-caption font-medium text-secondary transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          还原自动改写稿
                        </button>
                      </div>
                    </div>
                  </div>

                  {!hasRewritePlan && (
                    <div className="rounded-2xl border border-dashed border-border bg-white px-4 py-4 text-caption text-secondary dark:border-dark-border dark:bg-dark-card">
                      当前章节尚未生成改写计划，预览暂显示原文。
                    </div>
                  )}
                  {hasRewritePlan && !hasAnyRewrittenText && (
                    <div className="rounded-2xl border border-warning/20 bg-warning/10 px-4 py-4 text-caption text-warning">
                      本章目前没有成功返回改写文本，预览暂回退为原文。你可以在右侧「日志」查看完整失败详情并重试本章。
                    </div>
                  )}
                  {rewriteFailureSegments.length > 0 && (
                    <div className="rounded-2xl border border-error/20 bg-error/5 px-4 py-4">
                      <p className="text-callout font-semibold text-error">未改写成功段落（全文日志）</p>
                      <div className="mt-3 space-y-2">
                        {rewriteFailureSegments.map((segment) => {
                          const detail = parseJsonValue(segment.error_detail)
                          return (
                            <pre
                              key={`rewrite-failure-${segment.segment_id}`}
                              className="max-h-48 overflow-auto rounded-xl border border-error/20 bg-white px-3 py-3 font-mono text-[12px] leading-6 text-error dark:bg-dark-card"
                            >
{JSON.stringify({
  segment_id: segment.segment_id,
  paragraph_range: segment.paragraph_range,
  status: segment.status ?? null,
  error_code: segment.error_code ?? null,
  error_detail: detail,
  provider_raw_response: segment.provider_raw_response ?? null,
  validation_details: segment.validation_details ?? null,
  target_chars: segment.target_chars ?? null,
  target_chars_min: segment.target_chars_min ?? null,
  target_chars_max: segment.target_chars_max ?? null,
  rewritten_chars: segment.rewritten_chars ?? null,
}, null, 2)}
                            </pre>
                          )
                        })}
                      </div>
                    </div>
                  )}
                  {hasRewritePlan && (
                    <div className="rounded-2xl border border-border bg-white px-4 py-4 dark:border-dark-border dark:bg-dark-card">
                      <p className="text-callout font-semibold text-primary">窗口解释（命中 / 替换 / 保留）</p>
                      <p className="mt-1 text-caption text-secondary">
                        优先展示窗口 offset；若窗口字段缺失则自动回退到兼容模式（segment 级别）。
                      </p>
                      <div className="mt-3 space-y-3">
                        {rewriteResults.map((segment) => {
                          const windows = Array.isArray(segment.rewrite_windows) ? segment.rewrite_windows : []
                          const warningCodes = Array.isArray(segment.warning_codes)
                            ? segment.warning_codes.filter(Boolean)
                            : (segment.error_code ? [segment.error_code] : [])
                          const attemptsByWindow = new Map<string, RewriteWindowAttemptView[]>()
                          ;(Array.isArray(segment.window_attempts) ? segment.window_attempts : []).forEach((attempt) => {
                            const current = attemptsByWindow.get(attempt.window_id) ?? []
                            current.push(attempt)
                            attemptsByWindow.set(attempt.window_id, current)
                          })
                          attemptsByWindow.forEach((value) => {
                            value.sort((a, b) => Number(a.attempt_seq ?? 0) - Number(b.attempt_seq ?? 0))
                          })
                          const rollbackDetail = parseJsonValue(segment.error_detail)
                          const rollbackDetailObj = rollbackDetail && typeof rollbackDetail === 'object'
                            ? (rollbackDetail as Record<string, unknown>)
                            : null
                          return (
                            <div key={`window-explain-${segment.segment_id}`} className="rounded-xl border border-border bg-page px-3 py-3 dark:border-dark-border dark:bg-dark-page">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <p className="text-caption font-medium text-primary">
                                  Segment {shortSegmentId(segment.segment_id)} · P{segment.paragraph_range[0]} - P{segment.paragraph_range[1]}
                                </p>
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                    {segment.status ?? 'pending'}
                                  </span>
                                  <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                    {segment.completion_kind ?? 'normal'}
                                  </span>
                                  {segment.reason_code && (
                                    <span className="rounded-full bg-warning/10 px-2 py-0.5 text-caption text-warning">
                                      {segment.reason_code}
                                    </span>
                                  )}
                                </div>
                              </div>
                              <div className="mt-2 flex flex-wrap items-center gap-2">
                                <span className="rounded-full bg-white px-2 py-0.5 text-caption text-secondary dark:bg-dark-card">
                                  替换范围 {formatOffsetRange(segment.char_offset_range)}
                                </span>
                                <span className="rounded-full bg-white px-2 py-0.5 text-caption text-secondary dark:bg-dark-card">
                                  窗口数 {windows.length}
                                </span>
                                <span className="rounded-full bg-white px-2 py-0.5 text-caption text-secondary dark:bg-dark-card">
                                  告警 {warningCodes.length}
                                </span>
                              </div>
                              {warningCodes.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {warningCodes.map((code) => (
                                    <span key={`${segment.segment_id}-warn-${code}`} className="rounded-full bg-warning/10 px-2 py-0.5 text-caption text-warning">
                                      {code}
                                    </span>
                                  ))}
                                </div>
                              )}
                              {segment.error_code && (
                                <div className="mt-2 rounded-lg border border-warning/20 bg-warning/5 px-2.5 py-2 text-caption text-warning">
                                  <p>最终结果：{segment.error_code}</p>
                                  {rollbackDetailObj && (
                                    <p className="mt-1 text-secondary">
                                      {`rollback_original=${String(rollbackDetailObj.rollback_original ?? false)}，attempts=${String(rollbackDetailObj.attempts ?? '—')}`}
                                    </p>
                                  )}
                                </div>
                              )}

                              {windows.length === 0 ? (
                                <p className="mt-2 text-caption text-secondary">
                                  兼容模式：当前 segment 无窗口字段，组装将按旧字段（char/paragraph）回退渲染。
                                </p>
                              ) : (
                                <div className="mt-2 space-y-2">
                                  {windows.map((window) => (
                                    <div key={`${segment.segment_id}-${window.window_id}`} className="rounded-lg border border-border bg-white px-2.5 py-2 text-caption text-secondary dark:border-dark-border dark:bg-dark-card">
                                      {(() => {
                                        const attempts = attemptsByWindow.get(window.window_id) ?? []
                                        const latestAttempt = attempts[attempts.length - 1]
                                        return (
                                          <>
                                            <div className="flex flex-wrap items-center justify-between gap-2">
                                              <span className="font-medium text-primary">{window.window_id}</span>
                                              <span>
                                                offset [{window.start_offset}, {window.end_offset}) · 命中 {formatSentenceRange(window.hit_sentence_range)} · 上下文 {formatSentenceRange(window.context_sentence_range)}
                                              </span>
                                            </div>
                                            <div className="mt-1 flex flex-wrap items-center gap-2">
                                              <span>目标 {window.target_chars_min} - {window.target_chars_max}</span>
                                              <span>attempts {attempts.length}</span>
                                              {latestAttempt?.action && (
                                                <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                                  动作 {formatAttemptAction(latestAttempt.action)}
                                                </span>
                                              )}
                                              {latestAttempt?.guardrail?.level && (
                                                <span className={`rounded-full px-2 py-0.5 text-caption ${guardrailLevelClass(latestAttempt.guardrail.level)}`}>
                                                  {latestAttempt.guardrail.level}
                                                </span>
                                              )}
                                            </div>
                                            {attempts.length > 0 && (
                                              <div className="mt-2 space-y-1">
                                                {attempts.map((attempt, attemptIndex) => {
                                                  const guardrailCodes = Array.isArray(attempt.guardrail?.codes)
                                                    ? attempt.guardrail?.codes.filter(Boolean)
                                                    : []
                                                  return (
                                                    <div
                                                      key={`${window.window_id}-attempt-${attempt.attempt_seq ?? attemptIndex}`}
                                                      className="rounded-md bg-subtle px-2 py-1.5 text-caption text-secondary"
                                                    >
                                                      <div className="flex flex-wrap items-center gap-2">
                                                        <span>#{attempt.attempt_seq ?? attemptIndex + 1}</span>
                                                        <span>{formatAttemptAction(attempt.action)}</span>
                                                        <span>finish_reason {attempt.finish_reason ?? '—'}</span>
                                                        <span>run_seq {attempt.run_seq ?? '—'}</span>
                                                      </div>
                                                      {guardrailCodes.length > 0 && (
                                                        <div className="mt-1 flex flex-wrap gap-1.5">
                                                          {guardrailCodes.map((code) => (
                                                            <span key={`${window.window_id}-${attempt.attempt_seq ?? attemptIndex}-${code}`} className="rounded-full bg-warning/10 px-1.5 py-0.5 text-[11px] text-warning">
                                                              {code}
                                                            </span>
                                                          ))}
                                                        </div>
                                                      )}
                                                    </div>
                                                  )
                                                })}
                                              </div>
                                            )}
                                          </>
                                        )
                                      })()}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-subtle px-4 py-3">
                  <div>
                    <p className="text-caption font-medium text-primary">Diff 对比文本</p>
                    <p className="mt-1 text-caption text-secondary">平铺高亮展示新增/删除，保留左右两侧原始格式，不会改动原始导入文本。</p>
                  </div>
                  <div className="inline-flex rounded-full border border-border bg-white p-1">
                    {(['raw', 'sentence', 'canonical'] as DiffTextMode[]).map((mode) => {
                      const active = diffTextMode === mode
                      return (
                        <button
                          key={mode}
                          type="button"
                          onClick={() => setDiffTextMode(mode)}
                          className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors ${
                            active
                              ? 'bg-accent text-white'
                              : 'text-secondary hover:text-primary'
                          }`}
                        >
                          {diffTextModeLabel(mode)}
                        </button>
                      )
                    })}
                  </div>
                </div>
                {diffTextMode === 'canonical' && (
                  <p className="rounded-2xl border border-accent/20 bg-accent/5 px-4 py-2 text-caption text-secondary">
                    规范化规则：统一换行、去除行尾空白、折叠连续空行、章节标题前后保留空行、正文缩进空白归一化。
                  </p>
                )}
                {diffTextMode === 'sentence' && (
                  <p className="rounded-2xl border border-accent/20 bg-accent/5 px-4 py-2 text-caption text-secondary">
                    句子对齐规则：先规范化空白，再按句末标点拆分逐句比较，减少因换行和段落格式造成的噪音。
                  </p>
                )}
                {rawDiffHasChanges && (
                  <p className={`rounded-2xl px-4 py-2 text-caption ${
                    formatOnlyDiff
                      ? 'border border-success/30 bg-success/10 text-success'
                      : 'border border-warning/30 bg-warning/10 text-warning'
                  }`}
                  >
                    {formatOnlyDiff
                      ? `格式检测：当前仅发现排版变化（去除空白后文本一致）。换行差异 ${rawLineBreakDeltaLabel} 行。`
                      : `格式检测：当前包含正文内容改动。换行差异 ${rawLineBreakDeltaLabel} 行，建议优先查看「句子对齐」模式。`}
                  </p>
                )}
                {!diffHasChanges && (
                  <div className="rounded-2xl border border-warning/20 bg-warning/10 px-4 py-3 text-caption text-warning">
                    当前 Diff 与原文一致。若你预期有改写，请先确认已选中正确章节，或点击「还原自动改写稿」清除本地草稿覆盖。
                  </div>
                )}
                <GitDiffView
                  oldText={diffOldText}
                  newText={diffNewText}
                  comparisonStyle="flat"
                  mode="side-by-side"
                  title="Diff 对比（平铺高亮）"
                  leftLabel="原文"
                  rightLabel="改写稿"
                  collapseEqualGroupAfter={8}
                  showModeToggle={false}
                />
              </div>
            )}
          </div>
        </section>

        <aside className="flex w-full flex-col overflow-hidden rounded-3xl border border-border bg-white shadow-xs xl:m-4 xl:ml-2 xl:w-[360px] xl:min-w-[340px] dark:border-dark-border dark:bg-dark-card">
          <div className="border-b border-border px-4 py-3 dark:border-dark-border">
            <div className="flex gap-2 overflow-x-auto">
              {availableRightTabs.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setRightTab(tab)}
                  className={`rounded-full px-3 py-2 text-callout font-medium transition-colors cursor-pointer whitespace-nowrap ${rightTab === tab ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                >
                  {rightTabLabel(tab)}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            {rightTab === 'insights' && (
              <div className="space-y-4">
                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-caption text-secondary">当前章节</p>
                      <p className="mt-1 text-callout font-semibold text-primary">{chapterTitle(selectedChapter)}</p>
                    </div>
                    <span className={`rounded-full px-2.5 py-1 text-caption font-medium ${stageStatusClass(stageInfo.status)}`}>
                      {stageStatusLabel(stageInfo.status)}
                    </span>
                  </div>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                      <p className="text-caption text-secondary">章节字数</p>
                      <p className="mt-1 text-body-bold text-primary">{formatChars(chapterCharCount(selectedChapter))}</p>
                    </div>
                    <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                      <p className="text-caption text-secondary">Stage 进度</p>
                      <p className="mt-1 text-body-bold text-primary">{stageInfo.chapters_done} / {stageInfo.chapters_total || chapterItems.length || 0}</p>
                    </div>
                  </div>
                </div>

                {stageRunDetailQuery.data?.run?.config_snapshot && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <p className="text-caption font-semibold uppercase tracking-wide text-secondary">快照</p>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">Provider</p>
                        <p className="mt-1 text-callout font-medium text-primary">{stageRunDetailQuery.data.run.config_snapshot.provider_name ?? '未记录'}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">模型</p>
                        <p className="mt-1 text-callout font-medium text-primary">{stageRunDetailQuery.data.run.config_snapshot.model_name ?? '未记录'}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">Global Prompt</p>
                        <p className="mt-1 font-mono text-caption text-primary">{shortHash(stageRunDetailQuery.data.run.config_snapshot.global_prompt_version)}</p>
                      </div>
                      <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                        <p className="text-caption text-secondary">规则哈希</p>
                        <p className="mt-1 font-mono text-caption text-primary">{shortHash(stageRunDetailQuery.data.run.config_snapshot.rewrite_rules_hash ?? stageRunDetailQuery.data.run.config_snapshot.scene_rules_hash)}</p>
                      </div>
                    </div>
                  </div>
                )}

                {selectedStage !== 'split' && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <p className="text-caption font-semibold uppercase tracking-wide text-secondary">章节洞察</p>
                    {chapterAnalysisLoading ? (
                      <div className="mt-3 space-y-2">
                        {Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-16 animate-pulse rounded-2xl bg-subtle" />)}
                      </div>
                    ) : (
                      <div className="mt-3 grid gap-3 sm:grid-cols-3">
                        <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <p className="text-caption text-secondary">人物</p>
                          <p className="mt-1 text-title-3 font-semibold text-primary">{chapterAnalysis?.characters?.length ?? 0}</p>
                        </div>
                        <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <p className="text-caption text-secondary">事件</p>
                          <p className="mt-1 text-title-3 font-semibold text-primary">{chapterAnalysis?.key_events?.length ?? 0}</p>
                        </div>
                        <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <p className="text-caption text-secondary">改写段</p>
                          <p className="mt-1 text-title-3 font-semibold text-primary">{rewriteResults.length}</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {selectedStage === 'analyze' && chapterAnalysis?.scenes?.length ? (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <p className="text-caption font-semibold uppercase tracking-wide text-secondary">场景命中（整章）</p>
                    <p className="mt-1 text-caption text-secondary">本阶段按整章识别；`paragraph_range` 仅用于定位，不代表按段落拆分流程。</p>
                    <div className="mt-3 space-y-3">
                      {chapterAnalysis.scenes.map((scene, sceneIndex) => (
                        <div key={`${scene.scene_type}-${sceneIndex}`} className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-callout font-medium text-primary">{scene.scene_type || `场景 ${sceneIndex + 1}`}</p>
                            <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                              定位 P{scene.paragraph_range[0]} - P{scene.paragraph_range[1]}
                            </span>
                          </div>
                          {scene.rule_hits && scene.rule_hits.length > 0 ? (
                            <div className="mt-2 space-y-2">
                              {scene.rule_hits.map((hit, hitIndex) => (
                                <div key={`${hit.trigger_condition}-${hitIndex}`} className="rounded-xl bg-page px-2.5 py-2 dark:bg-dark-page">
                                  <p className="text-caption text-secondary">触发条件：{hit.trigger_condition}</p>
                                  <p className="mt-1 whitespace-pre-wrap text-caption leading-6 text-primary">{hit.evidence_text}</p>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <p className="mt-2 text-caption text-secondary">本场景暂无命中证据。</p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {selectedStage === 'assemble' && qualityReport && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-caption font-semibold uppercase tracking-wide text-secondary">质量闸门</p>
                      <span className={`rounded-full px-2.5 py-1 text-caption font-medium ${qualityReport.blocked ? 'bg-error/10 text-error' : 'bg-success/10 text-success'}`}>
                        {qualityReport.blocked ? 'BLOCKED' : 'PASS'}
                      </span>
                    </div>
                    <div className="mt-3 space-y-2">
                      {qualityReport.thresholdComparisons.map((item) => (
                        <div key={item.label} className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                          <div className="flex items-center justify-between gap-2">
                            <p className="text-callout font-medium text-primary">{item.label}</p>
                            <span className={`rounded-full px-2 py-0.5 text-caption ${item.status === 'blocked' ? 'bg-error/10 text-error' : item.status === 'warning' ? 'bg-warning/10 text-warning' : 'bg-success/10 text-success'}`}>
                              {item.status.toUpperCase()}
                            </span>
                          </div>
                          <p className="mt-1 text-caption text-secondary">{item.actual.toFixed(3)} / {item.threshold.toFixed(3)} {item.unit ?? ''}</p>
                        </div>
                      ))}
                    </div>
                    {latestRiskSignature && <p className="mt-3 text-caption text-warning">风险签名：{latestRiskSignature}</p>}
                  </div>
                )}
              </div>
            )}

            {rightTab === 'operations' && (
              <div className="space-y-4">
                {selectedStage === 'rewrite' && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <p className="text-caption font-semibold uppercase tracking-wide text-secondary">改写目标设置</p>
                    <p className="mt-1 text-callout text-primary">每章新增目标字数（全书复用）</p>
                    <p className="mt-2 text-caption text-secondary">
                      这是章节级目标，不是每段目标。一本小说设置一次即可；留空会使用规则默认值。
                    </p>
                    <div className="mt-3 space-y-2">
                      <label className="space-y-1">
                        <span className="text-caption text-secondary">改写 Provider</span>
                        <select
                          value={rewriteProviderId}
                          onChange={(event) => setRewriteProviderId(event.target.value)}
                          className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent dark:border-dark-border dark:bg-dark-card dark:text-dark-primary"
                        >
                          <option value="">
                            {providers.length > 1 ? '请选择本次改写使用的 Provider' : '自动选择（默认）'}
                          </option>
                          {providers.map((provider) => (
                            <option key={provider.id} value={provider.id}>
                              {provider.name} · {provider.model_name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <p className="text-caption text-secondary">
                        {providersLoading
                          ? '正在加载 Provider 列表...'
                          : providers.length === 0
                            ? '尚未配置 Provider，请先到「模型配置」页添加。'
                            : selectedRewriteProvider
                              ? `当前生效 Provider：${selectedRewriteProvider.name} · ${selectedRewriteProvider.model_name}`
                              : providers.length === 1
                                ? `当前生效 Provider：${providers[0].name} · ${providers[0].model_name}`
                                : '已配置多个 Provider，请先选择本次改写使用的 Provider。'}
                      </p>
                      {rewriteProviderSelectionMissing && (
                        <p className="text-caption text-warning">未选择 Provider 前，改写执行与重跑会保持禁用。</p>
                      )}
                      <label className="space-y-1">
                        <span className="text-caption text-secondary">新增字数目标</span>
                        <input
                          type="number"
                          min="0"
                          step="1"
                          value={rewriteTargetAddedCharsInput}
                          onChange={(event) => setRewriteTargetAddedCharsInput(event.target.value)}
                          placeholder="例如：4000"
                          className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent dark:border-dark-border dark:bg-dark-card dark:text-dark-primary"
                        />
                      </label>
                      <p className="text-caption text-secondary">
                        当前生效值：{rewriteTargetAddedCharsSetting.value === null ? '规则默认值' : `${rewriteTargetAddedCharsSetting.value} 字`}
                      </p>
                      {rewriteTargetSettingInvalid && (
                        <p className="text-caption text-error">请输入大于等于 0 的整数，或留空使用默认值。</p>
                      )}
                    </div>
                  </div>
                )}

                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-caption font-semibold uppercase tracking-wide text-secondary">当前章节操作</p>
                      <p className="mt-1 text-callout text-primary">{chapterTitle(selectedChapter)}</p>
                    </div>
                    <span className="rounded-full bg-subtle px-2.5 py-1 text-caption text-secondary">
                      Chapter #{selectedChapterIndex ?? '—'}
                    </span>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <ActionButton
                      label={chapterActionLabel}
                      onClick={() => executeChapterAction(chapterActionMode)}
                      disabled={chapterActionDisabled}
                      loading={chapterActionBusy === `${selectedStage}:${selectedChapterIndex}:${chapterActionMode}`}
                      tone={chapterActionMode === 'run' ? 'primary' : 'secondary'}
                      icon={chapterActionMode === 'run' ? <Play className="h-4 w-4" strokeWidth={1.5} /> : <RefreshCw className="h-4 w-4" strokeWidth={1.5} />}
                    />
                    {selectedStage === 'rewrite' && (
                      <ActionButton
                        label="回退本章到原文"
                        onClick={executeFallbackToOriginalForChapter}
                        disabled={selectedChapterIndex === null}
                        loading={chapterActionBusy === `${selectedStage}:${selectedChapterIndex}:fallback`}
                        tone="warning"
                        icon={<ShieldAlert className="h-4 w-4" strokeWidth={1.5} />}
                      />
                    )}
                    {selectedStage === 'analyze' && (
                      <ActionButton
                        label="查看本章模型日志"
                        onClick={() => setRightTab('logs')}
                        disabled={selectedChapterIndex === null}
                        tone="secondary"
                        icon={<Terminal className="h-4 w-4" strokeWidth={1.5} />}
                      />
                    )}
                  </div>
                  {chapterActionFeedback && (
                    <div
                      className={`mt-3 rounded-2xl border px-3 py-3 text-caption ${
                        chapterActionFeedback.tone === 'success'
                          ? 'border-success/20 bg-success/10 text-success'
                          : chapterActionFeedback.tone === 'error'
                            ? 'border-error/20 bg-error/10 text-error'
                            : 'border-accent/20 bg-accent/10 text-accent'
                      }`}
                    >
                      {chapterActionFeedback.text}
                    </div>
                  )}

                  <p className="mt-2 text-caption text-secondary">
                    这里只影响当前章节。若本章上一步已完成，可直接执行下一步（按章推进）；全量章节控制在下方“全局阶段操作”。
                  </p>
                  {rewriteChapterHasNoMarkedSegments && (
                    <p className="mt-2 text-caption text-warning">
                      当前章节在“分析与标记”阶段没有命中可改写段落，所以改写结果会显示为 0 段。
                    </p>
                  )}
                  {showGenericChapterActionDisabledReason && (
                    <p className="mt-2 text-caption text-warning">{chapterActionDisabledReason}</p>
                  )}
                  {selectedStage === 'rewrite' && rewriteChapterHasNoMarkedSegments && (
                    <p className="mt-2 text-caption text-secondary">
                      本章当前未命中可改写段落，点击后会按 no-op 方式返回并保持原文。
                    </p>
                  )}
                  {rewriteTargetSettingInvalid && selectedStage === 'rewrite' && (
                    <p className="mt-2 text-caption text-error">改写目标设置无效，当前章节操作已禁用。</p>
                  )}
                  {rewriteProviderSelectionMissing && selectedStage === 'rewrite' && (
                    <p className="mt-2 text-caption text-warning">已配置多个 Provider，请先选择本次改写 Provider，当前章节操作已禁用。</p>
                  )}
                  {!selectedChapterPreviousStageCompleted && previousStage && chapterActionStageSupported && (
                    <div className="mt-3 rounded-2xl border border-warning/20 bg-warning/10 px-3 py-3 text-caption text-warning">
                      当前章节需先完成上一步「{STAGE_LABELS[previousStage]}」，才能执行「{STAGE_LABELS[selectedStage]}」。
                    </div>
                  )}
                </div>

                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-caption font-semibold uppercase tracking-wide text-secondary">全局阶段操作</p>
                      <p className="mt-1 text-callout text-primary">{STAGE_LABELS[selectedStage]} · {stageStatusLabel(stageInfo.status)}</p>
                    </div>
                    <span className={`rounded-full px-2.5 py-1 text-caption ${stageStatusClass(stageInfo.status)}`}>{stageInfo.chapters_done}/{stageInfo.chapters_total || chapterItems.length || 0}</span>
                  </div>

                  {!previousStageCompleted && stageInfo.status === 'pending' && previousStage && (
                    <div className="mt-3 rounded-2xl border border-warning/20 bg-warning/10 px-3 py-3 text-caption text-warning">
                      全量执行前需先完成上游阶段「{STAGE_LABELS[previousStage]}」。你也可以先在上方按章节推进。
                    </div>
                  )}

                  <div className="mt-4 flex flex-wrap gap-2">
                    <ActionButton
                      label={stagePrimaryAction.label}
                      onClick={() => executeStageAction(stagePrimaryAction.key as 'run' | 'pause' | 'resume' | 'retry')}
                      disabled={stagePrimaryActionDisabled}
                      loading={stageActionBusy === `${selectedStage}:${stagePrimaryAction.key}`}
                      tone={stagePrimaryAction.tone}
                      icon={stagePrimaryAction.key === 'pause' ? <Pause className="h-4 w-4" strokeWidth={1.5} /> : stagePrimaryAction.key === 'retry' ? <RotateCcw className="h-4 w-4" strokeWidth={1.5} /> : stagePrimaryAction.key === 'resume' ? <Play className="h-4 w-4" strokeWidth={1.5} /> : <Play className="h-4 w-4" strokeWidth={1.5} />}
                    />
                    {currentStageExports.map((option) => (
                      <ActionButton
                        key={`${selectedStage}-${option.format}`}
                        label={option.label}
                        onClick={() => stagesApi.exportArtifact(novel.id, selectedStage, { format: option.format }).then((file) => {
                          const url = URL.createObjectURL(file.blob)
                          const link = document.createElement('a')
                          link.href = url
                          link.download = file.filename || `${selectedStage}.${option.format}`
                          link.click()
                          URL.revokeObjectURL(url)
                        })}
                        disabled={stageInfo.status !== 'completed'}
                        tone="secondary"
                        icon={option.format === 'json' ? <FileJson className="h-4 w-4" strokeWidth={1.5} /> : <FileText className="h-4 w-4" strokeWidth={1.5} />}
                      />
                    ))}
                  </div>
                  <p className="mt-2 text-caption text-secondary">
                    这里的动作会作用到当前阶段的全部章节。
                  </p>
                  {rewriteTargetSettingInvalid && selectedStage === 'rewrite' && (
                    <p className="mt-1 text-caption text-error">改写目标设置无效，全局改写动作已禁用。</p>
                  )}
                  {rewriteProviderSelectionMissing && selectedStage === 'rewrite' && (
                    <p className="mt-1 text-caption text-warning">已配置多个 Provider，请先选择本次改写 Provider，全局改写动作已禁用。</p>
                  )}
                </div>

                {selectedStage === 'split' && (
                  <div className="rounded-3xl border border-border bg-page p-4 text-callout text-secondary dark:border-dark-border dark:bg-dark-page">
                    章节切分规则已经保留在中栏。这里负责阶段动作，中栏负责规则编辑、预览与确认切分。
                  </div>
                )}

                {selectedStage === 'assemble' && (
                  <div className="rounded-3xl border border-border bg-page p-4 text-callout text-secondary dark:border-dark-border dark:bg-dark-page">
                    当前为组装阶段，可在上方执行全量组装并在日志中查看完整产物。
                  </div>
                )}
              </div>
            )}

            {rightTab === 'logs' && (
              <div className="space-y-4">
                <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-caption font-semibold uppercase tracking-wide text-secondary">运行历史</p>
                      <p className="mt-1 text-callout text-primary">Run #{stageInfo.run_seq ?? '—'}</p>
                    </div>
                    {stageLogQuery.isFetching && <Loader2 className="h-4 w-4 animate-spin text-secondary" />}
                  </div>

                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                      <p className="text-caption text-secondary">开始时间</p>
                      <p className="mt-1 text-callout text-primary">{stageInfo.started_at ? new Date(stageInfo.started_at).toLocaleString('zh-CN') : '—'}</p>
                    </div>
                    <div className="rounded-2xl bg-white px-3 py-3 dark:bg-dark-card">
                      <p className="text-caption text-secondary">完成时间</p>
                      <p className="mt-1 text-callout text-primary">{stageInfo.completed_at ? new Date(stageInfo.completed_at).toLocaleString('zh-CN') : '—'}</p>
                    </div>
                  </div>
                </div>

                {stageInfo.error_message && (
                  <div className="rounded-3xl border border-error/20 bg-error/5 px-4 py-4 text-caption leading-6 text-error">
                    <p className="font-semibold">错误摘要</p>
                    <pre className="mt-2 whitespace-pre-wrap font-mono text-[12px] leading-6 text-error">
{stageInfo.error_message}
                    </pre>
                  </div>
                )}

                {stageInfo.status === 'pending' ? (
                  <div className="rounded-3xl border border-dashed border-border bg-subtle px-4 py-12 text-center text-callout text-secondary dark:border-dark-border dark:bg-dark-subtle">
                    当前阶段还没有运行日志。
                  </div>
                ) : stageLogQuery.isLoading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 3 }).map((_, index) => <div key={index} className="h-28 animate-pulse rounded-2xl bg-subtle" />)}
                  </div>
                ) : stageLogQuery.error ? (
                  <div className="rounded-3xl border border-error/20 bg-error/5 px-4 py-4 text-callout text-error">
                    {stageLogQuery.error instanceof Error ? stageLogQuery.error.message : '日志加载失败'}
                  </div>
                ) : (
                  <pre className="max-h-[56vh] overflow-auto rounded-3xl border border-border bg-page px-4 py-4 font-mono text-[12px] leading-6 text-primary dark:border-dark-border dark:bg-dark-page">
{JSON.stringify(stageLogQuery.data?.artifact ?? stageLogQuery.data?.latest_artifact ?? stageLogQuery.data?.run ?? stageRunDetailQuery.data?.run ?? null, null, 2)}
                  </pre>
                )}

                {selectedStage === 'rewrite' && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <p className="text-caption font-semibold uppercase tracking-wide text-secondary">当前章节改写失败明细（全文）</p>
                    {rewriteFailureSegments.length === 0 ? (
                      <p className="mt-2 text-caption text-secondary">当前章节暂无失败段落明细。</p>
                    ) : (
                      <div className="mt-3 space-y-3">
                        {rewriteFailureSegments.map((segment) => {
                          const parsedErrorDetail = parseJsonValue(segment.error_detail)
                          return (
                            <pre
                              key={`rewrite-log-${segment.segment_id}`}
                              className="max-h-[36vh] overflow-auto rounded-2xl border border-error/20 bg-white px-3 py-3 font-mono text-[12px] leading-6 text-error dark:bg-dark-card"
                            >
{JSON.stringify({
  segment_id: segment.segment_id,
  paragraph_range: segment.paragraph_range,
  status: segment.status ?? null,
  error_code: segment.error_code ?? null,
  error_detail: parsedErrorDetail,
  provider_raw_response: segment.provider_raw_response ?? null,
  validation_details: segment.validation_details ?? null,
  target_chars: segment.target_chars ?? null,
  target_chars_min: segment.target_chars_min ?? null,
  target_chars_max: segment.target_chars_max ?? null,
  rewritten_chars: segment.rewritten_chars ?? null,
}, null, 2)}
                            </pre>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}

                {selectedStage === 'analyze' && selectedChapterIndex !== null && (
                  <div className="rounded-3xl border border-border bg-page p-4 dark:border-dark-border dark:bg-dark-page">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-caption font-semibold uppercase tracking-wide text-secondary">模型调用全文日志</p>
                        <p className="mt-1 text-callout text-primary">当前章节：第 {selectedChapterIndex} 章</p>
                        <p className="mt-1 text-caption text-secondary">
                          默认仅展示当前运行窗口日志；可切换查看历史。最新记录在最前，优先展开。
                        </p>
                      </div>
                      {promptLogsQuery.isFetching && <Loader2 className="h-4 w-4 animate-spin text-secondary" />}
                    </div>

                    {showPromptScopeSwitch && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => setPromptLogScope('current')}
                          className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors cursor-pointer ${promptLogScope === 'current' ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                        >
                          当前运行
                        </button>
                        <button
                          type="button"
                          onClick={() => setPromptLogScope('all')}
                          className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors cursor-pointer ${promptLogScope === 'all' ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                        >
                          全部历史
                        </button>
                      </div>
                    )}

                    {promptLogsQuery.isLoading ? (
                      <div className="mt-3 space-y-2">
                        {Array.from({ length: 2 }).map((_, index) => <div key={index} className="h-24 animate-pulse rounded-2xl bg-subtle" />)}
                      </div>
                    ) : promptLogsQuery.error ? (
                      <div className="mt-3 rounded-2xl border border-error/20 bg-error/5 px-3 py-3 text-callout text-error">
                        {promptLogsQuery.error instanceof Error ? promptLogsQuery.error.message : 'Prompt 日志加载失败'}
                      </div>
                    ) : visiblePromptLogEntries.length === 0 ? (
                      <div className="mt-3 rounded-2xl border border-dashed border-border bg-subtle px-3 py-8 text-center text-caption text-secondary">
                        当前章节暂无 Prompt 全文日志。
                      </div>
                    ) : (
                      <div className="mt-3 space-y-3">
                        {visiblePromptLogEntries.map((entry: PromptLogEntry, index: number) => {
                          const promptLayout = detectPromptLayout(entry)
                          return (
                            <details
                              key={entry.call_id}
                              open={index === 0}
                              className="rounded-2xl border border-border bg-white px-3 py-3 dark:border-dark-border dark:bg-dark-card"
                            >
                              <summary className="cursor-pointer text-callout font-medium text-primary">
                                {new Date(entry.timestamp).toLocaleString('zh-CN')} · attempt #{entry.attempt} · {entry.model_name ?? entry.provider}
                              </summary>
                              <div className="mt-3 space-y-3">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className={`rounded-full px-2.5 py-1 text-caption ${promptLayout.layout === 'legacy' ? 'bg-warning/10 text-warning' : promptLayout.layout === 'current' ? 'bg-success/10 text-success' : 'bg-subtle text-secondary'}`}>
                                    {promptLayout.layout === 'legacy'
                                      ? '旧结构：规则在 System'
                                      : promptLayout.layout === 'current'
                                        ? '新结构：规则在 User'
                                        : promptLayout.layout === 'mixed'
                                          ? '混合结构'
                                          : '结构未知'}
                                  </span>
                                  {promptLayout.userHasWholeChapterDirective && (
                                    <span className="rounded-full bg-accent/10 px-2.5 py-1 text-caption text-accent">整章识别指令</span>
                                  )}
                                </div>
                                <div>
                                  <p className="mb-1 text-caption text-secondary">Validation</p>
                                  <pre className="max-h-36 overflow-auto rounded-xl bg-page px-3 py-2 font-mono text-[12px] leading-6 text-primary dark:bg-dark-page">
{JSON.stringify(entry.validation ?? {}, null, 2)}
                                  </pre>
                                </div>
                                <div>
                                  <p className="mb-1 text-caption text-secondary">System Prompt</p>
                                  <pre className="max-h-36 overflow-auto rounded-xl bg-page px-3 py-2 font-mono text-[12px] leading-6 text-primary dark:bg-dark-page">
{entry.system_prompt || '—'}
                                  </pre>
                                </div>
                                <div>
                                  <p className="mb-1 text-caption text-secondary">User Prompt</p>
                                  <pre className="max-h-56 overflow-auto rounded-xl bg-page px-3 py-2 font-mono text-[12px] leading-6 text-primary dark:bg-dark-page">
{entry.user_prompt || '—'}
                                  </pre>
                                </div>
                                <div>
                                  <p className="mb-1 text-caption text-secondary">Model Response</p>
                                  <pre className="max-h-72 overflow-auto rounded-xl bg-page px-3 py-2 font-mono text-[12px] leading-6 text-primary dark:bg-dark-page">
{prettyPrintPromptPayload(entry.response)}
                                  </pre>
                                </div>
                              </div>
                            </details>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
