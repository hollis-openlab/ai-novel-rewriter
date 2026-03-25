import { beforeEach, describe, expect, it, vi } from 'vitest'
import { config, getNovelChapters, novels, providers } from '@/lib/api'
import { splitRulesApi } from '@/lib/split-rules'

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: {
      'content-type': 'application/json',
    },
  })
}

describe('frontend API contract', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('calls provider fetch-models endpoint with POST', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ models: ['gpt-4o-mini'] })
    )

    await providers.fetchModels({
      provider_type: 'openai_compatible',
      api_key: 'sk-test',
      base_url: 'https://api.example.com/v1',
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/providers/fetch-models')
    expect(init?.method).toBe('POST')
  })

  it('calls quality-report endpoint with task_id query', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ threshold_comparisons: [] })
    )

    await novels.getQualityReport('novel-1', 'task-1')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/novels/novel-1/quality-report')
    expect(String(url)).toContain('task_id=task-1')
    expect(init?.method).toBe('GET')
  })

  it('treats 204 delete response as success even when body is empty', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, {
        status: 204,
        headers: {
          'content-type': 'application/json',
        },
      })
    )

    await expect(novels.delete('novel-1')).resolves.toBeUndefined()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/novels/novel-1')
    expect(init?.method).toBe('DELETE')
  })

  it('calls config global-prompt endpoint with PUT', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        version: '1',
        global_prompt: 'next',
        scene_rules: [],
        rewrite_rules: [],
      })
    )

    await config.updateGlobalPrompt('next')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/config/global-prompt')
    expect(init?.method).toBe('PUT')
  })

  it('serializes rewrite rule strategies in config create request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        version: '1',
        global_prompt: '',
        rewrite_general_guidance: '',
        scene_rules: [],
        rewrite_rules: [],
      })
    )

    await config.createRewriteRule({
      scene_type: '战斗',
      strategies: ['expand', 'rewrite'],
      strategy: 'expand',
      rewrite_guidance: '战斗场景强化动作细节。',
      target_ratio: 2.2,
      priority: 1,
      enabled: true,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0]
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      scene_type: '战斗',
      strategies: ['expand', 'rewrite'],
      strategy: 'expand',
      rewrite_guidance: '战斗场景强化动作细节。',
      target_ratio: 2.2,
    })
  })

  it('calls split-rules preview endpoint with POST', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        preview_token: 'token',
        novel_id: 'novel-1',
        source_revision: 'rev',
        rules_version: '1',
        preview_valid: true,
        matched_count: 3,
        estimated_chapters: 3,
        matched_lines: [],
        boundary_hash: 'hash',
        chapters: [],
      })
    )

    await splitRulesApi.preview({
      novel_id: 'novel-1',
      selected_rule_id: 'builtin-zh-number',
      builtin_rules: [
        {
          id: 'builtin-zh-number',
          name: '中文章节号',
          pattern: '^第.+章',
          priority: 10,
          enabled: true,
          builtin: true,
        },
      ],
      custom_rules: [
        {
          id: 'custom-1',
          name: '前言',
          pattern: '^前言$',
          priority: 5,
          enabled: true,
          builtin: false,
        },
      ],
      sample_size: 10,
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/split-rules/preview')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      novel_id: 'novel-1',
      selected_rule_id: 'builtin-zh-number',
      sample_size: 10,
      builtin_rules: [{ id: 'builtin-zh-number' }],
      custom_rules: [{ id: 'custom-1' }],
    })
  })

  it('normalizes chapter-list response object to array', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        novel_id: 'novel-1',
        task_id: 'task-1',
        total: 1,
        data: [
          {
            id: 'chapter-1',
            index: 1,
            title: '第一章',
            word_count: 1200,
            status: 'pending',
            stages: {},
          },
        ],
      })
    )

    const chapters = await getNovelChapters('novel-1')

    expect(Array.isArray(chapters)).toBe(true)
    expect(chapters).toHaveLength(1)
    const [url] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/v1/novels/novel-1/chapters')
  })
})
