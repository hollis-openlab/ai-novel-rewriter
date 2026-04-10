from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import ChapterAdjustResponse, ChapterRenameRequest, ChapterSplitRequest
from backend.app.api.schemas import PromptLogEntryResponse, PromptLogListResponse, PromptLogRetryResponse
from backend.app.api.redaction import sanitize_public_payload
from backend.app.contracts.api import ChapterListItem, ChapterListResponse, ChapterStageTiming
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db import Chapter as ChapterRow
from backend.app.db import ChapterState, StageRun, Task, get_db_session
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    Paragraph,
    RewriteAuditEntry,
    RewritePlan,
    RewriteResult,
    RewriteResultStatus,
    RewriteReviewAction,
    RewriteSegment,
    RewriteStrategy,
    StageName,
    StageStatus,
    WindowAttemptAction,
    WindowGuardrailLevel,
)
from backend.app.services import analyze_pipeline
from backend.app.services.marking import build_anchor, merge_manual_segments, replace_manual_segments, write_mark_artifacts

router = APIRouter(prefix="/novels/{novel_id}/chapters", tags=["chapters"])

REWRITE_STAGE_NAME = "rewrite"
CHAPTER_REWRITE_FILE_TEMPLATE = "ch_{chapter_index:03d}_rewrites.json"
REWRITE_AGGREGATE_FILENAME = "rewrites.json"
PROMPT_AUDIT_ROOT = Path("logs") / "prompt_audit"
PROMPT_AUDIT_FILE_TEMPLATE = "chapter-{chapter_index:04d}.jsonl"


def _paragraphs(content: str) -> list[Paragraph]:
    parts = [part.strip() for part in content.split("\n\n") if part.strip()]
    offset = 0
    paragraphs: list[Paragraph] = []
    for index, part in enumerate(parts, start=1):
        paragraphs.append(
            Paragraph(
                index=index,
                start_offset=offset,
                end_offset=offset + len(part),
                char_count=len(part),
            )
        )
        offset += len(part) + 2
    return paragraphs


def _chapter_from_row(row: ChapterRow) -> Chapter:
    return Chapter(
        id=row.id,
        index=row.chapter_index,
        title=row.title,
        content=row.content,
        char_count=row.char_count,
        paragraph_count=row.paragraph_count,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        paragraphs=_paragraphs(row.content),
    )


def _chapter_from_payload(payload: dict[str, object]) -> Chapter:
    content = str(payload["content"])
    return Chapter(
        id=str(payload["id"]),
        index=int(payload["index"]),
        title=str(payload["title"]),
        content=content,
        char_count=int(payload["char_count"]),
        paragraph_count=int(payload["paragraph_count"]),
        start_offset=int(payload["start_offset"]),
        end_offset=int(payload["end_offset"]),
        paragraphs=_paragraphs(content),
    )


