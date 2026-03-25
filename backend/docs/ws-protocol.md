# WebSocket Protocol

Endpoint: `ws://<host>/ws/progress`

## Transport Rules

- Client sends `subscribe` after connect.
- Client may send `unsubscribe` to stop receiving a novel stream.
- Server sends `ping` heartbeats.
- Client replies with `pong`.
- Messages are JSON objects with a `type` field.

## Client Messages

### `subscribe`

```json
{
  "type": "subscribe",
  "novel_id": "uuid"
}
```

Use `"novel_id": "*"` to subscribe to all novels.

### `unsubscribe`

```json
{
  "type": "unsubscribe",
  "novel_id": "uuid"
}
```

### `pong`

```json
{
  "type": "pong",
  "nonce": "optional"
}
```

## Server Messages

### `ping`

```json
{
  "type": "ping",
  "nonce": "optional"
}
```

### `stage_progress`

```json
{
  "type": "stage_progress",
  "novel_id": "uuid",
  "stage": "analyze",
  "chapters_done": 12,
  "chapters_total": 42,
  "percentage": 28.57
}
```

### `chapter_completed`

```json
{
  "type": "chapter_completed",
  "novel_id": "uuid",
  "stage": "rewrite",
  "chapter_index": 12
}
```

### `stage_completed`

```json
{
  "type": "stage_completed",
  "novel_id": "uuid",
  "stage": "split",
  "duration_ms": 125000
}
```

### `stage_failed`

```json
{
  "type": "stage_failed",
  "novel_id": "uuid",
  "stage": "rewrite",
  "error": "ANCHOR_MISMATCH"
}
```

### `chapter_failed`

```json
{
  "type": "chapter_failed",
  "novel_id": "uuid",
  "stage": "analyze",
  "chapter_index": 9,
  "error": "JSON schema validation failed",
  "retries_exhausted": true
}
```

### `task_paused`

```json
{
  "type": "task_paused",
  "novel_id": "uuid",
  "stage": "rewrite",
  "at_chapter": 43
}
```

### `task_resumed`

```json
{
  "type": "task_resumed",
  "novel_id": "uuid",
  "stage": "rewrite",
  "resume_from_chapter": 44
}
```

### `stage_stale`

```json
{
  "type": "stage_stale",
  "novel_id": "uuid",
  "stage": "assemble",
  "caused_by": "analyze_rerun"
}
```

### `worker_pool_status`

```json
{
  "type": "worker_pool_status",
  "active_workers": 6,
  "idle_workers": 2,
  "queue_length": 14
}
```

## Message Families

- `WsMessageType.SUBSCRIBE`
- `WsMessageType.UNSUBSCRIBE`
- `WsMessageType.PING`
- `WsMessageType.PONG`
- `WsMessageType.STAGE_PROGRESS`
- `WsMessageType.CHAPTER_COMPLETED`
- `WsMessageType.STAGE_COMPLETED`
- `WsMessageType.STAGE_FAILED`
- `WsMessageType.CHAPTER_FAILED`
- `WsMessageType.TASK_PAUSED`
- `WsMessageType.TASK_RESUMED`
- `WsMessageType.STAGE_STALE`
- `WsMessageType.WORKER_POOL_STATUS`

