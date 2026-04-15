// ========== Core Domain Types ==========

// Novel Management
export interface Novel {
  id: string
  title: string
  original_filename: string
  file_format: 'txt' | 'epub'
  file_size: number
  total_chars: number
  imported_at: string
  chapter_count?: number
  config_override_json?: string
  task_id?: string | null
  active_task_id?: string | null
  pipeline_status?: Record<string, StageRunInfo>
}

export interface NovelDetail extends Novel {
  task_id?: string | null
  active_task_id?: string | null
  pipeline_status: Record<string, StageRunInfo>
}

export interface ImportResult {
  novel_id: string
  title: string
  total_chars: number
  chapters_detected: number
  format: 'txt' | 'epub'
}

// Chapter Management
export interface Chapter {
  index: number
  title: string
  char_count: number
  paragraph_count: number
  paragraphs: Paragraph[]
}

export interface Paragraph {
  index: number
  start_offset: number
  end_offset: number
  char_count: number
}

export interface ChapterAnalysis {
  summary: string
  characters: CharacterState[]
  key_events: KeyEvent[]
  scenes: SceneSegment[]
  location: string
  tone: string
}

export interface CharacterState {
  name: string
  emotion: string
  state: string
  role_in_chapter: string
}

export interface KeyEvent {
  description: string
  event_type: string
  importance: number // 1-5
  paragraph_range: [number, number]
}

export interface SceneSegment {
  scene_type: string
  paragraph_range: [number, number]
  rewrite_potential: RewritePotential
  rule_hits?: SceneRuleHit[]
}

export interface RewritePotential {
  expandable: boolean
  rewritable: boolean
  suggestion: string
  priority: number // 1-5
}

export interface SceneRuleHit {
  trigger_condition: string
  evidence_text: string
}

// Rewrite Management
export type RewriteStrategy = 'expand' | 'rewrite' | 'condense' | 'preserve'
export type SentenceBoundaryKind = 'terminal' | 'newline' | 'fallback'
export type WindowGuardrailLevel = 'info' | 'warning' | 'hard_fail'
export type WindowAttemptAction = 'accepted' | 'retry' | 'rollback_original'

export interface RewriteWindow {
  window_id: string
  segment_id: string
  chapter_index: number
  start_offset: number
  end_offset: number
  hit_sentence_range?: [number, number] | null
  context_sentence_range?: [number, number] | null
  target_chars: number
  target_chars_min: number
  target_chars_max: number
  source_fingerprint?: string | null
  plan_version?: string | null
}

export interface WindowGuardrail {
  level: WindowGuardrailLevel
  codes: string[]
  details?: Record<string, unknown>
}

export interface WindowAttempt {
  window_id: string
  attempt_seq: number
  run_seq?: number | null
  provider_id?: string | null
  model_name?: string | null
  finish_reason?: string | null
  raw_response_ref?: string | null
  guardrail?: WindowGuardrail | null
  action: WindowAttemptAction
}

export interface RewritePlan {
  novel_id: string
  created_at: string
  total_marked: number
  estimated_llm_calls: number
  estimated_added_chars: number
  chapters: ChapterRewritePlan[]
}

export interface ChapterRewritePlan {
  chapter_index: number
  segments: RewriteSegment[]
}

export interface RewriteSegment {
  segment_id: string
  paragraph_range: [number, number]
  sentence_range?: [number, number] | null
  char_offset_range?: [number, number] | null
  scene_type: string
  original_chars: number
  strategy: RewriteStrategy
  target_ratio: number
  target_chars: number
  target_chars_min: number
  target_chars_max: number
  rewrite_windows?: RewriteWindow[]
  source_fingerprint?: string | null
  plan_version?: string | null
  suggestion: string
  source: 'auto' | 'manual'
  confirmed: boolean
  manual_edited_text?: string | null
}

export type RewriteResultStatus =
  | 'pending'
  | 'completed'
  | 'accepted'
  | 'accepted_edited'
  | 'rejected'
  | 'failed'

export interface RewriteResult {
  segment_id: string
  chapter_index: number
  paragraph_range: [number, number]
  scene_type?: string | null
  suggestion?: string | null
  target_ratio?: number | null
  target_chars?: number | null
  target_chars_min?: number | null
  target_chars_max?: number | null
  char_offset_range?: [number, number] | null
  rewrite_windows?: RewriteWindow[]
  window_attempts?: WindowAttempt[]
  completion_kind?: 'normal' | 'noop'
  reason_code?: string | null
  has_warnings?: boolean
  warning_count?: number
  warning_codes?: string[]
  anchor_verified?: boolean
  strategy: RewriteStrategy
  original_text: string
  rewritten_text: string
  original_chars: number
  rewritten_chars: number
  actual_chars?: number | null
  status: RewriteResultStatus
  attempts: number
  provider_used?: string | null
  error_code?: string | null
  error_detail?: string | null
  provider_raw_response?: Record<string, unknown> | null
  validation_details?: Record<string, unknown> | null
  manual_edited_text?: string | null
}

