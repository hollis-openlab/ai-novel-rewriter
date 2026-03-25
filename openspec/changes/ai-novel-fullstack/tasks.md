## 0. 前置基础设施

- [x] 0.1 定义全项目 Python 数据模型：NovelMeta, Chapter, ChapterAnalysis, RewritePlan, RewriteResult, StageStatus 等核心 Pydantic model
- [x] 0.2 定义 REST API 契约：所有 endpoint 的 URL/method/request/response JSON 结构（参考 design.md API 概览）
- [x] 0.3 定义 WebSocket 消息协议：消息类型枚举、payload 结构、订阅/心跳机制
- [x] 0.4 定义统一错误处理框架：错误码枚举（VALIDATION_ERROR, STAGE_FAILED, PROVIDER_ERROR 等）、API 错误响应格式
- [x] 0.5 选型并集成 Prompt 模板引擎（Jinja2）：配置 Jinja2 依赖，定义变量注册表

## 1. 项目初始化与基础设施

- [x] 1.1 使用 uv 初始化 Python 3.13 后端项目，创建 `pyproject.toml`，配置 FastAPI + Uvicorn + SQLAlchemy + Alembic 依赖
- [ ] 1.2 对齐现有 React 19 前端工程结构（Vite + TailwindCSS + Zustand + React Query），补齐与后端联调所需约定
- [x] 1.3 设计 SQLite DDL schema：novels, tasks, stage_runs(run_seq 历史模型 + single-flight/idempotency 索引), chapter_states, providers, configs, chapters 表（含 CHECK/UNIQUE/FK 约束），WAL mode + foreign_keys
- [x] 1.4 实现 SQLite 初始化 + SQLAlchemy AsyncEngine + WAL mode 配置 [blocked by: 1.3]
- [x] 1.5 实现 FastAPI 路由框架 + 中间件（日志、CORS、统一错误处理）
- [x] 1.6 实现 Artifact Store 文件系统管理：data/novels/{id}/tasks/{task_id}/stages/ 目录创建、active_task_id 指针维护、orphan 检测
- [ ] 1.7 补齐前端基础设施（shadcn/ui 组件规范、TanStack Query 缓存策略、类型共享约定）
- [ ] 1.8 搭建前端设计系统：Tailwind config（Apple 色板、Inter 字体、圆角、阴影 token）、全局布局（Sidebar + Main）
- [ ] 1.9 配置前后端联调：Vite API proxy + WebSocket 连接管理（重连、心跳）
- [ ] 1.10 建立本地开发启动约定：后端 `uv run` 启动 + 前端 `npm run dev` 启动 + 基础联调 smoke checklist
- [ ] 1.11 实现后端 serve 前端静态文件（生产模式能力，不作为开发期默认启动方式）
- [x] 1.12 建立前后端契约同步机制：基于 OpenAPI 生成/校验前端 API 类型，避免后端改动后前端失配

## 2. 小说导入模块（novel-import）

- [x] 2.1 实现 TXT 文件上传 API（multipart form），包含文件大小校验（50MB 限制）
- [x] 2.2 实现 TXT 文件解析：UTF-8 检测，GBK/GB2312 自动转码
- [x] 2.3 实现 EPUB 文件解析：解压、按 spine 顺序提取纯文本、错误处理
- [x] 2.4 实现小说元数据记录：文件名、格式、大小、导入时间、总字数 [blocked by: 1.4]
- [ ] 2.5 前端实现文件上传界面：拖拽上传、格式校验、上传进度
- [x] 2.6 实现 Import Stage artifact 写入：raw.txt + novel.meta.json + epub_structure.json(EPUB) 写入 Artifact Store，创建 novels/tasks/stage_runs DB 记录 [blocked by: 1.4, 1.6]

## 3. 章节切分模块（chapter-splitting）

