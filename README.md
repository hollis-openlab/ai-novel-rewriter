[中文](README_zh.md)

# AI Novel - Intelligent Novel Rewriting Tool

An LLM-powered pipeline for novel content analysis, marking, and intelligent rewriting. Import your novel, and the system automatically analyzes scenes, marks rewrite targets, rewrites segment by segment with outline constraints and auto-review, then assembles the final output.

## Features

- **Novel Import & Auto-Splitting** — Import TXT format, automatically detect chapter boundaries
- **Intelligent Scene Analysis** — Configurable scene rules to identify scene types, character states, and key events
- **Auto-Mark Rewrite Segments** — Mark segments for rewriting based on analysis and rewrite rules; supports expand/rewrite/condense/preserve strategies
- **Chapter Rewrite Outline** — Generate a narrative outline per chapter before rewriting, defining each segment's role and boundaries to prevent plot drift
- **Serial Rewrite + Rolling Context** — Rewrite segment by segment, each seeing previously rewritten content for coherence
- **Auto-Review After Rewrite** — Automatically detect plot drift issues, locate problematic segments, and apply targeted fixes
- **Result Assembly & Export** — Assemble rewritten results with originals, export as TXT/EPUB
- **Real-time Progress** — WebSocket-pushed progress updates, live per-chapter/per-stage status on the frontend
- **Multi-Provider Support** — Works with any OpenAI-compatible API (DeepSeek, SiliconFlow, etc.)

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.13+, FastAPI, SQLAlchemy, SQLite |
| Frontend | React 19, TypeScript 5, Vite 6, TanStack Query |
| Deployment | Docker Compose |
| Package Managers | uv (Python), npm (Node) |

## Quick Start

### Docker Deployment (Recommended)

```bash
# Clone the project
git clone https://github.com/hollis-openlab/ai-novel-rewriter.git && cd ai-novel-rewriter

# Start
docker compose up -d

# Visit http://localhost:8899
```

> LLM Provider settings (API Key, Base URL) are configured in the app's Settings page — no environment variables needed.
> For custom port, data directory, or other service configs, run `cp .env.example .env` and edit as needed.

### Local Development

**Prerequisites**: Python 3.13+, Node.js 20+, [uv](https://docs.astral.sh/uv/)

```bash
# Install dependencies and start (frontend + backend)
./start.sh

# Backend: http://localhost:8899
# Frontend: http://localhost:5173
```

Stop services:

```bash
./stop.sh
```

### Environment Variables

See `.env.example` for reference:

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_NOVEL_PORT` | 8899 | Backend service port |
| `AI_NOVEL_DATA_DIR` | data | Data storage directory |
| `AI_NOVEL_CORS_ORIGINS` | localhost:5173 | CORS allowed origins (comma-separated) |
| `AI_NOVEL_LLM_TIMEOUT_SECONDS` | 600 | LLM request timeout (seconds) |
| `AI_NOVEL_DEBUG` | false | Debug mode |

LLM Provider API Key and Base URL are configured in the app's Settings page.

## Project Structure

```
AI-novel/
├── backend/           # FastAPI backend
│   ├── app/
│   │   ├── api/       # API routes
│   │   ├── services/  # Core pipeline (analyze, mark, outline, rewrite, review, assemble)
│   │   ├── llm/       # LLM client and prompt templates
│   │   ├── models/    # Data models
│   │   └── db/        # Database models and connections
│   └── tests/         # Backend tests
├── frontend/          # React frontend
│   └── src/
│       ├── pages/     # Page components
│       ├── components/# Shared components
│       └── lib/       # API client, WebSocket
├── docker-compose.yml # Production deployment
├── start.sh           # Local dev start script
└── stop.sh            # Local dev stop script
```

## Rewrite Pipeline

```
Import → Split → Analyze → Mark → Rewrite → Assemble
                                    ↓
                           [Outline → Serial Rewrite → Review → Fix]
```

## Running Tests

```bash
# Backend
uv run pytest backend/tests/ -v

# Frontend
cd frontend && npm test
```

## License

[AGPL-3.0](LICENSE)
