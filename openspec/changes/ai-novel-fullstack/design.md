## Context

这是一个用于自动化小说改写与扩写的全栈项目。当前前端已有可运行的 React 19 基础页面，后端尚未开始开发。

核心挑战在于：
1. 小说文件格式多样，章节边界识别需要规则 + LLM 混合策略
2. LLM 调用是主要瓶颈，需要精细的并发控制和速率限制
3. 改写流程是多阶段 pipeline，每个阶段依赖上一阶段的输出
4. 用户需要对中间结果有可视化和干预能力

约束条件：
- 后端必须使用 Python 3.13 + FastAPI（提升开发效率并保留异步并发能力）
- 前端必须使用 React 19（Apple 风格 UI）
- 需要支持多种 LLM 提供商
- 单机部署，不依赖分布式基础设施

## Stage 术语对照表

| Stage Name (EN) | Stage Name (CN) | Description | Spec Files |
|-----------------|-----------------|-------------|------------|
| Import | 导入 | File upload and parsing | novel-import |
| Split | 切分 | Chapter boundary detection | chapter-splitting |
| Analyze | 分析 | Summary + characters + events + scenes + rewrite potential | chapter-summary + scene-recognition (merged) |
| Mark | 标记 | Confirm rewrite targets and strategies | rewrite-marking |
| Rewrite | 改写 | LLM-driven content rewriting | content-rewrite |
| Assemble | 组装 | Merge rewrites into final novel | assemble + novel-export |

> **注意**：chapter-summary 和 scene-recognition 两个 spec 共同定义 Analyze Stage。
>
> **边界说明**：`assemble` spec 定义组装语义与合并算法，`novel-export` spec 定义导出 API 与导出格式能力。

## Goals / Non-Goals

**Goals:**
- 构建 Stage-based Artifact Pipeline：Import → Split → Analyze → Mark → Rewrite → Assemble，每个 Stage 产出可持久化、可独立导出的 Artifact
- 任意 Stage 的中间产物可导出为 JSON（机器可读）或 Markdown/TXT（人类可读）
- 提供直观的 Web UI，用户可以管理任务、配置规则、查看进度、查看和导出每个阶段的产物
- 支持多种 LLM 后端，通过统一适配层切换
- 精细的并行控制，避免 API 速率限制和资源浪费
- 用户可自定义场景规则和改写策略，灵活适配不同类型的小说

**Non-Goals:**
- 不做在线协作编辑功能
- 不做小说原创生成（只做改写和扩写）
- 不做移动端适配（仅桌面 Web）
- 不做多租户/SaaS 部署
- 不做小说版权检测或去重
- 第一版不做实时流式输出（改写结果完成后再展示）

## Decisions

### 1. 后端框架：FastAPI + asyncio

**选择**：FastAPI 作为 Web 框架，Python 3.13 自带 asyncio 作为异步运行时

**理由**：
- FastAPI 在 Python 生态成熟，类型标注和 OpenAPI 能力完整，开发效率高
- asyncio 足够支撑 IO 密集型的 LLM 调用并发
- Starlette middleware 生态完善，易于集成 CORS、日志、限流和异常处理

**替代方案**：
- Flask + Gunicorn：异步能力和类型约束较弱
- Django：功能全面但对当前单机工具型项目偏重

### 1b. Python 环境与依赖管理：uv

**选择**：使用 `uv` 管理 Python 3.13 环境、依赖解析和锁文件。

**理由**：
- 一致的环境创建与依赖安装入口，降低团队协作成本
- 锁文件可复现，减少“本地能跑、线上报错”的环境漂移
- 相比传统工具链启动更快，适合频繁迭代

### 2. 数据存储：SQLite + 文件系统 Artifact Store

**选择**：SQLite 存储任务元数据、配置和 Stage 状态；文件系统作为 Artifact Store 存储每个 Stage 的产物

**职责划分**：
- **SQLite**：novels 表（元数据）、stage_runs 表（stage 状态、时间戳、错误信息）、configs 表、providers 表等。轻量、可查询、可筛选
- **文件系统 Artifact Store**：每个 Stage 的完整输出（JSON/TXT/EPUB）。路径结构为 `data/novels/{novel_id}/tasks/{task_id}/stages/{stage_name}/`。大体积、结构化、可直接导出

**设计原则**：DB 是索引，文件系统是仓库。DB 记录"哪本小说的哪个 Stage 在什么时间完成了"，文件系统存储"那个 Stage 产出了什么"。

**理由**：
- 单机部署场景，SQLite 零配置、嵌入式，足够可靠
- 小说文本和分析结果可能很大（数 MB），存文件系统比存数据库更高效
- Artifact 以 JSON 文件存储，天然支持导出——导出 = 读取文件 + 可选的格式转换
- 使用 SQLAlchemy 2.0（async）+ Alembic 管理数据库访问与迁移

**替代方案**：
- PostgreSQL：过于重量级，单机场景不需要
- 全部存 DB（JSONB）：大对象查询慢，导出需要额外序列化
- 纯文件存储：缺乏查询能力，任务筛选和状态管理困难

