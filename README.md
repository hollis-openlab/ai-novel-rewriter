# AI Novel - 小说智能改写工具

基于 LLM 的小说内容分析、标记和智能改写管道。导入小说原文后，自动分析场景、标记改写点，按照大纲约束逐段改写并自动 Review，最终组装导出。

## 功能特点

- **小说导入与自动分章** — 支持 TXT 格式导入，自动识别章节边界并分章
- **智能场景分析** — 基于可配置的场景规则，自动识别场景类型、人物状态、关键事件
- **自动标记改写片段** — 根据分析结果和改写规则，标记需要改写的片段，支持扩写/改写/缩写/保留等策略
- **章节改写大纲** — 改写前为每章生成叙事大纲，明确每个片段的职责和边界，防止剧情超跑
- **串行改写 + 滚动上下文** — 逐段改写，每段能看到前面已改写的内容，保证段落间连贯性
- **改写后自动 Review** — 改写完成后自动检测剧情超跑问题，定位问题段落并局部修复
- **结果组装与导出** — 将改写结果与原文组装，支持导出为 TXT/EPUB 格式
- **实时进度** — WebSocket 推送改写进度，前端实时展示每章每阶段状态
- **多 Provider 支持** — 支持任意 OpenAI 兼容 API（DeepSeek、硅基流动等）

## 技术栈

| 层 | 技术 |
|---|------|
| 后端 | Python 3.13+, FastAPI, SQLAlchemy, SQLite |
| 前端 | React 19, TypeScript 5, Vite 6, TanStack Query |
| 部署 | Docker Compose |
| 依赖管理 | uv (Python), npm (Node) |

## 快速开始

### Docker 部署（推荐）

```bash
# 克隆项目
git clone https://github.com/hollis-openlab/ai-novel-rewriter.git && cd ai-novel-rewriter

# 启动
docker compose up -d

# 访问 http://localhost:8899
```

> LLM Provider（API Key、Base URL）在应用内的「设置」页面配置，无需环境变量。
> 如需自定义端口、数据目录等服务配置，可 `cp .env.example .env` 后修改。

### 本地开发

**前置要求**: Python 3.13+, Node.js 20+, [uv](https://docs.astral.sh/uv/)

```bash
# 安装依赖并启动（前后端同时）
./start.sh

# 后端: http://localhost:8899
# 前端: http://localhost:5173
```

停止服务：

```bash
./stop.sh
```

### 环境变量

参考 `.env.example`，主要配置项：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AI_NOVEL_PORT` | 8899 | 后端服务端口 |
| `AI_NOVEL_DATA_DIR` | data | 数据存储目录 |
| `AI_NOVEL_CORS_ORIGINS` | localhost:5173 | CORS 允许域名（逗号分隔） |
| `AI_NOVEL_LLM_TIMEOUT_SECONDS` | 600 | LLM 请求超时（秒） |
| `AI_NOVEL_DEBUG` | false | 调试模式 |

LLM Provider 的 API Key 和 Base URL 在应用内的「设置」页面配置。

## 项目结构

```
AI-novel/
├── backend/           # FastAPI 后端
│   ├── app/
│   │   ├── api/       # API 路由
│   │   ├── services/  # 核心管道（分析、标记、大纲、改写、审核、组装）
│   │   ├── llm/       # LLM 客户端和 Prompt 模板
│   │   ├── models/    # 数据模型
│   │   └── db/        # 数据库模型和连接
│   └── tests/         # 后端测试
├── frontend/          # React 前端
│   └── src/
│       ├── pages/     # 页面组件
│       ├── components/# 通用组件
│       └── lib/       # API 客户端、WebSocket
├── docker-compose.yml # 生产部署
├── start.sh           # 本地开发启动脚本
└── stop.sh            # 本地开发停止脚本
```

## 改写管道流程

```
导入 → 分章 → 分析 → 标记 → 改写 → 组装
                              ↓
                     [生成大纲 → 串行改写 → Review → 修复]
```

## 运行测试

```bash
# 后端
uv run pytest backend/tests/ -v

# 前端
cd frontend && npm test
```

## License

[AGPL-3.0](LICENSE)
