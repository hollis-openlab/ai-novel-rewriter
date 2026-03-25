# Novel Detail Page Design

> Overrides MASTER.md for Novel Detail Page

## Layout

```
┌──────────────────────────────────────────────────────────┐
│  ← Back    《仙剑奇缘》                   [Export ▾]     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Meta Card ───────────────────────────────────────┐   │
│  │  326,000 字  ·  89 章  ·  txt  ·  导入于 3/18     │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Pipeline ────────────────────────────────────────┐   │
│  │                                                    │   │
│  │  [Import]──[Split]──[Analyze]──[Mark]──[Rewrite]──[Assemble] │
│  │    ✓         ✓       ●●●○      ○        ○         ○  │   │
│  │                      48%                              │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Stage Detail Card ───────────────────────────────┐   │
│  │  Analyze Stage                                     │   │
│  │  ─────────────────────────────────────────         │   │
│  │  Progress: 43/89 chapters  ████████░░░ 48%         │   │
│  │  Elapsed: 1h 23m  ·  Est. remaining: 1h 32m       │   │
│  │                                                    │   │
│  │  [Pause]  [Export Artifact]  [View Prompt Log]     │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Chapter List ────────────────────────────────────┐   │
│  │  Filter: [All ▾] [Analyzed ▾] [Failed ▾]          │   │
│  │  ─────────────────────────────────────────         │   │
│  │  Ch.1  第一章 初入江湖        ✓✓✓○○○  Analyzed    │   │
│  │  Ch.2  第二章 山匪围城        ✓✓✓○○○  Analyzed    │   │
│  │  Ch.3  第三章 秘境之门        ✓✓●○○○  Analyzing   │   │
│  │  Ch.4  第四章 真相大白        ○○○○○○  Pending     │   │
│  │  ...                                               │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

## Pipeline Progress Bar

- Horizontal chain of 6 stage nodes connected by lines
- Node states:
  - **Completed** ✓: `bg-success` filled circle, green line to next
  - **Running** ●: `bg-accent` with pulse animation, blue line
  - **Pending** ○: `bg-subtle` outline circle, gray line
  - **Failed** ✗: `bg-error` with exclamation icon
  - **Stale** ⚠: `bg-warning` with warning icon, dashed border
- Click a node → expands Stage Detail Card below
- Each completed node has a subtle "Export" icon on hover

## Stage Detail Card

- Appears below pipeline when a stage node is clicked
- Shows: progress bar, elapsed/remaining time, action buttons
- Actions per state:
  - Running: [Pause] [View Log]
  - Completed: [Re-run] [Export JSON] [Export Markdown]
  - Failed: [Retry] [View Error] [Edit Prompt]
  - Stale: "Upstream data updated" banner + [Refresh]

## Chapter List

- Table-like list, each row clickable → Chapter Editor
- Columns: Index, Title, Stage Progress (6 dots), Status Badge
- Stage progress dots: mini version of pipeline, one dot per stage
- Status badges: pill shaped, color coded
- Filter bar: dropdown filters by status
