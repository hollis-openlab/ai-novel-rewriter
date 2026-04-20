import { describe, expect, it } from 'vitest'
import { deriveNovelStatus, normalizePipelineStageStatus } from '@/pages/Dashboard'
import type { Novel } from '@/types'

function baseNovel(): Novel {
  return {
    id: 'novel-1',
    title: '测试小说',
    original_filename: 'demo.txt',
    file_format: 'txt',
    file_size: 1024,
    total_chars: 1200,
    imported_at: '2026-03-30T12:00:00.000Z',
  }
}

describe('Dashboard status derivation', () => {
  it('treats stale stage with full progress as completed', () => {
    expect(normalizePipelineStageStatus({
      status: 'stale',
      chapters_total: 10,
      chapters_done: 10,
    })).toBe('completed')
  })

  it('marks novel completed when visible stages are completed even if mark is pending', () => {
    const novel: Novel = {
      ...baseNovel(),
      pipeline_status: {
        import: { status: 'completed', stage: 'import', run_seq: 1, id: '1', chapters_total: 1, chapters_done: 1 },
        split: { status: 'completed', stage: 'split', run_seq: 1, id: '2', chapters_total: 8, chapters_done: 8 },
        analyze: { status: 'completed', stage: 'analyze', run_seq: 1, id: '3', chapters_total: 8, chapters_done: 8 },
        mark: { status: 'pending', stage: 'mark', run_seq: 1, id: '4', chapters_total: 0, chapters_done: 0 },
        rewrite: { status: 'completed', stage: 'rewrite', run_seq: 1, id: '5', chapters_total: 8, chapters_done: 8 },
        assemble: { status: 'completed', stage: 'assemble', run_seq: 1, id: '6', chapters_total: 8, chapters_done: 8 },
      },
    }

    expect(deriveNovelStatus(novel, {}).status).toBe('completed')
  })

  it('prefers websocket running progress over persisted status snapshot', () => {
    const novel: Novel = {
      ...baseNovel(),
      pipeline_status: {
        import: { status: 'completed', stage: 'import', run_seq: 1, id: '1', chapters_total: 1, chapters_done: 1 },
      },
    }

    const status = deriveNovelStatus(novel, {
      'novel-1': { stage: 'rewrite', percent: 42 },
    })
    expect(status.status).toBe('running')
    expect(status.stage).toBe('rewrite')
    expect(status.percent).toBe(42)
  })
})