```sql
CREATE TABLE novels (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, original_filename TEXT NOT NULL,
    file_format TEXT NOT NULL CHECK (file_format IN ('txt','epub')),
    file_size INTEGER NOT NULL, total_chars INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')), config_override_json TEXT
);
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, novel_id TEXT NOT NULL REFERENCES novels(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    source_task_id TEXT REFERENCES tasks(id), auto_execute INTEGER NOT NULL DEFAULT 0,
    artifact_root TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')),
    archived_at TEXT
);
CREATE TABLE chapters (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
    char_count INTEGER NOT NULL, paragraph_count INTEGER NOT NULL,
    UNIQUE(task_id, chapter_index)
);
CREATE TABLE stage_runs (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    stage TEXT NOT NULL CHECK (stage IN ('import','split','analyze','mark','rewrite','assemble')),
    run_seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','paused','stale')),
    started_at TEXT, completed_at TEXT, error_message TEXT,
    run_idempotency_key TEXT,
    config_snapshot_json TEXT,
    warnings_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    chapters_total INTEGER DEFAULT 0, chapters_done INTEGER DEFAULT 0,
    UNIQUE(task_id, stage, run_seq)
);
CREATE TABLE chapter_states (
    id TEXT PRIMARY KEY, stage_run_id TEXT NOT NULL REFERENCES stage_runs(id),
    chapter_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','skipped')),
    error_message TEXT, started_at TEXT, completed_at TEXT,
    UNIQUE(stage_run_id, chapter_index)
);
CREATE TABLE providers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    provider_type TEXT NOT NULL CHECK (provider_type IN ('openai','openai_compatible')),
    credential_fingerprint TEXT NOT NULL,   -- hash(api_key + normalized_base_url)
    api_key_encrypted TEXT NOT NULL, base_url TEXT NOT NULL, model_name TEXT NOT NULL,
    temperature REAL DEFAULT 0.7, max_tokens INTEGER DEFAULT 4000, top_p REAL,
    presence_penalty REAL, frequency_penalty REAL,
    model_list_cache_json TEXT, model_list_fetched_at TEXT,
    rpm_limit INTEGER DEFAULT 60, tpm_limit INTEGER DEFAULT 100000,
    is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE configs (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'global' CHECK (scope IN ('global','novel')),
    novel_id TEXT REFERENCES novels(id), config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_tasks_one_active_per_novel ON tasks(novel_id) WHERE status = 'active';
CREATE INDEX idx_chapters_task_index ON chapters(task_id, chapter_index);
CREATE UNIQUE INDEX idx_providers_unique_credentials ON providers(provider_type, base_url, credential_fingerprint);
CREATE UNIQUE INDEX idx_stage_runs_singleflight ON stage_runs(task_id, stage) WHERE status = 'running';
CREATE UNIQUE INDEX idx_stage_runs_idempotency ON stage_runs(task_id, stage, run_idempotency_key) WHERE run_idempotency_key IS NOT NULL;
CREATE INDEX idx_stage_runs_latest ON stage_runs(task_id, stage, run_seq DESC);
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
```

Note: chapter_states 表支持暂停/恢复的逐章状态追踪。

### 3. LLM 适配层与 Prompt 架构

#### 3a. Provider Interface

```python
from typing import Protocol
from pydantic import BaseModel

class CompletionRequest(BaseModel):
    system_prompt: str
    user_prompt: str
    params: "GenerationParams"

class CompletionResponse(BaseModel):
    content: str
    usage: dict  # {"prompt_tokens": int, "completion_tokens": int}
    finish_reason: str  # stop, length, content_filter

class LlmProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    def name(self) -> str: ...
    def supports_json_mode(self) -> bool: ...
```

#### 3b. Prompt 策略（配置简化版）

每次 LLM 调用由两层组成：

```
① Global System Prompt（用户可配置，唯一入口）
② Stage 内置 Task Prompt（系统维护，不在配置页暴露）
```

- **Global System Prompt**：唯一可配置提示词，作用于 Analyze/Rewrite 全阶段
- **Stage 内置 Task Prompt**：用于约束结构化输出与任务目标，由系统维护默认模板
- **Scene 规则数据**：来自用户手动配置的场景识别规则与改写规则，作为上下文注入 Task Prompt

> 配置面板不提供 Stage/Scene prompt 分层编辑；用户只维护一份全局提示词。

**RewriteStrategy 枚举定义**：

```
RewriteStrategy = "expand" | "rewrite" | "condense" | "preserve"
```
- **expand**（扩写）：increase content, add details
- **rewrite**（改写）：same length, different expression
- **condense**（精简）：reduce content, keep core
- **preserve**（保留）：skip, do not modify

#### 3c. Generation Params（归属 Provider）

`temperature / max_tokens / top_p` 等模型生成参数由 Provider 配置管理，不在“配置管理（规则与提示词）”中维护。

```python
class GenerationParams(BaseModel):
    max_tokens: int
    temperature: float
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    response_format: str  # "json" (Analyze) / "text" (Rewrite)
```

参数解析优先级：
`provider.model_defaults -> stage runtime computed fields (e.g. target_chars) -> per-call temporary override`

#### 3d. 输出校验与智能重试

每次 LLM 调用的输出经过校验，校验失败自动重试：

| Stage | 校验项 | 失败动作 |
|-------|-------|---------|
| Split | JSON 格式合法 + 必须字段存在 | 重试 |
| Analyze | JSON schema 校验 + 摘要字数范围 + characters 字段存在（允许空数组） | 重试 |
| Rewrite | 字数在 target_chars_min..target_chars_max 范围内 | 重试 |
| Rewrite | 非空 + 不是原文复制 | 重试 |

相似度公式：`similarity = normalized_levenshtein(original, rewritten)`，`similarity > 0.90` 判定复制。

```python
class RetryConfig(BaseModel):
    max_retries: int = 3
    backoff_seconds: list[int] = [1, 2, 4]
    strategies: list[str] = ["adjust_temperature", "append_hint", "fallback_provider"]
```

#### 3e. Prompt 可观测性

每次 LLM 调用记录为 JSONL（每章一个文件）：
`data/novels/{id}/tasks/{task_id}/stages/{stage}/prompt_log/ch_{N}.jsonl`

每行 JSON 示例：
```json
{"call_id":"uuid","chapter_index":1,"attempt":1,"timestamp":"2026-03-18T21:00:00Z","system_prompt":"...global system prompt...","user_prompt":"...rendered task prompt...","params":{"temperature":0.7,"max_tokens":4000},"provider":"openai-compatible","response":"...","usage":{"prompt_tokens":1200,"completion_tokens":800},"validation":{"passed":true},"duration_ms":3200}
```

**理由**：
- 用户配置面收敛到“全局提示词 + 规则”，学习成本更低
- 生成参数放到 Provider，便于按模型统一调优
- 输出校验 + 自动重试保持稳定性
- Prompt 日志保证可调试性

### 4. 并行 Worker 架构：asyncio Queue + Worker Pool

**选择**：使用 `asyncio.Queue` 分发任务到 Worker Pool

**架构**：
```
TaskScheduler → asyncio.Queue → Worker Pool (N workers)
                                     ↓
                               LlmProvider.complete()
                                     ↓
                               Result → DB update + WebSocket notify
```

**Rate Limiter Integration**：

- 每个 Provider 拥有独立的 Token Bucket 速率限制器（RPM + TPM）
- Worker 工作流：Worker 从队列取任务 → 获取目标 Provider 的速率限制许可 → 调用 LLM → 释放许可
- 如果速率限制耗尽：Worker 在 acquire 上阻塞等待（**不拒绝**），超时 60s
- 如果超时：任务返回队列，Worker 取下一个任务
- 多个 Stage 共享同一 Provider：共享同一个速率限制器实例
- 速率限制器状态：仅存内存（重启后重置，无需持久化）

