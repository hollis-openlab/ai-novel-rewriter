import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Providers } from '@/pages/Providers'
import { renderWithProviders } from '@/test/utils'
import type { Provider } from '@/types'

const providerFixture: Provider = {
  id: 'provider-1',
  name: 'OpenAI Prod',
  provider_type: 'openai',
  api_key_masked: 'sk-***',
  base_url: 'https://api.openai.com/v1',
  model_name: 'gpt-4o-mini',
  temperature: 0.7,
  max_tokens: 4096,
  top_p: null,
  presence_penalty: null,
  frequency_penalty: null,
  rpm_limit: 60,
  tpm_limit: 100000,
  is_active: true,
  created_at: '2026-03-20T00:00:00.000Z',
}

const { providersApiMock } = vi.hoisted(() => ({
  providersApiMock: {
    list: vi.fn<() => Promise<Provider[]>>(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    test: vi.fn(),
    testConnection: vi.fn(),
    fetchModels: vi.fn(),
    listModels: vi.fn(),
    updateApiKey: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({
  providers: providersApiMock,
}))

describe('Providers page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    providersApiMock.list.mockResolvedValue([providerFixture])
    providersApiMock.create.mockResolvedValue(providerFixture)
    providersApiMock.update.mockResolvedValue(providerFixture)
    providersApiMock.delete.mockResolvedValue(undefined)
    providersApiMock.test.mockResolvedValue({ status: 'success', success: true, latency_ms: 120 })
  })

  it('allows switching provider type to OpenAI-compatible while editing', async () => {
    const user = userEvent.setup()

    renderWithProviders(<Providers />)

    await screen.findByText('LLM 提供商')
    await screen.findByText('OpenAI Prod')
    await user.click(screen.getByRole('button', { name: '编辑' }))
    await screen.findByText('编辑提供商')

    const openaiCompatibleButton = screen.getByRole('button', { name: /OpenAI 兼容/ })
    expect(openaiCompatibleButton).not.toBeDisabled()

    await user.click(openaiCompatibleButton)

    await waitFor(() => {
      expect(screen.getByLabelText('Base URL')).toHaveValue('https://api.siliconflow.cn/v1')
    })
    expect(screen.getByLabelText('模型名称')).toHaveValue('')
  })
})
