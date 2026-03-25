import { useCallback, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  ChevronDown,
  Eye,
  EyeOff,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  X,
} from 'lucide-react'
import { providers as providersApi } from '@/lib/api'
import type {
  CreateProviderForm,
  Provider,
  ProviderTestResult,
  ProviderType,
  TestConnectionRequest,
} from '@/types'

const PROVIDER_LABELS: Record<ProviderType, string> = {
  openai: 'OpenAI',
  openai_compatible: 'OpenAI 兼容',
}

const PROVIDER_HINTS: Record<ProviderType, string> = {
  openai: 'https://api.openai.com/v1',
  openai_compatible: 'https://api.siliconflow.cn/v1',
}

const PROVIDER_TYPES: ProviderType[] = ['openai', 'openai_compatible']

const parseNumber = (value: string, fallback: number) => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

const parseOptionalNumber = (value: string) => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const next = Number(trimmed)
  return Number.isFinite(next) ? next : null
}

const toProviderPayload = (state: ProviderModalState): CreateProviderForm => ({
  name: state.name.trim() || PROVIDER_LABELS[state.providerType],
  provider_type: state.providerType,
  api_key: state.apiKey.trim() || undefined,
  base_url: state.baseUrl.trim(),
  model_name: state.selectedModel.trim(),
  temperature: parseNumber(state.temperature, 0.7),
  max_tokens: Math.max(1, Math.round(parseNumber(state.maxTokens, 4096))),
  top_p: parseOptionalNumber(state.topP),
  presence_penalty: null,
  frequency_penalty: null,
  rpm_limit: Math.max(1, Math.round(parseNumber(state.rpmLimit, 60))),
  tpm_limit: Math.max(1, Math.round(parseNumber(state.tpmLimit, 100000))),
})

function ProviderIcon({ type }: { type: ProviderType }) {
  return (
    <div className={`flex h-10 w-10 items-center justify-center rounded-xl ${type === 'openai' ? 'bg-primary' : 'bg-accent'}`}>
      <span className="text-caption font-bold text-white">{type === 'openai' ? 'AI' : 'OC'}</span>
    </div>
  )
}

function StatusDot({ status }: { status: 'connected' | 'error' | 'unknown' }) {
  const palette = {
    connected: { dot: 'bg-success', text: 'text-success', label: '已连接' },
    error: { dot: 'bg-error', text: 'text-error', label: '连接失败' },
    unknown: { dot: 'bg-tertiary', text: 'text-secondary', label: '未测试' },
  }[status]

  return (
    <div className="flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${palette.dot}`} />
      <span className={`text-callout font-medium ${palette.text}`}>{palette.label}</span>
    </div>
  )
}