**理由**：
- Queue 天然提供背压（backpressure），防止任务堆积
- Worker 数量可动态调整
- 每个 Worker 独立处理，失败不影响其他 Worker

### 5. 处理 Pipeline 架构：Stage-based Artifact Pipeline

**核心原则：每个 Stage 产出持久化的、可独立导出的 Artifact。**

Pipeline 不是一条从头跑到尾的流水线，而是一系列独立的处理阶段，每个阶段把输入转化为一个明确的产物。用户可以在任意两个 Stage 之间停下来，检查产物、编辑产物、导出产物、调整配置后再继续。

**Pipeline Stages 及其 Artifact**：

```
Stage 1: Import ──→ Artifact: RawNovel (原始文本 + 元数据)
    ↓
Stage 2: Split  ──→ Artifact: ChapterList (章节边界 + 标题 + 内容)
    ↓
Stage 3: Analyze ─→ Artifact: AnalysisReport (摘要 + 人物 + 事件 + 场景 + 改写潜力)
    ↓
Stage 4: Mark   ──→ Artifact: RewritePlan (确认的改写标记 + 策略)
    ↓
Stage 5: Rewrite ─→ Artifact: RewriteResult (逐段落的原文/改写对)
    ↓
Stage 6: Assemble → Artifact: FinalNovel (拼装后的完整小说)
```

**Import Stage 特殊说明**：唯一不需要 LLM 的 Stage。文件解析后写入 `raw.txt` + `novel.meta.json`（EPUB 额外写入 `epub_structure.json`）到小说根目录，创建 DB 记录（novels + tasks + 初始 6 条 stage_runs，run_seq=1，import=completed 其余=pending）。后续 Stage 产物写入 task 作用域目录；同一 Stage 重跑时新增 run_seq 记录，不覆盖历史 run。

**文件系统 Artifact 存储结构**：
```
data/novels/{novel_id}/
├── novel.meta.json                        # 小说元数据（跨 task 共享）
├── raw.txt                                # Stage 1: 原始全文（跨 task 共享）
├── epub_structure.json                    # EPUB 导入时存在（跨 task 共享）
├── active_task_id                         # 当前活跃 task
└── tasks/
    ├── {task_id}/
    │   └── stages/
    │       ├── split/
    │       │   ├── status.json           # 阶段状态（时间戳、耗时、错误信息）
    │       │   └── chapters.json         # Stage 2 Artifact: 章节列表
    │       ├── analyze/
    │       │   ├── status.json
    │       │   ├── analysis.json         # Stage 3 Artifact: 全书分析（完整结构化数据）
    │       │   └── chapters/
    │       │       ├── ch_001_analysis.json
    │       │       └── ...
    │       ├── mark/
    │       │   ├── status.json
    │       │   └── rewrite_plan.json     # Stage 4 Artifact
    │       ├── rewrite/
    │       │   ├── status.json
    │       │   ├── rewrites.json         # Stage 5 Artifact
    │       │   └── chapters/
    │       │       ├── ch_001_rewrites.json
    │       │       └── ...
    │       └── assemble/
    │           ├── status.json
    │           ├── output.txt            # Stage 6 Artifact
    │           └── output.epub
    └── {archived_task_id}/...
```

补充：每个 stage 目录下维护 `runs/{run_seq}/` 快照子目录（保存该次运行的 status 与 artifact），根目录下文件始终指向 latest。这样既支持“当前读取”，也支持历史 run 追溯。

**Stage 状态机**：
```
每个 Stage 的状态：
  pending → running → paused → running → completed
                   ↘ failed → running (重试)

Stage 之间的依赖：
  只有前一个 Stage 为 completed 时，下一个 Stage 才可执行
  任意 Stage 可独立重跑（清除本阶段 artifact 后重新执行）
  重跑某个 Stage 后，其下游 Stage 的 artifact 标记为 stale（过期）
```

**Stale 级联矩阵**：
| 重跑 Stage | 下游全部变 Stale |
|-----------|-----------------|
| Import | Split → Analyze → Mark → Rewrite → Assemble |
| Split | Analyze → Mark → Rewrite → Assemble |
| Analyze | Mark → Rewrite → Assemble |
| Mark | Rewrite → Assemble |
| Rewrite | Assemble |
| Assemble | （无） |

规则：ALL 下游 Stage 变 stale，不仅仅直接下一个。

**Stage 执行并发控制（Single-flight + 幂等）**：
- 同一 `task_id + stage` 同时只能有一个 `running` 实例（`idx_stage_runs_singleflight` + 事务内状态检查）
- 触发执行时可传 `run_idempotency_key`，重复请求命中 `idx_stage_runs_idempotency` 后返回已有 `stage_run`（不重复启动）
- 自动推进与手动触发发生竞争时，统一经过同一加锁入口，后到请求复用先到执行
- 每次启动新运行时分配 `run_seq = max(run_seq)+1`，`GET stage status` 默认读取最新 run_seq

**Stage 运行配置快照（Config Snapshot）**：
- 每次 Stage 从 `pending/stale` 进入 `running` 时，持久化 `config_snapshot_json`
- 快照至少包含：provider/model、global_prompt 版本、scene_rules/rewrite_rules 版本哈希、关键运行参数
- Artifact `status.json` 中写入 `snapshot_ref`，确保导出结果可追溯、可重现

**Stage 历史查询语义**：
- `GET /novels/{id}/stages/{stage}/run`：返回 latest（run_seq 最大）记录
- `GET /novels/{id}/stages/{stage}/runs`：返回历史运行列表（按 run_seq 倒序）

