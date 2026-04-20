## ADDED Requirements

### Requirement: Backend translation utility
The system SHALL provide a translation module at `backend/i18n/` with dict-based translation lookup. The module SHALL expose a `t(key, lang)` function that returns the translated string for the given key and language. If a key is missing for the requested language, it SHALL fall back to Chinese.

#### Scenario: Translate a known key to English
- **WHEN** `t("status.processing", "en")` is called
- **THEN** it SHALL return `"Processing"`

#### Scenario: Translate a known key to Chinese
- **WHEN** `t("status.processing", "zh")` is called
- **THEN** it SHALL return `"进行中"`

#### Scenario: Missing key falls back to Chinese
- **WHEN** `t("some.key", "en")` is called and the key has no English translation
- **THEN** it SHALL return the Chinese value for that key

### Requirement: Accept-Language middleware
The system SHALL register FastAPI middleware that extracts the preferred language from the `Accept-Language` HTTP header. The detected language SHALL be stored in request state and accessible by route handlers. Supported languages are `zh` and `en`, defaulting to `zh`.

#### Scenario: Request with English Accept-Language
- **WHEN** a request arrives with `Accept-Language: en`
- **THEN** the request state SHALL contain `lang="en"`

#### Scenario: Request with no Accept-Language header
- **WHEN** a request arrives without an Accept-Language header
- **THEN** the request state SHALL contain `lang="zh"`

#### Scenario: Request with unsupported language
- **WHEN** a request arrives with `Accept-Language: fr`
- **THEN** the request state SHALL fall back to `lang="zh"`

### Requirement: Translated API responses
All user-facing strings in API responses (status labels, error messages, validation messages) SHALL be returned in the detected language. Internal error details and log messages SHALL remain in English and are not translated.

#### Scenario: Error response in English
- **WHEN** a validation error occurs and the request language is `en`
- **THEN** the error message in the response body SHALL be in English

#### Scenario: Status label in Chinese
- **WHEN** a novel status is returned and the request language is `zh`
- **THEN** the status label SHALL be in Chinese (e.g., "进行中")
