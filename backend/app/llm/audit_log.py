from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel


@dataclass(slots=True)
class PromptAuditEntry:
    call_id: str
    novel_id: str
    chapter_index: int
    stage: str
    attempt: int
    timestamp: str
    system_prompt: str
    user_prompt: str
    params: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model_name: str | None = None
    response: Any = None
    usage: Any = None
    validation: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


class PromptAuditLogger:
    def __init__(self, base_dir: Path | str = Path("logs") / "prompt_audit") -> None:
        self.base_dir = Path(base_dir)

    def chapter_path(self, novel_id: str, chapter_index: int) -> Path:
        return self.base_dir / novel_id / f"chapter-{chapter_index:04d}.jsonl"

    def append_entry(self, entry: PromptAuditEntry) -> Path:
        path = self.chapter_path(entry.novel_id, entry.chapter_index)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _json_safe(entry)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return path

    def record_call(
        self,
        *,
        novel_id: str,
        chapter_index: int,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        params: dict[str, Any] | None = None,
        provider: str = "",
        model_name: str | None = None,
        response: Any = None,
        usage: Any = None,
        validation: dict[str, Any] | None = None,
        duration_ms: int = 0,
        attempt: int = 1,
        call_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> PromptAuditEntry:
        entry = PromptAuditEntry(
            call_id=call_id or str(uuid4()),
            novel_id=novel_id,
            chapter_index=chapter_index,
            stage=stage,
            attempt=attempt,
            timestamp=(timestamp or datetime.now(timezone.utc)).isoformat(),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            params=dict(params or {}),
            provider=provider,
            model_name=model_name,
            response=_json_safe(response),
            usage=_json_safe(usage),
            validation=dict(validation or {}),
            duration_ms=duration_ms,
        )
        self.append_entry(entry)
        return entry

