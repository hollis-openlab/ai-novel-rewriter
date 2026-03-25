## Context

当前改写链路在“命中识别”和“改写应用”两个关键点都受空行段落粒度约束：
- Mark 以段落为主，空行少时单段过大
- Rewrite 对单段整块替换，容易把局部改写变成大块重写
- Assemble 缺少“窗口外不变性”硬保证，用户难以判断问题在模型还是在拼接

目标不是改原文格式，而是把执行粒度做细、可控、可追溯。

约束条件：
- 必须兼容历史 artifact（无窗口字段）
- 必须保持 `paragraph_range` 与现有锚点字段
- 必须支持单章重跑、全局串行、暂停恢复等现有状态机

## Goals / Non-Goals

**Goals:**
- 将命中单元升级为句子索引，执行单元升级为窗口
- 严格保证“仅替换命中窗口，窗口外逐字符不变”
- 增加可执行 guardrail，阻断半句起笔/明显断裂等低质量结果
- 提供窗口级审计证据，支持前端解释与问题归因
- 兼容旧数据，支持灰度与回滚

**Non-Goals:**
- 不改导入后的原文存储与分章规则
- 不重做全量 diff 引擎，仅增强范围解释
- 不引入新的外部存储系统（继续 SQLite + 文件 artifact）

## Decisions

### 1) 引入窗口级数据契约（Window-First）
决策：在 mark/rewrite artifact 中新增窗口结构，保留 segment 结构做外层兼容。

核心结构（逻辑字段）：
```text
SentenceSpan:
  sentence_index: int
  start_offset: int
  end_offset: int
  paragraph_index: int
  boundary_kind: enum(terminal,newline,fallback)

RewriteWindow:
  window_id: str
  segment_id: str
  chapter_index: int
  start_offset: int
  end_offset: int
  hit_sentence_range: [int,int]
  context_sentence_range: [int,int]
  target_chars: int
  target_chars_min: int
  target_chars_max: int

WindowAttempt:
  window_id: str
  attempt_seq: int
  provider_id: str|None
  model_name: str|None
  finish_reason: str|None
  raw_response_ref: str|None
  guardrail: {level, codes[], details}
  action: enum(accepted,retry,rollback_original)

MarkPlanMeta:
  sentence_splitter_version: str
  window_planner_version: str
  plan_version: str
  source_fingerprint: str

RewriteChapterStatus:
  rewrite_status: enum(completed,failed,running,pending,paused)
  completion_kind: enum(normal,noop)
  has_warnings: bool
  warning_count: int
  warning_codes: str[]
```

备选：
- 继续只有 segment（拒绝）
- 直接抛弃 segment 全面窗口化（暂不采用）
- segment 外壳 + window 内核（采用）

理由：兼容成本最低，能逐步迁移。

### 2) Mark 窗口规划采用“两阶段算法”
决策：
- 阶段 A：场景命中范围映射到句子索引（得到命中句集合）
- 阶段 B：命中句聚合成窗口（句边界优先、预算约束兜底）

窗口规划规则：
- 相邻命中句距离 <= `merge_gap_sentences` 则合并
- 每窗口扩展 `left_context_sentences/right_context_sentences`
- 若 `window_chars > max_window_chars`，按句边界拆分
- 每个窗口写入窗口身份键（`plan_version + window_id + start_offset + end_offset + source_fingerprint`）
- 任意窗口必须满足：
  - `0 <= start_offset < end_offset <= chapter_len`
  - 窗口之间无重叠

备选：固定句数、纯字符切分。均拒绝。

理由：兼顾语义完整与执行可控。

### 3) Rewrite 执行采用“只读上下文 + 可写正文”协议
决策：Prompt 中明确三段角色：
- `window_text`：唯一允许改写
- `preceding_text` / `following_text`：只读上下文

执行后只应用 `[start_offset,end_offset)` 返回值，不允许模型扩写到窗口外。

补充：若 provider 返回结构里存在截断信号（如 `finish_reason=length`）且 guardrail 判定风险高，则进入重试。

补充：若章节 `rewrite_windows == 0`，则该章节进入 no-op 完成路径：
- 不调用模型
- `completion_kind=noop`
- 文本源标记为 `original`
- 原因码为 `NO_REWRITE_WINDOW`

