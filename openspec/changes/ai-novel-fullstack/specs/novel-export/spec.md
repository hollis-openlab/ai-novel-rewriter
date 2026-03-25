## ADDED Requirements

### Requirement: Stage Artifact 导出 API
系统 SHALL 提供通用的 Stage Artifact 导出 API，允许用户导出任意已完成 Stage 的产物，支持 JSON 和人类可读格式。

#### Scenario: 导出 Analyze Stage 的 JSON Artifact
- **WHEN** 用户对一本已完成 Analyze Stage 的小说请求导出分析结果（JSON 格式）
- **THEN** 系统返回完整的 analysis.json 文件，包含所有章节的摘要、人物、事件、场景、改写潜力数据

#### Scenario: 导出 Analyze Stage 的 Markdown 报告
- **WHEN** 用户对一本已完成 Analyze Stage 的小说请求导出分析报告（Markdown 格式）
- **THEN** 系统生成人类可读的 Markdown 报告，包含每章的摘要、人物表格、关键事件列表、场景分布表

#### Scenario: 导出 Split Stage 的章节列表
- **WHEN** 用户请求导出 Split Stage 的结果
- **THEN** 系统返回 chapters.json（章节边界+标题）或逐章 TXT 文件的 ZIP 包

#### Scenario: 导出 Mark Stage 的改写计划
- **WHEN** 用户请求导出 Mark Stage 的结果
- **THEN** 系统返回 rewrite_plan.json（所有标记、策略、预估数据）或 Markdown 格式的改写计划表

#### Scenario: 导出 Rewrite Stage 的改写结果
- **WHEN** 用户请求导出 Rewrite Stage 的结果
- **THEN** 系统返回 rewrites.json（逐段落原文/改写对）或 diff 格式文件

#### Scenario: 导出历史任务 Artifact
- **WHEN** 同一本小说存在多个任务，用户指定历史 task_id 导出某个 Stage 的产物
- **THEN** 系统返回该历史任务对应的 Stage Artifact，而不是当前活跃任务的产物

#### Scenario: 导出未完成 Stage 的 Artifact
- **WHEN** 用户请求导出一个尚未完成的 Stage 的 Artifact
- **THEN** 系统返回错误提示，说明该 Stage 尚未完成

### Requirement: 单章 Artifact 导出
系统 SHALL 支持导出单个章节的 Stage Artifact，无需导出全书数据。

#### Scenario: 导出单章分析结果
- **WHEN** 用户选择某个章节并请求导出其分析结果
- **THEN** 系统返回该章节的 ch_N_analysis.json 或对应的 Markdown 片段

### Requirement: 最终小说导出为 TXT 格式
系统 SHALL 将 Assemble Stage 的产物导出为 UTF-8 编码的 .txt 文件，章节间以原始分隔格式排列。

#### Scenario: 导出完整改写小说
- **WHEN** 用户在 Assemble Stage 完成后点击"导出 TXT"
- **THEN** 系统生成包含所有章节（含已改写和未改写部分）的完整 .txt 文件供下载

### Requirement: 最终小说导出为 EPUB 格式
系统 SHALL 支持将 Assemble Stage 的产物导出为 .epub 格式，包含目录结构。

#### Scenario: 导出 EPUB 文件
- **WHEN** 用户点击"导出 EPUB"
- **THEN** 系统生成 EPUB 文件，包含正确的目录（基于章节切分），每章作为独立的内容文件

### Requirement: 质量闸门导出策略
系统 SHALL 在最终导出前读取 Assemble 质量闸门结果。默认情况下，质量闸门阻断时拒绝导出；用户显式确认后可强制导出并附带质量报告。

#### Scenario: 质量闸门阻断导出
- **WHEN** Assemble 标记为 QUALITY_GATE_BLOCKED
- **THEN** 导出 API 返回阻断原因和阈值对比，不返回最终文件

#### Scenario: 强制导出附带质量报告
- **WHEN** 用户在阻断后选择强制导出
- **THEN** 系统生成导出文件并附带 quality_report.json（失败统计、warning 明细、risk_signature）

### Requirement: 强制导出风险签名落盘
系统 SHALL 在强制导出时把风险签名写入最终导出产物，避免后续误认为“普通通过导出”。

#### Scenario: TXT 强制导出头部标记
- **WHEN** 质量闸门阻断后用户强制导出 TXT
- **THEN** 系统在 TXT 文件头部写入风险标记块（含 risk_signature、导出时间、阻断原因摘要）

#### Scenario: EPUB 强制导出元数据标记
- **WHEN** 质量闸门阻断后用户强制导出 EPUB
- **THEN** 系统在 EPUB metadata 写入风险签名字段，并附带 quality_report.json sidecar

### Requirement: 选择性导出
系统 SHALL 允许用户选择导出范围：全书、指定章节范围、仅已改写章节。

#### Scenario: 导出指定章节范围
- **WHEN** 用户选择第 5-10 章并导出
- **THEN** 导出文件只包含选定范围内的章节

### Requirement: 导出时保留原文对照
系统 SHALL 支持以双栏对照格式导出，左侧原文、右侧改写文。

#### Scenario: 对照格式导出
- **WHEN** 用户选择"对照导出"模式
- **THEN** 导出文件中每个段落同时展示原文和改写版本，便于对比审阅

### Requirement: 前端 Artifact 导出界面
前端 SHALL 在小说详情页的每个 Stage 旁提供导出按钮，用户可选择导出格式。

#### Scenario: 从 Pipeline 视图导出
- **WHEN** 用户在小说详情页查看 Pipeline 进度，某个 Stage 显示为已完成
- **THEN** 该 Stage 旁显示"导出"按钮，点击后弹出格式选择（JSON / Markdown / TXT 等）

#### Scenario: 批量导出所有 Stage Artifact
- **WHEN** 用户点击"导出全部中间产物"
- **THEN** 系统打包所有已完成 Stage 的 Artifact 为 ZIP 文件下载