async def _get_active_task_or_404(db: AsyncSession, novel_id: str) -> Task:
    row = (
        await db.execute(
            select(Task)
            .where(Task.novel_id == novel_id, Task.status == "active")
            .order_by(Task.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"Active task for novel `{novel_id}` not found",
            status.HTTP_404_NOT_FOUND,
        )
    return row


def _stage_split_paths(request: Request, novel_id: str, task_id: str) -> tuple[Path, Path]:
    store = request.app.state.artifact_store
    stage_dir = store.stage_dir(novel_id, task_id, "split")
    return stage_dir / "status.json", stage_dir / "chapters.json"


def _task_scoped_chapter_id(task_id: str, chapter_id: str) -> str:
    prefix = f"{task_id}:"
    if chapter_id.startswith(prefix):
        return chapter_id
    return f"{prefix}{chapter_id}"


def _empty_analysis() -> ChapterAnalysis:
    return ChapterAnalysis(summary="", characters=[], key_events=[], scenes=[], location="", tone="")


def _mark_plan_path(request: Request, novel_id: str, task_id: str) -> Path:
    store = request.app.state.artifact_store
    return store.stage_dir(novel_id, task_id, "mark") / "mark_plan.json"


def _rewrite_stage_dir(request: Request, novel_id: str, task_id: str) -> Path:
    store = request.app.state.artifact_store
    return store.stage_dir(novel_id, task_id, REWRITE_STAGE_NAME)


def _chapter_rewrite_path(request: Request, novel_id: str, task_id: str, chapter_idx: int) -> Path:
    return _rewrite_stage_dir(request, novel_id, task_id) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=chapter_idx)


def _rewrite_aggregate_path(request: Request, novel_id: str, task_id: str) -> Path:
    return _rewrite_stage_dir(request, novel_id, task_id) / REWRITE_AGGREGATE_FILENAME


def _prompt_audit_path(novel_id: str, chapter_idx: int) -> Path:
    return PROMPT_AUDIT_ROOT / novel_id / PROMPT_AUDIT_FILE_TEMPLATE.format(chapter_index=chapter_idx)


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _chapter_state_to_stage_status(raw: str) -> StageStatus:
    if raw == "running":
        return StageStatus.RUNNING
    if raw == "completed" or raw == "skipped":
        return StageStatus.COMPLETED
    if raw == "failed":
        return StageStatus.FAILED
    return StageStatus.PENDING


def _normalize_stage_status(
    raw: str | StageStatus,
    *,
    chapters_total: int = 0,
    chapters_done: int = 0,
    completed_at: object | None = None,
) -> StageStatus:
    status = raw if isinstance(raw, StageStatus) else StageStatus(str(raw))
    if status == StageStatus.STALE:
        return status  # Return STALE as-is so the frontend can display it
    return status


def _rewrite_stage_status_from_results(results: list[RewriteResult]) -> StageStatus:
    if not results:
        # Explicitly persisted empty chapter rewrite payload means "adopt original".
        return StageStatus.COMPLETED

    statuses = {item.status for item in results}
    if RewriteResultStatus.FAILED in statuses:
        return StageStatus.FAILED

    terminal = {
        RewriteResultStatus.COMPLETED,
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
        RewriteResultStatus.REJECTED,
    }
    if statuses.issubset(terminal):
        return StageStatus.COMPLETED
    if RewriteResultStatus.PENDING in statuses and any(status in terminal for status in statuses):
        return StageStatus.RUNNING
    return StageStatus.PENDING


def _derive_chapter_stage_status_from_run(run: StageRun, *, chapter_pos: int, total_chapters: int) -> StageStatus:
    status = _normalize_stage_status(
        run.status,
        chapters_total=run.chapters_total or total_chapters,
        chapters_done=run.chapters_done or 0,
        completed_at=run.completed_at,
    )
    done = max(0, min(total_chapters, int(run.chapters_done or 0)))
    if status == StageStatus.COMPLETED:
        return StageStatus.COMPLETED
    if status == StageStatus.RUNNING:
        if chapter_pos < done:
            return StageStatus.COMPLETED
        if chapter_pos == done:
            return StageStatus.RUNNING
        return StageStatus.PENDING
    if status in {StageStatus.FAILED, StageStatus.PAUSED}:
        if chapter_pos < done:
            return StageStatus.COMPLETED
        if chapter_pos == done:
            return status
        return StageStatus.PENDING
    return StageStatus.PENDING


def _aggregate_chapter_status(stages: dict[StageName, StageStatus]) -> StageStatus:
    priority = [
        StageStatus.FAILED,
        StageStatus.RUNNING,
        StageStatus.PAUSED,
        StageStatus.PENDING,
        StageStatus.COMPLETED,
    ]
    for item in priority:
        if any(status == item for status in stages.values()):
            return item
    return StageStatus.PENDING


def _prompt_log_entry_from_payload(payload: dict[str, Any]) -> PromptLogEntryResponse:
    usage = _coerce_dict(payload.get("usage"))
    validation = _coerce_dict(payload.get("validation"))
    tokens = {
        "prompt_tokens": _coerce_int(
            usage.get("prompt_tokens")
            if "prompt_tokens" in usage
            else usage.get("input_tokens")
        ),
        "completion_tokens": _coerce_int(
            usage.get("completion_tokens")
            if "completion_tokens" in usage
            else usage.get("output_tokens")
        ),
        "total_tokens": _coerce_int(
            usage.get("total_tokens")
            if "total_tokens" in usage
            else usage.get("tokens")
        ),
    }
    return PromptLogEntryResponse(
        call_id=str(payload.get("call_id") or ""),
        novel_id=str(payload.get("novel_id") or ""),
        chapter_index=int(payload.get("chapter_index") or 0),
        stage=str(payload.get("stage") or ""),
        attempt=max(1, int(payload.get("attempt") or 1)),
        timestamp=payload.get("timestamp") or datetime.utcnow(),
        provider=str(payload.get("provider") or ""),
        model_name=payload.get("model_name"),
        duration_ms=max(0, int(payload.get("duration_ms") or 0)),
        system_prompt=str(payload.get("system_prompt") or ""),
        user_prompt=str(payload.get("user_prompt") or ""),
        response=payload.get("response"),
        params=_coerce_dict(payload.get("params")),
        usage=usage,
        tokens=tokens,
        validation={
            "passed": validation.get("passed"),
            "error_code": validation.get("error_code"),
            "error_message": validation.get("error_message"),
            "details": _coerce_dict(validation.get("details")),
        },
    )


def _load_prompt_log_entries(novel_id: str, chapter_idx: int) -> list[PromptLogEntryResponse]:
    path = _prompt_audit_path(novel_id, chapter_idx)
    if not path.exists():
        return []

    entries: list[PromptLogEntryResponse] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            entries.append(_prompt_log_entry_from_payload(payload))
        except Exception:
            continue
    return entries


def _load_rewrite_plan(path: Path) -> RewritePlan:
    return RewritePlan.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _chapter_mark_segments(plan: RewritePlan, chapter_idx: int) -> list[dict[str, object]]:
    chapter = next((item for item in plan.chapters if item.chapter_index == chapter_idx), None)
    if chapter is None:
        return []
    return [segment.model_dump(mode="json") for segment in chapter.segments]


def _rewrite_result_payload(result: RewriteResult) -> dict[str, object]:
    return result.model_dump(mode="json")


def _rewrite_window_metrics(segments: list[RewriteResult]) -> dict[str, int]:
    windows_total = 0
    windows_retried = 0
    windows_hard_failed = 0
    windows_rollback = 0

    for item in segments:
        windows = list(item.rewrite_windows or [])
        attempts_by_window: dict[str, list[Any]] = {}
        for attempt in list(item.window_attempts or []):
            attempts_by_window.setdefault(attempt.window_id, []).append(attempt)

        if not windows:
            # Backward compatibility: old artifacts without window fields are
            # counted as one logical window per segment.
            windows_total += 1
            continue

        windows_total += len(windows)
        for window in windows:
            attempts = attempts_by_window.get(window.window_id, [])
            if len(attempts) > 1:
                windows_retried += 1
            if attempts and attempts[-1].action == WindowAttemptAction.ROLLBACK_ORIGINAL:
                windows_rollback += 1
            if any(
                attempt.guardrail is not None and attempt.guardrail.level == WindowGuardrailLevel.HARD_FAIL
                for attempt in attempts
            ):
                windows_hard_failed += 1

    return {
        "windows_total": windows_total,
        "windows_retried": windows_retried,
        "windows_hard_failed": windows_hard_failed,
        "windows_rollback": windows_rollback,
    }


def _rewrite_payload_status_fields(segments: list[RewriteResult]) -> dict[str, Any]:
    if not segments:
        return {
            "rewrite_status": "completed",
            "completion_kind": "noop",
            "reason_code": "NO_REWRITE_WINDOW",
            "has_warnings": False,
            "warning_count": 0,
            "warning_codes": [],
            "windows_total": 0,
            "windows_retried": 0,
            "windows_hard_failed": 0,
            "windows_rollback": 0,
        }

    terminal = {
        RewriteResultStatus.COMPLETED,
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
        RewriteResultStatus.REJECTED,
    }
    statuses = {item.status for item in segments}
    if RewriteResultStatus.FAILED in statuses:
        rewrite_status = "failed"
    elif statuses.issubset(terminal):
        rewrite_status = "completed"
    elif RewriteResultStatus.PENDING in statuses:
        rewrite_status = "running"
    else:
        rewrite_status = "pending"

    warning_codes: list[str] = []
    warning_count = 0
    for item in segments:
        codes = list(item.warning_codes or [])
        if not codes and item.error_code:
            codes = [item.error_code]
        warning_count += max(int(item.warning_count or 0), len(codes))
        for code in codes:
            if code and code not in warning_codes:
                warning_codes.append(code)

    return {
        "rewrite_status": rewrite_status,
        "completion_kind": "normal",
        "reason_code": None,
        "has_warnings": warning_count > 0,
        "warning_count": warning_count,
        "warning_codes": warning_codes,
        **_rewrite_window_metrics(segments),
    }


def _load_rewrite_chapter_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rewrite_results(path: Path) -> list[RewriteResult]:
    payload = _load_rewrite_chapter_artifact(path)
    return [RewriteResult.model_validate(item) for item in list(payload.get("segments", []))]


def _rewrite_chapter_payload(
    *,
    novel_id: str,
    task_id: str,
    chapter_idx: int,
    segments: list[RewriteResult],
    audit_trail: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status_fields = _rewrite_payload_status_fields(segments)
    return {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_index": chapter_idx,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "segments": [_rewrite_result_payload(segment) for segment in segments],
        "audit_trail": audit_trail or [],
        **status_fields,
    }


def _write_rewrite_chapter_artifact(
    request: Request,
    novel_id: str,
    task_id: str,
    chapter_idx: int,
    segments: list[RewriteResult],
    *,
    audit_trail: list[dict[str, Any]] | None = None,
) -> Path:
    path = _chapter_rewrite_path(request, novel_id, task_id, chapter_idx)
    request.app.state.artifact_store.ensure_json(
        path,
        _rewrite_chapter_payload(
            novel_id=novel_id,
            task_id=task_id,
            chapter_idx=chapter_idx,
            segments=segments,
            audit_trail=audit_trail,
        ),
    )
    return path


def _rebuild_rewrite_aggregate(request: Request, novel_id: str, task_id: str) -> Path:
    stage_dir = _rewrite_stage_dir(request, novel_id, task_id)
    stage_dir.mkdir(parents=True, exist_ok=True)
    chapters: list[dict[str, Any]] = []
    for path in sorted(stage_dir.glob("ch_*_rewrites.json")):
        chapters.append(_load_rewrite_chapter_artifact(path))
    chapters.sort(key=lambda item: int(item.get("chapter_index", 0)))
    aggregate = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_count": len(chapters),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "chapters": chapters,
    }
    aggregate_path = _rewrite_aggregate_path(request, novel_id, task_id)
    request.app.state.artifact_store.ensure_json(aggregate_path, aggregate)
    return aggregate_path


def _load_rewrite_chapter_results(request: Request, novel_id: str, task_id: str, chapter_idx: int) -> list[RewriteResult] | None:
    path = _chapter_rewrite_path(request, novel_id, task_id, chapter_idx)
    if not path.exists():
        return None
    return _load_rewrite_results(path)


async def _bootstrap_rewrite_chapter_artifact_from_mark_plan(
    request: Request,
    db: AsyncSession,
    *,
    novel_id: str,
    task: Task,
    chapter_idx: int,
) -> Path:
    path = _chapter_rewrite_path(request, novel_id, task.id, chapter_idx)
    if path.exists():
        return path

    plan_path = _mark_plan_path(request, novel_id, task.id)
    if not plan_path.exists():
        request.app.state.artifact_store.ensure_json(
            path,
            _rewrite_chapter_payload(
                novel_id=novel_id,
                task_id=task.id,
                chapter_idx=chapter_idx,
                segments=[],
                audit_trail=[],
            ),
        )
        _rebuild_rewrite_aggregate(request, novel_id, task.id)
        return path

    plan = _load_rewrite_plan(plan_path)
    chapter_plan = next((item for item in plan.chapters if item.chapter_index == chapter_idx), None)
    if chapter_plan is None:
        request.app.state.artifact_store.ensure_json(
            path,
            _rewrite_chapter_payload(
                novel_id=novel_id,
                task_id=task.id,
                chapter_idx=chapter_idx,
                segments=[],
                audit_trail=[],
            ),
        )
        _rebuild_rewrite_aggregate(request, novel_id, task.id)
        return path

    _, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    chapter = _chapter_for_index(payloads, chapter_idx)
    segments = [_rewrite_preview_from_mark_segment(chapter, segment) for segment in chapter_plan.segments]
    _write_rewrite_chapter_artifact(
        request,
        novel_id,
        task.id,
        chapter_idx,
        segments,
        audit_trail=[],
    )
    _rebuild_rewrite_aggregate(request, novel_id, task.id)
    return path


class ChapterUpdateAnalysisResponse(BaseModel):
    status: str = "updated"
    chapter_idx: int
    chapter_id: str | None = None
    chapter_title: str = ""
    stale_stages: list[str] = Field(default_factory=list)


class ChapterUpdateMarksRequest(BaseModel):
    mode: Literal["merge", "replace"] = Field(default="merge")
    segments: list[dict[str, object]] = Field(default_factory=list)


class ChapterUpdateMarksResponse(BaseModel):
    status: str = "updated"
    chapter_idx: int
    total_marked: int


class CharacterTrajectoryResponse(BaseModel):
    novel_id: str
    task_id: str
    character_name: str
    total: int
    data: list[dict[str, object]]


class RewriteReviewRequest(BaseModel):
    action: RewriteReviewAction
    rewritten_text: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_review_payload(self) -> "RewriteReviewRequest":
        if self.action == RewriteReviewAction.EDIT and not self.rewritten_text:
            raise ValueError("rewritten_text is required when action is edit")
        return self


class RewriteReviewResponse(BaseModel):
    status: RewriteResultStatus
    chapter_idx: int
    segment_id: str
    artifact_path: str
    audit_entries: int = Field(ge=0)
    stale_stages: list[str] = Field(default_factory=list)


def _normalize_payloads(task_id: str, payloads: list[dict[str, object]]) -> list[dict[str, object]]:
    offset = 0
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, payload in enumerate(payloads, start=1):
        content = str(payload["content"])
        chapter = dict(payload)
        chapter_id = _task_scoped_chapter_id(task_id, str(chapter.get("id") or f"chapter-{index}"))
        if chapter_id in seen_ids:
            chapter_id = f"{chapter_id}-{index}"
            while chapter_id in seen_ids:
                chapter_id = f"{chapter_id}-{index}"
        seen_ids.add(chapter_id)
        chapter["id"] = chapter_id
        chapter["index"] = index
        chapter["char_count"] = len(content)
        chapter["paragraph_count"] = len([part for part in content.split("\n\n") if part.strip()])
        chapter["start_offset"] = offset
        chapter["end_offset"] = offset + len(content)
        chapter["paragraphs"] = [paragraph.model_dump() for paragraph in _paragraphs(content)]
        normalized.append(chapter)
        offset = chapter["end_offset"] + 2
    return normalized


def _make_split_chapter_id(base_id: str, existing_ids: set[str]) -> str:
    candidate = f"{base_id}-split-{uuid4().hex[:8]}"
    while candidate in existing_ids:
        candidate = f"{base_id}-split-{uuid4().hex[:8]}"
    return candidate


async def _load_chapter_payloads(
    request: Request,
    db: AsyncSession,
    novel_id: str,
) -> tuple[Task, list[dict[str, object]], bool]:
    task = await _get_active_task_or_404(db, novel_id)
    rows = (
        await db.execute(
            select(ChapterRow)
            .where(ChapterRow.task_id == task.id)
            .order_by(ChapterRow.chapter_index.asc())
        )
    ).scalars().all()
    if rows:
        return task, [
            {
                "id": row.id,
                "index": row.chapter_index,
                "title": row.title,
                "content": row.content,
                "char_count": row.char_count,
                "paragraph_count": row.paragraph_count,
                "start_offset": row.start_offset,
                "end_offset": row.end_offset,
            }
            for row in rows
        ], True

    _, chapters_path = _stage_split_paths(request, novel_id, task.id)
    if chapters_path.exists():
        payload = json.loads(chapters_path.read_text(encoding="utf-8"))
        return task, _normalize_payloads(task.id, list(payload.get("chapters", []))), False

    return task, [], False


async def _persist_chapters(
    request: Request,
    db: AsyncSession,
    novel_id: str,
    task: Task,
    payloads: list[dict[str, object]],
) -> ChapterAdjustResponse:
    normalized = _normalize_payloads(task.id, payloads)
    await db.execute(delete(ChapterRow).where(ChapterRow.task_id == task.id))
    db.add_all(
        [
            ChapterRow(
                id=str(payload["id"]),
                task_id=task.id,
                chapter_index=int(payload["index"]),
                title=str(payload["title"]),
                content=str(payload["content"]),
                start_offset=int(payload["start_offset"]),
                end_offset=int(payload["end_offset"]),
                char_count=int(payload["char_count"]),
                paragraph_count=int(payload["paragraph_count"]),
            )
            for payload in normalized
        ]
    )
    await db.commit()

    store = request.app.state.artifact_store
    status_path, chapters_path = _stage_split_paths(request, novel_id, task.id)
    store.ensure_json(
        chapters_path,
        {
            "novel_id": novel_id,
            "task_id": task.id,
            "status": "completed",
            "chapters": normalized,
        },
    )
    store.ensure_json(
        status_path,
        {
            "novel_id": novel_id,
            "task_id": task.id,
            "status": "completed",
            "chapter_count": len(normalized),
        },
    )

    return ChapterAdjustResponse(
        novel_id=novel_id,
        task_id=task.id,
        total=len(normalized),
        data=[_chapter_from_payload(payload) for payload in normalized],
    )


async def _mark_stage_runs_stale(db: AsyncSession, task_id: str, stage_names: tuple[str, ...]) -> list[str]:
    # Product decision: downstream stage statuses are no longer marked "stale".
    # We preserve existing stage run statuses and rely on explicit reruns instead.
    _ = (db, task_id, stage_names)
    return []


async def _mark_downstream_stages_stale(db: AsyncSession, task_id: str) -> list[str]:
    return await _mark_stage_runs_stale(db, task_id, ("mark", "rewrite", "assemble"))


def _chapter_for_index(payloads: list[dict[str, object]], chapter_idx: int) -> Chapter:
    for payload in payloads:
        if int(payload["index"]) == chapter_idx:
            return _chapter_from_payload(payload)
    raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)


