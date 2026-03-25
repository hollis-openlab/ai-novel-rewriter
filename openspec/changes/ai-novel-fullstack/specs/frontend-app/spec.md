## ADDED Requirements

### Requirement: Apple 风格设计系统
前端 SHALL 采用 Apple 风格设计语言：大面积留白、圆角卡片、SF Pro 字体（或 Inter 替代）、柔和阴影、微妙动效。

#### Scenario: 视觉风格一致性
- **WHEN** 用户访问任意页面
- **THEN** 页面呈现统一的 Apple 风格视觉：白色/浅灰背景、圆角容器、清晰的层级关系、简洁的图标

### Requirement: 任务仪表盘
前端 SHALL 提供任务仪表盘页面，展示所有任务的概览、状态分布图、最近活动。

#### Scenario: 查看任务仪表盘
- **WHEN** 用户打开应用首页
- **THEN** 展示任务统计卡片（进行中/已完成/失败数量）、任务列表、最近操作记录

### Requirement: 小说详情页
前端 SHALL 提供小说详情页，包含章节列表、处理 pipeline 进度、章节内容预览。

#### Scenario: 查看小说处理状态
- **WHEN** 用户点击某本小说
- **THEN** 展示小说元数据、pipeline 阶段进度条、章节列表（每章标注当前处理状态）

### Requirement: 切分规则配置与预览
前端 SHALL 在章节切分页面提供“内置规则 + 自定义正则规则”配置区，支持优先级调整、启用开关、规则测试预览。

#### Scenario: 新增并测试自定义正则
- **WHEN** 用户添加正则规则并点击“测试规则”
- **THEN** 页面展示命中示例、预计章节数和切分预览，不直接覆盖当前切分结果

#### Scenario: 预览结果过期提示
- **WHEN** 用户基于旧 preview_token 提交切分确认，后端返回 PREVIEW_STALE
- **THEN** 页面提示“预览已过期，请重新测试规则”，并引导用户重新获取预览

#### Scenario: 内置规则保持可见
- **WHEN** 用户打开切分规则设置
- **THEN** 页面展示默认内置规则（如“第一章”“第 1 章”）及其启用状态

### Requirement: 章节编辑器
前端 SHALL 提供章节编辑器，支持场景颜色标注、改写标记可视化、原文/改写对照视图。

#### Scenario: 查看场景标注
- **WHEN** 用户打开某个已完成场景识别的章节
- **THEN** 章节内容以不同背景色标注各场景类型，鼠标悬停显示场景类型名称

#### Scenario: 对比原文和改写
- **WHEN** 用户在章节编辑器中切换到"对照"视图
- **THEN** 左右分栏展示原文和改写结果，差异部分高亮

#### Scenario: 接受后微调改写
- **WHEN** 用户已接受某段改写并点击“微调”
- **THEN** 页面提供内联编辑器保存人工微调文本，并显示状态为 accepted_edited

### Requirement: 三栏工作台统一布局
前端 SHALL 在 Split/Analyze/Mark/Rewrite/Assemble 五个核心环节统一采用左中右三栏布局，保持信息架构一致。

#### Scenario: 核心环节布局一致
- **WHEN** 用户在五个核心环节之间切换
- **THEN** 左栏保持章节导航、中栏保持主内容、右栏保持洞察/操作/日志三类能力

#### Scenario: 区域内滚动而非整页拉伸
- **WHEN** 当前章节内容很长或预览章节很多
- **THEN** 中栏内容区域内部滚动，页面整体不因正文长度无限延展

### Requirement: 左栏章节导航工作流
前端 SHALL 在左栏提供章节检索、状态筛选、风险标识和键盘快速切章能力。

#### Scenario: 按状态快速定位章节
- **WHEN** 用户在左栏选择“失败”或“需人工确认”筛选
- **THEN** 章节列表仅显示对应状态章节，并保留当前 Stage 上下文

#### Scenario: 键盘切章
- **WHEN** 用户在工作台中按下上下方向键
- **THEN** 当前选中章节切换到上一章/下一章，中栏与右栏同步刷新

### Requirement: 右栏三 Tab 决策中心
前端 SHALL 将右栏固定为“洞察 / 操作 / 日志”三个 Tab，避免关键操作分散在页面各处。

#### Scenario: 查看洞察
- **WHEN** 用户切换到“洞察”
- **THEN** 展示本章节关键上下文（规则命中、场景识别、风险提示、配置快照）