**Analyze Stage 输出结构（核心 Artifact）**：
```
ChapterAnalysis {
    summary: String,                    // 章节内容摘要
    characters: Vec<CharacterState> {   // 登场人物识别
        name: String,                   //   人物名称
        emotion: String,                //   当前情绪（愤怒、喜悦、焦虑...）
        state: String,                  //   当前状态（受伤、隐藏身份、突破修为...）
        role_in_chapter: String,        //   本章角色（主角、对手、旁观者...）
    },
    key_events: Vec<KeyEvent> {         // 关键事件识别
        description: String,            //   事件描述
        event_type: String,             //   类型（转折、冲突、揭示、成长...）
        importance: u8,                 //   重要程度 1-5
        paragraph_range: (usize, usize),//  事件所在段落范围
    },
    scenes: Vec<SceneSegment> {         // 场景分段
        scene_type: String,             //   场景类型
        paragraph_range: (usize, usize),
        rewrite_potential: RewritePotential {  // 可改写性评估
            expandable: bool,           //   是否可扩写
            rewritable: bool,           //   是否可改写
            suggestion: String,         //   改写建议（如"战斗细节可扩充"）
            priority: u8,               //   改写优先级 1-5
        },
    },
    location: String,                   // 场景地点
    tone: String,                       // 整体情感基调
}
```

**Stage Artifact JSON Schemas**

以下为每个 Stage 产出的 Artifact 的完整 JSON Schema 定义。

**Split Artifact (chapters.json)**：
```json
{
  "novel_id": "uuid",
  "total_chapters": 89,
  "chapter_separator": "\n\n",
  "chapters": [
    {
      "index": 1,
      "title": "第一章 初入江湖",
      "char_count": 3200,
      "paragraph_count": 24,
      "paragraph_separator": "\n\n",
      "paragraphs": [
        { "index": 1, "start_offset": 0, "end_offset": 156, "char_count": 156 },
        { "index": 2, "start_offset": 158, "end_offset": 412, "char_count": 254 }
      ]
    }
  ]
}
```

**Analyze Artifact (ch_N_analysis.json)** — LLM 输出的精确 JSON 结构：
```json
{
  "summary": "string, 200-500 chars",
  "characters": [
    {
      "name": "张无忌",
      "emotion": "愤怒",
      "state": "受伤、体力不支",
      "role_in_chapter": "主角"
    }
  ],
  "key_events": [
    {
      "description": "张无忌发现古老功法的秘密",
      "event_type": "揭示",
      "importance": 5,
      "paragraph_range": [3, 5]
    }
  ],
  "scenes": [
    {
      "scene_type": "战斗",
      "paragraph_range": [12, 18],
      "rewrite_potential": {
        "expandable": true,
        "rewritable": true,
        "suggestion": "战斗动作描写过于简略，可大幅扩充感官细节",
        "priority": 5
      }
    }
  ],
  "location": "青云山脉",
  "tone": "紧张、热血"
}
```

**Mark Artifact (rewrite_plan.json)**：
```json
{
  "novel_id": "uuid",
  "created_at": "ISO8601",
  "total_marked": 42,
  "estimated_llm_calls": 42,
  "estimated_added_chars": 85000,
  "chapters": [
    {
      "chapter_index": 1,
      "segments": [
        {
          "segment_id": "uuid",
          "paragraph_range": [12, 18],
          "anchor": {
            "paragraph_start_hash": "sha256(...)", 
            "paragraph_end_hash": "sha256(...)",
            "range_text_hash": "sha256(...)",
            "context_window_hash": "sha256(...)",
            "paragraph_count_snapshot": 42
          },
          "scene_type": "战斗",
          "original_chars": 800,
          "strategy": "expand",
          "target_ratio": 2.0,
          "target_chars": 1600,
          "target_chars_min": 1200,
          "target_chars_max": 2000,
          "suggestion": "战斗动作描写过于简略",
          "source": "auto",
          "confirmed": true
        }
      ]
    }
  ]
}
```
其中 `strategy` 枚举 = `"expand"` | `"rewrite"` | `"condense"` | `"preserve"`；`source` = `"auto"` | `"manual"`。

**Rewrite Artifact (ch_N_rewrites.json)**：
```json
{
  "chapter_index": 1,
  "segments": [
    {
      "segment_id": "uuid (matches rewrite_plan)",
      "paragraph_range": [12, 18],
      "anchor_verified": true,
      "strategy": "expand",
      "original_text": "原始文本...",
      "rewritten_text": "改写后的文本...",
      "original_chars": 800,
      "rewritten_chars": 1650,
      "status": "accepted",
      "attempts": 1,
      "provider_used": "openai-gpt4"
    }
  ]
}
```
其中 `status` 枚举 = `"pending"` | `"completed"` | `"accepted"` | `"accepted_edited"` | `"rejected"` | `"failed"`。
`failed` 需记录 `error_code`（如 `ANCHOR_MISMATCH` / `VALIDATION_FAILED`）。

> **重要说明**：Rewrite Stage 的 LLM 输出为**纯文本（Plain Text）**，不是 JSON。系统在收到 LLM 的纯文本改写结果后，自动将其与 RewritePlan 中的原始 segment 信息配对，包装成上述 JSON 结构存储。

**每个 Stage 的 Artifact 导出格式**：

| Stage | JSON 导出 | 可读格式导出 |
|-------|----------|------------|
| Import | novel.meta.json | raw.txt |
| Split | chapters.json（章节边界+标题） | 逐章 TXT 文件 |
| Analyze | analysis.json（完整结构化数据） | Markdown 分析报告（人物表格+事件时间线+场景分布） |
| Mark | rewrite_plan.json（标记+策略） | Markdown 改写计划表 |
| Rewrite | rewrites.json（原文/改写对） | diff 格式 / 双栏对照 TXT |
| Assemble | quality_report.json / export_manifest.json | TXT / EPUB / 对照格式 |

**Analyze Artifact 的 Markdown 导出示例**：
```markdown
# 《仙剑奇缘》分析报告
导出时间：2026-03-18

---

## 第一章：初入江湖

### 摘要
少年张无忌因偶然机遇获得一本古老功法，在回家途中遭遇山匪...

### 登场人物
| 人物 | 情绪 | 状态 | 角色 |
|------|------|------|------|
| 张无忌 | 兴奋→恐惧 | 初学功法、身体虚弱 | 主角 |
| 王大彪 | 贪婪 | 山匪首领 | 对手 |

### 关键事件
1. ⭐⭐⭐⭐⭐ [揭示] 张无忌发现古老功法的秘密 (段落 3-5)
2. ⭐⭐⭐⭐ [冲突] 山匪围攻，张无忌被迫应战 (段落 12-18)

### 场景分段
| 段落范围 | 场景类型 | 可改写 | 建议 | 优先级 |
|---------|---------|--------|------|--------|
| 1-5 | 叙事过渡 | 扩写 | 可增加环境描写和人物内心活动 | 3 |
| 6-11 | 对话 | 保留 | — | — |
| 12-18 | 战斗 | 扩写 | 战斗动作描写过于简略，可大幅扩充 | 5 |
```

