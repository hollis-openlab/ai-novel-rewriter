## ADDED Requirements

### Requirement: Mark 阶段 SHALL 产出句子级改写元数据
系统在构建改写计划时必须为每个改写片段保留段落范围兼容字段，并新增句子范围和字符偏移范围，用于更稳定的改写切片。

#### Scenario: 生成带句子范围的改写片段
- **WHEN** Mark 阶段基于分析结果生成 `RewriteSegment`
- **THEN** 每个 segment 必须包含 `paragraph_range`
- **THEN** 每个 segment 应包含 `sentence_range` 与 `char_offset_range`（可选但优先提供）

### Requirement: Rewrite 切片 SHALL 偏移优先并兼容回退
系统在改写阶段提取原文片段时必须优先使用字符偏移范围；当偏移信息缺失或无效时，必须回退到段落范围切片，保证旧数据可运行。

#### Scenario: 新数据优先使用偏移切片
- **WHEN** segment 含有效 `char_offset_range`
- **THEN** 改写输入文本必须由偏移范围切片得到

#### Scenario: 旧数据回退段落切片
- **WHEN** segment 不含 `char_offset_range` 或偏移非法
- **THEN** 系统必须回退到 `paragraph_range` 逻辑
- **THEN** 本章改写流程不得因缺失新字段而失败

### Requirement: Resume 行为 SHALL 只补跑未完成章节
系统在 analyze/rewrite 阶段执行 `resume` 时必须仅处理未完成章节，并以真实完成度更新阶段状态。

#### Scenario: Analyze resume 仅补跑缺失分析章节
- **WHEN** 当前任务已有部分章节分析结果
- **THEN** `resume` 只能执行缺失章节
- **THEN** 已完成章节不得重复调用分析

#### Scenario: Rewrite resume 仅补跑缺失改写章节
- **WHEN** 当前任务已有部分章节改写结果
- **THEN** `resume` 只能执行未完成章节
- **THEN** 无可改写段落章节必须计入已完成

### Requirement: 超长改写片段 SHALL 自动拆分并自动合并
系统在 Rewrite 执行阶段处理超长片段时必须自动拆分为多个子段执行，并在完成后自动合并为单段改写结果返回，不改变外部 segment 存储协议。

#### Scenario: 单片段超过安全预算时自动拆分
- **WHEN** 单个 `RewriteSegment` 文本长度超过阈值或目标字数超过模型单次安全输出预算
- **THEN** 系统必须按句末/段落边界拆分为多个子段执行
- **THEN** 前端应能读取到本段发生了拆分以及子段总数

#### Scenario: 子段执行完成后自动合并
- **WHEN** 所有子段改写执行完成
- **THEN** 系统必须输出单段 `rewritten_text`（自动合并结果）
- **THEN** 原有 `segment_id` 与 `paragraph_range` 不得因拆分而改变
