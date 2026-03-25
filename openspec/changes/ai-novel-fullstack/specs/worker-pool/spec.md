## ADDED Requirements

### Requirement: 可配置的 Worker 数量
系统 SHALL 允许用户配置并行 Worker 数量（1-50），控制 LLM 调用的并发级别。

#### Scenario: 调整 Worker 数量
- **WHEN** 用户将 Worker 数量从 5 调整为 10
- **THEN** 系统动态扩展 Worker Pool，新增的 Worker 开始处理队列中的等待任务

#### Scenario: 减少 Worker 数量
- **WHEN** 用户将 Worker 数量从 10 调整为 3
- **THEN** 系统等待多余 Worker 完成当前任务后回收，不中断正在执行的任务

### Requirement: 任务队列管理
系统 SHALL 维护一个任务队列，所有 LLM 调用请求按优先级排队，同优先级内按提交顺序（FIFO）执行。

#### Scenario: 任务排队执行
- **WHEN** 提交的 LLM 调用请求超过当前 Worker 数量
- **THEN** 超出的请求进入队列等待；若优先级相同则按 FIFO 顺序分配给空闲 Worker

### Requirement: Worker 状态监控
系统 SHALL 提供 Worker Pool 的实时状态：活跃 Worker 数、空闲 Worker 数、队列长度、处理速率。

#### Scenario: 查看 Worker Pool 状态
- **WHEN** 用户查看 Worker Pool 监控面板
- **THEN** 展示实时的 Worker 活跃/空闲数量、队列等待任务数、每分钟处理任务数

### Requirement: 失败重试机制
系统 SHALL 对失败的 LLM 调用自动重试，采用指数退避策略（1s, 2s, 4s...），最多重试 3 次。

#### Scenario: LLM 调用失败自动重试
- **WHEN** 某个 LLM 调用因网络错误失败
- **THEN** 系统等待 1 秒后重试，若再次失败等待 2 秒后重试，直到成功或达到最大重试次数

#### Scenario: 达到最大重试次数
- **WHEN** 某个 LLM 调用连续 3 次重试均失败
- **THEN** 该任务标记为失败状态，通知用户，不阻塞其他任务执行
