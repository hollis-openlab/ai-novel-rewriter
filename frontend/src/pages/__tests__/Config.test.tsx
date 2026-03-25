import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Config } from '@/pages/Config'
import { renderWithProviders } from '@/test/utils'
import type { ConfigParseResponse, ConfigSnapshot } from '@/types'

const baseSnapshot: ConfigSnapshot = {
  version: '1',
  global_prompt: '旧提示词',
  rewrite_general_guidance: '',
  scene_rules: [],
  rewrite_rules: [],
  updated_at: null,
}

const updatedSnapshot: ConfigSnapshot = {
  ...baseSnapshot,
  global_prompt: '新版提示词',
}

const rulesSnapshot: ConfigSnapshot = {
  ...baseSnapshot,
  scene_rules: [
    {
      id: 'scene-1',
      scene_type: '战斗',
      trigger_conditions: ['厮杀', '交锋'],
      weight: 1.2,
      enabled: true,
    },
  ],
  rewrite_rules: [
    {
      id: 'rewrite-1',
      scene_type: '战斗',
      strategies: ['expand', 'rewrite'],
      strategy: 'expand',
      rewrite_guidance: '',
      target_ratio: 2.2,
      priority: 1,
      enabled: true,
    },
  ],
}

const sceneSavedSnapshot: ConfigSnapshot = {
  ...baseSnapshot,
  scene_rules: [
    {
      id: 'scene-server-1',
      scene_type: '战斗',
      trigger_conditions: ['厮杀'],
      weight: 1,
      enabled: true,
    },
  ],
  rewrite_rules: [
    {
      id: 'rewrite-server-1',
      scene_type: '战斗',
      strategies: ['rewrite'],
      strategy: 'rewrite',
      rewrite_guidance: '',
      target_ratio: 1,
      priority: 0,
      enabled: true,
    },
  ],
}

