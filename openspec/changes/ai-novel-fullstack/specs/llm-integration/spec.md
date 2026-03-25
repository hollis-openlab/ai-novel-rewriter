## ADDED Requirements

### Requirement: Provider 范围
系统 SHALL 仅支持 OpenAI 官方与 OpenAI 兼容提供商（如硅基流动），通过统一 LlmProvider interface 调用。

#### Scenario: 配置 OpenAI Provider
- **WHEN** 用户新增 OpenAI provider，填写 API Key 并选择模型
- **THEN** provider 保存成功并可用于后续调用

#### Scenario: 配置 OpenAI 兼容 Provider
- **WHEN** 用户新增 OpenAI 兼容 provider，填写 BaseURL、API Key 并选择模型
- **THEN** 系统按 OpenAI 协议调用并返回可用状态

### Requirement: 模型列表获取与搜索
系统 SHALL 支持从 provider 拉取模型列表，并支持按关键字模糊搜索。

#### Scenario: 获取模型列表
- **WHEN** 用户输入 API Key + BaseURL 后点击“获取模型列表”
- **THEN** 系统返回可选模型列表

#### Scenario: 模糊搜索模型
- **WHEN** 用户输入关键字 `gpt` 或 `qwen`
- **THEN** 系统返回按匹配度排序的候选模型

### Requirement: 基于已选模型测试连接
系统 SHALL 在模型被选择后提供连接测试能力，返回连通性与延迟。

#### Scenario: 测试连接
- **WHEN** 用户选择模型后点击“测试连接”
- **THEN** 系统以该模型发送最小请求并返回成功/失败和延迟信息

### Requirement: 同凭证保存为更新
系统 SHALL 将相同 provider_type + BaseURL + API Key 识别为同一 provider，保存时执行更新而非新建。

#### Scenario: 同凭证更换模型
- **WHEN** 用户在同一 API Key + BaseURL 下改选模型并保存
- **THEN** 系统更新已有 provider 的 model_name，不创建重复 provider

### Requirement: Provider 参数管理
系统 SHALL 在 provider 维度维护模型生成参数，包括 temperature、max_tokens、top_p（可选）等。

#### Scenario: 调整 provider 参数
- **WHEN** 用户在 provider 配置中将 temperature 调整为 0.8
- **THEN** 该 provider 后续调用默认使用 temperature=0.8

### Requirement: 阶段模型分配与降级
系统 SHALL 允许按 Stage（Analyze/Rewrite）分配 provider，并支持失败时降级到备选 provider。

#### Scenario: Stage 使用不同 provider
- **WHEN** 用户配置 Analyze 使用 provider-A，Rewrite 使用 provider-B
- **THEN** 系统在对应阶段调用对应 provider

#### Scenario: 降级到备选 provider
- **WHEN** 主 provider 重试失败且已配置备选 provider
- **THEN** 系统切换到备选 provider 做最后尝试

### Requirement: 全局提示词 + 内置任务模板
系统 SHALL 提供单一全局提示词作为所有阶段的 system prompt；Stage task prompt 使用系统内置模板，不在配置页暴露。

#### Scenario: 全局提示词生效
- **WHEN** 用户更新全局提示词
- **THEN** Analyze/Rewrite 阶段调用均使用新提示词

### Requirement: 输出校验与重试
系统 SHALL 对 Analyze/Rewrite 输出执行校验并在失败时自动重试（指数退避，最多 3 次）。

#### Scenario: Analyze JSON 校验失败
- **WHEN** Analyze 返回非合法 JSON
- **THEN** 自动重试

#### Scenario: Rewrite 复制原文
- **WHEN** Rewrite 输出与原文相似度超过 90%
- **THEN** 判定失败并自动重试

### Requirement: Prompt Audit Log
系统 SHALL 记录每次 LLM 调用的完整日志：system_prompt、user_prompt、params、response、usage、validation、duration。

#### Scenario: 查看调用日志
- **WHEN** 用户查看某章节 Prompt 日志
- **THEN** 可看到每次调用及重试的完整记录

### Requirement: API Key 安全存储
系统 SHALL 加密存储 provider API Key，API 响应不返回明文。

#### Scenario: 保存 API Key
- **WHEN** 用户保存 API Key
- **THEN** DB 中仅保存加密值，查询仅返回脱敏信息

### Requirement: Provider 速率限制
系统 SHALL 支持 provider 级 RPM/TPM 配置并在 Worker Pool 调用时生效。

#### Scenario: 配置速率限制
- **WHEN** 用户将 provider 设置为 RPM=60, TPM=100000
- **THEN** 系统超限后排队等待，不直接丢弃请求
