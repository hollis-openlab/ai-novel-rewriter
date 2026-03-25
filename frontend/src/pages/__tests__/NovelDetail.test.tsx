import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NovelDetail } from '@/pages/NovelDetail'
import { splitRulesApi } from '@/lib/split-rules'
import { renderRouteWithProviders } from '@/test/utils'

const getNovelMock = vi.fn()
const getNovelChaptersMock = vi.fn()
const getChapterMock = vi.fn()
const getChapterAnalysisMock = vi.fn()
const getRewritesMock = vi.fn()
const reviewRewriteMock = vi.fn()
const retryChapterMock = vi.fn()
const exportFinalMock = vi.fn()
const stageRunMock = vi.fn()
const stagePauseMock = vi.fn()
const stageResumeMock = vi.fn()
const stageRetryMock = vi.fn()
const stageExportArtifactMock = vi.fn()
const providersListMock = vi.fn()
const onMessageMock = vi.fn()

const fetchStageRunDetailMock = vi.fn()
const fetchStageArtifactMock = vi.fn()

vi.mock('@/lib/ws', () => ({
  wsManager: {
    connect: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onMessage: (...args: unknown[]) => onMessageMock(...args),
  },
}))

vi.mock('@/lib/stage-insights', () => ({
  fetchQualityReport: vi.fn().mockResolvedValue(null),
  fetchStageArtifact: (...args: unknown[]) => fetchStageArtifactMock(...args),
  fetchStageRunDetail: (...args: unknown[]) => fetchStageRunDetailMock(...args),
  normalizeQualityReport: vi.fn((payload: unknown) => payload),
  summarizeRewriteCoverage: vi.fn(() => ({
    rewrittenSegments: 0,
    preservedSegments: 0,
    failedSegments: 0,
    rollbackSegments: 0,
    chapters: [],
  })),
}))

vi.mock('@/lib/split-rules', () => ({
  splitRulesApi: {
    get: vi.fn().mockResolvedValue({
      rules_version: 'rules-v1',
      builtin_rules: [],
      custom_rules: [],
    }),
    replace: vi.fn().mockResolvedValue({
      rules_version: 'rules-v1',
      builtin_rules: [],
      custom_rules: [],
    }),
    createCustom: vi.fn(),
    updateCustom: vi.fn(),
    deleteCustom: vi.fn(),
    runPreview: vi.fn().mockResolvedValue({
      preview_token: 'preview-token',
      novel_id: 'novel-1',
      task_id: 'task-1',
      stage: 'split',
      status: 'paused',
      run_id: 'run-1',
      run_seq: 1,
      source_revision: 'source-rev-1',
      rules_version: 'rules-v1',
      boundary_hash: 'boundary-hash',
      estimated_chapters: 3,
      chapters: [],
      created_at: new Date().toISOString(),
    }),
    confirm: vi.fn(),
    preview: vi.fn().mockResolvedValue({
      preview_token: 'preview-token',
      novel_id: 'novel-1',
      task_id: 'task-1',
      stage: 'split',
      status: 'paused',
      run_id: 'run-1',
      run_seq: 1,
      source_revision: 'source-rev-1',
      rules_version: 'rules-v1',
      boundary_hash: 'boundary-hash',
      estimated_chapters: 3,
      matched_count: 0,
      matched_lines: [],
      preview_valid: true,
      chapters: [],
      created_at: new Date().toISOString(),
    }),
  },
}))

vi.mock('@/lib/api', () => {
  class MockApiError extends Error {
    status: number
    code?: string

    constructor(message: string, status = 500, code?: string) {
      super(message)
      this.status = status
      this.code = code
    }
  }

  return {
    ApiError: MockApiError,
    getNovel: (...args: unknown[]) => getNovelMock(...args),
    getNovelChapters: (...args: unknown[]) => getNovelChaptersMock(...args),
    chapters: {
      get: (...args: unknown[]) => getChapterMock(...args),
      getAnalysis: (...args: unknown[]) => getChapterAnalysisMock(...args),
      getRewrites: (...args: unknown[]) => getRewritesMock(...args),
      reviewRewrite: (...args: unknown[]) => reviewRewriteMock(...args),
      retryChapter: (...args: unknown[]) => retryChapterMock(...args),
    },
    novels: {
      exportFinal: (...args: unknown[]) => exportFinalMock(...args),
    },
    stages: {
      run: (...args: unknown[]) => stageRunMock(...args),
      pause: (...args: unknown[]) => stagePauseMock(...args),
      resume: (...args: unknown[]) => stageResumeMock(...args),
      retry: (...args: unknown[]) => stageRetryMock(...args),
      exportArtifact: (...args: unknown[]) => stageExportArtifactMock(...args),
    },
    providers: {
      list: (...args: unknown[]) => providersListMock(...args),
    },
  }
})

