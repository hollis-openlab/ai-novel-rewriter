import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  CheckCircle2,
  Download,
  FileJson,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { config as configApi } from '@/lib/api'
import type {
  ConfigImportPreviewResponse,
  ConfigParseResponse,
  ConfigPatch,
  ConfigSnapshot,
  RewriteRule,
  RewriteRuleInput,
  RewriteStrategy,
  SceneRule,
} from '@/types'

type SceneRuleForm = {
  uiId: string
  id?: string
  scene_type: string
  triggerConditionsText: string
  weight: string
  enabled: boolean
  isNew?: boolean
}

type RewriteRuleForm = {
  uiId: string
  id?: string
  scene_type: string
  strategies: RewriteStrategy[]
  rewrite_guidance: string
  target_ratio: string
  target_chars: number
  priority: string
  enabled: boolean
  isNew?: boolean
}

const REWRITE_STRATEGY_ORDER: RewriteStrategy[] = ['expand', 'rewrite', 'condense', 'preserve']

const SAVE_SPINNER_STYLE = { animationDuration: '2.2s' } as const

const STORAGE_FILENAME = 'ai-novel-config.json'

const createUiId = () => globalThis.crypto?.randomUUID?.() ?? `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`

const normalizeRewriteStrategies = (strategies: RewriteStrategy[] | undefined, fallback?: RewriteStrategy): RewriteStrategy[] => {
  const source = strategies?.length ? strategies : fallback ? [fallback] : []
  const unique = Array.from(new Set(source))
  const ordered = REWRITE_STRATEGY_ORDER.filter((strategy) => unique.includes(strategy))
  return ordered.length > 0 ? ordered : ['rewrite']
}

const getPrimaryRewriteStrategy = (strategies: RewriteStrategy[]) => normalizeRewriteStrategies(strategies)[0] ?? 'rewrite'

const defaultSceneRule = (): SceneRuleForm => ({
  uiId: createUiId(),
  scene_type: '',
  triggerConditionsText: '',
  weight: '1',
  enabled: true,
  isNew: true,
})

const defaultRewriteRule = (sceneType = ''): RewriteRuleForm => ({
  uiId: createUiId(),
  scene_type: sceneType,
  strategies: ['expand', 'rewrite'],
  rewrite_guidance: '',
  target_ratio: '1',
  target_chars: 2000,
  priority: '0',
  enabled: true,
  isNew: true,
})

const normalizeSceneRule = (rule: SceneRule): SceneRuleForm => ({
  uiId: rule.id ?? createUiId(),
  id: rule.id,
  scene_type: rule.scene_type,
  triggerConditionsText: (rule.trigger_conditions ?? []).join('、'),
  weight: String(rule.weight ?? 1),
  enabled: rule.enabled,
})

const normalizeRewriteRule = (rule: RewriteRule): RewriteRuleForm => ({
  uiId: rule.id ?? createUiId(),
  id: rule.id,
  scene_type: rule.scene_type,
  strategies: normalizeRewriteStrategies(rule.strategies, rule.strategy),
  rewrite_guidance: rule.rewrite_guidance ?? '',
  target_ratio: String(rule.target_ratio ?? 1),
  target_chars: rule.target_chars ?? 2000,
  priority: String(rule.priority ?? 0),
  enabled: rule.enabled,
})

const splitTriggerConditions = (value: string): string[] =>
  Array.from(
    new Set(
      value
        .replace(
          /(?:识别点|触发条件|关键词|trigger(?:\s*_)?conditions?|keywords?)\s*[:：]\s*/gi,
          ''
        )
        .split(/[、,，/;；。\n]+/)
        .map((item) => item.trim())
        .filter(Boolean)
    )
  )

const parseNumber = (value: string, fallback = 0): number => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

const arrayShallowEqual = <T,>(left: T[], right: T[]) =>
  left.length === right.length && left.every((item, index) => item === right[index])

const sceneTypeKey = (value: string) => value.trim().toLowerCase()

