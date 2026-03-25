## ADDED Requirements

### Requirement: 配置范围收敛
系统 SHALL 将“规则配置”范围限定为三项：全局提示词（global prompt）、场景识别规则（scene rules）、改写规则（rewrite rules）。

#### Scenario: 打开配置页
- **WHEN** 用户进入配置管理页面
- **THEN** 页面仅展示全局提示词、场景识别规则、改写规则三类配置

### Requirement: 规则默认空白初始化
系统 SHALL 在初始状态不内置任何场景识别规则和改写规则，等待用户手动创建。

#### Scenario: 首次进入规则配置
- **WHEN** 系统首次初始化配置
- **THEN** `scene_rules` 与 `rewrite_rules` 均为空数组
- **AND** 页面展示“请先添加规则”的空状态提示

### Requirement: AI Config Bar 自然语言解析
系统 SHALL 提供自然语言配置解析 API（POST /api/v1/config/ai-parse），仅解析上述三类配置变更。

#### Scenario: 解析全局提示词修改
- **WHEN** 用户输入“全局提示词改成：你是一个偏写实风格的小说改写助手”
- **THEN** API 返回 global_prompt 字段变更预览

#### Scenario: 解析规则新增
- **WHEN** 用户输入“新增场景规则：修炼突破，关键词是突破、丹田”
- **THEN** API 返回待新增 scene rule 的结构化预览

#### Scenario: 超出范围的参数请求
- **WHEN** 用户输入“把 temperature 调到 0.8”
- **THEN** API 返回 clarification，提示“模型参数请在 provider 配置中调整”

### Requirement: AI Config Bar 变更应用
系统 SHALL 提供配置变更应用 API（POST /api/v1/config/ai-apply），对解析结果进行校验并写入配置。

#### Scenario: 应用确认变更
- **WHEN** 用户确认 AI Config Bar 变更预览
- **THEN** 系统写入配置并返回最新配置快照

### Requirement: 场景识别规则 CRUD
系统 SHALL 提供场景识别规则 CRUD，每条规则包含：scene_type、keywords、weight、enabled。

#### Scenario: 创建场景识别规则
- **WHEN** 用户新增场景规则“修炼突破”
- **THEN** 规则保存成功并在后续 Analyze 中生效

### Requirement: 改写规则 CRUD
系统 SHALL 提供改写规则 CRUD，每条规则包含：scene_type、strategy、target_ratio、priority、enabled。

#### Scenario: 配置场景改写规则
- **WHEN** 用户为“修炼突破”配置 strategy=expand, target_ratio=2.2
- **THEN** 该规则在 Mark/Rewrite 阶段生效

### Requirement: 配置导出为 JSON
系统 SHALL 支持导出配置为 JSON，至少包含 global_prompt、scene_rules、rewrite_rules。

#### Scenario: 导出完整配置
- **WHEN** 用户点击“导出配置”
- **THEN** 系统返回 JSON 文件，包含 version、global_prompt、scene_rules、rewrite_rules 字段

### Requirement: 从 JSON 导入配置
系统 SHALL 支持从 JSON 导入上述配置，并在应用前执行结构校验和预览确认。

#### Scenario: 导入有效配置
- **WHEN** 用户上传结构合法的配置 JSON
- **THEN** 系统展示导入预览并在确认后覆盖写入

#### Scenario: 导入无效配置
- **WHEN** 用户上传缺失必填字段（如 global_prompt）的 JSON
- **THEN** 系统返回 CONFIG_INVALID 及详细字段错误信息
