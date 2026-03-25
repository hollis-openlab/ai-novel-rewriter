from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.chapters import (
    CHAPTER_REWRITE_FILE_TEMPLATE,
    REWRITE_AGGREGATE_FILENAME,
    REWRITE_STAGE_NAME,
    RewriteReviewRequest,
    RewriteReviewResponse,
    router as chapters_router,
)
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import get_db_session
from backend.app.db.base import Base
from backend.app.db.models import Chapter as ChapterRow
from backend.app.db.models import Novel, NovelFileFormat, Task, TaskStatus
from backend.app.models.core import (
    RewriteAuditEntry,
    RewriteAnchor,
    RewriteChapterPlan,
    RewritePlan,
    RewriteResult,
    RewriteResultStatus,
    RewriteReviewAction,
    RewriteSegment,
    RewriteStrategy,
)
from backend.app.services.marking import write_mark_artifacts


async def _prepare_session(db_path: Path) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _build_app(sessionmaker: async_sessionmaker, artifact_store: ArtifactStore) -> FastAPI:
    app = FastAPI()
    app.include_router(chapters_router)
    app.state.artifact_store = artifact_store

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session

    @app.exception_handler(AppError)
    async def _handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, **exc.details),
        )

    return app


def _chapter_content() -> str:
    return "\n\n".join(
        [
            "第一段战斗动作很快，气氛瞬间紧绷。",
            "第二段人物对话推进线索，信息逐渐明确。",
            "第三段环境与情绪收束，为后续冲突留出空间。",
        ]
    )


async def _seed_db(sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        content = _chapter_content()
        session.add(
            Novel(
                id="novel-1",
                title="测试小说",
                original_filename="demo.txt",
                file_format=NovelFileFormat.TXT.value,
                file_size=128,
                total_chars=128,
                config_override_json=None,
            )
        )
        session.add(
            Task(
                id="task-1",
                novel_id="novel-1",
                status=TaskStatus.ACTIVE.value,
                source_task_id=None,
                auto_execute=False,
                artifact_root="unused",
            )
        )
        session.add(
            ChapterRow(
                id="chapter-1",
                task_id="task-1",
                chapter_index=1,
                title="第一章",
                content=content,
                start_offset=0,
                end_offset=len(content),
                char_count=len(content),
                paragraph_count=3,
            )
        )
        await session.commit()


def _rewrite_result(
    *,
    status: RewriteResultStatus,
    rewritten_text: str,
    segment_id: str | None = None,
) -> RewriteResult:
    content = _chapter_content()
    return RewriteResult(
        segment_id=segment_id or str(uuid4()),
        chapter_index=1,
        paragraph_range=(1, 1),
        anchor_verified=True,
        strategy=RewriteStrategy.EXPAND,
        original_text=content.split("\n\n")[0],
        rewritten_text=rewritten_text,
        original_chars=len(content.split("\n\n")[0]),
        rewritten_chars=len(rewritten_text),
        status=status,
        attempts=1,
        provider_used="openai_compatible",
    )


def _seed_rewrite_artifact(store: ArtifactStore, result: RewriteResult) -> Path:
    path = store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=1)
    store.ensure_json(
        path,
        {
            "novel_id": "novel-1",
            "task_id": "task-1",
            "chapter_index": 1,
            "updated_at": "2026-03-20T00:00:00Z",
            "segments": [result.model_dump(mode="json")],
            "audit_trail": [],
        },
    )
    return path


def _seed_empty_rewrite_artifact(store: ArtifactStore) -> Path:
    path = store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=1)
    store.ensure_json(
        path,
        {
            "novel_id": "novel-1",
            "task_id": "task-1",
            "chapter_index": 1,
            "updated_at": "2026-03-20T00:00:00Z",
            "segments": [],
            "audit_trail": [],
        },
    )
    return path


def _chapter_rewrite_path(store: ArtifactStore) -> Path:
    return store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=1)


def _aggregate_path(store: ArtifactStore) -> Path:
    return store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / REWRITE_AGGREGATE_FILENAME


def _rewrite_segment_with_targets() -> RewriteSegment:
    return RewriteSegment(
        segment_id=str(uuid4()),
        paragraph_range=(1, 1),
        anchor=RewriteAnchor(
            paragraph_start_hash="start",
            paragraph_end_hash="end",
            range_text_hash="range",
            context_window_hash="context",
            paragraph_count_snapshot=3,
        ),
        scene_type="battle",
        original_chars=10,
        strategy=RewriteStrategy.REWRITE,
        target_ratio=1.6,
        target_chars=16,
        target_chars_min=14,
        target_chars_max=18,
        suggestion="补充动作与气势",
        source="auto",
        confirmed=True,
    )