#### Scenario: 执行操作
- **WHEN** 用户切换到“操作”
- **THEN** 展示本阶段主按钮和单章/批量操作入口（重试、跳过、确认、回退等）

#### Scenario: 审计追踪
- **WHEN** 用户切换到“日志”
- **THEN** 展示运行历史、错误明细、Prompt 调用记录，并支持定位到对应章节

### Requirement: Git 风格 Diff 审核体验
前端 SHALL 在 Rewrite/Assemble 相关对比中提供 Git 风格差异展示（红删绿增），支持并排与行内两种模式。

#### Scenario: 并排对比
- **WHEN** 用户选择“Diff（并排）”
- **THEN** 左侧显示原文、右侧显示改写，差异行高亮显示

#### Scenario: 行内对比
- **WHEN** 用户选择“Diff（行内）”
- **THEN** 在同一列中按块展示新增/删除内容，颜色语义与并排模式一致

#### Scenario: 锚点冲突禁用接受
- **WHEN** 某段改写被标记为 ANCHOR_MISMATCH
- **THEN** 该段“接受”按钮禁用，右栏显示冲突原因与修复建议

### Requirement: Stage 按钮状态机一致性
前端 SHALL 统一关键按钮的可用性规则和加载反馈，防止重复触发与误操作。

#### Scenario: 运行中禁用重复触发
- **WHEN** 用户点击“开始/重试/确认”等关键操作后进入 loading
- **THEN** 同按钮进入 disabled+spinner 状态，直到请求完成

#### Scenario: 无变更禁用保存
- **WHEN** 表单或规则没有实际变更
- **THEN** “保存”按钮显示 disabled，不触发请求

### Requirement: AI Config Bar（自然语言配置输入）
前端 SHALL 在配置页面顶部提供一个全局输入框（AI Config Bar），用户用自然语言描述配置变更，系统解析后展示 Diff 预览，确认后应用。

#### Scenario: 自然语言修改配置
- **WHEN** 用户在 AI Config Bar 输入"战斗场景扩写比例改成2.5倍"
- **THEN** 系统调用 LLM 解析意图，展示变更预览卡片（旧值 2.0x → 新值 2.5x），用户点击"应用"后生效

#### Scenario: 自然语言新增配置
- **WHEN** 用户输入"新增一个场景类型叫'修炼突破'，关键词是突破、进阶、丹田"
- **THEN** 系统展示将要新增的场景规则预览，用户确认后创建

#### Scenario: 自然语言修改 Prompt
- **WHEN** 用户输入"全局system prompt改成：你是一个专业的武侠小说改写助手"
- **THEN** 系统展示 system prompt 的旧值和新值 Diff，确认后更新

#### Scenario: 含歧义的输入
- **WHEN** 用户输入"温度调高到0.8"但未指定 Stage
- **THEN** 系统返回提示"模型参数请在 Provider 配置页面调整"

#### Scenario: 输入历史和自动补全
- **WHEN** 用户点击 AI Config Bar 输入框
- **THEN** 展示最近的配置变更历史和常用操作建议

### Requirement: 可视化配置管理界面
前端 SHALL 提供可视化配置管理页面，包含全局提示词编辑器、场景规则编辑器、改写规则编辑器、JSON 导入导出。配置页面顶部常驻 AI Config Bar。

#### Scenario: 首次进入配置页为空状态
- **WHEN** 用户首次进入配置页且尚未创建任何规则
- **THEN** 场景规则和改写规则区域展示空状态提示“请先手动添加规则”
- **AND** 页面不展示任何内置场景模板

#### Scenario: 编辑场景规则
- **WHEN** 用户在配置页面编辑某条场景规则
- **THEN** 提供表单界面编辑关键词（标签输入）、权重（滑块）、启用状态等字段

#### Scenario: 导出配置为 JSON
- **WHEN** 用户在配置页面点击"导出 JSON"
- **THEN** 浏览器下载包含所有配置的 JSON 文件

#### Scenario: 从 JSON 导入配置
- **WHEN** 用户拖拽 JSON 文件到配置页面的导入区域
- **THEN** 系统校验 JSON 格式，展示导入预览（新增/覆盖/跳过），用户确认后执行导入

#### Scenario: JSON 原始编辑
- **WHEN** 用户切换到"JSON 编辑"模式
- **THEN** 展示配置的 JSON 原始内容，支持直接编辑、语法校验、错误提示

#### Scenario: 三种模式实时同步
- **WHEN** 用户通过 AI Config Bar 修改了某项配置
- **THEN** 可视化编辑器和 JSON 编辑器中对应值实时更新