export interface ChapterRewriteResults {
  chapter_index: number
  segments: RewriteResult[]
}

export type RewriteReviewAction = 'accept' | 'reject' | 'regenerate' | 'edit'

export interface RewriteReviewRequest {
  action: RewriteReviewAction
  rewritten_text?: string | null
  note?: string | null
}

export interface RewriteReviewResponse {
  status: RewriteResultStatus | 'updated'
  segment_id: string
}

export type ChapterMarkMode = 'merge' | 'replace'

export interface ChapterMarkRequest {
  mode: ChapterMarkMode
  segments: RewriteSegment[]
}

export interface ChapterMarkResponse {
  status: string
  chapter_idx: number
  total_marked: number
}

export interface CharacterTrajectoryItem {
  chapter_index: number
  chapter_title?: string | null
  paragraph_range?: [number, number] | null
  scene_type?: string | null
  summary?: string | null
  emotion?: string | null
  state?: string | null
  role_in_chapter?: string | null
  anchor_verified?: boolean | null
}

export interface CharacterTrajectoryResponse {
  novel_id: string
  task_id: string
  character_name: string
  total: number
  data: CharacterTrajectoryItem[]
}

// Stage Management
export type StageName = 'import' | 'split' | 'analyze' | 'mark' | 'rewrite' | 'assemble'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused' | 'stale'

export interface StageRunInfo {
  id: string
  stage: StageName
  status: StageStatus
  run_seq?: number
  task_id?: string
  started_at?: string
  completed_at?: string
  error_message?: string
  warnings_count?: number
  artifact_path?: string
  config_snapshot?: StageConfigSnapshot
  chapters_total: number
  chapters_done: number
}

export interface StageConfigSnapshot {
  provider_id?: string | null
  provider_name?: string | null
  provider_type?: ProviderType | null
  model_name?: string | null
  base_url?: string | null
  global_prompt_version?: string | null
  scene_rules_hash?: string | null
  rewrite_rules_hash?: string | null
  generation_params?: Record<string, unknown>
  rewrite_window_mode?: {
    enabled: boolean
    guardrail_enabled: boolean
    audit_enabled: boolean
    source?: string | null
  }
  captured_at?: string
}

export interface StageProgress {
  chapters_done: number
  chapters_total: number
  percentage: number
}

// Provider Management
export type ProviderType = 'openai' | 'openai_compatible'

export interface Provider {
  id: string
  name: string
  provider_type: ProviderType
  api_key_masked?: string
  base_url: string
  model_name: string
  temperature: number
  max_tokens: number
  top_p?: number | null
  presence_penalty?: number | null
  frequency_penalty?: number | null
  rpm_limit: number
  tpm_limit: number
  is_active: boolean
  created_at: string
}

export interface ProviderTestResult {
  status: 'success' | 'failed'
  success: boolean
  latency_ms?: number | null
  error?: string | null
  provider_id?: string | null
  provider_type?: ProviderType | null
  model_name?: string | null
}

// Worker Management
export interface WorkerStatus {
  active: number
  idle: number
  queue_size: number
}

// Configuration Management
export interface ConfigChange {
  path: string
  old_value: any
  new_value: any
  description: string
}

export interface ParseResult {
  changes: ConfigChange[]
  confidence: number
  clarification?: string
}

// ========== API Response Types ==========

export interface PaginatedResponse<T> {
  data: T[]
  total: number
  page: number
  per_page: number
}

