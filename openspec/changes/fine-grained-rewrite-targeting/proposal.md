## Why

当前改写流程在命中粒度上仍依赖“空行分段”，当章节空行稀疏时会形成超大改写区间，导致局部改写失控（一次替换覆盖上千字）。这直接引发三类高频问题：改写范围超预期、模型输出边界断裂（如半句起笔）、用户误判为系统丢失前文。该问题已在最新导入小说的中短章节（约 2.5k 字）中复现，说明不仅是超长章节问题，而是执行粒度问题。

## What Changes

- 将命中与执行单元升级为“句子索引驱动的改写窗口（Rewrite Window）”，不再以空行段落作为唯一粒度
- 在 Mark 阶段新增窗口规划：命中句聚合、上下文句扩展、窗口预算裁剪、超预算窗口拆分
- Rewrite 执行改为“窗口正文可改写、上下文只读参考”，并在应用层按 offset 精确替换
- Assemble 增加不变性保证：未命中区间逐字符保持原文，窗口区间不可重叠、不可越界
- 新增完整性守护（guardrails）：边界完整性、长度健康度、截断风险、空输出、重试与回退
- 新增窗口级审计与统计：每窗口执行轨迹、失败原因、重试链路、章节/阶段聚合指标
- 新增标准化章节告警状态字段（`has_warnings/warning_count/warning_codes/completion_kind`），前端只读后端状态
- 新增窗口身份键与版本字段（`plan_version/source_fingerprint`），确保重跑跳过判定可解释
- 明确零窗口章节 no-op 语义：无需模型调用、自动采用原文并输出原因码
- 前端改写预览补充窗口解释信息（命中范围/替换范围/保留范围），降低 diff 误读
- 通过特性开关灰度上线，支持快速回滚至旧 segment 执行路径

## Capabilities

### New Capabilities
- `sentence-window-targeting`: 句子级命中映射与可控窗口规划能力
- `scoped-rewrite-application`: 严格局部替换能力（只改窗口，窗口外不改）
- `rewrite-integrity-guardrails`: 改写边界与质量守护能力（失败重试/回退）
- `rewrite-window-auditability`: 窗口级可追溯审计与质量统计能力

### Modified Capabilities
- 无（`openspec/specs/` 当前无已归档基线规格，本次以新增能力完整定义）

## Impact

- Backend 关键模块
  - `backend/app/services/marking.py`：句子索引、窗口规划、mark artifact 扩展
  - `backend/app/services/rewrite_pipeline.py`：窗口执行、guardrail、重试回退
  - `backend/app/services/assemble_pipeline.py`：offset 精确替换、不变性校验
  - `backend/app/api/routes/chapters.py` / `stages.py`：窗口审计与阶段统计输出
- Frontend 关键模块
  - `frontend/src/pages/NovelDetail.tsx`：窗口级解释展示与降级兼容
- Artifact 与数据契约
  - `mark_plan.json` 新增窗口规划字段
  - `ch_*_rewrites.json` / `rewrites.json` 新增窗口执行与 guardrail 字段
  - 所有新增字段保持 optional，兼容历史文件
- 测试影响
  - 新增窗口规划、局部替换不变性、guardrail 重试/回退、API 可解释字段、前端展示回归测试

## Success Criteria

- 命中窗口平均大小显著下降，局部替换范围可解释（窗口级可视）
- “看起来丢前文”类问题可通过窗口审计定位到模型输出或守护回退原因
- 未命中区间保持原文不变的自动化测试通过率 100%
- 灰度期间可快速回滚且不影响历史 artifact 读取
