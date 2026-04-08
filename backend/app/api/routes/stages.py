from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.redaction import sanitize_public_payload
from backend.app.api.schemas import (
    SplitPreviewChapterResponse,
    SplitStageConfirmResponse,
    SplitStagePreviewResponse,
)
from backend.app.contracts.api import SplitConfirmRequest, StageActionRequest, StageActionResponse, StageChapterRetryRequest
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.secrets import decrypt_api_key
from backend.app.core.settings import get_settings
from backend.app.db import Chapter as ChapterRow
from backend.app.db import ChapterState, ChapterStateStatus, Novel, Provider, StageRun, Task, get_db_session
from backend.app.llm.token_counter import count_text_tokens
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    ProviderType as CoreProviderType,
    RewriteWindowModeSnapshot,
    RewritePlan,
    RewriteResult,
    RewriteResultStatus,
    RewriteSegment,
    StageConfigSnapshot,
    StageName,
    StageRunInfo,
    StageStatus,
    WindowAttemptAction,
    WindowGuardrailLevel,
)
from backend.app.services.analyze_pipeline import (
    AnalyzeChapterRequest,
    analyze_chapter,
    chapter_analysis_from_artifact,
    load_analysis_aggregate,
    persist_analysis_results,
    rebuild_analysis_aggregate,
)
from backend.app.services.assemble_pipeline import assemble_results_to_dict, assemble_novel
from backend.app.services.config_store import load_snapshot
from backend.app.services.marking import build_rewrite_plan, write_mark_artifacts
from backend.app.services.rewrite_pipeline import RewriteSegmentRequest, execute_rewrite_segment, extract_segment_source_text
from backend.app.services.worker_pool import WorkerPool
from backend.app.services.splitting import load_split_rules_state, make_split_preview, validate_preview_token

router = APIRouter(prefix="/novels/{novel_id}/stages", tags=["stages"])


def _normalize_stage_status(
    raw: str | StageStatus,
    *,
    chapters_total: int = 0,
    chapters_done: int = 0,
    completed_at: object | None = None,
) -> StageStatus:
    status = raw if isinstance(raw, StageStatus) else StageStatus(str(raw))
    if status != StageStatus.STALE:
        return status
    total = max(0, int(chapters_total or 0))
    done = max(0, min(total, int(chapters_done or 0)))
    if total > 0:
        return StageStatus.COMPLETED if done >= total else StageStatus.PAUSED
    if done > 0:
        return StageStatus.COMPLETED
    if completed_at is not None:
        return StageStatus.PAUSED
    return StageStatus.PENDING


def _to_run_info(stage_run: StageRun, *, artifact_path: str | None = None, is_latest: bool = True) -> StageRunInfo:
    return StageRunInfo(
        id=stage_run.id,
        run_seq=stage_run.run_seq,
        stage=StageName(stage_run.stage),
        status=_normalize_stage_status(
            stage_run.status,
            chapters_total=stage_run.chapters_total or 0,
            chapters_done=stage_run.chapters_done or 0,
            completed_at=stage_run.completed_at,
        ),
        started_at=stage_run.started_at,
        completed_at=stage_run.completed_at,
        error_message=stage_run.error_message,
        run_idempotency_key=stage_run.run_idempotency_key,
        warnings_count=stage_run.warnings_count,
        chapters_total=stage_run.chapters_total,
        chapters_done=stage_run.chapters_done,
        config_snapshot=_parse_stage_config_snapshot(stage_run.config_snapshot_json),
        artifact_path=artifact_path,
        is_latest=is_latest,
    )


def _task_scoped_chapter_id(task_id: str, chapter_ref: str | int) -> str:
    chapter_ref_str = str(chapter_ref)
    prefix = f"{task_id}:"
    if chapter_ref_str.startswith(prefix):
        return chapter_ref_str
    return f"{prefix}{chapter_ref_str}"