- [x] 3.1 实现多层正则章节切分引擎：6 组有序正则（A: 中文数字 "第一章"; B: 阿拉伯数字 "第1章"; C: 纯数字 "1."; D: 英文 "Chapter 1"; E: 特殊分隔符 "【卷一】"; F: 括号序号 "（一）"），按优先级尝试，首组匹配 >= 3 即停止
- [ ] 3.2 实现 LLM 辅助章节切分：当规则匹配失败时调用 LLM 识别切分点 [blocked by: 4.1]
- [x] 3.3 实现章节数据持久化：标题、内容、起止位置、序号存储到 chapters 表，并写入 Split Artifact [blocked by: 1.4]
- [x] 3.4 实现章节手动调整 API：合并、拆分、重命名
- [ ] 3.5 前端实现章节切分预览和确认界面：展示章节数/标题/字数，支持手动合并/拆分/重命名，用户确认后提交
- [ ] 3.6 实现切分验证 + LLM fallback：匹配数 < 3 触发 LLM 兜底；单章 < 100 字或 > 100,000 字标记异常；单章占比 > 50% 触发重切分 [blocked by: 3.1, 3.2]
- [x] 3.7 实现切分结果用户确认流程：展示预览（章节数/标题/字数），确认后 Split Stage → completed
- [x] 3.8 实现章节切分规则管理 API：内置规则启停 + 自定义 regex 规则 CRUD（name/pattern/priority/enabled），含编译校验
- [x] 3.9 实现章节切分规则预览 API：返回命中样本、预计章节数、切分边界（不覆盖已确认结果）
- [ ] 3.10 前端实现章节切分规则面板：自定义 regex 编辑、优先级排序、规则测试预览
- [x] 3.11 实现自定义 regex 安全执行策略：pattern 长度限制、复杂度预检、执行超时保护（REGEX_TIMEOUT）
- [x] 3.12 实现切分预览一致性校验：preview_token 绑定 source_revision/rules_version，确认时校验 PREVIEW_STALE

## 4. LLM 集成层（llm-integration）

- [x] 4.1 定义 LlmProvider interface（Protocol）和核心数据结构（CompletionRequest、CompletionResponse、GenerationParams）
- [x] 4.2 实现 OpenAI provider（GPT 系列 API 调用，支持 JSON mode）[blocked by: 4.1]
- [x] 4.3 实现 OpenAI 兼容 provider（兼容 base_url，如硅基流动）[blocked by: 4.1]
- [x] 4.4 实现 API Key 加密存储和 provider 配置 CRUD API（按 provider_type+base_url+credential_fingerprint upsert）[blocked by: 1.4]
- [x] 4.5 实现 provider 模型列表获取 API：支持未保存草稿凭证获取和已保存 provider 重新获取 [blocked by: 4.1]
- [x] 4.6 实现 provider 模型列表模糊搜索（`q` 查询，返回按匹配度排序）[blocked by: 4.5]
- [x] 4.7 实现 provider 连接测试和健康检查（基于已选模型）[blocked by: 4.1]
- [x] 4.8 实现每个 provider 的速率限制器（RPM/TPM）[blocked by: 4.1]
- [x] 4.9 实现全局提示词注入引擎：所有 Analyze/Rewrite 调用统一使用 global_prompt
- [x] 4.10 集成 Jinja2 模板引擎：实现 `{{变量}}` 插值 + `{% if %}` 条件渲染 + 变量注册表 [blocked by: 0.5]
- [x] 4.11 实现各 Stage 内置 Task Prompt Template（Split/Analyze/Rewrite） [blocked by: 4.10]
- [x] 4.12 实现 GenerationParams 解析：provider_defaults → runtime_computed_fields → per_call_overrides
- [x] 4.13 实现输出校验器：Analyze JSON schema 校验 + 摘要字数校验；Rewrite 字数范围校验 + 相似度检测（编辑距离 > 90% 判定复制）；Rewrite 相似度使用 rapidfuzz Levenshtein normalized similarity，阈值 0.90
- [x] 4.14 实现智能重试引擎：AdjustTemperature → AppendHint → FallbackProvider 策略链 + 指数退避 [blocked by: 4.13]
- [x] 4.15 实现 Prompt Audit Log：JSONL 格式，每章一个文件，记录完整 prompt/response/params/usage/validation
- [x] 4.16 实现 Token 计数工具：集成 tiktoken（OpenAI 与 OpenAI 兼容接口）[blocked by: 4.1]

