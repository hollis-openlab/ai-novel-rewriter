## 1. OpenSpec Artifacts

- [x] 1.1 编写 proposal：明确 Raw/Canonical 双轨、句子级标记与可选句末换行
- [x] 1.2 编写 design：确定偏移优先切片与段落回退策略
- [x] 1.3 编写 specs：`sentence-aware-marking` 与 `rewrite-output-reflow`
- [x] 1.4 创建实现任务清单并按会话更新进度

## 2. Backend Sentence-Aware Marking

- [x] 2.1 扩展 `RewriteSegment` 模型：新增可选 `sentence_range` 与 `char_offset_range`
- [x] 2.2 新增句子切分与偏移映射工具（保守规则，支持中文句末标点）
- [x] 2.3 在 Mark 阶段生成句子级元数据并写入 `mark_plan.json`
- [x] 2.4 保持段落锚点与旧字段兼容，确保旧 artifact 可读

## 3. Rewrite Execution and Progress Semantics

- [x] 3.1 修复 `resume`：Analyze/Rewrite 仅补跑未完成章节
- [x] 3.2 修复 Rewrite 完成度：无可改写章节计入 completed
- [x] 3.3 改写源文本切片改为“偏移优先、段落回退”
- [x] 3.4 为偏移回退路径记录明确 warning/错误细节
- [x] 3.5 实现超长段自动拆分改写并在结果层自动合并（不改变 segment 协议）

## 4. Output Reflow (View/Export Only)

- [x] 4.1 新增文本重排函数：`sentence_linebreak` 模式
- [x] 4.2 导出链路接入可选 reflow 参数（默认关闭）
- [x] 4.3 保证重排仅作用于输出，不回写存储文本

## 5. Frontend & Contracts

- [x] 5.1 `paused` 全局继续改回 `resume`，与后端补跑语义对齐
- [x] 5.2 修复 Analyze+Mark 状态合并，避免假 `running`
- [ ] 5.3 在导出/预览入口暴露句末换行模式开关（后续）

## 6. Tests

- [x] 6.1 新增后端测试：resume 仅补跑未完成章节（analyze/rewrite）
- [x] 6.2 更新后端测试：rewrite bootstrap 完成度语义
- [x] 6.3 新增后端测试：句子级元数据生成与偏移切片优先
- [x] 6.4 新增后端测试：`sentence_linebreak` 模式输出与默认行为兼容
- [x] 6.5 新增前端测试：paused 阶段继续按钮调用 `resume`
- [x] 6.6 新增后端测试：超长片段自动拆分执行与自动合并输出