def _normalize_split_chapters(task_id: str, chapters: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, chapter in enumerate(chapters, start=1):
        item = dict(chapter)
        chapter_id = _task_scoped_chapter_id(task_id, f"chapter-{index}")
        raw_id = str(item.get("id") or "")
        if raw_id:
            chapter_id = _task_scoped_chapter_id(task_id, raw_id)
        if chapter_id in seen_ids:
            suffix = 2
            deduped = f"{chapter_id}-{suffix}"
            while deduped in seen_ids:
                suffix += 1
                deduped = f"{chapter_id}-{suffix}"
            chapter_id = deduped
        seen_ids.add(chapter_id)
        item["id"] = chapter_id
        normalized.append(item)
    return normalized


def _chapter_payload_from_model(chapter: Chapter) -> dict[str, object]:
    return {
        "id": chapter.id,
        "index": chapter.index,
        "title": chapter.title,
        "content": chapter.content,
        "start_offset": chapter.start_offset,
        "end_offset": chapter.end_offset,
        "char_count": chapter.char_count,
        "paragraph_count": chapter.paragraph_count,
    }


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _parse_stage_config_snapshot(raw_snapshot: str | None) -> StageConfigSnapshot | None:
    if not raw_snapshot:
        return None
    try:
        payload = json.loads(raw_snapshot)
    except json.JSONDecodeError:
        return None
    try:
        return StageConfigSnapshot.model_validate(payload)
    except Exception:
        return None


def _novel_window_mode_overrides(raw: str | None) -> dict[str, bool | None]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    rewrite = payload.get("rewrite")
    if isinstance(rewrite, dict):
        window_mode = rewrite.get("window_mode")
        if isinstance(window_mode, dict):
            return {
                "enabled": (
                    bool(window_mode["enabled"])
                    if "enabled" in window_mode and isinstance(window_mode.get("enabled"), bool)
                    else None
                ),
                "guardrail_enabled": (
                    bool(window_mode["guardrail_enabled"])
                    if "guardrail_enabled" in window_mode and isinstance(window_mode.get("guardrail_enabled"), bool)
                    else None
                ),
                "audit_enabled": (
                    bool(window_mode["audit_enabled"])
                    if "audit_enabled" in window_mode and isinstance(window_mode.get("audit_enabled"), bool)
                    else None
                ),
            }
    return {}


async def _resolve_rewrite_window_mode_snapshot(
    db: AsyncSession,
    *,
    novel_id: str,
    task_id: str,
    request_enabled: bool | None = None,
    request_guardrail_enabled: bool | None = None,
    request_audit_enabled: bool | None = None,
) -> RewriteWindowModeSnapshot:
    settings = get_settings()
    novel_row = (
        await db.execute(
            select(Novel).where(Novel.id == novel_id).limit(1)
        )
    ).scalars().first()
    novel_overrides = _novel_window_mode_overrides(novel_row.config_override_json if novel_row is not None else None)

    allowlist_enabled = True
    if settings.rewrite_window_mode_novel_allowlist and novel_id not in set(settings.rewrite_window_mode_novel_allowlist):
        allowlist_enabled = False
    if settings.rewrite_window_mode_task_allowlist and task_id not in set(settings.rewrite_window_mode_task_allowlist):
        allowlist_enabled = False

    enabled = (
        request_enabled
        if request_enabled is not None
        else (
            novel_overrides.get("enabled")
            if novel_overrides.get("enabled") is not None
            else (settings.rewrite_window_mode_enabled and allowlist_enabled)
        )
    )
    guardrail_enabled = (
        request_guardrail_enabled
        if request_guardrail_enabled is not None
        else (
            novel_overrides.get("guardrail_enabled")
            if novel_overrides.get("guardrail_enabled") is not None
            else settings.rewrite_window_mode_guardrail_enabled
        )
    )
    audit_enabled = (
        request_audit_enabled
        if request_audit_enabled is not None
        else (
            novel_overrides.get("audit_enabled")
            if novel_overrides.get("audit_enabled") is not None
            else settings.rewrite_window_mode_audit_enabled
        )
    )

    source = "settings_default"
    if request_enabled is not None or request_guardrail_enabled is not None or request_audit_enabled is not None:
        source = "request_override"
    elif novel_overrides:
        source = "novel_override"

    return RewriteWindowModeSnapshot(
        enabled=bool(enabled),
        guardrail_enabled=bool(guardrail_enabled) if bool(enabled) else False,
        audit_enabled=bool(audit_enabled) if bool(enabled) else False,
        source=source,
    )


def _build_stage_config_snapshot(
    config_snapshot,
    provider: Provider | None = None,
    *,
    rewrite_window_mode: RewriteWindowModeSnapshot | None = None,
) -> StageConfigSnapshot:
    generation_params: dict[str, Any] = {}
    if provider is not None:
        generation_params = {
            "temperature": provider.temperature,
            "max_tokens": provider.max_tokens,
            "top_p": provider.top_p,
            "presence_penalty": provider.presence_penalty,
            "frequency_penalty": provider.frequency_penalty,
            "rpm_limit": provider.rpm_limit,
            "tpm_limit": provider.tpm_limit,
        }

    return StageConfigSnapshot(
        provider_id=provider.id if provider is not None else None,
        provider_name=provider.name if provider is not None else None,
        provider_type=CoreProviderType(provider.provider_type) if provider is not None else None,
        model_name=provider.model_name if provider is not None else None,
        base_url=provider.base_url if provider is not None else None,
        global_prompt_version=_hash_payload(config_snapshot.global_prompt),
        scene_rules_hash=_hash_payload([rule.model_dump(mode="json") for rule in config_snapshot.scene_rules]),
        rewrite_rules_hash=_hash_payload([rule.model_dump(mode="json") for rule in config_snapshot.rewrite_rules]),
        generation_params=generation_params,
        rewrite_window_mode=rewrite_window_mode or RewriteWindowModeSnapshot(),
        captured_at=datetime.utcnow(),
    )


def _stage_run_artifact_payload(
    *,
    novel_id: str,
    run: StageRun,
    config_snapshot: StageConfigSnapshot | None,
    artifact_path: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "novel_id": novel_id,
        "task_id": run.task_id,
        "stage": run.stage,
        "stage_run_id": run.id,
        "run_seq": run.run_seq,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "run_idempotency_key": run.run_idempotency_key,
        "warnings_count": run.warnings_count,
        "chapters_total": run.chapters_total,
        "chapters_done": run.chapters_done,
        "config_snapshot": config_snapshot.model_dump(mode="json") if config_snapshot is not None else None,
        "artifact_path": artifact_path,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if extra:
        payload.update(extra)
    return payload


def _stage_run_artifact_paths(
    artifact_store: ArtifactStore,
    novel_id: str,
    run: StageRun,
) -> tuple[Path, Path]:
    stage = str(run.stage)
    run_path = artifact_store.stage_run_manifest_path(novel_id, run.task_id, stage, run.run_seq)
    latest_path = artifact_store.stage_run_latest_manifest_path(novel_id, run.task_id, stage)
    return run_path, latest_path


def _write_stage_run_artifacts(
    artifact_store: ArtifactStore,
    novel_id: str,
    run: StageRun,
    config_snapshot: StageConfigSnapshot | None,
    *,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    run_path, latest_path = _stage_run_artifact_paths(artifact_store, novel_id, run)
    payload = _stage_run_artifact_payload(
        novel_id=novel_id,
        run=run,
        config_snapshot=config_snapshot,
        artifact_path=str(run_path),
        extra=extra,
    )
    artifact_store.ensure_json(run_path, payload)
    artifact_store.ensure_json(latest_path, payload)
    return run_path, latest_path


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


async def _latest_stage_run(db: AsyncSession, task_id: str, stage: StageName) -> StageRun | None:
    return (
        await db.execute(
            select(StageRun)
            .where(StageRun.task_id == task_id, StageRun.stage == stage.value)
            .order_by(StageRun.run_seq.desc())
            .limit(1)
        )
    ).scalars().first()


async def _mark_downstream_stale(db: AsyncSession, task_id: str, downstream: tuple[str, ...] = ("mark", "rewrite", "assemble")) -> None:
    """Mark the latest completed/failed run of each downstream stage as STALE.

    Called when an upstream stage (e.g. analyze) begins re-running so the UI
    shows that downstream results are based on outdated data.
    """
    for stage_name in downstream:
        run = (
            await db.execute(
                select(StageRun)
                .where(
                    StageRun.task_id == task_id,
                    StageRun.stage == stage_name,
                    StageRun.status.in_([StageStatus.COMPLETED.value, StageStatus.FAILED.value]),
                )
                .order_by(StageRun.run_seq.desc())
                .limit(1)
            )
        ).scalars().first()
        if run is not None:
            run.status = StageStatus.STALE.value
    await db.commit()


async def _running_stage_run(db: AsyncSession, task_id: str, stage: StageName) -> StageRun | None:
    return (
        await db.execute(
            select(StageRun)
            .where(StageRun.task_id == task_id, StageRun.stage == stage.value, StageRun.status == StageStatus.RUNNING.value)
            .order_by(StageRun.run_seq.desc())
            .limit(1)
        )
    ).scalars().first()


async def _stage_run_by_idempotency(
    db: AsyncSession,
    task_id: str,
    stage: StageName,
    idempotency_key: str,
) -> StageRun | None:
    return (
        await db.execute(
            select(StageRun).where(
                StageRun.task_id == task_id,
                StageRun.stage == stage.value,
                StageRun.run_idempotency_key == idempotency_key,
            )
        )
    ).scalars().first()


async def _stage_run_by_seq(
    db: AsyncSession,
    task_id: str,
    stage: StageName,
    run_seq: int,
) -> StageRun | None:
    return (
        await db.execute(
            select(StageRun).where(
                StageRun.task_id == task_id,
                StageRun.stage == stage.value,
                StageRun.run_seq == run_seq,
            )
        )
    ).scalars().first()


async def _get_active_provider(db: AsyncSession) -> Provider:
    provider = (
        await db.execute(
            select(Provider)
            .where(Provider.is_active.is_(True))
            .order_by(Provider.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if provider is None:
        raise AppError(ErrorCode.VALIDATION_ERROR, "No active provider configured")
    return provider


async def _load_provider_by_id_or_404(db: AsyncSession, provider_id: str) -> Provider:
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Provider `{provider_id}` not found", status.HTTP_404_NOT_FOUND)
    return provider


async def _resolve_provider_for_execution(db: AsyncSession, provider_id: str | None) -> Provider:
    if provider_id:
        return await _load_provider_by_id_or_404(db, provider_id)
    return await _get_active_provider(db)


async def _get_active_provider_if_any(db: AsyncSession) -> Provider | None:
    try:
        return await _get_active_provider(db)
    except AppError as exc:
        if exc.code == ErrorCode.VALIDATION_ERROR:
            return None
        raise


async def _load_task_chapters(db: AsyncSession, task_id: str) -> list[Chapter]:
    rows = (
        await db.execute(
            select(ChapterRow)
            .where(ChapterRow.task_id == task_id)
            .order_by(ChapterRow.chapter_index.asc())
        )
    ).scalars().all()
    return [
        Chapter(
            id=row.id,
            index=row.chapter_index,
            title=row.title,
            content=row.content,
            char_count=row.char_count,
            paragraph_count=row.paragraph_count,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
        )
        for row in rows
    ]


def _get_worker_pool(request: Request) -> WorkerPool:
    worker_pool = getattr(request.app.state, "worker_pool", None)
    if worker_pool is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Worker pool is not initialized", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return worker_pool


def _get_prompt_audit_logger(request: Request):
    return getattr(request.app.state, "prompt_audit_logger", None)


async def _load_config_snapshot(db: AsyncSession):
    return await load_snapshot(db)


async def _create_stage_run(
    db: AsyncSession,
    active_task: Task,
    stage: StageName,
    *,
    idempotency_key: str | None,
    status: StageStatus = StageStatus.RUNNING,
    config_snapshot: StageConfigSnapshot | None = None,
) -> StageRun:
    latest = await _latest_stage_run(db, active_task.id, stage)
    next_seq = (latest.run_seq + 1) if latest else 1
    now = datetime.utcnow()
    run = StageRun(
        id=f"{active_task.id}-{stage.value}-{next_seq}",
        task_id=active_task.id,
        stage=stage.value,
        run_seq=next_seq,
        status=status.value,
        started_at=now,
        run_idempotency_key=idempotency_key,
        config_snapshot_json=config_snapshot.model_dump_json() if config_snapshot is not None else None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _mark_stage_run_success(
    db: AsyncSession,
    run: StageRun,
    *,
    chapters_total: int,
    chapters_done: int,
) -> StageRun:
    run.status = StageStatus.COMPLETED.value
    run.completed_at = datetime.utcnow()
    run.chapters_total = chapters_total
    run.chapters_done = chapters_done
    run.error_message = None
    await db.commit()
    await db.refresh(run)
    return run


async def _mark_stage_run_failure(
    db: AsyncSession,
    run: StageRun,
    *,
    chapters_total: int,
    chapters_done: int,
    error_message: str,
) -> StageRun:
    run.status = StageStatus.FAILED.value
    run.completed_at = datetime.utcnow()
    run.chapters_total = chapters_total
    run.chapters_done = chapters_done
    run.error_message = error_message
    await db.commit()
    await db.refresh(run)
    return run


async def _upsert_chapter_state(
    db: AsyncSession,
    run: StageRun,
    *,
    chapter_index: int,
    status: ChapterStateStatus,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> ChapterState:
    row = (
        await db.execute(
            select(ChapterState)
            .where(ChapterState.stage_run_id == run.id, ChapterState.chapter_index == chapter_index)
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        row = ChapterState(
            id=f"{run.id}:ch{chapter_index}",
            stage_run_id=run.id,
            chapter_index=chapter_index,
            status=status.value,
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
        )
        db.add(row)
        return row

    row.status = status.value
    row.error_message = error_message
    if started_at is not None:
        row.started_at = started_at
    if completed_at is not None:
        row.completed_at = completed_at
    elif status == ChapterStateStatus.RUNNING:
        row.completed_at = None
    return row


async def _initialize_missing_chapter_states(
    db: AsyncSession,
    run: StageRun,
    *,
    chapter_indexes: list[int],
    default_status: ChapterStateStatus = ChapterStateStatus.PENDING,
) -> None:
    if not chapter_indexes:
        return
    rows = (
        await db.execute(
            select(ChapterState).where(
                ChapterState.stage_run_id == run.id,
                ChapterState.chapter_index.in_(chapter_indexes),
            )
        )
    ).scalars().all()
    existing = {int(row.chapter_index) for row in rows}
    for chapter_index in chapter_indexes:
        if chapter_index in existing:
            continue
        db.add(
            ChapterState(
                id=f"{run.id}:ch{chapter_index}",
                stage_run_id=run.id,
                chapter_index=chapter_index,
                status=default_status.value,
                error_message=None,
                started_at=None,
                completed_at=None,
            )
        )


async def _sync_stage_run_artifacts(
    request: Request,
    novel_id: str,
    run: StageRun,
    config_snapshot: StageConfigSnapshot | None,
    *,
    extra: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    artifact_store = request.app.state.artifact_store
    paths = _write_stage_run_artifacts(artifact_store, novel_id, run, config_snapshot, extra=extra)
    await _publish_stage_ws_update(request, novel_id, run)
    return paths


async def _publish_stage_ws_update(request: Request, novel_id: str, run: StageRun) -> None:
    hub = getattr(request.app.state, "ws_hub", None)
    if hub is None:
        return

    try:
        stage_name = StageName(run.stage)
    except Exception:
        return

    chapters_total = max(0, int(run.chapters_total or 0))
    chapters_done = max(0, int(run.chapters_done or 0))
    percentage = 100.0 if chapters_total == 0 and run.status == StageStatus.COMPLETED.value else 0.0
    if chapters_total > 0:
        percentage = max(0.0, min(100.0, (chapters_done / chapters_total) * 100))

    await hub.publish(
        {
            "type": "stage_progress",
            "novel_id": novel_id,
            "stage": stage_name.value,
            "chapters_done": chapters_done,
            "chapters_total": chapters_total,
            "percentage": percentage,
        }
    )

    if run.status == StageStatus.COMPLETED.value:
        started_at = run.started_at or run.completed_at
        completed_at = run.completed_at or datetime.utcnow()
        duration_ms = 0
        if started_at is not None:
            duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
        await hub.publish(
            {
                "type": "stage_completed",
                "novel_id": novel_id,
                "stage": stage_name.value,
                "duration_ms": duration_ms,
            }
        )
        return

    if run.status == StageStatus.FAILED.value:
        await hub.publish(
            {
                "type": "stage_failed",
                "novel_id": novel_id,
                "stage": stage_name.value,
                "error": run.error_message or "Stage failed",
            }
        )
        return

    if run.status == StageStatus.STALE.value:
        await hub.publish(
            {
                "type": "stage_stale",
                "novel_id": novel_id,
                "stage": stage_name.value,
            }
        )
        return

    if run.status == StageStatus.PAUSED.value:
        await hub.publish(
            {
                "type": "task_paused",
                "novel_id": novel_id,
                "stage": stage_name.value,
            }
        )
        return

    if run.status == StageStatus.RUNNING.value:
        await hub.publish(
            {
                "type": "task_resumed",
                "novel_id": novel_id,
                "stage": stage_name.value,
            }
        )


def _app_error_artifact_payload(exc: AppError) -> dict[str, Any]:
    return {
        "code": exc.code.value,
        "message": exc.message,
        "details": exc.details,
    }


def _build_analyze_request(
    novel_id: str,
    task_id: str,
    chapter: Chapter,
    *,
    provider: Provider,
    global_prompt: str,
    scene_rules: list[object],
) -> AnalyzeChapterRequest:
    return AnalyzeChapterRequest(
        novel_id=novel_id,
        task_id=task_id,
        chapter_index=chapter.index,
        chapter_text=chapter.content,
        chapter_title=chapter.title,
        chapter_id=chapter.id,
        global_prompt=global_prompt,
        scene_rules=scene_rules,
        provider_type=CoreProviderType(provider.provider_type),
        api_key=decrypt_api_key(provider.api_key_encrypted),
        base_url=provider.base_url,
        model_name=provider.model_name,
        generation={
            "temperature": provider.temperature,
            "max_tokens": provider.max_tokens,
            "top_p": provider.top_p,
            "presence_penalty": provider.presence_penalty,
            "frequency_penalty": provider.frequency_penalty,
        },
    )


async def _run_analyze_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    run: StageRun,
    stage_config_snapshot: StageConfigSnapshot,
    provider_id: str | None = None,
) -> StageActionResponse:
    artifact_store = request.app.state.artifact_store
    worker_pool = _get_worker_pool(request)
    audit_logger = _get_prompt_audit_logger(request)
    chapters = await _load_task_chapters(db, active_task.id)
    run.chapters_total = len(chapters)
    await _initialize_missing_chapter_states(
        db,
        run,
        chapter_indexes=[chapter.index for chapter in chapters],
        default_status=ChapterStateStatus.PENDING,
    )
    await db.commit()

    # Analyze is re-running — mark downstream stages as stale so the UI
    # reflects that their results are based on outdated analysis.
    await _mark_downstream_stale(db, active_task.id)

    config_snapshot = await _load_config_snapshot(db)
    provider = await _resolve_provider_for_execution(db, provider_id)

    requests = [
        _build_analyze_request(
            novel_id,
            active_task.id,
            chapter,
            provider=provider,
            global_prompt=config_snapshot.global_prompt,
            scene_rules=config_snapshot.scene_rules,
        )
        for chapter in chapters
    ]

    async def _submit(item: AnalyzeChapterRequest):
        request_tokens = max(1, count_text_tokens(item.chapter_text, model_name=item.model_name))
        return await worker_pool.submit(
            lambda: analyze_chapter(item, audit_logger=audit_logger),
            provider_id=str(provider.id),
            rpm_limit=provider.rpm_limit,
            tpm_limit=provider.tpm_limit,
            request_tokens=request_tokens,
        )

    results = []
    if requests:
        for item in requests:
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=item.chapter_index,
                status=ChapterStateStatus.RUNNING,
                error_message=None,
                started_at=datetime.utcnow(),
                completed_at=None,
            )
            await db.commit()
            try:
                result = await _submit(item)
            except AppError as exc:
                await _upsert_chapter_state(
                    db,
                    run,
                    chapter_index=item.chapter_index,
                    status=ChapterStateStatus.FAILED,
                    error_message=exc.message,
                    completed_at=datetime.utcnow(),
                )
                await db.commit()
                raise
            except Exception:
                await _upsert_chapter_state(
                    db,
                    run,
                    chapter_index=item.chapter_index,
                    status=ChapterStateStatus.FAILED,
                    error_message="Analyze chapter execution failed",
                    completed_at=datetime.utcnow(),
                )
                await db.commit()
                raise
            results.append(result)
            persist_analysis_results(artifact_store, [result])
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=item.chapter_index,
                status=ChapterStateStatus.COMPLETED,
                error_message=None,
                completed_at=datetime.utcnow(),
            )
            run.chapters_done = len(results)
            await db.commit()
            await db.refresh(run)
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                run,
                stage_config_snapshot,
                extra={
                    "chapter_progress": {
                        "chapter_index": item.chapter_index,
                        "chapters_done": run.chapters_done,
                        "chapters_total": len(chapters),
                    }
                },
            )
    else:
        rebuild_analysis_aggregate(artifact_store, novel_id, active_task.id)
    await _mark_stage_run_success(
        db,
        run,
        chapters_total=len(chapters),
        chapters_done=len(chapters),
    )
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        run,
        stage_config_snapshot,
    )
    return StageActionResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        stage=StageName.ANALYZE,
        run=_to_run_info(run),
    )


def _load_analysis_map(analysis_payload: dict[str, object]) -> dict[int, ChapterAnalysis]:
    chapters = analysis_payload.get("chapters", [])
    if not isinstance(chapters, list):
        raise AppError(ErrorCode.CONFIG_INVALID, "analysis.json chapters payload is invalid")
    analyses_by_index: dict[int, ChapterAnalysis] = {}
    for item in chapters:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.CONFIG_INVALID, "analysis.json chapter entry is invalid")
        chapter_index = int(item.get("chapter_index") or 0)
        if chapter_index < 1:
            raise AppError(ErrorCode.CONFIG_INVALID, "analysis.json chapter_index is invalid")
        analyses_by_index[chapter_index] = chapter_analysis_from_artifact(item)
    return analyses_by_index


def _chapter_by_index(chapters: list[Chapter], chapter_idx: int) -> Chapter:
    for item in chapters:
        if item.index == chapter_idx:
            return item
    raise AppError(
        ErrorCode.NOT_FOUND,
        f"Chapter `{chapter_idx}` not found",
        status.HTTP_404_NOT_FOUND,
    )


def _pending_analyze_chapter_indexes(
    chapters: list[Chapter],
    analyses_by_chapter: dict[int, ChapterAnalysis],
) -> list[int]:
    return [chapter.index for chapter in sorted(chapters, key=lambda item: item.index) if chapter.index not in analyses_by_chapter]


def _chapter_retry_running_done(chapters_total: int, chapter_idx: int) -> int:
    if chapters_total <= 0:
        return 0
    # Keep chapter-level retry observable in UI after route remount: chapters before retry target are completed,
    # current chapter is running.
    return max(0, min(chapters_total - 1, chapter_idx - 1))


def _rewrite_stage_progress(
    chapters: list[Chapter],
    rewrite_plan: RewritePlan,
    rewrite_results_by_chapter: dict[int, list[RewriteResult]],
) -> tuple[int, list[int]]:
    plan_by_index = {item.chapter_index: item for item in rewrite_plan.chapters}
    completed = 0
    pending: list[int] = []

    for chapter in sorted(chapters, key=lambda item: item.index):
        chapter_plan = plan_by_index.get(chapter.index)
        expected_segments = len(chapter_plan.segments) if chapter_plan is not None else 0
        actual_segments = len(rewrite_results_by_chapter.get(chapter.index, []))

        # Chapters without marked segments are considered finished for rewrite.
        if expected_segments == 0 or actual_segments >= expected_segments:
            completed += 1
            continue
        pending.append(chapter.index)

    return completed, pending


def _rewrite_results_cover_chapter_plan(chapter_plan: Any | None, results: list[RewriteResult]) -> bool:
    segments = list(getattr(chapter_plan, "segments", []) or [])
    if not segments:
        return True
    if len(results) < len(segments):
        return False

    terminal_statuses = {
        RewriteResultStatus.COMPLETED,
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
        RewriteResultStatus.REJECTED,
        RewriteResultStatus.ROLLED_BACK,
    }
    if any(item.status not in terminal_statuses for item in results):
        return False

    def _window_identity_from_parts(
        *,
        plan_version: str | None,
        window_id: str | None,
        start_offset: int | None,
        end_offset: int | None,
        source_fingerprint: str | None,
    ) -> str | None:
        if not window_id or start_offset is None or end_offset is None:
            return None
        if end_offset <= start_offset:
            return None
        return f"{plan_version or ''}:{window_id}:{start_offset}:{end_offset}:{source_fingerprint or ''}"

    result_ids = {item.segment_id for item in results if item.segment_id}
    result_ranges = {tuple(item.paragraph_range) for item in results}
    result_window_keys: set[str] = set()
    for item in results:
        for window in list(item.rewrite_windows or []):
            identity = _window_identity_from_parts(
                plan_version=window.plan_version,
                window_id=window.window_id,
                start_offset=window.start_offset,
                end_offset=window.end_offset,
                source_fingerprint=window.source_fingerprint,
            )
            if identity:
                result_window_keys.add(identity)

    for segment in segments:
        expected_window_keys: set[str] = set()
        for window in list(getattr(segment, "rewrite_windows", []) or []):
            identity = _window_identity_from_parts(
                plan_version=getattr(segment, "plan_version", None) or getattr(window, "plan_version", None),
                window_id=getattr(window, "window_id", None),
                start_offset=getattr(window, "start_offset", None),
                end_offset=getattr(window, "end_offset", None),
                source_fingerprint=getattr(segment, "source_fingerprint", None) or getattr(window, "source_fingerprint", None),
            )
            if identity:
                expected_window_keys.add(identity)
        if expected_window_keys:
            if expected_window_keys.issubset(result_window_keys):
                continue
            # Window identities changed (plan/range/fingerprint mismatch):
            # treat as incomplete and force rerun.
            return False
        if segment.segment_id in result_ids or tuple(segment.paragraph_range) in result_ranges:
            continue
        return False
    return True


def _rewrite_segment_window_keys(segment: Any) -> set[str]:
    def _window_identity_from_parts(
        *,
        plan_version: str | None,
        window_id: str | None,
        start_offset: int | None,
        end_offset: int | None,
        source_fingerprint: str | None,
    ) -> str | None:
        if not window_id or start_offset is None or end_offset is None:
            return None
        if end_offset <= start_offset:
            return None
        return f"{plan_version or ''}:{window_id}:{start_offset}:{end_offset}:{source_fingerprint or ''}"

    expected_window_keys: set[str] = set()
    for window in list(getattr(segment, "rewrite_windows", []) or []):
        identity = _window_identity_from_parts(
            plan_version=getattr(segment, "plan_version", None) or getattr(window, "plan_version", None),
            window_id=getattr(window, "window_id", None),
            start_offset=getattr(window, "start_offset", None),
            end_offset=getattr(window, "end_offset", None),
            source_fingerprint=getattr(segment, "source_fingerprint", None) or getattr(window, "source_fingerprint", None),
        )
        if identity:
            expected_window_keys.add(identity)
    return expected_window_keys


def _rewrite_result_window_keys(result: RewriteResult) -> set[str]:
    keys: set[str] = set()
    for window in list(result.rewrite_windows or []):
        window_id = getattr(window, "window_id", None)
        start_offset = getattr(window, "start_offset", None)
        end_offset = getattr(window, "end_offset", None)
        if not window_id or start_offset is None or end_offset is None:
            continue
        if end_offset <= start_offset:
            continue
        keys.add(
            f"{getattr(window, 'plan_version', None) or ''}:{window_id}:{start_offset}:{end_offset}:{getattr(window, 'source_fingerprint', None) or ''}"
        )
    return keys


def _split_rewrite_segments_for_execution(
    chapter_plan: Any | None,
    existing_results: list[RewriteResult],
) -> tuple[list[Any], list[RewriteResult]]:
    segments = list(getattr(chapter_plan, "segments", []) or [])
    if not segments:
        return [], []

    terminal_statuses = {
        RewriteResultStatus.COMPLETED,
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
        RewriteResultStatus.REJECTED,
        RewriteResultStatus.ROLLED_BACK,
    }
    terminal_results = [item for item in existing_results if item.status in terminal_statuses]
    pending_segments: list[Any] = []
    retained_results: list[RewriteResult] = []

    for segment in segments:
        expected_window_keys = _rewrite_segment_window_keys(segment)
        matched: RewriteResult | None = None

        if expected_window_keys:
            for result in terminal_results:
                result_keys = _rewrite_result_window_keys(result)
                if expected_window_keys and expected_window_keys.issubset(result_keys):
                    matched = result
                    break
        else:
            if matched is None and getattr(segment, "segment_id", None):
                matched = next(
                    (
                        result
                        for result in terminal_results
                        if result.segment_id == getattr(segment, "segment_id")
                    ),
                    None,
                )

            if matched is None:
                matched = next(
                    (
                        result
                        for result in terminal_results
                        if tuple(result.paragraph_range) == tuple(getattr(segment, "paragraph_range", (0, 0)))
                    ),
                    None,
                )

        if matched is None:
            pending_segments.append(segment)
            continue

        retained_results.append(matched)

    # Keep deterministic artifact order by segment position in plan.
    result_by_segment_id = {item.segment_id: item for item in retained_results if item.segment_id}
    ordered_retained: list[RewriteResult] = []
    for segment in segments:
        segment_id = getattr(segment, "segment_id", None)
        if segment_id and segment_id in result_by_segment_id:
            ordered_retained.append(result_by_segment_id[segment_id])
            continue
        matched = next(
            (item for item in retained_results if tuple(item.paragraph_range) == tuple(getattr(segment, "paragraph_range", (0, 0)))),
            None,
        )
        if matched is not None and matched not in ordered_retained:
            ordered_retained.append(matched)

    return pending_segments, ordered_retained


def _count_failed_rewrite_segments(rewrite_results_by_chapter: dict[int, list[RewriteResult]]) -> int:
    return sum(
        1
        for results in rewrite_results_by_chapter.values()
        for item in results
        if item.status == RewriteResultStatus.FAILED
    )


def _refresh_mark_artifacts_for_task(
    request: Request,
    *,
    novel_id: str,
    task_id: str,
    chapters: list[Chapter],
) -> tuple[RewritePlan, list[int], list[int]]:
    analysis_payload = load_analysis_aggregate(request.app.state.artifact_store, novel_id, task_id)
    analyses_by_chapter = _load_analysis_map(analysis_payload)
    missing = [chapter.index for chapter in chapters if chapter.index not in analyses_by_chapter]

    plan = build_rewrite_plan(
        novel_id,
        chapters,
        analyses_by_chapter,
        [],
    )
    write_mark_artifacts(request.app.state.artifact_store, novel_id, task_id, plan)
    available = sorted(analyses_by_chapter.keys())
    return plan, available, missing


async def _retry_analyze_stage_chapter(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    chapter_idx: int,
    provider_id: str | None = None,
) -> dict[str, object]:
    chapters = await _load_task_chapters(db, active_task.id)
    chapters_total = len(chapters)
    chapter = _chapter_by_index(chapters, chapter_idx)
    provider = await _resolve_provider_for_execution(db, provider_id)
    config_snapshot = await _load_config_snapshot(db)
    stage_config_snapshot = _build_stage_config_snapshot(config_snapshot, provider)
    latest_analyze = await _latest_stage_run(db, active_task.id, StageName.ANALYZE)
    if latest_analyze is None:
        latest_analyze = await _create_stage_run(
            db,
            active_task,
            StageName.ANALYZE,
            idempotency_key=None,
            config_snapshot=stage_config_snapshot,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_analyze,
            stage_config_snapshot,
            extra={"trigger": "chapter_retry_bootstrap"},
        )
    elif latest_analyze.config_snapshot_json != stage_config_snapshot.model_dump_json():
        latest_analyze.config_snapshot_json = stage_config_snapshot.model_dump_json()
    retry_running_done = _chapter_retry_running_done(chapters_total, chapter_idx)
    latest_analyze.status = StageStatus.RUNNING.value
    latest_analyze.error_message = None
    latest_analyze.completed_at = None
    latest_analyze.chapters_total = chapters_total
    latest_analyze.chapters_done = retry_running_done
    await _upsert_chapter_state(
        db,
        latest_analyze,
        chapter_index=chapter_idx,
        status=ChapterStateStatus.RUNNING,
        error_message=None,
        started_at=datetime.utcnow(),
        completed_at=None,
    )
    await db.commit()

    # Single-chapter analyze retry — mark downstream stages as stale.
    await _mark_downstream_stale(db, active_task.id)

    await db.refresh(latest_analyze)
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        latest_analyze,
        _parse_stage_config_snapshot(latest_analyze.config_snapshot_json) or stage_config_snapshot,
        extra={
            "chapter_retry": {
                "chapter_index": chapter_idx,
                "stage": "analyze",
                "status": "running",
            },
        },
    )

    try:
        worker_pool = _get_worker_pool(request)
        audit_logger = _get_prompt_audit_logger(request)

        analyze_request = _build_analyze_request(
            novel_id,
            active_task.id,
            chapter,
            provider=provider,
            global_prompt=config_snapshot.global_prompt,
            scene_rules=config_snapshot.scene_rules,
        )
        request_tokens = max(1, count_text_tokens(analyze_request.chapter_text, model_name=analyze_request.model_name))
        result = await worker_pool.submit(
            lambda: analyze_chapter(analyze_request, audit_logger=audit_logger),
            provider_id=str(provider.id),
            rpm_limit=provider.rpm_limit,
            tpm_limit=provider.tpm_limit,
            request_tokens=request_tokens,
        )

        persist_analysis_results(request.app.state.artifact_store, [result])
        plan, analyzed_chapter_indexes, missing_chapter_indexes = _refresh_mark_artifacts_for_task(
            request,
            novel_id=novel_id,
            task_id=active_task.id,
            chapters=chapters,
        )
        analyzed_count = len(analyzed_chapter_indexes)

        latest_analyze.status = StageStatus.COMPLETED.value if analyzed_count >= len(chapters) else StageStatus.PAUSED.value
        latest_analyze.error_message = None
        latest_analyze.completed_at = datetime.utcnow()
        latest_analyze.chapters_total = len(chapters)
        latest_analyze.chapters_done = analyzed_count
        await _upsert_chapter_state(
            db,
            latest_analyze,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.COMPLETED,
            error_message=None,
            completed_at=datetime.utcnow(),
        )

        latest_mark = await _latest_stage_run(db, active_task.id, StageName.MARK)
        if latest_mark is not None:
            latest_mark.status = StageStatus.COMPLETED.value if analyzed_count >= len(chapters) else StageStatus.PAUSED.value
            latest_mark.error_message = None
            latest_mark.completed_at = datetime.utcnow()
            latest_mark.chapters_total = len(chapters)
            latest_mark.chapters_done = analyzed_count
            await _upsert_chapter_state(
                db,
                latest_mark,
                chapter_index=chapter_idx,
                status=ChapterStateStatus.COMPLETED,
                error_message=None,
                completed_at=datetime.utcnow(),
            )

        await db.commit()
        await db.refresh(latest_analyze)
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_analyze,
            _parse_stage_config_snapshot(latest_analyze.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "analyze",
                    "status": "completed",
                },
                "missing_chapters": missing_chapter_indexes,
            },
        )

        if latest_mark is not None:
            await db.refresh(latest_mark)
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                latest_mark,
                _parse_stage_config_snapshot(latest_mark.config_snapshot_json) or stage_config_snapshot,
                extra={
                    "chapter_retry": {
                        "chapter_index": chapter_idx,
                        "stage": "mark",
                        "status": "completed",
                    },
                    "total_marked": plan.total_marked,
                    "missing_chapters": missing_chapter_indexes,
                },
            )

        return {
            "novel_id": novel_id,
            "task_id": active_task.id,
            "stage": StageName.ANALYZE.value,
            "chapter_idx": chapter_idx,
            "status": "completed",
            "analysis_updated": True,
            "mark_updated": True,
            "marked_segments_total": plan.total_marked,
            "analyzed_chapters": analyzed_count,
            "chapters_total": len(chapters),
            "missing_chapters": missing_chapter_indexes,
        }
    except AppError as exc:
        latest_analyze = await _latest_stage_run(db, active_task.id, StageName.ANALYZE) or latest_analyze
        await _upsert_chapter_state(
            db,
            latest_analyze,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.FAILED,
            error_message=exc.message,
            completed_at=datetime.utcnow(),
        )
        await _mark_stage_run_failure(
            db,
            latest_analyze,
            chapters_total=chapters_total,
            chapters_done=min(chapters_total, int(latest_analyze.chapters_done or retry_running_done)),
            error_message=exc.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_analyze,
            _parse_stage_config_snapshot(latest_analyze.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "analyze",
                    "status": "failed",
                },
                "error": _app_error_artifact_payload(exc),
            },
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive
        app_error = AppError(
            ErrorCode.STAGE_FAILED,
            "Analyze chapter retry failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"stage": StageName.ANALYZE.value, "chapter_idx": chapter_idx, "exception": exc.__class__.__name__},
        )
        latest_analyze = await _latest_stage_run(db, active_task.id, StageName.ANALYZE) or latest_analyze
        await _upsert_chapter_state(
            db,
            latest_analyze,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.FAILED,
            error_message=app_error.message,
            completed_at=datetime.utcnow(),
        )
        await _mark_stage_run_failure(
            db,
            latest_analyze,
            chapters_total=chapters_total,
            chapters_done=min(chapters_total, int(latest_analyze.chapters_done or retry_running_done)),
            error_message=app_error.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_analyze,
            _parse_stage_config_snapshot(latest_analyze.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "analyze",
                    "status": "failed",
                },
                "error": {
                    "code": app_error.code.value,
                    "message": app_error.message,
                    "details": app_error.details,
                    "traceback": traceback.format_exc(),
                },
            },
        )
        raise app_error from exc