## 5. Worker Pool 模块（worker-pool）

- [x] 5.1 实现基于 asyncio.Queue 的 Worker Pool：任务分发、Worker 生命周期管理
- [x] 5.2 实现 Worker 数量动态调整（扩缩容）
- [x] 5.3 实现失败重试机制（指数退避，最多 3 次）
- [x] 5.4 实现 Worker Pool 状态监控 API：活跃/空闲 Worker 数、队列长度、处理速率
- [x] 5.5 实现 per-Provider Token Bucket 速率限制器（RPM + TPM），与 Worker Pool 集成：Worker 获取 permit → 调用 LLM → 释放 permit [blocked by: 4.1]
- [ ] 5.6 前端实现 Worker Pool 监控面板

## 6. 章节深度分析模块（chapter-summary + scene-recognition 合并）

- [x] 6.1 定义 ChapterAnalysis JSON Schema 对应的 Pydantic model + 序列化/反序列化 [blocked by: 0.1]
- [x] 6.2 实现 Analyze Stage prompt 构造：注入 {{output_schema}} 变量为精确 JSON Schema [blocked by: 4.10, 4.11, 4.13]
- [x] 6.3 实现 Analyze Stage 执行引擎：调用 LLM → 校验 JSON → 提取 characters/events/scenes [blocked by: 4.1, 4.13]
- [x] 6.4 实现批量分析：通过 Worker Pool 逐章并行处理 [blocked by: 5.1, 6.3]
- [x] 6.5 实现分析结果持久化：ch_N_analysis.json 写入 Artifact Store + analysis.json 聚合 [blocked by: 1.6]
- [x] 6.6 实现分析结果编辑 API：用户可修改 summary/characters/events，修改后标记下游 Stage 为 stale
- [x] 6.7 实现人物跨章追踪查询 API（某人物在各章的情绪/状态变化轨迹）
- [ ] 6.8 前端实现章节分析结果展示：摘要、人物状态卡片、关键事件时间线、改写建议图层

## 7. 场景类型管理（scene-recognition 配置部分）

- [x] 7.1 实现空白初始化：默认不内置场景类型，用户手动添加 [blocked by: 1.4]
- [x] 7.2 实现场景识别规则配置 API（关键词、权重、启用状态）[blocked by: 7.1]
- [ ] 7.3 前端实现场景颜色标注可视化、人物状态面板、事件时间线、改写潜力标注

## 8. 改写标记模块（rewrite-marking）

- [x] 8.1 实现 RewritePlan 数据结构（segment_id UUID v4 自动生成, paragraph_range 不重叠约束校验, strategy enum, target_ratio 等字段）[blocked by: 0.1]
- [x] 8.2 实现基于规则的自动改写标记引擎：根据 scene_type + rewrite_rules 自动生成标记 [blocked by: 6.3, 7.1]
- [x] 8.3 实现改写规则配置 API（场景类型 → RewriteStrategy + 目标字数比例）
- [x] 8.4 实现手动标记调整 API（添加/移除/修改标记，source 字段标记为 manual）
- [x] 8.5 实现改写预估计算：预计新增字数、LLM 调用次数、耗时
- [ ] 8.6 前端实现改写标记可视化和手动调整界面
- [x] 8.7 在 RewritePlan 中增加锚点元数据（paragraph_start_hash / paragraph_end_hash / range_text_hash / context_window_hash / paragraph_count_snapshot）

## 9. 内容改写模块（content-rewrite）

