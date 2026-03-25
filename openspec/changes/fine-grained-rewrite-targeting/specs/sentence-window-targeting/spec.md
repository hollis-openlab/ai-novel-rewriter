## ADDED Requirements

### Requirement: 系统 MUST 生成可复现的句子级索引
系统 MUST 基于章节原文生成句子级索引，并保证在同一输入与同一规则版本下结果可复现。每条句子索引至少包含句序号、起止偏移、段落归属与边界类型。

#### Scenario: 同章重复计算结果一致
- **WHEN** 对同一章节在同一规则版本下重复执行 Mark
- **THEN** 句子总数、每句 `start_offset/end_offset` 必须一致
- **THEN** 句子索引顺序必须稳定且无重排

### Requirement: 句子切分 MUST 支持中文终止符并提供回退规则
系统 MUST 优先基于中文句末标点切分（如 `。！？` 等），并在标点不足时回退到换行/长度安全规则，保证可切分。

#### Scenario: 标点充分时按句末切分
- **WHEN** 段落中存在明确句末标点
- **THEN** 句子边界应优先落在句末标点后
- **THEN** 切分结果不得跨越明显句末边界

#### Scenario: 标点稀缺时触发回退
- **WHEN** 长文本中缺少有效句末标点
- **THEN** 系统必须使用回退规则生成句子索引
- **THEN** 每个回退句子仍须具备有效 offset 区间

### Requirement: 场景命中 MUST 映射为句子命中集合
系统 MUST 将 analyze/mark 的命中结果映射到句子索引，得到明确的命中句集合，且每个命中句必须可追溯到原始场景来源。

#### Scenario: 段落命中映射到句子集合
- **WHEN** 上游仅提供段落范围命中
- **THEN** 系统必须将段落范围覆盖的句子全部纳入命中集合
- **THEN** 命中集合应记录来源信息（自动/手动/规则）

### Requirement: 系统 SHALL 将命中句聚合为预算受控的改写窗口
系统 SHALL 将命中句按距离与预算规则聚合成改写窗口；窗口必须包含命中句范围、上下文句范围、目标字数区间，且窗口区间不得重叠。

#### Scenario: 相邻命中句合并为窗口
- **WHEN** 两组命中句间距不超过合并阈值
- **THEN** 系统应合并为一个窗口
- **THEN** 合并后窗口必须保留完整命中句范围

#### Scenario: 超预算窗口拆分
- **WHEN** 窗口字数超出最大预算
- **THEN** 系统必须按句边界拆分窗口
- **THEN** 拆分后每个窗口都必须满足预算约束与不重叠约束

### Requirement: Mark 产物 MUST 同时写入新窗口字段与旧兼容字段
系统 MUST 在 `mark_plan.json` 中写入窗口级字段，并保留 `paragraph_range` 等旧字段，确保新旧执行链路均可读取。

#### Scenario: 新旧字段并存且可读取
- **WHEN** Mark 计划写入完成
- **THEN** 每个可改写条目必须包含窗口字段与旧兼容字段
- **THEN** 缺失窗口字段的历史计划仍可被系统读取并继续执行

### Requirement: 系统 MUST 持久化分句与窗口规划版本信息
系统 MUST 在 mark 产物中持久化 `sentence_splitter_version`、`window_planner_version`、`plan_version` 与 `source_fingerprint`，以支持可复现与跨 run 比对。

#### Scenario: 同版本重跑可复现
- **WHEN** 章节文本与版本字段保持不变并重跑 Mark
- **THEN** 生成的句子索引与窗口边界必须一致
- **THEN** 产物中的 `plan_version` 与 `source_fingerprint` 必须保持一致

#### Scenario: 规则升级后可识别计划变化
- **WHEN** 句子切分或窗口规划规则版本升级
- **THEN** 新产物必须写入新的版本字段
- **THEN** 后续重跑判定必须将旧版本窗口视为不可直接复用