async def _retry_rewrite_stage_chapter(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    chapter_idx: int,
    provider_id: str | None = None,
    rewrite_target_added_chars_override: int | None = None,
    force_rerun: bool = False,
    rewrite_window_mode_enabled: bool | None = None,
    rewrite_window_guardrail_enabled: bool | None = None,
    rewrite_window_audit_enabled: bool | None = None,
) -> dict[str, object]:
    chapters = await _load_task_chapters(db, active_task.id)
    chapters_total = len(chapters)
    chapter = _chapter_by_index(chapters, chapter_idx)
    config_snapshot = await _load_config_snapshot(db)
    provider = await _resolve_provider_for_execution(db, provider_id)
    api_key = decrypt_api_key(provider.api_key_encrypted)
    window_mode_snapshot = await _resolve_rewrite_window_mode_snapshot(
        db,
        novel_id=novel_id,
        task_id=active_task.id,
        request_enabled=rewrite_window_mode_enabled,
        request_guardrail_enabled=rewrite_window_guardrail_enabled,
        request_audit_enabled=rewrite_window_audit_enabled,
    )
    stage_config_snapshot = _build_stage_config_snapshot(
        config_snapshot,
        provider,
        rewrite_window_mode=window_mode_snapshot,
    )
    latest_rewrite = await _latest_stage_run(db, active_task.id, StageName.REWRITE)
    if latest_rewrite is None:
        latest_rewrite = await _create_stage_run(
            db,
            active_task,
            StageName.REWRITE,
            idempotency_key=None,
            config_snapshot=stage_config_snapshot,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_rewrite,
            stage_config_snapshot,
            extra={"trigger": "chapter_retry_bootstrap"},
        )
    elif latest_rewrite.config_snapshot_json != stage_config_snapshot.model_dump_json():
        latest_rewrite.config_snapshot_json = stage_config_snapshot.model_dump_json()
    retry_running_done = _chapter_retry_running_done(chapters_total, chapter_idx)
    latest_rewrite.status = StageStatus.RUNNING.value
    latest_rewrite.error_message = None
    latest_rewrite.completed_at = None
    latest_rewrite.chapters_total = chapters_total
    latest_rewrite.chapters_done = retry_running_done
    await _upsert_chapter_state(
        db,
        latest_rewrite,
        chapter_index=chapter_idx,
        status=ChapterStateStatus.RUNNING,
        error_message=None,
        started_at=datetime.utcnow(),
        completed_at=None,
    )
    await db.commit()
    await db.refresh(latest_rewrite)
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        latest_rewrite,
        _parse_stage_config_snapshot(latest_rewrite.config_snapshot_json) or stage_config_snapshot,
        extra={
            "chapter_retry": {
                "chapter_index": chapter_idx,
                "stage": "rewrite",
                "status": "running",
            },
            "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
        },
    )

    try:
        worker_pool = _get_worker_pool(request)

        rewrite_plan = _load_mark_plan(request, novel_id, active_task.id)
        plan_by_index = {item.chapter_index: item for item in rewrite_plan.chapters}
        chapter_plan = plan_by_index.get(chapter_idx)

        analysis_payload = load_analysis_aggregate(request.app.state.artifact_store, novel_id, active_task.id)
        analyses_by_chapter = _load_analysis_map(analysis_payload)
        analysis = analyses_by_chapter.get(chapter_idx)
        if analysis is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "analysis.json is missing chapter analysis entry required for rewrite retry",
                details={"chapter_idx": chapter_idx},
            )

        existing_map = _load_rewrite_results_map(request, novel_id, active_task.id)
        existing_results = existing_map.get(chapter_idx, [])
        pending_segments: list[Any] = []
        retained_results: list[RewriteResult] = []
        if chapter_plan is not None and chapter_plan.segments:
            if force_rerun:
                pending_segments = list(chapter_plan.segments)
                retained_results = []
            else:
                pending_segments, retained_results = _split_rewrite_segments_for_execution(chapter_plan, existing_results)
            adjusted_segments = _segments_with_added_chars_override(
                pending_segments,
                rewrite_target_added_chars_override,
            )
            segment_requests = [
                RewriteSegmentRequest(
                    novel_id=novel_id,
                    task_id=active_task.id,
                    chapter=chapter,
                    analysis=analysis,
                    segment=segment,
                    rewrite_rules=config_snapshot.rewrite_rules,
                    global_prompt=config_snapshot.global_prompt,
                    rewrite_general_guidance=config_snapshot.rewrite_general_guidance,
                    provider_type=CoreProviderType(provider.provider_type),
                    api_key=api_key,
                    base_url=provider.base_url,
                    model_name=provider.model_name,
                    generation={
                        "temperature": provider.temperature,
                        "max_tokens": provider.max_tokens,
                        "top_p": provider.top_p,
                        "presence_penalty": provider.presence_penalty,
                        "frequency_penalty": provider.frequency_penalty,
                    },
                    stage_run_seq=latest_rewrite.run_seq,
                    window_mode_enabled=window_mode_snapshot.enabled,
                    window_guardrail_enabled=window_mode_snapshot.guardrail_enabled,
                    window_audit_enabled=window_mode_snapshot.audit_enabled,
                )
                for segment in adjusted_segments
            ]

            async def _submit(item: RewriteSegmentRequest) -> RewriteResult:
                request_tokens = max(1, count_text_tokens(_segment_source_text(item.chapter, item.segment), model_name=item.model_name))
                return await worker_pool.submit(
                    lambda: execute_rewrite_segment(item),
                    provider_id=str(provider.id),
                    rpm_limit=provider.rpm_limit,
                    tpm_limit=provider.tpm_limit,
                    request_tokens=request_tokens,
                )

            executed_results = list(await asyncio.gather(*(_submit(item) for item in segment_requests)))
        else:
            executed_results = []

        chapter_results = [*retained_results, *executed_results]
        if chapter_plan is not None and chapter_plan.segments:
            by_segment_id = {item.segment_id: item for item in chapter_results if item.segment_id}
            ordered: list[RewriteResult] = []
            for segment in chapter_plan.segments:
                segment_id = getattr(segment, "segment_id", None)
                if segment_id and segment_id in by_segment_id:
                    ordered.append(by_segment_id[segment_id])
                    continue
                fallback = next(
                    (
                        item
                        for item in chapter_results
                        if tuple(item.paragraph_range) == tuple(getattr(segment, "paragraph_range", (0, 0)))
                    ),
                    None,
                )
                if fallback is not None and fallback not in ordered:
                    ordered.append(fallback)
            chapter_results = ordered or chapter_results

        existing_map[chapter_idx] = chapter_results
        merged_results = sorted(existing_map.items(), key=lambda item: item[0])
        _write_rewrite_artifacts(request, novel_id, active_task.id, merged_results)

        merged_results_by_chapter = {chapter_index: results for chapter_index, results in merged_results}
        failed_segments = _count_failed_rewrite_segments(merged_results_by_chapter)
        completed_chapters, pending_chapters = _rewrite_stage_progress(chapters, rewrite_plan, merged_results_by_chapter)
        latest_rewrite.status = StageStatus.COMPLETED.value if not pending_chapters else StageStatus.PAUSED.value
        latest_rewrite.error_message = None
        latest_rewrite.completed_at = datetime.utcnow()
        latest_rewrite.warnings_count = failed_segments
        latest_rewrite.chapters_total = len(chapters)
        latest_rewrite.chapters_done = completed_chapters
        await _upsert_chapter_state(
            db,
            latest_rewrite,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.COMPLETED,
            error_message=None,
            completed_at=datetime.utcnow(),
        )
        await db.commit()
        await db.refresh(latest_rewrite)

        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_rewrite,
            _parse_stage_config_snapshot(latest_rewrite.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "rewrite",
                    "status": "completed",
                    "segment_count": len(chapter_results),
                    "failed_segments": sum(1 for item in chapter_results if item.status == RewriteResultStatus.FAILED),
                },
                "failed_segments": failed_segments,
                "chapter_count": len(chapters),
                "chapters_completed": completed_chapters,
                "pending_chapters": pending_chapters,
                "rewrite_window_metrics": _rewrite_window_metrics_by_chapter(merged_results_by_chapter),
                "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
                "force_rerun": force_rerun,
            },
        )

        return {
            "novel_id": novel_id,
            "task_id": active_task.id,
            "stage": StageName.REWRITE.value,
            "chapter_idx": chapter_idx,
            "status": "completed",
            "segments_total": len(chapter_results),
            "failed_segments": sum(1 for item in chapter_results if item.status == RewriteResultStatus.FAILED),
            "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
            "force_rerun": force_rerun,
        }
    except AppError as exc:
        latest_rewrite = await _latest_stage_run(db, active_task.id, StageName.REWRITE) or latest_rewrite
        await _upsert_chapter_state(
            db,
            latest_rewrite,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.FAILED,
            error_message=exc.message,
            completed_at=datetime.utcnow(),
        )
        await _mark_stage_run_failure(
            db,
            latest_rewrite,
            chapters_total=chapters_total,
            chapters_done=min(chapters_total, int(latest_rewrite.chapters_done or retry_running_done)),
            error_message=exc.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_rewrite,
            _parse_stage_config_snapshot(latest_rewrite.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "rewrite",
                    "status": "failed",
                },
                "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
                "force_rerun": force_rerun,
                "error": _app_error_artifact_payload(exc),
            },
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive
        app_error = AppError(
            ErrorCode.STAGE_FAILED,
            "Rewrite chapter retry failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"stage": StageName.REWRITE.value, "chapter_idx": chapter_idx, "exception": exc.__class__.__name__},
        )
        latest_rewrite = await _latest_stage_run(db, active_task.id, StageName.REWRITE) or latest_rewrite
        await _upsert_chapter_state(
            db,
            latest_rewrite,
            chapter_index=chapter_idx,
            status=ChapterStateStatus.FAILED,
            error_message=app_error.message,
            completed_at=datetime.utcnow(),
        )
        await _mark_stage_run_failure(
            db,
            latest_rewrite,
            chapters_total=chapters_total,
            chapters_done=min(chapters_total, int(latest_rewrite.chapters_done or retry_running_done)),
            error_message=app_error.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_rewrite,
            _parse_stage_config_snapshot(latest_rewrite.config_snapshot_json) or stage_config_snapshot,
            extra={
                "chapter_retry": {
                    "chapter_index": chapter_idx,
                    "stage": "rewrite",
                    "status": "failed",
                },
                "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
                "force_rerun": force_rerun,
                "error": {
                    "code": app_error.code.value,
                    "message": app_error.message,
                    "details": app_error.details,
                    "traceback": traceback.format_exc(),
                },
            },
        )
        raise app_error from exc