const { configApiMock } = vi.hoisted(() => ({
  configApiMock: {
    getSnapshot: vi.fn<() => Promise<ConfigSnapshot>>(),
    parseInstruction: vi.fn<(instruction: string) => Promise<ConfigParseResponse>>(),
    applyPatch: vi.fn(),
    updateGlobalPrompt: vi.fn(),
    updateRewriteGeneralGuidance: vi.fn(),
    createSceneRule: vi.fn(),
    updateSceneRule: vi.fn(),
    deleteSceneRule: vi.fn(),
    createRewriteRule: vi.fn(),
    updateRewriteRule: vi.fn(),
    deleteRewriteRule: vi.fn(),
    exportJson: vi.fn(),
    previewImportJson: vi.fn(),
    importJson: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  config: configApiMock,
}))

describe('Config page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    configApiMock.getSnapshot.mockResolvedValue(baseSnapshot)
    configApiMock.applyPatch.mockResolvedValue(updatedSnapshot)
    configApiMock.updateGlobalPrompt.mockResolvedValue(updatedSnapshot)
    configApiMock.updateRewriteGeneralGuidance.mockResolvedValue(updatedSnapshot)
    configApiMock.updateSceneRule.mockResolvedValue(baseSnapshot)
    configApiMock.createRewriteRule.mockResolvedValue(baseSnapshot)
    configApiMock.updateRewriteRule.mockResolvedValue(baseSnapshot)
    configApiMock.exportJson.mockResolvedValue(baseSnapshot)
    configApiMock.previewImportJson.mockResolvedValue({
      status: 'preview',
      summary: {
        global_prompt_changed: false,
        scene_rules_added: 0,
        scene_rules_updated: 0,
        rewrite_rules_added: 0,
        rewrite_rules_updated: 0,
        conflicts: [],
      },
      snapshot: baseSnapshot,
      requires_confirmation: true,
    })
    configApiMock.importJson.mockResolvedValue(baseSnapshot)
  })

  it('applies AI Config Bar patch and syncs global prompt + JSON editor', async () => {
    const user = userEvent.setup()
    configApiMock.parseInstruction.mockResolvedValue({
      status: 'ok',
      clarification: null,
      diff_summary: ['global_prompt: 旧提示词 -> 新版提示词'],
      patch: { global_prompt: '新版提示词' },
      snapshot: updatedSnapshot,
    })

    renderWithProviders(<Config />)

    await screen.findByText('配置中心')

    const aiInput = screen.getByPlaceholderText(/例如：把全局提示词改成/)
    await user.type(aiInput, '把全局提示词改成：新版提示词')
    await user.click(screen.getByRole('button', { name: '解析变更' }))

    await screen.findByText('预览结果')
    await user.click(screen.getByRole('button', { name: '确认应用' }))

    const promptEditor = screen.getByPlaceholderText('输入全局提示词...')
    const jsonEditor = screen.getByPlaceholderText('在这里查看或编辑 JSON 配置...')

    await waitFor(() => {
      expect(promptEditor).toHaveValue('新版提示词')
    })
    expect(String((jsonEditor as HTMLTextAreaElement).value)).toContain('新版提示词')
    expect(configApiMock.parseInstruction).toHaveBeenCalledWith('把全局提示词改成：新版提示词')
    expect(configApiMock.applyPatch).toHaveBeenCalledWith({ global_prompt: '新版提示词' })
  })

  it('saves global prompt through dedicated editor', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Config />)

    await screen.findByText('配置中心')
    const promptEditor = screen.getByPlaceholderText('输入全局提示词...')
    const saveButtons = screen.getAllByRole('button', { name: '保存' })

    expect(saveButtons[0]).toBeDisabled()

    await user.clear(promptEditor)
    await user.type(promptEditor, '用于测试保存的提示词')
    expect(saveButtons[0]).not.toBeDisabled()
    await user.click(saveButtons[0])

    await waitFor(() => {
      expect(configApiMock.updateGlobalPrompt).toHaveBeenCalledWith(
        expect.stringContaining('用于测试保存的提示词')
      )
    })
  })

  it('collapses rule cards and sends multi-strategy rewrite payload', async () => {
    const user = userEvent.setup()
    configApiMock.getSnapshot.mockResolvedValue(rulesSnapshot)

    renderWithProviders(<Config />)

    await screen.findByText('战斗 · 主策略：拓展 · 组合：拓展、改写 · 未配置场景指导 · target_ratio=2.2 · priority=1')

    const collapseButtons = screen.getAllByRole('button', { name: '收起' })
    await user.click(collapseButtons[0])

    expect(screen.queryByPlaceholderText('例如：战斗')).not.toBeInTheDocument()
    expect(screen.getByText('战斗 · 触发条件：厮杀、交锋 · weight=1.2')).toBeInTheDocument()
    expect(screen.getByText('战斗 · 主策略：拓展 · 组合：拓展、改写 · 未配置场景指导 · target_ratio=2.2 · priority=1')).toBeInTheDocument()

    let saveButtons = screen.getAllByRole('button', { name: '保存' })
    expect(saveButtons[saveButtons.length - 1]).toBeDisabled()

    const condenseCheckbox = screen.getByLabelText('精简')
    await user.click(condenseCheckbox)
    const ruleTextarea = screen.getByPlaceholderText('例如：战斗场景增加动作细节与心理张力，但不新增人物设定。')
    await user.type(ruleTextarea, '尽量强化动作细节，不改动剧情事实。')

    saveButtons = screen.getAllByRole('button', { name: '保存' })
    expect(saveButtons[saveButtons.length - 1]).not.toBeDisabled()
    await user.click(saveButtons[saveButtons.length - 1])

    await waitFor(() => {
      expect(configApiMock.updateRewriteRule).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'rewrite-1',
          scene_type: '战斗',
          strategies: ['expand', 'rewrite', 'condense'],
          strategy: 'expand',
          rewrite_guidance: '尽量强化动作细节，不改动剧情事实。',
        })
      )
    })
  })

  it('parses labeled trigger conditions before saving scene rule', async () => {
    const user = userEvent.setup()
    configApiMock.getSnapshot.mockResolvedValue(rulesSnapshot)

    renderWithProviders(<Config />)

    await screen.findByText('战斗 · 触发条件：厮杀、交锋 · weight=1.2')

    const triggerTextarea = screen.getByRole('textbox', { name: '触发条件' })
    await user.clear(triggerTextarea)
    await user.type(triggerTextarea, '识别点：厮杀、交锋。关键词：刀光、对砍。')

    const saveButtons = screen.getAllByRole('button', { name: '保存' })
    await user.click(saveButtons[1])

    await waitFor(() => {
      expect(configApiMock.updateSceneRule).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'scene-1',
          scene_type: '战斗',
          trigger_conditions: ['厮杀', '交锋', '刀光', '对砍'],
        })
      )
    })
  })

  it('keeps other unsaved rules when saving one newly added rule', async () => {
    const user = userEvent.setup()
    configApiMock.getSnapshot.mockResolvedValue(baseSnapshot)
    configApiMock.createSceneRule.mockResolvedValue(sceneSavedSnapshot)

    renderWithProviders(<Config />)

    await screen.findByText('配置中心')

    const addButtons = screen.getAllByRole('button', { name: '新增规则' })
    await user.click(addButtons[0])
    await user.click(addButtons[0])

    const sceneTypeInputs = screen.getAllByPlaceholderText('例如：战斗')
    await user.type(sceneTypeInputs[0], '战斗')
    await user.type(sceneTypeInputs[1], '对话')

    let saveButtons = screen.getAllByRole('button', { name: '保存' })
    await user.click(saveButtons[1])

    await waitFor(() => {
      expect(configApiMock.createSceneRule).toHaveBeenCalledWith(
        expect.objectContaining({
          scene_type: '战斗',
        })
      )
    })

    saveButtons = screen.getAllByRole('button', { name: '保存' })
    expect(saveButtons.length).toBeGreaterThanOrEqual(3)
    const updatedInputs = screen.getAllByPlaceholderText('例如：战斗') as HTMLInputElement[]
    expect(updatedInputs.some((input) => input.value === '对话')).toBe(true)
  })
})
