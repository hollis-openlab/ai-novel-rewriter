import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GitDiffView } from '@/components/workbench/GitDiffView'

describe('GitDiffView', () => {
  it('shows replacement content on both sides in side-by-side mode', () => {
    render(
      <GitDiffView
        oldText="旧句子：今夜无风。"
        newText="新句子：今夜有风。"
        showModeToggle={false}
        defaultMode="side-by-side"
      />,
    )

    const section = screen.getByRole('heading', { name: 'Git 风格 Diff' }).closest('section')
    expect(section).not.toBeNull()
    expect(section).toHaveTextContent('旧句子：今夜无风。')
    expect(section).toHaveTextContent('新句子：今夜有风。')
  })

  it('keeps placeholder only for truly missing side', () => {
    render(
      <GitDiffView
        oldText={'保留行\n删除行'}
        newText="保留行"
        showModeToggle={false}
        defaultMode="side-by-side"
      />,
    )

    const section = screen.getByRole('heading', { name: 'Git 风格 Diff' }).closest('section')
    expect(section).not.toBeNull()
    expect(section).toHaveTextContent('删除行')
    expect(section).toHaveTextContent('—')
  })

  it('shows character-level summary stats for old/add/delete counts', () => {
    render(
      <GitDiffView
        oldText="ABCDE"
        newText="ABXDEY"
        showModeToggle={false}
        defaultMode="side-by-side"
      />,
    )

    const section = screen.getByRole('heading', { name: 'Git 风格 Diff' }).closest('section')
    expect(section).not.toBeNull()
    expect(section).toHaveTextContent('原文字数5 字')
    expect(section).toHaveTextContent('改写稿字数6 字')
    expect(section).toHaveTextContent('新增字数+2 字')
    expect(section).toHaveTextContent('删除字数-1 字')
  })

  it('renders flat comparison without line counters and keeps full text panes', () => {
    render(
      <GitDiffView
        oldText={'甲。乙。\n丙。'}
        newText={'乙。\n甲。\n丙。\n新增。'}
        comparisonStyle="flat"
        showModeToggle={false}
      />,
    )

    const section = screen.getByRole('heading', { name: 'Git 风格 Diff' }).closest('section')
    expect(section).not.toBeNull()
    expect(section).toHaveTextContent('甲。乙。')
    expect(section).toHaveTextContent('乙。')
    expect(section).toHaveTextContent('甲。')
    expect(section).toHaveTextContent('新增。')
    expect(section).not.toHaveTextContent('原文行数')
    expect(section).not.toHaveTextContent('改写稿行数')
  })
})
