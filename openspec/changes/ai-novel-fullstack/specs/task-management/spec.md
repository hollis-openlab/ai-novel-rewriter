## ADDED Requirements

### Requirement: 创建处理任务
系统 SHALL 为每本导入的小说创建一个处理任务，任务包含 6 个 Stage（Import/Split/Analyze/Mark/Rewrite/Assemble）的独立状态。

#### Scenario: 导入小说后自动创建任务
- **WHEN** 小说成功导入
- **THEN** 系统自动创建一个处理任务，Import Stage 标记为 completed，其余 Stage 为 pending

### Requirement: 任务与小说的关系
系统 SHALL 维护一本小说对应一个活跃任务（Task）的约束。一本小说可以有多个历史任务（已完成或已归档），但同一时间只能有一个活跃任务。

#### Scenario: 同一小说创建新任务
- **WHEN** 用户对一本已有已完成任务的小说创建新任务
- **THEN** 旧任务自动归档，新任务成为活跃任务，可以选择从头开始或基于旧任务的 Artifact 继续

### Requirement: 历史任务 Artifact 隔离
系统 SHALL 按 task 维度隔离 Stage Artifact，确保新任务执行不会覆盖历史任务产物。

#### Scenario: 新任务不覆盖历史结果
- **WHEN** 同一本小说已有一个归档任务，用户创建并运行新任务
- **THEN** 新任务写入新的 task Artifact 目录，归档任务的 Stage Artifact 保持可读可导出

### Requirement: 任务进度追踪
系统 SHALL 实时追踪任务在各 pipeline 阶段的进度，包括：当前阶段、已完成章节数/总章节数、预计剩余时间。

#### Scenario: 查看任务进度
- **WHEN** 用户在任务列表中查看某个运行中的任务
- **THEN** 展示当前处理阶段、各阶段的完成百分比、已处理章节数、预计完成时间

#### Scenario: WebSocket 实时推送进度
- **WHEN** 任务正在执行
- **THEN** 系统通过 WebSocket 实时推送进度更新，前端无需轮询

#### Scenario: 完整 WebSocket 消息类型
- **WHEN** 系统推送 WebSocket 事件
- **THEN** 支持消息类型：stage_progress, chapter_completed, stage_completed, stage_failed, chapter_failed, task_paused, task_resumed, stage_stale, worker_pool_status

### Requirement: 任务暂停与恢复
系统 SHALL 允许用户暂停正在执行的任务，暂停后可恢复执行。

#### Scenario: 暂停任务
- **WHEN** 用户对一个运行中的任务点击"暂停"
- **THEN** 系统等待当前正在处理的 LLM 调用完成后暂停，不发起新的 LLM 调用

#### Scenario: 恢复任务
- **WHEN** 用户对一个暂停的任务点击"恢复"
- **THEN** 系统从暂停点继续执行，不重复已完成的工作

### Requirement: Stage 级别重试
系统 SHALL 允许用户重试失败的 Stage 或重跑已完成的 Stage，重跑后下游 Stage 的 Artifact 标记为 stale。

#### Scenario: 重试失败的 Stage
- **WHEN** 用户对失败的 Analyze Stage 点击"重试"
- **THEN** 系统使用 Split Stage 的 Artifact 作为输入，重新执行 Analyze Stage

#### Scenario: 重跑已完成的 Stage
- **WHEN** 用户对已完成的 Analyze Stage 点击"重新执行"
- **THEN** 系统重新执行 Analyze Stage，覆盖旧 Artifact，并将 Mark/Rewrite/Assemble Stage 标记为 stale

#### Scenario: Stale Stage 提示
- **WHEN** 用户查看一个被标记为 stale 的 Stage
- **THEN** 界面提示"上游数据已更新，当前结果可能过期"，提供"刷新"按钮重新执行

#### Scenario: 从头重试
- **WHEN** 用户对任务选择"重新开始"
- **THEN** 系统清除所有 Stage Artifact，从 Split Stage 重新开始（保留原始导入文件）

