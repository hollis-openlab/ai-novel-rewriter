import type {
  Novel,
  NovelDetail,
  ImportResult,
  ChapterListItem,
  ChapterDetail,
  ChapterAnalysis,
  ChapterMarkRequest,
  CharacterTrajectoryResponse,
  RewriteSegment,
  RewriteReviewRequest,
  Provider,
  CreateProviderForm,
  UpdateProviderForm,
  ProviderTestResult,
  TestConnectionRequest,
  FetchModelsRequest,
  FetchModelsResponse,
  WorkerStatus,
  StageName,
  ConfigSnapshot,
  ConfigPatch,
  ConfigParseResponse,
  ConfigImportPreviewResponse,
  SceneRule,
  RewriteRuleInput,
  StageArtifactExportRequest,
  FinalExportRequest,
  DownloadedFile,
  QualityReport,
} from '@/types'

const BASE_URL = '/api/v1'

// Generic API error class
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
    public details?: Record<string, unknown>
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function buildUrl(path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  return url.toString()
}

function prepareHeaders(init?: RequestInit): Headers {
  const headers = new Headers(init?.headers)
  const body = init?.body
  if (!headers.has('Content-Type') && !(body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  return headers
}

function contentDispositionFilename(contentDisposition: string | null): string | null {
  if (!contentDisposition) return null

  const utf8Match = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(contentDisposition)
  const fallbackMatch = /filename\s*=\s*"([^"]+)"/i.exec(contentDisposition)
  const raw = (utf8Match?.[1] ?? fallbackMatch?.[1])?.trim()
  if (!raw) return null

  try {
    return decodeURIComponent(raw.replace(/^"|"$/g, ''))
  } catch {
    return raw.replace(/^"|"$/g, '')
  }
}

async function parseApiError(response: Response): Promise<ApiError> {
  let errorMessage = `HTTP ${response.status}: ${response.statusText}`
  let errorCode: string | undefined
  let details: Record<string, unknown> | undefined

  try {
    const errorBody = await response.json() as { error?: { code?: string; message?: string; details?: Record<string, unknown> } }
    if (errorBody.error) {
      errorMessage = errorBody.error.message || errorMessage
      errorCode = errorBody.error.code
      details = errorBody.error.details
    }
  } catch {
    // Non-JSON error responses fall back to status text
  }

  return new ApiError(errorMessage, response.status, errorCode, details)
}

async function requestResponse(
  path: string,
  init?: RequestInit,
  query?: Record<string, string | number | boolean | null | undefined>
): Promise<Response> {
  const response = await fetch(buildUrl(path, query), init)
  if (!response.ok) {
    throw await parseApiError(response)
  }
  return response
}

async function requestJson<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.status === 205 || response.status === 304) {
    return undefined as T
  }

  const contentLength = response.headers.get('content-length')
  if (contentLength === '0') {
    return undefined as T
  }

  const contentType = response.headers.get('content-type')
  if (!contentType || !contentType.includes('application/json')) {
    return undefined as T
  }

  const text = await response.text()
  if (!text.trim()) {
    return undefined as T
  }

  return JSON.parse(text) as T
}

async function requestDownload(
  path: string,
  init?: RequestInit,
  query?: Record<string, string | number | boolean | null | undefined>,
  filenameFallback = 'download'
): Promise<DownloadedFile> {
  const response = await requestResponse(path, init, query)
  const blob = await response.blob()
  const contentType = response.headers.get('content-type')
  const filename = contentDispositionFilename(response.headers.get('content-disposition')) ?? filenameFallback
  const riskSignature = response.headers.get('x-risk-signature')

  return {
    blob,
    filename,
    risk_signature: riskSignature,
    content_type: contentType,
  }
}

// Generic API fetch function with error handling
export async function apiFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  try {
    const response = await fetch(buildUrl(path), {
      ...init,
      headers: prepareHeaders(init),
    })

    if (!response.ok) {
      throw await parseApiError(response)
    }

    return await requestJson<T>(response)
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }

    // Network errors, parse errors, etc.
    throw new ApiError(
      `Network error: ${error instanceof Error ? error.message : 'Unknown error'}`,
      0
    )
  }
}