def _segment_range_text(chapter: Chapter, paragraph_range: tuple[int, int]) -> str:
    start, end = paragraph_range
    parts = [part.strip() for part in chapter.content.split("\n\n") if part.strip()]
    if start < 1 or end < start or end > len(parts):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "paragraph_range is outside chapter bounds",
            status.HTTP_400_BAD_REQUEST,
            details={"paragraph_range": list(paragraph_range), "paragraph_count": len(parts)},
        )
    return "\n\n".join(parts[start - 1 : end])


def _coerce_rewrite_segment(raw: dict[str, object], *, chapter: Chapter) -> RewriteSegment:
    try:
        return RewriteSegment.model_validate(raw)
    except Exception:
        paragraph_range_raw = raw.get("paragraph_range")
        if not isinstance(paragraph_range_raw, (list, tuple)) or len(paragraph_range_raw) != 2:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "segment paragraph_range must be a [start, end] tuple",
                status.HTTP_400_BAD_REQUEST,
            )
        paragraph_range = (int(paragraph_range_raw[0]), int(paragraph_range_raw[1]))
        original_text = _segment_range_text(chapter, paragraph_range)
        original_chars = len(original_text)
        target_ratio = float(raw.get("target_ratio") or 1.2)
        target_chars = int(raw.get("target_chars") or max(1, round(original_chars * target_ratio)))
        target_chars_min = int(raw.get("target_chars_min") or max(1, round(target_chars * 0.88)))
        target_chars_max = int(raw.get("target_chars_max") or max(target_chars_min, round(target_chars * 1.12)))
        strategy_value = str(raw.get("strategy") or RewriteStrategy.REWRITE.value)
        try:
            strategy = RewriteStrategy(strategy_value)
        except ValueError:
            strategy = RewriteStrategy.REWRITE
        source_value = str(raw.get("source") or "manual")
        if source_value not in {"auto", "manual"}:
            source_value = "manual"

        return RewriteSegment(
            segment_id=str(raw.get("segment_id") or uuid4()),
            paragraph_range=paragraph_range,
            anchor=build_anchor(chapter, paragraph_range),
            scene_type=str(raw.get("scene_type") or "手动标记"),
            original_chars=original_chars,
            strategy=strategy,
            target_ratio=target_ratio,
            target_chars=target_chars,
            target_chars_min=target_chars_min,
            target_chars_max=target_chars_max,
            suggestion=str(raw.get("suggestion") or ""),
            source=source_value,  # type: ignore[arg-type]
            confirmed=bool(raw.get("confirmed", True)),
        )


