import { apiFetch } from '@/lib/api'

export interface SplitRuleSpec {
  id?: string | null
  name: string
  pattern: string
  priority: number
  enabled: boolean
  builtin: boolean
}

export interface SplitRulesConfigResponse {
  rules_version: string
  builtin_rules: SplitRuleSpec[]
  custom_rules: SplitRuleSpec[]
}

export interface SplitRulesConfigRequest {
  builtin_rules: SplitRuleSpec[]
  custom_rules: SplitRuleSpec[]
}

export interface SplitMatchedLine {
  paragraph_index: number
  line_number: number
  text: string
  rule_id?: string | null
  rule_name?: string | null
}

export interface SplitPreviewChapter {
  id: string
  index: number
  title: string
  content: string
  start_offset: number
  end_offset: number
  char_count: number
  paragraph_count: number
}

export interface SplitRulesPreviewResponse {
  preview_token: string
  novel_id: string
  source_revision: string
  rules_version: string
  preview_valid: boolean
  failure_reason?: string | null
  matched_count: number
  estimated_chapters: number
  matched_lines: SplitMatchedLine[]
  boundary_hash: string
  chapters: SplitPreviewChapter[]
  sample_size?: number
  preview_sample_size?: number
  sampled_chapter_count?: number
  chapters_sampled?: number
  preview_truncated?: boolean
  chapters_truncated?: boolean
  is_truncated?: boolean
}

export interface SplitStagePreviewResponse {
  novel_id: string
  task_id: string
  stage: 'split'
  status: 'paused'
  run_id: string
  run_seq: number
  preview_token: string
  source_revision: string
  rules_version: string
  boundary_hash: string
  estimated_chapters: number
  chapters: SplitPreviewChapter[]
  created_at: string
}

export interface SplitRulesConfirmResponse {
  preview_token: string
  novel_id: string
  source_revision: string
  rules_version: string
  boundary_hash: string
  preview_valid: boolean
  chapter_count: number
  chapters: SplitPreviewChapter[]
}

export interface SplitRuleCreateRequest {
  name: string
  pattern: string
  priority: number
  enabled: boolean
}

export interface SplitRuleUpdateRequest {
  name?: string
  pattern?: string
  priority?: number
  enabled?: boolean
}

export interface SplitPreviewRequest {
  novel_id: string
  source_revision?: string
  rules_version?: string
  sample_size?: number
  selected_rule_id?: string | null
  builtin_rules?: SplitRuleSpec[]
  custom_rules?: SplitRuleSpec[]
}

export interface SplitConfirmRequest {
  preview_token: string
}

export interface SplitRunPreviewRequest {
  force?: boolean
  split_rule_id?: string | null
}

export const splitRulesApi = {
  get: () => apiFetch<SplitRulesConfigResponse>('/split-rules'),

  replace: (payload: SplitRulesConfigRequest) =>
    apiFetch<SplitRulesConfigResponse>('/split-rules', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  createCustom: (payload: SplitRuleCreateRequest) =>
    apiFetch<SplitRulesConfigResponse>('/split-rules/custom', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateCustom: (ruleId: string, payload: SplitRuleUpdateRequest) =>
    apiFetch<SplitRulesConfigResponse>(`/split-rules/custom/${ruleId}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  deleteCustom: (ruleId: string) =>
    apiFetch<SplitRulesConfigResponse>(`/split-rules/custom/${ruleId}`, {
      method: 'DELETE',
    }),

  runPreview: (novelId: string, payload?: SplitRunPreviewRequest) =>
    apiFetch<SplitStagePreviewResponse>(`/novels/${novelId}/stages/split/run`, {
      method: 'POST',
      body: JSON.stringify({
        force: payload?.force ?? false,
        split_rule_id: payload?.split_rule_id || undefined,
      }),
    }),

  confirm: (novelId: string, previewToken: string) =>
    apiFetch<SplitRulesConfirmResponse>(`/novels/${novelId}/stages/split/confirm`, {
      method: 'POST',
      body: JSON.stringify({ preview_token: previewToken }),
    }),

  preview: (payload: SplitPreviewRequest) =>
    apiFetch<SplitRulesPreviewResponse>('/split-rules/preview', {
      method: 'POST',
      body: JSON.stringify({
        novel_id: payload.novel_id,
        source_revision: payload.source_revision,
        rules_version: payload.rules_version,
        sample_size: payload.sample_size ?? 10,
        selected_rule_id: payload.selected_rule_id || undefined,
        builtin_rules: payload.builtin_rules,
        custom_rules: payload.custom_rules,
      }),
    }),
}