def test_rewrite_review_accept_reject_regenerate_lifecycle(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            initial = _rewrite_result(
                status=RewriteResultStatus.COMPLETED,
                rewritten_text="改写后的第一版。",
            )
            segment_id = initial.segment_id
            _seed_rewrite_artifact(store, initial)
            app = _build_app(sessionmaker, store)

            with TestClient(app) as client:
                first = client.get("/novels/novel-1/chapters/1/rewrites")
                assert first.status_code == 200
                first_json = first.json()
                assert len(first_json) == 1
                assert first_json[0]["status"] == "completed"
                assert first_json[0]["rewritten_text"] == "改写后的第一版。"

                accepted = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment_id}",
                    json={"action": "accept", "note": "确认改写可用"},
                )
                assert accepted.status_code == 200
                accepted_json = accepted.json()
                assert accepted_json["status"] == "accepted"
                assert accepted_json["chapter_idx"] == 1
                assert accepted_json["segment_id"] == segment_id

                rejected = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment_id}",
                    json={"action": "reject", "note": "风格不匹配"},
                )
                assert rejected.status_code == 200
                rejected_json = rejected.json()
                assert rejected_json["status"] == "rejected"

                regenerated = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment_id}",
                    json={"action": "regenerate"},
                )
                assert regenerated.status_code == 200
                regenerated_json = regenerated.json()
                assert regenerated_json["status"] == "pending"

                latest = client.get("/novels/novel-1/chapters/1/rewrites")
                assert latest.status_code == 200
                latest_json = latest.json()
                assert latest_json[0]["status"] == "pending"
                assert latest_json[0]["audit_trail"][-1]["action"] == "regenerate"
                assert latest_json[0]["rollback_snapshot"]["status"] == "rejected"

            artifact = json.loads(_chapter_rewrite_path(store).read_text(encoding="utf-8"))
            assert artifact["segments"][0]["status"] == "pending"
            assert len(artifact["segments"][0]["audit_trail"]) == 3
            assert _aggregate_path(store).exists()
            aggregate = json.loads(_aggregate_path(store).read_text(encoding="utf-8"))
            assert aggregate["chapter_count"] == 1
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_review_edit_transitions_to_accepted_edited_and_keeps_rollback(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            initial = _rewrite_result(
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="已经接受的改写版本。",
            )
            segment_id = initial.segment_id
            _seed_rewrite_artifact(store, initial)
            app = _build_app(sessionmaker, store)

            with TestClient(app) as client:
                edited_text = "人工微调后的改写版本。"
                edited = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment_id}",
                    json={
                        "action": "edit",
                        "rewritten_text": edited_text,
                        "note": "补充语气与节奏",
                    },
                )
                assert edited.status_code == 200
                edited_json = edited.json()
                assert edited_json["status"] == "accepted_edited"
                assert edited_json["audit_entries"] == 1

                latest = client.get("/novels/novel-1/chapters/1/rewrites")
                assert latest.status_code == 200
                latest_json = latest.json()
                assert latest_json[0]["status"] == "accepted_edited"
                assert latest_json[0]["rewritten_text"] == edited_text
                assert latest_json[0]["manual_edited_text"] == edited_text
                assert latest_json[0]["rollback_snapshot"]["rewritten_text"] == "已经接受的改写版本。"
                assert latest_json[0]["audit_trail"][0]["action"] == "edit"

            artifact = json.loads(_chapter_rewrite_path(store).read_text(encoding="utf-8"))
            assert artifact["segments"][0]["status"] == "accepted_edited"
            assert artifact["segments"][0]["manual_edited_text"] == edited_text
            assert artifact["audit_trail"][0]["action"] == "edit"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_rewrites_backfills_target_fields_from_mark_plan_for_legacy_artifact(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            segment = _rewrite_segment_with_targets()
            plan = RewriteChapterPlan(chapter_index=1, segments=[segment])
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.utcnow(),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[plan],
                ),
            )

            legacy_artifact = {
                "novel_id": "novel-1",
                "task_id": "task-1",
                "chapter_index": 1,
                "updated_at": "2026-03-20T00:00:00Z",
                "segments": [
                    {
                        "segment_id": segment.segment_id,
                        "chapter_index": 1,
                        "paragraph_range": [1, 1],
                        "anchor_verified": True,
                        "strategy": segment.strategy.value,
                        "original_text": "第一段战斗开场。",
                        "rewritten_text": "第一段战斗开场，细节更丰富。",
                        "original_chars": 8,
                        "rewritten_chars": 13,
                        "actual_chars": 13,
                        "status": "completed",
                        "attempts": 1,
                        "provider_used": "openai_compatible",
                        "error_code": None,
                        "error_detail": None,
                        "manual_edited_text": None,
                        "rollback_snapshot": None,
                        "audit_trail": [],
                    }
                ],
                "audit_trail": [],
            }
            store.ensure_json(_chapter_rewrite_path(store), legacy_artifact)

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters/1/rewrites")
                assert response.status_code == 200
                payload = response.json()
                assert len(payload) == 1
                item = payload[0]
                assert item["segment_id"] == segment.segment_id
                assert item["target_ratio"] == segment.target_ratio
                assert item["target_chars"] == segment.target_chars
                assert item["target_chars_min"] == segment.target_chars_min
                assert item["target_chars_max"] == segment.target_chars_max
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_rewrites_backfills_pending_segments_when_rewrite_artifact_is_empty(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            segment = _rewrite_segment_with_targets()
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.utcnow(),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[RewriteChapterPlan(chapter_index=1, segments=[segment])],
                ),
            )
            _seed_empty_rewrite_artifact(store)

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters/1/rewrites")
                assert response.status_code == 200
                payload = response.json()
                assert len(payload) == 1
                item = payload[0]
                assert item["segment_id"] == segment.segment_id
                assert item["status"] == "pending"
                assert item["scene_type"] == segment.scene_type
                assert item["target_ratio"] == segment.target_ratio
                assert item["target_chars"] == segment.target_chars
                assert item["target_chars_min"] == segment.target_chars_min
                assert item["target_chars_max"] == segment.target_chars_max
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_rewrites_exposes_full_diagnostics_fields(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            segment = _rewrite_segment_with_targets()
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.utcnow(),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[RewriteChapterPlan(chapter_index=1, segments=[segment])],
                ),
            )

            diagnostics_artifact = {
                "novel_id": "novel-1",
                "task_id": "task-1",
                "chapter_index": 1,
                "updated_at": "2026-03-20T00:00:00Z",
                "segments": [
                    {
                        "segment_id": segment.segment_id,
                        "chapter_index": 1,
                        "paragraph_range": [1, 1],
                        "anchor_verified": True,
                        "scene_type": segment.scene_type,
                        "suggestion": segment.suggestion,
                        "target_ratio": segment.target_ratio,
                        "target_chars": segment.target_chars,
                        "target_chars_min": segment.target_chars_min,
                        "target_chars_max": segment.target_chars_max,
                        "strategy": segment.strategy.value,
                        "original_text": "第一段战斗开场。",
                        "rewritten_text": "",
                        "original_chars": 8,
                        "rewritten_chars": 9,
                        "actual_chars": 9,
                        "status": "failed",
                        "attempts": 1,
                        "provider_used": "openai_compatible",
                        "error_code": "REWRITE_LENGTH_OUT_OF_RANGE",
                        "error_detail": "{\"target_chars_min\":14,\"target_chars_max\":18,\"actual_chars\":9}",
                        "provider_raw_response": {
                            "error": {
                                "message": "Bad Request",
                                "type": "invalid_request_error",
                                "param": None,
                                "code": "invalid_request_error",
                            },
                            "api_key": "sk-test-123",
                            "authorization": "Bearer secret-token",
                        },
                        "validation_details": {
                            "target_chars": 16,
                            "target_chars_min": 14,
                            "target_chars_max": 18,
                            "actual_chars": 9,
                            "rewritten_chars": 9,
                        },
                        "manual_edited_text": None,
                        "rollback_snapshot": None,
                        "audit_trail": [],
                    }
                ],
                "audit_trail": [],
            }
            store.ensure_json(_chapter_rewrite_path(store), diagnostics_artifact)

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters/1/rewrites")
                assert response.status_code == 200
                item = response.json()[0]
                assert item["status"] == "failed"
                assert item["error_code"] == "REWRITE_LENGTH_OUT_OF_RANGE"
                assert item["actual_chars"] == 9
                assert item["target_chars"] == 16
                assert item["validation_details"]["actual_chars"] == 9
                assert item["validation_details"]["target_chars_min"] == 14
                assert item["provider_raw_response"]["error"]["message"] == "Bad Request"
                assert item["provider_raw_response"]["api_key"] == "***"
                assert item["provider_raw_response"]["authorization"] == "***"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_review_missing_artifact_returns_404(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            app = _build_app(sessionmaker, store)

            with TestClient(app) as client:
                response = client.put(
                    "/novels/novel-1/chapters/1/rewrites/segment-1",
                    json={"action": "accept"},
                )
                assert response.status_code == 404
                assert response.json()["error"]["code"] == "NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_review_reject_bootstraps_artifact_from_mark_plan(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-bootstrap.db")
        store = ArtifactStore(tmp_path / "data-bootstrap")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)

            segment = _rewrite_segment_with_targets()
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.utcnow(),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[RewriteChapterPlan(chapter_index=1, segments=[segment])],
                ),
            )

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                rejected = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment.segment_id}",
                    json={"action": "reject", "note": "adopt_original_before_run"},
                )
                assert rejected.status_code == 200
                assert rejected.json()["status"] == "rejected"

                latest = client.get("/novels/novel-1/chapters/1/rewrites")
                assert latest.status_code == 200
                latest_json = latest.json()
                assert len(latest_json) == 1
                assert latest_json[0]["segment_id"] == segment.segment_id
                assert latest_json[0]["status"] == "rejected"

            assert _chapter_rewrite_path(store).exists()
        finally:
            await engine.dispose()

    asyncio.run(_run())