### Requirement: 模型提供商配置交互
前端 SHALL 在模型配置页面提供 OpenAI/OpenAI 兼容 provider 的完整配置流程：获取模型列表、列表模糊搜索、选择模型后测试连接、保存（同凭证更新）。

#### Scenario: 获取模型列表并搜索
- **WHEN** 用户填写 API Key 与 BaseURL 后点击“获取模型列表”，并在搜索框输入关键字
- **THEN** 页面展示可选模型列表并实时按关键字模糊筛选

#### Scenario: 选择模型后测试连接
- **WHEN** 用户从列表中选择模型并点击“测试连接”
- **THEN** 页面显示该模型的连接测试结果（成功/失败、延迟）

#### Scenario: 同凭证修改模型并保存
- **WHEN** 用户在已有 provider 上保持 API Key 和 BaseURL 不变，仅切换模型后点击“保存”
- **THEN** 页面提示“已更新现有 provider”，不新增重复 provider 卡片

### Requirement: 全局提示词编辑
前端 SHALL 提供全局提示词编辑器，作为规则配置页中的唯一提示词配置入口。

#### Scenario: 编辑全局提示词
- **WHEN** 用户在配置页修改 global prompt 并保存
- **THEN** 新提示词立即生效，并同步到 JSON 编辑视图

### Requirement: Prompt 日志查看器
前端 SHALL 在章节详情页提供 Prompt 日志查看器，展示该章节的所有 LLM 调用历史。

#### Scenario: 查看单章 Prompt 日志
- **WHEN** 用户在章节详情页点击"Prompt 日志"按钮
- **THEN** 展示该章节的 LLM 调用时间线：每次调用显示 Stage、时间、provider、token 用量、校验结果。展开可查看完整的 system prompt、task prompt、LLM 响应原文

#### Scenario: 复制 Prompt 用于调试
- **WHEN** 用户在日志中点击某次调用的"复制 Prompt"按钮
- **THEN** 完整的 system prompt + task prompt 复制到剪贴板，可粘贴到 ChatGPT 或兼容调试工具

#### Scenario: 从日志重试
- **WHEN** 用户在日志中对某次失败调用点击"使用此 Prompt 重试"
- **THEN** 系统使用相同 prompt 但可让用户微调参数后重新调用 LLM

### Requirement: 章节分析结果展示
前端 SHALL 提供章节分析结果的多维度展示：人物状态面板、关键事件时间线、改写潜力标注。

#### Scenario: 查看人物状态卡片
- **WHEN** 用户在章节详情页展开"登场人物"面板
- **THEN** 以卡片形式展示每个人物的名称、情绪标签（带颜色）、状态描述、章内角色

#### Scenario: 查看关键事件时间线
- **WHEN** 用户在章节详情页展开"关键事件"面板
- **THEN** 以垂直时间线展示事件列表，每个事件显示类型图标、描述文本、重要度星级，点击可定位到对应段落

#### Scenario: 查看改写建议图层
- **WHEN** 用户在章节编辑器中开启"改写建议"开关
- **THEN** 有改写潜力的段落以虚线边框标注，右侧显示改写建议气泡和优先级徽章

### Requirement: 改写覆盖率与回退告警面板
前端 SHALL 提供改写覆盖率与回退明细面板，展示“已改写/保留/失败/回退原文”统计及章节级明细。

#### Scenario: 查看回退告警
- **WHEN** Rewrite 或 Assemble 完成后存在 failed 或 warning
- **THEN** 页面高亮显示告警数，并可展开查看章节、段落范围和失败原因（如 ANCHOR_MISMATCH）

#### Scenario: 质量闸门阻断提示
- **WHEN** Assemble 被质量闸门阻断
- **THEN** 页面展示阻断原因、阈值对比、修复建议和“强制导出”入口
- **AND** 强制导出完成后展示 risk_signature，并标记该文件为“风险导出”

### Requirement: 实时进度展示
前端 SHALL 通过 WebSocket 接收任务进度更新，实时刷新 UI。

#### Scenario: 实时进度更新
- **WHEN** 后端处理任务产生进度更新
- **THEN** 前端在 500ms 内更新对应任务的进度条和状态文字，无需手动刷新

### Requirement: 响应式布局
前端 SHALL 适配 1024px 及以上宽度的桌面浏览器。

#### Scenario: 不同桌面分辨率
- **WHEN** 用户使用 1024px 到 2560px 宽度的浏览器
- **THEN** 布局自适应调整，内容区域合理利用空间
