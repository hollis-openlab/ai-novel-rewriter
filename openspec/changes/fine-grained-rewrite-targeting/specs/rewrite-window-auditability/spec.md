## ADDED Requirements

### Requirement: 系统 MUST 写入窗口级执行审计记录
系统 MUST 为每个窗口写入结构化审计记录，至少包含窗口范围、命中句范围、attempt 列表、guardrail 结果与最终动作。

#### Scenario: 窗口完成后产生日志
- **WHEN** 窗口执行结束（accepted/retry-success/rollback-original）
- **THEN** 系统必须写入窗口级审计记录
- **THEN** 审计记录必须可关联章节、segment、stage run

### Requirement: 审计记录 MUST 保留重试链路
审计数据 MUST 保留每次尝试的顺序、provider/model 元信息、finish_reason、失败码与动作决策，不得仅保留最终结果。

#### Scenario: 多次重试链路可追踪
- **WHEN** 窗口执行经历多次尝试
- **THEN** 审计记录必须包含完整 attempt 序列
- **THEN** attempt 序列必须可复原“为什么最终通过或回退”

### Requirement: 章节改写 API SHALL 返回窗口级可解释字段
章节改写详情接口 SHALL 返回窗口级命中范围、替换范围、保留区间信息与 guardrail 状态，供前端直接展示。

#### Scenario: 客户端读取章节改写详情
- **WHEN** 客户端请求章节改写结果
- **THEN** 响应中必须包含窗口解释字段
- **THEN** 客户端无需自行推断即可区分模型问题与拼接问题

### Requirement: 阶段运行详情 MUST 提供窗口质量聚合指标
rewrite 阶段运行信息 MUST 提供窗口总数、重试数、硬失败数、回退数等聚合指标，并支持章节维度拆分。

#### Scenario: 查看 rewrite 运行统计
- **WHEN** 用户查询 rewrite 阶段 run 详情
- **THEN** 响应中必须包含窗口质量统计
- **THEN** 统计值必须可追溯到窗口审计明细

### Requirement: 审计输出 MUST 进行敏感信息最小暴露
系统 MUST 在返回审计信息时避免泄露密钥、凭证或不必要的原始请求敏感字段。

#### Scenario: 返回前端的审计数据脱敏
- **WHEN** 系统对外返回窗口审计数据
- **THEN** 输出中不得包含 API key、凭证明文或高敏感请求头
- **THEN** 必要时仅返回可追踪引用而非完整敏感负载

### Requirement: 系统 MUST 输出标准化 warnings 状态字段
系统 MUST 在章节与阶段接口输出标准化 warnings 字段，至少包含 `rewrite_status`、`has_warnings`、`warning_count`、`warning_codes`，并要求前端仅基于这些字段展示告警状态而不自行推断。

#### Scenario: 回退窗口导致章节带告警完成
- **WHEN** 某窗口最终动作是 `rollback_original`
- **THEN** 章节返回必须为 `rewrite_status=completed` 且 `has_warnings=true`
- **THEN** `warning_count` 与 `warning_codes` 必须包含对应 guardrail 结果

#### Scenario: 全章节无告警
- **WHEN** 全部窗口均 `accepted` 且无 warning
- **THEN** 章节返回必须为 `has_warnings=false` 且 `warning_count=0`
- **THEN** 阶段聚合统计中的 warning 计数不得增加