// ========== Novels ==========
export const novels = {
  list: () => apiFetch<{data: Novel[], total: number}>('/novels').then(r => Array.isArray(r) ? r : (r?.data ?? [])),

  get: (id: string) => apiFetch<NovelDetail>(`/novels/${id}`),

  getQualityReport: (novelId: string, taskId?: string) =>
    requestResponse(
      `/novels/${novelId}/quality-report`,
      { method: 'GET' },
      taskId ? { task_id: taskId } : undefined
    ).then((response) => requestJson<QualityReport>(response)),

  exportFinal: (novelId: string, request: FinalExportRequest) =>
    requestDownload(
      `/novels/${novelId}/export`,
      { method: 'GET' },
      {
        format: request.format,
        scope: request.scope,
        chapter_start: request.chapter_start,
        chapter_end: request.chapter_end,
        force: request.force,
        task_id: request.task_id,
      },
      `novel-export.${request.format === 'compare' ? 'html' : request.format}`
    ),

  import: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiFetch<ImportResult>('/novels/import', {
      method: 'POST',
      body: formData,
      headers: {} // Let browser set Content-Type for FormData
    })
  },

  delete: (id: string) => apiFetch<void>(`/novels/${id}`, { method: 'DELETE' })
}

interface StageActionPayload {
  run_idempotency_key?: string
  force?: boolean
  split_rule_id?: string
  provider_id?: string
  rewrite_target_chars?: number | null
  rewrite_target_added_chars?: number | null
}

interface StageChapterRetryPayload {
  force_rerun?: boolean
  provider_id?: string
  rewrite_target_chars?: number | null
  rewrite_target_added_chars?: number | null
}

export interface StageChapterRetryResponse {
  novel_id: string
  task_id: string
  stage: StageName
  chapter_idx: number
  status: string
  segments_total?: number
  failed_segments?: number
  marked_segments_total?: number
  rewrite_target_added_chars_override?: number | null
}

// ========== Stages ==========
export const stages = {
  run: (novelId: string, stage: StageName, payload?: StageActionPayload) =>
    apiFetch<void>(`/novels/${novelId}/stages/${stage}/run`, {
      method: 'POST',
      body: payload ? JSON.stringify(payload) : undefined,
    }),

  pause: (novelId: string, stage: StageName) =>
    apiFetch<void>(`/novels/${novelId}/stages/${stage}/pause`, { method: 'POST' }),

  resume: (novelId: string, stage: StageName) =>
    apiFetch<void>(`/novels/${novelId}/stages/${stage}/resume`, { method: 'POST' }),

  retry: (novelId: string, stage: StageName, payload?: StageActionPayload) =>
    apiFetch<void>(`/novels/${novelId}/stages/${stage}/retry`, {
      method: 'POST',
      body: payload ? JSON.stringify(payload) : undefined,
    }),

  confirmSplit: (novelId: string) =>
    apiFetch<void>(`/novels/${novelId}/stages/split/confirm`, { method: 'POST' }),

  exportArtifact: (novelId: string, stage: StageName, request: StageArtifactExportRequest) =>
    requestDownload(
      `/artifacts/novels/${novelId}/stages/${stage}/artifact`,
      { method: 'GET' },
      {
        format: request.format,
        task_id: request.task_id,
      },
      `${stage}-artifact.${request.format === 'markdown' ? 'md' : request.format}`
    ),

  getArtifact: (novelId: string, stage: StageName, format = 'json') =>
    requestDownload(
      `/artifacts/novels/${novelId}/stages/${stage}/artifact`,
      { method: 'GET' },
      { format },
      `${stage}-artifact.${format === 'markdown' ? 'md' : format}`
    ),

  getRunArtifact: (novelId: string, stage: StageName, runSeq: number, format = 'json', taskId?: string) =>
    requestDownload(
      `/novels/${novelId}/stages/${stage}/artifact`,
      { method: 'GET' },
      {
        format,
        run_seq: runSeq,
        task_id: taskId,
      },
      `${stage}-artifact-r${runSeq}.${format === 'markdown' ? 'md' : format}`
    ),
}

// ========== Chapters ==========
export const chapters = {
  list: (novelId: string) =>
    apiFetch<{ data: ChapterListItem[]; total: number } | ChapterListItem[]>(`/novels/${novelId}/chapters`).then((r) =>
      Array.isArray(r) ? r : (r?.data ?? [])
    ),

  get: (novelId: string, chapterIdx: number) =>
    apiFetch<ChapterDetail>(`/novels/${novelId}/chapters/${chapterIdx}`),

  getAnalysis: (novelId: string, chapterIdx: number) =>
    apiFetch<ChapterAnalysis>(`/novels/${novelId}/chapters/${chapterIdx}/analysis`),

  updateAnalysis: (novelId: string, chapterIdx: number, analysis: ChapterAnalysis) =>
    apiFetch<void>(`/novels/${novelId}/chapters/${chapterIdx}/analysis`, {
      method: 'PUT',
      body: JSON.stringify(analysis)
    }),

  getRewrites: (novelId: string, chapterIdx: number) =>
    apiFetch<RewriteSegment[]>(`/novels/${novelId}/chapters/${chapterIdx}/rewrites`),

  reviewRewrite: (novelId: string, chapterIdx: number, segmentId: string, payload: RewriteReviewRequest) =>
    apiFetch<void>(`/novels/${novelId}/chapters/${chapterIdx}/rewrites/${segmentId}`, {
      method: 'PUT',
      body: JSON.stringify(payload)
    }),

  updateMarks: (novelId: string, chapterIdx: number, marks: ChapterMarkRequest) =>
    apiFetch<void>(`/novels/${novelId}/chapters/${chapterIdx}/marks`, {
      method: 'PUT',
      body: JSON.stringify(marks)
    }),

  getCharacterTrajectory: (novelId: string, characterName: string) =>
    apiFetch<CharacterTrajectoryResponse>(`/novels/${novelId}/chapters/characters/${encodeURIComponent(characterName)}/trajectory`),

  retryChapter: (novelId: string, stage: StageName, chapterIdx: number, payload?: StageChapterRetryPayload) =>
    apiFetch<StageChapterRetryResponse>(`/novels/${novelId}/stages/${stage}/chapters/${chapterIdx}/retry`, {
      method: 'POST',
      body: payload ? JSON.stringify(payload) : undefined,
    })
}

