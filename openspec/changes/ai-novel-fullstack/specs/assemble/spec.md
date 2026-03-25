## ADDED Requirements

### Requirement: 改写结果合并
系统 SHALL 将 Rewrite Stage 的改写结果合并回原文，生成完整的改写后小说。对于有改写的段落，使用改写文本替换原文；对于无改写的段落，保留原文不变。

#### Scenario: 合并含改写的章节
- **WHEN** 某章节有 3 个段落被改写，其余段落未改写
- **THEN** 系统将 3 个改写段落替换到原文对应位置，其余段落保持原文，输出完整章节

#### Scenario: 合并无改写的章节
- **WHEN** 某章节没有任何改写标记
- **THEN** 系统直接使用原文作为该章节的输出

#### Scenario: 处理被拒绝的改写
- **WHEN** 某段落的改写结果被用户标记为 "rejected"
- **THEN** 系统使用原文而非改写文本

#### Scenario: 回填人工微调后的改写
- **WHEN** 某段落状态为 "accepted_edited"
- **THEN** 系统优先使用人工微调后的 rewritten_text 回填原文位置

#### Scenario: 处理失败的改写
- **WHEN** 某段落的改写状态为 "failed"
- **THEN** 系统使用原文，并在组装日志中记录该段落未改写

### Requirement: 保持章节分隔格式
系统 SHALL 在组装时保持原始小说的章节分隔格式（如空行数、分隔符样式）。

#### Scenario: 保持原始分隔
- **WHEN** 原文章节间以两个空行分隔
- **THEN** 组装后的小说保持相同的两个空行分隔

### Requirement: EPUB 结构还原
当原文为 EPUB 格式导入时，系统 SHALL 将改写后的文本重新嵌入原始 EPUB 结构（manifest、spine、CSS）。

#### Scenario: EPUB 结构保留
- **WHEN** 小说从 EPUB 导入且 Import 阶段保存了 epub_structure.json
- **THEN** Assemble 阶段将改写后文本回填到原始 EPUB 结构中，保留目录、样式和封面

#### Scenario: TXT 导入生成 EPUB
- **WHEN** 小说从 TXT 导入，用户请求 EPUB 导出
- **THEN** 系统生成最简 EPUB 结构：一章一个 HTML 文件，自动生成目录

### Requirement: 组装统计
系统 SHALL 在组装完成后生成统计信息：总字数变化、改写段落数、保留段落数、失败段落数。

#### Scenario: 查看组装统计
- **WHEN** Assemble Stage 完成
- **THEN** status.json 中包含 original_chars、final_chars、rewritten_segments、preserved_segments、failed_segments 字段

### Requirement: 组装前完整性预检
系统 SHALL 在 Assemble 开始前执行完整性预检，至少校验：chapter_index 连续性、segment_id 可映射性、paragraph_range 合法性（不越界/不重叠）。

#### Scenario: 发现非法 segment
- **WHEN** 预检发现某个 segment paragraph_range 越界或无法映射到 RewritePlan
- **THEN** 系统跳过该 segment 的改写回填并使用原文，同时在组装日志记录 warning

#### Scenario: 缺失某章 rewrite artifact
- **WHEN** 某章节不存在 rewrite artifact 文件
- **THEN** 该章节整章使用 Split 阶段原文参与组装，不中断全书输出

### Requirement: 章节覆盖完整性保障
系统 SHALL 保证 Assemble 输出覆盖 Split 阶段的全部章节且每章仅出现一次，不因部分改写失败导致章节丢失。

#### Scenario: 部分章节未改写
- **WHEN** 多个章节没有改写标记或改写全部失败
- **THEN** 这些章节仍以原文完整进入最终输出，章节顺序与 Split 结果一致

### Requirement: 组装质量闸门
系统 SHALL 在导出前执行质量闸门校验（如锚点失败率、segment 失败率、warning 总量）。超过阈值时默认阻断最终导出并提示用户处理。

#### Scenario: 失败率超阈值阻断导出
- **WHEN** 本次任务 rewrite failed_segments 占比超过配置阈值
- **THEN** 系统将 Assemble 标记为 failed 并返回 QUALITY_GATE_BLOCKED，提示用户修复后重试

#### Scenario: 用户强制导出
- **WHEN** 用户在质量闸门告警后选择“强制导出”
- **THEN** 系统允许导出，但在导出报告中附带完整 warning 与失败明细，并写入本次强制导出的风险签名元数据

### Requirement: 强制导出风险签名
系统 SHALL 在强制导出时生成可追溯风险签名（包含 task_id、stage_run_id、触发原因、阈值对比、时间戳），并写入导出元数据与 sidecar 报告。

#### Scenario: 强制导出生成风险签名
- **WHEN** 导出请求带有 force=true 且质量闸门未通过
- **THEN** 系统生成 `risk_signature`，写入 quality_report，并在导出清单中标记该文件为“风险导出”
