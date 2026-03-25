## ADDED Requirements

### Requirement: 系统 SHALL 提供可选句末换行输出模式
系统必须提供可选输出模式，将改写文本按句末标点重排为“每句一行”，用于展示或导出。

#### Scenario: 启用句末换行模式
- **WHEN** 用户在导出或展示选择 `sentence_linebreak` 模式
- **THEN** 输出文本应在句末标点（如 `。` `！` `？`）后断行
- **THEN** 模式应保留原句内容语义不变

### Requirement: 句末换行 SHALL 不修改核心存储文本
句末换行仅为视图/导出层转换，系统不得覆盖数据库或 stage artifact 中的原始改写文本。

#### Scenario: 文本存储保持不变
- **WHEN** 用户使用句末换行模式导出
- **THEN** 导出文件可重排版
- **THEN** 系统内存储的 raw/rewrite 原文必须保持原样

### Requirement: 模式默认 SHALL 为关闭
系统默认输出模式必须与当前行为一致，未显式启用时不进行句末换行。

#### Scenario: 未选择模式时保持原行为
- **WHEN** 用户不传递重排版参数
- **THEN** 输出文本必须与当前未重排版流程一致