async def _continue_analyze_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    latest_run: StageRun,
) -> StageRun:
    stage_config_snapshot = _parse_stage_config_snapshot(latest_run.config_snapshot_json)
    chapters = await _load_task_chapters(db, active_task.id)
    analysis_payload = load_analysis_aggregate(request.app.state.artifact_store, novel_id, active_task.id)
    analyses_by_chapter = _load_analysis_map(analysis_payload)
    pending_chapters = _pending_analyze_chapter_indexes(chapters, analyses_by_chapter)
    pending_set = set(pending_chapters)
    existing_rows = (
        await db.execute(select(ChapterState).where(ChapterState.stage_run_id == latest_run.id))
    ).scalars().all()
    existing_indexes = {int(row.chapter_index) for row in existing_rows}
    for chapter in chapters:
        if chapter.index in existing_indexes:
            continue
        status = ChapterStateStatus.PENDING if chapter.index in pending_set else ChapterStateStatus.COMPLETED
        await _upsert_chapter_state(
            db,
            latest_run,
            chapter_index=chapter.index,
            status=status,
            error_message=None,
            completed_at=datetime.utcnow() if status == ChapterStateStatus.COMPLETED else None,
        )
    await db.commit()
    analyzed_count = len(chapters) - len(pending_chapters)

    if pending_chapters:
        latest_run.status = StageStatus.RUNNING.value
        latest_run.error_message = None
        latest_run.completed_at = None
        latest_run.chapters_total = len(chapters)
        latest_run.chapters_done = analyzed_count
        await db.commit()
        await db.refresh(latest_run)
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_run,
            stage_config_snapshot,
            extra={"trigger": "resume_continue", "pending_chapters": pending_chapters},
        )
        try:
            for chapter_idx in pending_chapters:
                await _retry_analyze_stage_chapter(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    chapter_idx=chapter_idx,
                    provider_id=stage_config_snapshot.provider_id if stage_config_snapshot is not None else None,
                )
        except AppError as exc:
            latest_run = await _latest_stage_run(db, active_task.id, StageName.ANALYZE) or latest_run
            await _mark_stage_run_failure(
                db,
                latest_run,
                chapters_total=len(chapters),
                chapters_done=min(len(chapters), int(latest_run.chapters_done or 0)),
                error_message=exc.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                latest_run,
                _parse_stage_config_snapshot(latest_run.config_snapshot_json) or stage_config_snapshot,
                extra={"trigger": "resume_continue", "error": _app_error_artifact_payload(exc)},
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive
            app_error = AppError(
                ErrorCode.STAGE_FAILED,
                "Analyze continue execution failed",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                details={"stage": StageName.ANALYZE.value, "exception": exc.__class__.__name__},
            )
            latest_run = await _latest_stage_run(db, active_task.id, StageName.ANALYZE) or latest_run
            await _mark_stage_run_failure(
                db,
                latest_run,
                chapters_total=len(chapters),
                chapters_done=min(len(chapters), int(latest_run.chapters_done or 0)),
                error_message=app_error.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                latest_run,
                _parse_stage_config_snapshot(latest_run.config_snapshot_json) or stage_config_snapshot,
                extra={
                    "trigger": "resume_continue",
                    "error": {
                        "code": app_error.code.value,
                        "message": app_error.message,
                        "details": app_error.details,
                        "traceback": traceback.format_exc(),
                    },
                },
            )
            raise app_error from exc

        return await _latest_stage_run(db, active_task.id, StageName.ANALYZE) or latest_run

    latest_run.status = StageStatus.COMPLETED.value
    latest_run.error_message = None
    latest_run.completed_at = datetime.utcnow()
    latest_run.chapters_total = len(chapters)
    latest_run.chapters_done = len(chapters)
    await db.commit()
    await db.refresh(latest_run)
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        latest_run,
        stage_config_snapshot,
        extra={"trigger": "resume_continue", "pending_chapters": []},
    )
    return latest_run


