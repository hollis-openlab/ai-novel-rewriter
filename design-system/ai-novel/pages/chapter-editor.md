# Chapter Editor Page Design

> Overrides MASTER.md for Chapter Editor

## Layout: Three-Panel

```
┌──────────────────────────────────────────────────────────────┐
│  ← Ch.3 秘境之门                [Scene ▾] [Rewrite ▾] [···] │
├──────────┬───────────────────────────────┬───────────────────┤
│ Left     │  Center: Text Editor          │ Right Panel       │
│ Nav      │                               │                   │
│ (toggle) │  ┌─ Scene: 战斗 ──────────┐   │ ┌─ Tab Bar ────┐ │
│          │  │ ░░░ 张无忌挥剑斩向      │   │ │ 人物│事件│建议│ │
│ ┌──────┐ │  │ ░░░ 山匪首领，剑光      │   │ ├─────────────┤ │
│ │ Ch.1 │ │  │ ░░░ 如虹，一道凌厉      │   │ │             │ │
│ │ Ch.2 │ │  │ ░░░ 的弧线划过...       │   │ │  张无忌      │ │
│ │>Ch.3 │ │  │     [可扩写 P5]         │   │ │  情绪: 愤怒  │ │
│ │ Ch.4 │ │  └────────────────────────┘   │ │  状态: 受伤  │ │
│ │ Ch.5 │ │                               │ │  角色: 主角  │ │
│ │ ...  │ │  ┌─ Scene: 对话 ──────────┐   │ │             │ │
│ └──────┘ │  │ "你以为你能逃得掉？"    │   │ │  王大彪      │ │
│          │  │ 山匪首领冷笑道。        │   │ │  情绪: 贪婪  │ │
│          │  │     [保留]              │   │ │  角色: 对手  │ │
│          │  └────────────────────────┘   │ └─────────────┘ │
├──────────┴───────────────────────────────┴───────────────────┤
│  ┌─ Bottom Bar (expandable) ─────────────────────────────┐   │
│  │  摘要 │ Prompt 日志 │ 分析 JSON                        │   │
│  └───────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## Layout Overrides

- **Full viewport height** — no page scroll, panels scroll independently
- **Three-panel**: Left nav (220px, collapsible) + Center (flex-1) + Right (320px, collapsible)
- All panels have independent scroll
- Bottom bar: collapsed 40px, expanded 300px (slides up)

## Center: Text Editor

- Scene segments: `border-l-3` with scene color + subtle scene bg
- Scene badge: top-right of segment, pill with scene color
- Rewrite markers: dashed `border-2` + floating priority badge
- **View modes** (toggle buttons in header):
  - Scene View: color-coded segments
  - Rewrite View: highlight marked paragraphs
  - Compare View: split panel (original left / rewritten right)
- Novel body text: `body` size, `line-height: 1.8` (reading-optimized)

## Right Panel: Three Tabs

### 人物 Tab
- Stacked character cards: `rounded-xl p-4 bg-white shadow-xs`
- Name: `title-3`, Emotion: colored pill tag, State: `callout`, Role: `caption` badge

### 事件 Tab
- Vertical timeline with colored dots (importance → color intensity)
- Clickable → scrolls center to paragraph

### 建议 Tab
- Rewrite suggestions list with paragraph range, priority stars, [Apply] button

## Bottom Bar

- Collapsed: thin tab strip at bottom
- Tabs: 摘要 (summary text) | Prompt 日志 (LLM call timeline) | 分析 JSON (raw artifact viewer)
- Expand: click tab or drag handle upward

## Color Overrides

- Use scene color coding from MASTER for segment backgrounds
- Priority badges: P1-2 `text-secondary`, P3 `text-accent`, P4-5 `text-warning` with bold
