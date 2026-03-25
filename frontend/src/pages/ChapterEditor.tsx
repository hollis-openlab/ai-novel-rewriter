import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Copy,
  FileText,
  Loader2,
  Pencil,
  RotateCw,
  Save,
  Sparkles,
  Star,
  Users,
  X,
} from 'lucide-react'
import {
  useChapter,
  useChapterAnalysis,
  useChapters,
  useChapterRewrites,
  useCharacterTrajectory,
  useReviewRewrite,
} from '@/hooks/api'
import { buildPromptClipboardText, detectPromptLayout, formatPromptLogTokens, prettyPrintPromptPayload, promptLogsApi } from '@/lib/prompt-logs'
import type { PromptLogEntry, PromptLogRetryResponse } from '@/types/prompt-log'
import type { RewriteSegment } from '@/types'

const SCENE_COLORS: Record<string, { bg: string; border: string; name: string }> = {
  '战斗': { bg: 'bg-scene-combat', border: '#EF4444', name: '战斗' },
  '对话': { bg: 'bg-scene-dialogue', border: '#3B82F6', name: '对话' },
  '心理描写': { bg: 'bg-scene-psychology', border: '#8B5CF6', name: '心理' },
  '环境描写': { bg: 'bg-scene-environment', border: '#22C55E', name: '环境' },
  '叙事过渡': { bg: 'bg-scene-narration', border: '#94A3B8', name: '叙事' },
  '感情互动': { bg: 'bg-scene-romance', border: '#F43F5E', name: '感情' },
  '回忆闪回': { bg: 'bg-scene-flashback', border: '#F59E0B', name: '回忆' },
  '日常生活': { bg: 'bg-scene-daily', border: '#0EA5E9', name: '日常' },
}

const STAGE_KEYS = ['split', 'analyze', 'mark', 'rewrite', 'assemble'] as const
const STAGE_STATUS_COLOR: Record<string, string> = {
  completed: 'bg-success',
  running: 'bg-accent',
  failed: 'bg-error',
  paused: 'bg-warning',
  pending: 'bg-border',
  stale: 'bg-warning',
}

type ViewMode = 'scene' | 'rewrite' | 'compare'
type RightTab = 'characters' | 'events' | 'suggestions' | 'review'
type ReviewStatus = 'pending' | 'accepted' | 'rejected' | 'regenerating' | 'accepted_edited'
type ReviewFilter = 'all' | ReviewStatus

type SegmentState = {
  status: ReviewStatus
  draftText: string
  editing: boolean
  busy: boolean
}

function emotionColor(emotion: string): string {
  if (emotion.includes('愤怒') || emotion.includes('愤')) return 'bg-red-100 text-red-600'
  if (emotion.includes('喜悦') || emotion.includes('喜') || emotion.includes('高兴')) return 'bg-green-100 text-green-600'
  if (emotion.includes('焦虑') || emotion.includes('焦') || emotion.includes('紧张')) return 'bg-amber-100 text-amber-600'
  if (emotion.includes('平静') || emotion.includes('冷静')) return 'bg-gray-100 text-gray-600'
  return 'bg-blue-100 text-blue-600'
}

function PriorityStars({ priority }: { priority: number }) {
  const color = priority >= 4 ? 'text-warning' : priority === 3 ? 'text-accent' : 'text-secondary'
  return (
    <span className={`flex items-center gap-0.5 ${color}`}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Star
          key={i}
          className="w-3 h-3"
          strokeWidth={1.5}
          fill={i < priority ? 'currentColor' : 'none'}
        />
      ))}
    </span>
  )
}

function getParagraphs(content?: string): string[] {
  return content ? content.split(/\n\n+/).map((part) => part.trim()).filter(Boolean) : []
}

function getParagraphRangeText(paragraphs: string[], range: [number, number]): string {
  const start = Math.max(1, range[0])
  const end = Math.max(start, range[1])
  return paragraphs.slice(start - 1, end).join('\n\n')
}

function reviewStatusLabel(status: ReviewStatus): string {
  switch (status) {
    case 'accepted':
      return '已采纳'
    case 'accepted_edited':
      return '已编辑采纳'
    case 'rejected':
      return '已拒绝'
    case 'regenerating':
      return '重写中'
    default:
      return '待处理'
  }
}

function reviewStatusClass(status: ReviewStatus): string {
  switch (status) {
    case 'accepted':
      return 'bg-success/10 text-success border-success/20'
    case 'accepted_edited':
      return 'bg-accent/10 text-accent border-accent/20'
    case 'rejected':
      return 'bg-error/10 text-error border-error/20'
    case 'regenerating':
      return 'bg-warning/10 text-warning border-warning/20'
    default:
      return 'bg-subtle text-secondary border-border'
  }
}

function formatPromptLogTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function segmentDefaultState(segment: RewriteSegment): SegmentState {
  return {
    status: segment.confirmed ? 'accepted' : 'pending',
    draftText: segment.manual_edited_text ?? '',
    editing: false,
    busy: false,
  }
}