export interface ApiError {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

// ========== Export Types ==========

export type StageArtifactExportFormat = 'json' | 'markdown' | 'diff' | 'zip'
export type FinalExportFormat = 'txt' | 'epub' | 'compare'
export type FinalExportScope = 'all' | 'chapter_range' | 'rewritten_only'

export interface QualityThresholdComparison {
  label: string
  metric?: string
  actual: number
  threshold: number
  comparator?: '>' | '>=' | '<' | '<='
  unit?: string
  status?: 'ok' | 'warning' | 'blocked'
  suggestion?: string
}

export interface QualityReport {
  novel_id?: string
  task_id?: string
  stage?: StageName
  export_format?: FinalExportFormat
  export_scope?: FinalExportScope
  blocked_reason?: string
  warning_count?: number
  failed_segment_count?: number
  total_segment_count?: number
  allow_force?: boolean
  risk_signature?: string | null
  threshold_comparisons: QualityThresholdComparison[]
  suggestions?: string[]
  generated_at?: string
  export_manifest?: Record<string, unknown>
  warnings?: Array<Record<string, unknown>>
}

export interface QualityGateBlockedDetails extends QualityReport {
  reason?: string
  warning_details?: Array<Record<string, unknown>>
}

export interface StageArtifactExportRequest {
  format: StageArtifactExportFormat
  run_seq?: number
  task_id?: string
}

export interface FinalExportRequest {
  format: FinalExportFormat
  scope: FinalExportScope
  chapter_start?: number
  chapter_end?: number
  force?: boolean
  task_id?: string
}

export interface DownloadedFile {
  blob: Blob
  filename: string
  risk_signature?: string | null
  content_type: string | null
}

// ========== WebSocket Message Types ==========

export type WSMessage =
  | { type: 'stage_progress'; novel_id: string; stage: string; chapters_done: number; chapters_total: number; percentage: number }
  | { type: 'chapter_completed'; novel_id: string; stage: string; chapter_index: number }
  | { type: 'stage_completed'; novel_id: string; stage: string; duration_ms: number }
  | { type: 'stage_failed'; novel_id: string; stage: string; error: string }
  | { type: 'chapter_failed'; novel_id: string; stage: string; chapter_index: number; error: string; retries_exhausted: boolean }
  | { type: 'task_paused'; novel_id: string }
  | { type: 'task_resumed'; novel_id: string }
  | { type: 'stage_stale'; novel_id: string; stage: string }
  | { type: 'worker_pool_status'; active: number; idle: number; queue_size: number }
  | { type: 'ping' }
  | { type: 'pong' }

// ========== Form Types ==========

export interface UploadNovelForm {
  file: File
}

export interface CreateProviderForm {
  name: string
  provider_type: ProviderType
  api_key?: string
  base_url: string
  model_name: string
  temperature: number
  max_tokens: number
  top_p?: number | null
  presence_penalty?: number | null
  frequency_penalty?: number | null
  rpm_limit: number
  tpm_limit: number
}

export interface UpdateProviderForm extends Partial<CreateProviderForm> {}

export interface TestConnectionRequest {
  provider_id?: string
  provider_type?: ProviderType
  api_key?: string
  base_url?: string
  model_name?: string
}

export interface FetchModelsRequest {
  provider_id?: string
  api_key?: string
  base_url?: string
  provider_type?: ProviderType
}

export interface FetchModelsResponse {
  models: string[]
  fetched_at?: string
  source?: 'draft' | 'saved'
  provider_id?: string | null
  provider_type?: ProviderType | null
  cached?: boolean
}

// ========== Scene Types ==========

export type SceneType =
  | '战斗'
  | '对话'
  | '心理描写'
  | '环境描写'
  | '叙事过渡'
  | '感情互动'
  | '回忆闪回'
  | '日常生活'

// ========== Configuration Types ==========

export interface SceneRule {
  id: string
  scene_type: string
  trigger_conditions: string[]
  weight: number
  enabled: boolean
}

export interface RewriteRule {
  id: string
  scene_type: string
  strategies?: RewriteStrategy[]
  strategy?: RewriteStrategy
  rewrite_guidance?: string
  target_ratio: number
  target_chars?: number
  priority: number
  enabled: boolean
}

export interface RewriteRuleInput {
  scene_type: string
  strategies: RewriteStrategy[]
  strategy: RewriteStrategy
  rewrite_guidance?: string
  target_ratio: number
  target_chars?: number
  priority: number
  enabled: boolean
}

export interface ConfigSnapshot {
  version: string
  global_prompt: string
  rewrite_general_guidance: string
  scene_rules: SceneRule[]
  rewrite_rules: RewriteRule[]
  updated_at?: string | null
}

export interface ConfigPatch {
  global_prompt?: string | null
  rewrite_general_guidance?: string | null
  scene_rules?: SceneRule[] | null
  rewrite_rules?: RewriteRule[] | null
}

export interface ConfigParseResponse {
  status: 'ok' | 'clarification_needed'
  clarification?: string | null
  diff_summary: string[]
  patch: ConfigPatch
  snapshot: ConfigSnapshot
}

export interface ImportDiffSummary {
  global_prompt_changed: boolean
  scene_rules_added: number
  scene_rules_updated: number
  rewrite_rules_added: number
  rewrite_rules_updated: number
  conflicts: string[]
}

export interface ConfigImportPreviewResponse {
  status: 'preview'
  summary: ImportDiffSummary
  snapshot: ConfigSnapshot
  requires_confirmation: boolean
}

export interface ConfigData {
  version: string
  global_prompt: string
  rewrite_general_guidance: string
  scene_rules: SceneRule[]
  rewrite_rules: RewriteRule[]
}

export interface GenerationParams {
  temperature?: number
  max_tokens?: number
  top_p?: number
  presence_penalty?: number
  frequency_penalty?: number
}

// ========== UI State Types ==========

export interface NovelListItem {
  id: string
  title: string
  word_count: number
  chapter_count: number
  format: 'txt' | 'epub'
  imported_at: string
  status: string
}

export interface ChapterStageTiming {
  started_at?: string | null
  completed_at?: string | null
}

export interface ChapterListItem {
  id: string
  index: number
  title: string
  char_count?: number
  word_count?: number
  status?: string
  stages?: Partial<Record<StageName, StageStatus>>
  stage_timings?: Partial<Record<StageName, ChapterStageTiming>>
}

export interface ChapterDetail {
  id: string
  novel_id: string
  index: number
  title: string
  content: string
  word_count: number
  status: string
  analysis?: ChapterAnalysis
  rewrite_plan?: ChapterRewritePlan
  rewrites?: RewriteResult[]
}
