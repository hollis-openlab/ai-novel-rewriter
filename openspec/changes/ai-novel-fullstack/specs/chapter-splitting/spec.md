## ADDED Requirements

### Requirement: 基于规则的章节切分
系统 SHALL 使用正则表达式规则匹配常见章节标记模式（如"第X章"、"第X节"、"Chapter X"等），将小说文本切分为独立章节。

#### Scenario: 匹配标准章节格式
- **WHEN** 小说文本包含"第一章"、"第二章"等标准格式
- **THEN** 系统按这些标记切分为对应的章节列表，每个章节包含标题和内容

#### Scenario: 匹配英文章节格式
- **WHEN** 小说文本包含"Chapter 1"、"Chapter 2"等英文格式
- **THEN** 系统正确识别并按英文章节标记切分

### Requirement: LLM 辅助章节切分
当规则匹配无法识别章节边界时，系统 SHALL 调用 LLM 分析文本结构，识别章节分隔点。

#### Scenario: 规则匹配失败后 LLM 兜底
- **WHEN** 小说文本没有明确的章节标记且规则匹配未产生结果
- **THEN** 系统调用 LLM 分析文本，返回建议的章节切分点

### Requirement: 章节切分结果可编辑
系统 SHALL 允许用户在自动切分后手动调整章节边界（合并、拆分、重命名章节）。

#### Scenario: 手动合并两个章节
- **WHEN** 用户选择两个相邻章节并执行合并操作
- **THEN** 系统将两个章节内容合并为一个章节，后续章节编号自动调整

#### Scenario: 手动拆分一个章节
- **WHEN** 用户在某个位置标记拆分点
- **THEN** 系统在该位置将章节拆分为两个独立章节

### Requirement: 章节数据持久化
系统 SHALL 将切分结果持久化存储，包括每个章节的标题、内容、起止位置、序号。

#### Scenario: 切分结果保存后可恢复
- **WHEN** 章节切分完成并保存
- **THEN** 重新打开该小说时，切分结果完整保留

### Requirement: 多层正则模式匹配
系统 SHALL 按优先级顺序尝试 6 组正则模式，第一组匹配数 >= 3 即停止。

#### Scenario: Group A — 中文数字章节号（"第一章"、"第二十三回"）
- **WHEN** 文本匹配 `^第[一二三四五六七八九十百千万零〇]+[章节回集卷部篇][\s：:·]?.*$`
- **THEN** 按此模式切分章节

#### Scenario: Group B — 阿拉伯数字章节号（"第1章"、"第 23 回"）
- **WHEN** 文本匹配 `^第\s*\d+\s*[章节回集卷部篇][\s：:·]?.*$`
- **THEN** 按此模式切分章节

#### Scenario: Group C — 纯数字序号（"1. 初入江湖"、"23、第二天"）
- **WHEN** 文本匹配 `^\d{1,4}[\.、\s]\s*\S+`
- **THEN** 按此模式切分章节

#### Scenario: Group D — 英文章节标记（"Chapter 1"、"Part 2"）
- **WHEN** 文本匹配 `^(?:Chapter|CHAPTER|Part|PART|Book|BOOK|Vol(?:ume)?\.?)\s+\d+[\s：:.\-]?.*$`
- **THEN** 按此模式切分章节

#### Scenario: Group E — 特殊分隔符（"【卷一】"、"===正文==="）
- **WHEN** 文本匹配 `^(?:【.+】|〔.+〕|■.*|★.*|={3,}.*|-{3,}.*)$`
- **THEN** 按此模式切分章节

#### Scenario: Group F — 括号序号（"（一）"、"(1)"）
- **WHEN** 文本匹配 `^(?:（[一二三四五六七八九十]+）|（\d+）|\([一二三四五六七八九十]+\)|\(\d+\)).*$`
- **THEN** 按此模式切分章节

### Requirement: 用户自定义正则切分规则
系统 SHALL 支持用户配置自定义章节切分正则规则（pattern、name、priority、enabled），用于补充内置规则无法覆盖的文本格式。

