## Why

当前改写流程以段落为主要处理单元，导致三个问题：一是场景命中与改写范围过粗，二是格式差异会放大 diff 噪声，三是“句号后换行”这类展示需求被错误挤进了核心数据层。需要把“改写单元”升级为句子级，同时保留原文作为唯一真实源，避免再次出现状态与结果不一致。

## What Changes

- 引入“Raw + Canonical”双轨文本处理：Raw 作为唯一持久化真值，Canonical 仅用于分析/标记/diff 对齐
- 在 Mark 阶段为每个可改写片段补充句子级元数据：`sentence_range` 与 `char_offset_range`（同时保留 `paragraph_range` 兼容旧流程）
- Rewrite 阶段优先按字符偏移切片源文本（句子级窗口），缺失时回退到段落范围，保证兼容
- Rewrite 阶段对超长改写片段启用自动拆分执行，并在完成后自动合并回单段结果（前端仅展示拆分处理信息）
- 新增改写完成度判定：无可改写段落章节计入已完成，`resume` 仅补跑未完成章节
- 新增“句末换行”可选输出模式（展示/导出层），不修改底层原文与改写存储

## Capabilities

### New Capabilities
- `sentence-aware-marking`: 句子级标记与改写窗口能力（Raw 保留、Canonical 对齐、句子范围锚定）
- `rewrite-output-reflow`: 改写结果可选重排版能力（句末换行，仅展示/导出生效）

### Modified Capabilities
- `rewrite-marking`: 从纯段落标记升级为“段落 + 句子 + 偏移”混合标记（兼容旧字段）
- `content-rewrite`: 源文本切片策略由段落优先调整为偏移优先、段落回退
- `task-management`: `resume` 行为升级为“仅补跑未完成章节”，并修正章节完成度计算

## Impact

- 后端：`marking.py`、`stages.py`、导出渲染与相关 Pydantic 模型
- 前端：全局阶段操作状态语义、分析与标记状态合并逻辑、输出格式选项（后续）
- Artifact：`mark_plan.json` 和 rewrite 聚合新增句子级元数据（向后兼容）
- 测试：新增句子级标记、偏移切片、resume 补跑、可选排版模式回归测试