async def _continue_rewrite_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    latest_run: StageRun,
) -> StageRun:
    stage_config_snapshot = _parse_stage_config_snapshot(latest_run.config_snapshot_json)
    chapters = await _load_task_chapters(db, active_task.id)
    rewrite_plan = _load_mark_plan(request, novel_id, active_task.id)
    rewrite_results_by_chapter = _load_rewrite_results_map(request, novel_id, active_task.id)
    completed_chapters, pending_chapters = _rewrite_stage_progress(chapters, rewrite_plan, rewrite_results_by_chapter)
    pending_set = set(pending_chapters)
    existing_rows = (
        await db.execute(select(ChapterState).where(ChapterState.stage_run_id == latest_run.id))
    ).scalars().all()
    existing_indexes = {int(row.chapter_index) for row in existing_rows}
    for chapter in chapters:
        if chapter.index in existing_indexes:
            continue
        if chapter.index in pending_set:
            status = ChapterStateStatus.PENDING
            completed_at = None
        else:
            status = ChapterStateStatus.COMPLETED
            completed_at = datetime.utcnow()
        await _upsert_chapter_state(
            db,
            latest_run,
            chapter_index=chapter.index,
            status=status,
            error_message=None,
            completed_at=completed_at,
        )
    await db.commit()
    failed_segments = _count_failed_rewrite_segments(rewrite_results_by_chapter)

    if pending_chapters:
        latest_run.status = StageStatus.RUNNING.value
        latest_run.error_message = None
        latest_run.completed_at = None
        latest_run.warnings_count = failed_segments
        latest_run.chapters_total = len(chapters)
        latest_run.chapters_done = completed_chapters
        await db.commit()
        await db.refresh(latest_run)
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            latest_run,
            stage_config_snapshot,
            extra={
                "trigger": "resume_continue",
                "failed_segments": failed_segments,
                "pending_chapters": pending_chapters,
                "rewrite_window_metrics": _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter),
            },
        )
        try:
            for chapter_idx in pending_chapters:
                await _retry_rewrite_stage_chapter(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    chapter_idx=chapter_idx,
                    provider_id=stage_config_snapshot.provider_id if stage_config_snapshot is not None else None,
                    rewrite_target_added_chars_override=None,
                    rewrite_window_mode_enabled=(
                        stage_config_snapshot.rewrite_window_mode.enabled if stage_config_snapshot is not None else None
                    ),
                    rewrite_window_guardrail_enabled=(
                        stage_config_snapshot.rewrite_window_mode.guardrail_enabled if stage_config_snapshot is not None else None
                    ),
                    rewrite_window_audit_enabled=(
                        stage_config_snapshot.rewrite_window_mode.audit_enabled if stage_config_snapshot is not None else None
                    ),
                )
        except AppError as exc:
            latest_run = await _latest_stage_run(db, active_task.id, StageName.REWRITE) or latest_run
            await _mark_stage_run_failure(
                db,
                latest_run,
                chapters_total=len(chapters),
                chapters_done=min(len(chapters), int(latest_run.chapters_done or 0)),
                error_message=exc.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                latest_run,
                _parse_stage_config_snapshot(latest_run.config_snapshot_json) or stage_config_snapshot,
                extra={"trigger": "resume_continue", "error": _app_error_artifact_payload(exc)},
            )
            raise
        except Exception as exc:  # pragma: no cover - defensive
            app_error = AppError(
                ErrorCode.STAGE_FAILED,
                "Rewrite continue execution failed",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                details={"stage": StageName.REWRITE.value, "exception": exc.__class__.__name__},
            )
            latest_run = await _latest_stage_run(db, active_task.id, StageName.REWRITE) or latest_run
            await _mark_stage_run_failure(
                db,
                latest_run,
                chapters_total=len(chapters),
                chapters_done=min(len(chapters), int(latest_run.chapters_done or 0)),
                error_message=app_error.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                latest_run,
                _parse_stage_config_snapshot(latest_run.config_snapshot_json) or stage_config_snapshot,
                extra={
                    "trigger": "resume_continue",
                    "error": {
                        "code": app_error.code.value,
                        "message": app_error.message,
                        "details": app_error.details,
                        "traceback": traceback.format_exc(),
                    },
                },
            )
            raise app_error from exc

        return await _latest_stage_run(db, active_task.id, StageName.REWRITE) or latest_run

    latest_run.status = StageStatus.COMPLETED.value
    latest_run.error_message = None
    latest_run.completed_at = datetime.utcnow()
    latest_run.warnings_count = failed_segments
    latest_run.chapters_total = len(chapters)
    latest_run.chapters_done = completed_chapters
    await db.commit()
    await db.refresh(latest_run)
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        latest_run,
        stage_config_snapshot,
        extra={
            "trigger": "resume_continue",
            "failed_segments": failed_segments,
            "pending_chapters": [],
            "rewrite_window_metrics": _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter),
        },
    )
    return latest_run


async def _run_mark_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    run: StageRun,
    stage_config_snapshot: StageConfigSnapshot,
) -> StageActionResponse:
    artifact_store = request.app.state.artifact_store
    chapters = await _load_task_chapters(db, active_task.id)
    run.chapters_total = len(chapters)
    await _load_config_snapshot(db)
    analysis_payload = load_analysis_aggregate(artifact_store, novel_id, active_task.id)
    analyses_by_chapter = _load_analysis_map(analysis_payload)

    missing = [chapter.index for chapter in chapters if chapter.index not in analyses_by_chapter]
    if missing:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "analysis.json is missing chapter analysis entries",
            details={"missing_chapters": missing},
        )

    plan = build_rewrite_plan(
        novel_id,
        chapters,
        analyses_by_chapter,
        [],
    )
    write_mark_artifacts(artifact_store, novel_id, active_task.id, plan)
    await _mark_stage_run_success(
        db,
        run,
        chapters_total=len(chapters),
        chapters_done=len(chapters),
    )
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        run,
        stage_config_snapshot,
    )
    return StageActionResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        stage=StageName.MARK,
        run=_to_run_info(run),
    )


async def _run_mark_stage_after_analyze(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    stage_config_snapshot: StageConfigSnapshot,
    idempotency_key: str | None,
) -> StageActionResponse:
    running_mark = await _running_stage_run(db, active_task.id, StageName.MARK)
    if running_mark is not None:
        running_mark.status = StageStatus.PAUSED.value
        running_mark.completed_at = datetime.utcnow()
        running_mark.error_message = "Superseded by auto mark run after analyze"
        await db.commit()

    mark_idempotency_key = f"{idempotency_key}:auto-mark" if idempotency_key else None
    mark_run = await _create_stage_run(
        db,
        active_task,
        StageName.MARK,
        idempotency_key=mark_idempotency_key,
        config_snapshot=stage_config_snapshot,
    )
    await _sync_stage_run_artifacts(request, novel_id, mark_run, stage_config_snapshot, extra={"trigger": "auto_after_analyze"})

    try:
        response = await _run_mark_stage(
            request=request,
            db=db,
            novel_id=novel_id,
            active_task=active_task,
            run=mark_run,
            stage_config_snapshot=stage_config_snapshot,
        )
    except AppError as exc:
        await _mark_stage_run_failure(
            db,
            mark_run,
            chapters_total=mark_run.chapters_total or 0,
            chapters_done=mark_run.chapters_done or 0,
            error_message=exc.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            mark_run,
            stage_config_snapshot,
            extra={"trigger": "auto_after_analyze", "error": _app_error_artifact_payload(exc)},
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive
        app_error = AppError(
            ErrorCode.STAGE_FAILED,
            "Auto mark stage execution failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={"stage": StageName.MARK.value, "exception": exc.__class__.__name__},
        )
        await _mark_stage_run_failure(
            db,
            mark_run,
            chapters_total=mark_run.chapters_total or 0,
            chapters_done=mark_run.chapters_done or 0,
            error_message=app_error.message,
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            mark_run,
            stage_config_snapshot,
            extra={
                "trigger": "auto_after_analyze",
                "error": {
                    "code": app_error.code.value,
                    "message": app_error.message,
                    "details": app_error.details,
                    "traceback": traceback.format_exc(),
                },
            },
        )
        raise app_error from exc

    return response


REWRITE_STAGE_NAME = "rewrite"
CHAPTER_REWRITE_FILE_TEMPLATE = "ch_{chapter_index:03d}_rewrites.json"
REWRITE_AGGREGATE_FILENAME = "rewrites.json"
REWRITE_TARGET_BUFFER_RATIO = 0.12
ASSEMBLE_STAGE_NAME = "assemble"
ASSEMBLE_TXT_FILENAME = "output.txt"
ASSEMBLE_COMPARE_FILENAME = "output.compare.txt"
ASSEMBLE_RESULT_FILENAME = "assemble_result.json"
ASSEMBLE_QUALITY_FILENAME = "quality_report.json"
ASSEMBLE_MANIFEST_FILENAME = "export_manifest.json"


def _mark_plan_path(request: Request, novel_id: str, task_id: str) -> Path:
    store = request.app.state.artifact_store
    return store.stage_dir(novel_id, task_id, "mark") / "mark_plan.json"


def _rewrite_stage_dir(request: Request, novel_id: str, task_id: str) -> Path:
    store = request.app.state.artifact_store
    return store.stage_dir(novel_id, task_id, REWRITE_STAGE_NAME)


def _rewrite_chapter_path(request: Request, novel_id: str, task_id: str, chapter_index: int) -> Path:
    return _rewrite_stage_dir(request, novel_id, task_id) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=chapter_index)


def _rewrite_aggregate_path(request: Request, novel_id: str, task_id: str) -> Path:
    return _rewrite_stage_dir(request, novel_id, task_id) / REWRITE_AGGREGATE_FILENAME


def _reset_rewrite_output_artifacts(request: Request, novel_id: str, task_id: str) -> None:
    """Clear chapter/aggregate rewrite payloads for a fresh global rewrite run."""
    stage_dir = _rewrite_stage_dir(request, novel_id, task_id)
    if not stage_dir.exists():
        return

    for chapter_path in stage_dir.glob("ch_*_rewrites.json"):
        chapter_path.unlink(missing_ok=True)
    _rewrite_aggregate_path(request, novel_id, task_id).unlink(missing_ok=True)


def _load_mark_plan(request: Request, novel_id: str, task_id: str) -> RewritePlan:
    path = _mark_plan_path(request, novel_id, task_id)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, "mark_plan.json not found for rewrite stage", status.HTTP_404_NOT_FOUND)
    return RewritePlan.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _assemble_stage_dir(request: Request, novel_id: str, task_id: str) -> Path:
    store = request.app.state.artifact_store
    return store.stage_dir(novel_id, task_id, ASSEMBLE_STAGE_NAME)


def _load_rewrite_results_map(
    request: Request,
    novel_id: str,
    task_id: str,
) -> dict[int, list[RewriteResult]]:
    aggregate_path = _rewrite_aggregate_path(request, novel_id, task_id)
    if not aggregate_path.exists():
        return {}

    payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
    chapter_entries = payload.get("chapters", [])
    if not isinstance(chapter_entries, list):
        raise AppError(ErrorCode.CONFIG_INVALID, "rewrite aggregate payload is invalid")

    mapped: dict[int, list[RewriteResult]] = {}
    for chapter_payload in chapter_entries:
        if not isinstance(chapter_payload, dict):
            continue
        chapter_index = int(chapter_payload.get("chapter_index") or 0)
        if chapter_index < 1:
            continue
        segments = chapter_payload.get("segments", [])
        if not isinstance(segments, list):
            segments = []
        mapped[chapter_index] = [RewriteResult.model_validate(item) for item in segments if isinstance(item, dict)]
    return mapped


def _segment_source_text(chapter: Chapter, segment: RewriteSegment) -> str:
    source_text, _ = extract_segment_source_text(chapter, segment)
    return source_text


def _distribute_total_target_chars(weights: list[int], total_target_chars: int) -> list[int]:
    if not weights:
        return []
    count = len(weights)
    target = max(count, int(total_target_chars))
    remaining = max(0, target - count)
    total_weight = sum(max(1, int(weight)) for weight in weights)
    if remaining == 0:
        return [1 for _ in weights]

    weighted = [remaining * (max(1, int(weight)) / total_weight) for weight in weights]
    floors = [int(value) for value in weighted]
    fractions = [value - floor for value, floor in zip(weighted, floors)]
    undistributed = remaining - sum(floors)
    if undistributed > 0:
        ranked_indexes = sorted(range(count), key=lambda index: fractions[index], reverse=True)
        for index in ranked_indexes[:undistributed]:
            floors[index] += 1
    return [1 + extra for extra in floors]


def _segment_with_target_chars(segment: RewriteSegment, target_chars: int) -> RewriteSegment:
    normalized_target_chars = max(1, int(target_chars))
    buffer = max(1, round(normalized_target_chars * REWRITE_TARGET_BUFFER_RATIO))
    target_chars_min = max(1, normalized_target_chars - buffer)
    target_chars_max = max(target_chars_min, normalized_target_chars + buffer)
    original_chars = max(1, int(segment.original_chars))
    target_ratio = normalized_target_chars / original_chars
    return segment.model_copy(
        update={
            "target_ratio": target_ratio,
            "target_chars": normalized_target_chars,
            "target_chars_min": target_chars_min,
            "target_chars_max": target_chars_max,
        }
    )


def _segments_with_added_chars_override(
    segments: list[RewriteSegment],
    rewrite_target_added_chars_override: int | None,
) -> list[RewriteSegment]:
    if rewrite_target_added_chars_override is None:
        return list(segments)
    if not segments:
        return []

    override_added = max(0, int(rewrite_target_added_chars_override))
    total_original_chars = sum(max(1, int(segment.original_chars)) for segment in segments)
    chapter_target_chars = total_original_chars + override_added
    weights = [max(1, int(segment.target_chars or segment.original_chars or 1)) for segment in segments]
    distributed_target_chars = _distribute_total_target_chars(weights, chapter_target_chars)
    return [
        _segment_with_target_chars(segment, target_chars)
        for segment, target_chars in zip(segments, distributed_target_chars)
    ]