function buildNovel() {
  return {
    id: 'novel-1',
    title: '测试小说',
    original_filename: 'demo.txt',
    file_format: 'txt',
    file_size: 1024,
    total_chars: 120000,
    imported_at: '2026-03-20T10:00:00.000Z',
    chapter_count: 3,
    task_id: 'task-1',
    active_task_id: 'task-1',
    pipeline_status: {
      import: { status: 'completed', run_seq: 1, chapters_total: 3, chapters_done: 3 },
      split: { status: 'completed', run_seq: 2, chapters_total: 3, chapters_done: 3 },
      analyze: { status: 'completed', run_seq: 3, chapters_total: 3, chapters_done: 3 },
      mark: { status: 'completed', run_seq: 4, chapters_total: 3, chapters_done: 3 },
      rewrite: { status: 'failed', run_seq: 5, chapters_total: 3, chapters_done: 2, warnings_count: 1 },
      assemble: { status: 'pending', run_seq: 0, chapters_total: 0, chapters_done: 0 },
    },
  }
}

function buildChapters() {
  return [
    {
      id: 'chapter-1',
      index: 1,
      title: '第一章 序幕',
      word_count: 8000,
      status: 'failed',
      stages: {
        import: 'completed',
        split: 'completed',
        analyze: 'completed',
        mark: 'completed',
        rewrite: 'failed',
        assemble: 'pending',
      },
    },
    {
      id: 'chapter-2',
      index: 2,
      title: '第二章 风起',
      word_count: 9200,
      status: 'running',
      stages: {
        import: 'completed',
        split: 'completed',
        analyze: 'completed',
        mark: 'completed',
        rewrite: 'running',
        assemble: 'pending',
      },
    },
    {
      id: 'chapter-3',
      index: 3,
      title: '第三章 追击',
      word_count: 10800,
      status: 'completed',
      stages: {
        import: 'completed',
        split: 'completed',
        analyze: 'completed',
        mark: 'completed',
        rewrite: 'completed',
        assemble: 'completed',
      },
    },
  ]
}

function chapterDetailByIndex(index: number) {
  const contentMap: Record<number, string> = {
    1: '夜色未褪，城门将开。\n\n风卷尘起，马蹄声近。\n\n他握紧刀柄，呼吸渐稳。',
    2: '巷道狭长，脚步回响。\n\n追兵尚远，火光却近。\n\n她回头看了一眼。',
    3: '雨势骤急，青石路滑。\n\n旧事翻涌，心绪难平。\n\n天边终于亮起。',
  }
  return {
    id: `chapter-${index}`,
    novel_id: 'novel-1',
    index,
    title: buildChapters()[index - 1].title,
    content: contentMap[index],
    word_count: 1000,
    status: 'pending',
  }
}

function chapterAnalysisByIndex(index: number) {
  return {
    summary: `第${index}章摘要`,
    characters: [{ name: '主角', emotion: '紧张', state: '警惕', role_in_chapter: '推动剧情' }],
    key_events: [{ description: '冲突升级', event_type: '冲突', importance: 4, paragraph_range: [1, 2] as [number, number] }],
    scenes: [],
    location: '城门',
    tone: '紧张',
  }
}

function chapterRewritesByIndex(index: number) {
  if (index === 1) {
    return [
      {
        segment_id: 'seg-1',
        chapter_index: 1,
        paragraph_range: [1, 2] as [number, number],
        anchor_verified: false,
        strategy: 'rewrite',
        original_text: '夜色未褪，城门将开。\n\n风卷尘起，马蹄声近。',
        rewritten_text: '',
        original_chars: 24,
        rewritten_chars: 0,
        status: 'failed',
        attempts: 1,
        provider_used: 'openai-compatible',
        error_code: 'REWRITE_LENGTH_OUT_OF_RANGE',
        error_detail: JSON.stringify({
          target_ratio: 1.2,
          target_chars_min: 85,
          target_chars_max: 109,
          actual_chars: 24,
          provider_detail: {
            message: 'length exceeded the accepted range',
            request_id: 'req-1',
          },
        }),
        manual_edited_text: null,
        scene_type: '战斗',
        target_ratio: 1.2,
        target_chars_min: 85,
        target_chars_max: 109,
        suggestion: '减少冗余描写，保留动作推进。',
      },
    ]
  }
  if (index === 2) {
    return [
      {
        segment_id: 'seg-2',
        chapter_index: 2,
        paragraph_range: [1, 2] as [number, number],
        anchor_verified: true,
        strategy: 'expand',
        original_text: '巷道狭长，脚步回响。\n\n追兵尚远，火光却近。',
        rewritten_text: '巷道狭长，脚步回响。\n\n追兵尚远，火光却近，火光像潮水一样压过来。',
        original_chars: 24,
        rewritten_chars: 34,
        status: 'accepted',
        attempts: 1,
        provider_used: 'openai-compatible',
        error_code: null,
        error_detail: null,
        manual_edited_text: null,
        scene_type: '追逐',
        target_ratio: 1.5,
        target_chars_min: 30,
        target_chars_max: 45,
        suggestion: '扩写追逐过程中的环境与动作张力。',
      },
    ]
  }
  if (index === 3) {
    return []
  }
  return []
}

