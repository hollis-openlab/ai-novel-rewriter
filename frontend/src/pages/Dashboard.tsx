import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Upload,
  FileText,
  CheckCircle,
  AlertCircle,
  Loader2,
  X,
  Activity,
  Users,
  Clock,
  TrendingUp,
  ChevronRight,
} from 'lucide-react'
import { getNovels, uploadFile, workers as workersApi } from '@/lib/api'
import { wsManager } from '@/lib/ws'
import { useWorkerStore } from '@/stores/index'
import type { WSMessage, Novel } from '@/types'

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万字`
  return `${n.toLocaleString()}字`
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}小时前`
  return `${Math.floor(hrs / 24)}天前`
}

// ── Activity Event type ───────────────────────────────────────────────────────

interface ActivityEvent {
  id: string
  type: string
  label: string
  time: number
  novel_id?: string
}

function wsMessageToActivity(msg: WSMessage): ActivityEvent | null {
  const base = { id: `${Date.now()}-${Math.random()}`, time: Date.now() }
  switch (msg.type) {
    case 'stage_completed':
      return { ...base, type: 'completed', label: `「${msg.stage}」阶段完成`, novel_id: msg.novel_id }
    case 'stage_failed':
      return { ...base, type: 'failed', label: `「${msg.stage}」阶段失败`, novel_id: msg.novel_id }
    case 'stage_progress':
      return { ...base, type: 'progress', label: `${msg.stage} ${msg.percentage ?? 0}%`, novel_id: msg.novel_id }
    case 'chapter_completed':
      return { ...base, type: 'chapter', label: `第${msg.chapter_index + 1}章完成 (${msg.stage})`, novel_id: msg.novel_id }
    default:
      return null
  }
}

// ── StatsCard ─────────────────────────────────────────────────────────────────

interface StatsCardProps {
  label: string
  value: number
  icon: React.ReactNode
  iconBg: string
}