**Rewrite Stage 执行算法**：

1. 读取 RewritePlan → 按 chapter_index 分组
2. 章节间并行（Worker Pool），章节内 segments 串行（保证上下文连贯）
3. 每个 segment 处理流程：
   - strategy == preserve → 跳过（不调用 LLM，不写入 RewriteResult）
   - 校验锚点（chapter_index + paragraph_range + start/end hash + range_text_hash + context_window_hash + paragraph_count_snapshot）
   - 锚点不一致 → 标记 failed(error_code=ANCHOR_MISMATCH)，回退原文，不调用 LLM
   - 否则：提取 original_text + preceding_text(300字) + following_text(300字)
   - 从 Analyze Artifact 读取 chapter_summary + character_states
   - 构造 prompt → 调用 LLM → 收到纯文本 → 校验 → 包装为 RewriteResult
4. segment_id 由 Mark Stage 生成（UUID v4），Rewrite 通过 segment_id 关联
5. 章节内串行原因：后续 segment 的 preceding_text 可能包含前一个 segment 的改写结果

**Assemble Stage 设计**：

- **输入**：Split Artifact（章节列表）+ Rewrite Artifact（逐章改写结果）+ 原始 raw.txt
- **算法**：
  0. 组装前预检：校验 chapter_index 连续性、segment_id 可映射性、paragraph_range 合法性（不越界/不重叠）
     - 预检失败的 segment：记录 warning，按原文回退
     - 缺失 rewrite artifact 的章节：整章回退原文
  1. 按章节顺序遍历所有章节：
     a. 如果该章有改写段落（rewrite segments）：用改写文本替换对应的原始段落范围
     b. 如果该章无改写：使用原始文本不变
  2. 按原始章节分隔符模式拼接所有章节
  3. 执行质量闸门：
     - failed segment 占比 > 阈值 或 warning 总量 > 阈值 → 默认阻断导出（可强制导出）
     - 章节覆盖校验：输出章节数必须等于 Split 章节数，且 chapter_index 唯一
- **输出**：`output.txt`（完整小说）+ 可选 `output.epub` + `quality_report.json`（包含 `risk_signature`）+ `export_manifest.json`
- **EPUB 策略**：
  - 如果原始文件从 EPUB 导入：Import 阶段保存原始 EPUB 结构（manifest、spine、CSS）到 `epub_structure.json`。Assemble 阶段将修改后的文本重新插入原始结构
  - 如果原始文件从 TXT 导入：EPUB 导出时生成最小结构（每章一个文件、自动生成 TOC）

```python
# Assemble 合并算法
for chapter in chapters:
    original_paragraphs = split_by_blank_lines(chapter.raw_text)
    segments = load_rewrites(chapter.index)  # sorted by paragraph_range[0]
    output = []
    cursor = 1
    for seg in segments:
        while cursor < seg.paragraph_range[0]:
            output.append(original_paragraphs[cursor-1]); cursor += 1
        if seg.status in ("completed", "accepted", "accepted_edited"):
            output.append(seg.rewritten_text)
        else:  # rejected/failed/pending → 原文
            for i in range(seg.paragraph_range[0], seg.paragraph_range[1]+1):
                output.append(original_paragraphs[i-1])
        cursor = seg.paragraph_range[1] + 1
    while cursor <= len(original_paragraphs):
        output.append(original_paragraphs[cursor-1]); cursor += 1
    chapter_output = chapter.paragraph_separator.join(output)  # from Split Artifact
# 约束：segments 的 paragraph_range 不可重叠（Mark Stage 校验）
assert len(output_chapters) == len(chapters)  # 章节覆盖完整性
```

**段落定义（Paragraph Definition）**：

- 一个段落（paragraph）= 由一个或多个空行（`\n\n+`）分隔的文本块
- 文本内的单个换行符（`\n`）**不是**段落边界
- 段落编号为每章内从 1 开始（1-based）
- `paragraph_range` 为闭区间 `[start, end]`，start 和 end 均为 1-based 索引

**坐标系统一**：所有 Stage 统一使用段落号（1-based）定位。Split Artifact 中 `paragraphs` 数组建立段落号 → 字符偏移映射，同时记录 `chapter_separator` 和 `paragraph_separator`。后续 Analyze/Mark/Rewrite/Assemble 均使用 `paragraph_range: [start, end]` 引用段落。

**Task/Novel 关系定义**：

- 一本 Novel 同一时间只能有**一个活跃的 pipeline 执行（Task）**
- Task = 一本 Novel 的一次完整 pipeline 执行
- Novel 记录持久存在；Task 记录执行状态
- 若需以不同配置重跑：为同一 Novel 创建新 Task（旧 Task 归档）
- 新 Task 创建模式：
  - `from_scratch`：仅复用 `raw.txt`，从 Split 重新执行
  - `clone_from_task`：复制源 Task 的中间 Artifact 作为初始输入，再按需继续
- DB schema：`novels` 表 (1) → `tasks` 表 (N, 但仅 1 个 active) → `stage_runs` 表（每 stage 多次运行历史）
- Artifact 目录按 task 隔离，避免历史任务产物被新任务覆盖

**Pause/Resume 语义**：

- 暂停粒度：章节边界（当前章处理完成后暂停，下一章不启动）
- 进行中的 LLM 调用：等待完成并保存结果，然后暂停
- 恢复：跳过已完成的章节，从下一个 pending 章节继续
- 暂停状态：`paused`（区别于 `pending` 和 `running`）
- Stage 状态机更新：`pending → running → paused → running → completed / failed`

**理由**：
- **Artifact 持久化**：每个 Stage 的输出落盘为 JSON 文件，而非仅存在于内存或数据库。崩溃重启后可从最近的 Artifact 恢复
- **独立导出**：用户可能只需要分析报告（比如外发给编辑审核），不需要走完整个 pipeline
- **增量重试**：重跑 Stage 3 只需要 Stage 2 的 Artifact 作为输入，不需要重新切分
- **Artifact 版本化**：同一 Task 内重跑 Stage 会覆盖该 Stage 的 Artifact，但下游 Stage 标记为 stale，用户决定是否级联刷新
- **逐章粒度**：Analyze 和 Rewrite 的 Artifact 按章存储，支持单章重跑，不影响其他章节
- 人物情绪/状态追踪为后续改写提供上下文，避免改写后人物表现不一致
- 关键事件识别帮助改写引擎理解叙事节奏，避免在关键转折处过度扩写

