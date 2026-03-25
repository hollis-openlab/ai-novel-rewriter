# AI Novel — Design System (Master)

> **LOGIC:** When building a specific page, first check `design-system/ai-novel/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** AI Novel
**Style:** Apple-inspired Minimalism + Bento Grid + AI-Native
**Stack:** React 19 + TailwindCSS + shadcn/ui + Lucide Icons

---

## Color Palette

### Light Mode (Default)

| Token | Hex | Tailwind | Usage |
|-------|-----|----------|-------|
| `--bg-page` | `#F5F5F7` | custom | Page background (Apple off-white) |
| `--bg-card` | `#FFFFFF` | `white` | Card backgrounds, panels |
| `--bg-subtle` | `#E8E8ED` | custom | Dividers, disabled, hover bg |
| `--text-primary` | `#1D1D1F` | custom | Headings, body text |
| `--text-secondary` | `#6E6E73` | custom | Descriptions, labels |
| `--text-tertiary` | `#86868B` | custom | Placeholders, hints |
| `--accent` | `#0071E3` | custom | Primary CTA, links, focus rings |
| `--accent-hover` | `#0077ED` | custom | Hover on accent |
| `--success` | `#34C759` | custom | Completed stages |
| `--warning` | `#FF9500` | custom | Stale stages, attention |
| `--error` | `#FF3B30` | custom | Failed, errors |
| `--ai-purple` | `#6366F1` | `indigo-500` | AI Config Bar glow, AI features |
| `--border` | `#D2D2D7` | custom | Card borders, input borders |

### Dark Mode

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-page` | `#000000` | Page background |
| `--bg-card` | `#1C1C1E` | Card backgrounds |
| `--bg-subtle` | `#2C2C2E` | Sections, hover bg |
| `--text-primary` | `#F5F5F7` | Headings, body |
| `--text-secondary` | `#A1A1A6` | Descriptions |
| `--accent` | `#0A84FF` | Links, actions |
| `--border` | `#38383A` | Borders |

### Scene Color Coding

| Scene Type | Background | Border Left | Label Color |
|------------|-----------|-------------|-------------|
| 战斗 Combat | `#FEF2F2` | `#EF4444` | Red |
| 对话 Dialogue | `#EFF6FF` | `#3B82F6` | Blue |
| 心理描写 Psychology | `#F5F3FF` | `#8B5CF6` | Purple |
| 环境描写 Environment | `#F0FDF4` | `#22C55E` | Green |
| 叙事过渡 Narration | `#F8FAFC` | `#94A3B8` | Slate |
| 感情互动 Romance | `#FFF1F2` | `#F43F5E` | Rose |
| 回忆闪回 Flashback | `#FFFBEB` | `#F59E0B` | Amber |
| 日常生活 Daily | `#F0F9FF` | `#0EA5E9` | Sky |

---

## Typography