export function ChapterEditor() {
  const { id: novelId, chapterId } = useParams<{ id: string; chapterId: string }>()
  const navigate = useNavigate()
  const chapterIdx = Number.isFinite(Number(chapterId)) ? Number(chapterId) : 0

  const [viewMode, setViewMode] = useState<ViewMode>('scene')
  const [rightTab, setRightTab] = useState<RightTab>('characters')
  const [bottomExpanded, setBottomExpanded] = useState(false)
  const [bottomTab, setBottomTab] = useState<'summary' | 'prompts' | 'json'>('summary')
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>('all')
  const [selectedCharacter, setSelectedCharacter] = useState('')
  const [selectedSegmentId, setSelectedSegmentId] = useState('')
  const [segmentState, setSegmentState] = useState<Record<string, SegmentState>>({})
  const [expandedPromptLogId, setExpandedPromptLogId] = useState<string | null>(null)
  const [copiedPromptLogId, setCopiedPromptLogId] = useState<string | null>(null)
  const [promptRetryFeedback, setPromptRetryFeedback] = useState<Record<string, PromptLogRetryResponse>>({})

  const centerRef = useRef<HTMLDivElement>(null)
  const paragraphRefs = useRef<Record<number, HTMLDivElement | null>>({})

  const { data: chapterList, isLoading: listLoading } = useChapters(novelId ?? '')
  const { data: chapter, isLoading: chapterLoading } = useChapter(novelId ?? '', chapterIdx)
  const { data: analysis, isLoading: analysisLoading } = useChapterAnalysis(novelId ?? '', chapterIdx)
  const { data: rewrites, isLoading: rewritesLoading } = useChapterRewrites(novelId ?? '', chapterIdx)
  const reviewRewrite = useReviewRewrite()
  const trajectoryQuery = useCharacterTrajectory(novelId ?? '', selectedCharacter)
  const promptLogsQuery = useQuery({
    queryKey: ['chapter-prompt-logs', novelId, chapterIdx],
    queryFn: () => promptLogsApi.list(novelId!, chapterIdx),
    enabled: Boolean(novelId) && Number.isFinite(chapterIdx),
    staleTime: 30_000,
  })

  const retryPromptLogMutation = useMutation({
    mutationFn: (entry: PromptLogEntry) => promptLogsApi.retry(novelId!, chapterIdx, entry.call_id),
    onSuccess: (result) => {
      setPromptRetryFeedback((prev) => ({ ...prev, [result.call_id]: result }))
    },
  })

  const paragraphs = useMemo(() => getParagraphs(chapter?.content), [chapter?.content])
  const fullChapterText = useMemo(() => chapter?.content?.trim() ?? '', [chapter?.content])
  const rewriteSegments = rewrites ?? []
  const promptLogs = useMemo(
    () => [...(promptLogsQuery.data?.data ?? [])].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
    ),
    [promptLogsQuery.data]
  )

  useEffect(() => {
    if (!bottomExpanded || bottomTab !== 'prompts' || promptLogs.length === 0) return
    if (!expandedPromptLogId || !promptLogs.some((entry) => entry.call_id === expandedPromptLogId)) {
      setExpandedPromptLogId(promptLogs[0].call_id)
    }
  }, [bottomExpanded, bottomTab, expandedPromptLogId, promptLogs])

  useEffect(() => {
    if (viewMode === 'compare') {
      setRightTab('review')
    }
  }, [viewMode])

  useEffect(() => {
    setSelectedCharacter('')
    setSelectedSegmentId('')
    setSegmentState({})
    setReviewFilter('all')
    setBottomExpanded(false)
    setExpandedPromptLogId(null)
    setCopiedPromptLogId(null)
    setPromptRetryFeedback({})
  }, [novelId, chapterIdx])

  useEffect(() => {
    if (!selectedCharacter && analysis?.characters?.[0]?.name) {
      setSelectedCharacter(analysis.characters[0].name)
    }
  }, [analysis, selectedCharacter])

  useEffect(() => {
    if (!rewriteSegments.length) {
      setSelectedSegmentId('')
      return
    }
    if (!selectedSegmentId || !rewriteSegments.some((segment) => segment.segment_id === selectedSegmentId)) {
      setSelectedSegmentId(rewriteSegments[0].segment_id)
    }
  }, [rewriteSegments, selectedSegmentId])

  const selectedSegment = useMemo(
    () => rewriteSegments.find((segment) => segment.segment_id === selectedSegmentId) ?? rewriteSegments[0],
    [rewriteSegments, selectedSegmentId]
  )

  const selectedSegmentState = selectedSegment
    ? (segmentState[selectedSegment.segment_id] ?? segmentDefaultState(selectedSegment))
    : undefined

  const filteredSegments = useMemo(() => {
    const items = rewriteSegments
      .map((segment) => ({ segment, state: segmentState[segment.segment_id] ?? segmentDefaultState(segment) }))
      .sort((a, b) => a.segment.paragraph_range[0] - b.segment.paragraph_range[0])

    return reviewFilter === 'all' ? items : items.filter(({ state }) => state.status === reviewFilter)
  }, [rewriteSegments, reviewFilter, segmentState])

  const reviewStats = useMemo(() => {
    const stats = { total: rewriteSegments.length, pending: 0, accepted: 0, rejected: 0, edited: 0, regenerating: 0 }
    rewriteSegments.forEach((segment) => {
      const state = segmentState[segment.segment_id] ?? segmentDefaultState(segment)
      if (state.status === 'accepted') stats.accepted += 1
      else if (state.status === 'accepted_edited') stats.edited += 1
      else if (state.status === 'rejected') stats.rejected += 1
      else if (state.status === 'regenerating') stats.regenerating += 1
      else stats.pending += 1
    })
    return stats
  }, [rewriteSegments, segmentState])

  const currentCharacterTrajectory = trajectoryQuery.data?.data ?? []

  function getSceneForParagraph(paraIdx: number) {
    if (!analysis?.scenes) return undefined
    return analysis.scenes.find((scene) => paraIdx >= scene.paragraph_range[0] && paraIdx <= scene.paragraph_range[1])
  }

  function scrollToRange(start: number) {
    const el = paragraphRefs.current[start]
    if (el && centerRef.current) {
      centerRef.current.scrollTo({ top: el.offsetTop - 24, behavior: 'smooth' })
    }
  }

  function getSegmentState(segment: RewriteSegment): SegmentState {
    return segmentState[segment.segment_id] ?? segmentDefaultState(segment)
  }

  function updateSegmentState(segmentId: string, patch: Partial<SegmentState>) {
    setSegmentState((prev) => {
      const current = prev[segmentId] ?? { status: 'pending', draftText: '', editing: false, busy: false }
      return { ...prev, [segmentId]: { ...current, ...patch } }
    })
  }

  async function copyPromptLog(entry: PromptLogEntry) {
    try {
      const payload = buildPromptClipboardText(entry)
      await navigator.clipboard.writeText(payload)
      setCopiedPromptLogId(entry.call_id)
      window.setTimeout(() => {
        setCopiedPromptLogId((current) => (current === entry.call_id ? null : current))
      }, 1500)
    } catch (error) {
      console.error('Failed to copy prompt log', error)
    }
  }

  async function retryPromptLog(entry: PromptLogEntry) {
    await retryPromptLogMutation.mutateAsync(entry)
  }

  function selectSegment(segment: RewriteSegment) {
    setSelectedSegmentId(segment.segment_id)
    scrollToRange(segment.paragraph_range[0])
  }

  function runReviewAction(
    segment: RewriteSegment,
    action: 'accept' | 'reject' | 'regenerate' | 'edit',
    manualEditedText?: string
  ) {
    const current = getSegmentState(segment)
    const nextStatus: ReviewStatus =
      action === 'accept' ? 'accepted' : action === 'reject' ? 'rejected' : action === 'regenerate' ? 'regenerating' : 'accepted_edited'

    updateSegmentState(segment.segment_id, {
      status: nextStatus,
      busy: true,
      editing: action === 'edit' ? false : current.editing,
      draftText: manualEditedText ?? current.draftText,
    })

    reviewRewrite.mutate(
      {
        novelId: novelId!,
        chapterIdx,
        segmentId: segment.segment_id,
        action,
        rewritten_text: manualEditedText ?? null,
      },
      {
        onSuccess: () => {
          if (action === 'regenerate') updateSegmentState(segment.segment_id, { status: 'pending' })
          if (action === 'edit' && manualEditedText) {
            updateSegmentState(segment.segment_id, { draftText: manualEditedText, editing: false })
          }
        },
        onError: () => {
          updateSegmentState(segment.segment_id, { status: current.status, editing: current.editing })
        },
        onSettled: () => {
          updateSegmentState(segment.segment_id, { busy: false })
        },
      }
    )
  }

  function renderSegmentCard(segment: RewriteSegment, compact = false) {
    const state = getSegmentState(segment)
    const originalText = getParagraphRangeText(paragraphs, segment.paragraph_range)
    const isSelected = segment.segment_id === selectedSegmentId
    const canEdit = state.editing || state.status === 'accepted_edited'

    return (
      <div
        key={segment.segment_id}
        className={`rounded-2xl border bg-white shadow-xs transition-all duration-150 ${isSelected ? 'border-accent ring-2 ring-accent/15' : 'border-border'}`}
      >
        <button
          type="button"
          onClick={() => selectSegment(segment)}
          className="w-full text-left flex items-start justify-between gap-3 border-b border-border px-4 py-3 cursor-pointer"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-caption px-2 py-0.5 rounded-full bg-subtle text-secondary font-medium">
                P{segment.paragraph_range[0]}-{segment.paragraph_range[1]}
              </span>
              <span className="text-caption px-2 py-0.5 rounded-full bg-subtle text-secondary">
                {segment.source === 'manual' ? '手动' : '自动'}
              </span>
              {segment.confirmed && <span className="text-caption px-2 py-0.5 rounded-full bg-success/10 text-success">已确认</span>}
            </div>
            <p className="mt-1 text-callout font-medium text-primary truncate">
              {segment.scene_type || '未命名分段'}
            </p>
          </div>
          <span className={`shrink-0 text-caption px-2 py-0.5 rounded-full border ${reviewStatusClass(state.status)}`}>
            {reviewStatusLabel(state.status)}
          </span>
        </button>

        {!compact && (
          <div className="grid gap-4 p-4 lg:grid-cols-2">
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-caption font-semibold text-secondary uppercase tracking-wide">
                <FileText className="w-3.5 h-3.5" />
                原文
              </div>
              <div className="rounded-xl bg-subtle p-3 text-body leading-7 text-primary whitespace-pre-wrap min-h-[120px] max-h-[220px] overflow-y-auto">
                {originalText || '暂无原文可显示'}
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-caption font-semibold text-secondary uppercase tracking-wide">
                  <Sparkles className="w-3.5 h-3.5" />
                  改写稿
                </div>
                <button
                  type="button"
                  onClick={() => updateSegmentState(segment.segment_id, { editing: !state.editing })}
                  className="inline-flex items-center gap-1.5 text-caption text-accent hover:underline cursor-pointer"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  {state.editing ? '收起编辑' : '编辑'}
                </button>
              </div>

              {canEdit ? (
                <textarea
                  value={state.draftText}
                  onChange={(event) => updateSegmentState(segment.segment_id, { draftText: event.target.value })}
                  placeholder="在这里输入或修改改写文本，然后保存为 accepted_edited"
                  className="min-h-[160px] w-full rounded-xl border border-border bg-white px-3 py-3 text-body leading-7 text-primary outline-none transition-colors placeholder:text-tertiary focus:border-accent focus:ring-2 focus:ring-accent/10"
                />
              ) : (
                <div className="rounded-xl border border-dashed border-border bg-white px-3 py-3 text-body leading-7 text-secondary min-h-[160px]">
                  点击“编辑”后即可输入改写文本
                </div>
              )}

              {segment.suggestion && (
                <div className="rounded-xl border border-warning/20 bg-warning/10 px-3 py-2 text-caption leading-6 text-primary">
                  <span className="font-semibold text-warning">建议：</span>
                  {segment.suggestion}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 px-4 pb-4">
          <button
            type="button"
            onClick={() => runReviewAction(segment, 'accept')}
            disabled={state.busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-success/20 bg-success/10 px-3 py-1.5 text-caption font-medium text-success transition-colors hover:bg-success/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {state.busy && state.status === 'accepted' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            采纳
          </button>
          <button
            type="button"
            onClick={() => runReviewAction(segment, 'reject')}
            disabled={state.busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-error/20 bg-error/10 px-3 py-1.5 text-caption font-medium text-error transition-colors hover:bg-error/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <X className="w-3.5 h-3.5" />
            拒绝
          </button>
          <button
            type="button"
            onClick={() => runReviewAction(segment, 'regenerate')}
            disabled={state.busy}
            className="inline-flex items-center gap-1.5 rounded-lg border border-warning/20 bg-warning/10 px-3 py-1.5 text-caption font-medium text-warning transition-colors hover:bg-warning/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RotateCw className={`w-3.5 h-3.5 ${state.busy && state.status === 'regenerating' ? 'animate-spin' : ''}`} />
            重写
          </button>
          <button
            type="button"
            onClick={() => updateSegmentState(segment.segment_id, { editing: !state.editing })}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white px-3 py-1.5 text-caption font-medium text-secondary transition-colors hover:text-primary hover:bg-subtle"
          >
            <Pencil className="w-3.5 h-3.5" />
            {state.editing ? '结束编辑' : '编辑'}
          </button>
          <button
            type="button"
            onClick={() => runReviewAction(segment, 'edit', state.draftText.trim())}
            disabled={state.busy || !state.draftText.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-accent/20 bg-accent/10 px-3 py-1.5 text-caption font-medium text-accent transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Save className="w-3.5 h-3.5" />
            保存采纳
          </button>
          {state.busy && (
            <span className="inline-flex items-center gap-1.5 text-caption text-secondary">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              正在同步
            </span>
          )}
        </div>
      </div>
    )
  }

  const rightTabs: Array<{ key: RightTab; label: string }> = [
    { key: 'characters', label: '人物' },
    { key: 'events', label: '事件' },
    { key: 'suggestions', label: '建议' },
    { key: 'review', label: '审核' },
  ]

  const reviewFilters: Array<{ key: ReviewFilter; label: string }> = [
    { key: 'all', label: '全部' },
    { key: 'pending', label: '待处理' },
    { key: 'accepted', label: '已采纳' },
    { key: 'accepted_edited', label: '已编辑' },
    { key: 'rejected', label: '已拒绝' },
    { key: 'regenerating', label: '重写中' },
  ]

  return (
    <div className="-m-8 h-[calc(100%+64px)] flex flex-col bg-white overflow-hidden">
      <div className="flex items-center justify-between gap-4 px-4 py-3 border-b border-border bg-white shrink-0 z-10">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate(`/novels/${novelId}`)}
            className="p-1.5 hover:bg-subtle rounded-lg transition-colors duration-150 cursor-pointer"
          >
            <ArrowLeft className="w-5 h-5 text-secondary" strokeWidth={1.5} />
          </button>
          <div className="min-w-0">
            <h1 className="text-title-3 font-semibold text-primary truncate max-w-[280px] md:max-w-[420px]">
              {chapter?.title ?? (chapterLoading ? '加载中...' : `第 ${chapterId ?? chapterIdx} 章`)}
            </h1>
            <div className="flex items-center gap-2 mt-1 text-caption text-secondary flex-wrap">
              <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-2 py-0.5">
                <Sparkles className="w-3 h-3" />
                {reviewStats.total} 个审核分段
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-2 py-0.5">
                <Clock3 className="w-3 h-3" />
                {reviewStats.pending} 待处理
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-subtle px-2 py-0.5">
                <Check className="w-3 h-3" />
                {reviewStats.accepted} 已采纳
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1 overflow-x-auto">
          {(['scene', 'rewrite', 'compare'] as const).map((mode) => {
            const label = mode === 'scene' ? '场景' : mode === 'rewrite' ? '改写' : '对照'
            return (
              <button
                key={mode}
                onClick={() => setViewMode(mode)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-callout font-medium transition-colors duration-150 cursor-pointer whitespace-nowrap ${
                  viewMode === mode
                    ? 'bg-accent text-white'
                    : 'bg-subtle text-secondary hover:text-primary hover:bg-border'
                }`}
              >
                {label}
                <ChevronDown className="w-3.5 h-3.5" strokeWidth={1.5} />
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden flex-col md:flex-row">
        <div className="w-full md:w-[220px] md:flex-shrink-0 flex flex-col overflow-y-auto border-b md:border-b-0 md:border-r border-border bg-white max-h-40 md:max-h-none">
          <div className="px-3 py-3 border-b border-border shrink-0">
            <span className="text-caption font-semibold text-secondary uppercase tracking-wide">章节</span>
          </div>
          <div className="flex-1 py-2 space-y-0.5">
            {listLoading && Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="mx-2 h-10 rounded-lg bg-subtle animate-pulse" />
            ))}
            {chapterList?.map((ch) => {
              const isCurrent = String(ch.index) === chapterId
              return (
                <button
                  key={ch.id}
                  onClick={() => navigate(`/novels/${novelId}/chapters/${ch.index}`)}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg mx-1 text-left transition-colors duration-150 cursor-pointer ${
                    isCurrent ? 'bg-accent/10 text-accent font-semibold' : 'hover:bg-subtle text-primary'
                  }`}
                  style={{ width: 'calc(100% - 8px)' }}
                >
                  <span className={`text-caption w-5 shrink-0 ${isCurrent ? 'text-accent' : 'text-secondary'}`}>
                    {ch.index + 1}
                  </span>
                  <span className="text-caption flex-1 truncate">{ch.title}</span>
                  <span className="flex gap-0.5 shrink-0">
                    {STAGE_KEYS.map((stage) => {
                      const status = ch.stages?.[stage] ?? 'pending'
                      return <span key={stage} className={`w-1.5 h-1.5 rounded-full ${STAGE_STATUS_COLOR[status] ?? 'bg-border'}`} title={stage} />
                    })}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {viewMode === 'compare' && (
            <div className="flex flex-1 min-h-0 overflow-hidden flex-col xl:grid xl:grid-cols-[1.05fr_0.95fr] divide-y xl:divide-y-0 xl:divide-x divide-border">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="text-caption font-semibold text-secondary uppercase tracking-wide">原文</div>
                  <div className="text-caption text-secondary">点击右侧分段可定位到原文位置</div>
                </div>
                {paragraphs.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-border bg-subtle/30 p-6 text-center text-secondary">暂无章节内容</div>
                )}
                {paragraphs.map((text, idx) => {
                  const scene = getSceneForParagraph(idx + 1)
                  const sceneStyle = scene ? SCENE_COLORS[scene.scene_type] : undefined
                  const selected = selectedSegment
                    ? idx + 1 >= selectedSegment.paragraph_range[0] && idx + 1 <= selectedSegment.paragraph_range[1]
                    : false
                  return (
                    <div
                      key={idx}
                      ref={(el) => { paragraphRefs.current[idx + 1] = el }}
                      onClick={() => {
                        const hit = rewriteSegments.find((segment) => idx + 1 >= segment.paragraph_range[0] && idx + 1 <= segment.paragraph_range[1])
                        if (hit) selectSegment(hit)
                      }}
                      className={`relative mb-3 rounded-2xl px-4 py-3 border-l-4 transition-all duration-200 cursor-pointer ${sceneStyle ? sceneStyle.bg : 'bg-white'} ${selected ? 'ring-2 ring-accent/20' : ''}`}
                      style={sceneStyle ? { borderLeftColor: sceneStyle.border } : { borderLeftColor: 'transparent' }}
                    >
                      {scene && sceneStyle && (
                        <span
                          className="absolute top-2 right-2 text-caption px-2 py-0.5 rounded-full font-medium"
                          style={{
                            backgroundColor: sceneStyle.border + '22',
                            color: sceneStyle.border,
                            border: `1px solid ${sceneStyle.border}44`,
                          }}
                        >
                          {sceneStyle.name}
                        </span>
                      )}
                      <p className="text-body leading-[1.8] text-primary pr-14 whitespace-pre-wrap">{text}</p>
                      {selected && (
                        <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-accent/10 px-2.5 py-1 text-caption text-accent">
                          <Sparkles className="w-3 h-3" />
                          当前选中分段
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="flex-1 overflow-y-auto p-4 md:p-6 bg-subtle/20">
                <div className="flex items-center justify-between gap-3 mb-4">
                  <div>
                    <div className="text-caption font-semibold text-secondary uppercase tracking-wide">改写审核</div>
                    <div className="text-callout text-secondary mt-1">原文对照、文本编辑、采纳/拒绝/重写</div>
                  </div>
                  <div className="text-caption text-secondary">{reviewStats.total} 个分段</div>
                </div>
                {rewritesLoading && Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="mb-4 h-48 rounded-2xl bg-white animate-pulse border border-border" />
                ))}
                {!rewritesLoading && rewriteSegments.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-border bg-white p-6 text-center text-secondary">暂无改写分段，请先完成 mark/rewrite 阶段。</div>
                )}
                <div className="space-y-4">{rewriteSegments.map((segment) => renderSegmentCard(segment))}</div>
              </div>
            </div>
          )}

          {viewMode !== 'compare' && (
            <div ref={centerRef} className="flex-1 overflow-y-auto p-4 md:p-6 space-y-3">
              {(chapterLoading || analysisLoading) && Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="space-y-2">
                  <div className="h-4 rounded bg-subtle animate-pulse w-full" />
                  <div className="h-4 rounded bg-subtle animate-pulse w-5/6" />
                  <div className="h-4 rounded bg-subtle animate-pulse w-4/6" />
                </div>
              ))}

              {!chapterLoading && !chapter && (
                <div className="flex items-center justify-center h-40 text-secondary text-callout">选择一个章节以查看内容</div>
              )}

              {!chapterLoading && viewMode === 'scene' && (
                <div className="space-y-3">
                  <div className="rounded-2xl border border-border bg-page px-4 py-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="rounded-full bg-subtle px-2.5 py-1 text-caption text-secondary">整章全文</span>
                      <span className="rounded-full bg-accent/10 px-2.5 py-1 text-caption text-accent">Analyze 阶段按整章识别</span>
                    </div>
                    <p className="whitespace-pre-wrap text-body leading-[1.8] text-primary">
                      {fullChapterText || '暂无章节内容'}
                    </p>
                  </div>
                </div>
              )}

              {!chapterLoading && viewMode !== 'scene' && paragraphs.map((text, idx) => {
                const scene = getSceneForParagraph(idx + 1)
                const sceneStyle = scene ? SCENE_COLORS[scene.scene_type] : undefined
                const potential = scene?.rewrite_potential
                const isHighPriority = Boolean(potential && potential.priority >= 3)
                const selected = selectedSegment
                  ? idx + 1 >= selectedSegment.paragraph_range[0] && idx + 1 <= selectedSegment.paragraph_range[1]
                  : false

                if (viewMode === 'rewrite' && !potential?.expandable && !potential?.rewritable) {
                  return <p key={idx} className={`text-body leading-[1.8] px-4 ${selected ? 'text-primary' : 'text-tertiary'}`}>{text}</p>
                }

                return (
                  <div
                    key={idx}
                    ref={(el) => { paragraphRefs.current[idx + 1] = el }}
                    onClick={() => {
                      const hit = rewriteSegments.find((segment) => idx + 1 >= segment.paragraph_range[0] && idx + 1 <= segment.paragraph_range[1])
                      if (hit) selectSegment(hit)
                    }}
                    className={`relative rounded-md px-4 py-3 border-l-4 transition-all duration-200 cursor-pointer ${sceneStyle ? sceneStyle.bg : 'bg-white'} ${isHighPriority ? 'ring-1 ring-warning/50' : ''} ${selected ? 'ring-2 ring-accent/20' : ''}`}
                    style={sceneStyle ? { borderLeftColor: sceneStyle.border } : { borderLeftColor: 'transparent' }}
                  >
                    {scene && sceneStyle && (
                      <span
                        className="absolute top-2 right-2 text-caption px-2 py-0.5 rounded-full font-medium"
                        style={{
                          backgroundColor: sceneStyle.border + '22',
                          color: sceneStyle.border,
                          border: `1px solid ${sceneStyle.border}44`,
                        }}
                      >
                        {sceneStyle.name}
                      </span>
                    )}
                    <p className="text-body leading-[1.8] text-primary pr-14 whitespace-pre-wrap">{text}</p>
                    {selected && (
                      <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-accent/10 px-2.5 py-1 text-caption text-accent">
                        <Sparkles className="w-3 h-3" />
                        当前选中分段
                      </div>
                    )}
                    {isHighPriority && potential?.suggestion && (
                      <div className="mt-2 flex items-start gap-2 p-2 rounded-md bg-warning/10 border border-warning/20">
                        <span className="text-caption text-warning font-medium shrink-0">建议</span>
                        <span className="text-caption text-primary">{potential.suggestion}</span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          <div className="flex-shrink-0 border-t border-border bg-white transition-all duration-300 ease-out overflow-hidden" style={{ height: bottomExpanded ? '280px' : '40px' }}>
            <div className="flex items-center h-10 px-4 border-b border-border">
              <div className="flex gap-1 flex-1 overflow-x-auto">
                {(['summary', 'prompts', 'json'] as const).map((tab) => {
                  const label = tab === 'summary' ? '摘要' : tab === 'prompts' ? 'Prompt日志' : '分析JSON'
                  return (
                    <button
                      key={tab}
                      onClick={() => {
                        setBottomTab(tab)
                        if (!bottomExpanded) setBottomExpanded(true)
                      }}
                      className={`px-3 py-1 text-caption rounded transition-colors duration-150 cursor-pointer whitespace-nowrap ${bottomTab === tab && bottomExpanded ? 'bg-accent/10 text-accent font-medium' : 'text-secondary hover:text-primary'}`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
              <button
                onClick={() => setBottomExpanded(!bottomExpanded)}
                className="p-1 rounded hover:bg-subtle transition-colors duration-150 cursor-pointer text-secondary"
              >
                {bottomExpanded ? <ChevronDown className="w-4 h-4" strokeWidth={1.5} /> : <ChevronUp className="w-4 h-4" strokeWidth={1.5} />}
              </button>
            </div>

            {bottomExpanded && (
              <div className="h-[240px] overflow-y-auto p-4">
                {bottomTab === 'summary' && <p className="text-body leading-[1.8] text-primary whitespace-pre-wrap">{analysis?.summary ?? (analysisLoading ? '加载中...' : '暂无摘要')}</p>}
                {bottomTab === 'prompts' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-callout font-medium text-primary">Prompt 时间线</p>
                        <p className="text-caption text-secondary">{promptLogs.length} 条调用记录（最新在前）</p>
                      </div>
                      {promptLogsQuery.isFetching && <Loader2 className="h-4 w-4 animate-spin text-secondary" />}
                    </div>

                    {promptLogsQuery.isLoading && (
                      <div className="space-y-2">
                        {Array.from({ length: 3 }).map((_, index) => (
                          <div key={index} className="h-20 rounded-2xl bg-subtle animate-pulse" />
                        ))}
                      </div>
                    )}

                    {promptLogsQuery.isError && (
                      <div className="rounded-2xl border border-error/20 bg-error/5 px-4 py-3 text-callout text-error">
                        Prompt 日志加载失败，请稍后重试。
                      </div>
                    )}

                    {!promptLogsQuery.isLoading && !promptLogsQuery.isError && promptLogs.length === 0 && (
                      <div className="rounded-2xl border border-dashed border-border bg-subtle/20 px-4 py-8 text-center">
                        <p className="text-callout text-secondary">当前章节还没有 Prompt 日志</p>
                        <p className="mt-1 text-caption text-tertiary">完成分析或改写后，时间线会自动显示。</p>
                      </div>
                    )}

                    {promptLogs.map((entry) => {
                      const promptLayout = detectPromptLayout(entry)
                      const expanded = expandedPromptLogId === entry.call_id
                      const copied = copiedPromptLogId === entry.call_id
                      const retryResult = promptRetryFeedback[entry.call_id]
                      const retryPending = retryPromptLogMutation.isPending && retryPromptLogMutation.variables?.call_id === entry.call_id
                      return (
                        <div key={entry.call_id} className="rounded-2xl border border-border bg-white shadow-xs overflow-hidden">
                          <button
                            type="button"
                            onClick={() => setExpandedPromptLogId((current) => (current === entry.call_id ? null : entry.call_id))}
                            className="flex w-full items-start justify-between gap-4 px-4 py-3 text-left hover:bg-subtle/40 transition-colors"
                          >
                            <div className="min-w-0 space-y-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="rounded-full bg-accent/10 px-2 py-0.5 text-caption font-medium text-accent">
                                  {entry.stage}
                                </span>
                                <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                  {entry.provider}
                                </span>
                                {entry.model_name && (
                                  <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                    {entry.model_name}
                                  </span>
                                )}
                                {entry.validation.passed === false && (
                                  <span className="rounded-full bg-error/10 px-2 py-0.5 text-caption text-error">
                                    {entry.validation.error_code ?? '校验失败'}
                                  </span>
                                )}
                                {entry.validation.passed === true && (
                                  <span className="rounded-full bg-success/10 px-2 py-0.5 text-caption text-success">
                                    校验通过
                                  </span>
                                )}
                                <span className={`rounded-full px-2 py-0.5 text-caption ${promptLayout.layout === 'legacy' ? 'bg-warning/10 text-warning' : promptLayout.layout === 'current' ? 'bg-success/10 text-success' : 'bg-subtle text-secondary'}`}>
                                  {promptLayout.layout === 'legacy'
                                    ? '旧结构：规则在 System'
                                    : promptLayout.layout === 'current'
                                      ? '新结构：规则在 User'
                                      : promptLayout.layout === 'mixed'
                                        ? '混合结构'
                                        : '结构未知'}
                                </span>
                                {promptLayout.userHasWholeChapterDirective && (
                                  <span className="rounded-full bg-accent/10 px-2 py-0.5 text-caption text-accent">
                                    整章识别指令
                                  </span>
                                )}
                              </div>
                              <p className="text-callout font-medium text-primary">
                                {formatPromptLogTokens(entry.tokens)} · {entry.attempt} 次尝试
                              </p>
                              <p className="text-caption text-secondary">
                                {formatPromptLogTime(entry.timestamp)} · {entry.duration_ms}ms
                              </p>
                            </div>
                            <div className="flex flex-shrink-0 items-center gap-2">
                              <span className="rounded-full bg-subtle px-2 py-0.5 text-caption text-secondary">
                                {entry.call_id.slice(0, 8)}
                              </span>
                              <ChevronDown className={`h-4 w-4 text-secondary transition-transform ${expanded ? 'rotate-180' : ''}`} />
                            </div>
                          </button>

                          {expanded && (
                            <div className="border-t border-border bg-subtle/20 p-4 space-y-4">
                              <div className="grid gap-3 xl:grid-cols-2">
                                <div className="space-y-2">
                                  <div className="text-caption font-semibold uppercase tracking-wide text-secondary">System Prompt</div>
                                  <pre className="max-h-44 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-white p-3 text-caption text-primary">
                                    {entry.system_prompt}
                                  </pre>
                                </div>
                                <div className="space-y-2">
                                  <div className="text-caption font-semibold uppercase tracking-wide text-secondary">Task Prompt</div>
                                  <pre className="max-h-44 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-white p-3 text-caption text-primary">
                                    {entry.user_prompt}
                                  </pre>
                                </div>
                              </div>

                              <div className="grid gap-3 xl:grid-cols-2">
                                <div className="space-y-2">
                                  <div className="text-caption font-semibold uppercase tracking-wide text-secondary">Response</div>
                                  <pre className="max-h-44 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-white p-3 text-caption text-primary">
                                    {prettyPrintPromptPayload(entry.response)}
                                  </pre>
                                </div>
                                <div className="space-y-2">
                                  <div className="text-caption font-semibold uppercase tracking-wide text-secondary">Validation / Params</div>
                                  <div className="rounded-xl border border-border bg-white p-3 space-y-3">
                                    <div className="space-y-1">
                                      <p className="text-caption text-secondary">Validation</p>
                                      <pre className="whitespace-pre-wrap text-caption text-primary">
                                        {prettyPrintPromptPayload(entry.validation)}
                                      </pre>
                                    </div>
                                    <div className="space-y-1">
                                      <p className="text-caption text-secondary">Params</p>
                                      <pre className="whitespace-pre-wrap text-caption text-primary">
                                        {prettyPrintPromptPayload(entry.params)}
                                      </pre>
                                    </div>
                                  </div>
                                </div>
                              </div>

                              <div className="flex flex-wrap items-center gap-2">
                                <button
                                  type="button"
                                  onClick={() => copyPromptLog(entry)}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white px-3 py-1.5 text-caption font-medium text-secondary transition-colors hover:border-accent hover:text-accent"
                                >
                                  <Copy className="h-3.5 w-3.5" />
                                  {copied ? '已复制' : '复制 Prompt'}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => retryPromptLog(entry)}
                                  disabled={retryPending}
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-accent/20 bg-accent/10 px-3 py-1.5 text-caption font-medium text-accent transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  {retryPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
                                  使用此 Prompt 重试
                                </button>
                                {retryResult && (
                                  <span className="rounded-full bg-success/10 px-3 py-1 text-caption text-success">
                                    {retryResult.message}
                                  </span>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
                {bottomTab === 'json' && <pre className="font-mono text-caption text-primary whitespace-pre-wrap break-all">{JSON.stringify(analysis, null, 2)}</pre>}
              </div>
            )}
          </div>
        </div>

        <div className="w-full md:w-[360px] md:flex-shrink-0 flex flex-col border-t md:border-t-0 md:border-l border-border bg-white min-h-0">
          <div className="flex border-b border-border shrink-0 overflow-x-auto">
            {rightTabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setRightTab(tab.key)}
                className={`flex-1 min-w-[72px] py-3 text-callout font-medium transition-colors duration-150 cursor-pointer whitespace-nowrap ${rightTab === tab.key ? 'text-accent border-b-2 border-accent' : 'text-secondary hover:text-primary'}`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-3">
            {rightTab === 'characters' && (
              <>
                {analysisLoading && Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-20 rounded-xl bg-subtle animate-pulse" />)}
                {!analysisLoading && (!analysis?.characters || analysis.characters.length === 0) && <p className="text-callout text-secondary text-center py-8">暂无人物分析</p>}
                {analysis?.characters?.map((char, i) => {
                  const active = selectedCharacter === char.name
                  return (
                    <button
                      key={i}
                      onClick={() => setSelectedCharacter(char.name)}
                      className={`w-full text-left bg-white rounded-xl p-4 shadow-xs border transition-colors ${active ? 'border-accent ring-2 ring-accent/15' : 'border-border'}`}
                    >
                      <div className="flex items-center justify-between mb-2 gap-2">
                        <span className="text-title-3 font-semibold text-primary">{char.name}</span>
                        <span className={`text-caption px-2 py-0.5 rounded-full font-medium ${emotionColor(char.emotion)}`}>{char.emotion}</span>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-callout text-secondary text-left">{char.state}</span>
                        <span className="text-caption px-2 py-0.5 rounded-md bg-subtle text-secondary">{char.role_in_chapter}</span>
                      </div>
                    </button>
                  )
                })}

                {selectedCharacter && (
                  <div className="space-y-2 rounded-2xl border border-border bg-subtle/20 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 text-caption font-semibold text-secondary uppercase tracking-wide">
                        <Users className="w-3.5 h-3.5" />
                        角色轨迹
                      </div>
                      {trajectoryQuery.isLoading && <Loader2 className="w-3.5 h-3.5 animate-spin text-secondary" />}
                    </div>
                    {!trajectoryQuery.isLoading && currentCharacterTrajectory.length === 0 && <p className="text-caption text-secondary">暂无轨迹数据</p>}
                    <div className="space-y-2">
                      {currentCharacterTrajectory.map((item, index) => (
                        <div key={`${item.chapter_index}-${index}`} className="rounded-xl bg-white border border-border p-3">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-caption px-2 py-0.5 rounded-full bg-accent/10 text-accent">第 {item.chapter_index} 章</span>
                            {item.paragraph_range && <span className="text-caption text-secondary">P{item.paragraph_range[0]}-{item.paragraph_range[1]}</span>}
                          </div>
                          <p className="mt-2 text-callout text-primary leading-6 whitespace-pre-wrap">{item.summary ?? item.state ?? '暂无说明'}</p>
                          <div className="mt-2 flex items-center gap-2 flex-wrap text-caption text-secondary">
                            {item.emotion && <span className="rounded-full bg-subtle px-2 py-0.5">{item.emotion}</span>}
                            {item.role_in_chapter && <span className="rounded-full bg-subtle px-2 py-0.5">{item.role_in_chapter}</span>}
                            {item.scene_type && <span className="rounded-full bg-subtle px-2 py-0.5">{item.scene_type}</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            {rightTab === 'events' && (
              <>
                {analysisLoading && Array.from({ length: 4 }).map((_, i) => <div key={i} className="h-16 rounded-xl bg-subtle animate-pulse" />)}
                {!analysisLoading && (!analysis?.key_events || analysis.key_events.length === 0) && <p className="text-callout text-secondary text-center py-8">暂无事件记录</p>}
                <div className="relative pl-4">
                  <div className="absolute left-2 top-2 bottom-2 w-px bg-border" />
                  <div className="space-y-3">
                    {analysis?.key_events?.map((event, i) => {
                      const dotColor = event.importance >= 4 ? 'bg-warning' : event.importance === 3 ? 'bg-accent' : 'bg-tertiary'
                      return (
                        <button key={i} onClick={() => scrollToRange(event.paragraph_range[0])} className="w-full text-left flex items-start gap-3 cursor-pointer">
                          <span className={`mt-1.5 w-2.5 h-2.5 rounded-full shrink-0 ${dotColor} relative z-10 ring-2 ring-white`} />
                          <div className="flex-1 p-2 rounded-lg hover:bg-subtle transition-colors duration-150">
                            <p className="text-callout text-primary leading-snug">{event.description}</p>
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-caption text-secondary">{event.event_type}</span>
                              <span className="text-caption text-tertiary">P{event.paragraph_range[0]}–{event.paragraph_range[1]}</span>
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </>
            )}

            {rightTab === 'suggestions' && (
              <>
                {analysisLoading && Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-24 rounded-xl bg-subtle animate-pulse" />)}
                {!analysisLoading && (!analysis?.scenes || analysis.scenes.every((scene) => !scene.rewrite_potential?.expandable && !scene.rewrite_potential?.rewritable)) && <p className="text-callout text-secondary text-center py-8">暂无改写建议</p>}
                {analysis?.scenes
                  ?.filter((scene) => scene.rewrite_potential?.expandable || scene.rewrite_potential?.rewritable)
                  .sort((a, b) => (b.rewrite_potential?.priority ?? 0) - (a.rewrite_potential?.priority ?? 0))
                  .map((scene, i) => {
                    const p = scene.rewrite_potential
                    const sceneStyle = SCENE_COLORS[scene.scene_type]
                    return (
                      <div key={i} className="bg-white rounded-xl p-4 shadow-xs border border-border space-y-2">
                        <div className="flex items-center justify-between">
                          {sceneStyle ? (
                            <span className="text-caption px-2 py-0.5 rounded-full" style={{ backgroundColor: sceneStyle.border + '22', color: sceneStyle.border }}>
                              {sceneStyle.name}
                            </span>
                          ) : <span />}
                          <PriorityStars priority={p?.priority ?? 0} />
                        </div>
                        <p className="text-callout text-primary">{p?.suggestion}</p>
                        <div className="flex items-center justify-between">
                          <span className="text-caption text-tertiary">P{scene.paragraph_range[0]}–{scene.paragraph_range[1]}</span>
                          <button onClick={() => scrollToRange(scene.paragraph_range[0])} className="text-caption text-accent hover:underline cursor-pointer">
                            标记改写
                          </button>
                        </div>
                      </div>
                    )
                  })}
              </>
            )}

            {rightTab === 'review' && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-2">
                  <div className="rounded-xl bg-subtle p-3">
                    <div className="text-caption text-secondary">总分段</div>
                    <div className="mt-1 text-title-3 font-semibold text-primary">{reviewStats.total}</div>
                  </div>
                  <div className="rounded-xl bg-subtle p-3">
                    <div className="text-caption text-secondary">待处理</div>
                    <div className="mt-1 text-title-3 font-semibold text-primary">{reviewStats.pending}</div>
                  </div>
                  <div className="rounded-xl bg-subtle p-3">
                    <div className="text-caption text-secondary">已采纳</div>
                    <div className="mt-1 text-title-3 font-semibold text-primary">{reviewStats.accepted}</div>
                  </div>
                  <div className="rounded-xl bg-subtle p-3">
                    <div className="text-caption text-secondary">已编辑</div>
                    <div className="mt-1 text-title-3 font-semibold text-primary">{reviewStats.edited}</div>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {reviewFilters.map((filter) => (
                    <button
                      key={filter.key}
                      onClick={() => setReviewFilter(filter.key)}
                      className={`rounded-full px-3 py-1.5 text-caption font-medium transition-colors ${reviewFilter === filter.key ? 'bg-accent text-white' : 'bg-subtle text-secondary hover:text-primary'}`}
                    >
                      {filter.label}
                    </button>
                  ))}
                </div>

                {rewritesLoading && Array.from({ length: 3 }).map((_, i) => <div key={i} className="h-24 rounded-xl bg-subtle animate-pulse" />)}
                {!rewritesLoading && filteredSegments.length === 0 && <p className="text-callout text-secondary text-center py-8">暂无可显示的审核分段</p>}

                <div className="space-y-3">
                  {filteredSegments.map(({ segment, state }) => {
                    const selected = segment.segment_id === selectedSegmentId
                    const shortText = getParagraphRangeText(paragraphs, segment.paragraph_range)
                    return (
                      <button
                        key={segment.segment_id}
                        onClick={() => selectSegment(segment)}
                        className={`w-full text-left rounded-2xl border bg-white p-3 transition-colors ${selected ? 'border-accent ring-2 ring-accent/15' : 'border-border hover:border-accent/40'}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-caption px-2 py-0.5 rounded-full bg-subtle text-secondary">P{segment.paragraph_range[0]}-{segment.paragraph_range[1]}</span>
                              <span className="text-caption px-2 py-0.5 rounded-full bg-subtle text-secondary">{segment.source === 'manual' ? '手动' : '自动'}</span>
                            </div>
                            <p className="mt-1 text-callout font-medium text-primary truncate max-w-[220px]">{segment.scene_type || '未命名分段'}</p>
                          </div>
                          <span className={`shrink-0 text-caption px-2 py-0.5 rounded-full border ${reviewStatusClass(state.status)}`}>
                            {reviewStatusLabel(state.status)}
                          </span>
                        </div>
                        <p className="mt-2 text-caption leading-6 text-secondary line-clamp-3 whitespace-pre-wrap">
                          {shortText || segment.suggestion || '暂无原文'}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <span className="inline-flex items-center gap-1 rounded-lg bg-success/10 px-2 py-1 text-caption text-success"><Check className="w-3 h-3" />采纳</span>
                          <span className="inline-flex items-center gap-1 rounded-lg bg-error/10 px-2 py-1 text-caption text-error"><X className="w-3 h-3" />拒绝</span>
                          <span className="inline-flex items-center gap-1 rounded-lg bg-warning/10 px-2 py-1 text-caption text-warning"><RotateCw className="w-3 h-3" />重写</span>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="pointer-events-none fixed bottom-4 right-4 hidden xl:block">
        {selectedSegment && selectedSegmentState && (
          <div className="pointer-events-auto max-w-[320px] rounded-2xl border border-border bg-white/95 backdrop-blur shadow-lg p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-caption font-semibold text-secondary uppercase tracking-wide">当前分段</div>
              <span className={`text-caption px-2 py-0.5 rounded-full border ${reviewStatusClass(selectedSegmentState.status)}`}>
                {reviewStatusLabel(selectedSegmentState.status)}
              </span>
            </div>
            <div className="mt-2 text-callout font-medium text-primary">
              P{selectedSegment.paragraph_range[0]}-{selectedSegment.paragraph_range[1]} · {selectedSegment.scene_type || '未命名分段'}
            </div>
            <p className="mt-2 text-caption leading-6 text-secondary whitespace-pre-wrap max-h-24 overflow-y-auto">
              {selectedSegmentState.draftText || selectedSegment.suggestion || '尚未填写改写稿'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
