import { apiFetch } from '@/lib/api'
import type { PromptLogEntry, PromptLogListResponse, PromptLogRetryResponse } from '@/types/prompt-log'

export const promptLogsApi = {
  list: (novelId: string, chapterIdx: number) =>
    apiFetch<PromptLogListResponse>(`/novels/${novelId}/chapters/${chapterIdx}/prompt-logs`),

  retry: (novelId: string, chapterIdx: number, callId: string) =>
    apiFetch<PromptLogRetryResponse>(`/novels/${novelId}/chapters/${chapterIdx}/prompt-logs/${callId}/retry`, {
      method: 'POST',
    }),
}

export function formatPromptLogTokens(tokens: PromptLogEntry['tokens']): string {
  const items: string[] = []
  if (tokens.prompt_tokens != null) items.push(`prompt ${tokens.prompt_tokens}`)
  if (tokens.completion_tokens != null) items.push(`completion ${tokens.completion_tokens}`)
  if (tokens.total_tokens != null) items.push(`total ${tokens.total_tokens}`)
  return items.length > 0 ? items.join(' / ') : 'tokens n/a'
}

export function buildPromptClipboardText(entry: PromptLogEntry): string {
  return [
    '[SYSTEM]',
    entry.system_prompt,
    '',
    '[TASK]',
    entry.user_prompt,
  ].join('\n')
}

export function prettyPrintPromptPayload(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const SCENE_RULE_PROMPT_RE = /(可用场景规则|场景识别规则（仅用于命中判定）)/u
const WHOLE_CHAPTER_PROMPT_RE = /(整章全文|不要按段落拆成多个独立分析任务)/u

export type PromptLayout = 'legacy' | 'current' | 'mixed' | 'unknown'

export type PromptLayoutInfo = {
  layout: PromptLayout
  systemHasSceneRules: boolean
  userHasSceneRules: boolean
  userHasWholeChapterDirective: boolean
}

export function detectPromptLayout(entry: Pick<PromptLogEntry, 'system_prompt' | 'user_prompt'>): PromptLayoutInfo {
  const systemPrompt = entry.system_prompt || ''
  const userPrompt = entry.user_prompt || ''
  const systemHasSceneRules = SCENE_RULE_PROMPT_RE.test(systemPrompt)
  const userHasSceneRules = SCENE_RULE_PROMPT_RE.test(userPrompt)
  const userHasWholeChapterDirective = WHOLE_CHAPTER_PROMPT_RE.test(userPrompt)

  let layout: PromptLayout = 'unknown'
  if (systemHasSceneRules && !userHasSceneRules) {
    layout = 'legacy'
  } else if (!systemHasSceneRules && userHasSceneRules) {
    layout = 'current'
  } else if (systemHasSceneRules && userHasSceneRules) {
    layout = 'mixed'
  }

  return {
    layout,
    systemHasSceneRules,
    userHasSceneRules,
    userHasWholeChapterDirective,
  }
}