def _rewrite_preview_from_mark_segment(chapter: Chapter, segment: RewriteSegment) -> RewriteResult:
    original_text = _segment_range_text(chapter, segment.paragraph_range)
    return RewriteResult(
        segment_id=segment.segment_id,
        chapter_index=chapter.index,
        paragraph_range=segment.paragraph_range,
        char_offset_range=segment.char_offset_range,
        rewrite_windows=list(segment.rewrite_windows or []),
        scene_type=segment.scene_type,
        suggestion=segment.suggestion,
        target_ratio=segment.target_ratio,
        target_chars=segment.target_chars,
        target_chars_min=segment.target_chars_min,
        target_chars_max=segment.target_chars_max,
        completion_kind="normal",
        has_warnings=False,
        warning_count=0,
        warning_codes=[],
        anchor_verified=True,
        strategy=segment.strategy,
        original_text=original_text,
        rewritten_text="",
        original_chars=segment.original_chars,
        rewritten_chars=0,
        actual_chars=0,
        status=RewriteResultStatus.PENDING,
        attempts=0,
        provider_used=None,
        error_code=None,
        error_detail=None,
        provider_raw_response=None,
        validation_details=None,
        manual_edited_text=None,
        rollback_snapshot=None,
        audit_trail=[],
    )


async def _load_rewrite_results_for_display(
    request: Request,
    db: AsyncSession,
    novel_id: str,
    chapter_idx: int,
) -> list[dict[str, Any]]:
    task = await _get_active_task_or_404(db, novel_id)
    rewrite_results = _load_rewrite_chapter_results(request, novel_id, task.id, chapter_idx)
    if rewrite_results is not None:
        plan_path = _mark_plan_path(request, novel_id, task.id)
        if plan_path.exists():
            try:
                plan = _load_rewrite_plan(plan_path)
                chapter_plan = next((item for item in plan.chapters if item.chapter_index == chapter_idx), None)
                if chapter_plan is not None:
                    by_segment_id = {segment.segment_id: segment for segment in chapter_plan.segments}
                    by_range = {tuple(segment.paragraph_range): segment for segment in chapter_plan.segments}
                    enriched: list[RewriteResult] = []
                    matched_segment_ids: set[str] = set()
                    matched_ranges: set[tuple[int, int]] = set()
                    for result in rewrite_results:
                        plan_segment = by_segment_id.get(result.segment_id) or by_range.get(tuple(result.paragraph_range))
                        if plan_segment is None:
                            enriched.append(result)
                            continue
                        matched_segment_ids.add(plan_segment.segment_id)
                        matched_ranges.add(tuple(plan_segment.paragraph_range))
                        enriched.append(
                            result.model_copy(
                                update={
                                    "scene_type": result.scene_type or plan_segment.scene_type,
                                    "suggestion": result.suggestion if result.suggestion is not None else plan_segment.suggestion,
                                    "target_ratio": result.target_ratio if result.target_ratio is not None else plan_segment.target_ratio,
                                    "target_chars": result.target_chars if result.target_chars is not None else plan_segment.target_chars,
                                    "target_chars_min": result.target_chars_min if result.target_chars_min is not None else plan_segment.target_chars_min,
                                    "target_chars_max": result.target_chars_max if result.target_chars_max is not None else plan_segment.target_chars_max,
                                }
                            )
                        )
                    missing_segments = [
                        segment
                        for segment in chapter_plan.segments
                        if segment.segment_id not in matched_segment_ids and tuple(segment.paragraph_range) not in matched_ranges
                    ]
                    if missing_segments:
                        _, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
                        chapter = _chapter_for_index(payloads, chapter_idx)
                        enriched.extend(_rewrite_preview_from_mark_segment(chapter, segment) for segment in missing_segments)
                    rewrite_results = enriched
            except Exception:
                # Display fallback should remain resilient even if mark artifact is temporarily malformed.
                pass
        return [sanitize_public_payload(result.model_dump(mode="json")) for result in rewrite_results]

    plan_path = _mark_plan_path(request, novel_id, task.id)
    if not plan_path.exists():
        return []

    _, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    chapter = _chapter_for_index(payloads, chapter_idx)
    plan = _load_rewrite_plan(plan_path)
    chapter_plan = next((item for item in plan.chapters if item.chapter_index == chapter_idx), None)
    if chapter_plan is None:
        return []
    return [sanitize_public_payload(_rewrite_preview_from_mark_segment(chapter, segment).model_dump(mode="json")) for segment in chapter_plan.segments]


