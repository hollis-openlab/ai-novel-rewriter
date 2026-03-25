## 1. Contracts, Flags, and Compatibility Baseline

- [x] 1.1 定义窗口模式特性开关（`rewrite.window_mode.enabled/guardrail_enabled/audit_enabled`）及默认值
- [x] 1.2 扩展核心模型：`SentenceSpan`、`RewriteWindow`、`WindowAttempt`、窗口聚合统计、章节 warnings 状态结构
- [x] 1.3 扩展 artifact 读写：`mark_plan.json`、`ch_*_rewrites.json`、`rewrites.json` 新字段序列化
- [x] 1.4 实现新旧 artifact 兼容读取优先级（window -> char_offset_range -> paragraph_range）
- [x] 1.5 为兼容读取路径补充单测，确认历史数据不因新字段缺失而失败
- [x] 1.6 在 mark 产物写入 `sentence_splitter_version/window_planner_version/plan_version/source_fingerprint`

## 2. Sentence Index and Window Planner (Mark Stage)

- [x] 2.1 在 `marking.py` 实现句子切分器（中文终止符优先 + 回退规则）
- [x] 2.2 输出句子索引（句序号、start/end offset、段落归属、boundary kind）
- [x] 2.3 将 analyze/mark 命中映射为句子命中集合
- [x] 2.4 实现窗口聚合策略（命中句合并、上下文句扩展、预算裁剪）
- [x] 2.5 实现超预算窗口按句边界拆分，并确保窗口不重叠
- [x] 2.6 生成窗口身份键（`plan_version + window_id + start/end + source_fingerprint`）
- [x] 2.7 将窗口字段写入 mark artifact，同时保留旧 `paragraph_range` 字段
- [x] 2.8 为窗口规划增加确定性测试（同输入多次运行结果一致）
- [x] 2.9 为规则版本升级场景增加测试（版本变化后窗口不可直接复用）

## 3. Scoped Rewrite Execution (Rewrite Stage)

- [x] 3.1 改写请求协议升级为“窗口正文可写 + 前后上下文只读”
- [x] 3.2 实现窗口级执行器（按窗口串行/受控并发提交）
- [x] 3.3 实现截断信号识别（如 `finish_reason=length`）并接入判定逻辑
- [x] 3.4 实现窗口 guardrail 判定（空输出、半句起笔、异常收尾、严重长度离群、越界）
- [x] 3.5 实现硬失败重试（有限次数）与软告警直通策略
- [x] 3.6 实现重试耗尽回退原窗口文本（`rollback_original`）
- [x] 3.7 实现基于窗口身份键的重跑跳过判定（完全匹配才跳过）
- [x] 3.8 为零窗口章节实现 no-op 完成路径（`completion_kind=noop`、`reason_code=NO_REWRITE_WINDOW`、不调用模型）
- [x] 3.9 确保章节重跑/全局补跑仅执行缺失或失败窗口，跳过已完成窗口

## 4. Offset-Safe Assemble and Invariance Guarantees

- [x] 4.1 在 `assemble_pipeline.py` 实现基于 offset 的窗口替换拼接
- [x] 4.2 加入窗口不重叠、按序、越界校验，违规即阻断章节组装
- [x] 4.3 增加“窗口外逐字符不变”断言与失败处理策略
- [x] 4.4 保持标题补齐与现有章节组装语义兼容
- [x] 4.5 为旧数据兼容路径（无窗口字段）保留可运行逻辑

## 5. API Surface and Frontend Explainability

- [x] 5.1 扩展章节改写详情 API，返回窗口级范围、guardrail 与最终动作
- [x] 5.2 扩展阶段运行详情 API，返回窗口聚合统计（total/retried/hard_failed/rollback）
- [x] 5.3 前端改写视图新增窗口解释展示（命中/替换/保留区间）
- [x] 5.4 前端展示窗口级 guardrail 告警与回退原因
- [x] 5.5 前端为缺失窗口字段场景提供降级渲染（旧 segment 视图）
- [x] 5.6 API 增加标准化章节告警字段（`rewrite_status/completion_kind/has_warnings/warning_count/warning_codes`）
- [x] 5.7 前端状态渲染仅依赖后端状态字段，移除本地推断逻辑

## 6. Auditability and Safety

- [x] 6.1 记录窗口级 attempt 审计（attempt_seq、finish_reason、guardrail codes、action）
- [x] 6.2 建立章节级与阶段级窗口质量聚合逻辑
- [x] 6.3 对外审计输出做敏感信息最小暴露（禁止密钥/凭证明文）
- [x] 6.4 增加问题定位字段：可回溯到 `window_id`、`segment_id`、`run_seq`
- [x] 6.5 建立 warnings 聚合统计口径并与章节状态字段保持一致

## 7. Testing and Regression Coverage

- [x] 7.1 后端单测：句子切分、窗口聚合、窗口拆分、确定性
- [x] 7.2 后端单测：guardrail 硬失败/软告警、重试成功、重试耗尽回退
- [x] 7.3 后端单测：offset 替换不变性（窗口外逐字符一致）
- [x] 7.4 后端接口测：章节详情窗口字段、阶段统计字段、兼容读取路径
- [x] 7.5 前端测试：窗口解释展示、告警展示、降级兼容行为
- [x] 7.6 端到端回归：全局串行执行、暂停/继续、章节重跑与补跑语义
- [x] 7.7 后端单测：零窗口章节 no-op 完成与 `NO_REWRITE_WINDOW` 原因码
- [x] 7.8 后端单测：窗口身份键变更后必须重跑，不得误跳过

## 8. Rollout and Rollback Plan

- [x] 8.1 增加灰度配置（novel/task 维度）并默认关闭窗口模式
- [x] 8.2 小流量灰度观察指标：平均窗口大小、回退率、硬失败率、重试率
- [x] 8.3 设定发布门槛与告警阈值，达标后逐步放量
- [x] 8.4 验证一键回滚（关闭 `rewrite.window_mode.enabled`）可恢复旧执行路径
- [x] 8.5 输出上线后运维手册与故障排查手册（窗口审计定位流程）