- [x] 9.1 实现 Rewrite Stage prompt 构造：注入上下文（{{preceding_text}} 300字 + {{following_text}} 300字 + {{chapter_summary}} + {{character_states}} + {{rewrite_mode}}）[blocked by: 4.10, 4.11]
- [x] 9.2 实现 Rewrite Stage 执行引擎：LLM 输出纯文本 → 系统配对原始 segment → 包装为 RewriteResult JSON [blocked by: 4.1, 4.13, 8.1]
- [x] 9.3 实现批量改写：按章节顺序通过 Worker Pool 并行处理 [blocked by: 5.1, 9.2]
- [x] 9.4 实现改写结果审核 API：接受/拒绝/重新生成（状态更新 accepted/rejected/pending）
- [x] 9.5 前端实现改写结果审核界面和原文对照视图（Compare View 分栏 + diff 高亮）
- [x] 9.6 实现 Rewrite 锚点一致性校验：chapter_index + paragraph_range + paragraph hash，不一致时 failed(error_code=ANCHOR_MISMATCH)
- [x] 9.7 实现 Rewrite 失败原因持久化：error_code/error_detail 写入 Rewrite Artifact 与日志
- [x] 9.8 实现 accepted 后人工微调 API：accepted→accepted_edited，保留版本审计与回滚记录
- [x] 9.9 实现锚点多因子一致性校验：start/end/range/context/count 全量校验

## 10. Artifact 导出与最终导出模块（novel-export）

- [x] 10.1 实现 Assemble Stage 核心：遍历章节 → 替换已改写段落 → 保留未改写段落 → 拼接完整小说 [blocked by: 9.2, 3.3]
- [x] 10.2 实现 Assemble 统计：original_chars、final_chars、rewritten/preserved/failed segments
- [x] 10.3 实现 EPUB 结构还原：读取 epub_structure.json → 将改写文本回填到原始 EPUB 结构 [blocked by: 10.1]
- [x] 10.4 实现通用 Stage Artifact 导出 API：按 stage_name + novel_id（默认 active task）读取 Artifact 文件，支持可选 task_id 读取历史任务
- [x] 10.5 实现 Analyze Artifact → Markdown 报告转换器（人物表格、事件列表、场景分布表）
- [x] 10.6 实现 Mark Artifact → Markdown 改写计划转换器
- [x] 10.7 实现 Rewrite Artifact → diff 格式转换器
- [x] 10.8 实现单章 Artifact 导出 API + 批量导出 ZIP
- [x] 10.9 实现 TXT 格式最终导出
- [x] 10.10 实现 EPUB 格式最终导出
- [x] 10.11 实现选择性导出：全书/指定章节范围/仅已改写章节
- [x] 10.12 实现对照格式导出：原文 + 改写双栏对比
- [x] 10.13 前端实现每个 Stage 的导出按钮（格式选择弹窗）和最终导出配置界面
- [x] 10.14 实现 Assemble 预检：chapter_index 连续性、segment_id 可映射性、paragraph_range 合法性（不越界/不重叠）
- [x] 10.15 实现组装降级策略：非法 segment 或缺失 rewrite artifact 时自动回退原文并记录 warning
- [x] 10.16 实现章节覆盖校验：输出覆盖 Split 的全部章节且每章仅一次，失败则阻断导出并提示
- [x] 10.17 实现 Assemble 质量闸门：failed ratio / warning count 阈值校验，超阈值默认阻断导出
- [x] 10.18 实现质量报告输出：quality_report.json（阈值、统计、warning 明细、是否允许强制导出、risk_signature）
- [x] 10.19 实现强制导出风险签名落盘：TXT 头部标记、EPUB metadata、export_manifest 标记

## 11. 任务管理模块（task-management）