async function renderNovelDetail() {
  renderRouteWithProviders(<NovelDetail />, { route: '/novels/novel-1', path: '/novels/:id' })
  await screen.findByRole('heading', { name: /《测试小说》/ })
}

describe('NovelDetail workbench', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    onMessageMock.mockReturnValue(() => {})

    vi.mocked(splitRulesApi.get).mockResolvedValue({
      rules_version: 'rules-v1',
      builtin_rules: [],
      custom_rules: [],
    })
    vi.mocked(splitRulesApi.replace).mockResolvedValue({
      rules_version: 'rules-v1',
      builtin_rules: [],
      custom_rules: [],
    })
    vi.mocked(splitRulesApi.preview).mockResolvedValue({
      preview_token: 'preview-token',
      novel_id: 'novel-1',
      source_revision: 'source-rev-1',
      rules_version: 'rules-v1',
      boundary_hash: 'boundary-hash',
      estimated_chapters: 3,
      matched_count: 0,
      matched_lines: [],
      preview_valid: true,
      chapters: [],
      sample_size: 10,
    })
    vi.mocked(splitRulesApi.runPreview).mockResolvedValue({
      preview_token: 'preview-token',
      novel_id: 'novel-1',
      task_id: 'task-1',
      stage: 'split',
      status: 'paused',
      run_id: 'run-1',
      run_seq: 1,
      source_revision: 'source-rev-1',
      rules_version: 'rules-v1',
      boundary_hash: 'boundary-hash',
      estimated_chapters: 3,
      chapters: [],
      created_at: new Date().toISOString(),
    })
    vi.mocked(splitRulesApi.confirm).mockResolvedValue({
      preview_token: 'preview-token',
      novel_id: 'novel-1',
      source_revision: 'source-rev-1',
      rules_version: 'rules-v1',
      boundary_hash: 'boundary-hash',
      preview_valid: true,
      chapter_count: 3,
      chapters: [],
    })

    getNovelMock.mockResolvedValue(buildNovel())
    getNovelChaptersMock.mockResolvedValue(buildChapters())
    getChapterMock.mockImplementation(async (_novelId: string, chapterIndex: number) => chapterDetailByIndex(chapterIndex))
    getChapterAnalysisMock.mockImplementation(async (_novelId: string, chapterIndex: number) => chapterAnalysisByIndex(chapterIndex))
    getRewritesMock.mockImplementation(async (_novelId: string, chapterIndex: number) => chapterRewritesByIndex(chapterIndex))
    reviewRewriteMock.mockResolvedValue({ status: 'updated' })
    retryChapterMock.mockResolvedValue({
      status: 'completed',
      segments_total: 1,
      failed_segments: 0,
    })

    exportFinalMock.mockResolvedValue({
      blob: new Blob(['ok']),
      filename: 'novel-export.txt',
      risk_signature: null,
      content_type: 'text/plain',
    })
    stageRunMock.mockResolvedValue(undefined)
    stagePauseMock.mockResolvedValue(undefined)
    stageResumeMock.mockResolvedValue(undefined)
    stageRetryMock.mockResolvedValue(undefined)
    stageExportArtifactMock.mockResolvedValue({
      blob: new Blob(['{}']),
      filename: 'rewrite.json',
      risk_signature: null,
      content_type: 'application/json',
    })
    providersListMock.mockResolvedValue([
      {
        id: 'provider-1',
        name: '默认 Provider',
        provider_type: 'openai_compatible',
        api_key_masked: 'sk-***',
        base_url: 'https://api.example.com/v1',
        model_name: 'gpt-4o-mini',
        temperature: 0.7,
        max_tokens: 4000,
        top_p: 1,
        presence_penalty: 0,
        frequency_penalty: 0,
        rpm_limit: 60,
        tpm_limit: 100000,
        is_active: true,
        created_at: '2026-03-20T10:00:00.000Z',
      },
    ])

    fetchStageRunDetailMock.mockResolvedValue({
      run: {
        run_seq: 5,
        status: 'failed',
        warnings_count: 1,
        chapters_total: 3,
        chapters_done: 2,
        config_snapshot: {
          provider_name: 'SiliconFlow',
          model_name: 'Pro/zai-org/GLM-4.7',
          global_prompt_version: 'prompt-v1',
          rewrite_rules_hash: 'rules-hash-v1',
        },
      },
    })
    fetchStageArtifactMock.mockResolvedValue({
      artifact: {
        status: 'failed',
        error_code: 'REWRITE_LENGTH_OUT_OF_RANGE',
        error_detail: {
          target_ratio: 1.2,
          target_chars_min: 85,
          target_chars_max: 109,
          actual_chars: 24,
          provider_detail: {
            message: 'length exceeded the accepted range',
            request_id: 'req-1',
            payload: { model: 'Pro/zai-org/GLM-4.7' },
          },
        },
      },
      latest_artifact: null,
      run: { run_seq: 5 },
    })
  })

  it('renders three-pane workbench skeleton', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    expect(screen.getByText('章节导航')).toBeInTheDocument()
    expect(screen.getByText('洞察')).toBeInTheDocument()
    expect(screen.getByText('操作')).toBeInTheDocument()
    expect(screen.getByText('日志')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    expect(screen.getByRole('button', { name: '改写稿预览' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Diff' })).toBeInTheDocument()
  })

  it('switches right sidebar tabs', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))

    expect(screen.queryByRole('button', { name: '洞察' })).not.toBeInTheDocument()
    expect(await screen.findByText('章节洞察')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '操作' }))
    expect(await screen.findByText('全局阶段操作')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '日志' }))
    expect(await screen.findByText('运行历史')).toBeInTheDocument()
  })

  it('switches center view modes including flat diff', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))

    await user.click(screen.getByRole('button', { name: 'Diff' }))
    expect(await screen.findByText('Diff 对比（平铺高亮）')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '改写稿预览' }))
    expect(await screen.findByRole('heading', { name: '改写稿预览' })).toBeInTheDocument()
  })

  it('switches diff text mode between sentence, raw and canonical', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))

    await user.click(screen.getByRole('button', { name: 'Diff' }))
    expect(await screen.findByText('Diff 对比（平铺高亮）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '句子对齐' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '原文对齐' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '规范化对齐' })).toBeInTheDocument()
    expect(screen.queryByText(/句子对齐规则：先规范化空白/)).not.toBeInTheDocument()
    expect(screen.queryByText(/规范化规则：统一换行/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '句子对齐' }))
    expect(screen.getByText(/句子对齐规则：先规范化空白/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '原文对齐' }))
    expect(screen.queryByText(/句子对齐规则：先规范化空白/)).not.toBeInTheDocument()
    expect(screen.queryByText(/规范化规则：统一换行/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '规范化对齐' }))
    expect(screen.getByText(/规范化规则：统一换行/)).toBeInTheDocument()
  })

  it('supports chapter search and keyboard navigation', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))

    const searchInput = screen.getByPlaceholderText('搜索章节标题 / 编号')
    await user.type(searchInput, '第三章')
    expect(screen.getAllByText('第三章 追击').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('第一章 序幕')).toHaveLength(0)

    await user.clear(searchInput)
    await waitFor(() => {
      expect(screen.getAllByText('第一章 序幕').length).toBeGreaterThan(0)
      expect(screen.getAllByText('第二章 风起').length).toBeGreaterThan(0)
    })

    fireEvent.keyDown(document.body, { key: 'ArrowUp' })
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    expect(chapterTwoNav.className).toContain('border-accent')
  })

  it('renders accepted rewrite content separately from the original text', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    expect(await screen.findByRole('heading', { name: '改写稿预览' })).toBeInTheDocument()
    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    expect(rewriteTextarea.value).toContain('巷道狭长，脚步回响。')
    expect(rewriteTextarea.value).toContain('火光像潮水一样压过来。')
    expect(rewriteTextarea.value).not.toContain('夜色未褪，城门将开。')
    expect(screen.getAllByText((_, element) => Boolean(element?.textContent?.includes('成功 1'))).length).toBeGreaterThan(0)
  })

  it('falls back to original heading when heading segment is abnormally expanded', async () => {
    const user = userEvent.setup()
    getChapterMock.mockImplementation(async (_novelId: string, chapterIndex: number) => {
      if (chapterIndex !== 2) return chapterDetailByIndex(chapterIndex)
      return {
        id: 'chapter-2',
        novel_id: 'novel-1',
        index: 2,
        title: '第二章 风起',
        content: '第一章\n\n正文第一段原文。',
        word_count: 1000,
        status: 'pending',
      }
    })
    getRewritesMock.mockImplementation(async (_novelId: string, chapterIndex: number) => {
      if (chapterIndex !== 2) return chapterRewritesByIndex(chapterIndex)
      return [
        {
          segment_id: 'seg-heading',
          chapter_index: 2,
          paragraph_range: [1, 1] as [number, number],
          anchor_verified: true,
          strategy: 'expand',
          original_text: '第一章',
          rewritten_text: '第一章\n\n这里是异常扩写的正文，不应该覆盖标题段。',
          original_chars: 3,
          rewritten_chars: 24,
          status: 'completed',
          attempts: 1,
          provider_used: 'openai-compatible',
          error_code: null,
          error_detail: null,
          manual_edited_text: null,
          scene_type: '环境',
          target_ratio: 1.2,
          target_chars_min: 4,
          target_chars_max: 8,
          suggestion: '错误命中标题',
        },
        {
          segment_id: 'seg-body',
          chapter_index: 2,
          paragraph_range: [2, 2] as [number, number],
          anchor_verified: true,
          strategy: 'expand',
          original_text: '正文第一段原文。',
          rewritten_text: '正文第一段改写。',
          original_chars: 8,
          rewritten_chars: 8,
          status: 'completed',
          attempts: 1,
          provider_used: 'openai-compatible',
          error_code: null,
          error_detail: null,
          manual_edited_text: null,
          scene_type: '环境',
          target_ratio: 1.2,
          target_chars_min: 8,
          target_chars_max: 12,
          suggestion: '正文命中',
        },
      ]
    })

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    expect(rewriteTextarea.value).toContain('第一章')
    expect(rewriteTextarea.value).toContain('正文第一段改写。')
    expect(rewriteTextarea.value).not.toContain('异常扩写的正文')
  })

  it('falls back to chapter original text in rewrite preview when there is no rewrite output', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterThreeNav = await screen.findByRole('button', { name: /第 3 章 第三章 追击/ })
    await user.click(chapterThreeNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    expect(await screen.findByRole('heading', { name: '改写稿预览' })).toBeInTheDocument()
    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    expect(rewriteTextarea.value).toContain('雨势骤急，青石路滑。')
    expect(rewriteTextarea.value).toContain('旧事翻涌，心绪难平。')
    expect(screen.getByText('当前章节尚未生成改写计划，预览暂显示原文。')).toBeInTheDocument()
  })

  it('surfaces rewrite-length diagnostics and full error JSON in logs', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterOneNav = await screen.findByRole('button', { name: /第 1 章 第一章 序幕/ })
    await user.click(chapterOneNav)

    await user.click(screen.getByRole('button', { name: '日志' }))

    await screen.findByText('当前章节改写失败明细（全文）')
    const allErrorBlocks = screen.getAllByText((_, element) => element?.tagName === 'PRE' && Boolean(element.textContent?.includes('"error_code": "REWRITE_LENGTH_OUT_OF_RANGE"')))
    expect(allErrorBlocks.length).toBeGreaterThan(0)
    const detailBlock = allErrorBlocks.find((item) => item.textContent?.includes('"segment_id": "seg-1"')) ?? allErrorBlocks[0]
    expect(detailBlock).toHaveTextContent('"actual_chars": 24')
    expect(detailBlock).toHaveTextContent('"target_chars_min": 85')
    expect(detailBlock).toHaveTextContent('"target_chars_max": 109')
    expect(detailBlock).toHaveTextContent('"request_id": "req-1"')
  })

  it('keeps the selected chapter progress counters in sync with stage state', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)

    await user.click(screen.getByRole('button', { name: '洞察' }))
    expect(screen.getByText('Stage 进度')).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByText(/改写 · 运行中|改写 · 已完成|改写 · 已暂停|改写 · 失败/)).toBeInTheDocument()
    expect(chapterTwoNav).toHaveTextContent(/运行中|已完成|已暂停|失败/)
  })

  it('does not infer chapter statuses from stage_progress websocket events', async () => {
    const user = userEvent.setup()
    let wsHandler: ((message: Record<string, unknown>) => void) | null = null
    onMessageMock.mockImplementation((handler: (message: Record<string, unknown>) => void) => {
      wsHandler = handler
      return () => {}
    })

    const novel = buildNovel()
    const pendingAnalyzeNovel = {
      ...novel,
      pipeline_status: {
        ...novel.pipeline_status,
        analyze: { status: 'pending', run_seq: 0, chapters_total: 3, chapters_done: 0 },
        mark: { status: 'pending', run_seq: 0, chapters_total: 3, chapters_done: 0 },
      },
    }
    getNovelMock.mockResolvedValue(pendingAnalyzeNovel)

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))

    expect(wsHandler).not.toBeNull()
    act(() => {
      wsHandler?.({
        type: 'stage_progress',
        novel_id: 'novel-1',
        stage: 'analyze',
        chapters_done: 1,
        chapters_total: 3,
        percentage: 33.3,
      })
    })

    const chapterOneNav = await screen.findByRole('button', { name: /第 1 章 第一章 序幕/ })
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })

    await waitFor(() => {
      expect(chapterOneNav).toHaveTextContent('已完成')
      expect(chapterTwoNav).toHaveTextContent('已完成')
    })
  })

  it('keeps persisted chapter status when analyze stage is paused and receives progress events', async () => {
    const user = userEvent.setup()
    let wsHandler: ((message: Record<string, unknown>) => void) | null = null
    onMessageMock.mockImplementation((handler: (message: Record<string, unknown>) => void) => {
      wsHandler = handler
      return () => {}
    })

    const novel = buildNovel()
    const pausedAnalyzeNovel = {
      ...novel,
      pipeline_status: {
        ...novel.pipeline_status,
        analyze: { status: 'paused', run_seq: 3, chapters_total: 3, chapters_done: 1 },
        mark: { status: 'paused', run_seq: 4, chapters_total: 3, chapters_done: 1 },
      },
    }
    getNovelMock.mockResolvedValue(pausedAnalyzeNovel)

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))

    expect(wsHandler).not.toBeNull()
    act(() => {
      wsHandler?.({
        type: 'stage_progress',
        novel_id: 'novel-1',
        stage: 'analyze',
        chapters_done: 1,
        chapters_total: 3,
        percentage: 33.3,
      })
    })

    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await waitFor(() => {
      expect(chapterTwoNav).toHaveTextContent('已完成')
      expect(chapterTwoNav).not.toHaveTextContent('运行中')
    })
  })

  it('keeps selected chapter status aligned with persisted backend value while chapter retry request is in flight', async () => {
    const user = userEvent.setup()
    let resolveRetry: ((value: { status: string; segments_total?: number; failed_segments?: number }) => void) | null = null
    retryChapterMock.mockImplementation(() => new Promise((resolve) => {
      resolveRetry = resolve as typeof resolveRetry
    }))

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))

    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '操作' }))
    await user.click(screen.getByRole('button', { name: '重跑当前章节' }))

    await waitFor(() => {
      expect(chapterTwoNav).toHaveTextContent('已完成')
      expect(chapterTwoNav).not.toHaveTextContent('运行中')
    })

    act(() => {
      resolveRetry?.({ status: 'completed', segments_total: 0, failed_segments: 0 })
    })
  })

  it('shows chapter list running flow while global analyze retry is in flight', async () => {
    const user = userEvent.setup()
    let resolveRun: (() => void) | null = null
    stageRunMock.mockImplementation(() => new Promise<void>((resolve) => {
      resolveRun = resolve
    }))

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: '分析与标记' }))
    await user.click(screen.getByRole('button', { name: '操作' }))
    await user.click(screen.getByRole('button', { name: '重新执行' }))

    const chapterOneNav = await screen.findByRole('button', { name: /第 1 章 第一章 序幕/ })
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })

    await waitFor(() => {
      expect(chapterOneNav).toHaveTextContent('已完成')
      expect(chapterTwoNav).toHaveTextContent('已完成')
    })

    act(() => {
      resolveRun?.()
    })
  })

  it('supports chapter-level rewrite draft editing and diff preview', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    await user.type(rewriteTextarea, '\n\n新增句子测试')
    expect(rewriteTextarea.value).toContain('新增句子测试')

    await user.click(screen.getByRole('button', { name: 'Diff' }))
    const diffHeading = await screen.findByText('Diff 对比（平铺高亮）')
    const diffSectionText = diffHeading.closest('section')?.textContent ?? ''
    expect(diffSectionText).toContain('新增')
    expect(diffSectionText).not.toMatch(/新增\s*\+0/)
  })

  it('renders rewrite window explainability with warnings and attempts', async () => {
    const user = userEvent.setup()
    getRewritesMock.mockImplementation(async (_novelId: string, chapterIndex: number) => {
      if (chapterIndex !== 2) return chapterRewritesByIndex(chapterIndex)
      return [
        {
          segment_id: 'seg-2-windowed',
          chapter_index: 2,
          paragraph_range: [1, 2] as [number, number],
          sentence_range: [1, 3] as [number, number],
          char_offset_range: [0, 24] as [number, number],
          anchor_verified: true,
          strategy: 'expand',
          original_text: '巷道狭长，脚步回响。\n\n追兵尚远，火光却近。',
          rewritten_text: '巷道狭长，脚步回响，风声压得人透不过气。\n\n追兵尚远，火光却近，像潮水一样涌来。',
          original_chars: 24,
          rewritten_chars: 39,
          status: 'completed',
          attempts: 1,
          provider_used: 'openai-compatible',
          error_code: null,
          error_detail: null,
          manual_edited_text: null,
          scene_type: '追逐',
          target_ratio: 1.5,
          target_chars_min: 30,
          target_chars_max: 45,
          suggestion: '扩写追逐过程中的环境与动作张力。',
          completion_kind: 'normal',
          reason_code: null,
          warning_count: 1,
          warning_codes: ['REWRITE_END_FRAGMENT_BROKEN'],
          rewrite_windows: [
            {
              window_id: 'win-2-1',
              segment_id: 'seg-2-windowed',
              chapter_index: 2,
              start_offset: 0,
              end_offset: 24,
              hit_sentence_range: [1, 2] as [number, number],
              context_sentence_range: [1, 3] as [number, number],
              target_chars: 36,
              target_chars_min: 30,
              target_chars_max: 45,
            },
          ],
          window_attempts: [
            { window_id: 'win-2-1', attempt_seq: 1, action: 'retry' },
            { window_id: 'win-2-1', attempt_seq: 2, action: 'accepted' },
          ],
        },
      ]
    })

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)

    expect(await screen.findByText('窗口解释（命中 / 替换 / 保留）')).toBeInTheDocument()
    expect(screen.getByText('win-2-1')).toBeInTheDocument()
    expect(screen.getByText('REWRITE_END_FRAGMENT_BROKEN')).toBeInTheDocument()
    expect(screen.getByText(/attempts 2/)).toBeInTheDocument()
    expect(screen.getByText(/替换范围 \[0, 24\)/)).toBeInTheDocument()
  })

  it('shows local draft save status after chapter rewrite edits', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    await user.type(rewriteTextarea, '\n\n本地草稿新增')

    expect(await screen.findByText('本地微调草稿未保存。')).toBeInTheDocument()
    const saveButton = screen.getByRole('button', { name: '保存草稿' })
    expect(saveButton).not.toBeDisabled()
    await user.click(saveButton)

    expect(await screen.findByText(/本地微调草稿已保存/)).toBeInTheDocument()
    const raw = window.localStorage.getItem('ai-novel:rewrite-chapter-drafts:novel-1')
    expect(raw).not.toBeNull()
    const stored = JSON.parse(String(raw)) as Record<string, { text: string; saved_at: string }>
    expect(stored['2'].text).toContain('本地草稿新增')
  })

  it('uses page-level chapter added chars setting when retrying rewrite', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    await user.click(screen.getByRole('button', { name: '操作' }))

    const targetInput = await screen.findByLabelText('新增字数目标') as HTMLInputElement
    await user.clear(targetInput)
    await user.type(targetInput, '180')

    await screen.findByText('全局阶段操作')
    const retryButton = await screen.findByRole('button', { name: '重试' })
    await user.click(retryButton)

    await waitFor(() => {
      expect(stageRetryMock).toHaveBeenCalledWith('novel-1', 'rewrite', {
        rewrite_target_added_chars: 180,
        provider_id: 'provider-1',
      })
    })
  })

  it('requires explicit provider selection when multiple providers are configured for rewrite', async () => {
    const user = userEvent.setup()
    providersListMock.mockResolvedValue([
      {
        id: 'provider-1',
        name: 'Provider A',
        provider_type: 'openai_compatible',
        api_key_masked: 'sk-***',
        base_url: 'https://api.a.com/v1',
        model_name: 'model-a',
        temperature: 0.7,
        max_tokens: 4000,
        top_p: 1,
        presence_penalty: 0,
        frequency_penalty: 0,
        rpm_limit: 60,
        tpm_limit: 100000,
        is_active: true,
        created_at: '2026-03-20T10:00:00.000Z',
      },
      {
        id: 'provider-2',
        name: 'Provider B',
        provider_type: 'openai_compatible',
        api_key_masked: 'sk-***',
        base_url: 'https://api.b.com/v1',
        model_name: 'model-b',
        temperature: 0.7,
        max_tokens: 4000,
        top_p: 1,
        presence_penalty: 0,
        frequency_penalty: 0,
        rpm_limit: 60,
        tpm_limit: 100000,
        is_active: true,
        created_at: '2026-03-20T10:01:00.000Z',
      },
    ])

    await renderNovelDetail()
    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    await user.click(screen.getByRole('button', { name: '操作' }))

    const retryButton = await screen.findByRole('button', { name: '重试' })
    expect(retryButton).toBeDisabled()
    expect(screen.getByText('已配置多个 Provider，请先选择本次改写 Provider，全局改写动作已禁用。')).toBeInTheDocument()

    const providerSelect = await screen.findByLabelText('改写 Provider')
    fireEvent.change(providerSelect, { target: { value: 'provider-2' } })

    await waitFor(() => {
      expect(retryButton).not.toBeDisabled()
    })
    await user.click(retryButton)

    await waitFor(() => {
      expect(stageRetryMock).toHaveBeenCalledWith('novel-1', 'rewrite', { provider_id: 'provider-2' })
    })
  })

  it('uses resume when continuing a paused stage', async () => {
    const user = userEvent.setup()
    const novel = buildNovel()
    getNovelMock.mockResolvedValueOnce({
      ...novel,
      pipeline_status: {
        ...novel.pipeline_status,
        rewrite: { status: 'paused', run_seq: 5, chapters_total: 3, chapters_done: 1 },
      },
    })
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    await user.click(screen.getByRole('button', { name: '操作' }))

    const continueButton = await screen.findByRole('button', { name: '继续' })
    await user.click(continueButton)

    await waitFor(() => {
      expect(stageResumeMock).toHaveBeenCalledWith('novel-1', 'rewrite')
    })
    expect(stageRetryMock).not.toHaveBeenCalled()
  })

  it('disables chapter-level rewrite action when the chapter has no marked segments', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterThreeNav = await screen.findByRole('button', { name: /第 3 章 第三章 追击/ })
    await user.click(chapterThreeNav)
    await user.click(screen.getByRole('button', { name: '操作' }))

    const chapterActionButton = await screen.findByRole('button', { name: '重跑当前章节' })
    expect(chapterActionButton).toBeDisabled()
    expect(screen.getByText('当前章节未命中可改写段落，无需执行改写重跑。')).toBeInTheDocument()

    const fallbackButton = await screen.findByRole('button', { name: '回退本章到原文' })
    expect(fallbackButton).not.toBeDisabled()
    await user.click(fallbackButton)
    expect(await screen.findByText('当前章节未命中可改写段，已默认采用原文（组装阶段直接使用原文）。')).toBeInTheDocument()
    expect(reviewRewriteMock).not.toHaveBeenCalled()
  })

  it('clears local chapter draft after retrying rewrite so backend result becomes visible again', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '改写稿预览' }))

    const rewriteTextarea = screen.getByPlaceholderText('改写稿将在这里展示，你可以直接微调整章内容。') as HTMLTextAreaElement
    await user.clear(rewriteTextarea)
    await user.type(rewriteTextarea, '这是本地草稿覆盖')
    expect(rewriteTextarea.value).toContain('这是本地草稿覆盖')

    await user.click(screen.getByRole('button', { name: '操作' }))
    await user.click(screen.getByRole('button', { name: '重跑当前章节' }))

    await user.click(screen.getByRole('button', { name: '改写稿预览' }))
    await waitFor(() => {
      expect(rewriteTextarea.value).toContain('火光像潮水一样压过来。')
    })
    expect(rewriteTextarea.value).not.toContain('这是本地草稿覆盖')
  })

  it('can fallback current chapter to original by rejecting all rewrite segments', async () => {
    const user = userEvent.setup()
    await renderNovelDetail()

    await user.click(screen.getByRole('button', { name: /^改写\s*\d*$/ }))
    const chapterTwoNav = await screen.findByRole('button', { name: /第 2 章 第二章 风起/ })
    await user.click(chapterTwoNav)
    await user.click(screen.getByRole('button', { name: '操作' }))

    const fallbackButton = await screen.findByRole('button', { name: '回退本章到原文' })
    await user.click(fallbackButton)

    await waitFor(() => {
      expect(reviewRewriteMock).toHaveBeenCalledWith('novel-1', 2, 'seg-2', {
        action: 'reject',
        note: 'chapter_fallback_to_original',
      })
    })
    expect(await screen.findByText('已将本章 1 段标记为不接受，组装阶段将采用本章原文。')).toBeInTheDocument()
  })
})