@router.get("", response_model=ChapterListResponse)
async def list_chapters(
    novel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterListResponse:
    task, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    chapters = [_chapter_from_payload(payload) for payload in payloads]
    if not chapters:
        return ChapterListResponse(
            novel_id=novel_id,
            task_id=task.id,
            total=0,
            data=[],
        )

    stage_rows = (
        await db.execute(
            select(StageRun)
            .where(StageRun.task_id == task.id)
            .order_by(StageRun.stage.asc(), StageRun.run_seq.desc())
        )
    ).scalars().all()
    stage_runs_by_stage: dict[str, list[StageRun]] = {}
    for row in stage_rows:
        stage_runs_by_stage.setdefault(row.stage, []).append(row)

    all_run_ids = [row.id for row in stage_rows]
    chapter_states_by_run: dict[str, dict[int, str]] = {}
    chapter_state_objects_by_run: dict[str, dict[int, ChapterState]] = {}
    if all_run_ids:
        chapter_state_rows = (
            await db.execute(
                select(ChapterState).where(ChapterState.stage_run_id.in_(all_run_ids))
            )
        ).scalars().all()
        for row in chapter_state_rows:
            chapter_states_by_run.setdefault(row.stage_run_id, {})[int(row.chapter_index)] = str(row.status)
            chapter_state_objects_by_run.setdefault(row.stage_run_id, {})[int(row.chapter_index)] = row

    rewrite_expected_segments: dict[int, int] = {chapter.index: 0 for chapter in chapters}
    mark_plan_exists = False
    plan_path = _mark_plan_path(request, novel_id, task.id)
    if plan_path.exists():
        try:
            plan = _load_rewrite_plan(plan_path)
            mark_plan_exists = True
            for chapter_plan in plan.chapters:
                rewrite_expected_segments[int(chapter_plan.chapter_index)] = len(chapter_plan.segments)
        except Exception:
            mark_plan_exists = False

    sorted_chapters = sorted(chapters, key=lambda item: item.index)
    chapter_pos = {chapter.index: idx for idx, chapter in enumerate(sorted_chapters)}
    rewrite_artifact_status_by_chapter: dict[int, StageStatus] = {}
    for chapter in sorted_chapters:
        rewrite_results = _load_rewrite_chapter_results(request, novel_id, task.id, chapter.index)
        if rewrite_results is None:
            continue
        rewrite_artifact_status_by_chapter[chapter.index] = _rewrite_stage_status_from_results(rewrite_results)

    chapter_items: list[ChapterListItem] = []
    for chapter in sorted_chapters:
        per_stage: dict[StageName, StageStatus] = {}
        for stage in StageName:
            runs_for_stage = stage_runs_by_stage.get(stage.value, [])
            if not runs_for_stage:
                if (
                    stage == StageName.REWRITE
                    and mark_plan_exists
                    and rewrite_expected_segments.get(chapter.index, 0) == 0
                ):
                    per_stage[stage] = StageStatus.COMPLETED
                else:
                    per_stage[stage] = StageStatus.COMPLETED if stage == StageName.IMPORT else StageStatus.PENDING
                continue
            latest_run = runs_for_stage[0]
            normalized_latest_status = _normalize_stage_status(
                latest_run.status,
                chapters_total=latest_run.chapters_total or 0,
                chapters_done=latest_run.chapters_done or 0,
                completed_at=latest_run.completed_at,
            )
            if stage == StageName.ASSEMBLE:
                # Assemble is a global stage, not a chapter-level stage.
                per_stage[stage] = normalized_latest_status
                continue
            latest_state_map = chapter_states_by_run.get(latest_run.id) or {}
            state_value = latest_state_map.get(chapter.index)
            if state_value is not None:
                state_status = _chapter_state_to_stage_status(state_value)
                # If the latest run is paused, persisted per-chapter `running`
                # states should render as paused in chapter list until resumed.
                if state_status == StageStatus.RUNNING and normalized_latest_status == StageStatus.PAUSED:
                    per_stage[stage] = StageStatus.PAUSED
                else:
                    per_stage[stage] = state_status
                continue
            if (
                stage == StageName.REWRITE
                and mark_plan_exists
                and rewrite_expected_segments.get(chapter.index, 0) == 0
            ):
                # Chapters without marked rewrite segments are effectively
                # "adopt original", so rewrite stage should be considered done.
                per_stage[stage] = StageStatus.COMPLETED
                continue
            if stage == StageName.REWRITE:
                artifact_status = rewrite_artifact_status_by_chapter.get(chapter.index)
                if artifact_status is not None:
                    per_stage[stage] = artifact_status
                    continue
            if latest_state_map:
                # Once per-chapter states are available, avoid linear `chapters_done`
                # fallback that can incorrectly mark earlier chapters as completed.
                per_stage[stage] = StageStatus.PENDING
                continue
            if normalized_latest_status == StageStatus.FAILED:
                # Stage-level failed runs may not have per-chapter states (for
                # example, mark plan build fails before any chapter is marked as
                # running). In that case, avoid falsely pinning the first
                # chapter to failed/risk; only preserve completed progress.
                chapters_done = max(0, min(len(sorted_chapters), int(latest_run.chapters_done or 0)))
                per_stage[stage] = StageStatus.COMPLETED if chapter_pos.get(chapter.index, 0) < chapters_done else StageStatus.PENDING
                continue
            per_stage[stage] = _derive_chapter_stage_status_from_run(
                latest_run,
                chapter_pos=chapter_pos.get(chapter.index, 0),
                total_chapters=len(sorted_chapters),
            )
        # Build per-stage timing from ChapterState objects
        timings: dict[StageName, ChapterStageTiming] = {}
        for stage_name_val, runs in stage_runs_by_stage.items():
            if not runs:
                continue
            latest_run_for_timing = runs[0]
            state_objs = chapter_state_objects_by_run.get(latest_run_for_timing.id) or {}
            state_obj = state_objs.get(chapter.index)
            if state_obj is not None:
                timings[StageName(stage_name_val)] = ChapterStageTiming(
                    started_at=state_obj.started_at.isoformat() if state_obj.started_at else None,
                    completed_at=state_obj.completed_at.isoformat() if state_obj.completed_at else None,
                )
        chapter_items.append(
            ChapterListItem(
                **chapter.model_dump(mode="json"),
                status=_aggregate_chapter_status(per_stage),
                stages=per_stage,
                stage_timings=timings,
            )
        )

    return ChapterListResponse(
        novel_id=novel_id,
        task_id=task.id,
        total=len(chapter_items),
        data=chapter_items,
    )


@router.get("/characters/{character_name}/trajectory", response_model=CharacterTrajectoryResponse)
async def get_character_trajectory(
    novel_id: str,
    character_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> CharacterTrajectoryResponse:
    task = await _get_active_task_or_404(db, novel_id)
    aggregate = analyze_pipeline.load_analysis_aggregate(request.app.state.artifact_store, novel_id, task.id)
    data = analyze_pipeline.build_character_trajectory(aggregate, character_name)
    data.sort(key=lambda item: int(item.get("chapter_index") or 0))
    return CharacterTrajectoryResponse(
        novel_id=novel_id,
        task_id=task.id,
        character_name=character_name,
        total=len(data),
        data=data,
    )


@router.get("/{chapter_idx}", response_model=Chapter)
async def get_chapter(
    novel_id: str,
    chapter_idx: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Chapter:
    task, payloads, from_db = await _load_chapter_payloads(request, db, novel_id)
    if from_db:
        row = (
            await db.execute(
                select(ChapterRow)
                .where(ChapterRow.task_id == task.id, ChapterRow.chapter_index == chapter_idx)
                .limit(1)
            )
        ).scalars().first()
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)
        return _chapter_from_row(row)

    for payload in payloads:
        if int(payload["index"]) == chapter_idx:
            return _chapter_from_payload(payload)
    raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)


@router.get("/{chapter_idx}/analysis", response_model=ChapterAnalysis)
async def get_analysis(
    novel_id: str,
    chapter_idx: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterAnalysis:
    try:
        task = await _get_active_task_or_404(db, novel_id)
    except AppError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return _empty_analysis()
        raise

    artifact_path = analyze_pipeline.chapter_analysis_path(request.app.state.artifact_store, novel_id, task.id, chapter_idx)
    if not artifact_path.exists():
        return _empty_analysis()

    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    try:
        return analyze_pipeline.chapter_analysis_from_artifact(payload)
    except AppError as exc:
        if exc.code == ErrorCode.CONFIG_INVALID:
            return _empty_analysis()
        raise


@router.put("/{chapter_idx}/analysis", response_model=ChapterUpdateAnalysisResponse)
async def update_analysis(
    novel_id: str,
    chapter_idx: int,
    analysis: ChapterAnalysis,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    task = await _get_active_task_or_404(db, novel_id)
    _, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    chapter_id = None
    chapter_title = ""
    chapter_content = ""
    for payload in payloads:
        if int(payload["index"]) != chapter_idx:
            continue
        chapter_id = str(payload.get("id") or "") or None
        chapter_title = str(payload.get("title") or "")
        chapter_content = str(payload.get("content") or "")
        break

    analyze_pipeline.update_analysis_artifact(
        request.app.state.artifact_store,
        novel_id,
        task.id,
        chapter_idx,
        analysis,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        chapter_text=chapter_content,
        source="manual",
    )
    stale_stages = await _mark_downstream_stages_stale(db, task.id)
    return {
        "status": "updated",
        "chapter_idx": chapter_idx,
        "chapter_id": chapter_id,
        "chapter_title": chapter_title,
        "stale_stages": stale_stages,
    }


@router.get("/{chapter_idx}/rewrites", response_model=list[RewriteResult])
async def get_rewrites(
    novel_id: str,
    chapter_idx: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    try:
        return await _load_rewrite_results_for_display(request, db, novel_id, chapter_idx)
    except AppError as exc:
        if exc.code == ErrorCode.NOT_FOUND:
            return []
        raise


@router.put("/{chapter_idx}/rewrites/{segment_id}", response_model=RewriteReviewResponse)
async def review_rewrite(
    novel_id: str,
    chapter_idx: int,
    segment_id: str,
    payload: RewriteReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> RewriteReviewResponse:
    task = await _get_active_task_or_404(db, novel_id)
    path = _chapter_rewrite_path(request, novel_id, task.id, chapter_idx)
    if not path.exists():
        # Support "adopt original" before first rewrite run by bootstrapping chapter
        # rewrite artifact from mark plan preview segments.
        path = await _bootstrap_rewrite_chapter_artifact_from_mark_plan(
            request,
            db,
            novel_id=novel_id,
            task=task,
            chapter_idx=chapter_idx,
        )

    payload_json = _load_rewrite_chapter_artifact(path)
    segments = [RewriteResult.model_validate(item) for item in list(payload_json.get("segments", []))]
    target_index = next((index for index, item in enumerate(segments) if item.segment_id == segment_id), None)
    if target_index is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Rewrite segment `{segment_id}` not found", status.HTTP_404_NOT_FOUND)

    current = segments[target_index]
    previous_snapshot = current.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    if payload.action == RewriteReviewAction.EDIT and current.status not in {
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
    }:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "accepted rewrite results can only be edited after acceptance",
            status.HTTP_400_BAD_REQUEST,
        )
    if payload.action == RewriteReviewAction.ACCEPT:
        next_status = RewriteResultStatus.ACCEPTED
        next_segment = current.model_copy(
            update={
                "status": next_status,
                "rollback_snapshot": previous_snapshot,
                "error_code": None,
                "error_detail": None,
            }
        )
    elif payload.action == RewriteReviewAction.REJECT:
        next_status = RewriteResultStatus.REJECTED
        next_segment = current.model_copy(
            update={
                "status": next_status,
                "rollback_snapshot": previous_snapshot,
                "error_code": None,
                "error_detail": None,
            }
        )
    elif payload.action == RewriteReviewAction.REGENERATE:
        next_status = RewriteResultStatus.PENDING
        next_segment = current.model_copy(
            update={
                "status": next_status,
                "rollback_snapshot": previous_snapshot,
                "error_code": None,
                "error_detail": None,
            }
        )
    elif payload.action == RewriteReviewAction.EDIT:
        next_status = RewriteResultStatus.ACCEPTED_EDITED
        edited_text = str(payload.rewritten_text or "").strip()
        next_segment = current.model_copy(
            update={
                "status": next_status,
                "rewritten_text": edited_text,
                "rewritten_chars": len(edited_text),
                "manual_edited_text": edited_text,
                "rollback_snapshot": previous_snapshot,
                "error_code": None,
                "error_detail": None,
            }
        )
    else:  # pragma: no cover - Literal keeps this path unreachable
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Unsupported review action `{payload.action}`", status.HTTP_400_BAD_REQUEST)

    audit_entry = RewriteAuditEntry(
        action=payload.action,
        from_status=current.status,
        to_status=next_status,
        reviewed_at=now,
        note=payload.note,
        previous_rewritten_text=current.rewritten_text,
        manual_edited_text=payload.rewritten_text if payload.action == RewriteReviewAction.EDIT else None,
        rollback_snapshot=previous_snapshot,
    )
    next_segment = next_segment.model_copy(update={"audit_trail": [*current.audit_trail, audit_entry]})
    segments[target_index] = next_segment
    payload_json["segments"] = [item.model_dump(mode="json") for item in segments]
    payload_json["audit_trail"] = [*list(payload_json.get("audit_trail", [])), audit_entry.model_dump(mode="json")]
    payload_json["updated_at"] = now.isoformat()
    request.app.state.artifact_store.ensure_json(path, payload_json)
    _rebuild_rewrite_aggregate(request, novel_id, task.id)
    stale_stages = await _mark_stage_runs_stale(db, task.id, ("rewrite", "assemble"))
    return RewriteReviewResponse(
        status=next_status,
        chapter_idx=chapter_idx,
        segment_id=segment_id,
        artifact_path=str(path),
        audit_entries=len(next_segment.audit_trail),
        stale_stages=stale_stages,
    )


@router.put("/{chapter_idx}/marks", response_model=ChapterUpdateMarksResponse)
async def update_marks(
    novel_id: str,
    chapter_idx: int,
    payload: ChapterUpdateMarksRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    task = await _get_active_task_or_404(db, novel_id)
    plan_path = _mark_plan_path(request, novel_id, task.id)
    if not plan_path.exists():
        raise AppError(ErrorCode.NOT_FOUND, f"Mark plan for chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)

    _, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    chapter = _chapter_for_index(payloads, chapter_idx)
    plan = _load_rewrite_plan(plan_path)
    segments = [_coerce_rewrite_segment(item, chapter=chapter) for item in payload.segments]
    if payload.mode == "replace":
        updated = replace_manual_segments(plan, chapter_idx, segments)
    else:
        updated = merge_manual_segments(plan, chapter_idx, segments)

    write_mark_artifacts(request.app.state.artifact_store, novel_id, task.id, updated)
    return {"status": "updated", "chapter_idx": chapter_idx, "total_marked": updated.total_marked}


@router.put("/{chapter_idx}/rename", response_model=ChapterAdjustResponse)
async def rename_chapter(
    novel_id: str,
    chapter_idx: int,
    payload: ChapterRenameRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterAdjustResponse:
    task, payloads, from_db = await _load_chapter_payloads(request, db, novel_id)
    if not payloads:
        raise AppError(ErrorCode.NOT_FOUND, "No split chapters available", status.HTTP_404_NOT_FOUND)
    updated = False
    for chapter in payloads:
        if int(chapter["index"]) == chapter_idx:
            chapter["title"] = payload.title
            updated = True
            break
    if not updated:
        raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)
    return await _persist_chapters(request, db, novel_id, task, payloads)


@router.post("/{chapter_idx}/split", response_model=ChapterAdjustResponse)
async def split_chapter(
    novel_id: str,
    chapter_idx: int,
    payload: ChapterSplitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterAdjustResponse:
    task, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    if not payloads:
        raise AppError(ErrorCode.NOT_FOUND, "No split chapters available", status.HTTP_404_NOT_FOUND)

    target_index = next((i for i, chapter in enumerate(payloads) if int(chapter["index"]) == chapter_idx), None)
    if target_index is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)

    target = dict(payloads[target_index])
    parts = [part.strip() for part in str(target["content"]).split("\n\n") if part.strip()]
    if payload.split_at_paragraph_index <= 0 or payload.split_at_paragraph_index >= len(parts):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "split_at_paragraph_index must be within the chapter paragraph range",
            status.HTTP_400_BAD_REQUEST,
        )

    first_content = "\n\n".join(parts[: payload.split_at_paragraph_index])
    second_content = "\n\n".join(parts[payload.split_at_paragraph_index :])
    first = dict(target)
    first["content"] = first_content
    existing_ids = {str(chapter["id"]) for chapter in payloads}
    second = {
        "id": _make_split_chapter_id(_task_scoped_chapter_id(task.id, str(target["id"])), existing_ids),
        "index": int(target["index"]) + 1,
        "title": f"{target['title']}-2",
        "content": second_content,
    }

    updated_payloads = payloads[:target_index] + [first, second] + payloads[target_index + 1 :]
    return await _persist_chapters(request, db, novel_id, task, updated_payloads)


@router.post("/{chapter_idx}/merge-next", response_model=ChapterAdjustResponse)
async def merge_next_chapter(
    novel_id: str,
    chapter_idx: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterAdjustResponse:
    task, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    if not payloads:
        raise AppError(ErrorCode.NOT_FOUND, "No split chapters available", status.HTTP_404_NOT_FOUND)

    target_index = next((i for i, chapter in enumerate(payloads) if int(chapter["index"]) == chapter_idx), None)
    if target_index is None or target_index + 1 >= len(payloads):
        raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` has no next chapter to merge", status.HTTP_404_NOT_FOUND)

    current = dict(payloads[target_index])
    nxt = dict(payloads[target_index + 1])
    merged = dict(current)
    merged["content"] = f"{current['content']}\n\n{nxt['content']}".strip()
    merged["title"] = str(current["title"])
    updated_payloads = payloads[:target_index] + [merged] + payloads[target_index + 2 :]
    return await _persist_chapters(request, db, novel_id, task, updated_payloads)


@router.delete("/{chapter_idx}", response_model=ChapterAdjustResponse)
async def delete_chapter(
    novel_id: str,
    chapter_idx: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> ChapterAdjustResponse:
    task, payloads, _ = await _load_chapter_payloads(request, db, novel_id)
    if not payloads:
        raise AppError(ErrorCode.NOT_FOUND, "No split chapters available", status.HTTP_404_NOT_FOUND)
    if len(payloads) <= 1:
        raise AppError(ErrorCode.VALIDATION_ERROR, "Cannot delete the only remaining chapter")

    target_index = next((i for i, chapter in enumerate(payloads) if int(chapter["index"]) == chapter_idx), None)
    if target_index is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_idx}` not found", status.HTTP_404_NOT_FOUND)

    updated_payloads = payloads[:target_index] + payloads[target_index + 1:]
    return await _persist_chapters(request, db, novel_id, task, updated_payloads)


@router.get("/{chapter_idx}/prompt-logs", response_model=PromptLogListResponse)
async def list_prompt_logs(novel_id: str, chapter_idx: int) -> PromptLogListResponse:
    entries = sorted(
        _load_prompt_log_entries(novel_id, chapter_idx),
        key=lambda item: item.timestamp,
        reverse=True,
    )
    return PromptLogListResponse(
        novel_id=novel_id,
        chapter_idx=chapter_idx,
        total=len(entries),
        data=entries,
    )


@router.post("/{chapter_idx}/prompt-logs/{call_id}/retry", response_model=PromptLogRetryResponse)
async def retry_prompt_log(
    novel_id: str,
    chapter_idx: int,
    call_id: str,
) -> PromptLogRetryResponse:
    entries = _load_prompt_log_entries(novel_id, chapter_idx)
    entry = next((item for item in entries if item.call_id == call_id), None)
    if entry is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Prompt log `{call_id}` not found", status.HTTP_404_NOT_FOUND)

    return PromptLogRetryResponse(
        novel_id=novel_id,
        chapter_idx=chapter_idx,
        call_id=call_id,
        stage=entry.stage,
        message="Prompt log replay queued in degraded mode; original LLM request is not replayed automatically.",
    )
