# AI Novel Backend

This directory contains the FastAPI backend skeleton for local-first development.

## Requirements

- Python 3.13
- `uv`

## Local run

```bash
uv sync
```

Then start the app:

```bash
uv run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8899
```

## OpenAPI export

```bash
uv run python -m backend.scripts.sync_openapi
```

This writes the schema to:
- `backend/docs/openapi.json`
- `frontend/src/types/api-schema.json`

## Environment variables

- `AI_NOVEL_DATA_DIR`: data root, defaults to `data`
- `AI_NOVEL_DATABASE_URL`: override DB URL (default `sqlite+aiosqlite:///data/ai-novel-backend.db`)
- `AI_NOVEL_HOST`: bind host, defaults to `0.0.0.0`
- `AI_NOVEL_PORT`: bind port, defaults to `8899`
- `AI_NOVEL_DEBUG`: enable debug mode
- `AI_NOVEL_CORS_ORIGINS`: comma-separated allowed origins

## What is included

- FastAPI app entrypoint and middleware
- Standardized error payloads
- API route skeletons for novels, providers, workers, stages, health, and artifact consistency checks
- Artifact store helper for `data/novels/{id}/tasks/{task_id}/stages`
- OpenAPI export script