### 6. 前端技术栈：React 19 + Vite + TailwindCSS

**选择**：
- React 19（利用并发渲染与 Suspense 改进交互体验）
- Vite 作为构建工具
- TailwindCSS 实现 Apple 风格设计
- Zustand 作为状态管理
- React Query 处理服务端数据

**理由**：
- React 19 的并发特性适合处理实时进度更新
- TailwindCSS 可以高效实现 Apple 风格的简洁设计
- Zustand 轻量且 API 简洁，适合中等复杂度的状态管理

**替代方案**：
- Next.js：SSR 能力在本项目中不需要，增加复杂度
- Redux：模板代码过多，本项目状态不算极其复杂

### 7. 前后端通信：REST + WebSocket

**选择**：
- REST API 处理 CRUD 操作（任务创建、配置管理、文件上传）
- WebSocket 推送实时进度更新

**理由**：
- REST 适合请求-响应模式的操作
- WebSocket 适合持续的进度推送，避免轮询开销
- 两者结合覆盖所有通信场景

### 7a. 模型提供商策略：仅 OpenAI 与 OpenAI 兼容

**选择**：
- 提供商范围收敛为 OpenAI 官方与 OpenAI 兼容服务（如硅基流动）
- 不保留 Anthropic/Ollama 专有适配层

**提供商配置工作流**：
1. 用户填写 API Key 与 BaseURL（OpenAI 官方可使用默认 BaseURL）
2. 点击“获取模型列表”从上游拉取可用模型
3. 在返回列表中进行模糊搜索并选择模型
4. 点击“测试连接”（使用已选模型发起最小请求）
5. 点击“保存”

**同凭证更新语义**：
- 同一 `provider_type + base_url + api_key` 视为同一个 provider（通过 `credential_fingerprint` 识别）
- 若已存在同凭证 provider，保存时执行更新（如切换 `model_name`），不新建重复记录
- 用户后续只需“重新获取模型列表 → 测试连接 → 保存”，即可修改该 provider 的模型

### 8. 配置系统：全局提示词 + 场景规则 + 改写规则 + JSON 导入导出

配置面仅包含三类内容：
- 全局提示词（global prompt）
- 场景识别规则（scene rules）
- 对应改写规则（rewrite rules）
- 场景规则默认空列表，不预置任何小说场景类型

> 模型参数（temperature / max_tokens 等）在 Provider 配置页维护，不在此处配置。

**核心交互：AI Config Bar（自然语言配置）**

AI Config Bar 只解析上述三类配置，不处理模型参数。

```
┌─────────────────────────────────────────────────────────────┐
│ 🔍 输入你想修改的配置...                                      │
│                                                              │
│  示例：                                                       │
│  "全局提示词改成：你是一个偏写实风格的小说改写助手"              │
│  "新增场景规则：修炼突破，关键词是突破、丹田、进阶"               │
│  "修炼突破场景改写策略改成扩写，倍率 2.2"                         │
└─────────────────────────────────────────────────────────────┘
```

**三种配置编辑模式并存**：

| 模式 | 适用场景 | 交互方式 |
|------|---------|---------|
| **AI Config Bar** | 快速修改 | 自然语言输入 → 预览 → 确认 |
| **可视化编辑器** | 精细编辑 | 全局提示词文本框 + 场景规则表单 + 改写规则表单 |
| **JSON 原始编辑** | 批量修改/备份恢复 | 直接编辑 JSON，导入导出 |

**JSON 配置结构**：
```json
{
  "version": "1.0",
  "global_prompt": "你是一个专业的网络小说改写助手...",
  "scene_rules": [
    { "scene_type": "修炼突破", "keywords": ["突破", "丹田", "进阶"], "weight": 1.0, "enabled": true }
  ],
  "rewrite_rules": [
    { "scene_type": "修炼突破", "strategy": "expand", "target_ratio": 2.2, "priority": 4, "enabled": true }
  ]
}
```

**理由**：
- 配置面收敛后，学习成本更低，减少误配置
- 规则与提示词能完整导入导出，便于迁移与版本管理
- 模型参数和规则配置分离，职责清晰

### 9. 章节切分策略：规则优先 + LLM 兜底

**选择**：先用“用户自定义正则 + 内置规则”匹配常见章节格式，匹配失败时调用 LLM 辅助识别

**规则来源与执行顺序**：
1. 用户自定义规则（可配置 `name/pattern/priority/enabled`，按 `priority` 升序执行）
2. 内置规则组（默认启用，覆盖“第一章 / 第 1 章 / Chapter 1”等常见格式）
3. 若上述规则验证失败，再进入 LLM 兜底切分

**自定义规则能力边界**：
- 支持新增/编辑/删除/启停
- 保存前必须完成 regex 编译校验（非法 regex 拒绝保存）
- 支持“规则测试预览”：返回命中样本、预计章节数和切分边界，不直接覆盖已确认切分结果
- 规则执行安全保护：pattern 长度上限、复杂度预检、预览/切分执行超时（触发 `REGEX_TIMEOUT`）
- 预览防漂移：预览结果返回 `preview_token`（绑定 source_revision + rules_version + boundary_hash），确认切分必须携带且校验一致

**理由**：
- 大部分网络小说有明确的章节标记（第X章、Chapter X 等），正则即可处理
- LLM 调用成本高，只在规则失败时使用，节省 API 费用
- 用户可以在 UI 上手动调整切分结果
- 自定义 regex 使项目能适配站点特有章标题格式（如“【卷一】”“正文 001”）
- 防 ReDoS 与预览漂移可以避免“规则卡死”和“预览与落盘不一致”两类隐蔽故障

### 10. Prompt 模板引擎：Jinja2 (Python)

**选择**：Jinja2 模板引擎

**支持特性**：
- 变量插值：`{{ variable }}`
- 条件判断：`{% if condition %}...{% endif %}`
- 循环迭代：`{% for item in list %}...{% endfor %}`
- 过滤器（Filters）：`{{ value | upper }}`、`{{ value | truncate(length=200) }}` 等

