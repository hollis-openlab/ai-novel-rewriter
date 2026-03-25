## ADDED Requirements

### Requirement: 基于规则的改写标记
系统 SHALL 根据场景类型和改写规则配置，自动标记章节中可以改写或扩写的段落。

#### Scenario: 自动标记改写区域
- **WHEN** 场景识别完成后，用户触发改写标记
- **THEN** 系统根据配置规则扫描章节，标记出建议改写的段落，每个标记包含改写类型（扩写/改写/精简）和建议理由

### Requirement: 改写规则配置
系统 SHALL 允许用户为每种场景类型配置改写规则，包括：目标字数比例、改写策略（扩写/改写/保留）、优先级。

#### Scenario: 配置对话场景的改写规则
- **WHEN** 用户为"对话"场景设置"扩写"策略，目标字数比例为 1.5x
- **THEN** 后续标记时，对话场景的段落被标记为需要扩写至 1.5 倍字数

#### Scenario: 配置某场景类型为"保留"
- **WHEN** 用户为"环境描写"场景设置"保留"策略
- **THEN** 后续标记时，环境描写段落不会被标记为需要改写

### Requirement: 手动调整标记
系统 SHALL 允许用户手动添加、移除或修改改写标记。

#### Scenario: 手动添加改写标记
- **WHEN** 用户选中一个未被标记的段落并手动添加改写标记
- **THEN** 该段落加入改写队列，用户可指定改写类型和备注

#### Scenario: 手动移除改写标记
- **WHEN** 用户移除一个自动生成的改写标记
- **THEN** 该段落从改写队列中移除，不会被改写

### Requirement: 改写预估
系统 SHALL 在标记完成后展示改写预估：预计新增字数、预计 LLM 调用次数、预计耗时。

#### Scenario: 查看改写预估
- **WHEN** 所有改写标记确认后
- **THEN** 系统计算并展示改写预估数据，帮助用户决定是否执行

### Requirement: 改写标记数据结构
系统 SHALL 为每个改写标记存储以下字段：segment_id（唯一标识）、paragraph_range（段落范围）、anchor（段落锚点元数据：paragraph_start_hash/paragraph_end_hash/range_text_hash/context_window_hash/paragraph_count_snapshot）、scene_type（场景类型）、strategy（expand/rewrite/condense/preserve）、target_ratio（目标字数比例）、target_chars（目标字数）、target_chars_min/max（字数范围）、suggestion（改写建议）、source（auto/manual）、confirmed（是否已确认）。

#### Scenario: 查看标记详情
- **WHEN** 用户在改写标记界面查看某个标记
- **THEN** 展示该标记的所有字段信息，包括来源（自动/手动）和确认状态

### Requirement: 锚点多因子基线
系统 SHALL 在 Mark Stage 写入多因子锚点（首段 hash、末段 hash、区间文本 hash、上下文窗口 hash、段落总数快照），供 Rewrite/Assemble 校验一致性。

#### Scenario: 中间段落被修改导致锚点失效
- **WHEN** 首尾段落未变，但区间中间段落文本已变更
- **THEN** 后续校验通过 range_text_hash 识别不一致，并判定为 ANCHOR_MISMATCH

### Requirement: segment_id 自动生成
系统 SHALL 在创建改写标记时自动生成 segment_id（UUID v4），无论来源是自动还是手动。

#### Scenario: segment_id 自动分配
- **WHEN** 系统或用户创建改写标记
- **THEN** 自动生成唯一 UUID v4 作为 segment_id

### Requirement: 段落范围不重叠
系统 SHALL 验证同一章节内的改写标记 paragraph_range 不重叠。

#### Scenario: 拒绝重叠标记
- **WHEN** 用户添加 paragraph_range [5,10] 但已存在 [8,12]
- **THEN** 系统拒绝并提示段落范围重叠
