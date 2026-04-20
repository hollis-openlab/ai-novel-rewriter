## Context

The AI-novel project is a FastAPI + React 19 application for AI-assisted novel rewriting. Currently all UI text (~1,074 occurrences across 12 files), backend API messages, and documentation are in Chinese only. The project has been released on GitHub and needs English support to reach international users.

Frontend: React 19 + TypeScript + Vite + TailwindCSS. Backend: Python 3.13 + FastAPI + SQLite. No i18n infrastructure exists today.

## Goals / Non-Goals

**Goals:**
- Support Chinese (default) and English across the full stack
- Minimal disruption to existing code structure — string extraction, not rewrite
- Language preference persisted per user (localStorage for frontend, Accept-Language for backend)
- Documentation accessible in both languages

**Non-Goals:**
- Supporting more than 2 languages (architecture should allow it, but only zh/en implemented)
- Translating internal logs, developer-facing error traces, or database content
- Lazy-loading translation files (unnecessary for 2 languages with small payloads)
- Translating user-generated content (novel text, chapter content)

## Decisions

### 1. Frontend: i18next + react-i18next

**Choice**: i18next ecosystem
**Over**: react-intl (heavier ICU format unnecessary for zh/en), custom solution (too much boilerplate)
**Rationale**: Most widely adopted React i18n library. Supports namespaces for per-page translation splitting, has built-in browser language detection plugin, and localStorage persistence. Minimal API surface: `useTranslation()` hook + `t()` function.

### 2. Namespace-per-page translation structure

**Choice**: 7 namespace JSON files per language, mapped to pages
**Over**: Single flat file, component-level splitting
**Rationale**: Matches existing page-based architecture. `common.json` holds shared strings (nav, status, buttons). Each page's strings stay co-located for easy maintenance. Not too granular to be cumbersome.

### 3. Backend: Simple dict-based translation

**Choice**: Python dict lookup with `t(key, lang)` utility
**Over**: gettext/babel, third-party i18n library
**Rationale**: Backend has relatively few user-facing strings (status labels, error messages). A simple dict approach avoids new dependencies and is trivial to maintain. Language detected from Accept-Language header via lightweight middleware.

### 4. Documentation: Dual separate files

**Choice**: `README.md` (English) + `README_zh.md` (Chinese), cross-linked
**Over**: Single bilingual file, i18n documentation framework
**Rationale**: Standard GitHub convention. English as primary README for international discoverability. Each file is independently maintainable. Cross-links at top of each file.

### 5. Language switcher in Sidebar

**Choice**: Toggle button at bottom of Sidebar component
**Over**: Top navbar dropdown, settings page option
**Rationale**: Sidebar is always visible. Simple 中/EN toggle is sufficient for 2 languages. No need for a dropdown selector. Calls `i18n.changeLanguage()` directly.

## Risks / Trade-offs

- **Translation completeness** → All 1,074 Chinese occurrences must be extracted. Risk of missing strings. Mitigation: Grep for Chinese characters after extraction to catch stragglers.
- **Key naming consistency** → With 7 namespace files, keys could diverge in naming style. Mitigation: Flat dot-notation convention (`status.processing`, `error.uploadFailed`), documented in PR.
- **Backend string coverage** → Some API responses may embed Chinese strings we miss. Mitigation: Review all route handlers returning user-facing messages.
- **Maintenance burden** → Two translation files must stay in sync. Mitigation: Keep namespace structure aligned; future tooling (i18n linting) can be added if needed.