- [ ] 11.1 实现 Stage 状态机：6 个 Stage 独立状态（pending/running/completed/failed/paused/stale）+ 状态转换规则 [blocked by: 1.4]
- [ ] 11.2 实现 Stage 依赖检查：仅当前置 Stage completed 时允许执行下一 Stage
- [ ] 11.3 实现 Stale 级联标记：按矩阵（Import→全部 stale; Split→Analyze+后续; Analyze→Mark+后续; Mark→Rewrite+Assemble; Rewrite→Assemble）
- [ ] 11.4 实现 Stage 手动触发执行 API + 自动连续执行模式开关
- [ ] 11.5 实现 Task/Novel 关系管理：一本小说一个活跃 Task（DB partial unique index），旧 Task 归档 [blocked by: 1.4]
- [ ] 11.6 实现任务暂停/恢复：章节边界暂停，在途 LLM 等待完成，恢复跳过已完成章节
- [ ] 11.7 实现 Stage 级进度追踪 API（当前 Stage、各 Stage 完成百分比、逐章进度）
- [ ] 11.8 实现 WebSocket 实时进度推送（stage_progress / chapter_completed / stage_completed / stage_failed / chapter_failed / task_paused / task_resumed / stage_stale / worker_pool_status）[blocked by: 1.9, 0.3]
- [ ] 11.9 实现 Stage 级别重试（单 Stage 或单章重试）和全任务重新开始
- [ ] 11.10 实现任务列表 API：筛选（全部/进行中/已完成/失败/暂停）、排序、分页
- [ ] 11.11 前端实现 Pipeline 进度视图：Stage 卡片链、状态/耗时/导出按钮/stale 提示
- [ ] 11.12 实现任务创建模式 API：`from_scratch`（从 Split 开始）/ `clone_from_task`（复用历史 Artifact）
- [ ] 11.13 实现任务归档 Artifact 保留与历史访问：新任务不覆盖旧任务产物，支持按 task_id 读取历史 Stage Artifact
- [x] 11.14 实现 Stage 运行配置快照：stage_run 启动时写入 provider/model/prompt/rules hash 等快照字段
- [x] 11.15 实现 Stage 单飞锁 + 幂等触发：同 task+stage 只允许一个 running，`run_idempotency_key` 重复请求复用已有 run
- [x] 11.16 实现 Stage 运行历史查询：按 run_seq 返回 latest/detail/history，不覆盖旧 run 记录
- [x] 11.17 实现 Stage Artifact 历史快照目录：`stages/{stage}/runs/{run_seq}/` 与 latest 映射

## 12. 配置管理模块（config-management）

- [x] 12.1 实现全局提示词（global_prompt）CRUD API [blocked by: 0.1]
- [x] 12.2 实现场景识别规则 CRUD API（scene_type/keywords/weight/enabled）[blocked by: 0.1]
- [x] 12.3 实现改写规则 CRUD API（scene_type/strategy/target_ratio/priority/enabled）[blocked by: 0.1]
- [x] 12.4 实现 AI Config Bar 解析 API（POST /api/v1/config/ai-parse）：仅解析 global_prompt/scene_rules/rewrite_rules 变更
- [x] 12.5 实现 AI Config Bar 变更应用 API（POST /api/v1/config/ai-apply）：校验并执行解析结果
- [x] 12.6 实现 AI Config Bar 越界提示：当请求 temperature 等模型参数时返回“请到 provider 配置页调整”
- [x] 12.7 实现配置 JSON 导出 API：导出 global_prompt + scene_rules + rewrite_rules
- [x] 12.8 实现配置 JSON 导入 API：格式校验、冲突检测、预览确认
- [x] 12.9 前端实现 AI Config Bar 组件：输入框 + Diff 预览 + 确认/取消/追问交互
- [x] 12.10 前端实现简化配置页：全局提示词编辑器 + 场景规则编辑器 + 改写规则编辑器
- [x] 12.11 前端实现 JSON 导入导出界面：拖拽导入、下载导出、JSON 原始编辑器（语法校验）
- [x] 12.12 实现三种模式实时同步：AI Config Bar / 可视化编辑器 / JSON 编辑器操作同一份配置

## 13. 前端核心页面（frontend-app）