**Primary Font:** Inter (closest Google Fonts to SF Pro)
**Chinese Fallback:** `'PingFang SC', 'Noto Sans SC', 'Microsoft YaHei'`
**Mono Font:** `'SF Mono', 'JetBrains Mono', 'Fira Code', monospace`

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
```

**Tailwind Config:**
```js
fontFamily: {
  sans: ['Inter', 'PingFang SC', 'Noto Sans SC', 'sans-serif'],
  mono: ['JetBrains Mono', 'SF Mono', 'Fira Code', 'monospace'],
}
```

| Token | Size | Weight | Line Height | Letter Spacing | Usage |
|-------|------|--------|-------------|----------------|-------|
| `display` | 34px / 2.125rem | 700 | 1.12 | -0.01em | Page titles |
| `title-1` | 28px / 1.75rem | 700 | 1.14 | -0.01em | Section headings |
| `title-2` | 22px / 1.375rem | 600 | 1.18 | -0.005em | Card titles |
| `title-3` | 17px / 1.0625rem | 600 | 1.24 | 0 | Subsections |
| `body` | 15px / 0.9375rem | 400 | 1.53 | 0 | Main content |
| `body-bold` | 15px / 0.9375rem | 600 | 1.53 | 0 | Emphasis |
| `callout` | 14px / 0.875rem | 400 | 1.43 | 0 | Secondary info |
| `caption` | 12px / 0.75rem | 500 | 1.33 | 0.01em | Labels, meta, badges |
| `mono` | 13px / 0.8125rem | 400 | 1.5 | 0 | Code, JSON, prompts |

---

## Spacing

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | 4px | Inline micro gaps |
| `--space-2` | 8px | Icon-text gaps, tight padding |
| `--space-3` | 12px | Compact card padding |
| `--space-4` | 16px | Standard padding, list gaps |
| `--space-5` | 20px | Bento grid gap |
| `--space-6` | 24px | Card padding, section gaps |
| `--space-8` | 32px | Page padding, section separators |
| `--space-12` | 48px | Major section dividers |

**Layout Constants:**
- Sidebar width: `260px` (collapsed: `64px`)
- Content max-width: `1280px`
- Bento grid gap: `20px`
- Card border-radius: `16px` (Apple standard)

---

## Component Tokens

| Token | Value |
|-------|-------|
| `--radius-sm` | `8px` |
| `--radius-md` | `12px` |
| `--radius-lg` | `16px` — cards, panels |
| `--radius-xl` | `20px` — modal, hero cards |
| `--radius-full` | `9999px` — pills, badges |
| `--shadow-xs` | `0 1px 2px rgba(0,0,0,0.04)` |
| `--shadow-sm` | `0 2px 8px rgba(0,0,0,0.06)` |
| `--shadow-md` | `0 4px 16px rgba(0,0,0,0.08)` |
| `--shadow-lg` | `0 8px 32px rgba(0,0,0,0.12)` |
| `--shadow-ai-glow` | `0 0 24px rgba(99,102,241,0.15)` — AI Config Bar |
| `--transition-fast` | `150ms ease-out` |
| `--transition-normal` | `200ms ease-out` |
| `--transition-slow` | `300ms ease-out` |

---

## Global Layout

```
┌───────────────────────────────────────────────────────────┐
│                                                           │
│  ┌─ Sidebar ─────┐  ┌─ Main Content ──────────────────┐  │
│  │                │  │                                  │  │
│  │  Logo          │  │  Page Header                     │  │
│  │                │  │  ─────────────────────────────    │  │
│  │  Nav Items     │  │                                  │  │
│  │  · Dashboard   │  │  Content Area                    │  │
│  │  · Novels      │  │  (page-specific)                 │  │
│  │  · Config      │  │                                  │  │
│  │  · Providers   │  │                                  │  │
│  │                │  │                                  │  │
│  │  ─────────     │  │                                  │  │
│  │  Worker        │  │                                  │  │
│  │  Monitor       │  │                                  │  │
│  │  (mini)        │  │                                  │  │
│  └────────────────┘  └──────────────────────────────────┘  │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

- **Sidebar**: `bg-page`, nav items with `rounded-lg` + `hover:bg-subtle` + `transition-colors 150ms`
- **Active nav**: `bg-white` + `shadow-xs` + `font-weight: 600` + accent icon color
- **Main content**: `bg-white rounded-2xl` inset in page bg, `padding: 32px`
- **Sidebar bottom**: Worker Pool mini-monitor (active/idle/queue counts)
- **< 1280px**: sidebar collapses to icon-only 64px

---

## Icon System

- **Library**: Lucide React (`lucide-react`)
- **Default size**: `20px` (`w-5 h-5`)
- **Compact size**: `16px` (`w-4 h-4`)
- **Header size**: `24px` (`w-6 h-6`)
- **Stroke width**: `1.5` (Apple-like thin strokes)
- **Color**: `currentColor` (inherits text color)

---

## Animation Principles

1. Always check `prefers-reduced-motion`
2. Micro-interactions: `150ms ease-out`
3. State transitions: `200ms ease-out`
4. Page/panel transitions: `300ms ease-out`
5. Only animate `transform` + `opacity` (GPU composited)
6. Card hover: `translateY(-2px)` + shadow expansion (NOT scale)
7. No infinite animations except loading spinners/skeletons
8. Use shadcn `Skeleton` component for loading states

---

## Anti-Patterns (NEVER)

- ❌ Emojis as icons — use Lucide SVG
- ❌ Missing `cursor-pointer` on clickable elements
- ❌ `scale` transforms that shift layout
- ❌ Text contrast < 4.5:1
- ❌ Instant state changes without transition
- ❌ Animations > 500ms for UI elements
- ❌ `linear` easing (use `ease-out`)
- ❌ Colors as sole error indicator (add icons/text)

---

## Pre-Delivery Checklist

- [ ] No emojis as icons (Lucide SVG only)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with `transition-all duration-200 ease-out`
- [ ] Light mode text contrast >= 4.5:1
- [ ] Dark mode tested, all tokens switching
- [ ] Focus rings visible (`ring-2 ring-accent ring-offset-2`)
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive tested: 1024px, 1280px, 1440px, 1920px
- [ ] Chinese text renders with correct fallback
- [ ] Skeleton loading states for async content