// ========== Config ==========
export const config = {
  getSnapshot: () => apiFetch<ConfigSnapshot>('/config/export-json'),

  updateGlobalPrompt: (global_prompt: string) =>
    apiFetch<ConfigSnapshot>('/config/global-prompt', {
      method: 'PUT',
      body: JSON.stringify({ global_prompt })
    }),

  updateRewriteGeneralGuidance: (rewrite_general_guidance: string) =>
    apiFetch<ConfigSnapshot>('/config/rewrite-general-guidance', {
      method: 'PUT',
      body: JSON.stringify({ rewrite_general_guidance })
    }),

  createSceneRule: (rule: Omit<SceneRule, 'id'>) =>
    apiFetch<ConfigSnapshot>('/config/scene-rules', {
      method: 'POST',
      body: JSON.stringify(rule)
    }),

  updateSceneRule: (rule: SceneRule) =>
    apiFetch<ConfigSnapshot>('/config/scene-rules', {
      method: 'PUT',
      body: JSON.stringify(rule)
    }),

  deleteSceneRule: (id: string) =>
    apiFetch<ConfigSnapshot>('/config/scene-rules', {
      method: 'DELETE',
      body: JSON.stringify({ id })
    }),

  createRewriteRule: (rule: RewriteRuleInput) =>
    apiFetch<ConfigSnapshot>('/config/rewrite-rules', {
      method: 'POST',
      body: JSON.stringify(rule)
    }),

  updateRewriteRule: (rule: RewriteRuleInput & { id: string }) =>
    apiFetch<ConfigSnapshot>('/config/rewrite-rules', {
      method: 'PUT',
      body: JSON.stringify(rule)
    }),

  deleteRewriteRule: (id: string) =>
    apiFetch<ConfigSnapshot>('/config/rewrite-rules', {
      method: 'DELETE',
      body: JSON.stringify({ id })
    }),

  parseInstruction: (instruction: string) =>
    apiFetch<ConfigParseResponse>('/config/ai-parse', {
      method: 'POST',
      body: JSON.stringify({ instruction })
    }),

  applyPatch: (patch: ConfigPatch) =>
    apiFetch<ConfigSnapshot>('/config/ai-apply', {
      method: 'POST',
      body: JSON.stringify({ patch })
    }),

  exportJson: () => apiFetch<ConfigSnapshot>('/config/export-json'),

  previewImportJson: (payload: Record<string, unknown>) =>
    apiFetch<ConfigImportPreviewResponse>('/config/import-json?confirm=false', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),

  importJson: (payload: Record<string, unknown>) =>
    apiFetch<ConfigSnapshot>('/config/import-json?confirm=true', {
      method: 'POST',
      body: JSON.stringify(payload)
    }),

  getSceneRules: async () => (await apiFetch<ConfigSnapshot>('/config/export-json')).scene_rules,
  updateSceneRules: async (rules: SceneRule[]) => apiFetch<ConfigSnapshot>('/config/ai-apply', {
    method: 'POST',
    body: JSON.stringify({ patch: { scene_rules: rules } })
  }),
  getStrategies: async () => (await apiFetch<ConfigSnapshot>('/config/export-json')).rewrite_rules,
  getRewriteStrategies: async () => (await apiFetch<ConfigSnapshot>('/config/export-json')).rewrite_rules,
  updateStrategies: async (strategies: RewriteRuleInput[]) => apiFetch<ConfigSnapshot>('/config/ai-apply', {
    method: 'POST',
    body: JSON.stringify({ patch: { rewrite_rules: strategies } })
  }),
  getPrompts: async () => ({ global: (await apiFetch<ConfigSnapshot>('/config/export-json')).global_prompt, analyze: '', rewrite: '' }),
  updatePrompts: async (prompts: { global?: string }) => apiFetch<ConfigSnapshot>('/config/global-prompt', {
    method: 'PUT',
    body: JSON.stringify({ global_prompt: prompts.global ?? '' })
  }),
  getParams: async () => ({}),
  updateParams: async (_params: Record<string, unknown>) => ({}),
  getPresets: async () => [],
  createPreset: async (_preset: { name: string; config: any }) => ({}),
  exportAll: async () => apiFetch<ConfigSnapshot>('/config/export-json'),
  importAll: async (configFile: File) => {
    const text = await configFile.text()
    return apiFetch<ConfigSnapshot>('/config/import-json?confirm=true', {
      method: 'POST',
      body: text
    })
  },
  aiParse: (input: string) => apiFetch<ConfigParseResponse>('/config/ai-parse', {
    method: 'POST',
    body: JSON.stringify({ instruction: input })
  }),
  aiApply: (patch: ConfigPatch) => apiFetch<ConfigSnapshot>('/config/ai-apply', {
    method: 'POST',
    body: JSON.stringify({ patch })
  }),
}

