## ADDED Requirements

### Requirement: Rewrite 请求 MUST 明确区分可改写正文与只读上下文
系统 MUST 在向模型发起窗口改写请求时将窗口正文作为唯一可改写区域，并将前后上下文标记为只读参考。

#### Scenario: 请求载荷包含三段角色
- **WHEN** 系统构建窗口改写请求
- **THEN** 请求中必须包含 `window_text`、`preceding_context`、`following_context`
- **THEN** 请求协议必须明确上下文不允许被改写

### Requirement: 系统 MUST 仅在窗口 offset 区间应用改写结果
系统 MUST 在应用改写结果时严格基于窗口 `start_offset/end_offset` 替换，窗口外文本不得因改写流程发生变化。

#### Scenario: 单窗口替换保持窗口外不变
- **WHEN** 章节仅执行一个窗口改写
- **THEN** 组装结果在窗口外区间必须与原文逐字符一致
- **THEN** 仅窗口覆盖区间允许出现文本差异

### Requirement: 系统 MUST 拒绝重叠或越界窗口的应用
系统 MUST 在窗口替换前校验窗口集合不重叠、不越界、按 offset 有序；任一校验失败必须阻断应用。

#### Scenario: 出现重叠窗口
- **WHEN** 两个待应用窗口区间相交
- **THEN** 系统必须拒绝该章节的窗口应用
- **THEN** 系统必须返回可诊断错误并写入审计

#### Scenario: 出现越界窗口
- **WHEN** 任一窗口 `end_offset` 超出章节长度
- **THEN** 系统必须阻断应用并标记为错误
- **THEN** 章节结果不得写入不可恢复的脏数据

### Requirement: 系统 MUST 以窗口身份键判定覆盖并跳过重复执行
系统 MUST 在章节级重跑或全局补跑时基于窗口身份键判定覆盖关系；窗口身份键至少包含 `plan_version`、`window_id`、`start_offset`、`end_offset`、`source_fingerprint`，仅当完全匹配时才允许跳过模型调用。

#### Scenario: 中途重跑仅补缺窗口
- **WHEN** 章节已有部分窗口结果且其余窗口缺失
- **THEN** 系统必须仅执行缺失窗口
- **THEN** 已完成窗口不得重复调用模型

#### Scenario: 窗口计划变更后不得误跳过
- **WHEN** 章节重跑时 `plan_version` 或任一窗口身份键字段发生变化
- **THEN** 系统必须将对应窗口视为待执行
- **THEN** 系统不得沿用旧窗口结果直接标记完成

### Requirement: 系统 MUST 对零窗口章节执行显式 no-op 完成
系统 MUST 在 mark 结果无可执行窗口时，将章节改写标记为 no-op 完成并采用原文，不得触发模型调用。

#### Scenario: 无窗口章节自动采用原文
- **WHEN** 当前章节 `rewrite_windows` 数量为 0
- **THEN** 系统必须返回 `rewrite_status=completed` 且 `completion_kind=noop`
- **THEN** 章节文本源必须标记为 `original` 并附带 `reason_code=NO_REWRITE_WINDOW`

#### Scenario: 全局串行时跳过无窗口章节
- **WHEN** 全局改写队列遇到无窗口章节
- **THEN** 系统必须在不调用模型的前提下完成该章节
- **THEN** 队列必须继续执行后续章节而不阻塞

### Requirement: 系统 MUST 兼容无窗口字段的历史数据
系统 MUST 在历史 artifact 不含窗口字段时回退到 `char_offset_range`/`paragraph_range` 兼容路径，保证流程可执行。

#### Scenario: 读取旧版 rewrite 数据
- **WHEN** 章节改写结果仅包含旧字段
- **THEN** 系统必须启用兼容应用逻辑完成组装
- **THEN** 前端应获得明确的“兼容模式”标识而非失败
