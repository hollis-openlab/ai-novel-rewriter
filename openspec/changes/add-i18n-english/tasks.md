## 1. Frontend i18n Setup

- [x] 1.1 Install i18next, react-i18next, i18next-browser-languagedetector npm packages
- [x] 1.2 Create `src/i18n.ts` configuration file with namespace registration, browser language detection, and localStorage persistence
- [x] 1.3 Import i18n config in `main.tsx` and wrap app with I18nextProvider
- [x] 1.4 Create `src/locales/zh/common.json` and `src/locales/en/common.json` with shared strings (navigation, status badges, buttons, time formatting)

## 2. Language Switcher

- [x] 2.1 Create `src/components/LanguageSwitcher.tsx` toggle component (shows EN/中)
- [x] 2.2 Integrate LanguageSwitcher into Sidebar component

## 3. Frontend String Extraction — High Priority Pages

- [x] 3.1 Extract Chinese strings from `pages/NovelDetail.tsx` into `novelDetail.json` (zh/en)
- [x] 3.2 Extract Chinese strings from `pages/Config.tsx` into `config.json` (zh/en)
- [x] 3.3 Extract Chinese strings from `pages/ChapterEditor.tsx` into `chapterEditor.json` (zh/en)
- [x] 3.4 Extract Chinese strings from `pages/Providers.tsx` into `providers.json` (zh/en)
- [x] 3.5 Extract Chinese strings from `pages/Dashboard.tsx` into `dashboard.json` (zh/en)

## 4. Frontend String Extraction — Remaining Files

- [x] 4.1 Extract Chinese strings from `pages/Novels.tsx` into `novels.json` (zh/en)
- [x] 4.2 Extract Chinese strings from `components/workbench/GitDiffView.tsx` into `common.json` (zh/en)
- [x] 4.3 Extract Chinese strings from `components/layout/Sidebar.tsx` into `common.json` (zh/en)
- [x] 4.4 Extract Chinese strings from `lib/stage-insights.ts`, `lib/diff.ts`, `lib/prompt-logs.ts` into `common.json` (zh/en)
- [x] 4.5 Extract Chinese strings from `types/index.ts` into translation files (zh/en)

## 5. Frontend Verification

- [x] 5.1 Grep for remaining Chinese characters in src/ (excluding locales/) — ensure zero matches
- [x] 5.2 Verify all zh keys have corresponding en keys and vice versa
- [x] 5.3 Verify language switching works without page reload

## 6. Backend i18n

- [x] 6.1 Create `backend/i18n/__init__.py` with `t(key, lang)` translation utility
- [x] 6.2 Create `backend/i18n/zh.py` and `backend/i18n/en.py` translation dicts
- [x] 6.3 Add Accept-Language middleware to FastAPI app that stores detected lang in request state
- [x] 6.4 Update route handlers returning user-facing strings to use `t()` utility
- [x] 6.5 Add backend i18n tests

## 7. Documentation

- [x] 7.1 Rename current `README.md` to `README_zh.md`, add English cross-link at top
- [x] 7.2 Create English `README.md` with equivalent content and Chinese cross-link at top
- [x] 7.3 Rename current `CONTRIBUTING.md` to `CONTRIBUTING_zh.md`, add English cross-link at top
- [x] 7.4 Create English `CONTRIBUTING.md` with equivalent content and Chinese cross-link at top
