from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.chapters import router as chapters_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import get_db_session
from backend.app.db.base import Base
from backend.app.db.models import Chapter as ChapterRow
from backend.app.db.models import (
    ChapterState,
    ChapterStateStatus,
    Novel,
    NovelFileFormat,
    StageRun,
    StageRunStatus,
    Task,
    TaskStatus,
)
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    CharacterState,
    KeyEvent,
    RewritePotential,
    RewritePlan,
    RewriteResult,
    RewriteResultStatus,
    RewriteSegment,
    RewriteStrategy,
    SceneSegment,
)
from backend.app.services.analyze_pipeline import (
    chapter_analysis_path,
    load_analysis_aggregate,
    update_analysis_artifact,
)
from backend.app.services.marking import build_anchor, build_rewrite_plan, write_mark_artifacts
from backend.app.services.config_store import RewriteRule


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


def _chapter_model() -> Chapter:
    content = _chapter_content()
    return Chapter(
        id="chapter-1",
        index=1,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=3,
        start_offset=0,
        end_offset=len(content),
    )


def _analysis_model() -> ChapterAnalysis:
    summary = (
        "主角在城门外观察局势，随后与同伴交换情报，"
        "再进入更危险的区域，整个过程充满紧张感与未知压力。"
    ) * 3
    summary = summary[:260]
    return ChapterAnalysis(
        summary=summary,
        characters=[
            CharacterState(
                name="主角",
                emotion="警惕",
                state="观察局势",
                role_in_chapter="主视角",
            )
        ],
        key_events=[
            KeyEvent(
                description="主角观察局势并交换情报",
                event_type="观察",
                importance=4,
                paragraph_range=(1, 2),
            )
        ],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="可补充动作细节",
                    priority=5,
                ),
            )
        ],
        location="城门",
        tone="紧张",
    )


