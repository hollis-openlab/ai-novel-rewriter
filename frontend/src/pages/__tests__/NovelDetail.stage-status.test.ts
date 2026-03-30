import { describe, expect, it } from 'vitest'
import { mergeAnalyzeAndMarkStatus, stageStatusForChapter } from '@/pages/NovelDetail'
import type { ChapterListItem, StageStatus } from '@/types'

function chapterWithRewriteStatus(status: StageStatus): ChapterListItem {
  return {
    id: 'chapter-2',
    index: 2,
    title: '第二章',
    status,
    stages: {
      rewrite: status,
    },
  }
}

describe('stageStatusForChapter', () => {
  it('returns persisted completed status', () => {
    const chapter = chapterWithRewriteStatus('completed')
    const resolved = stageStatusForChapter(chapter, 'rewrite')
    expect(resolved).toBe('completed')
  })

  it('returns persisted pending status', () => {
    const chapter = chapterWithRewriteStatus('pending')
    const resolved = stageStatusForChapter(chapter, 'rewrite')
    expect(resolved).toBe('pending')
  })

  it('uses runtime override when chapter action is in flight', () => {
    const chapter = chapterWithRewriteStatus('completed')
    const resolved = stageStatusForChapter(chapter, 'rewrite', {
      rewrite: {
        2: 'running',
      },
    })
    expect(resolved).toBe('running')
  })
})

describe('mergeAnalyzeAndMarkStatus', () => {
  function stage(status: StageStatus, extra: Partial<{
    chapters_total: number
    chapters_done: number
    error_message: string
  }> = {}) {
    return {
      status,
      chapters_total: extra.chapters_total ?? 10,
      chapters_done: extra.chapters_done ?? 0,
      error_message: extra.error_message,
    }
  }

  it('returns completed when mark is completed even if analyze is failed', () => {
    const merged = mergeAnalyzeAndMarkStatus(
      stage('failed', { chapters_done: 10, error_message: 'Auto mark stage execution failed' }),
      stage('completed', { chapters_done: 10 })
    )
    expect(merged.status).toBe('completed')
    expect(merged.error_message).toBeUndefined()
  })

  it('keeps pending when analyze is completed but mark is pending', () => {
    const merged = mergeAnalyzeAndMarkStatus(
      stage('completed', { chapters_done: 10 }),
      stage('pending', { chapters_done: 0 })
    )
    expect(merged.status).toBe('pending')
  })

  it('surfaces failed when mark fails', () => {
    const merged = mergeAnalyzeAndMarkStatus(
      stage('completed', { chapters_done: 10 }),
      stage('failed', { chapters_done: 3, error_message: 'mark failed' })
    )
    expect(merged.status).toBe('failed')
    expect(merged.error_message).toBe('mark failed')
  })
})