def _rewrite_result_payload(result: RewriteResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _rewrite_window_metrics(segments: list[RewriteResult]) -> dict[str, int | float]:
    windows_total = 0
    windows_retried = 0
    windows_hard_failed = 0
    windows_rollback = 0
    windows_char_total = 0

    for item in segments:
        windows = list(item.rewrite_windows or [])
        attempts_by_window: dict[str, list[Any]] = {}
        for attempt in list(item.window_attempts or []):
            attempts_by_window.setdefault(attempt.window_id, []).append(attempt)

        if not windows:
            # Backward compatibility: old artifacts without window fields are
            # counted as one logical window per segment.
            windows_total += 1
            fallback_chars = int(item.actual_chars or item.original_chars or 0)
            windows_char_total += max(0, fallback_chars)
            continue

        windows_total += len(windows)
        for window in windows:
            windows_char_total += max(0, int(window.end_offset) - int(window.start_offset))
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

    denominator = max(1, windows_total)
    return {
        "windows_total": windows_total,
        "windows_retried": windows_retried,
        "windows_hard_failed": windows_hard_failed,
        "windows_rollback": windows_rollback,
        "windows_avg_chars": round(windows_char_total / denominator, 2) if windows_total > 0 else 0.0,
        "window_retry_rate": round(windows_retried / denominator, 4) if windows_total > 0 else 0.0,
        "window_hard_fail_rate": round(windows_hard_failed / denominator, 4) if windows_total > 0 else 0.0,
        "window_rollback_rate": round(windows_rollback / denominator, 4) if windows_total > 0 else 0.0,
    }


def _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter: dict[int, list[RewriteResult]]) -> dict[str, int | float]:
    summary = {
        "windows_total": 0,
        "windows_retried": 0,
        "windows_hard_failed": 0,
        "windows_rollback": 0,
        "windows_avg_chars": 0.0,
        "window_retry_rate": 0.0,
        "window_hard_fail_rate": 0.0,
        "window_rollback_rate": 0.0,
    }
    for results in rewrite_results_by_chapter.values():
        metrics = _rewrite_window_metrics(results)
        summary["windows_total"] += metrics["windows_total"]
        summary["windows_retried"] += metrics["windows_retried"]
        summary["windows_hard_failed"] += metrics["windows_hard_failed"]
        summary["windows_rollback"] += metrics["windows_rollback"]

    total = int(summary["windows_total"])
    denominator = max(1, total)
    summary["window_retry_rate"] = round(int(summary["windows_retried"]) / denominator, 4) if total > 0 else 0.0
    summary["window_hard_fail_rate"] = round(int(summary["windows_hard_failed"]) / denominator, 4) if total > 0 else 0.0
    summary["window_rollback_rate"] = round(int(summary["windows_rollback"]) / denominator, 4) if total > 0 else 0.0
    char_total = 0
    for results in rewrite_results_by_chapter.values():
        for item in results:
            windows = list(item.rewrite_windows or [])
            if not windows:
                char_total += max(0, int(item.actual_chars or item.original_chars or 0))
                continue
            char_total += sum(max(0, int(window.end_offset) - int(window.start_offset)) for window in windows)
    summary["windows_avg_chars"] = round(char_total / denominator, 2) if total > 0 else 0.0
    return summary


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
            "windows_avg_chars": 0.0,
            "window_retry_rate": 0.0,
            "window_hard_fail_rate": 0.0,
            "window_rollback_rate": 0.0,
        }

    terminal = {
        RewriteResultStatus.COMPLETED,
        RewriteResultStatus.ACCEPTED,
        RewriteResultStatus.ACCEPTED_EDITED,
        RewriteResultStatus.REJECTED,
        RewriteResultStatus.ROLLED_BACK,
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


def _write_rewrite_artifacts(
    request: Request,
    novel_id: str,
    task_id: str,
    chapter_results: list[tuple[int, list[RewriteResult]]],
) -> Path:
    stage_dir = _rewrite_stage_dir(request, novel_id, task_id)
    stage_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().isoformat()
    chapter_payloads: list[dict[str, Any]] = []
    for chapter_index, results in sorted(chapter_results, key=lambda item: item[0]):
        status_fields = _rewrite_payload_status_fields(results)
        payload = {
            "novel_id": novel_id,
            "task_id": task_id,
            "chapter_index": chapter_index,
            "updated_at": now,
            "segments": [_rewrite_result_payload(item) for item in results],
            "audit_trail": [],
            **status_fields,
        }
        request.app.state.artifact_store.ensure_json(
            _rewrite_chapter_path(request, novel_id, task_id, chapter_index),
            payload,
        )
        chapter_payloads.append(payload)

    aggregate_payload = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_count": len(chapter_payloads),
        "updated_at": now,
        "chapters": chapter_payloads,
    }
    aggregate_path = _rewrite_aggregate_path(request, novel_id, task_id)
    request.app.state.artifact_store.ensure_json(aggregate_path, aggregate_payload)
    return aggregate_path


async def _run_rewrite_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    run: StageRun,
    stage_config_snapshot: StageConfigSnapshot,
    provider_id: str | None = None,
    rewrite_target_added_chars_override: int | None = None,
) -> StageActionResponse:
    worker_pool = _get_worker_pool(request)
    chapters = await _load_task_chapters(db, active_task.id)
    run.chapters_total = len(chapters)
    await _initialize_missing_chapter_states(
        db,
        run,
        chapter_indexes=[chapter.index for chapter in chapters],
        default_status=ChapterStateStatus.PENDING,
    )
    await db.commit()
    if not chapters:
        _write_rewrite_artifacts(request, novel_id, active_task.id, [])
        await _mark_stage_run_success(db, run, chapters_total=0, chapters_done=0)
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            run,
            stage_config_snapshot,
            extra={"failed_segments": 0, "rewrite_window_metrics": _rewrite_window_metrics_by_chapter({})},
        )
        return StageActionResponse(
            novel_id=novel_id,
            task_id=active_task.id,
            stage=StageName.REWRITE,
            run=_to_run_info(run),
        )

    config_snapshot = await _load_config_snapshot(db)
    provider = await _resolve_provider_for_execution(db, provider_id)
    api_key = decrypt_api_key(provider.api_key_encrypted)
    rewrite_plan = _load_mark_plan(request, novel_id, active_task.id)
    loaded_results_map = _load_rewrite_results_map(request, novel_id, active_task.id)
    analysis_payload = load_analysis_aggregate(request.app.state.artifact_store, novel_id, active_task.id)
    analyses_by_chapter = _load_analysis_map(analysis_payload)

    chapter_indexes = {chapter.index for chapter in chapters}
    rewrite_results_by_chapter = {
        chapter_index: list(results)
        for chapter_index, results in loaded_results_map.items()
        if chapter_index in chapter_indexes
    }
    chapters_by_index = {chapter.index: chapter for chapter in chapters}
    plan_by_index = {chapter.chapter_index: chapter for chapter in rewrite_plan.chapters}
    failed_segments = _count_failed_rewrite_segments(rewrite_results_by_chapter)

    async def _publish_rewrite_progress(chapter_index: int) -> None:
        nonlocal failed_segments
        failed_segments = _count_failed_rewrite_segments(rewrite_results_by_chapter)
        completed_chapters, _ = _rewrite_stage_progress(chapters, rewrite_plan, rewrite_results_by_chapter)
        run.chapters_done = completed_chapters
        run.warnings_count = failed_segments
        await db.commit()
        await db.refresh(run)
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            run,
            stage_config_snapshot,
            extra={
                "chapter_progress": {
                    "chapter_index": chapter_index,
                    "chapters_done": run.chapters_done,
                    "chapters_total": len(chapters),
                },
                "failed_segments": failed_segments,
                "chapter_count": len(chapters),
                "rewrite_window_metrics": _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter),
                "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
            },
        )

    def _persist_rewrite_results() -> None:
        _write_rewrite_artifacts(
            request,
            novel_id,
            active_task.id,
            sorted(rewrite_results_by_chapter.items(), key=lambda item: item[0]),
        )

    for chapter in sorted(chapters, key=lambda item: item.index):
        chapter_plan = plan_by_index.get(chapter.index)
        existing_results = rewrite_results_by_chapter.get(chapter.index, [])
        if chapter_plan is None or not chapter_plan.segments:
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=chapter.index,
                status=ChapterStateStatus.COMPLETED,
                error_message=None,
                completed_at=datetime.utcnow(),
            )
            rewrite_results_by_chapter[chapter.index] = []
            _persist_rewrite_results()
            await _publish_rewrite_progress(chapter.index)
            continue

        if _rewrite_results_cover_chapter_plan(chapter_plan, existing_results):
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=chapter.index,
                status=ChapterStateStatus.COMPLETED,
                error_message=None,
                completed_at=datetime.utcnow(),
            )
            rewrite_results_by_chapter[chapter.index] = list(existing_results)
            _persist_rewrite_results()
            await _publish_rewrite_progress(chapter.index)
            continue

        await _upsert_chapter_state(
            db,
            run,
            chapter_index=chapter.index,
            status=ChapterStateStatus.RUNNING,
            error_message=None,
            started_at=datetime.utcnow(),
            completed_at=None,
        )
        await db.commit()
        analysis = analyses_by_chapter.get(chapter.index)
        if analysis is None:
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=chapter.index,
                status=ChapterStateStatus.FAILED,
                error_message="analysis.json is missing chapter analysis entries required for rewrite",
                completed_at=datetime.utcnow(),
            )
            await db.commit()
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "analysis.json is missing chapter analysis entries required for rewrite",
                details={"missing_chapters": [chapter.index]},
            )

        pending_segments, retained_results = _split_rewrite_segments_for_execution(chapter_plan, existing_results)
        adjusted_segments = _segments_with_added_chars_override(
            pending_segments,
            rewrite_target_added_chars_override,
        )
        segment_requests = [
            # Chapter-level added-chars target is distributed across marked segments.
            RewriteSegmentRequest(
                novel_id=novel_id,
                task_id=active_task.id,
                chapter=chapters_by_index[chapter.index],
                analysis=analysis,
                segment=segment,
                rewrite_rules=config_snapshot.rewrite_rules,
                global_prompt=config_snapshot.global_prompt,
                rewrite_general_guidance=config_snapshot.rewrite_general_guidance,
                provider_type=CoreProviderType(provider.provider_type),
                api_key=api_key,
                base_url=provider.base_url,
                model_name=provider.model_name,
                generation={
                    "temperature": provider.temperature,
                    "max_tokens": provider.max_tokens,
                    "top_p": provider.top_p,
                    "presence_penalty": provider.presence_penalty,
                    "frequency_penalty": provider.frequency_penalty,
                },
                stage_run_seq=run.run_seq,
                window_mode_enabled=bool(stage_config_snapshot.rewrite_window_mode.enabled),
                window_guardrail_enabled=bool(stage_config_snapshot.rewrite_window_mode.guardrail_enabled),
                window_audit_enabled=bool(stage_config_snapshot.rewrite_window_mode.audit_enabled),
            )
            for segment in adjusted_segments
        ]

        async def _submit(item: RewriteSegmentRequest) -> RewriteResult:
            request_tokens = max(1, count_text_tokens(_segment_source_text(item.chapter, item.segment), model_name=item.model_name))
            return await worker_pool.submit(
                lambda: execute_rewrite_segment(item),
                provider_id=str(provider.id),
                rpm_limit=provider.rpm_limit,
                tpm_limit=provider.tpm_limit,
                request_tokens=request_tokens,
            )

        try:
            results = list(await asyncio.gather(*(_submit(item) for item in segment_requests)))
        except AppError as exc:
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=chapter.index,
                status=ChapterStateStatus.FAILED,
                error_message=exc.message,
                completed_at=datetime.utcnow(),
            )
            await db.commit()
            raise
        except Exception:
            await _upsert_chapter_state(
                db,
                run,
                chapter_index=chapter.index,
                status=ChapterStateStatus.FAILED,
                error_message="Rewrite chapter execution failed",
                completed_at=datetime.utcnow(),
            )
            await db.commit()
            raise

        results = [*retained_results, *results]
        if chapter_plan.segments:
            by_segment_id = {item.segment_id: item for item in results if item.segment_id}
            ordered: list[RewriteResult] = []
            for segment in chapter_plan.segments:
                segment_id = getattr(segment, "segment_id", None)
                if segment_id and segment_id in by_segment_id:
                    ordered.append(by_segment_id[segment_id])
                    continue
                fallback = next(
                    (
                        item
                        for item in results
                        if tuple(item.paragraph_range) == tuple(getattr(segment, "paragraph_range", (0, 0)))
                    ),
                    None,
                )
                if fallback is not None and fallback not in ordered:
                    ordered.append(fallback)
            results = ordered or results

        await _upsert_chapter_state(
            db,
            run,
            chapter_index=chapter.index,
            status=ChapterStateStatus.COMPLETED,
            error_message=None,
            completed_at=datetime.utcnow(),
        )
        rewrite_results_by_chapter[chapter.index] = results
        _persist_rewrite_results()
        await _publish_rewrite_progress(chapter.index)

    _persist_rewrite_results()
    failed_segments = _count_failed_rewrite_segments(rewrite_results_by_chapter)
    completed_chapters, _ = _rewrite_stage_progress(chapters, rewrite_plan, rewrite_results_by_chapter)
    run.warnings_count = failed_segments
    await _mark_stage_run_success(
        db,
        run,
        chapters_total=len(chapters),
        chapters_done=completed_chapters,
    )
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        run,
        stage_config_snapshot,
        extra={
            "failed_segments": failed_segments,
            "chapter_count": len(chapters),
            "rewrite_window_metrics": _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter),
            "rewrite_target_added_chars_override": rewrite_target_added_chars_override,
        },
    )
    return StageActionResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        stage=StageName.REWRITE,
        run=_to_run_info(run),
    )


def _write_assemble_artifacts(
    request: Request,
    novel_id: str,
    task_id: str,
    assembled_payload: dict[str, Any],
) -> dict[str, str]:
    stage_dir = _assemble_stage_dir(request, novel_id, task_id)
    stage_dir.mkdir(parents=True, exist_ok=True)

    txt_path = stage_dir / ASSEMBLE_TXT_FILENAME
    txt_path.write_text(str(assembled_payload.get("assembled_text") or ""), encoding="utf-8")

    compare_path = stage_dir / ASSEMBLE_COMPARE_FILENAME
    compare_path.write_text(str(assembled_payload.get("compare_text") or ""), encoding="utf-8")

    result_path = stage_dir / ASSEMBLE_RESULT_FILENAME
    request.app.state.artifact_store.ensure_json(result_path, assembled_payload)

    quality_payload = assembled_payload.get("quality_report") if isinstance(assembled_payload.get("quality_report"), dict) else {}
    quality_payload = {
        **quality_payload,
        "novel_id": novel_id,
        "task_id": task_id,
        "stage": "assemble",
    }
    quality_path = stage_dir / ASSEMBLE_QUALITY_FILENAME
    request.app.state.artifact_store.ensure_json(quality_path, quality_payload)

    manifest_payload = assembled_payload.get("export_manifest") if isinstance(assembled_payload.get("export_manifest"), dict) else {}
    manifest_payload = {
        **manifest_payload,
        "novel_id": novel_id,
        "task_id": task_id,
    }
    manifest_path = stage_dir / ASSEMBLE_MANIFEST_FILENAME
    request.app.state.artifact_store.ensure_json(manifest_path, manifest_payload)
    return {
        "output_txt_path": str(txt_path),
        "compare_txt_path": str(compare_path),
        "assemble_result_path": str(result_path),
        "quality_report_path": str(quality_path),
        "export_manifest_path": str(manifest_path),
    }