def _manual_segment(chapter: Chapter, paragraph_range: tuple[int, int]) -> RewriteSegment:
    return RewriteSegment(
        segment_id=str(uuid4()),
        paragraph_range=paragraph_range,
        anchor=build_anchor(chapter, paragraph_range),
        scene_type="战斗",
        original_chars=12,
        strategy=RewriteStrategy.EXPAND,
        target_ratio=1.5,
        target_chars=18,
        target_chars_min=15,
        target_chars_max=21,
        suggestion="补充手动改写内容",
        source="manual",
        confirmed=False,
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


def test_analysis_read_write_closed_loop(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            app = _build_app(sessionmaker, store)
            analysis = _analysis_model()

            with TestClient(app) as client:
                initial = client.get("/novels/novel-1/chapters/1/analysis")
                assert initial.status_code == 200
                initial_json = initial.json()
                assert initial_json["summary"] == ""
                assert initial_json["characters"] == []

                stored_path = update_analysis_artifact(
                    store,
                    "novel-1",
                    "task-1",
                    1,
                    analysis,
                    chapter_id="chapter-1",
                    chapter_title="第一章",
                )
                assert Path(stored_path).exists()

                updated = client.put(
                    "/novels/novel-1/chapters/1/analysis",
                    json=analysis.model_dump(mode="json"),
                )
                assert updated.status_code == 200
                updated_json = updated.json()
                assert updated_json["status"] == "updated"
                assert updated_json["chapter_idx"] == 1
                assert updated_json["chapter_title"] == "第一章"

                fetched = client.get("/novels/novel-1/chapters/1/analysis")
                assert fetched.status_code == 200
                fetched_json = fetched.json()
                assert fetched_json["location"] == "城门"
                assert fetched_json["tone"] == "紧张"
                assert fetched_json["summary"] == analysis.summary

                artifact_json = json.loads(
                    chapter_analysis_path(store, "novel-1", "task-1", 1).read_text(encoding="utf-8")
                )
                aggregate = load_analysis_aggregate(store, "novel-1", "task-1")
                assert artifact_json["chapter_id"] == "chapter-1"
                assert aggregate["chapter_count"] == 1
                assert aggregate["chapters"][0]["analysis"]["location"] == "城门"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_marks_merge_replace_and_rewrites_preview(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            app = _build_app(sessionmaker, store)

            chapter = _chapter_model()
            analysis = _analysis_model()
            rewrite_rules = [RewriteRule(scene_type="战斗", strategy="expand", target_ratio=1.5, priority=1, enabled=True)]
            plan = build_rewrite_plan("novel-1", [chapter], {1: analysis}, rewrite_rules)
            write_mark_artifacts(store, "novel-1", "task-1", plan)

            manual_segment = _manual_segment(chapter, (2, 2))

            with TestClient(app) as client:
                preview = client.get("/novels/novel-1/chapters/1/rewrites")
                assert preview.status_code == 200
                preview_json = preview.json()
                assert len(preview_json) == 1
                assert preview_json[0]["segment_id"] != ""

                merged = client.put(
                    "/novels/novel-1/chapters/1/marks",
                    json={
                        "mode": "merge",
                        "segments": [manual_segment.model_dump(mode="json")],
                    },
                )
                assert merged.status_code == 200
                merged_json = merged.json()
                assert merged_json["status"] == "updated"
                assert merged_json["chapter_idx"] == 1
                assert merged_json["total_marked"] == 2

                merged_preview = client.get("/novels/novel-1/chapters/1/rewrites")
                assert merged_preview.status_code == 200
                merged_preview_json = merged_preview.json()
                assert len(merged_preview_json) == 2

                replaced = client.put(
                    "/novels/novel-1/chapters/1/marks",
                    json={
                        "mode": "replace",
                        "segments": [manual_segment.model_dump(mode="json")],
                    },
                )
                assert replaced.status_code == 200
                replaced_json = replaced.json()
                assert replaced_json["total_marked"] == 1

                replaced_preview = client.get("/novels/novel-1/chapters/1/rewrites")
                assert replaced_preview.status_code == 200
                replaced_preview_json = replaced_preview.json()
                assert len(replaced_preview_json) == 1
                assert replaced_preview_json[0]["paragraph_range"] == [2, 2]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_marks_missing_plan_returns_404(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            app = _build_app(sessionmaker, store)
            manual_segment = _manual_segment(_chapter_model(), (2, 2))

            with TestClient(app) as client:
                response = client.put(
                    "/novels/novel-1/chapters/1/marks",
                    json={
                        "mode": "merge",
                        "segments": [manual_segment.model_dump(mode="json")],
                    },
                )
                assert response.status_code == 404
                payload = response.json()
                assert payload["error"]["code"] == "NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_returns_persisted_stage_statuses(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-status.db")
        store = ArtifactStore(tmp_path / "data-status")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            async with sessionmaker() as session:
                analyze_run = StageRun(
                    id="task-1-analyze-1",
                    task_id="task-1",
                    stage="analyze",
                    run_seq=1,
                    status=StageRunStatus.RUNNING.value,
                    chapters_total=1,
                    chapters_done=0,
                )
                rewrite_run = StageRun(
                    id="task-1-rewrite-1",
                    task_id="task-1",
                    stage="rewrite",
                    run_seq=1,
                    status=StageRunStatus.PAUSED.value,
                    chapters_total=1,
                    chapters_done=0,
                )
                session.add_all([analyze_run, rewrite_run])
                session.add_all(
                    [
                        ChapterState(
                            id="task-1-analyze-1:ch1",
                            stage_run_id=analyze_run.id,
                            chapter_index=1,
                            status=ChapterStateStatus.RUNNING.value,
                        ),
                        ChapterState(
                            id="task-1-rewrite-1:ch1",
                            stage_run_id=rewrite_run.id,
                            chapter_index=1,
                            status=ChapterStateStatus.COMPLETED.value,
                        ),
                    ]
                )
                await session.commit()

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                assert payload["total"] == 1
                chapter = payload["data"][0]
                assert chapter["stages"]["analyze"] == "running"
                assert chapter["stages"]["rewrite"] == "completed"
                assert chapter["status"] == "running"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_prefers_only_latest_run_states_per_chapter(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-status-multi.db")
        store = ArtifactStore(tmp_path / "data-status-multi")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            async with sessionmaker() as session:
                extra_content = _chapter_content()
                session.add_all(
                    [
                        ChapterRow(
                            id="chapter-2",
                            task_id="task-1",
                            chapter_index=2,
                            title="第二章",
                            content=extra_content,
                            start_offset=0,
                            end_offset=len(extra_content),
                            char_count=len(extra_content),
                            paragraph_count=3,
                        ),
                        ChapterRow(
                            id="chapter-3",
                            task_id="task-1",
                            chapter_index=3,
                            title="第三章",
                            content=extra_content,
                            start_offset=0,
                            end_offset=len(extra_content),
                            char_count=len(extra_content),
                            paragraph_count=3,
                        ),
                    ]
                )
                rewrite_run_v1 = StageRun(
                    id="task-1-rewrite-1",
                    task_id="task-1",
                    stage="rewrite",
                    run_seq=1,
                    status=StageRunStatus.COMPLETED.value,
                    chapters_total=3,
                    chapters_done=1,
                )
                rewrite_run_v2 = StageRun(
                    id="task-1-rewrite-2",
                    task_id="task-1",
                    stage="rewrite",
                    run_seq=2,
                    status=StageRunStatus.PAUSED.value,
                    chapters_total=3,
                    chapters_done=1,
                )
                session.add_all([rewrite_run_v1, rewrite_run_v2])
                session.add_all(
                    [
                        ChapterState(
                            id="task-1-rewrite-1:ch1",
                            stage_run_id=rewrite_run_v1.id,
                            chapter_index=1,
                            status=ChapterStateStatus.COMPLETED.value,
                        ),
                        ChapterState(
                            id="task-1-rewrite-2:ch3",
                            stage_run_id=rewrite_run_v2.id,
                            chapter_index=3,
                            status=ChapterStateStatus.RUNNING.value,
                        ),
                    ]
                )
                await session.commit()

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                by_index = {item["index"]: item for item in payload["data"]}

                assert by_index[1]["stages"]["rewrite"] == "pending"
                assert by_index[2]["stages"]["rewrite"] == "pending"
                assert by_index[3]["stages"]["rewrite"] == "paused"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_keeps_pending_when_failed_run_has_no_chapter_states(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-status-failed-no-state.db")
        store = ArtifactStore(tmp_path / "data-status-failed-no-state")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            extra_content = _chapter_content()
            async with sessionmaker() as session:
                session.add(
                    ChapterRow(
                        id="chapter-2",
                        task_id="task-1",
                        chapter_index=2,
                        title="第二章",
                        content=extra_content,
                        start_offset=0,
                        end_offset=len(extra_content),
                        char_count=len(extra_content),
                        paragraph_count=3,
                    )
                )
                session.add(
                    StageRun(
                        id="task-1-mark-1",
                        task_id="task-1",
                        stage="mark",
                        run_seq=1,
                        status=StageRunStatus.FAILED.value,
                        chapters_total=2,
                        chapters_done=0,
                        error_message="mark failed before chapter states were initialized",
                    )
                )
                await session.commit()

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                by_index = {item["index"]: item for item in payload["data"]}

                assert by_index[1]["stages"]["mark"] == "pending"
                assert by_index[2]["stages"]["mark"] == "pending"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_marks_rewrite_completed_when_no_marked_segments(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-no-rewrite.db")
        store = ArtifactStore(tmp_path / "data-no-rewrite")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.now(timezone.utc),
                    total_marked=0,
                    estimated_llm_calls=0,
                    estimated_added_chars=0,
                    chapters=[],
                ),
            )

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                chapter_item = payload["data"][0]
                assert chapter_item["stages"]["rewrite"] == "completed"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_uses_rewrite_artifact_for_manual_fallback_chapters(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-rewrite-artifact-status.db")
        store = ArtifactStore(tmp_path / "data-rewrite-artifact-status")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            extra_content = _chapter_content()
            async with sessionmaker() as session:
                session.add_all(
                    [
                        ChapterRow(
                            id="chapter-2",
                            task_id="task-1",
                            chapter_index=2,
                            title="第二章",
                            content=extra_content,
                            start_offset=0,
                            end_offset=len(extra_content),
                            char_count=len(extra_content),
                            paragraph_count=3,
                        ),
                        ChapterRow(
                            id="chapter-3",
                            task_id="task-1",
                            chapter_index=3,
                            title="第三章",
                            content=extra_content,
                            start_offset=0,
                            end_offset=len(extra_content),
                            char_count=len(extra_content),
                            paragraph_count=3,
                        ),
                    ]
                )
                rewrite_run = StageRun(
                    id="task-1-rewrite-1",
                    task_id="task-1",
                    stage="rewrite",
                    run_seq=1,
                    status=StageRunStatus.COMPLETED.value,
                    chapters_total=3,
                    chapters_done=0,
                )
                session.add(rewrite_run)
                session.add(
                    ChapterState(
                        id="task-1-rewrite-1:ch3",
                        stage_run_id=rewrite_run.id,
                        chapter_index=3,
                        status=ChapterStateStatus.RUNNING.value,
                    )
                )
                await session.commit()

            chapter = _chapter_model()
            segment = _manual_segment(chapter, (1, 1))
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.now(timezone.utc),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[
                        {
                            "chapter_index": 2,
                            "segments": [segment.model_dump(mode="json")],
                        }
                    ],
                ),
            )
            rewrite_stage_dir = store.stage_dir("novel-1", "task-1", "rewrite")
            rewrite_stage_dir.mkdir(parents=True, exist_ok=True)
            store.ensure_json(
                rewrite_stage_dir / "ch_002_rewrites.json",
                {
                    "novel_id": "novel-1",
                    "task_id": "task-1",
                    "chapter_index": 2,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "segments": [
                        RewriteResult(
                            segment_id=segment.segment_id,
                            chapter_index=2,
                            paragraph_range=segment.paragraph_range,
                            scene_type=segment.scene_type,
                            suggestion=segment.suggestion,
                            target_ratio=segment.target_ratio,
                            target_chars=segment.target_chars,
                            target_chars_min=segment.target_chars_min,
                            target_chars_max=segment.target_chars_max,
                            strategy=segment.strategy,
                            original_text="原文占位",
                            rewritten_text="",
                            original_chars=segment.original_chars,
                            rewritten_chars=0,
                            actual_chars=0,
                            status=RewriteResultStatus.REJECTED,
                            attempts=0,
                            provider_used=None,
                            error_code=None,
                            error_detail=None,
                            provider_raw_response=None,
                            validation_details=None,
                            manual_edited_text=None,
                            rollback_snapshot=None,
                            audit_trail=[],
                        ).model_dump(mode="json")
                    ],
                    "audit_trail": [],
                },
            )

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                by_index = {item["index"]: item for item in payload["data"]}

                assert by_index[2]["stages"]["rewrite"] == "completed"
                assert by_index[3]["stages"]["rewrite"] == "running"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_chapters_normalizes_legacy_stale_rewrite_to_completed(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "chapters-rewrite-stale-artifact.db")
        store = ArtifactStore(tmp_path / "data-rewrite-stale-artifact")
        store.ensure_novel_dirs("novel-1")
        store.ensure_task_scaffold("novel-1", "task-1")
        store.write_active_task_id("novel-1", "task-1")
        try:
            await _seed_db(sessionmaker)
            chapter = _chapter_model()
            segment = _manual_segment(chapter, (1, 1))
            write_mark_artifacts(
                store,
                "novel-1",
                "task-1",
                RewritePlan(
                    novel_id="novel-1",
                    created_at=datetime.now(timezone.utc),
                    total_marked=1,
                    estimated_llm_calls=1,
                    estimated_added_chars=6,
                    chapters=[
                        {
                            "chapter_index": 1,
                            "segments": [segment.model_dump(mode="json")],
                        }
                    ],
                ),
            )

            rewrite_stage_dir = store.stage_dir("novel-1", "task-1", "rewrite")
            rewrite_stage_dir.mkdir(parents=True, exist_ok=True)
            store.ensure_json(
                rewrite_stage_dir / "ch_001_rewrites.json",
                {
                    "novel_id": "novel-1",
                    "task_id": "task-1",
                    "chapter_index": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "segments": [
                        RewriteResult(
                            segment_id=segment.segment_id,
                            chapter_index=1,
                            paragraph_range=segment.paragraph_range,
                            scene_type=segment.scene_type,
                            suggestion=segment.suggestion,
                            target_ratio=segment.target_ratio,
                            target_chars=segment.target_chars,
                            target_chars_min=segment.target_chars_min,
                            target_chars_max=segment.target_chars_max,
                            strategy=segment.strategy,
                            original_text="原文占位",
                            rewritten_text="改写占位",
                            original_chars=segment.original_chars,
                            rewritten_chars=8,
                            actual_chars=8,
                            status=RewriteResultStatus.COMPLETED,
                            attempts=1,
                            provider_used=None,
                            error_code=None,
                            error_detail=None,
                            provider_raw_response=None,
                            validation_details=None,
                            manual_edited_text=None,
                            rollback_snapshot=None,
                            audit_trail=[],
                        ).model_dump(mode="json")
                    ],
                    "audit_trail": [],
                },
            )

            async with sessionmaker() as session:
                session.add(
                    StageRun(
                        id="task-1-rewrite-1",
                        task_id="task-1",
                        stage="rewrite",
                        run_seq=1,
                        status=StageRunStatus.STALE.value,
                        chapters_total=1,
                        chapters_done=1,
                    )
                )
                await session.commit()

            app = _build_app(sessionmaker, store)
            with TestClient(app) as client:
                response = client.get("/novels/novel-1/chapters")
                assert response.status_code == 200
                payload = response.json()
                chapter_item = payload["data"][0]
                assert chapter_item["stages"]["rewrite"] == "completed"
        finally:
            await engine.dispose()

    asyncio.run(_run())
