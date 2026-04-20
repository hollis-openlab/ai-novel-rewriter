## Why

The AI-novel project is released with a 100% Chinese UI and documentation. To reach international users and improve the open-source project's accessibility on GitHub, we need English language support across the full stack — frontend, backend API responses, and documentation.

## What Changes

- Add i18next + react-i18next to the frontend for runtime language switching
- Extract ~1,074 hardcoded Chinese strings from 12 frontend files into translation JSON files (zh/en)
- Add a language switcher (中/EN) to the Sidebar
- Add backend i18n middleware to detect Accept-Language and return translated API responses
- Create dual-language documentation: English as primary README.md, Chinese as README_zh.md

## Capabilities

### New Capabilities
- `frontend-i18n`: i18next setup, translation file structure (zh/en namespaces), language switcher component, and string extraction from all frontend pages/components
- `backend-i18n`: FastAPI middleware for Accept-Language detection, translation utility, and translated API response strings
- `docs-i18n`: Dual-language documentation files (README, CONTRIBUTING) with cross-links

### Modified Capabilities

(none — no existing specs)

## Impact

- **Frontend**: All 12 source files with Chinese text will be modified to use `t()` translation calls. New dependencies: `i18next`, `react-i18next`, `i18next-browser-languagedetector`. 14 new translation JSON files (7 namespaces x 2 languages).
- **Backend**: New `i18n/` module added. Middleware registered in app startup. Route handlers returning user-facing strings updated to use translation utility.
- **Documentation**: README.md rewritten to English; current Chinese content moved to README_zh.md. Same for CONTRIBUTING.md.
- **Dependencies**: 3 new npm packages (frontend). No new Python packages (simple dict-based approach).
