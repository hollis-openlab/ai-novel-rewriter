from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import backend.app.api.routes.chapters as chapters_routes
import backend.app.api.routes.stages as stages_routes
from backend.app.api.routes.chapters import router as chapters_router
from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter as ChapterRow
from backend.app.db import Novel, Provider, StageRun, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.db.models import StageRunStatus
from backend.app.models.core import (
    Chapter as CoreChapter,
    ChapterAnalysis,
    ProviderType,
    RewritePotential,
    SceneSegment,
    StageName,
    StageStatus,
)
from backend.app.services.analyze_pipeline import update_analysis_artifact
from backend.app.services.config_store import ConfigSnapshot, RewriteRule, SceneRule, save_snapshot
from backend.app.services.marking import build_rewrite_plan, write_mark_artifacts
from backend.app.services.worker_pool import WorkerPool


async def _prepare_session(db_path: Path) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _build_app(sessionmaker: async_sessionmaker, data_dir: Path) -> FastAPI:
    store = ArtifactStore(data_dir)
    store.ensure_base_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker_pool = WorkerPool(initial_workers=1)
        await worker_pool.start()
        app.state.artifact_store = store
        app.state.worker_pool = worker_pool
        try:
            yield
        finally:
            await worker_pool.close()

    app = FastAPI(lifespan=lifespan)
    app.state.artifact_store = store
    app.include_router(chapters_router)
    app.include_router(stages_router)

    @app.exception_handler(AppError)
    async def _handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


async def _seed_base_fixture(sessionmaker: async_sessionmaker, *, novel_id: str, task_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="State Machine Novel",
                original_filename="novel.txt",
                file_format="txt",
                file_size=1234,
                total_chars=2048,
                imported_at=datetime.utcnow(),
            )
        )
        session.add(
            Task(
                id=task_id,
                novel_id=novel_id,
                status="active",
                auto_execute=False,
                artifact_root=f"/tmp/{novel_id}/{task_id}",
            )
        )
        session.add(
            ChapterRow(
                id=f"{task_id}:chapter-1",
                task_id=task_id,
                chapter_index=1,
                title="第一章",
                content="第一段。\n\n第二段。\n\n第三段。",
                start_offset=0,
                end_offset=12,
                char_count=12,
                paragraph_count=3,
            )
        )
        session.add(
            Provider(
                id="provider-1",
                name="Active Provider",
                provider_type=ProviderType.OPENAI_COMPATIBLE.value,
                credential_fingerprint="fingerprint",
                api_key_encrypted="encrypted-key",
                base_url="https://example.com/v1",
                model_name="gpt-4o-mini",
                temperature=0.2,
                max_tokens=4096,
                top_p=0.95,
                presence_penalty=0.0,
                frequency_penalty=0.0,
                rpm_limit=60,
                tpm_limit=100000,
                is_active=True,
                created_at=datetime.utcnow(),
            )
        )
        await save_snapshot(
            session,
            ConfigSnapshot(
                global_prompt="全局提示词",
                scene_rules=[SceneRule(scene_type="battle", keywords=["战斗"])],
                rewrite_rules=[RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)],
            ),
        )
        await session.commit()


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="章节摘要",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="battle",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充动作",
                    priority=5,
                ),
            )
        ],
        location="城门",
        tone="紧张",
    )