### Requirement: Stage 手动触发执行
系统 SHALL 允许用户手动触发下一个 Stage 的执行，而非自动连续执行所有 Stage。

#### Scenario: 手动推进 Pipeline
- **WHEN** Analyze Stage 完成后，用户审核分析结果并调整配置
- **THEN** 用户手动点击"开始标记"触发 Mark Stage，系统不自动执行

#### Scenario: 自动连续执行模式
- **WHEN** 用户在任务创建时或设置中开启"自动执行"模式
- **THEN** 每个 Stage 完成后自动开始下一个 Stage，无需手动触发

### Requirement: Stage 运行配置快照
系统 SHALL 在每次 Stage 启动时保存运行配置快照（provider/model/global_prompt 版本、scene_rules/rewrite_rules 版本哈希、关键运行参数），用于结果追溯与重现。

#### Scenario: 启动 Stage 时写入快照
- **WHEN** 用户触发 Analyze Stage
- **THEN** 系统在 stage_run 记录中写入本次配置快照，并在 Artifact status 中可查询

### Requirement: Stage 单飞锁与幂等触发
系统 SHALL 保证同一 task 的同一 stage 在同一时刻最多只有一个 running 实例；重复触发请求通过幂等键复用已有运行。

#### Scenario: 重复点击开始执行
- **WHEN** 用户在 3 秒内连续两次触发同一 Stage 运行
- **THEN** 系统仅启动一次执行，第二次请求返回已有 stage_run 信息

#### Scenario: 自动模式与手动触发竞争
- **WHEN** 自动模式准备触发下游 Stage，同时用户手动点击同一 Stage
- **THEN** 系统通过单飞锁去重，避免并发重复执行

### Requirement: Stage 运行历史保留
系统 SHALL 保留同一 task + stage 的多次运行历史（含 run_seq、状态、配置快照、告警计数、错误信息），并提供“最新运行”与“历史运行”查询能力。

#### Scenario: 同一 Stage 重跑后保留旧记录
- **WHEN** 用户对同一 Stage 多次重跑
- **THEN** 系统为每次执行创建新的 stage_run 记录，旧记录保留为历史，不被覆盖

#### Scenario: 查询 Stage 最新状态
- **WHEN** 前端查询某个 Stage 当前状态
- **THEN** 系统返回该 Stage run_seq 最大的一条记录作为 latest，并可继续展开历史列表

#### Scenario: 读取历史运行 Artifact
- **WHEN** 用户查看某个 Stage 的历史 run（非 latest）
- **THEN** 系统从 `runs/{run_seq}` 快照读取对应 status 与 artifact，不受 latest 结果覆盖影响

### Requirement: 暂停粒度与恢复语义
系统 SHALL 在章节边界暂停任务：当前正在处理的 LLM 调用等待完成并保存结果，然后暂停，不发起新的章节处理。恢复时从下一个未完成的章节继续。

#### Scenario: 暂停时保留在途结果
- **WHEN** 用户暂停任务，此时第 43 章的 LLM 调用正在进行
- **THEN** 系统等待第 43 章完成并保存结果，然后暂停，第 44 章不开始

#### Scenario: 恢复跳过已完成章节
- **WHEN** 用户恢复一个在第 43 章后暂停的任务
- **THEN** 系统从第 44 章继续处理，第 1-43 章不重复执行

#### Scenario: Stage 状态含暂停
- **WHEN** 任务被暂停
- **THEN** 当前 Stage 状态变为 paused（区别于 pending/running/completed/failed/stale）

### Requirement: 任务列表管理
系统 SHALL 提供任务列表视图，支持按状态筛选（全部/进行中/已完成/失败/暂停）和按时间排序。

#### Scenario: 筛选失败任务
- **WHEN** 用户在任务列表中选择"失败"筛选条件
- **THEN** 仅展示状态为失败的任务