#### Scenario: 新增自定义规则
- **WHEN** 用户新增规则 `^第\\s*\\d+\\s*章.*$`，priority=10，enabled=true
- **THEN** 系统保存规则并在下次切分时参与匹配

#### Scenario: 正则语法非法
- **WHEN** 用户提交无法编译的正则表达式
- **THEN** 系统拒绝保存并返回 REGEX_INVALID 及错误位置

### Requirement: 自定义正则安全执行
系统 SHALL 对自定义正则执行安全约束（pattern 长度、执行超时、样本规模限制），防止灾难性回溯导致切分线程阻塞。

#### Scenario: 正则复杂度超限
- **WHEN** 用户提交超出系统安全阈值的 pattern（如长度超限或复杂度检测失败）
- **THEN** 系统拒绝保存并返回 REGEX_INVALID，提示用户简化规则

#### Scenario: 规则预览执行超时
- **WHEN** 某条自定义规则在预览测试中触发超时保护
- **THEN** 系统中止本次预览并返回 REGEX_TIMEOUT，不写入切分结果

### Requirement: 规则执行优先级
系统 SHALL 先按 priority 执行用户启用的自定义规则，再执行内置 6 组规则；同优先级按创建顺序稳定执行。

#### Scenario: 自定义规则优先命中
- **WHEN** 文本同时命中用户规则和内置 Group B
- **THEN** 系统优先采用命中的用户规则进行切分

### Requirement: 规则预览与测试
系统 SHALL 支持对自定义规则进行预览测试，返回预计切分点、章节数量和命中行示例，供用户确认后执行正式切分。

#### Scenario: 预览测试规则
- **WHEN** 用户在切分设置中点击“测试规则”
- **THEN** 系统返回匹配命中列表与预估章节数，不直接改写已保存切分结果

### Requirement: 预览结果一致性防漂移
系统 SHALL 为每次切分预览返回 `preview_token`（绑定 source_revision + rules_version + boundary_hash）。用户确认切分时必须携带该 token；若源文本或规则已变化则拒绝确认。

#### Scenario: 预览后规则被修改
- **WHEN** 用户完成预览后，其他操作修改了切分规则，再提交原预览结果确认
- **THEN** 系统拒绝确认并返回 PREVIEW_STALE，要求重新预览

#### Scenario: 预览后文本基线变化
- **WHEN** 预览完成后 Split 输入文本基线发生变化（如重新导入或手动重切分）
- **THEN** 系统拒绝确认并返回 PREVIEW_STALE，避免把旧边界写入新文本

### Requirement: 切分结果验证
系统 SHALL 在正则匹配后执行验证，任一条件失败则触发 LLM 兜底。

#### Scenario: 匹配数不足
- **WHEN** 正则匹配到的章节数 < 3
- **THEN** 判定为误匹配，触发 LLM 兜底

#### Scenario: 章节长度异常
- **WHEN** 任一章节字数 < 100 或 > 100,000
- **THEN** 标记该章节为疑似异常，提示用户检查

#### Scenario: 单章占比过大
- **WHEN** 任一章节字数超过全书的 50%
- **THEN** 判定为误切分，触发 LLM 兜底或提示用户手动调整

### Requirement: LLM 兜底切分策略
系统 SHALL 在正则验证失败时采用 LLM 兜底。

#### Scenario: LLM 识别模式（长文本）
- **WHEN** 小说总字数 >= 50,000 且正则验证失败
- **THEN** 发送前 5000 字 + 末尾 2000 字给 LLM，识别章节命名模式，再用该模式程序化切分全文

#### Scenario: LLM 直接切分（短文本）
- **WHEN** 小说总字数 < 50,000 且正则验证失败
- **THEN** 将全文发送给 LLM 直接标注章节分隔点

### Requirement: 切分结果用户确认
系统 SHALL 在自动切分完成后展示预览，用户确认后 Split Stage 才标记为 completed。

#### Scenario: 用户确认切分
- **WHEN** 自动切分完成
- **THEN** 展示切分预览（章节数、各章标题、字数），用户点击"确认"并提交有效 `preview_token` 后 Split Stage → completed
