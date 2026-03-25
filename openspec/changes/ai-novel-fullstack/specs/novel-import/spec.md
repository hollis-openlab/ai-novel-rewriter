## ADDED Requirements

### Requirement: 支持 TXT 文件导入
系统 SHALL 接受 .txt 文件上传，优先按 UTF-8 解析，并支持 GBK/GB2312 等常见中文编码自动检测后转为 UTF-8 存储。

#### Scenario: 成功导入 TXT 文件
- **WHEN** 用户上传一个有效的 .txt 文件
- **THEN** 系统解析文件内容，创建对应的小说记录，返回小说 ID

#### Scenario: TXT 文件编码异常
- **WHEN** 用户上传一个非 UTF-8 编码的 .txt 文件
- **THEN** 系统尝试自动检测编码（GBK/GB2312）并转换为 UTF-8，若无法识别则返回编码错误提示

### Requirement: 支持 EPUB 文件导入
系统 SHALL 接受 .epub 文件上传，提取其中的纯文本内容（忽略图片和样式），按阅读顺序拼接并存储。

#### Scenario: 成功导入 EPUB 文件
- **WHEN** 用户上传一个有效的 .epub 文件
- **THEN** 系统解析 EPUB 结构，按 spine 顺序提取各章 HTML 中的纯文本，拼接为完整内容

#### Scenario: EPUB 文件损坏
- **WHEN** 用户上传一个结构损坏的 .epub 文件
- **THEN** 系统返回明确的错误信息，说明文件无法解析

### Requirement: 文件大小限制
系统 SHALL 限制单个上传文件的大小不超过 50MB。

#### Scenario: 文件超过大小限制
- **WHEN** 用户上传一个超过 50MB 的文件
- **THEN** 系统拒绝上传并返回文件过大的错误提示

### Requirement: 小说元数据记录
系统 SHALL 在导入时记录小说的元数据：原始文件名、文件格式、文件大小、导入时间、总字数。

#### Scenario: 导入后查看元数据
- **WHEN** 小说成功导入后
- **THEN** 系统可返回该小说的完整元数据信息

### Requirement: EPUB 原始结构保存
当导入 EPUB 文件时，系统 SHALL 在提取纯文本的同时，保存 EPUB 的原始结构信息（manifest、spine、CSS、封面）到 epub_structure.json，供 Assemble 阶段还原使用。

#### Scenario: EPUB 结构保存
- **WHEN** 用户导入一个 EPUB 文件
- **THEN** 系统提取纯文本的同时，将 EPUB 的 OPF manifest、spine 顺序、CSS 样式文件路径、封面图片保存到 epub_structure.json

#### Scenario: epub_structure.json 格式
- **WHEN** EPUB 结构保存完成
- **THEN** JSON 包含：opf_path, spine (文件名数组), manifest (文件名→media_type+path 映射), metadata (title/author/language), css_files (路径数组), cover_image (路径)。CSS 和图片以路径引用存储在 Artifact Store import/ 目录下。

#### Scenario: TXT 导入无结构保存
- **WHEN** 用户导入一个 TXT 文件
- **THEN** 不生成 epub_structure.json
