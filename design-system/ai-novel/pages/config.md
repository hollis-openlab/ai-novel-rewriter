# Config Page Design

> Overrides MASTER.md for Config Page

## Layout

```
┌──────────────────────────────────────────────────────────────┐
│  Settings                                                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─ AI Config Bar ─────────────────────────────────────────┐  │
│  │  🔍 Describe what you want to change...                  │  │
│  │                                                          │  │
│  │  Recent: "战斗扩写2.5x" · "分析用Claude" · "温度0.8"     │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌─ Tab Bar ──────────────────────────────────────────────┐   │
│  │  场景规则 │ 改写策略 │ Prompt 工作台 │ 参数 │ 预设 │ JSON │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ Tab Content ─────────────────────────────────────────┐   │
│  │                                                        │   │
│  │  (varies by selected tab, see below)                   │   │
│  │                                                        │   │
│  └────────────────────────────────────────────────────────┘   │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## AI Config Bar (Hero Element)

**This is the most important interaction element on the page.**

- Position: top of config page, always visible
- Style: large input, `rounded-2xl`, `shadow-md`, prominent
- Focused state: `shadow-ai-glow` (indigo glow), border becomes `ai-purple`
- Height: `56px`, font-size: `body` (15px)
- Placeholder: "Describe what you want to change..." (italic, `text-tertiary`)
- Below input: recent history chips (clickable, `rounded-full bg-subtle`)

### AI Config Bar Interaction Flow

```
1. User types → debounce 500ms → show autocomplete suggestions
2. User presses Enter → loading shimmer on bar
3. Response arrives → Diff Preview Card slides down below bar

┌─ Diff Preview ──────────────────────────────────────────┐
│                                                          │
│  Understood: 将战斗场景的扩写比例从 2.0x 改为 2.5x        │
│                                                          │
│  ┌─ Change ──────────────────────────────────────────┐   │
│  │  rewrite_strategies.combat.target_ratio            │   │
│  │  2.0  →  2.5                                       │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│                              [Cancel]  [Apply Changes]   │
└──────────────────────────────────────────────────────────┘
```

- Diff card: `bg-white rounded-xl shadow-lg border border-border`
- Old value: `text-secondary` with strikethrough
- New value: `text-primary font-bold`
- Confidence indicator: if < 0.8, show amber warning
- Clarification: if LLM unsure, show question + quick-reply buttons

## Tab: 场景规则 (Scene Rules)

```
┌──────────────────────────────────────────────────────────┐
│  Scene Rules                              [+ Add Rule]   │
│                                                          │
│  ┌─ 战斗 Combat ──────────────────────────────────────┐  │
│  │  Keywords: [剑] [攻击] [防御] [闪避] [+]            │  │
│  │  Weight: ████████░░  0.8                            │  │
│  │  Prompt Template: [Edit]                            │  │
│  │  Color: ● #EF4444                                   │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ 对话 Dialogue ────────────────────────────────────┐  │
│  │  Keywords: [说道] [问道] [回答] [+]                  │  │
│  │  Weight: ██████░░░░  0.6                            │  │
│  │  ...                                                │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

- Each rule: expandable card, `rounded-xl bg-white shadow-xs`
- Keywords: tag pills with [×] delete, `rounded-full bg-subtle`
- Weight: slider (`0.0–1.0`)
- Drag handle on left for reordering

## Tab: Prompt 工作台 (Prompt Workbench)

```
┌──────────────────────────────────────────────────────────┐
│  System Prompts                                          │
│                                                          │
│  ┌─ Global ──────────────────────────────────────────┐   │
│  │  你是一个专业的网络小说分析与改写助手。你精通中文网    │   │
│  │  络小说的叙事技巧、人物塑造和场景描写...             │   │
│  │  [Edit]                                            │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  ┌─ Stage Overrides ─────────────────────────────────┐   │
│  │  [Analyze]  [Rewrite]                              │   │
│  │                                                    │   │
│  │  你是一个精准的小说文本分析师...                     │   │
│  │  [Edit]  [Reset to Default]                        │   │
│  └───────────────────────────────────────────────────┘   │
│                                                          │
│  Task Templates                                          │
│  ┌────────────────────────┬──────────────────────────┐   │
│  │  Editor                │  Preview                  │   │
│  │                        │                           │   │
│  │  请对以下章节进行深度    │  请对以下章节进行深度      │   │
│  │  分析。                 │  分析。                    │   │
│  │                        │                           │   │
│  │  【小说信息】            │  【小说信息】              │   │
│  │  书名：{{novel_title}}  │  书名：仙剑奇缘           │   │
│  │  {{#if novel_genre}}    │  类型：玄幻               │   │
│  │  类型：{{novel_genre}}  │                           │   │
│  │  {{/if}}               │  【本章】第三章 秘境之门    │   │
│  │                        │  ...                      │   │
│  │  [{{] auto-complete ▾  │                           │   │
│  └────────────────────────┴──────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

- Template editor: split view, left=edit (mono font), right=preview (rendered)
- `{{` trigger → dropdown autocomplete with all available variables
- Variables in editor: highlighted with `bg-indigo-50 text-ai-purple rounded px-1`
- Preview: uses sample data, updates in real-time

## Tab: JSON

- Full-screen code editor (Monaco-based or CodeMirror)
- Mono font, syntax highlighting
- Toolbar: [Format] [Validate] [Copy] [Export] [Import]
- Validation errors: inline red underlines + error panel at bottom