- [x] 13.1 实现任务仪表盘页面：统计卡片（Bento Grid）、小说列表、最近活动流、Worker 监控
- [x] 13.2 实现小说详情页：元数据卡片、Pipeline 节点链进度条（6 Stage）、Stage 详情展开卡片、章节列表（含筛选）
- [x] 13.3 实现章节编辑器：三栏布局（章节导航 + 文本编辑器 + 分析面板）、场景色标注、改写标记气泡、Compare 分栏视图
- [x] 13.4 实现 LLM 模型配置页面：Provider 卡片、连接测试、速率限制、Stage 分配
- [x] 13.5 实现 Prompt 日志查看器：LLM 调用时间线、展开查看完整 prompt/response、一键复制
- [x] 13.6 实现 WebSocket 实时进度集成：进度条动态更新、状态通知 [blocked by: 11.8]
- [x] 13.7 响应式布局适配（1024px - 2560px）+ Dark Mode 支持
- [x] 13.8 实现 Provider 模型列表交互：点击“获取模型列表”拉取模型，支持列表模糊搜索与选择
- [x] 13.9 实现 Provider 编辑复用流程：同 APIKey + BaseURL 编辑模型时复用原 provider，支持“获取模型列表 → 测试连接 → 保存”
- [x] 13.10 实现改写覆盖率与回退告警面板：展示已改写/保留/失败/回退统计与章节明细
- [x] 13.11 实现质量闸门阻断交互：阈值对比、修复建议、强制导出确认流程
- [x] 13.12 前端 API 适配后端新契约：split-rules、split-rules preview、stage run detail（snapshot/warnings）、quality-report
- [x] 13.13 适配 Stage 状态展示：增加 QUALITY_GATE_BLOCKED、ANCHOR_MISMATCH、warning 计数与来源明细
- [x] 13.14 适配章节切分页面：内置规则启停、自定义 regex CRUD、优先级排序、规则测试预览
- [x] 13.15 执行前后端联调回归：从导入到导出全流程，逐页校验字段映射与交互一致性
- [x] 13.16 适配新交互：PREVIEW_STALE 提示、accepted_edited 微调入口、risk_signature 风险导出标识
- [x] 13.17 输出三栏工作台低保真线框说明（逐屏/逐按钮/逐状态），沉淀到 OpenSpec 文档
- [x] 13.18 按低保真方案重构小说详情页为统一三栏工作台骨架（左章列表/中主区/右侧栏）
- [x] 13.19 实现右栏三 Tab（洞察/操作/日志）并统一五个核心 Stage 的操作入口
- [x] 13.20 实现中栏统一视图切换（原文/改写稿/Diff并排/Diff行内/最终稿）
- [x] 13.21 实现左栏章节导航增强：搜索、状态筛选、风险徽章、键盘切章
- [x] 13.22 实现 Git 风格 diff 审核细节：红删绿增、未变块折叠、锚点冲突禁用接受
- [x] 13.23 实现统一按钮状态机：loading/disabled 规范化（防重复触发、无变更禁用保存）
- [x] 13.24 实现长内容区域内滚动规范：章节预览、正文对比、右栏日志均独立滚动
- [x] 13.25 修正 Rewrite 视图语义：`原文` 固定原文数据源，`改写稿` 固定工作草稿数据源；无有效改写时显示空结果态与失败统计，禁止静默回退为原文同文
- [x] 13.26 将 Rewrite 段落审核迁移到中栏：在中栏展示完整原文、完整改写与微调输入，右栏移除正文编辑区
- [x] 13.27 强化 Rewrite 洞察诊断：对 `REWRITE_LENGTH_OUT_OF_RANGE` 展示 target_ratio/target_chars_min/target_chars_max/actual_chars 与修复建议
- [x] 13.28 拆分 Rewrite 操作作用域：段级动作（接受/拒绝/微调/重写本段）与章级动作（执行本章/重跑本章/批量接受）明确分层
- [x] 13.29 修复章节状态传导：左栏章节状态与顶部章节计数实时反映当前阶段逐章进度（pending/running/completed/failed）
- [x] 13.30 补齐完整错误日志查看：日志 Tab 支持查看完整错误详情（provider 返回体 + 校验上下文）
- [x] 13.31 修正文案与截断策略：中栏文本默认不截断；右栏仅摘要展示并提供“展开查看”入口

## 14. 测试

