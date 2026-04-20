## ADDED Requirements

### Requirement: i18next initialization and configuration
The system SHALL initialize i18next with react-i18next and i18next-browser-languagedetector. The default language SHALL be `zh` with fallback to `zh`. Language preference SHALL be persisted to localStorage. Translation files SHALL be organized as namespace JSON files under `src/locales/{lang}/`.

#### Scenario: First visit with Chinese browser
- **WHEN** a user visits the app for the first time with a Chinese browser locale
- **THEN** the UI SHALL display in Chinese and store `zh` in localStorage

#### Scenario: First visit with English browser
- **WHEN** a user visits the app for the first time with an English browser locale
- **THEN** the UI SHALL display in English and store `en` in localStorage

#### Scenario: Return visit with stored preference
- **WHEN** a user returns to the app with a previously stored language preference
- **THEN** the UI SHALL use the stored preference regardless of browser locale

### Requirement: Translation namespace structure
The system SHALL provide 7 translation namespaces per language: `common`, `dashboard`, `novels`, `novelDetail`, `chapterEditor`, `config`, `providers`. The `common` namespace SHALL be loaded globally. Page-specific namespaces SHALL be used by their corresponding page components.

#### Scenario: Common namespace available on all pages
- **WHEN** any page is rendered
- **THEN** translations from the `common` namespace (navigation labels, status badges, buttons, time formatting) SHALL be available

#### Scenario: Page namespace isolation
- **WHEN** the NovelDetail page is rendered
- **THEN** it SHALL use translations from both `common` and `novelDetail` namespaces

### Requirement: Language switcher component
The system SHALL provide a language toggle button in the Sidebar. The button SHALL display "EN" when current language is `zh` and "中" when current language is `en`. Clicking the button SHALL immediately switch the entire UI language without page reload.

#### Scenario: Switch from Chinese to English
- **WHEN** user clicks the language toggle showing "EN" while UI is in Chinese
- **THEN** the UI SHALL immediately re-render all text in English and persist `en` to localStorage

#### Scenario: Switch from English to Chinese
- **WHEN** user clicks the language toggle showing "中" while UI is in English
- **THEN** the UI SHALL immediately re-render all text in Chinese and persist `zh` to localStorage

### Requirement: All Chinese strings extracted to translation files
The system SHALL extract all hardcoded Chinese strings from the 12 frontend source files into translation JSON files. No Chinese characters SHALL remain hardcoded in TSX/TS source files (except translation file imports and configuration). All extracted strings SHALL have corresponding English translations.

#### Scenario: No hardcoded Chinese in source files after extraction
- **WHEN** a grep for Chinese characters is run on frontend src/ (excluding locales/)
- **THEN** zero matches SHALL be found in TSX/TS files

#### Scenario: All translation keys have both zh and en values
- **WHEN** the zh and en translation files are compared
- **THEN** every key present in zh SHALL also be present in en, and vice versa

### Requirement: Interpolation and dynamic content
The system SHALL support i18next interpolation for dynamic values including: character counts, chapter counts, relative timestamps, percentages, and numeric formatting. Locale-specific formatting (e.g., `toLocaleDateString`) SHALL use the current i18n language.

#### Scenario: Dynamic character count display
- **WHEN** a novel with 50,000 characters is displayed in Chinese
- **THEN** it SHALL show "5 万字"
- **WHEN** displayed in English
- **THEN** it SHALL show "50k chars"

#### Scenario: Relative time formatting
- **WHEN** an event occurred 3 minutes ago and language is Chinese
- **THEN** it SHALL show "3 分钟前"
- **WHEN** language is English
- **THEN** it SHALL show "3 minutes ago"
