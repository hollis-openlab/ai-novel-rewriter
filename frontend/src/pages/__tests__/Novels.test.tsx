import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Novels } from '@/pages/Novels'
import { renderWithProviders } from '@/test/utils'
import type { Novel } from '@/types'

const novelFixture: Novel = {
  id: 'novel-1',
  title: '测试小说',
  original_filename: 'test.txt',
  file_format: 'txt',
  file_size: 1024,
  total_chars: 12800,
  imported_at: '2026-03-21T00:00:00.000Z',
  chapter_count: 12,
}

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T | PromiseLike<T>) => void
  reject: (reason?: unknown) => void
}

function createDeferred<T>(): Deferred<T> {
  let resolve!: Deferred<T>['resolve']
  let reject!: Deferred<T>['reject']
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const { getNovelsMock, deleteNovelMock } = vi.hoisted(() => ({
  getNovelsMock: vi.fn<() => Promise<Novel[]>>(),
  deleteNovelMock: vi.fn<(id: string) => Promise<void>>(),
}))

vi.mock('@/lib/api', () => ({
  getNovels: getNovelsMock,
  uploadFile: vi.fn(),
  novels: {
    delete: deleteNovelMock,
  },
}))

describe('Novels page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getNovelsMock.mockResolvedValue([novelFixture])
    deleteNovelMock.mockResolvedValue(undefined)
  })

  it('locks delete action to one request even when user double-clicks', async () => {
    const user = userEvent.setup()
    const deferred = createDeferred<void>()
    deleteNovelMock.mockReturnValue(deferred.promise)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderWithProviders(<Novels />)

    await screen.findByText('《测试小说》')
    const deleteButton = screen.getByRole('button', { name: '删除' })
    await user.dblClick(deleteButton)

    await waitFor(() => {
      expect(deleteNovelMock).toHaveBeenCalledTimes(1)
      expect(deleteNovelMock).toHaveBeenCalledWith('novel-1')
    })
    expect(window.confirm).toHaveBeenCalledTimes(1)

    deferred.resolve(undefined)

    await waitFor(() => {
      expect(screen.getByText('已删除《测试小说》')).toBeInTheDocument()
    })
  })
})