function StatsCard({ label, value, icon, iconBg }: StatsCardProps) {
  const [hovered, setHovered] = useState(false)
  return (
    <div
      className="bg-white rounded-2xl p-6 shadow-xs transition-all duration-200 cursor-default select-none"
      style={{ transform: hovered ? 'translateY(-2px)' : 'translateY(0)', boxShadow: hovered ? '0 2px 8px rgba(0,0,0,0.06)' : '' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-caption text-secondary uppercase tracking-wide mb-1">{label}</p>
          <p className="text-display font-bold text-primary leading-none">{value}</p>
        </div>
        <div className={`p-3 rounded-xl ${iconBg}`}>
          {icon}
        </div>
      </div>
    </div>
  )
}

// ── Status Badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status, stage, percent }: { status: string; stage?: string; percent?: number }) {
  if (status === 'processing' || status === 'running') {
    return (
      <div className="flex items-center gap-2 bg-accent/10 text-accent rounded-full px-3 py-1">
        <Loader2 className="w-3 h-3 animate-spin" strokeWidth={2} />
        <span className="text-caption font-medium">
          {stage ? `${stage} ${percent != null ? Math.round(percent) + '%' : ''}` : '进行中'}
        </span>
      </div>
    )
  }
  if (status === 'completed' || status === 'done') {
    return (
      <div className="flex items-center gap-1.5 bg-success/10 text-success rounded-full px-3 py-1">
        <CheckCircle className="w-3 h-3" strokeWidth={2} />
        <span className="text-caption font-medium">已完成</span>
      </div>
    )
  }
  if (status === 'failed' || status === 'error') {
    return (
      <div className="flex items-center gap-1.5 bg-error/10 text-error rounded-full px-3 py-1">
        <AlertCircle className="w-3 h-3" strokeWidth={2} />
        <span className="text-caption font-medium">失败</span>
      </div>
    )
  }
  return (
    <div className="flex items-center gap-1.5 bg-subtle text-secondary rounded-full px-3 py-1">
      <Clock className="w-3 h-3" strokeWidth={1.5} />
      <span className="text-caption font-medium">待处理</span>
    </div>
  )
}

// ── Novel Row ─────────────────────────────────────────────────────────────────

interface NovelRowProps {
  novel: Novel
  progressMap: Record<string, { stage: string; percent: number }>
}

const DASHBOARD_STAGES = ['import', 'split', 'analyze', 'rewrite', 'assemble'] as const

type PipelineStageInfo = {
  status?: string
  chapters_total?: number
  chapters_done?: number
  completed_at?: string | null
}

export function normalizePipelineStageStatus(stage: PipelineStageInfo | undefined): string {
  if (!stage) return 'pending'
  const raw = stage.status ?? 'pending'
  const total = Math.max(0, Number(stage.chapters_total ?? 0))
  const done = Math.max(0, Number(stage.chapters_done ?? 0))

  if ((raw === 'paused' || raw === 'stale') && total > 0 && done >= total) {
    return 'completed'
  }
  if (raw === 'stale') {
    if (done > 0 || stage.completed_at) return 'paused'
    return 'pending'
  }
  return raw
}

export function deriveNovelStatus(novel: Novel, progressMap: Record<string, { stage: string; percent: number }>) {
  const prog = progressMap[novel.id]
  if (prog) {
    return {
      status: 'running' as const,
      stage: prog.stage,
      percent: prog.percent,
    }
  }

  const pipelineStatus = novel.pipeline_status as Record<string, PipelineStageInfo> | undefined
  if (!pipelineStatus) {
    return {
      status: 'pending' as const,
      stage: undefined,
      percent: undefined,
    }
  }

  const normalized = DASHBOARD_STAGES.map((stage) => ({
    stage,
    status: normalizePipelineStageStatus(pipelineStatus[stage]),
    info: pipelineStatus[stage],
  }))

  const runningStage = normalized.find((item) => item.status === 'running')
  if (runningStage) {
    const total = Math.max(0, Number(runningStage.info?.chapters_total ?? 0))
    const done = Math.max(0, Number(runningStage.info?.chapters_done ?? 0))
    return {
      status: 'running' as const,
      stage: runningStage.stage,
      percent: total > 0 ? (done / total) * 100 : 0,
    }
  }

  if (normalized.some((item) => item.status === 'failed')) {
    return {
      status: 'failed' as const,
      stage: undefined,
      percent: undefined,
    }
  }

  if (normalized.every((item) => item.status === 'completed')) {
    return {
      status: 'completed' as const,
      stage: undefined,
      percent: undefined,
    }
  }

  return {
    status: 'pending' as const,
    stage: undefined,
    percent: undefined,
  }
}

function NovelRow({ novel, progressMap }: NovelRowProps) {
  const navigate = useNavigate()
  const [hovered, setHovered] = useState(false)
  const derived = deriveNovelStatus(novel, progressMap)
  const status = derived.status
  const activeStage = derived.stage
  const activePct = derived.percent

  return (
    <div
      className="bg-white rounded-xl p-5 transition-all duration-200 cursor-pointer border border-transparent"
      style={{
        boxShadow: hovered ? '0 2px 8px rgba(0,0,0,0.06)' : '0 1px 2px rgba(0,0,0,0.04)',
        borderColor: hovered ? 'transparent' : 'transparent',
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={() => navigate(`/novels/${novel.id}`)}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === 'Enter' && navigate(`/novels/${novel.id}`)}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-title-2 font-semibold text-primary truncate">《{novel.title}》</p>
          <p className="text-callout text-secondary mt-0.5">
            {formatChars(novel.total_chars)} · {novel.chapter_count ?? '—'} 章 · {relativeTime(novel.imported_at)}
          </p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <StatusBadge status={status} stage={activeStage} percent={activePct} />
          <ChevronRight
            className="w-4 h-4 text-tertiary transition-transform duration-150"
            strokeWidth={1.5}
            style={{ transform: hovered ? 'translateX(2px)' : 'translateX(0)' }}
          />
        </div>
      </div>
    </div>
  )
}

// ── Worker Monitor ─────────────────────────────────────────────────────────────

function WorkerMonitor() {
  const { active, idle, queue_size, update } = useWorkerStore()

  // Poll the workers API every 5 seconds to keep the store fresh
  useEffect(() => {
    const poll = async () => {
      try {
        const status = await workersApi.status()
        update({ active: status.active, idle: status.idle, queue_size: status.queue_size })
      } catch {
        // Silently ignore — store retains previous values
      }
    }
    poll()
    const timer = setInterval(poll, 5000)
    return () => clearInterval(timer)
  }, [update])

  const total = active + idle
  const usagePercent = total > 0 ? Math.round((active / total) * 100) : 0

  return (
    <div className="bg-white rounded-2xl p-6 shadow-xs h-full">
      <div className="flex items-center gap-2 mb-5">
        <div className="p-2 bg-ai/10 rounded-lg">
          <Users className="w-4 h-4 text-ai" strokeWidth={1.5} />
        </div>
        <h3 className="text-title-3 font-semibold text-primary">Worker 池</h3>
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div className="text-center p-3 bg-page rounded-xl">
            <p className="text-title-2 font-bold text-success">{active}</p>
            <p className="text-caption text-secondary">活跃</p>
          </div>
          <div className="text-center p-3 bg-page rounded-xl">
            <p className="text-title-2 font-bold text-secondary">{idle}</p>
            <p className="text-caption text-secondary">空闲</p>
          </div>
          <div className="text-center p-3 bg-page rounded-xl">
            <p className="text-title-2 font-bold text-warning">{queue_size}</p>
            <p className="text-caption text-secondary">队列</p>
          </div>
        </div>

        <div>
          <div className="flex justify-between items-center mb-1.5">
            <span className="text-caption text-secondary">使用率</span>
            <span className="text-caption font-medium text-primary">{usagePercent}%</span>
          </div>
          <div className="h-2 bg-subtle rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all duration-300"
              style={{ width: `${usagePercent}%` }}
            />
          </div>
        </div>

        {total > 0 && (
          <div className="flex items-center gap-2 text-caption text-secondary">
            <TrendingUp className="w-3.5 h-3.5" strokeWidth={1.5} />
            <span>{active} / {total} 活跃</span>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Activity Feed ─────────────────────────────────────────────────────────────

function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  function activityIcon(type: string) {
    if (type === 'completed' || type === 'chapter') return <CheckCircle className="w-3.5 h-3.5 text-success flex-shrink-0" strokeWidth={1.5} />
    if (type === 'failed') return <AlertCircle className="w-3.5 h-3.5 text-error flex-shrink-0" strokeWidth={1.5} />
    return <Activity className="w-3.5 h-3.5 text-accent flex-shrink-0" strokeWidth={1.5} />
  }

  return (
    <div className="bg-white rounded-2xl p-6 shadow-xs h-full">
      <div className="flex items-center gap-2 mb-5">
        <div className="p-2 bg-accent/10 rounded-lg">
          <Activity className="w-4 h-4 text-accent" strokeWidth={1.5} />
        </div>
        <h3 className="text-title-3 font-semibold text-primary">最近动态</h3>
      </div>

      {events.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <Activity className="w-8 h-8 text-tertiary mb-3" strokeWidth={1.5} />
          <p className="text-callout text-secondary">暂无活动记录</p>
          <p className="text-caption text-tertiary mt-1">处理任务时将在此显示进度</p>
        </div>
      ) : (
        <div className="space-y-3">
          {events.map(ev => (
            <div key={ev.id} className="flex items-start gap-3">
              <div className="mt-0.5">{activityIcon(ev.type)}</div>
              <div className="flex-1 min-w-0">
                <p className="text-callout text-primary truncate">{ev.label}</p>
              </div>
              <p className="text-caption text-tertiary flex-shrink-0 whitespace-nowrap">
                {relativeTime(new Date(ev.time).toISOString())}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Import Modal ──────────────────────────────────────────────────────────────

interface ImportModalProps {
  onClose: () => void
  onSuccess: (novelId: string) => void
}

function ImportModal({ onClose, onSuccess }: ImportModalProps) {
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = (f: File) => {
    if (!f.name.match(/\.(txt|epub)$/i)) {
      setError('仅支持 .txt 和 .epub 格式')
      return
    }
    setError(null)
    setFile(f)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const result = await uploadFile(file, (pct) => setProgress(pct))
      onSuccess(result.novel_id)
    } catch (err: any) {
      setError(err?.message ?? '上传失败，请重试')
      setUploading(false)
    }
  }

  // Close on backdrop click
  const backdropRef = useRef<HTMLDivElement>(null)

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={e => { if (e.target === backdropRef.current) onClose() }}
    >
      <div
        className="bg-white rounded-2xl shadow-lg w-full max-w-md mx-4 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-subtle">
          <h2 className="text-title-2 font-semibold text-primary">导入小说</h2>
          <button
            className="p-1.5 hover:bg-subtle rounded-lg transition-colors duration-150 cursor-pointer"
            onClick={onClose}
            disabled={uploading}
          >
            <X className="w-4 h-4 text-secondary" strokeWidth={1.5} />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-4">
          {/* Drop zone */}
          <div
            className={`border-2 border-dashed rounded-xl p-8 text-center transition-all duration-200 cursor-pointer
              ${dragging ? 'border-accent bg-accent/5' : 'border-subtle hover:border-accent/50 hover:bg-page'}
              ${file ? 'border-success/50 bg-success/5' : ''}
            `}
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => !uploading && inputRef.current?.click()}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".txt,.epub"
              className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
            />

            {file ? (
              <div className="space-y-2">
                <div className="flex items-center justify-center gap-2">
                  <FileText className="w-8 h-8 text-success" strokeWidth={1.5} />
                </div>
                <p className="text-body-bold text-primary">{file.name}</p>
                <p className="text-caption text-secondary">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-center">
                  <div className="p-4 bg-subtle rounded-xl">
                    <Upload className="w-8 h-8 text-secondary" strokeWidth={1.5} />
                  </div>
                </div>
                <div>
                  <p className="text-body-bold text-primary">拖拽文件到此处</p>
                  <p className="text-callout text-secondary mt-1">或点击选择文件</p>
                  <p className="text-caption text-tertiary mt-2">支持 .txt · .epub</p>
                </div>
              </div>
            )}
          </div>

          {/* Progress bar */}
          {uploading && (
            <div className="space-y-1.5">
              <div className="flex justify-between">
                <span className="text-caption text-secondary">上传中...</span>
                <span className="text-caption font-medium text-primary">{Math.round(progress)}%</span>
              </div>
              <div className="h-2 bg-subtle rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent rounded-full transition-all duration-200"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 p-3 bg-error/10 rounded-lg">
              <AlertCircle className="w-4 h-4 text-error flex-shrink-0" strokeWidth={1.5} />
              <p className="text-callout text-error">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 px-6 py-4 border-t border-subtle">
          <button
            className="button-secondary text-callout"
            onClick={onClose}
            disabled={uploading}
          >
            取消
          </button>
          <button
            className="button-primary text-callout flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            onClick={handleUpload}
            disabled={!file || uploading}
          >
            {uploading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" strokeWidth={1.5} />
                <span>上传中</span>
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" strokeWidth={1.5} />
                <span>开始导入</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Empty State ───────────────────────────────────────────────────────────────

function EmptyState({ onImport }: { onImport: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 space-y-6">
      <div className="relative">
        <div className="p-6 bg-subtle rounded-2xl">
          <FileText className="w-16 h-16 text-secondary" strokeWidth={1.5} />
        </div>
        <div className="absolute -top-1 -right-1 p-1.5 bg-accent rounded-full">
          <Upload className="w-3.5 h-3.5 text-white" strokeWidth={2} />
        </div>
      </div>

      <div className="text-center space-y-2">
        <h2 className="text-title-2 font-semibold text-primary">导入你的第一部小说</h2>
        <p className="text-callout text-secondary max-w-xs">
          支持 .txt 和 .epub 格式，导入后自动进入 AI 处理流水线
        </p>
      </div>

      <button
        className="button-primary flex items-center gap-2 px-6 py-3 rounded-xl text-body-bold cursor-pointer"
        onClick={onImport}
      >
        <Upload className="w-5 h-5" strokeWidth={1.5} />
        <span>导入小说</span>
      </button>
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export function Dashboard() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showImport, setShowImport] = useState(false)
  const [activities, setActivities] = useState<ActivityEvent[]>([])
  // novelId → { stage, percent }
  const [progressMap, setProgressMap] = useState<Record<string, { stage: string; percent: number }>>({})

  const { data: novels = [], isLoading } = useQuery({
    queryKey: ['novels'],
    queryFn: getNovels,
    refetchInterval: 10000,
  })

  // Derived counts
  const statusSummary = novels.reduce(
    (acc, novel) => {
      const status = deriveNovelStatus(novel, progressMap).status
      if (status === 'running') acc.processing += 1
      else if (status === 'completed') acc.completed += 1
      else if (status === 'failed') acc.failed += 1
      return acc
    },
    { processing: 0, completed: 0, failed: 0 }
  )

  const processingCount = statusSummary.processing
  const completedCount = statusSummary.completed
  const failedCount = statusSummary.failed

  // WebSocket subscription
  const addActivity = useCallback((ev: ActivityEvent) => {
    setActivities(prev => [ev, ...prev].slice(0, 10))
  }, [])

  useEffect(() => {
    wsManager.connect()
    wsManager.subscribe('*')

    const unsubscribe = wsManager.onMessage((msg: WSMessage) => {
      // Update progress map
      if (msg.type === 'stage_progress') {
        setProgressMap(prev => ({
          ...prev,
          [msg.novel_id]: { stage: msg.stage, percent: msg.percentage ?? 0 },
        }))
      }
      // Clear progress when completed/failed
      if (msg.type === 'stage_completed' || msg.type === 'stage_failed') {
        queryClient.invalidateQueries({ queryKey: ['novels'] })
        setProgressMap(prev => {
          const next = { ...prev }
          delete next[msg.novel_id]
          return next
        })
      }

      const ev = wsMessageToActivity(msg)
      if (ev) addActivity(ev)
    })

    return () => {
      unsubscribe()
    }
  }, [queryClient, addActivity])

  const handleImportSuccess = (novelId: string) => {
    setShowImport(false)
    queryClient.invalidateQueries({ queryKey: ['novels'] })
    navigate(`/novels/${novelId}`)
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-display font-bold text-primary">Dashboard</h1>
        <button
          className="button-primary flex items-center gap-2 cursor-pointer"
          onClick={() => setShowImport(true)}
        >
          <Upload className="w-4 h-4" strokeWidth={1.5} />
          <span>导入小说</span>
        </button>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-3 gap-5">
        <StatsCard
          label="处理中"
          value={processingCount}
          icon={<Loader2 className="w-5 h-5 text-accent animate-spin" strokeWidth={1.5} />}
          iconBg="bg-accent/10"
        />
        <StatsCard
          label="已完成"
          value={completedCount}
          icon={<CheckCircle className="w-5 h-5 text-success" strokeWidth={1.5} />}
          iconBg="bg-success/10"
        />
        <StatsCard
          label="失败"
          value={failedCount}
          icon={<AlertCircle className="w-5 h-5 text-error" strokeWidth={1.5} />}
          iconBg="bg-error/10"
        />
      </div>

      {/* Novel List / Empty State */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="bg-white rounded-xl p-5 shadow-xs">
              <div className="animate-pulse space-y-2">
                <div className="h-5 bg-subtle rounded-lg w-1/3" />
                <div className="h-3.5 bg-subtle rounded w-1/2" />
              </div>
            </div>
          ))}
        </div>
      ) : novels.length === 0 ? (
        <EmptyState onImport={() => setShowImport(true)} />
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-title-3 font-semibold text-primary">小说列表</h2>
            <span className="text-caption text-secondary">{novels.length} 部</span>
          </div>
          <div className="space-y-2">
            {novels.map(novel => (
              <NovelRow key={novel.id} novel={novel} progressMap={progressMap} />
            ))}
          </div>
        </div>
      )}

      {/* Bottom bento row */}
      {novels.length > 0 && (
        <div className="grid gap-5" style={{ gridTemplateColumns: '3fr 2fr' }}>
          <ActivityFeed events={activities} />
          <WorkerMonitor />
        </div>
      )}

      {/* Import modal */}
      {showImport && (
        <ImportModal
          onClose={() => setShowImport(false)}
          onSuccess={handleImportSuccess}
        />
      )}
    </div>
  )
}