def _resolve_rewrite_added_chars_override(payload: StageActionRequest | StageChapterRetryRequest | None) -> int | None:
    if payload is None:
        return None
    added = getattr(payload, "rewrite_target_added_chars", None)
    if added is not None:
        return int(added)
    legacy = getattr(payload, "rewrite_target_chars", None)
    if legacy is not None:
        return int(legacy)
    return None


async def _run_assemble_stage(
    *,
    request: Request,
    db: AsyncSession,
    novel_id: str,
    active_task: Task,
    run: StageRun,
    stage_config_snapshot: StageConfigSnapshot,
    force: bool,
) -> StageActionResponse:
    chapters = await _load_task_chapters(db, active_task.id)
    run.chapters_total = len(chapters)
    rewrite_results_by_chapter = _load_rewrite_results_map(request, novel_id, active_task.id)
    assembled = assemble_novel(
        novel_id,
        active_task.id,
        chapters,
        rewrite_results_by_chapter,
        stage_run_id=run.id,
        force=force,
    )
    assembled_payload = assemble_results_to_dict(assembled)
    artifact_paths = _write_assemble_artifacts(request, novel_id, active_task.id, assembled_payload)

    stats_payload = assembled_payload.get("stats") if isinstance(assembled_payload.get("stats"), dict) else {}
    run.warnings_count = int(stats_payload.get("warning_count") or 0)
    run.chapters_done = len(chapters)

    if assembled.blocked and not force:
        quality_payload = assembled_payload.get("quality_report") if isinstance(assembled_payload.get("quality_report"), dict) else {}
        raise AppError(
            ErrorCode.QUALITY_GATE_BLOCKED,
            "Assemble quality gate blocked export",
            status.HTTP_409_CONFLICT,
            details={
                "quality_report": quality_payload,
                "risk_signature": quality_payload.get("risk_signature"),
                **artifact_paths,
            },
        )

    await _mark_stage_run_success(
        db,
        run,
        chapters_total=len(chapters),
        chapters_done=len(chapters),
    )
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        run,
        stage_config_snapshot,
        extra={
            **artifact_paths,
            "blocked": bool(assembled_payload.get("blocked")),
            "stats": stats_payload,
            "risk_signature": (assembled_payload.get("risk_signature") or None),
        },
    )
    return StageActionResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        stage=StageName.ASSEMBLE,
        run=_to_run_info(run),
    )


def _split_artifact_paths(request: Request, novel_id: str, task_id: str) -> tuple[Path, Path]:
    store = request.app.state.artifact_store
    stage_dir = store.stage_dir(novel_id, task_id, "split")
    return stage_dir / "status.json", stage_dir / "chapters.json"


async def _load_split_preview_payload(request: Request, novel_id: str, task_id: str) -> dict[str, object] | None:
    status_path, chapters_path = _split_artifact_paths(request, novel_id, task_id)
    if not status_path.exists() or not chapters_path.exists():
        return None
    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    chapters_payload = json.loads(chapters_path.read_text(encoding="utf-8"))
    return {"status": status_payload, "chapters": chapters_payload}


def _build_split_response(
    novel_id: str,
    task_id: str,
    stage_run: StageRun,
    preview_token: str,
    source_revision: str,
    rules_version: str,
    boundary_hash: str,
    chapters: list[dict[str, object]],
) -> SplitStagePreviewResponse:
    return SplitStagePreviewResponse(
        novel_id=novel_id,
        task_id=task_id,
        run_id=stage_run.id,
        run_seq=stage_run.run_seq,
        preview_token=preview_token,
        source_revision=source_revision,
        rules_version=rules_version,
        boundary_hash=boundary_hash,
        estimated_chapters=len(chapters),
        chapters=[
            SplitPreviewChapterResponse(
                id=str(chapter["id"]),
                index=int(chapter["index"]),
                title=str(chapter["title"]),
                content=str(chapter["content"]),
                start_offset=int(chapter["start_offset"]),
                end_offset=int(chapter["end_offset"]),
                char_count=int(chapter["char_count"]),
                paragraph_count=int(chapter["paragraph_count"]),
            )
            for chapter in chapters
        ],
        created_at=stage_run.started_at or datetime.utcnow(),
    )


def _stage_window_metrics(
    request: Request,
    *,
    stage: StageName,
    novel_id: str,
    task_id: str,
) -> dict[str, int] | None:
    if stage != StageName.REWRITE:
        return None
    rewrite_results_by_chapter = _load_rewrite_results_map(request, novel_id, task_id)
    return _rewrite_window_metrics_by_chapter(rewrite_results_by_chapter)


@router.post("/{stage}/run")
async def run_stage(
    novel_id: str,
    stage: StageName,
    request: Request,
    payload: StageActionRequest | None = Body(default=None),
    run_idempotency_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    active_task = await _get_active_task_or_404(db, novel_id)
    idempotency_key = run_idempotency_key or (payload.run_idempotency_key if payload else None)
    force = payload.force if payload is not None else False
    split_rule_id = payload.split_rule_id if payload is not None else None
    rewrite_target_added_chars_override = _resolve_rewrite_added_chars_override(payload)
    rewrite_window_mode_enabled = payload.rewrite_window_mode_enabled if payload is not None else None
    rewrite_window_guardrail_enabled = payload.rewrite_window_guardrail_enabled if payload is not None else None
    rewrite_window_audit_enabled = payload.rewrite_window_audit_enabled if payload is not None else None
    config_snapshot = await _load_config_snapshot(db)
    provider_override_id = payload.provider_id if payload is not None else None
    if stage in {StageName.ANALYZE, StageName.REWRITE}:
        provider_snapshot = (
            await _load_provider_by_id_or_404(db, provider_override_id)
            if provider_override_id
            else await _get_active_provider_if_any(db)
        )
    else:
        provider_snapshot = await _get_active_provider_if_any(db)
    if stage == StageName.REWRITE:
        rewrite_window_mode_snapshot = await _resolve_rewrite_window_mode_snapshot(
            db,
            novel_id=novel_id,
            task_id=active_task.id,
            request_enabled=rewrite_window_mode_enabled,
            request_guardrail_enabled=rewrite_window_guardrail_enabled,
            request_audit_enabled=rewrite_window_audit_enabled,
        )
    else:
        rewrite_window_mode_snapshot = RewriteWindowModeSnapshot()
    stage_config_snapshot = _build_stage_config_snapshot(
        config_snapshot,
        provider_snapshot,
        rewrite_window_mode=rewrite_window_mode_snapshot,
    )

    if stage == StageName.SPLIT:
        if request is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "Request context missing", status.HTTP_500_INTERNAL_SERVER_ERROR)

        if idempotency_key:
            existing = (
                await db.execute(
                    select(StageRun).where(
                        StageRun.task_id == active_task.id,
                        StageRun.stage == stage.value,
                        StageRun.run_idempotency_key == idempotency_key,
                    )
                )
            ).scalars().first()
            if existing is not None:
                preview_payload = await _load_split_preview_payload(request, novel_id, active_task.id)
                if preview_payload is None:
                    raise AppError(
                        ErrorCode.PREVIEW_STALE,
                        "Existing split preview artifacts are missing",
                        status.HTTP_409_CONFLICT,
                    )
                status_payload = preview_payload["status"]
                chapters_payload = preview_payload["chapters"]
                return _build_split_response(
                    novel_id,
                    active_task.id,
                    existing,
                    str(status_payload["preview_token"]),
                    str(status_payload["source_revision"]),
                    str(status_payload["rules_version"]),
                    str(status_payload["boundary_hash"]),
                    _normalize_split_chapters(active_task.id, list(chapters_payload["chapters"])),
                ).model_dump()

        novel_file = request.app.state.artifact_store.novel_dir(novel_id) / "raw.txt"
        if not novel_file.exists():
            raise AppError(ErrorCode.NOT_FOUND, "raw.txt not found for split stage", status.HTTP_404_NOT_FOUND)

        raw_text = novel_file.read_text(encoding="utf-8")
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        rules_state = load_split_rules_state()
        preview = make_split_preview(
            novel_id,
            raw_text,
            source_revision,
            rules_state.rules_version,
            state=rules_state,
            selected_rule_id=split_rule_id,
        )
        chapters = _normalize_split_chapters(
            active_task.id,
            [_chapter_payload_from_model(chapter) for chapter in preview.chapters],
        )
        rule_name = preview.matched_lines[0].rule_name if preview.matched_lines else None

        latest = await _latest_stage_run(db, active_task.id, stage)
        next_seq = (latest.run_seq + 1) if latest else 1
        now = datetime.utcnow()
        run = StageRun(
            id=f"{active_task.id}-{stage.value}-{next_seq}",
            task_id=active_task.id,
            stage=stage.value,
            run_seq=next_seq,
            status=StageStatus.PAUSED.value,
            started_at=now,
            run_idempotency_key=idempotency_key,
            config_snapshot_json=stage_config_snapshot.model_dump_json(),
            chapters_total=len(chapters),
            chapters_done=0,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)

        store = request.app.state.artifact_store
        status_path, chapters_path = _split_artifact_paths(request, novel_id, active_task.id)
        store.ensure_json(
            chapters_path,
            {
                "novel_id": novel_id,
                "task_id": active_task.id,
                "preview_token": preview.preview_token,
                "chapters": chapters,
            },
        )
        store.ensure_json(
            status_path,
            {
                "novel_id": novel_id,
                "task_id": active_task.id,
                "stage_run_id": run.id,
                "run_seq": run.run_seq,
                "status": "paused",
                "source_revision": source_revision,
                "rules_version": preview.rules_version,
                "boundary_hash": preview.boundary_hash,
                "preview_token": preview.preview_token,
                "estimated_chapters": len(chapters),
                "rule_name": rule_name,
                "preview_valid": preview.preview_valid,
                "failure_reason": preview.failure_reason,
                "matched_count": preview.matched_count,
                "created_at": now.isoformat(),
            },
        )
        await _sync_stage_run_artifacts(
            request,
            novel_id,
            run,
            stage_config_snapshot,
            extra={
                "source_revision": source_revision,
                "rules_version": preview.rules_version,
                "boundary_hash": preview.boundary_hash,
                "preview_token": preview.preview_token,
                "rule_name": rule_name,
                "preview_valid": preview.preview_valid,
                "failure_reason": preview.failure_reason,
                "matched_count": preview.matched_count,
                "estimated_chapters": len(chapters),
                "status": "paused",
            },
        )
        return _build_split_response(
            novel_id,
            active_task.id,
            run,
            preview.preview_token,
            source_revision,
            preview.rules_version,
            preview.boundary_hash,
            chapters,
        ).model_dump()

    if idempotency_key:
        existing = await _stage_run_by_idempotency(db, active_task.id, stage, idempotency_key)
        if existing is not None:
            latest = await _latest_stage_run(db, active_task.id, stage)
            artifact_path = str(
                request.app.state.artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, existing.run_seq)
            )
            return StageActionResponse(
                novel_id=novel_id,
                task_id=active_task.id,
                stage=stage,
                run=_to_run_info(existing, artifact_path=artifact_path, is_latest=latest is not None and latest.id == existing.id),
            ).model_dump()

    running = await _running_stage_run(db, active_task.id, stage)
    if running is not None:
        latest = await _latest_stage_run(db, active_task.id, stage)
        artifact_path = str(
            request.app.state.artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, running.run_seq)
        )
        return StageActionResponse(
            novel_id=novel_id,
            task_id=active_task.id,
            stage=stage,
            run=_to_run_info(running, artifact_path=artifact_path, is_latest=latest is not None and latest.id == running.id),
        ).model_dump()

    if stage in {StageName.ANALYZE, StageName.MARK, StageName.REWRITE, StageName.ASSEMBLE}:
        run = await _create_stage_run(
            db,
            active_task,
            stage,
            idempotency_key=idempotency_key,
            config_snapshot=stage_config_snapshot,
        )
        await _sync_stage_run_artifacts(request, novel_id, run, stage_config_snapshot)
        try:
            if stage == StageName.ANALYZE:
                analyze_response = await _run_analyze_stage(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    run=run,
                    stage_config_snapshot=stage_config_snapshot,
                    provider_id=provider_snapshot.id if provider_snapshot is not None else None,
                )
                await _run_mark_stage_after_analyze(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    stage_config_snapshot=stage_config_snapshot,
                    idempotency_key=idempotency_key,
                )
                return analyze_response.model_dump()
            if stage == StageName.REWRITE:
                return (await _run_rewrite_stage(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    run=run,
                    stage_config_snapshot=stage_config_snapshot,
                    provider_id=provider_snapshot.id if provider_snapshot is not None else None,
                    rewrite_target_added_chars_override=rewrite_target_added_chars_override,
                )).model_dump()
            if stage == StageName.ASSEMBLE:
                return (await _run_assemble_stage(
                    request=request,
                    db=db,
                    novel_id=novel_id,
                    active_task=active_task,
                    run=run,
                    stage_config_snapshot=stage_config_snapshot,
                    force=force,
                )).model_dump()
            return (await _run_mark_stage(
                request=request,
                db=db,
                novel_id=novel_id,
                active_task=active_task,
                run=run,
                stage_config_snapshot=stage_config_snapshot,
            )).model_dump()
        except AppError as exc:
            await _mark_stage_run_failure(
                db,
                run,
                chapters_total=run.chapters_total or 0,
                chapters_done=run.chapters_done or 0,
                error_message=exc.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                run,
                stage_config_snapshot,
                extra={"error": _app_error_artifact_payload(exc)},
            )
            raise
        except Exception as exc:
            app_error = AppError(
                ErrorCode.STAGE_FAILED,
                "Stage execution failed",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                details={"stage": stage.value, "exception": exc.__class__.__name__},
            )
            await _mark_stage_run_failure(
                db,
                run,
                chapters_total=run.chapters_total or 0,
                chapters_done=run.chapters_done or 0,
                error_message=app_error.message,
            )
            await _sync_stage_run_artifacts(
                request,
                novel_id,
                run,
                stage_config_snapshot,
                extra={
                    "error": {
                        "code": app_error.code.value,
                        "message": app_error.message,
                        "details": app_error.details,
                        "traceback": traceback.format_exc(),
                    }
                },
            )
            raise app_error from exc

    latest = await _latest_stage_run(db, active_task.id, stage)
    next_seq = (latest.run_seq + 1) if latest else 1
    now = datetime.utcnow()
    run = StageRun(
        id=f"{active_task.id}-{stage.value}-{next_seq}",
        task_id=active_task.id,
        stage=stage.value,
        run_seq=next_seq,
        status=StageStatus.RUNNING.value,
        started_at=now,
        run_idempotency_key=idempotency_key,
        config_snapshot_json=stage_config_snapshot.model_dump_json(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await _sync_stage_run_artifacts(request, novel_id, run, stage_config_snapshot)
    return StageActionResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        stage=stage,
        run=_to_run_info(
            run,
            artifact_path=str(request.app.state.artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, run.run_seq)),
            is_latest=True,
        ),
    ).model_dump()