**缺失变量处理**：默认渲染为空字符串；可配置 strict mode 在变量缺失时报错。

**转义**：`{{ "{{" }}` 用于输出字面量花括号。

**理由**：
- Jinja2 是 Python 生态事实标准，集成成本低
- Jinja2 语法在 Python/JS 社区广泛使用，用户学习成本低
- 支持 `{% if %}`/`{% for %}` 解决了 Open Questions 中关于是否需要循环渲染的问题

### 11. 文件上传规范

- 上传方式：`multipart/form-data` POST
- 最大文件大小：50MB（EPUB 为解压前大小）
- **EPUB 处理**：在临时目录解压 → 验证 EPUB 结构 → 提取文本 → 清理临时文件
- **TXT 编码检测**：使用 `charset-normalizer`（必要时回退 `chardet`），支持 UTF-8 / GBK / GB2312 / Big5
- **BOM 处理**：如果存在 BOM（Byte Order Mark），自动剥离
- **临时文件**：存储在 `data/tmp/{upload_id}/`，Import 完成或失败后清理（孤立文件 1 小时 TTL 自动清理）
- **上传响应**：`202 Accepted` + `novel_id` + `task_id`，后续异步处理（客户端通过 WebSocket 或轮询获取进度）

### 11a. 交付流程：Local First，Docker 最后

开发与验收采用“先本地、后容器”的顺序：
1. 前后端分离本地启动（后端 `uv run`，前端 `npm run dev`）完成联调
2. 本地验证核心闭环（导入 → 切分 → 分析 → 标记 → 改写 → 导出）与 WebSocket 进度推送
3. 验证通过后再进入容器化（Dockerfile / docker-compose）与容器冒烟测试

该策略用于降低排障复杂度：先在最小环境确认功能正确，再把问题缩小到“容器化差异”这一维度。

### Error Handling Framework

**API 错误响应格式**：
```json
{
  "error": {
    "code": "STAGE_FAILED",
    "message": "Chapter 5 analysis failed after 3 retries",
    "details": {
      "stage": "analyze",
      "chapter_index": 5,
      "last_error": "JSON parse error: unexpected token at position 1234",
      "attempts": 3
    }
  }
}
```

**错误码（Error Codes）**：

| Code | HTTP Status | Description |
|------|-------------|-------------|
| VALIDATION_ERROR | 400 | 请求参数校验失败 |
| NOT_FOUND | 404 | 资源不存在 |
| STAGE_FAILED | 500 | Stage 执行失败（含重试耗尽） |
| PROVIDER_ERROR | 502 | LLM Provider 调用失败 |
| RATE_LIMITED | 429 | 速率限制 |
| FILE_TOO_LARGE | 413 | 上传文件超过 50MB |
| UNSUPPORTED_FORMAT | 415 | 不支持的文件格式 |
| CONFIG_INVALID | 400 | 配置内容无效 |
| REGEX_INVALID | 400 | 自定义正则表达式非法 |
| REGEX_TIMEOUT | 422 | 自定义正则执行超时（触发安全保护） |
| PREVIEW_STALE | 409 | 切分预览已过期（规则或文本基线变化） |
| QUALITY_GATE_BLOCKED | 409 | 质量闸门阻断最终导出 |

**Chapter-level 失败聚合**：
- 失败章节列表记录在 Stage 的 `status.json` 中
- 前端展示："43/89 completed, 2 failed, 44 pending"
- 点击 "failed" 展开显示每章的错误详情
- 单章重试：`POST /api/v1/novels/{id}/stages/{stage}/chapters/{idx}/retry`

### 12. REST API 契约概览

**Base URL:** `/api/v1`

**Core Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | /novels/import | Upload and import novel file (multipart) |
| GET | /novels | List all novels |
| GET | /novels/{id} | Get novel detail + stage statuses |
| GET | /novels/{id}/tasks | List task history for one novel |
| POST | /novels/{id}/tasks | Create a new task (`from_scratch` / `clone_from_task`) |
| POST | /novels/{id}/stages/{stage}/run | Trigger a stage execution |
| GET | /novels/{id}/stages/{stage}/runs | List stage run history (run_seq desc) |
| POST | /novels/{id}/stages/{stage}/pause | Pause running stage |
| POST | /novels/{id}/stages/{stage}/resume | Resume paused stage |
| POST | /novels/{id}/stages/{stage}/retry | Retry failed stage |
| POST | /novels/{id}/stages/{stage}/chapters/{idx}/retry | Retry single chapter |
| GET/PUT | /split-rules | Get/update split rules（内置规则状态 + 自定义 regex） |
| POST | /split-rules/preview | Test split rules and return preview |
| POST | /novels/{id}/stages/split/confirm | Confirm split result with preview_token |
| GET | /novels/{id}/stages/{stage}/artifact | Download stage artifact（默认 latest，可选 `run_seq`） |
| GET | /novels/{id}/tasks/{task_id}/stages/{stage}/artifact | Download historical task artifact |
| GET | /novels/{id}/stages/{stage}/artifact?format=markdown | Download in specific format |
| GET | /novels/{id}/stages/{stage}/run | Stage run detail（含 config snapshot / warnings） |
| GET | /novels/{id}/chapters | List chapters with status |
| GET | /novels/{id}/chapters/{idx} | Get chapter detail (analysis, rewrites) |
| PUT | /novels/{id}/chapters/{idx}/analysis | Edit chapter analysis |
| PUT | /novels/{id}/chapters/{idx}/marks | Edit rewrite marks |
| PUT | /novels/{id}/chapters/{idx}/rewrites/{segment_id}/manual-edit | Edit accepted rewrite segment text |
| GET/POST/PUT/DELETE | /config/scene-rules | Scene rules CRUD |
| GET/POST/PUT/DELETE | /config/rewrite-rules | Rewrite rules CRUD |
| GET/PUT | /config/global-prompt | Global prompt config |
| POST | /config/ai-parse | AI Config Bar: parse natural language |
| POST | /config/ai-apply | AI Config Bar: apply parsed changes |
| POST | /config/export | Export config as JSON |
| POST | /config/import | Import config from JSON |
| GET/POST/PUT/DELETE | /providers | LLM provider CRUD（POST 按凭证 Upsert） |
| POST | /providers/models/fetch | Fetch models for unsaved provider draft (api_key + base_url) |
| POST | /providers/{id}/models/fetch | Re-fetch models for an existing provider |
| GET | /providers/{id}/models?q=keyword | List cached models with fuzzy search |
| POST | /providers/{id}/test | Test provider connection |
| GET | /novels/{id}/quality-report | Get assemble quality gate report |
| GET | /workers/status | Worker pool status |
| PUT | /workers/count | Adjust worker count |