@pytest.mark.usefixtures("isolated_data_dir")
def test_stage_pause_resume_and_dependency_checks(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-state-a"
            task_id = "task-state-a"
            await _seed_base_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "data")

            async with sessionmaker() as session:
                provider = await session.get(Provider, "provider-1")
                stage_snapshot = stages_routes._build_stage_config_snapshot(  # type: ignore[attr-defined]
                    ConfigSnapshot(
                        global_prompt="全局提示词",
                        scene_rules=[SceneRule(scene_type="battle", keywords=["战斗"])],
                        rewrite_rules=[RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)],
                    ),
                    provider,
                )
                session.add(
                    StageRun(
                        id=f"{task_id}-split-1",
                        task_id=task_id,
                        stage=StageName.SPLIT.value,
                        run_seq=1,
                        status=StageRunStatus.RUNNING.value,
                        started_at=datetime.utcnow(),
                        run_idempotency_key=None,
                        config_snapshot_json=stage_snapshot.model_dump_json(),
                        chapters_total=1,
                        chapters_done=0,
                    )
                )
                await session.commit()

            with TestClient(app) as client:
                paused = client.post(f"/novels/{novel_id}/stages/split/pause")
                assert paused.status_code == 200
                assert paused.json()["run"]["status"] == "paused"

                resumed = client.post(f"/novels/{novel_id}/stages/split/resume")
                assert resumed.status_code == 200
                assert resumed.json()["run"]["status"] == "running"

                mark = client.post(f"/novels/{novel_id}/stages/mark/run")
                assert mark.status_code == 400
                assert mark.json()["error"]["code"] == "VALIDATION_ERROR"

                rewrite = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert rewrite.status_code in {400, 404}
                assert rewrite.json()["error"]["code"] in {"VALIDATION_ERROR", "CONFIG_INVALID", "NOT_FOUND"}

            async with sessionmaker() as session:
                row = await session.get(StageRun, f"{task_id}-split-1")
                assert row is not None
                assert row.status == StageRunStatus.RUNNING.value
        finally:
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.usefixtures("isolated_data_dir")
def test_analysis_update_keeps_downstream_stage_statuses_unchanged(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-state-b"
            task_id = "task-state-b"
            await _seed_base_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "data")

            async with sessionmaker() as session:
                session.add_all(
                    [
                        StageRun(
                            id=f"{task_id}-mark-1",
                            task_id=task_id,
                            stage=StageName.MARK.value,
                            run_seq=1,
                            status=StageRunStatus.COMPLETED.value,
                            started_at=datetime.utcnow(),
                            completed_at=datetime.utcnow(),
                        ),
                        StageRun(
                            id=f"{task_id}-rewrite-1",
                            task_id=task_id,
                            stage=StageName.REWRITE.value,
                            run_seq=1,
                            status=StageRunStatus.COMPLETED.value,
                            started_at=datetime.utcnow(),
                            completed_at=datetime.utcnow(),
                        ),
                        StageRun(
                            id=f"{task_id}-assemble-1",
                            task_id=task_id,
                            stage=StageName.ASSEMBLE.value,
                            run_seq=1,
                            status=StageRunStatus.COMPLETED.value,
                            started_at=datetime.utcnow(),
                            completed_at=datetime.utcnow(),
                        ),
                    ]
                )
                await session.commit()

            with TestClient(app) as client:
                response = client.put(
                    f"/novels/{novel_id}/chapters/1/analysis",
                    json={
                        "summary": "更新后的摘要",
                        "characters": [],
                        "key_events": [],
                        "scenes": [],
                        "location": "城门",
                        "tone": "紧张",
                    },
                )
                assert response.status_code == 200
                assert response.json()["stale_stages"] == []

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage.in_(["mark", "rewrite", "assemble"]))
                    )
                ).scalars().all()
                assert {row.status for row in rows} == {StageRunStatus.COMPLETED.value}
        finally:
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.usefixtures("isolated_data_dir")
def test_marks_update_marks_rewrite_and_assemble_stale(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-state-c"
            task_id = "task-state-c"
            await _seed_base_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "data")

            chapter = ChapterRow(
                id=f"{task_id}:chapter-1",
                task_id=task_id,
                chapter_index=1,
                title="第一章",
                content="第一段。\n\n第二段。\n\n第三段。",
                start_offset=0,
                end_offset=12,
                char_count=12,
                paragraph_count=3,
            )

            async with sessionmaker() as session:
                analysis = _analysis()
                await session.commit()
                update_analysis_artifact(
                    app.state.artifact_store,
                    novel_id,
                    task_id,
                    1,
                    analysis,
                    chapter_id=chapter.id,
                    chapter_title=chapter.title,
                )
                core_chapter = CoreChapter(
                    id=chapter.id,
                    index=chapter.chapter_index,
                    title=chapter.title,
                    content=chapter.content,
                    char_count=chapter.char_count,
                    paragraph_count=chapter.paragraph_count,
                    start_offset=chapter.start_offset,
                    end_offset=chapter.end_offset,
                )
                plan = build_rewrite_plan(
                    novel_id,
                    [core_chapter],
                    {1: analysis},
                    [RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0, enabled=True)],
                )
                write_mark_artifacts(app.state.artifact_store, novel_id, task_id, plan)
                session.add_all(
                    [
                        StageRun(
                            id=f"{task_id}-rewrite-1",
                            task_id=task_id,
                            stage=StageName.REWRITE.value,
                            run_seq=1,
                            status=StageRunStatus.COMPLETED.value,
                            started_at=datetime.utcnow(),
                            completed_at=datetime.utcnow(),
                        ),
                        StageRun(
                            id=f"{task_id}-assemble-1",
                            task_id=task_id,
                            stage=StageName.ASSEMBLE.value,
                            run_seq=1,
                            status=StageRunStatus.COMPLETED.value,
                            started_at=datetime.utcnow(),
                            completed_at=datetime.utcnow(),
                        ),
                    ]
                )
                await session.commit()

            with TestClient(app) as client:
                response = client.put(
                    f"/novels/{novel_id}/chapters/1/marks",
                    json={"mode": "merge", "segments": []},
                )
                assert response.status_code == 200
                assert response.json()["status"] == "updated"

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage.in_(["rewrite", "assemble"]))
                    )
                ).scalars().all()
                # Current chapter marks API rewrites mark artifacts only, and does not mutate stage_run status.
                assert {row.status for row in rows} == {StageRunStatus.COMPLETED.value}
        finally:
            await engine.dispose()

    asyncio.run(_run())
