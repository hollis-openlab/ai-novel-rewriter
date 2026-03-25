# Dashboard Page Design

> Overrides MASTER.md for Dashboard-specific rules

## Layout: Bento Grid

```
┌──────────────────────────────────────────────────────────┐
│  Dashboard                                    [+ Import] │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐        │
│  │  Processing  │ │  Completed   │ │   Failed    │        │
│  │     3        │ │     12       │ │     1       │        │
│  │  ◉ active    │ │  ✓ done      │ │  ! alert    │        │
│  └─────────────┘ └─────────────┘ └─────────────┘        │
│                                                          │
│  ┌─ Novel List ──────────────────────────────────────┐   │
│  │                                                    │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │  《仙剑奇缘》           [Analyze ●●●○○○ 48%] │  │   │
│  │  │  326,000 字 · 89 章 · 2h ago                 │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  │                                                    │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │  《都市修仙录》                   [Completed ✓] │  │   │
│  │  │  510,000 字 · 156 章 · 1d ago                │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  │                                                    │   │
│  └────────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Recent Activity ─────────┐ ┌─ Worker Pool ────────┐  │
│  │  ✓ Ch.15 analyzed  2m ago │ │  Active: 5 / 8       │  │
│  │  ✓ Ch.14 analyzed  3m ago │ │  Queue:  12          │  │
│  │  ↻ Ch.13 retrying  4m ago │ │  Speed:  4.2/min     │  │
│  │  ✓ Export done     12m ago│ │  ████████░░  62%     │  │
│  └───────────────────────────┘ └──────────────────────┘  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Stats Cards (Top Row)

- 3 cards in a row, equal width, `gap-5`
- Card: `bg-white rounded-2xl p-6 shadow-xs`
- Number: `display` size, `font-weight: 700`
- Label: `caption` size, `text-secondary`
- Active indicator: small Lucide icon, color-coded (success/warning/error)
- Hover: `shadow-sm` + `translateY(-2px)`, `transition 200ms`

## Novel List

- Each novel row: `bg-white rounded-xl p-5`, `hover:shadow-sm`
- Left: Title (`title-2`) + meta line (`callout`, `text-secondary`)
- Right: Pipeline progress badge
  - Processing: accent pill with mini progress dots
  - Completed: success pill with check icon
  - Failed: error pill with alert icon
- Click row → navigates to Novel Detail Page

## Bottom Bento Row

- 2 columns: Recent Activity (wider, ~60%) + Worker Pool (40%)
- Activity: Lucide icon + description + relative time
- Worker Pool: active/idle counts, queue, throughput, progress bar

## Empty State

- Centered: simple line illustration (NOT emoji)
- "Import your first novel" `title-2`
- "Supports .txt and .epub files" `callout text-secondary`
- Large `[+ Import Novel]` button, accent color, `rounded-xl`
