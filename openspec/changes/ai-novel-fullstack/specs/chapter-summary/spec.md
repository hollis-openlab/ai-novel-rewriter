## ADDED Requirements

### Requirement: 自动生成章节摘要
系统 SHALL 使用配置的 LLM 对每个章节内容生成摘要，摘要长度控制在 200-500 字。

#### Scenario: 成功生成单章摘要
- **WHEN** 用户触发对某个章节的摘要生成
- **THEN** 系统调用 LLM 生成该章节的内容摘要，包含主要情节、人物行为和场景变化

#### Scenario: 批量生成所有章节摘要
- **WHEN** 用户触发全书摘要生成
- **THEN** 系统并行调用 LLM 为所有章节生成摘要，通过 Worker Pool 控制并发

### Requirement: 摘要包含结构化信息
系统 SHALL 在摘要中提取并标注：登场人物（含情绪和状态）、关键事件（含类型和重要程度）、场景地点、情感基调。

#### Scenario: 摘要包含结构化字段
- **WHEN** 章节摘要生成完成
- **THEN** 摘要结果包含以下结构化字段：summary（自由文本摘要）、characters（人物列表）、key_events（关键事件列表）、location（场景地点）、tone（情感基调）

### Requirement: 登场人物深度识别
系统 SHALL 识别每个章节中的登场人物，并分析每个人物的情绪状态和当前处境。

#### Scenario: 识别人物及情绪状态
- **WHEN** 章节分析完成
- **THEN** 每个登场人物包含以下字段：name（名称）、emotion（当前情绪，如愤怒、喜悦、焦虑、平静等）、state（当前状态/处境，如受伤、隐藏身份、突破修为、陷入困境等）、role_in_chapter（本章角色，如主角、对手、盟友、旁观者等）

#### Scenario: 人物跨章追踪
- **WHEN** 用户查看某个人物在多个章节中的出现情况
- **THEN** 系统展示该人物在各章节的情绪和状态变化轨迹

### Requirement: 关键事件深度识别
系统 SHALL 识别每个章节中的关键事件，标注事件类型、重要程度和所在段落范围。

#### Scenario: 识别关键事件
- **WHEN** 章节分析完成
- **THEN** 每个关键事件包含以下字段：description（事件描述）、event_type（类型，如转折、冲突、揭示、成长、分离、重逢等）、importance（重要程度 1-5）、paragraph_range（事件所在段落起止位置）

#### Scenario: 高重要度事件高亮
- **WHEN** 用户查看章节分析结果
- **THEN** importance >= 4 的关键事件在界面上以醒目样式标注

### Requirement: 可改写场景识别
系统 SHALL 在分析阶段同时评估每个场景段落的改写潜力，标注是否可扩写、可改写以及改写建议。

#### Scenario: 场景改写潜力评估
- **WHEN** 章节分析完成
- **THEN** 每个场景段落附带 rewrite_potential 字段，包含：expandable（是否可扩写）、rewritable（是否可改写）、suggestion（改写建议文本，如"战斗动作描写可增加感官细节"）、priority（改写优先级 1-5）

#### Scenario: 改写建议作为标记阶段输入
- **WHEN** 进入改写标记阶段
- **THEN** 系统自动将分析阶段的 rewrite_potential 作为默认标记建议，用户可在此基础上调整

### Requirement: 摘要可手动编辑
系统 SHALL 允许用户手动修改 LLM 生成的摘要内容。

#### Scenario: 编辑后摘要持久化
- **WHEN** 用户修改章节摘要并保存
- **THEN** 系统保存修改后的摘要，后续阶段使用修改后的版本

### Requirement: 摘要生成可重试
系统 SHALL 允许用户对不满意的摘要重新生成。

#### Scenario: 重新生成摘要
- **WHEN** 用户对某章节摘要点击"重新生成"
- **THEN** 系统重新调用 LLM 生成新摘要，替换旧摘要（旧摘要不保留）

### Requirement: 段落定义与编号
系统 SHALL 以空行（一个或多个连续空行）作为段落分隔符，段落从 1 开始编号。所有涉及段落范围的字段（paragraph_range）使用 1-based 闭区间 [start, end]。

#### Scenario: 段落编号规则
- **WHEN** 章节内容被解析
- **THEN** 以 \n\n（一个或多个连续空行）分隔段落，单个换行符 \n 不构成段落分隔，段落从 1 开始编号
