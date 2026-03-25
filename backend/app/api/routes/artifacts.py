from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.schemas import ArtifactScaffoldResponse, OrphanArtifactListResponse, OrphanArtifactResponse
from backend.app.core.artifact_store import ArtifactStore, STAGE_NAMES
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.settings import get_settings
from backend.app.db import Chapter as ChapterRow
from backend.app.db import StageRun, Task, get_db_session
from backend.app.models.core import RewritePlan, StageName, StageStatus
from backend.app.services import analyze_pipeline
from backend.app.services.export_renderers import (
    build_rewrite_zip,
    build_split_zip,
    json_bytes,
    render_analysis_markdown,
    render_mark_markdown,
    render_rewrite_diff,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _artifact_store(request: Request) -> ArtifactStore:
    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        raise RuntimeError("artifact_store is not configured")
    return store


async def _get_task_or_404(db: AsyncSession, novel_id: str, task_id: str | None) -> Task:
    if task_id is not None:
        row = await db.get(Task, task_id)
        if row is None or row.novel_id != novel_id:
            raise AppError(ErrorCode.NOT_FOUND, f"Task `{task_id}` not found for novel `{novel_id}`", status.HTTP_404_NOT_FOUND)
        return row

    row = (
        await db.execute(
            select(Task)
            .where(Task.novel_id == novel_id, Task.status == "active")
            .order_by(Task.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Active task for novel `{novel_id}` not found", status.HTTP_404_NOT_FOUND)
    return row


async def _get_completed_stage_run_or_404(db: AsyncSession, task_id: str, stage: StageName) -> StageRun:
    row = (
        await db.execute(
            select(StageRun)
            .where(StageRun.task_id == task_id, StageRun.stage == stage.value)
            .order_by(StageRun.run_seq.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Stage `{stage.value}` has not been run for task `{task_id}`", status.HTTP_404_NOT_FOUND)
    if row.status != StageStatus.COMPLETED.value:
        raise AppError(
            ErrorCode.STAGE_FAILED,
            f"Stage `{stage.value}` is not completed for task `{task_id}`",
            status.HTTP_409_CONFLICT,
            details={"stage": stage.value, "status": row.status, "run_seq": row.run_seq},
        )
    return row


def _artifact_response(*, body: bytes, filename: str, media_type: str) -> Response:
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=body, media_type=media_type, headers=headers)


async def _load_chapter_rows(db: AsyncSession, task_id: str) -> list[ChapterRow]:
    rows = (
        await db.execute(
            select(ChapterRow)
            .where(ChapterRow.task_id == task_id)
            .order_by(ChapterRow.chapter_index.asc())
        )
    ).scalars().all()
    return list(rows)


@router.get("/orphans", response_model=OrphanArtifactListResponse)
async def list_orphans(novel_id: str | None = Query(default=None)) -> OrphanArtifactListResponse:
    store = ArtifactStore(get_settings().data_dir)
    orphans = store.detect_orphans(novel_id=novel_id)
    return OrphanArtifactListResponse(
        data=[OrphanArtifactResponse(**asdict(item)) for item in orphans],
        total=len(orphans),
    )


@router.post("/novels/{novel_id}/tasks/{task_id}/scaffold", response_model=ArtifactScaffoldResponse)
async def create_task_scaffold(novel_id: str, task_id: str) -> ArtifactScaffoldResponse:
    store = ArtifactStore(get_settings().data_dir)
    store.ensure_novel_dirs(novel_id)
    store.ensure_task_scaffold(novel_id, task_id)
    store.write_active_task_id(novel_id, task_id)
    return ArtifactScaffoldResponse(
        novel_id=novel_id,
        task_id=task_id,
        created_stages=list(STAGE_NAMES),
        active_task_id=task_id,
    )


@router.get("/novels/{novel_id}/stages/{stage}/artifact")
async def export_stage_artifact(
    novel_id: str,
    stage: StageName,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    task_id: str | None = Query(default=None),
    chapter_index: int | None = Query(default=None, ge=1),
    format: Literal["json", "markdown", "diff", "zip"] = Query(default="json"),
) -> Response:
    task = await _get_task_or_404(db, novel_id, task_id)
    await _get_completed_stage_run_or_404(db, task.id, stage)
    store = _artifact_store(request)

    if stage == StageName.SPLIT:
        split_path = store.stage_dir(novel_id, task.id, stage.value) / "chapters.json"
        if not split_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Split artifact for task `{task.id}` not found", status.HTTP_404_NOT_FOUND)
        payload = json.loads(split_path.read_text(encoding="utf-8"))
        chapters = list(payload.get("chapters", []))
        if chapter_index is not None:
            if format == "zip":
                raise AppError(ErrorCode.VALIDATION_ERROR, "chapter_index is not supported with zip export", status.HTTP_400_BAD_REQUEST)
            chapter = next((item for item in chapters if int(item.get("index") or 0) == chapter_index), None)
            if chapter is None:
                raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_index}` not found", status.HTTP_404_NOT_FOUND)
            if format != "json":
                raise AppError(ErrorCode.VALIDATION_ERROR, "Split single-chapter export only supports json", status.HTTP_400_BAD_REQUEST)
            return _artifact_response(body=json_bytes(chapter), filename=f"split_ch_{chapter_index:03d}.json", media_type="application/json")
        if format == "zip":
            return _artifact_response(body=build_split_zip(chapters), filename=f"split_{task.id}.zip", media_type="application/zip")
        if format == "json":
            return _artifact_response(body=json_bytes(payload), filename="split_chapters.json", media_type="application/json")
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for split exports", status.HTTP_400_BAD_REQUEST)

    if stage == StageName.ANALYZE:
        aggregate_path = store.stage_dir(novel_id, task.id, stage.value) / "analysis.json"
        if not aggregate_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Analyze artifact for task `{task.id}` not found", status.HTTP_404_NOT_FOUND)
        if chapter_index is None:
            aggregate = analyze_pipeline.load_analysis_aggregate(store, novel_id, task.id)
            chapters = list(aggregate.get("chapters", []))
            if format == "json":
                return _artifact_response(body=json_bytes(aggregate), filename="analyze.json", media_type="application/json")
            if format == "markdown":
                return _artifact_response(body=render_analysis_markdown(chapters).encode("utf-8"), filename="analyze.md", media_type="text/markdown; charset=utf-8")
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for analyze exports", status.HTTP_400_BAD_REQUEST)
        chapter_path = analyze_pipeline.chapter_analysis_path(store, novel_id, task.id, chapter_index)
        if not chapter_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Analyze chapter `{chapter_index}` not found", status.HTTP_404_NOT_FOUND)
        chapter_payload = json.loads(chapter_path.read_text(encoding="utf-8"))
        if format == "json":
            return _artifact_response(body=json_bytes(chapter_payload), filename=f"analyze_ch_{chapter_index:03d}.json", media_type="application/json")
        if format == "markdown":
            return _artifact_response(body=render_analysis_markdown([chapter_payload], chapter_index=chapter_index).encode("utf-8"), filename=f"analyze_ch_{chapter_index:03d}.md", media_type="text/markdown; charset=utf-8")
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for analyze chapter exports", status.HTTP_400_BAD_REQUEST)

    if stage == StageName.MARK:
        mark_plan_path = store.stage_dir(novel_id, task.id, stage.value) / "mark_plan.json"
        if not mark_plan_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Mark artifact for task `{task.id}` not found", status.HTTP_404_NOT_FOUND)
        plan = RewritePlan.model_validate(json.loads(mark_plan_path.read_text(encoding="utf-8")))
        if chapter_index is None:
            if format == "json":
                return _artifact_response(body=json_bytes(plan.model_dump(mode="json")), filename="mark_plan.json", media_type="application/json")
            if format == "markdown":
                return _artifact_response(body=render_mark_markdown(plan).encode("utf-8"), filename="mark_plan.md", media_type="text/markdown; charset=utf-8")
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for mark exports", status.HTTP_400_BAD_REQUEST)
        chapter_plan = next((chapter for chapter in plan.chapters if chapter.chapter_index == chapter_index), None)
        if chapter_plan is None:
            raise AppError(ErrorCode.NOT_FOUND, f"Mark chapter `{chapter_index}` not found", status.HTTP_404_NOT_FOUND)
        if format == "json":
            return _artifact_response(body=json_bytes(chapter_plan.model_dump(mode="json")), filename=f"mark_ch_{chapter_index:03d}.json", media_type="application/json")
        if format == "markdown":
            return _artifact_response(body=render_mark_markdown(plan, chapter_index=chapter_index).encode("utf-8"), filename=f"mark_ch_{chapter_index:03d}.md", media_type="text/markdown; charset=utf-8")
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for mark chapter exports", status.HTTP_400_BAD_REQUEST)

    if stage == StageName.REWRITE:
        rewrite_dir = store.stage_dir(novel_id, task.id, stage.value)
        aggregate_path = rewrite_dir / "rewrites.json"
        if not aggregate_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Rewrite artifact for task `{task.id}` not found", status.HTTP_404_NOT_FOUND)
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
        chapter_rows = await _load_chapter_rows(db, task.id)
        split_chapters = {
            row.chapter_index: payload
            for row, payload in zip(chapter_rows, [
                {
                    "id": row.id,
                    "chapter_index": row.chapter_index,
                    "chapter_title": row.title,
                    "title": row.title,
                    "content": row.content,
                    "char_count": row.char_count,
                    "paragraph_count": row.paragraph_count,
                    "start_offset": row.start_offset,
                    "end_offset": row.end_offset,
                }
                for row in chapter_rows
            ], strict=False)
        }
        if chapter_index is None:
            if format == "json":
                return _artifact_response(body=json_bytes(aggregate), filename="rewrites.json", media_type="application/json")
            if format == "diff":
                return _artifact_response(body=render_rewrite_diff(list(aggregate.get("chapters", [])), split_chapters=split_chapters).encode("utf-8"), filename="rewrites.diff", media_type="text/x-diff; charset=utf-8")
            if format == "zip":
                return _artifact_response(body=build_rewrite_zip(list(aggregate.get("chapters", [])), split_chapters=split_chapters), filename=f"rewrite_{task.id}.zip", media_type="application/zip")
            raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for rewrite exports", status.HTTP_400_BAD_REQUEST)
        chapter_path = rewrite_dir / f"ch_{chapter_index:03d}_rewrites.json"
        if not chapter_path.exists():
            raise AppError(ErrorCode.NOT_FOUND, f"Rewrite chapter `{chapter_index}` not found", status.HTTP_404_NOT_FOUND)
        chapter_payload = json.loads(chapter_path.read_text(encoding="utf-8"))
        if format == "json":
            return _artifact_response(body=json_bytes(chapter_payload), filename=f"rewrite_ch_{chapter_index:03d}.json", media_type="application/json")
        if format == "diff":
            return _artifact_response(body=render_rewrite_diff([chapter_payload], split_chapters=split_chapters, chapter_index=chapter_index).encode("utf-8"), filename=f"rewrite_ch_{chapter_index:03d}.diff", media_type="text/x-diff; charset=utf-8")
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Format `{format}` is not supported for rewrite chapter exports", status.HTTP_400_BAD_REQUEST)

    raise AppError(ErrorCode.VALIDATION_ERROR, f"Unsupported stage `{stage.value}` for artifact export", status.HTTP_400_BAD_REQUEST)
