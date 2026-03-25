## ADDED Requirements

### Requirement: LLM 驱动的内容改写
系统 SHALL 使用配置的 LLM 对标记段落执行改写，改写时参考章节摘要和上下文保持一致性。

#### Scenario: 执行单段落改写
- **WHEN** 用户触发对某个标记段落的改写
- **THEN** 系统构造包含上下文（前后段落 + 章节摘要）的 prompt，调用 LLM 生成改写结果

#### Scenario: 批量执行改写
- **WHEN** 用户触发全书改写
- **THEN** 系统按章节顺序，通过 Worker Pool 并行处理各章节的改写任务

### Requirement: 改写策略支持
系统 SHALL 支持四种改写策略：
1. **扩写**：在保持原意的基础上增加细节描写，扩充内容
2. **改写**：重写段落，调整表达方式和行文风格
3. **精简**：缩减冗余内容，保留核心信息
4. **保留**：跳过改写，保留原文

#### Scenario: 扩写一个战斗场景
- **WHEN** 对一个标记为"扩写"的战斗段落执行改写
- **THEN** LLM 生成的结果比原文更长，增加了动作细节、感官描写等

#### Scenario: 改写一段对话
- **WHEN** 对一个标记为"改写"的对话段落执行改写
- **THEN** LLM 生成的结果保持对话含义不变，但调整了语言风格和表达方式

### Requirement: 改写结果审核
系统 SHALL 允许用户逐条审核改写结果，可接受、拒绝或重新生成。

#### Scenario: 接受改写结果
- **WHEN** 用户审核某段改写后点击"接受"
- **THEN** 改写结果替换原文，状态标记为已确认

#### Scenario: 拒绝改写结果
- **WHEN** 用户审核某段改写后点击"拒绝"
- **THEN** 保留原文不变，该标记状态更新为已拒绝

#### Scenario: 重新生成改写
- **WHEN** 用户对某段改写结果点击"重新生成"
- **THEN** 系统重新调用 LLM 生成新的改写结果

### Requirement: 上下文连贯性保证
系统 SHALL 在改写时传入前后章节的摘要和相邻段落内容，确保改写结果与上下文连贯。

#### Scenario: 改写结果与上下文一致
- **WHEN** 对章节中间的某段落执行改写
- **THEN** LLM prompt 中包含前一章摘要、当前章摘要、前后段落原文，确保生成内容不出现逻辑断裂

### Requirement: 改写输出格式
系统 SHALL 要求 LLM 输出纯改写文本（不含解释、标注或 JSON 包装）。系统在收到 LLM 输出后，自动将其与原始段落配对，生成结构化的 RewriteResult Artifact（包含 segment_id、original_text、rewritten_text、字数统计、状态）。

#### Scenario: LLM 输出纯文本
- **WHEN** LLM 完成一个段落的改写
- **THEN** LLM 输出纯改写文本，系统将其包装为 RewriteResult JSON 结构，与 RewritePlan 中的 segment_id 关联

### Requirement: 改写相似度检测
系统 SHALL 检测 LLM 输出与原文的相似度。若相似度超过 90%（基于字符级编辑距离），判定为"复制原文"并触发重试。

#### Scenario: 检测到复制原文
- **WHEN** LLM 输出与原文的字符级相似度超过 90%
- **THEN** 校验失败，触发重试，prompt 中追加"请进行实质性改写，不要复制原文"

### Requirement: preserve 策略跳过改写
系统 SHALL 对 strategy=preserve 的 segments 跳过 LLM 调用，不写入 RewriteResult，由 Assemble 阶段直接使用原文。

#### Scenario: preserve 段落跳过
- **WHEN** segment strategy=preserve
- **THEN** Rewrite Stage 跳过，RewriteResult 无该条目

### Requirement: 改写状态转换规则
系统 SHALL 按以下规则管理 segment status：pending→completed（LLM 成功）、pending→failed（重试耗尽）、completed→accepted（用户确认）、completed→rejected（用户拒绝）、rejected→pending（重新生成）、failed→pending（重试）、accepted→accepted_edited（人工微调并保存）。

#### Scenario: accepted 后进行人工微调
- **WHEN** 用户已接受某段改写，并在编辑框中手动修改部分文本后保存
- **THEN** 系统将该段状态更新为 accepted_edited，保留原始改写版本和人工编辑审计记录

#### Scenario: 人工微调后重新生成
- **WHEN** 用户对 accepted_edited 段落点击“重新生成”
- **THEN** 系统将状态回到 pending 并重新调用 LLM，历史人工编辑版本保留可追溯

### Requirement: 锚点一致性校验
系统 SHALL 在 Rewrite 执行与 Assemble 前校验每个 segment 的锚点一致性，至少校验 chapter_index、paragraph_range、paragraph_start_hash、paragraph_end_hash、range_text_hash、context_window_hash、paragraph_count_snapshot 是否与 Split 阶段段落基线一致。锚点不一致时不得写入改写结果。

#### Scenario: 段落范围越界
- **WHEN** segment 的 paragraph_range 超出该章节段落总数
- **THEN** 该 segment 标记为 failed，错误码为 ANCHOR_MISMATCH，并回退使用原文

#### Scenario: 段落基线不一致
- **WHEN** segment 的锚点校验发现与 Split 阶段段落基线不一致（如段落哈希不匹配）
- **THEN** 该 segment 标记为 failed，记录锚点校验失败详情，Assemble 阶段对该段落使用原文
