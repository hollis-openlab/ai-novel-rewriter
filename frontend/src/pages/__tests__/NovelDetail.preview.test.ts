import { describe, expect, it } from 'vitest'
import { buildChapterPreview } from '@/pages/NovelDetail'

describe('buildChapterPreview', () => {
  it('prefers char ranges and preserves untouched text when paragraph ranges overlap', () => {
    const content = 'para1-AAAA\n\npara2-BBBB\n\npara3-CCCC'
    const paragraphs = content.split(/\n\s*\n+/).map((item) => item.trim()).filter(Boolean)
    const bStart = content.indexOf('BBBB')
    const cStart = content.indexOf('CCCC')

    const preview = buildChapterPreview(content, paragraphs, [
      {
        paragraph_range: [1, 2],
        char_offset_range: [bStart, bStart + 4],
        status: 'completed',
        rewritten_text: 'BBBB-new',
      },
      {
        paragraph_range: [2, 3],
        char_offset_range: [cStart, cStart + 4],
        status: 'completed',
        rewritten_text: 'CCCC-new',
      },
    ])

    expect(preview).toContain('para1-AAAA')
    expect(preview).toContain('BBBB-new')
    expect(preview).toContain('CCCC-new')
    expect(preview).toContain('para2-BBBB-new')
    expect(preview).toContain('para3-CCCC-new')
    expect(preview).not.toBe(content)
  })

  it('falls back to paragraph range replacement when char ranges are absent', () => {
    const content = 'para1-AAAA\n\npara2-BBBB\n\npara3-CCCC'
    const paragraphs = content.split(/\n\s*\n+/).map((item) => item.trim()).filter(Boolean)

    const preview = buildChapterPreview(content, paragraphs, [
      {
        paragraph_range: [2, 2],
        status: 'completed',
        rewritten_text: 'para2-REWRITE',
      },
    ])

    expect(preview).toBe('para1-AAAA\n\npara2-REWRITE\n\npara3-CCCC')
  })
})
