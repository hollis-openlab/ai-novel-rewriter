# AI Novel API Contract

## Conventions

- Base URL: `/api/v1`
- Error envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human readable message",
    "details": {
      "field": "file",
      "reason": "missing"
    }
  }
}
```

- Stage enum: `import | split | analyze | mark | rewrite | assemble`
- Stage status enum: `pending | running | completed | failed | paused | stale`

## Endpoints

### `POST /novels/import`

Multipart upload for TXT or EPUB.

Request:

- `file`: binary file, max 50 MB

Response: `202 Accepted`

```json
{
  "novel_id": "uuid",
  "task_id": "uuid",
  "meta": {
    "id": "uuid",
    "title": "Example Novel",
    "original_filename": "book.txt",
    "file_format": "txt",
    "file_size": 123456,
    "total_chars": 200000,
    "imported_at": "2026-03-20T12:00:00Z",
    "chapter_count": 0,
    "config_override_json": null
  },
  "stage_runs": []
}
```

### `GET /novels`

Returns paginated novel list.

Query:

- `page` default `1`
- `per_page` default `20`

### `GET /novels/{id}`

Returns novel detail and current stage pipeline status.

### `POST /novels/{id}/stages/{stage}/run`

Triggers a stage run.

Request:

```json
{
  "run_idempotency_key": "optional-uuid-or-client-key",
  "force": false
}
```

### `POST /novels/{id}/stages/{stage}/pause`

Pauses the current running stage.

### `POST /novels/{id}/stages/{stage}/resume`

Resumes a paused stage.

### `POST /novels/{id}/stages/{stage}/retry`

Retries a failed stage.

### `POST /novels/{id}/stages/split/confirm`

Confirms split results after preview.

Request:

```json
{
  "preview_token": "token-bound-to-source-revision-and-rules-version"
}
```

If the token is stale, the API returns `PREVIEW_STALE`.

### `GET /novels/{id}/chapters`

Lists chapters for the active task.

### `GET /providers`

Lists configured providers.

### `POST /providers`

Creates or updates a provider using credential fingerprint upsert semantics.

## Response Shapes

### Stage action response

```json
{
  "novel_id": "uuid",
  "task_id": "uuid",
  "stage": "split",
  "run": {
    "id": "uuid",
    "run_seq": 3,
    "stage": "split",
    "status": "running",
    "started_at": "2026-03-20T12:00:00Z",
    "completed_at": null,
    "error_message": null,
    "run_idempotency_key": "client-key",
    "warnings_count": 0,
    "chapters_total": 42,
    "chapters_done": 0,
    "config_snapshot": {
      "provider_id": "uuid",
      "provider_name": "OpenAI",
      "provider_type": "openai",
      "model_name": "gpt-4.1",
      "base_url": "https://api.openai.com/v1",
      "global_prompt_version": "v1",
      "scene_rules_hash": "sha256...",
      "rewrite_rules_hash": "sha256...",
      "generation_params": {}
    },
    "artifact_path": "data/novels/.../runs/3",
    "is_latest": true
  }
}
```

### Split preview

```json
{
  "preview_token": "preview-token",
  "novel_id": "uuid",
  "source_revision": "raw@sha256...",
  "rules_version": "rules@sha256...",
  "estimated_chapters": 42,
  "matched_lines": [
    { "line": 1, "rule_id": "builtin-chapter", "title": "第一章" }
  ],
  "boundary_hash": "sha256..."
}
```

## Notes

- `GET /novels/{id}/stages/{stage}/run` returns the latest run.
- `GET /novels/{id}/stages/{stage}/runs` is reserved for run history.
- `GET /novels/{id}/stages/{stage}/artifact` defaults to the latest run unless a `run_seq` is specified.
- `PUT /novels/{id}/chapters/{idx}/rewrites/{segment_id}/manual-edit` is reserved for later implementation of accepted rewrite fine-tuning.

