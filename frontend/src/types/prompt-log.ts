export interface PromptLogTokens {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
}

export interface PromptLogValidation {
  passed: boolean | null
  error_code: string | null
  error_message: string | null
  details: Record<string, unknown>
}

export interface PromptLogEntry {
  call_id: string
  novel_id: string
  chapter_index: number
  stage: string
  attempt: number
  timestamp: string
  provider: string
  model_name: string | null
  duration_ms: number
  system_prompt: string
  user_prompt: string
  response: unknown
  params: Record<string, unknown>
  usage: Record<string, unknown>
  tokens: PromptLogTokens
  validation: PromptLogValidation
}

export interface PromptLogListResponse {
  novel_id: string
  chapter_idx: number
  total: number
  data: PromptLogEntry[]
}

export interface PromptLogRetryResponse {
  novel_id: string
  chapter_idx: number
  call_id: string
  stage: string
  status: 'queued'
  replay_mode: 'degraded'
  message: string
}