Provider 保存规则：`POST /providers` 根据 `(provider_type, base_url, credential_fingerprint)` 执行 upsert。命中已存在记录时更新模型、temperature/max_tokens 等生成参数与限流参数，不创建重复 provider。

Stage 触发规则：`POST /novels/{id}/stages/{stage}/run` 支持 `run_idempotency_key`。相同 key 的重复触发返回同一 stage_run，不重复启动；新触发会生成新的 `run_seq` 历史记录。
切分确认规则：`POST /novels/{id}/stages/split/confirm` 需提交 `preview_token`；若规则版本或文本基线变化，返回 `PREVIEW_STALE`。

**WebSocket:** `ws://host/ws/progress`

Message format:
```json
{ "type": "stage_progress", "novel_id": "uuid", "stage": "analyze", "chapter_index": 5, "chapters_done": 43, "chapters_total": 89, "percentage": 48.3 }
{ "type": "chapter_completed", "novel_id": "uuid", "stage": "analyze", "chapter_index": 43 }
{ "type": "stage_completed", "novel_id": "uuid", "stage": "analyze", "duration_ms": 3600000 }
{ "type": "stage_failed", "novel_id": "uuid", "stage": "analyze", "error": "..." }
{ "type": "chapter_failed", "novel_id": "uuid", "stage": "analyze", "chapter_index": 5, "error": "...", "retries_exhausted": true }
{ "type": "task_paused", "novel_id": "uuid", "stage": "analyze", "at_chapter": 43 }
{ "type": "task_resumed", "novel_id": "uuid", "stage": "analyze", "resume_from_chapter": 44 }
{ "type": "stage_stale", "novel_id": "uuid", "stage": "rewrite", "caused_by": "analyze_rerun" }
{ "type": "worker_pool_status", "active_workers": 6, "idle_workers": 2, "queue_length": 14 }
```

- 客户端订阅：发送 `{ "type": "subscribe", "novel_id": "uuid" }`（或 `"novel_id": "*"` 订阅所有）
- 心跳：服务端每 30s 发送 `{ "type": "ping" }`，客户端响应 `{ "type": "pong" }`
- 重连策略：客户端使用指数退避（1s, 2s, 4s, 最大 30s）

**Authentication:** v1 无鉴权（单用户本地部署）。预留 `X-API-Key` Header 供未来多用户版本使用。

**Pagination:** 列表接口支持 `?page=1&per_page=20`。响应格式：`{ "data": [...], "total": 89, "page": 1, "per_page": 20 }`。

**Error Response:** 所有错误统一格式 `{ "error": { "code": "...", "message": "...", "details": {...} } }`，配合相应 HTTP 状态码。

## Frontend 三栏工作台（Low-Fi 决策）

为统一 Split / Analyze / Mark / Rewrite / Assemble 的交互路径，前端交互采用「左中右三栏工作台」统一骨架：

1. 左栏：章节导航（搜索、筛选、状态）。
2. 中栏：主工作区（原文/改写/Diff/最终稿）。
3. 右栏：智能侧栏（洞察/操作/日志）。

关键决策：

1. 各 Stage 共享同一布局和按钮状态机，避免“每页一套交互”。
2. 长内容采用区域内滚动，不拉长整页。
3. 改写对比采用 Git 风格红删绿增，支持并排与行内两种模式。
4. 右栏作为统一决策中心，承载风险解释、可执行操作、运行审计。
5. Rewrite 阶段中栏承担段落级审核主职责：完整原文、完整改写、微调输入与段级动作都在中栏闭环完成。
6. Rewrite 阶段右栏不再承载正文编辑，只保留洞察、章级动作、日志入口，避免中右信息重复与状态割裂。
7. `原文`/`改写稿` 视图强制使用不同数据源；当章节无有效改写时，改写稿视图显示空结果态和失败统计，不允许静默回退成原文同文展示。

低保真线框与逐屏/逐按钮/逐状态说明见：

- `openspec/changes/ai-novel-fullstack/frontend-three-pane-lowfi.md`

实施顺序约束（Local First）：

1. 先完成三栏工作台开发与本地联调验收。
2. 再完成前后端回归测试与验收。
3. 最后执行 Docker 化发布相关任务。

## Risks / Trade-offs

- **[LLM API 不稳定]** → Artifact 持久化保证崩溃后不丢失已完成的 Stage 结果，配合重试机制（指数退避）
- **[大文件处理内存压力]** → 逐章处理，Artifact 按章拆分存储（ch_001_analysis.json），不全量加载
- **[改写质量不可控]** → Stage 间天然有人工审核点，用户可在 Analyze 后导出报告审核、在 Mark 后调整标记再继续
- **[多模型 API 差异大]** → provider interface 抽象层隔离差异，每个 provider 独立维护
- **[前后端分离部署复杂度]** → 后端直接 serve 前端静态文件，简化部署为单进程服务
- **[并发过高触发速率限制]** → Worker Pool 内置 rate limiter，可按 provider 配置 RPM/TPM 限制
- **[Artifact 文件膨胀]** → 每次重跑 Stage 覆盖旧 Artifact（不做历史版本），单本小说 Artifact 总量预计 < 50MB
- **[Stage 间数据一致性]** → 重跑某 Stage 后下游标记为 stale，前端提示用户级联刷新，不自动覆盖

## Open Questions

- epub 解析是否需要支持图片和样式，还是只提取纯文本？（建议第一版只提取文本）
- 改写策略的粒度：按段落级别还是按句子级别标记和改写？（当前设计为段落级别）
- 人物跨章追踪是否需要自动合并同名/别名人物？（如"张三"和"老张"是否自动关联）
- Prompt 模板变量缺失时的默认策略是否启用 strict mode（缺失即报错）？还是保持默认空字符串以提升容错？
- Analyze Stage 是否应该拆分为两次 LLM 调用（一次提取事实信息，一次评估改写潜力），以提高单次输出的准确性？
