import { apiFetch } from '@/lib/api'
import type { StageName, StageStatus } from '@/types'

export interface StageRunConfigSnapshot {
  provider_id?: string | null
  provider_name?: string | null
  provider_type?: string | null
  model_name?: string | null
  base_url?: string | null
  global_prompt_version?: string | null
  scene_rules_hash?: string | null
  rewrite_rules_hash?: string | null
  generation_params?: Record<string, unknown>
  captured_at?: string | null
}

export interface StageRunDetail {
  id: string
  run_seq: number
  stage: StageName
  status: StageStatus
  started_at?: string | null
  completed_at?: string | null
  error_message?: string | null
  run_idempotency_key?: string | null
  warnings_count: number
  chapters_total: number
  chapters_done: number
  config_snapshot?: StageRunConfigSnapshot | null
  artifact_path?: string | null
  is_latest: boolean
}

export interface StageRunDetailResponse {
  novel_id: string
  stage: StageName
  run: StageRunDetail | null
}

export interface StageArtifactResponse {
  novel_id: string
  stage: StageName
  format: string
  run_seq: number
  run: StageRunDetail
  artifact_path: string
  latest_artifact_path: string
  artifact: Record<string, unknown> | null
  latest_artifact: Record<string, unknown> | null
}

export interface QualityThresholdComparison {
  label: string
  actual: number
  threshold: number
  comparator: '>' | '>=' | '<' | '<='
  unit?: string
  status: 'ok' | 'warning' | 'blocked'
  suggestion?: string
}

export interface QualityReportWarning {
  code: string
  message: string
  chapter_index?: number | null
  segment_id?: string | null
  paragraph_range?: [number, number] | null
  details?: Record<string, unknown>
}

export interface QualityReportPayload {
  thresholds: Record<string, number>
  stats: {
    original_chars: number
    final_chars: number
    rewritten_segments: number
    preserved_segments: number
    failed_segments: number
    failed_ratio: number
    warning_count: number
  }
  warnings: QualityReportWarning[]
  blocked: boolean
  block_reasons: string[]
  allow_force_export: boolean
  risk_signature?: Record<string, unknown> | null
}

export interface QualityReportView {
  thresholdComparisons: QualityThresholdComparison[]
  warnings: QualityReportWarning[]
  blocked: boolean
  blockReasons: string[]
  allowForceExport: boolean
  riskSignature?: Record<string, unknown> | null
  stats: QualityReportPayload['stats']
}

export interface RewriteCoverageChapter {
  chapterIndex: number
  chapterTitle: string
  totalSegments: number
  rewrittenSegments: number
  preservedSegments: number
  failedSegments: number
  rollbackSegments: number
  failureCodes: string[]
  warningCodes: string[]
}

export interface RewriteCoverageSummary {
  rewrittenSegments: number
  preservedSegments: number
  failedSegments: number
  rollbackSegments: number
  chapters: RewriteCoverageChapter[]
}

export interface RewriteCoverageItem {
  status?: string | null
  error_code?: string | null
}

export function fetchStageRunDetail(novelId: string, stage: StageName, runSeq: number) {
  return apiFetch<StageRunDetailResponse>(`/novels/${novelId}/stages/${stage}/runs/${runSeq}`)
}

export function fetchStageArtifact(novelId: string, stage: StageName, runSeq?: number) {
  const query = runSeq ? `?run_seq=${encodeURIComponent(String(runSeq))}` : ''
  return apiFetch<StageArtifactResponse>(`/novels/${novelId}/stages/${stage}/artifact${query}`)
}

export function fetchQualityReport(novelId: string, taskId?: string | null) {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''
  return apiFetch<QualityReportPayload>(`/novels/${novelId}/quality-report${query}`)
}

function pickThresholdComparison(
  label: string,
  actual: number,
  threshold: number,
  comparator: '>' | '>=' | '<' | '<=',
  unit?: string,
  suggestion?: string
): QualityThresholdComparison {
  const blocked = comparator === '>' || comparator === '>=' ? actual > threshold : actual < threshold
  return {
    label,
    actual,
    threshold,
    comparator,
    unit,
    status: blocked ? 'blocked' : actual === threshold ? 'warning' : 'ok',
    suggestion,
  }
}

export function normalizeQualityReport(report: QualityReportPayload | null | undefined): QualityReportView | null {
  if (!report) return null

  const failedThreshold = Number(report.thresholds.max_failed_ratio ?? 0.25)
  const warningThreshold = Number(report.thresholds.max_warning_count ?? 5)
  const failedRatio = Number(report.stats.failed_ratio ?? 0)
  const warningCount = Number(report.stats.warning_count ?? 0)

  return {
    thresholdComparisons: [
      pickThresholdComparison('quality.failedRatio', failedRatio, failedThreshold, '<=', 'ratio', 'quality.failedRatioSuggestion'),
      pickThresholdComparison('quality.warningCount', warningCount, warningThreshold, '<=', 'count', 'quality.warningCountSuggestion'),
    ],
    warnings: report.warnings ?? [],
    blocked: report.blocked,
    blockReasons: report.block_reasons ?? [],
    allowForceExport: report.allow_force_export,
    riskSignature: report.risk_signature ?? null,
    stats: report.stats,
  }
}

export function summarizeRewriteCoverage(
  chapters: Array<{ chapterIndex: number; chapterTitle: string; rewrites: RewriteCoverageItem[] }>
): RewriteCoverageSummary {
  const allowed = new Set(['completed', 'accepted', 'accepted_edited'])

  const summary = chapters.map((chapter) => {
    const rewrittenSegments = chapter.rewrites.filter((item) => allowed.has(item.status ?? '')).length
    const preservedSegments = chapter.rewrites.filter((item) => item.status === 'rejected').length
    const failedSegments = chapter.rewrites.filter((item) => item.status === 'failed').length
    const rollbackSegments = chapter.rewrites.filter((item) => item.status === 'pending' || item.status === 'rejected').length

    return {
      chapterIndex: chapter.chapterIndex,
      chapterTitle: chapter.chapterTitle,
      totalSegments: chapter.rewrites.length,
      rewrittenSegments,
      preservedSegments,
      failedSegments,
      rollbackSegments,
      failureCodes: chapter.rewrites.filter((item) => item.status === 'failed' && item.error_code).map((item) => item.error_code as string),
      warningCodes: chapter.rewrites.filter((item) => item.error_code && item.status !== 'failed').map((item) => item.error_code as string),
    }
  })

  return summary.reduce<RewriteCoverageSummary>(
    (acc, item) => {
      acc.rewrittenSegments += item.rewrittenSegments
      acc.preservedSegments += item.preservedSegments
      acc.failedSegments += item.failedSegments
      acc.rollbackSegments += item.rollbackSegments
      acc.chapters.push(item)
      return acc
    },
    { rewrittenSegments: 0, preservedSegments: 0, failedSegments: 0, rollbackSegments: 0, chapters: [] }
  )
}