const downloadJson = (filename: string, payload: unknown) => {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

const stringifySnapshot = (snapshot: ConfigSnapshot) => JSON.stringify(snapshot, null, 2)

function SectionCard({
  title,
  description,
  children,
  action,
}: {
  title: string
  description?: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="space-y-4 rounded-2xl border border-border bg-white p-6 shadow-xs">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-title-3 font-semibold text-primary">{title}</h2>
          {description && <p className="mt-1 text-callout text-secondary">{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </div>
  )
}

export function Config() {
  const { t } = useTranslation(['config', 'common'])
  const queryClient = useQueryClient()
  const { data: snapshot } = useQuery({
    queryKey: ['config', 'snapshot'],
    queryFn: configApi.getSnapshot,
  })

  const [globalPrompt, setGlobalPrompt] = useState('')
  const [rewriteGeneralGuidance, setRewriteGeneralGuidance] = useState('')
  const [sceneRules, setSceneRules] = useState<SceneRuleForm[]>([])
  const [rewriteRules, setRewriteRules] = useState<RewriteRuleForm[]>([])
  const [sceneRuleCollapsed, setSceneRuleCollapsed] = useState<Record<string, boolean>>({})
  const [rewriteRuleCollapsed, setRewriteRuleCollapsed] = useState<Record<string, boolean>>({})
  const [aiInput, setAiInput] = useState('')
  const [parseResult, setParseResult] = useState<ConfigParseResponse | null>(null)
  const [parseError, setParseError] = useState('')
  const [jsonText, setJsonText] = useState('')
  const [jsonMessage, setJsonMessage] = useState('')
  const [jsonPreview, setJsonPreview] = useState<ConfigImportPreviewResponse | null>(null)
  const [jsonPreviewError, setJsonPreviewError] = useState('')
  const skipNextSnapshotSyncRef = useRef(false)

  const syncSnapshot = useCallback((next: ConfigSnapshot) => {
    setGlobalPrompt(next.global_prompt ?? '')
    setRewriteGeneralGuidance(next.rewrite_general_guidance ?? '')
    setSceneRules(next.scene_rules.map(normalizeSceneRule))
    setRewriteRules(next.rewrite_rules.map(normalizeRewriteRule))
    setJsonText(stringifySnapshot(next))
    setJsonMessage('')
    setJsonPreview(null)
    setJsonPreviewError('')
    setParseResult(null)
    setParseError('')
  }, [])

  useEffect(() => {
    if (snapshot) {
      if (skipNextSnapshotSyncRef.current) {
        skipNextSnapshotSyncRef.current = false
        return
      }
      syncSnapshot(snapshot)
    }
  }, [snapshot, syncSnapshot])

  const applySnapshot = useCallback((next: ConfigSnapshot, options?: { preserveDraftRules?: boolean }) => {
    if (options?.preserveDraftRules) {
      skipNextSnapshotSyncRef.current = true
    }
    queryClient.setQueryData(['config', 'snapshot'], next)
    if (!options?.preserveDraftRules) {
      syncSnapshot(next)
      return
    }

    setGlobalPrompt(next.global_prompt ?? '')
    setRewriteGeneralGuidance(next.rewrite_general_guidance ?? '')
    setSceneRules((prev) => {
      const persisted = next.scene_rules.map(normalizeSceneRule)
      const persistedTypes = new Set(
        persisted
          .map((rule) => sceneTypeKey(rule.scene_type))
          .filter(Boolean)
      )
      const drafts = prev.filter((rule) => {
        if (!(rule.isNew || !rule.id)) return false
        const key = sceneTypeKey(rule.scene_type)
        return !key || !persistedTypes.has(key)
      })
      return [...persisted, ...drafts]
    })
    setRewriteRules((prev) => {
      const persisted = next.rewrite_rules.map(normalizeRewriteRule)
      const persistedTypes = new Set(
        persisted
          .map((rule) => sceneTypeKey(rule.scene_type))
          .filter(Boolean)
      )
      const drafts = prev.filter((rule) => {
        if (!(rule.isNew || !rule.id)) return false
        const key = sceneTypeKey(rule.scene_type)
        return !key || !persistedTypes.has(key)
      })
      return [...persisted, ...drafts]
    })
    setJsonText(stringifySnapshot(next))
    setJsonMessage('')
    setJsonPreview(null)
    setJsonPreviewError('')
    setParseResult(null)
    setParseError('')
  }, [queryClient, syncSnapshot])

  const parseMutation = useMutation({
    mutationFn: (instruction: string) => configApi.parseInstruction(instruction),
    onSuccess: (result) => {
      setParseResult(result)
      setParseError('')
      setJsonPreviewError('')
    },
    onError: (error) => {
      setParseResult(null)
      setParseError(error instanceof Error ? error.message : t('error.parseFailed'))
    },
  })

  const applyPatchMutation = useMutation({
    mutationFn: (patch: ConfigPatch) => configApi.applyPatch(patch),
    onSuccess: (next) => applySnapshot(next),
    onError: (error) => {
      setParseError(error instanceof Error ? error.message : t('error.applyFailed'))
    },
  })

  const updateGlobalPromptMutation = useMutation({
    mutationFn: (value: string) => configApi.updateGlobalPrompt(value),
    onSuccess: (next) => applySnapshot(next),
    onError: (error) => {
      setJsonMessage(error instanceof Error ? error.message : t('error.saveFailed'))
    },
  })

  const saveSceneRuleMutation = useMutation({
    mutationFn: async (payload: SceneRuleForm) => {
      const normalized = {
        scene_type: payload.scene_type.trim(),
        trigger_conditions: splitTriggerConditions(payload.triggerConditionsText),
        weight: parseNumber(payload.weight, 1),
        enabled: payload.enabled,
      }
      if (payload.id) {
        return configApi.updateSceneRule({ id: payload.id, ...normalized })
      }
      return configApi.createSceneRule(normalized)
    },
    onSuccess: (next) => applySnapshot(next, { preserveDraftRules: true }),
  })

  const deleteSceneRuleMutation = useMutation({
    mutationFn: (id: string) => configApi.deleteSceneRule(id),
    onSuccess: (next) => applySnapshot(next, { preserveDraftRules: true }),
  })

  const updateRewriteGuidanceMutation = useMutation({
    mutationFn: (value: string) => configApi.updateRewriteGeneralGuidance(value),
    onSuccess: (next) => applySnapshot(next),
    onError: (error) => {
      setJsonMessage(error instanceof Error ? error.message : t('error.saveFailed'))
    },
  })

  const saveRewriteRuleMutation = useMutation({
    mutationFn: async (payload: RewriteRuleForm) => {
      const strategies = normalizeRewriteStrategies(payload.strategies)
      const normalized: RewriteRuleInput = {
        scene_type: payload.scene_type.trim(),
        strategies,
        strategy: getPrimaryRewriteStrategy(strategies),
        rewrite_guidance: payload.rewrite_guidance.trim(),
        target_ratio: parseNumber(payload.target_ratio, 1),
        target_chars: payload.target_chars ?? 2000,
        priority: Math.max(0, parseInt(payload.priority || '0', 10) || 0),
        enabled: payload.enabled,
      }
      if (payload.id) {
        return configApi.updateRewriteRule({ id: payload.id, ...normalized })
      }
      return configApi.createRewriteRule(normalized)
    },
    onSuccess: (next) => applySnapshot(next, { preserveDraftRules: true }),
  })

  const deleteRewriteRuleMutation = useMutation({
    mutationFn: (id: string) => configApi.deleteRewriteRule(id),
    onSuccess: (next) => applySnapshot(next, { preserveDraftRules: true }),
  })

  const exportJsonMutation = useMutation({
    mutationFn: () => configApi.exportJson(),
    onSuccess: (next) => {
      setJsonText(stringifySnapshot(next))
      setJsonMessage(t('json.message.refreshed'))
      setJsonPreview(null)
      setJsonPreviewError('')
    },
  })

  const previewImportMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => configApi.previewImportJson(payload),
    onSuccess: (preview) => {
      setJsonPreview(preview)
      setJsonPreviewError('')
      setJsonMessage(t('json.message.generatePreview'))
    },
    onError: (error) => {
      setJsonPreview(null)
      setJsonPreviewError(error instanceof Error ? error.message : t('json.error.previewFailed'))
    },
  })

  const importJsonMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => configApi.importJson(payload),
    onSuccess: (next) => {
      applySnapshot(next)
      setJsonMessage(t('json.message.importSuccess'))
    },
    onError: (error) => {
      setJsonPreviewError(error instanceof Error ? error.message : t('json.error.importFailed'))
    },
  })

  const handleParseInstruction = async () => {
    const instruction = aiInput.trim()
    if (!instruction) return
    await parseMutation.mutateAsync(instruction)
  }

  const handleApplyPatch = async () => {
    if (!parseResult || parseResult.status !== 'ok') return
    await applyPatchMutation.mutateAsync(parseResult.patch)
    setAiInput('')
  }

  const handleAddSceneRule = () => {
    setSceneRules((prev) => [...prev, defaultSceneRule()])
  }

  const availableRewriteSceneTypes = useMemo(() => {
    const definedSceneTypes = sceneRules
      .map((item) => item.scene_type.trim())
      .filter(Boolean)
    const mappedSceneTypes = new Set(
      rewriteRules
        .map((item) => item.scene_type.trim())
        .filter(Boolean)
    )
    return definedSceneTypes.filter((sceneType) => !mappedSceneTypes.has(sceneType))
  }, [sceneRules, rewriteRules])

  const handleAddRewriteRule = () => {
    setRewriteRules((prev) => [...prev, defaultRewriteRule(availableRewriteSceneTypes[0] ?? '')])
  }

  const updateSceneDraft = (index: number, patch: Partial<SceneRuleForm>) => {
    setSceneRules((prev) => prev.map((item, currentIndex) => (currentIndex === index ? { ...item, ...patch } : item)))
  }

  const updateRewriteDraft = (index: number, patch: Partial<RewriteRuleForm>) => {
    setRewriteRules((prev) => prev.map((item, currentIndex) => (currentIndex === index ? { ...item, ...patch } : item)))
  }

  const toggleSceneRuleCollapsed = (uiId: string) => {
    setSceneRuleCollapsed((prev) => ({ ...prev, [uiId]: !prev[uiId] }))
  }

  const toggleRewriteRuleCollapsed = (uiId: string) => {
    setRewriteRuleCollapsed((prev) => ({ ...prev, [uiId]: !prev[uiId] }))
  }

  const selectableRewriteSceneTypes = (currentSceneType: string): string[] =>
    Array.from(
      new Set(
        [currentSceneType.trim(), ...availableRewriteSceneTypes].filter(Boolean)
      )
    )

  const handleSaveSceneRule = async (index: number) => {
    await saveSceneRuleMutation.mutateAsync(sceneRules[index])
  }

  const handleDeleteSceneRule = async (index: number) => {
    const rule = sceneRules[index]
    if (rule.isNew || !rule.id) {
      setSceneRules((prev) => prev.filter((_, currentIndex) => currentIndex !== index))
      return
    }
    await deleteSceneRuleMutation.mutateAsync(rule.id)
  }

  const handleSaveRewriteRule = async (index: number) => {
    await saveRewriteRuleMutation.mutateAsync(rewriteRules[index])
  }

  const handleDeleteRewriteRule = async (index: number) => {
    const rule = rewriteRules[index]
    if (rule.isNew || !rule.id) {
      setRewriteRules((prev) => prev.filter((_, currentIndex) => currentIndex !== index))
      return
    }
    await deleteRewriteRuleMutation.mutateAsync(rule.id)
  }

  const handleGlobalPromptSave = async () => {
    await updateGlobalPromptMutation.mutateAsync(globalPrompt)
  }

  const handleRewriteGuidanceSave = async () => {
    await updateRewriteGuidanceMutation.mutateAsync(rewriteGeneralGuidance)
  }

  const handleJsonExport = async () => {
    const next = await exportJsonMutation.mutateAsync()
    downloadJson(STORAGE_FILENAME, next)
  }

  const handleJsonValidate = () => {
    try {
      JSON.parse(jsonText)
      setJsonMessage(t('json.message.validated'))
      setJsonPreviewError('')
    } catch (error) {
      setJsonMessage('')
      setJsonPreviewError(error instanceof Error ? error.message : t('error.jsonFormatError'))
    }
  }

  const handleJsonPreviewImport = async () => {
    try {
      const payload = JSON.parse(jsonText) as Record<string, unknown>
      await previewImportMutation.mutateAsync(payload)
    } catch (error) {
      setJsonPreview(null)
      setJsonPreviewError(error instanceof Error ? error.message : t('error.jsonFormatError'))
    }
  }

  const handleJsonImport = async () => {
    try {
      const payload = JSON.parse(jsonText) as Record<string, unknown>
      await importJsonMutation.mutateAsync(payload)
    } catch (error) {
      setJsonPreviewError(error instanceof Error ? error.message : t('error.jsonFormatError'))
    }
  }

  const handleJsonFile = (file: File | null) => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setJsonText(String(reader.result ?? ''))
      setJsonMessage(t('json.message.fileLoaded'))
      setJsonPreview(null)
      setJsonPreviewError('')
    }
    reader.readAsText(file)
  }

  const patchHasChanges = useMemo(() => {
    if (!parseResult) return false
    const patch = parseResult.patch
    return Boolean(
      patch.global_prompt !== undefined ||
      patch.rewrite_general_guidance !== undefined ||
      (patch.scene_rules && patch.scene_rules.length > 0) ||
      (patch.rewrite_rules && patch.rewrite_rules.length > 0)
    )
  }, [parseResult])

  const isParsingBusy = parseMutation.isPending || applyPatchMutation.isPending
  const isImportBusy = previewImportMutation.isPending || importJsonMutation.isPending
  const isGlobalPromptDirty = snapshot ? globalPrompt !== (snapshot.global_prompt ?? '') : false
  const isRewriteGuidanceDirty = snapshot ? rewriteGeneralGuidance !== (snapshot.rewrite_general_guidance ?? '') : false

  const sceneRuleSnapshotMap = useMemo(
    () => new Map((snapshot?.scene_rules ?? []).map((rule) => [rule.id, rule])),
    [snapshot]
  )

  const rewriteRuleSnapshotMap = useMemo(
    () => new Map((snapshot?.rewrite_rules ?? []).map((rule) => [rule.id, rule])),
    [snapshot]
  )

  const isSceneRuleDirty = useCallback((rule: SceneRuleForm) => {
    if (rule.isNew || !rule.id) {
      return true
    }
    const baseline = sceneRuleSnapshotMap.get(rule.id)
    if (!baseline) {
      return true
    }
    const nextSceneType = rule.scene_type.trim()
    const nextTriggerConditions = splitTriggerConditions(rule.triggerConditionsText)
    const nextWeight = parseNumber(rule.weight, 1)
    return !(
      baseline.scene_type === nextSceneType &&
      arrayShallowEqual(baseline.trigger_conditions ?? [], nextTriggerConditions) &&
      baseline.weight === nextWeight &&
      baseline.enabled === rule.enabled
    )
  }, [sceneRuleSnapshotMap])

  const isRewriteRuleDirty = useCallback((rule: RewriteRuleForm) => {
    if (rule.isNew || !rule.id) {
      return true
    }
    const baseline = rewriteRuleSnapshotMap.get(rule.id)
    if (!baseline) {
      return true
    }
    const nextSceneType = rule.scene_type.trim()
    const nextStrategies = normalizeRewriteStrategies(rule.strategies)
    const baselineStrategies = normalizeRewriteStrategies(baseline.strategies, baseline.strategy)
    const nextTargetRatio = parseNumber(rule.target_ratio, 1)
    const nextPriority = Math.max(0, parseInt(rule.priority || '0', 10) || 0)
    const nextTargetChars = rule.target_chars ?? 2000
    const baselineTargetChars = baseline.target_chars ?? 2000
    return !(
      baseline.scene_type === nextSceneType &&
      arrayShallowEqual(baselineStrategies, nextStrategies) &&
      (baseline.rewrite_guidance ?? '') === rule.rewrite_guidance.trim() &&
      baseline.target_ratio === nextTargetRatio &&
      baselineTargetChars === nextTargetChars &&
      baseline.priority === nextPriority &&
      baseline.enabled === rule.enabled
    )
  }, [rewriteRuleSnapshotMap])

  const aiHelpText = parseResult?.status === 'clarification_needed'
    ? parseResult.clarification ?? t('ai.clarificationFallback')
    : parseError || t('ai.helpText')

  const renderRuleCount = t('ruleCount', { scene: sceneRules.length, rewrite: rewriteRules.length })

  const summarizeSceneRule = (rule: SceneRuleForm) =>
    [
      rule.scene_type || t('sceneRules.unnamed'),
      rule.triggerConditionsText
        ? t('sceneRules.triggerConditions', { value: rule.triggerConditionsText })
        : t('sceneRules.noTriggerConditions'),
      `weight=${rule.weight || '1'}`,
    ].join(' · ')

  const summarizeRewriteStrategies = (strategies: RewriteStrategy[]) =>
    normalizeRewriteStrategies(strategies)
      .map((strategy) => t(`common:rewriteStrategy.${strategy}`))
      .join('、')

  const summarizeRewriteRule = (rule: RewriteRuleForm) => {
    const strategies = normalizeRewriteStrategies(rule.strategies)
    const primaryStrategy = getPrimaryRewriteStrategy(strategies)
    return [
      rule.scene_type || t('rewriteRules.unnamed'),
      t('rewriteRules.primaryStrategy', { value: t(`common:rewriteStrategy.${primaryStrategy}`) }),
      t('rewriteRules.combination', { value: summarizeRewriteStrategies(strategies) }),
      rule.rewrite_guidance.trim() ? t('rewriteRules.guidanceSet') : t('rewriteRules.guidanceNotSet'),
      `target_ratio=${rule.target_ratio || '1'}`,
      `priority=${rule.priority || '0'}`,
    ].join(' · ')
  }

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h1 className="text-display font-bold text-primary">{t('pageTitle')}</h1>
          <p className="mt-2 text-callout text-secondary">{t('pageDescription')}</p>
        </div>
        <div className="rounded-2xl border border-border bg-subtle px-4 py-3 text-right">
          <p className="text-caption text-secondary">{t('currentConfig')}</p>
          <p className="text-callout font-medium text-primary">{renderRuleCount}</p>
        </div>
      </div>

      <SectionCard
        title={t('ai.sectionTitle')}
        description={t('ai.sectionDescription')}
        action={
          <div className="flex items-center gap-2 text-caption text-secondary">
            {isParsingBusy && <Loader2 className="h-4 w-4 animate-spin" />}
            <span>{isParsingBusy ? t('ai.processing') : t('ai.realtime')}</span>
          </div>
        }
      >
        <div className="relative">
          <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-tertiary" />
          <input
            value={aiInput}
            onChange={(e) => setAiInput(e.target.value)}
            onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleParseInstruction()
              }
            }}
            placeholder={t('ai.inputPlaceholder')}
            className="w-full rounded-xl border border-border bg-subtle py-4 pl-12 pr-4 text-body text-primary outline-none transition focus:border-accent focus:bg-white focus:shadow-[0_0_24px_rgba(99,102,241,0.15)]"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          {([
            'ai.chip.simplifyPrompt',
            'ai.chip.addSceneRule',
            'ai.chip.setGeneralGuidance',
            'ai.chip.addRewriteRule',
          ] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setAiInput(t(key))}
              className="rounded-xl border border-border bg-white px-3 py-1.5 text-caption text-secondary transition hover:border-accent hover:text-accent"
            >
              {t(key)}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handleParseInstruction}
            disabled={!aiInput.trim() || parseMutation.isPending}
            className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {parseMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            {t('ai.parseButton')}
          </button>
          <button
            type="button"
            onClick={() => {
              setAiInput('')
              setParseResult(null)
              setParseError('')
            }}
            className="button-secondary flex items-center gap-2"
          >
            <X className="h-4 w-4" />
            {t('common:action.clear')}
          </button>
        </div>

        <div className={`rounded-xl border px-4 py-3 text-callout ${parseResult?.status === 'clarification_needed' ? 'border-amber-200 bg-amber-50 text-amber-700' : parseError ? 'border-red-200 bg-red-50 text-red-600' : 'border-border bg-subtle text-secondary'}`}>
          {aiHelpText}
        </div>

        {parseResult && (
          <div className="space-y-4 rounded-xl border border-border bg-subtle p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-callout font-medium text-primary">{t('ai.preview.title')}</p>
                <p className="text-caption text-secondary">{t('ai.preview.suggestions', { count: parseResult.diff_summary.length })}</p>
              </div>
              <button
                type="button"
                onClick={handleApplyPatch}
                disabled={!patchHasChanges || parseResult.status !== 'ok' || applyPatchMutation.isPending}
                className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {applyPatchMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                {t('ai.preview.applyButton')}
              </button>
            </div>

            {parseResult.diff_summary.length > 0 && (
              <div className="space-y-2">
                {parseResult.diff_summary.map((item, index) => (
                  <div key={`${item}-${index}`} className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                    {item}
                  </div>
                ))}
              </div>
            )}

            <div className="rounded-xl border border-border bg-white p-3">
              <p className="text-caption font-medium uppercase tracking-wide text-secondary">Patch</p>
              <pre className="mt-2 overflow-x-auto text-caption text-primary">{JSON.stringify(parseResult.patch, null, 2)}</pre>
            </div>
          </div>
        )}
      </SectionCard>

      <div className="grid gap-6 xl:grid-cols-2">
        <SectionCard
          title={t('globalPrompt.sectionTitle')}
          description={t('globalPrompt.sectionDescription')}
          action={
            <button
              type="button"
              onClick={handleGlobalPromptSave}
              disabled={updateGlobalPromptMutation.isPending || !isGlobalPromptDirty}
              className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {updateGlobalPromptMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <Download className="h-4 w-4" />}
              {t('common:action.save')}
            </button>
          }
        >
          <textarea
            value={globalPrompt}
            onChange={(e) => setGlobalPrompt(e.target.value)}
            rows={12}
            className="w-full rounded-xl border border-border bg-subtle px-4 py-3 font-mono text-caption text-primary outline-none transition focus:border-accent focus:bg-white"
            placeholder={t('globalPrompt.placeholder')}
          />
        </SectionCard>

        <SectionCard
          title={t('sceneRules.sectionTitle')}
          description={t('sceneRules.sectionDescription')}
          action={
            <button
              type="button"
              onClick={handleAddSceneRule}
              className="button-secondary flex items-center gap-2"
            >
              <Plus className="h-4 w-4" />
              {t('sceneRules.addButton')}
            </button>
          }
        >
          <div className="space-y-4">
            {sceneRules.length === 0 && (
              <div className="rounded-xl border border-dashed border-border bg-subtle px-4 py-8 text-center text-callout text-secondary">
                {t('sceneRules.empty')}
              </div>
            )}

            {sceneRules.map((rule, index) => (
              <div key={rule.uiId} className={`space-y-3 rounded-xl border p-4 ${rule.isNew ? 'border-dashed border-accent/40 bg-accent/5' : 'border-border bg-subtle'}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="rounded-full bg-white px-2 py-1 text-caption font-medium text-secondary">#{index + 1}</span>
                      <span className="text-callout font-medium text-primary">{rule.scene_type || t('sceneRules.unnamed')}</span>
                      {rule.isNew && (
                        <span className="rounded-full bg-accent/10 px-2 py-1 text-caption font-medium text-accent">new</span>
                      )}
                    </div>
                    <p className="text-caption text-secondary">{summarizeSceneRule(rule)}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-2 text-caption text-secondary">
                      <input
                        type="checkbox"
                        checked={rule.enabled}
                        onChange={(e) => updateSceneDraft(index, { enabled: e.target.checked })}
                      />
                      {t('common:action.enable')}
                    </label>
                    <button
                      type="button"
                      onClick={() => toggleSceneRuleCollapsed(rule.uiId)}
                      className="button-secondary flex items-center gap-2"
                    >
                      {sceneRuleCollapsed[rule.uiId] ? t('common:action.expand') : t('common:action.collapse')}
                    </button>
                  </div>
                </div>

                {!sceneRuleCollapsed[rule.uiId] && (
                  <>
                    <div className="grid gap-3 md:grid-cols-2">
                      <label className="space-y-1">
                        <span className="text-caption text-secondary">{t('sceneRules.field.sceneType')}</span>
                        <input
                          value={rule.scene_type}
                          onChange={(e) => updateSceneDraft(index, { scene_type: e.target.value })}
                          className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                          placeholder={t('sceneRules.placeholder.sceneType')}
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-caption text-secondary">{t('sceneRules.field.weight')}</span>
                        <input
                          type="number"
                          step="0.1"
                          min="0"
                          value={rule.weight}
                          onChange={(e) => updateSceneDraft(index, { weight: e.target.value })}
                          className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                        />
                      </label>
                    </div>

                    <label className="block space-y-1">
                      <span className="text-caption text-secondary">{t('sceneRules.field.triggerConditions')}</span>
                      <textarea
                        value={rule.triggerConditionsText}
                        onChange={(e) => updateSceneDraft(index, { triggerConditionsText: e.target.value })}
                        rows={3}
                        className="w-full rounded-xl border border-border bg-white px-3 py-2 font-mono text-caption text-primary outline-none focus:border-accent"
                        placeholder={t('sceneRules.placeholder.triggerConditions')}
                      />
                    </label>
                  </>
                )}

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => handleSaveSceneRule(index)}
                    disabled={saveSceneRuleMutation.isPending || !isSceneRuleDirty(rule)}
                    className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {saveSceneRuleMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <RefreshCw className="h-4 w-4" />}
                    {t('common:action.save')}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteSceneRule(index)}
                    className="button-secondary flex items-center gap-2 text-red-500 hover:text-red-600"
                  >
                    <Trash2 className="h-4 w-4" />
                    {t('common:action.delete')}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      <SectionCard
        title={t('rewriteRules.sectionTitle')}
        description={t('rewriteRules.sectionDescription')}
        action={
          <button
            type="button"
            onClick={handleAddRewriteRule}
            disabled={availableRewriteSceneTypes.length === 0}
            className="button-secondary flex items-center gap-2"
          >
            <Plus className="h-4 w-4" />
            {t('rewriteRules.addButton')}
          </button>
        }
      >
        <div className="space-y-4">
          <div className="space-y-2 rounded-xl border border-border bg-subtle p-4">
            <div className="flex items-center justify-between gap-4">
              <p className="text-callout font-medium text-primary">{t('rewriteRules.generalGuidance.title')}</p>
              <button
                type="button"
                onClick={handleRewriteGuidanceSave}
                disabled={updateRewriteGuidanceMutation.isPending || !isRewriteGuidanceDirty}
                className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {updateRewriteGuidanceMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <RefreshCw className="h-4 w-4" />}
                {t('common:action.save')}
              </button>
            </div>
            <textarea
              value={rewriteGeneralGuidance}
              onChange={(e) => setRewriteGeneralGuidance(e.target.value)}
              rows={4}
              className="w-full rounded-xl border border-border bg-white px-3 py-2 font-mono text-caption text-primary outline-none focus:border-accent"
              placeholder={t('rewriteRules.generalGuidance.placeholder')}
            />
          </div>

          {availableRewriteSceneTypes.length === 0 && sceneRules.length > 0 && (
            <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-secondary">
              {t('rewriteRules.allBound')}
            </div>
          )}

          {rewriteRules.length === 0 && (
            <div className="rounded-xl border border-dashed border-border bg-subtle px-4 py-8 text-center text-callout text-secondary">
              {t('rewriteRules.empty')}
            </div>
          )}

          {rewriteRules.map((rule, index) => (
            <div key={rule.uiId} className={`space-y-3 rounded-xl border p-4 ${rule.isNew ? 'border-dashed border-accent/40 bg-accent/5' : 'border-border bg-subtle'}`}>
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-white px-2 py-1 text-caption font-medium text-secondary">#{index + 1}</span>
                    <span className="text-callout font-medium text-primary">{rule.scene_type || t('rewriteRules.unnamed')}</span>
                    {rule.isNew && (
                      <span className="rounded-full bg-accent/10 px-2 py-1 text-caption font-medium text-accent">new</span>
                    )}
                  </div>
                  <p className="text-caption text-secondary">{summarizeRewriteRule(rule)}</p>
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-caption text-secondary">
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={(e) => updateRewriteDraft(index, { enabled: e.target.checked })}
                    />
                    {t('common:action.enable')}
                  </label>
                  <button
                    type="button"
                    onClick={() => toggleRewriteRuleCollapsed(rule.uiId)}
                    className="button-secondary flex items-center gap-2"
                  >
                    {rewriteRuleCollapsed[rule.uiId] ? t('common:action.expand') : t('common:action.collapse')}
                  </button>
                </div>
              </div>

              {!rewriteRuleCollapsed[rule.uiId] && (
                <>
                  <div className="grid gap-3 lg:grid-cols-4">
                    <label className="space-y-1 lg:col-span-2">
                      <span className="text-caption text-secondary">{t('rewriteRules.field.sceneType')}</span>
                      <select
                        value={rule.scene_type}
                        onChange={(e) => updateRewriteDraft(index, { scene_type: e.target.value })}
                        className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                      >
                        <option value="">{t('rewriteRules.selectScene')}</option>
                        {selectableRewriteSceneTypes(rule.scene_type).map((sceneType) => (
                          <option key={`${sceneType}-${index}`} value={sceneType}>
                            {sceneType}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="space-y-1">
                      <span className="text-caption text-secondary">{t('rewriteRules.field.priority')}</span>
                      <input
                        type="number"
                        min="0"
                        step="1"
                        value={rule.priority}
                        onChange={(e) => updateRewriteDraft(index, { priority: e.target.value })}
                        className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                      />
                    </label>
                    <label className="space-y-1">
                      <span className="text-caption text-secondary">{t('rewriteRules.field.targetRatio')}</span>
                      <input
                        type="number"
                        min="0.1"
                        step="0.1"
                        value={rule.target_ratio}
                        onChange={(e) => updateRewriteDraft(index, { target_ratio: e.target.value })}
                        className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                      />
                    </label>
                    <label className="block">
                      <span className="text-caption text-secondary">{t('rewriteRules.field.targetChars')}</span>
                      <input
                        type="number"
                        min="1"
                        step="100"
                        value={rule.target_chars ?? 2000}
                        placeholder={t('rewriteRules.field.targetCharsPlaceholder')}
                        onChange={(e) => updateRewriteDraft(index, { target_chars: e.target.value ? Number(e.target.value) : 2000 })}
                        className="w-full rounded-xl border border-border bg-white px-3 py-2 text-body text-primary outline-none focus:border-accent"
                      />
                    </label>
                  </div>

                  <div className="space-y-2 rounded-xl border border-border bg-white p-3">
                    <div className="flex items-center justify-between gap-4">
                      <span className="text-caption text-secondary">{t('rewriteRules.field.strategies')}</span>
                      <span className="text-caption text-secondary">
                        {t('rewriteRules.field.primaryStrategy', { value: t(`common:rewriteStrategy.${getPrimaryRewriteStrategy(rule.strategies)}`) })}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {REWRITE_STRATEGY_ORDER.map((strategy) => {
                        const checked = rule.strategies.includes(strategy)
                        return (
                          <label
                            key={`${rule.uiId}-${strategy}`}
                            className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-caption transition ${
                              checked ? 'border-accent bg-accent/10 text-accent' : 'border-border bg-subtle text-secondary'
                            }`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const nextStrategies = e.target.checked
                                  ? normalizeRewriteStrategies([...rule.strategies, strategy])
                                  : normalizeRewriteStrategies(rule.strategies.filter((item) => item !== strategy))
                                updateRewriteDraft(index, { strategies: nextStrategies })
                              }}
                            />
                            {t(`common:rewriteStrategy.${strategy}`)}
                          </label>
                        )
                      })}
                    </div>
                  </div>

                  <label className="block space-y-1">
                    <span className="text-caption text-secondary">{t('rewriteRules.field.sceneGuidance')}</span>
                    <textarea
                      value={rule.rewrite_guidance}
                      onChange={(e) => updateRewriteDraft(index, { rewrite_guidance: e.target.value })}
                      rows={3}
                      className="w-full rounded-xl border border-border bg-white px-3 py-2 font-mono text-caption text-primary outline-none focus:border-accent"
                      placeholder={t('rewriteRules.field.sceneGuidancePlaceholder')}
                    />
                  </label>
                </>
              )}

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => handleSaveRewriteRule(index)}
                  disabled={saveRewriteRuleMutation.isPending || !isRewriteRuleDirty(rule)}
                  className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {saveRewriteRuleMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" style={SAVE_SPINNER_STYLE} /> : <RefreshCw className="h-4 w-4" />}
                  {t('common:action.save')}
                </button>
                <button
                  type="button"
                  onClick={() => handleDeleteRewriteRule(index)}
                  className="button-secondary flex items-center gap-2 text-red-500 hover:text-red-600"
                >
                  <Trash2 className="h-4 w-4" />
                  {t('common:action.delete')}
                </button>
              </div>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard
        title={t('json.sectionTitle')}
        description={t('json.sectionDescription')}
        action={
          <div className="flex items-center gap-2 text-caption text-secondary">
            {isImportBusy && <Loader2 className="h-4 w-4 animate-spin" />}
            <span>{isImportBusy ? t('json.processing') : t('json.editor')}</span>
          </div>
        }
      >
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleJsonExport}
            disabled={exportJsonMutation.isPending}
            className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {exportJsonMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {t('json.exportButton')}
          </button>
          <button
            type="button"
            onClick={handleJsonValidate}
            className="button-secondary flex items-center gap-2"
          >
            <FileJson className="h-4 w-4" />
            {t('json.validateButton')}
          </button>
          <button
            type="button"
            onClick={handleJsonPreviewImport}
            disabled={previewImportMutation.isPending}
            className="button-secondary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {previewImportMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            {t('json.previewButton')}
          </button>
          <button
            type="button"
            onClick={handleJsonImport}
            disabled={importJsonMutation.isPending}
            className="button-primary flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {importJsonMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
            {t('json.importButton')}
          </button>
          <label className="button-secondary flex cursor-pointer items-center gap-2">
            <input
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => handleJsonFile(e.target.files?.[0] ?? null)}
            />
            <Upload className="h-4 w-4" />
            {t('json.loadFromFile')}
          </label>
        </div>

        {jsonMessage && (
          <div className="rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-caption text-green-700">
            {jsonMessage}
          </div>
        )}

        {jsonPreviewError && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-caption text-red-600">
            {jsonPreviewError}
          </div>
        )}

        {jsonPreview && (
          <div className="space-y-3 rounded-xl border border-border bg-subtle p-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-callout font-medium text-primary">{t('json.preview.title')}</p>
                <p className="text-caption text-secondary">{t('json.preview.description')}</p>
              </div>
              <span className="rounded-full bg-white px-3 py-1 text-caption text-secondary">preview</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.globalPromptChanged', { value: jsonPreview.summary.global_prompt_changed ? t('json.preview.globalPromptYes') : t('json.preview.globalPromptNo') })}
              </div>
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.sceneAdded', { count: jsonPreview.summary.scene_rules_added })}
              </div>
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.sceneUpdated', { count: jsonPreview.summary.scene_rules_updated })}
              </div>
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.rewriteAdded', { count: jsonPreview.summary.rewrite_rules_added })}
              </div>
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.rewriteUpdated', { count: jsonPreview.summary.rewrite_rules_updated })}
              </div>
              <div className="rounded-xl border border-border bg-white px-3 py-2 text-caption text-primary">
                {t('json.preview.conflicts', { count: jsonPreview.summary.conflicts.length })}
              </div>
            </div>
            {jsonPreview.summary.conflicts.length > 0 && (
              <div className="space-y-2">
                {jsonPreview.summary.conflicts.map((item) => (
                  <div key={item} className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-caption text-amber-700">
                    {item}
                  </div>
                ))}
              </div>
            )}
            <div className="rounded-xl border border-border bg-white p-3">
              <pre className="overflow-x-auto text-caption text-primary">{stringifySnapshot(jsonPreview.snapshot)}</pre>
            </div>
          </div>
        )}

        <textarea
          value={jsonText}
          onChange={(e) => {
            setJsonText(e.target.value)
            setJsonPreview(null)
          }}
          rows={20}
          className="w-full rounded-xl border border-border bg-white px-4 py-3 font-mono text-caption text-primary outline-none transition focus:border-accent"
          placeholder={t('json.placeholder')}
        />
      </SectionCard>
    </div>
  )
}