### 4) Assemble 改为 offset 拼接并增加不变性断言
决策：章节组装以“原文切片 + 改写窗口切片”拼接：
1. 排序窗口
2. 校验不重叠/不越界
3. 从 `cursor=0` 逐段拼接：
   - 先拼 `original[cursor:window.start]`
   - 再拼 `window.rewritten_text`
   - 更新 `cursor=window.end`
4. 最后拼 `original[cursor:len]`

断言：
- 所有窗口外区间与原文逐字符一致
- 任一断言失败则章节级失败并回退到安全路径

### 5) Guardrail 采用“硬失败/软告警”矩阵
决策：
- 硬失败（阻断并重试）
  - `REWRITE_EMPTY`
  - `REWRITE_WINDOW_OUT_OF_BOUNDS`
  - `REWRITE_START_FRAGMENT_BROKEN`（半句起笔）
  - `REWRITE_END_FRAGMENT_BROKEN`（异常收尾）
  - `REWRITE_LENGTH_SEVERE_OUTLIER`
- 软告警（可继续）
  - 轻微长度偏离
  - 风格相似度偏高但未超阈值

重试策略：
- 每窗口最多 `max_retry_per_window`
- 达上限后动作为 `rollback_original`
- 章节状态为 completed + warnings，不做静默成功

### 6) 状态机保持现有语义并扩展窗口统计
决策：不新增用户可见阶段状态枚举，仅在 run/chapter 元数据中增加标准化告警字段与窗口维度统计：
- `completion_kind`（`normal/noop`）
- `has_warnings`
- `warning_count`
- `warning_codes`
- `windows_total`
- `windows_retried`
- `windows_rollback`
- `windows_hard_failed`

章节是否完成仍由“可产出文本是否可组装”判定；回退属于完成但告警。前端必须只依据后端字段渲染状态，不做本地推断。

### 7) API 与 Artifact 采取向后兼容扩展
决策：新增字段全部 optional，读取优先级：
1. `rewrite_windows`（新）
2. `char_offset_range`（旧增强）
3. `paragraph_range`（旧）

重跑跳过判定仅在“窗口身份键完全一致”时生效（`plan_version + window_id + start/end + source_fingerprint`）。

新增输出点：
- 章节改写详情：返回窗口范围与 guardrail 结果
- 章节状态详情：返回 `rewrite_status/completion_kind/has_warnings/warning_count/warning_codes`
- 阶段运行详情：返回窗口聚合统计与 warnings 聚合统计

### 8) 灰度开关与回滚
决策：增加特性开关：
- `rewrite.window_mode.enabled`
- `rewrite.window_mode.guardrail_enabled`
- `rewrite.window_mode.audit_enabled`

回滚策略：关闭 `window_mode.enabled` 即回退旧路径；新增字段保留但不消费。

## Risks / Trade-offs

- [句子切分规则在口语化文本中误判] → 保留 fallback 分句与人工阈值调参
- [窗口拆分过细导致调用次数上涨] → 设置最小窗口与合并阈值，结合 worker 限速
- [guardrail 过严造成回退率升高] → 先宽后严灰度，基于审计指标迭代阈值
- [新字段增加前后端耦合] → 字段 optional + 前端降级渲染，避免强依赖
- [运行中途切换开关导致语义混杂] → 开关按 stage run 快照冻结，不在单次 run 内动态变更

## Migration Plan

1. 数据契约层
- 扩展 Pydantic 模型与 artifact 读写器
- 增加新旧格式互读测试

2. Mark 阶段
- 实现句子索引
- 实现窗口规划与拆分
- 写入 mark artifact 新字段

3. Rewrite 阶段
- 实现窗口执行协议
- 接入 guardrail 与重试回退
- 记录窗口 attempt 审计

4. Assemble 阶段
- 切换到 offset 拼接
- 添加窗口外不变性断言

5. API / 前端
- 输出窗口解释字段与统计字段
- 前端展示窗口范围、告警、回退原因

6. 灰度与发布
- 按 novel_id 白名单灰度
- 监控窗口回退率、硬失败率、平均窗口大小
- 指标稳定后全量开启

回滚：
- 关闭 `rewrite.window_mode.enabled`
- 保留审计数据，不影响历史查询
- 对进行中的 run 不回滚，下一 run 生效

## Open Questions

- 半句起笔规则是否需要按场景类型（对话/叙述）差异化阈值？
- guardrail 失败后的默认重试提示词是否需要 provider 分流配置？
- 前端窗口视图默认展开还是懒加载，如何平衡性能与可解释性？
- 是否需要在导出报告中附加窗口质量摘要（用于人工审校）？
