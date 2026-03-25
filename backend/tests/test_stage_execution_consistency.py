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

import backend.app.api.routes.stages as stages_routes
from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, Novel, Provider, StageRun, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.db.models import StageRunStatus
from backend.app.llm.interface import CompletionResponse, UsageInfo
from backend.app.llm.prompting import StagePromptBundle
from backend.app.llm.validation import AnalyzeValidationResult
from backend.app.models.core import (
    ChapterAnalysis,
    ProviderType,
    RewritePotential,
    SceneSegment,
    StageName,
)
from backend.app.services.analyze_pipeline import AnalyzeChapterRequest, AnalyzeChapterResult
from backend.app.services.config_store import ConfigSnapshot, RewriteRule, SceneRule, save_snapshot
from backend.app.services.worker_pool import WorkerPool


async def _prepare_session(db_path: Path) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _build_app(sessionmaker: async_sessionmaker, data_dir: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = ArtifactStore(data_dir)
        store.ensure_base_dirs()
        worker_pool = WorkerPool(initial_workers=1)
        await worker_pool.start()
        app.state.artifact_store = store
        app.state.worker_pool = worker_pool
        try:
            yield
        finally:
            await worker_pool.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(stages_router)

    @app.exception_handler(AppError)
    async def _handle_app_error(_, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


async def _seed_task_fixture(sessionmaker: async_sessionmaker, *, novel_id: str, task_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Consistency Novel",
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
            Chapter(
                id=f"{task_id}:chapter-1",
                task_id=task_id,
                chapter_index=1,
                title="第一章",
                content="第一段。\n\n第二段。",
                start_offset=0,
                end_offset=9,
                char_count=9,
                paragraph_count=2,
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


def _completion_from_analysis(analysis: ChapterAnalysis) -> CompletionResponse:
    return CompletionResponse(
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        model_name="gpt-4o-mini",
        text=analysis.model_dump_json(),
        latency_ms=12,
        usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        raw_response={"choices": [{"message": {"content": analysis.summary}}]},
    )


def _validation_from_analysis(analysis: ChapterAnalysis) -> AnalyzeValidationResult:
    return AnalyzeValidationResult(
        passed=True,
        parsed=analysis,
        details={"summary_chars": len(analysis.summary)},
    )


@pytest.fixture(autouse=True)
def _patch_analyze(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _analysis()
        return AnalyzeChapterResult(
            request=request,
            analysis=analysis,
            validation=_validation_from_analysis(analysis),
            completion=_completion_from_analysis(analysis),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    monkeypatch.setattr(stages_routes, "decrypt_api_key", lambda _: "sk-test")
    monkeypatch.setattr(stages_routes, "analyze_chapter", _fake_analyze_chapter)


def test_stage_run_idempotency_reuses_completed_run(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-consistency-a"
            task_id = "task-consistency-a"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "data")

            with TestClient(app) as client:
                first = client.post(f"/novels/{novel_id}/stages/analyze/run", params={"run_idempotency_key": "same-key"})
                assert first.status_code == 200
                assert first.json()["run"]["run_seq"] == 1

                second = client.post(f"/novels/{novel_id}/stages/analyze/run", params={"run_idempotency_key": "same-key"})
                assert second.status_code == 200
                assert second.json()["run"]["run_seq"] == 1
                assert second.json()["run"]["status"] == "completed"

                latest = client.get(f"/novels/{novel_id}/stages/analyze/run")
                assert latest.status_code == 200
                assert latest.json()["run"]["config_snapshot"]["provider_id"] == "provider-1"
                assert latest.json()["run"]["config_snapshot"]["model_name"] == "gpt-4o-mini"

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(
                            StageRun.task_id == task_id,
                            StageRun.stage == StageName.ANALYZE.value,
                            StageRun.run_idempotency_key == "same-key",
                        )
                    )
                ).scalars().all()
                assert len(rows) == 1
                assert rows[0].status == StageRunStatus.COMPLETED.value
                assert rows[0].config_snapshot_json is not None
                assert "gpt-4o-mini" in rows[0].config_snapshot_json
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_running_stage_is_returned_without_reexecution(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-consistency-b"
            task_id = "task-consistency-b"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "data")

            async with sessionmaker() as session:
                provider = await session.get(Provider, "provider-1")
                config_snapshot = await save_snapshot(
                    session,
                    ConfigSnapshot(
                        global_prompt="全局提示词",
                        scene_rules=[SceneRule(scene_type="battle", keywords=["战斗"])],
                        rewrite_rules=[RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)],
                    ),
                )
                stage_snapshot = stages_routes._build_stage_config_snapshot(  # type: ignore[attr-defined]
                    config_snapshot,
                    provider,
                )
                session.add(
                    StageRun(
                        id=f"{task_id}-rewrite-2",
                        task_id=task_id,
                        stage=StageName.REWRITE.value,
                        run_seq=2,
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
                response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert response.status_code == 200
                payload = response.json()
                assert payload["run"]["status"] == "running"
                assert payload["run"]["run_seq"] == 2
                assert payload["run"]["config_snapshot"]["provider_name"] == "Active Provider"

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == StageName.REWRITE.value)
                    )
                ).scalars().all()
                assert len(rows) == 1
                assert any(row.status == StageRunStatus.RUNNING.value for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(_run())