function ProviderCard({
  provider,
  onDelete,
  onEdit,
  onTest,
  testResult,
  testing,
}: {
  provider: Provider
  onDelete: (id: string) => void
  onEdit: (provider: Provider) => void
  onTest: (id: string) => void
  testResult?: ProviderTestResult
  testing?: boolean
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  const status: 'connected' | 'error' | 'unknown' = testResult
    ? testResult.success
      ? 'connected'
      : 'error'
    : provider.is_active
      ? 'connected'
      : 'unknown'

  return (
    <div className="space-y-4 rounded-2xl border border-border bg-white p-6 shadow-xs">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <ProviderIcon type={provider.provider_type} />
          <div>
            <h3 className="text-title-2 font-semibold text-primary">{provider.name}</h3>
            <p className="text-callout text-secondary">{provider.model_name}</p>
          </div>
        </div>
        <StatusDot status={status} />
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl bg-subtle px-3 py-2">
          <p className="text-caption text-secondary">Provider</p>
          <p className="text-callout font-medium text-primary">{PROVIDER_LABELS[provider.provider_type]}</p>
        </div>
        <div className="rounded-xl bg-subtle px-3 py-2">
          <p className="text-caption text-secondary">Base URL</p>
          <p className="truncate font-mono text-callout text-primary" title={provider.base_url}>{provider.base_url}</p>
        </div>
        <div className="rounded-xl bg-subtle px-3 py-2">
          <p className="text-caption text-secondary">温度 / Max Tokens</p>
          <p className="text-callout text-primary">{provider.temperature} / {provider.max_tokens}</p>
        </div>
        <div className="rounded-xl bg-subtle px-3 py-2">
          <p className="text-caption text-secondary">RPM / TPM</p>
          <p className="text-callout text-primary">{provider.rpm_limit} / {provider.tpm_limit}</p>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onTest(provider.id)}
            disabled={testing}
            className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            测试连接
          </button>
          {testResult && (
            <span className={`text-callout font-medium ${testResult.success ? 'text-success' : 'text-error'}`}>
              {testResult.success ? `${testResult.latency_ms ?? 0}ms` : (testResult.error ?? '连接失败')}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onEdit(provider)}
            className="flex items-center gap-1.5 text-callout text-accent transition hover:underline"
          >
            <Pencil className="h-4 w-4" />
            编辑
          </button>
          {confirmDelete ? (
            <div className="flex items-center gap-2">
              <span className="text-callout text-secondary">确认删除?</span>
              <button
                type="button"
                onClick={() => onDelete(provider.id)}
                className="text-callout font-medium text-error transition hover:underline"
              >
                删除
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="text-callout text-secondary transition hover:text-primary"
              >
                取消
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="flex items-center gap-1.5 text-callout text-error transition hover:underline"
            >
              <Trash2 className="h-4 w-4" />
              删除
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

type ProviderModalState = {
  providerType: ProviderType
  name: string
  apiKey: string
  baseUrl: string
  selectedModel: string
  availableModels: string[]
  modelSearch: string
  showKey: boolean
  temperature: string
  maxTokens: string
  topP: string
  rpmLimit: string
  tpmLimit: string
  fetchState: 'idle' | 'loading' | 'ready' | 'error'
  fetchError: string
  testState: 'idle' | 'testing' | 'success' | 'error'
  testLatency: number | null
  testError: string
}

function ProviderModal({
  editProvider,
  onClose,
  onSave,
  isSaving,
}: {
  editProvider?: Provider | null
  onClose: () => void
  onSave: (payload: CreateProviderForm) => Promise<void> | void
  isSaving?: boolean
}) {
  const isEdit = Boolean(editProvider)

  const [state, setState] = useState<ProviderModalState>(() => ({
    providerType: editProvider?.provider_type ?? 'openai',
    name: editProvider?.name ?? '',
    apiKey: '',
    baseUrl: editProvider?.base_url ?? PROVIDER_HINTS[editProvider?.provider_type ?? 'openai'],
    selectedModel: editProvider?.model_name ?? '',
    availableModels: [],
    modelSearch: '',
    showKey: false,
    temperature: String(editProvider?.temperature ?? 0.7),
    maxTokens: String(editProvider?.max_tokens ?? 4096),
    topP: editProvider?.top_p == null ? '' : String(editProvider.top_p),
    rpmLimit: String(editProvider?.rpm_limit ?? 60),
    tpmLimit: String(editProvider?.tpm_limit ?? 100000),
    fetchState: 'idle',
    fetchError: '',
    testState: 'idle',
    testLatency: null,
    testError: '',
  }))

  const canUseStoredCredentials = Boolean(
    isEdit &&
      editProvider &&
      !state.apiKey.trim() &&
      state.baseUrl.trim() === editProvider.base_url &&
      state.providerType === editProvider.provider_type
  )

  const filteredModels = useMemo(() => {
    const query = state.modelSearch.trim().toLowerCase()
    if (!query) return state.availableModels

    const score = (candidate: string) => {
      const value = candidate.toLowerCase()
      if (value === query) return 1000
      if (value.startsWith(query)) return 900 - value.length
      const index = value.indexOf(query)
      if (index >= 0) return 800 - index
      let qi = 0
      let penalty = 0
      for (const char of value) {
        if (char === query[qi]) {
          qi += 1
          if (qi === query.length) return 600 - penalty
        } else if (qi > 0) {
          penalty += 1
        }
      }
      return -1
    }

    return [...state.availableModels]
      .map((candidate) => ({ candidate, score: score(candidate) }))
      .filter((item) => item.score >= 0)
      .sort((a, b) => b.score - a.score)
      .map((item) => item.candidate)
  }, [state.availableModels, state.modelSearch])

  const canTest = useMemo(() => {
    if (!state.selectedModel.trim()) return false
    if (canUseStoredCredentials) return true
    return Boolean(state.apiKey.trim() && state.baseUrl.trim())
  }, [canUseStoredCredentials, state.apiKey, state.baseUrl, state.selectedModel])

  const update = useCallback((patch: Partial<ProviderModalState>) => {
    setState((prev) => {
      const next = { ...prev, ...patch }
      const providerTypeChanged =
        patch.providerType !== undefined && patch.providerType !== prev.providerType

      if (providerTypeChanged) {
        const nextType = patch.providerType as ProviderType
        const previousHint = PROVIDER_HINTS[prev.providerType]
        const shouldResetBaseUrl =
          !prev.baseUrl.trim() || prev.baseUrl.trim() === previousHint || !isEdit

        if (shouldResetBaseUrl) {
          next.baseUrl = PROVIDER_HINTS[nextType]
        }
      }

      if (patch.apiKey !== undefined || patch.baseUrl !== undefined || providerTypeChanged) {
        next.testState = 'idle'
        next.testError = ''
        next.testLatency = null
        next.fetchState = 'idle'
        next.fetchError = ''
        next.availableModels = []
        next.modelSearch = ''
        next.selectedModel = ''
      }

      if (patch.selectedModel !== undefined) {
        next.testState = 'idle'
        next.testError = ''
        next.testLatency = null
      }
      return next
    })
  }, [isEdit])

  const handleFetchModels = async () => {
    update({ fetchState: 'loading', fetchError: '' })
    try {
      const payload = canUseStoredCredentials && editProvider
        ? { provider_id: editProvider.id }
        : {
            provider_type: state.providerType,
            api_key: state.apiKey.trim(),
            base_url: state.baseUrl.trim(),
          }
      const result = await providersApi.fetchModels(payload)
      update({
        availableModels: result.models,
        fetchState: 'ready',
        modelSearch: '',
        selectedModel: state.selectedModel.trim() || result.models[0] || '',
        testState: 'idle',
        testError: '',
        testLatency: null,
      })
    } catch (error) {
      update({
        fetchState: 'error',
        fetchError: error instanceof Error ? error.message : '获取模型列表失败',
      })
    }
  }

  const handleTestConnection = async () => {
    if (!canTest) {
      update({ testState: 'error', testError: '请先选择模型并补全连接信息' })
      return
    }

    update({ testState: 'testing', testError: '', testLatency: null })

    try {
      if (canUseStoredCredentials && editProvider) {
        const result = await providersApi.testConnection({
          provider_id: editProvider.id,
          model_name: state.selectedModel.trim(),
        })
        update({
          testState: result.success ? 'success' : 'error',
          testLatency: result.latency_ms ?? null,
          testError: result.success ? '' : (result.error ?? '连接失败'),
        })
        return
      }

      const request: TestConnectionRequest = {
        provider_type: state.providerType,
        api_key: state.apiKey.trim(),
        base_url: state.baseUrl.trim(),
        model_name: state.selectedModel.trim(),
      }
      const result = await providersApi.testConnection(request)
      update({
        testState: result.success ? 'success' : 'error',
        testLatency: result.latency_ms ?? null,
        testError: result.success ? '' : (result.error ?? '连接失败'),
      })
    } catch (error) {
      update({
        testState: 'error',
        testError: error instanceof Error ? error.message : '连接失败',
      })
    }
  }

  const handleSave = async () => {
    await onSave(toProviderPayload(state))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-primary/20 backdrop-blur-sm">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-border bg-white p-8 shadow-lg">
        <div className="mb-6 flex items-center justify-between gap-4">
          <div>
            <h2 className="text-title-2 font-semibold text-primary">{isEdit ? '编辑提供商' : '添加提供商'}</h2>
            <p className="mt-1 text-callout text-secondary">只支持 OpenAI 官方和 OpenAI 兼容提供商。</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-secondary transition hover:bg-subtle hover:text-primary"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            {PROVIDER_TYPES.map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => update({ providerType: type })}
                className={`flex flex-col items-center gap-2 rounded-xl border-2 p-3 transition ${
                  state.providerType === type ? 'border-accent bg-accent/5' : 'border-border bg-white hover:border-accent/40 hover:bg-subtle'
                } cursor-pointer`}
              >
                <ProviderIcon type={type} />
                <span className={`text-callout font-medium ${state.providerType === type ? 'text-accent' : 'text-primary'}`}>
                  {PROVIDER_LABELS[type]}
                </span>
              </button>
            ))}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1">
              <span className="text-callout text-secondary">名称</span>
              <input
                value={state.name}
                onChange={(e) => update({ name: e.target.value })}
                placeholder={PROVIDER_LABELS[state.providerType]}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-callout text-secondary">Base URL</span>
              <input
                value={state.baseUrl}
                onChange={(e) => update({ baseUrl: e.target.value })}
                placeholder={PROVIDER_HINTS[state.providerType]}
                className="w-full rounded-xl border border-border font-mono text-body text-primary outline-none focus:border-accent"
              />
            </label>
          </div>

          <div className="space-y-1">
            <span className="text-callout text-secondary">API Key</span>
            <div className="relative">
              <input
                type={state.showKey ? 'text' : 'password'}
                value={state.apiKey}
                onChange={(e) => update({ apiKey: e.target.value })}
                placeholder={isEdit ? '输入新 Key 或留空保持不变' : 'sk-...'}
                className="w-full rounded-xl border border-border px-3 py-2 pr-10 font-mono text-body text-primary outline-none focus:border-accent"
              />
              <button
                type="button"
                onClick={() => update({ showKey: !state.showKey })}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-secondary transition hover:text-primary"
              >
                {state.showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            {isEdit && (
              <p className="text-caption text-tertiary">如果保持为空，系统会继续使用已保存的 API Key。</p>
            )}
          </div>

          <div className="rounded-2xl border border-border bg-subtle p-4 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={handleFetchModels}
                disabled={state.fetchState === 'loading' || (!state.apiKey.trim() && !canUseStoredCredentials)}
                className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {state.fetchState === 'loading' ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                获取模型列表
              </button>
              <button
                type="button"
                onClick={handleTestConnection}
                disabled={state.testState === 'testing' || !canTest}
                className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {state.testState === 'testing' ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                测试连接
              </button>
              <span className="text-caption text-secondary">
                {isEdit && !state.apiKey.trim()
                  ? '保留当前 API Key 的情况下可复用 provider 拉取模型。'
                  : '获取模型后可通过搜索快速筛选并选择。'}
              </span>
            </div>

            <div className={`rounded-xl border px-4 py-3 text-callout ${
              state.fetchState === 'error' || state.testState === 'error'
                ? 'border-red-200 bg-red-50 text-red-600'
                : state.testState === 'success'
                  ? 'border-green-200 bg-green-50 text-green-700'
                  : 'border-border bg-white text-secondary'
            }`}>
              {state.fetchError || state.testError || (state.testState === 'success' ? `连接成功${state.testLatency != null ? ` · ${state.testLatency}ms` : ''}` : '先获取模型，再选择合适的模型进行测试。')}
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1">
                <span className="text-caption text-secondary">模型名称</span>
                <input
                  value={state.selectedModel}
                  onChange={(e) => update({ selectedModel: e.target.value })}
                  placeholder="从列表中选择，或手动输入"
                  className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                />
              </label>
              <label className="space-y-1">
                <span className="text-caption text-secondary">搜索模型</span>
                <input
                  value={state.modelSearch}
                  onChange={(e) => setState((prev) => ({ ...prev, modelSearch: e.target.value }))}
                  placeholder="输入关键字模糊搜索"
                  className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                />
              </label>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-callout font-medium text-primary">模型列表</p>
                <p className="text-caption text-secondary">{filteredModels.length} 个候选</p>
              </div>
              <div className="max-h-56 overflow-y-auto rounded-xl border border-border bg-white p-2">
                {state.availableModels.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-caption text-secondary">
                    还没有模型列表，请先点击“获取模型列表”。
                  </div>
                ) : filteredModels.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-caption text-secondary">
                    没有匹配的模型。
                  </div>
                ) : (
                  filteredModels.map((model) => (
                    <button
                      key={model}
                      type="button"
                      onClick={() => update({ selectedModel: model })}
                      className={`mb-2 flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left transition ${
                        state.selectedModel === model
                          ? 'border-accent bg-accent/5 text-accent'
                          : 'border-border bg-white text-primary hover:border-accent/40 hover:bg-subtle'
                      }`}
                    >
                      <span className="font-mono text-callout">{model}</span>
                      {state.selectedModel === model && <ChevronDown className="h-4 w-4" />}
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <label className="space-y-1">
              <span className="text-callout text-secondary">temperature</span>
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={state.temperature}
                onChange={(e) => update({ temperature: e.target.value })}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-callout text-secondary">max_tokens</span>
              <input
                type="number"
                min="1"
                step="1"
                value={state.maxTokens}
                onChange={(e) => update({ maxTokens: e.target.value })}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-callout text-secondary">top_p</span>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={state.topP}
                onChange={(e) => update({ topP: e.target.value })}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
                placeholder="可选"
              />
            </label>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1">
              <span className="text-callout text-secondary">RPM 限制</span>
              <input
                type="number"
                min="1"
                step="1"
                value={state.rpmLimit}
                onChange={(e) => update({ rpmLimit: e.target.value })}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
              />
            </label>
            <label className="space-y-1">
              <span className="text-callout text-secondary">TPM 限制</span>
              <input
                type="number"
                min="1"
                step="1"
                value={state.tpmLimit}
                onChange={(e) => update({ tpmLimit: e.target.value })}
                className="w-full rounded-xl border border-border px-3 py-2 text-body text-primary outline-none focus:border-accent"
              />
            </label>
          </div>

          <div className="flex justify-end gap-3 border-t border-border pt-4">
            <button
              type="button"
              onClick={onClose}
              className="button-secondary"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || !state.selectedModel.trim() || state.testState !== 'success'}
              className="button-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSaving ? '保存中...' : '保存'}
            </button>
          </div>

          {isEdit && !state.apiKey.trim() && state.selectedModel !== editProvider?.model_name && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-caption text-amber-700">
              你当前沿用的是已保存的 API Key。现在可以直接测试新模型，无需重复输入 Key。
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function Providers() {
  const queryClient = useQueryClient()
  const { data: providerList = [], isLoading } = useQuery({
    queryKey: ['providers'],
    queryFn: providersApi.list,
  })

  const [showModal, setShowModal] = useState(false)
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null)
  const [testResults, setTestResults] = useState<Record<string, ProviderTestResult>>({})
  const [testingId, setTestingId] = useState<string | null>(null)

  const createMutation = useMutation({
    mutationFn: providersApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
      setShowModal(false)
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: CreateProviderForm }) => providersApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
      setEditingProvider(null)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: providersApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
  })

  const handleEdit = (provider: Provider) => {
    setShowModal(false)
    setEditingProvider(provider)
  }

  const handleSave = async (payload: CreateProviderForm) => {
    if (editingProvider) {
      await updateMutation.mutateAsync({ id: editingProvider.id, data: payload })
      return
    }
    await createMutation.mutateAsync(payload)
  }

  const handleTest = async (id: string) => {
    setTestingId(id)
    try {
      const result = await providersApi.test(id)
      setTestResults((prev) => ({ ...prev, [id]: result }))
    } catch (error) {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          status: 'failed',
          success: false,
          error: error instanceof Error ? error.message : '连接失败',
        },
      }))
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between gap-6">
        <div>
          <h1 className="text-display font-bold text-primary">LLM 提供商</h1>
          <p className="mt-2 text-callout text-secondary">只保留 OpenAI 官方和 OpenAI 兼容 provider，模型参数和速率限制都在这里管理。</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setEditingProvider(null)
            setShowModal(true)
          }}
          className="button-primary flex items-center gap-2"
        >
          <Plus className="h-4 w-4" />
          添加提供商
        </button>
      </div>

      {isLoading && (
        <div className="space-y-6">
          {Array.from({ length: 2 }).map((_, index) => (
            <div key={index} className="animate-pulse rounded-2xl border border-border bg-white p-6 shadow-xs">
              <div className="h-4 w-1/3 rounded bg-subtle" />
              <div className="mt-4 h-24 rounded-xl bg-subtle" />
            </div>
          ))}
        </div>
      )}

      {!isLoading && providerList.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-6 rounded-2xl border border-dashed border-border bg-white px-6 py-24 shadow-xs">
          <div className="rounded-2xl bg-subtle p-6">
            <Server className="h-12 w-12 text-secondary" />
          </div>
          <div className="text-center">
            <h2 className="text-title-2 font-semibold text-primary">暂无提供商</h2>
            <p className="mt-2 text-callout text-secondary">先添加一个 OpenAI 或 OpenAI 兼容 provider，然后获取模型列表并测试连接。</p>
          </div>
          <button type="button" onClick={() => setShowModal(true)} className="button-primary flex items-center gap-2">
            <Plus className="h-4 w-4" />
            添加第一个提供商
          </button>
        </div>
      )}

      {!isLoading && providerList.length > 0 && (
        <div className="space-y-6">
          {providerList.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onDelete={(id) => deleteMutation.mutate(id)}
              onEdit={handleEdit}
              onTest={handleTest}
              testResult={testResults[provider.id]}
              testing={testingId === provider.id}
            />
          ))}
        </div>
      )}

      {showModal && (
        <ProviderModal
          key="create"
          onClose={() => setShowModal(false)}
          onSave={handleSave}
          isSaving={createMutation.isPending || updateMutation.isPending}
        />
      )}

      {editingProvider && (
        <ProviderModal
          key={editingProvider.id}
          editProvider={editingProvider}
          onClose={() => setEditingProvider(null)}
          onSave={handleSave}
          isSaving={createMutation.isPending || updateMutation.isPending}
        />
      )}
    </div>
  )
}
