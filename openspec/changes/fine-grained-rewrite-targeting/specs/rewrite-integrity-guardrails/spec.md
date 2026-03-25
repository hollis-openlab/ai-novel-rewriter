## ADDED Requirements

### Requirement: 系统 MUST 对窗口改写执行硬失败校验
系统 MUST 在窗口级改写后执行硬失败校验，至少覆盖空输出、窗口越界、半句起笔、异常收尾和严重长度离群。

#### Scenario: 半句起笔触发硬失败
- **WHEN** 改写文本以明显续写片段开头且缺失自然句首
- **THEN** 系统必须标记 `REWRITE_START_FRAGMENT_BROKEN`
- **THEN** 该窗口结果不得直接进入组装

#### Scenario: 空输出触发硬失败
- **WHEN** 改写文本为空或仅空白
- **THEN** 系统必须标记 `REWRITE_EMPTY`
- **THEN** 系统必须进入重试或回退流程

### Requirement: 系统 SHALL 对轻度异常输出软告警而非阻断
系统 SHALL 将轻度长度偏离、轻度风格风险等问题标记为软告警，并允许窗口继续通过，但必须写入告警详情。

#### Scenario: 轻度长度偏离记为 warning
- **WHEN** 改写结果超出目标区间但未达到严重离群阈值
- **THEN** 系统应记录 warning 级别 guardrail
- **THEN** 窗口结果可继续进入组装

### Requirement: 系统 MUST 对硬失败触发有限重试并保留尝试链路
系统 MUST 对硬失败窗口执行有限次数重试；每次尝试都必须记录 attempt 序号、失败码、finish_reason 与结果动作。

#### Scenario: 重试后成功
- **WHEN** 首次尝试硬失败且后续尝试通过
- **THEN** 系统必须采用最后一次通过的改写文本
- **THEN** 审计中必须保留失败尝试与成功尝试记录

### Requirement: 系统 MUST 在重试耗尽时回退原窗口文本并显式告警
系统 MUST 在窗口重试次数耗尽仍未通过硬失败校验时回退该窗口为原文，并将章节标记为 completed-with-warnings 语义。

#### Scenario: 回退原文
- **WHEN** 窗口所有尝试均硬失败
- **THEN** 系统必须将该窗口动作标记为 `rollback_original`
- **THEN** 阶段统计中必须增加回退计数

### Requirement: 系统 MUST 将截断风险纳入 guardrail 判定
系统 MUST 结合 provider 返回的截断信号（如 `finish_reason=length`）与输出完整性共同判定是否触发重试。

#### Scenario: 截断信号导致重试
- **WHEN** provider 返回 `finish_reason=length` 且边界完整性不通过
- **THEN** 系统必须将该尝试视为硬失败
- **THEN** 系统必须执行下一次重试而非直接通过