- [x] 14.1 编写章节切分单元测试：正则模式匹配、中文/英文/数字章节格式、边界情况
- [x] 14.2 编写 LLM Provider mock 和集成测试：模拟 OpenAI 与 OpenAI 兼容响应
- [x] 14.3 编写 Prompt 模板引擎单元测试：变量插值、条件渲染、缺失变量处理
- [x] 14.4 编写 Stage 状态机单元测试：状态转换、依赖检查、stale 级联、暂停/恢复
- [x] 14.5 编写输出校验器测试：JSON schema 校验、字数范围校验、相似度检测
- [x] 14.6 编写 Worker Pool 测试：并发控制、动态扩缩容、失败重试、Rate Limiter 集成
- [x] 14.7 编写 API 集成测试：Import → Split → Analyze → Mark → Rewrite → Assemble 全流程
- [x] 14.8 编写导出格式测试：TXT/EPUB/Markdown/diff 输出正确性
- [x] 14.9 前端组件测试：AI Config Bar、全局提示词编辑器、Pipeline 进度视图
- [x] 14.10 E2E 测试：完整 pipeline 从上传到导出
- [x] 14.11 编写三模式配置同步测试：AI Config Bar / 可视化编辑器 / JSON 编辑器修改后验证其余模式实时同步
- [x] 14.12 编写 Provider 模型列表流程测试：获取模型列表、模糊搜索、测试连接、保存更新（同凭证 upsert）
- [x] 14.13 编写锚点一致性测试：paragraph_range 越界、hash 不匹配时应 failed(ANCHOR_MISMATCH) 且回退原文
- [x] 14.14 编写 Assemble 预检与覆盖测试：缺失章节 rewrite artifact、非法 segment、部分章节未改写场景下导出完整性
- [x] 14.15 编写章节切分规则测试：内置规则启停、自定义 regex 编译校验、规则优先级与预览准确性
- [x] 14.16 编写 Stage 执行一致性测试：单飞锁、幂等触发、配置快照持久化与读取
- [x] 14.17 编写质量闸门测试：超阈值阻断、强制导出放行、quality_report 内容完整性
- [x] 14.18 编写前端契约回归测试：后端 OpenAPI 变更后校验前端 API client 与关键页面不回归
- [x] 14.19 编写 regex 安全测试：灾难性回溯规则超时中断（REGEX_TIMEOUT）与复杂度拒绝
- [x] 14.20 编写切分预览防漂移测试：rules_version/source_revision 变化时返回 PREVIEW_STALE
- [x] 14.21 编写锚点强度测试：首尾 hash 相同但中间文本变化时命中 ANCHOR_MISMATCH
- [x] 14.22 编写改写微调测试：accepted→accepted_edited 生命周期与审计轨迹正确
- [x] 14.23 编写强制导出签名测试：TXT/EPUB/export_manifest 均包含 risk_signature
- [x] 14.24 编写 Rewrite 视图语义测试：`原文`/`改写稿` 数据源严格分离，无有效改写时显示空结果态
- [x] 14.25 编写中栏段落审核测试：完整原文与完整改写可见，微调在中栏完成并成功回写状态
- [x] 14.26 编写 Rewrite 长度失败洞察测试：`REWRITE_LENGTH_OUT_OF_RANGE` 诊断字段完整展示
- [x] 14.27 编写日志全文可见性测试：错误详情支持完整展开，不出现正文截断导致的信息缺失
- [x] 14.28 编写章节状态传导回归测试：左栏章节状态与顶部章节计数与后端 run 进度一致

## 15. 部署

- [ ] 15.0 阶段门禁：Docker 相关任务必须在 13.18~13.31 与对应测试（含 14.24~14.28）验收完成后再开始（Local First）
- [ ] 15.1 本地分离启动验收：前端 `npm run dev` + 后端 `uv run`，验证 API / WebSocket / 核心流程联调无误
- [ ] 15.2 本地生产模式验收：前端 build 后由后端静态托管，验证单进程运行与导出链路 [blocked by: 1.11]
- [ ] 15.3 编写 Dockerfile 和 docker-compose 配置（仅在 15.1 / 15.2 通过后进行）
- [ ] 15.4 执行容器化冒烟测试：启动、健康检查、导入→处理→导出最小闭环
- [ ] 15.5 实现 SQLite 定期备份 + Artifact Store 清理策略（可配置保留天数）
- [ ] 15.6 编写项目 README：安装、配置、运行说明（明确“本地验证通过后再使用 Docker”）