@router.post("/{stage}/pause", response_model=StageActionResponse)
async def pause_stage(
    novel_id: str,
    stage: StageName,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> StageActionResponse:
    active_task = await _get_active_task_or_404(db, novel_id)
    latest = await _latest_stage_run(db, active_task.id, stage)
    if latest is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Stage `{stage.value}` has no runs", status.HTTP_404_NOT_FOUND)
    latest.status = StageStatus.PAUSED.value
    await db.commit()
    await db.refresh(latest)
    await _sync_stage_run_artifacts(request, novel_id, latest, _parse_stage_config_snapshot(latest.config_snapshot_json))
    return StageActionResponse(novel_id=novel_id, task_id=active_task.id, stage=stage, run=_to_run_info(latest))


@router.post("/{stage}/resume", response_model=StageActionResponse)
async def resume_stage(
    novel_id: str,
    stage: StageName,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> StageActionResponse:
    active_task = await _get_active_task_or_404(db, novel_id)
    latest = await _latest_stage_run(db, active_task.id, stage)
    if latest is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Stage `{stage.value}` has no runs", status.HTTP_404_NOT_FOUND)

    if stage == StageName.ANALYZE:
        latest = await _continue_analyze_stage(
            request=request,
            db=db,
            novel_id=novel_id,
            active_task=active_task,
            latest_run=latest,
        )
        return StageActionResponse(novel_id=novel_id, task_id=active_task.id, stage=stage, run=_to_run_info(latest))

    if stage == StageName.REWRITE:
        latest = await _continue_rewrite_stage(
            request=request,
            db=db,
            novel_id=novel_id,
            active_task=active_task,
            latest_run=latest,
        )
        return StageActionResponse(novel_id=novel_id, task_id=active_task.id, stage=stage, run=_to_run_info(latest))

    latest.status = StageStatus.RUNNING.value
    await db.commit()
    await db.refresh(latest)
    await _sync_stage_run_artifacts(request, novel_id, latest, _parse_stage_config_snapshot(latest.config_snapshot_json))
    return StageActionResponse(novel_id=novel_id, task_id=active_task.id, stage=stage, run=_to_run_info(latest))


@router.post("/{stage}/retry", response_model=StageActionResponse)
async def retry_stage(
    novel_id: str,
    stage: StageName,
    request: Request,
    payload: StageActionRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> StageActionResponse:
    return await run_stage(
        novel_id,
        stage,
        request=request,
        payload=payload or StageActionRequest(),
        run_idempotency_key=None,
        db=db,
    )


@router.post("/split/confirm", response_model=SplitStageConfirmResponse)
async def confirm_split(
    novel_id: str,
    request: Request,
    payload: SplitConfirmRequest | None = Body(default=None),
    preview_token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> SplitStageConfirmResponse:
    active_task = await _get_active_task_or_404(db, novel_id)
    if request is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "Request context missing", status.HTTP_500_INTERNAL_SERVER_ERROR)
    token = payload.preview_token if payload else preview_token
    if not token:
        raise AppError(ErrorCode.VALIDATION_ERROR, "preview_token is required", status.HTTP_400_BAD_REQUEST)

    preview_payload = await _load_split_preview_payload(request, novel_id, active_task.id)
    if preview_payload is None:
        raise AppError(ErrorCode.PREVIEW_STALE, "Split preview has expired", status.HTTP_409_CONFLICT)

    status_payload = preview_payload["status"]
    chapters_payload = preview_payload["chapters"]
    if token != status_payload["preview_token"]:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token does not match current split preview", status.HTTP_409_CONFLICT)

    novel_file = request.app.state.artifact_store.novel_dir(novel_id) / "raw.txt"
    if not novel_file.exists():
        raise AppError(ErrorCode.NOT_FOUND, "raw.txt not found for split stage", status.HTTP_404_NOT_FOUND)
    raw_text = novel_file.read_text(encoding="utf-8")
    current_source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    rules_state = load_split_rules_state()

    expected_source_revision = str(status_payload["source_revision"])
    expected_rules_version = str(status_payload["rules_version"])
    expected_boundary_hash = str(status_payload["boundary_hash"])
    if current_source_revision != expected_source_revision:
        raise AppError(ErrorCode.PREVIEW_STALE, "Split source revision changed, please rerun preview", status.HTTP_409_CONFLICT)
    if rules_state.rules_version != expected_rules_version:
        raise AppError(ErrorCode.PREVIEW_STALE, "Split rules changed, please rerun preview", status.HTTP_409_CONFLICT)

    token_payload = validate_preview_token(
        token,
        novel_id=novel_id,
        source_revision=expected_source_revision,
        rules_version=expected_rules_version,
        boundary_hash=expected_boundary_hash,
    )
    recomputed_preview = make_split_preview(
        novel_id,
        raw_text,
        current_source_revision,
        rules_state.rules_version,
        state=rules_state,
        selected_rule_id=token_payload.selected_rule_id,
    )
    if recomputed_preview.boundary_hash != expected_boundary_hash:
        raise AppError(ErrorCode.PREVIEW_STALE, "Split preview boundary changed, please rerun preview", status.HTTP_409_CONFLICT)

    chapter_payloads = _normalize_split_chapters(active_task.id, list(chapters_payload["chapters"]))
    await db.execute(delete(ChapterRow).where(ChapterRow.task_id == active_task.id))
    db.add_all(
        [
            ChapterRow(
                id=str(chapter["id"]),
                task_id=active_task.id,
                chapter_index=int(chapter["index"]),
                title=str(chapter["title"]),
                content=str(chapter["content"]),
                start_offset=int(chapter["start_offset"]),
                end_offset=int(chapter["end_offset"]),
                char_count=int(chapter["char_count"]),
                paragraph_count=int(chapter["paragraph_count"]),
            )
            for chapter in chapter_payloads
        ]
    )

    latest = await _latest_stage_run(db, active_task.id, StageName.SPLIT)
    now = datetime.utcnow()
    if latest is None:
        raise AppError(ErrorCode.NOT_FOUND, "Split stage run not found", status.HTTP_404_NOT_FOUND)
    latest.status = StageStatus.COMPLETED.value
    latest.completed_at = now
    latest.chapters_total = len(chapter_payloads)
    latest.chapters_done = len(chapter_payloads)
    await db.commit()
    await db.refresh(latest)
    await _sync_stage_run_artifacts(
        request,
        novel_id,
        latest,
        _parse_stage_config_snapshot(latest.config_snapshot_json),
        extra={
            "status": "completed",
            "completed_at": now.isoformat(),
            "chapter_count": len(chapter_payloads),
            "preview_token": token,
            "source_revision": status_payload["source_revision"],
            "rules_version": status_payload["rules_version"],
            "boundary_hash": status_payload["boundary_hash"],
            "confirmed": True,
        },
    )

    store = request.app.state.artifact_store
    status_path, chapters_path = _split_artifact_paths(request, novel_id, active_task.id)
    store.ensure_json(
        chapters_path,
        {
            "novel_id": novel_id,
            "task_id": active_task.id,
            "preview_token": token,
            "chapters": chapter_payloads,
            "confirmed": True,
        },
    )
    store.ensure_json(
        status_path,
        {
            "novel_id": novel_id,
            "task_id": active_task.id,
            "stage_run_id": latest.id,
            "run_seq": latest.run_seq,
            "status": "completed",
            "preview_token": token,
            "source_revision": status_payload["source_revision"],
            "rules_version": status_payload["rules_version"],
            "boundary_hash": status_payload["boundary_hash"],
            "chapter_count": len(chapter_payloads),
            "confirmed_at": now.isoformat(),
        },
    )
    return SplitStageConfirmResponse(
        novel_id=novel_id,
        task_id=active_task.id,
        preview_token=token,
        chapter_count=len(chapter_payloads),
        run_id=latest.id,
        run_seq=latest.run_seq,
        completed_at=now,
    )


@router.get("/{stage}/run")
async def get_stage_latest_run(
    novel_id: str,
    stage: StageName,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    active_task = await _get_active_task_or_404(db, novel_id)
    latest = await _latest_stage_run(db, active_task.id, stage)
    if latest is None:
        return {"novel_id": novel_id, "stage": stage.value, "run": None}
    artifact_store = request.app.state.artifact_store
    artifact_path = str(artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, latest.run_seq))
    window_metrics = _stage_window_metrics(
        request,
        stage=stage,
        novel_id=novel_id,
        task_id=active_task.id,
    )
    return {
        "novel_id": novel_id,
        "stage": stage.value,
        "run": _to_run_info(latest, artifact_path=artifact_path, is_latest=True).model_dump(),
        "window_metrics": window_metrics,
    }


@router.get("/{stage}/runs")
async def list_stage_runs(
    novel_id: str,
    stage: StageName,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    active_task = await _get_active_task_or_404(db, novel_id)
    artifact_store = request.app.state.artifact_store
    rows = (
        await db.execute(
            select(StageRun)
            .where(StageRun.task_id == active_task.id, StageRun.stage == stage.value)
            .order_by(StageRun.run_seq.desc())
        )
    ).scalars().all()
    latest_id = rows[0].id if rows else None
    data = [
        _to_run_info(
            item,
            artifact_path=str(artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, item.run_seq)),
            is_latest=item.id == latest_id,
        ).model_dump()
        for item in rows
    ]
    return {"data": data, "total": len(data)}


@router.get("/{stage}/runs/{run_seq}")
async def get_stage_run_detail(
    novel_id: str,
    stage: StageName,
    run_seq: int,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    active_task = await _get_active_task_or_404(db, novel_id)
    run = await _stage_run_by_seq(db, active_task.id, stage, run_seq)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Stage `{stage.value}` run `{run_seq}` not found", status.HTTP_404_NOT_FOUND)
    latest = await _latest_stage_run(db, active_task.id, stage)
    artifact_store = request.app.state.artifact_store
    artifact_path = str(artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, run.run_seq))
    window_metrics = _stage_window_metrics(
        request,
        stage=stage,
        novel_id=novel_id,
        task_id=active_task.id,
    )
    return {
        "novel_id": novel_id,
        "stage": stage.value,
        "run": _to_run_info(run, artifact_path=artifact_path, is_latest=latest is not None and latest.id == run.id).model_dump(),
        "window_metrics": window_metrics,
    }


@router.post("/{stage}/chapters/{chapter_idx}/retry")
async def retry_stage_chapter(
    novel_id: str,
    stage: StageName,
    chapter_idx: int,
    request: Request,
    payload: StageChapterRetryRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    if chapter_idx < 1:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "chapter_idx must be greater than or equal to 1",
            status.HTTP_400_BAD_REQUEST,
        )

    if stage not in {StageName.ANALYZE, StageName.REWRITE}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Chapter retry is only supported for analyze and rewrite stages",
            status.HTTP_400_BAD_REQUEST,
            details={"stage": stage.value},
        )

    active_task = await _get_active_task_or_404(db, novel_id)
    if stage == StageName.ANALYZE:
        return await _retry_analyze_stage_chapter(
            request=request,
            db=db,
            novel_id=novel_id,
            active_task=active_task,
            chapter_idx=chapter_idx,
            provider_id=payload.provider_id if payload is not None else None,
        )
    return await _retry_rewrite_stage_chapter(
        request=request,
        db=db,
        novel_id=novel_id,
        active_task=active_task,
        chapter_idx=chapter_idx,
        provider_id=payload.provider_id if payload is not None else None,
        rewrite_target_added_chars_override=_resolve_rewrite_added_chars_override(payload),
        force_rerun=bool(payload.force_rerun) if payload is not None else False,
        rewrite_window_mode_enabled=payload.rewrite_window_mode_enabled if payload is not None else None,
        rewrite_window_guardrail_enabled=payload.rewrite_window_guardrail_enabled if payload is not None else None,
        rewrite_window_audit_enabled=payload.rewrite_window_audit_enabled if payload is not None else None,
    )


@router.get("/{stage}/artifact")
async def get_stage_artifact(
    novel_id: str,
    stage: StageName,
    request: Request,
    format: str = Query(default="json"),
    run_seq: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    active_task = await _get_active_task_or_404(db, novel_id)
    if run_seq is None:
        run = await _latest_stage_run(db, active_task.id, stage)
    else:
        run = await _stage_run_by_seq(db, active_task.id, stage, run_seq)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Stage `{stage.value}` run not found", status.HTTP_404_NOT_FOUND)

    artifact_store = request.app.state.artifact_store
    manifest_path = artifact_store.stage_run_manifest_path(novel_id, active_task.id, stage.value, run.run_seq)
    latest_manifest_path = artifact_store.stage_run_latest_manifest_path(novel_id, active_task.id, stage.value)
    artifact = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    latest_artifact = json.loads(latest_manifest_path.read_text(encoding="utf-8")) if latest_manifest_path.exists() else None
    latest = await _latest_stage_run(db, active_task.id, stage)
    return {
        "novel_id": novel_id,
        "stage": stage.value,
        "format": format,
        "run_seq": run.run_seq,
        "run": _to_run_info(
            run,
            artifact_path=str(manifest_path),
            is_latest=latest is not None and latest.id == run.id,
        ).model_dump(),
        "artifact_path": str(manifest_path),
        "latest_artifact_path": str(latest_manifest_path),
        "artifact": sanitize_public_payload(artifact),
        "latest_artifact": sanitize_public_payload(latest_artifact),
    }
