## ADDED Requirements

### Requirement: Dual-language README
The project SHALL provide `README.md` in English as the primary documentation and `README_zh.md` in Chinese. Both files SHALL contain equivalent content. Each file SHALL include a cross-link to the other language version at the top.

#### Scenario: English README has Chinese link
- **WHEN** a user opens README.md
- **THEN** a link to README_zh.md with label "中文" SHALL be visible near the top

#### Scenario: Chinese README has English link
- **WHEN** a user opens README_zh.md
- **THEN** a link to README.md with label "English" SHALL be visible near the top

#### Scenario: Content equivalence
- **WHEN** both README files are compared
- **THEN** they SHALL cover the same sections: project description, features, installation, usage, configuration, contributing, license

### Requirement: Dual-language CONTRIBUTING guide
The project SHALL provide `CONTRIBUTING.md` in English and `CONTRIBUTING_zh.md` in Chinese. Both files SHALL contain equivalent content with cross-links at the top.

#### Scenario: English CONTRIBUTING has Chinese link
- **WHEN** a user opens CONTRIBUTING.md
- **THEN** a link to CONTRIBUTING_zh.md with label "中文" SHALL be visible near the top

#### Scenario: Chinese CONTRIBUTING has English link
- **WHEN** a user opens CONTRIBUTING_zh.md
- **THEN** a link to CONTRIBUTING.md with label "English" SHALL be visible near the top
