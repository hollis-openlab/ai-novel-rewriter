import { describe, expect, it } from 'vitest'
import { stageStatusForChapter } from '@/pages/NovelDetail'
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