// ========== Providers ==========
export const providers = {
  list: () => apiFetch<{ providers: Provider[]; total: number }>('/providers').then(r => Array.isArray(r) ? r : (r?.providers ?? [])),

  create: (data: CreateProviderForm) =>
    apiFetch<Provider>('/providers', {
      method: 'POST',
      body: JSON.stringify(data)
    }),

  update: (id: string, data: UpdateProviderForm) =>
    apiFetch<Provider>(`/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    }),

  delete: (id: string) => apiFetch<void>(`/providers/${id}`, { method: 'DELETE' }),

  test: (id: string) =>
    apiFetch<ProviderTestResult>(`/providers/${id}/test`, { method: 'POST' }),

  testConnection: (config: TestConnectionRequest) =>
    apiFetch<ProviderTestResult>('/providers/test-connection', {
      method: 'POST',
      body: JSON.stringify(config)
    }),

  fetchModels: (config: FetchModelsRequest) =>
    apiFetch<FetchModelsResponse>('/providers/fetch-models', {
      method: 'POST',
      body: JSON.stringify(config)
    }),

  listModels: (id: string) =>
    apiFetch<FetchModelsResponse>(`/providers/${id}/models`),

  updateApiKey: (id: string, api_key: string) =>
    apiFetch<void>(`/providers/${id}/api-key`, {
      method: 'PUT',
      body: JSON.stringify({ api_key })
    }),
}

// ========== Workers ==========
export const workers = {
  status: () => apiFetch<WorkerStatus>('/workers/status'),

  setCount: (count: number) =>
    apiFetch<void>('/workers/count', {
      method: 'PUT',
      body: JSON.stringify({ count })
    })
}

// ========== Upload with Progress ==========
export const uploadNovel = (file: File, onProgress?: (progress: number) => void) => {
  return new Promise<ImportResult>((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file)

    const xhr = new XMLHttpRequest()

    // Track upload progress
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress((e.loaded / e.total) * 100)
        }
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const result = JSON.parse(xhr.responseText)
          resolve(result)
        } catch {
          reject(new ApiError('Invalid response format', xhr.status))
        }
      } else {
        let errorMessage = `HTTP ${xhr.status}: ${xhr.statusText}`
        try {
          const errorBody = JSON.parse(xhr.responseText)
          if (errorBody.error?.message) {
            errorMessage = errorBody.error.message
          }
        } catch {
          // Use default error message
        }
        reject(new ApiError(errorMessage, xhr.status))
      }
    }

    xhr.onerror = () => {
      reject(new ApiError('Network error during upload', 0))
    }

    xhr.open('POST', `${BASE_URL}/novels/import`)
    xhr.send(formData)
  })
}

// Legacy exports for backward compatibility
export const getHealth = () => apiFetch<{ status: string; version?: string }>('/health')
export const getNovels = novels.list
export const getNovel = novels.get
export const getNovelChapters = chapters.list
export const getChapter = chapters.get
export const getWorkerStatus = workers.status
export const getConfig = config.getSnapshot
export const updateConfig = config.updateGlobalPrompt
export const getProviders = providers.list
export const testProvider = providers.test
export const uploadFile = uploadNovel
// Individual exports for Config.tsx compatibility
export const aiParseConfig = config.parseInstruction
export const aiApplyConfig = config.applyPatch
export const getSceneRules = async () => (await config.getSnapshot()).scene_rules
export const getRewriteStrategies = async () => (await config.getSnapshot()).rewrite_rules
export const getSystemPrompts = async () => ({ global: (await config.getSnapshot()).global_prompt })
export const getGenerationParams = async () => ({})
export const getPresets = async () => []
export const savePreset = async (_name: string) => ({})
export const deletePreset = async (_name: string) => ({})
export const exportConfig = config.exportJson
