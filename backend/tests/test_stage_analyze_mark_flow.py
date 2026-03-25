from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import backend.app.api.routes.stages as stages_routes
from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, Config, Novel, Provider, StageRun, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.llm.interface import CompletionResponse, UsageInfo
from backend.app.llm.prompting import StagePromptBundle
from backend.app.llm.validation import AnalyzeValidationResult
from backend.app.models.core import (
    ChapterAnalysis,
    ProviderType,
    RewriteAnchor,
    RewriteChapterPlan,
    RewritePlan,
    RewriteSegment,
    RewriteStrategy,
    SceneSegment,
    RewritePotential,
)
from backend.app.services.analyze_pipeline import AnalyzeChapterRequest
from backend.app.services.analyze_pipeline import AnalyzeChapterResult
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
        worker_pool = WorkerPool(initial_workers=2)
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
    async def _app_error_handler(_, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


async def _seed_task_fixture(
    sessionmaker: async_sessionmaker,
    *,
    novel_id: str,
    task_id: str,
    provider_active: bool = True,
) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Test Novel",
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
        session.add_all(
            [
                Chapter(
                    id=f"{task_id}-chapter-1",
                    task_id=task_id,
                    chapter_index=1,
                    title="第一章",
                    content="第一段\n\n第二段",
                    start_offset=0,
                    end_offset=9,
                    char_count=9,
                    paragraph_count=2,
                ),
                Chapter(
                    id=f"{task_id}-chapter-2",
                    task_id=task_id,
                    chapter_index=2,
                    title="第二章",
                    content="只有一段",
                    start_offset=10,
                    end_offset=14,
                    char_count=4,
                    paragraph_count=1,
                ),
            ]
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
                is_active=provider_active,
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


def _fake_analyze_result(request: AnalyzeChapterRequest) -> ChapterAnalysis:
    if request.chapter_index == 1:
        scenes = [
            SceneSegment(
                scene_type="battle",
                paragraph_range=(1, 2),
                rewrite_potential=RewritePotential(
                    expandable=False,
                    rewritable=True,
                    suggestion="可改写",
                    priority=3,
                ),
            )
        ]
        return ChapterAnalysis(
            summary="第一章摘要",
            characters=[],
            key_events=[],
            scenes=scenes,
            location="城门",
            tone="紧张",
        )

    scenes = [
        SceneSegment(
            scene_type="dialogue",
            paragraph_range=(1, 1),
            rewrite_potential=RewritePotential(
                expandable=False,
                rewritable=False,
                suggestion="无需改写",
                priority=1,
            ),
        )
    ]
    return ChapterAnalysis(
        summary="第二章摘要",
        characters=[],
        key_events=[],
        scenes=scenes,
        location="客栈",
        tone="平静",
    )


@pytest.fixture(autouse=True)
def _patch_stage_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _fake_analyze_result(request)
        return AnalyzeChapterResult(
            request=request,
            analysis=analysis,
            validation=AnalyzeValidationResult(
                passed=True,
                parsed=analysis,
                details={"summary_chars": len(analysis.summary)},
            ),
            completion=CompletionResponse(
                provider_type=ProviderType.OPENAI_COMPATIBLE,
                model_name=request.model_name,
                text=json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
                latency_ms=12,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            ),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    monkeypatch.setattr(stages_routes, "decrypt_api_key", lambda value: "sk-test")
    monkeypatch.setattr(stages_routes, "analyze_chapter", _fake_analyze_chapter)


def test_analyze_writes_artifacts_and_updates_stage_run(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-a"
            task_id = "task-a"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id, provider_active=True)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                response = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert response.status_code == 200
                payload = response.json()
                assert payload["run"]["status"] == "completed"
                assert payload["run"]["chapters_total"] == 2
                assert payload["run"]["chapters_done"] == 2

            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "analyze")
                    )
                ).scalars().one()
                assert row.status == "completed"
                assert row.chapters_total == 2
                assert row.chapters_done == 2

            store = ArtifactStore(tmp_path / "artifacts")
            stage_dir = store.stage_dir(novel_id, task_id, "analyze")
            ch1 = json.loads((stage_dir / "ch_001_analysis.json").read_text(encoding="utf-8"))
            ch2 = json.loads((stage_dir / "ch_002_analysis.json").read_text(encoding="utf-8"))
            aggregate = json.loads((stage_dir / "analysis.json").read_text(encoding="utf-8"))

            assert ch1["chapter_index"] == 1
            assert ch2["chapter_index"] == 2
            assert aggregate["chapter_count"] == 2
            assert [item["chapter_index"] for item in aggregate["chapters"]] == [1, 2]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_mark_writes_artifacts_and_aligns_analysis_by_chapter(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-b"
            task_id = "task-b"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id, provider_active=True)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_response = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_response.status_code == 200

                mark_response = client.post(f"/novels/{novel_id}/stages/mark/run")
                assert mark_response.status_code == 200
                payload = mark_response.json()
                assert payload["run"]["status"] == "completed"
                assert payload["run"]["chapters_total"] == 2
                assert payload["run"]["chapters_done"] == 2

            store = ArtifactStore(tmp_path / "artifacts")
            mark_dir = store.stage_dir(novel_id, task_id, "mark")
            plan = json.loads((mark_dir / "mark_plan.json").read_text(encoding="utf-8"))
            ch1_mark = json.loads((mark_dir / "ch_1_mark.json").read_text(encoding="utf-8"))
            ch2_mark = json.loads((mark_dir / "ch_2_mark.json").read_text(encoding="utf-8"))

            assert plan["novel_id"] == novel_id
            assert len(plan["chapters"]) == 2
            assert len(plan["chapters"][0]["segments"]) == 1
            assert len(plan["chapters"][1]["segments"]) == 0
            assert ch1_mark["rewrite_plan"]["chapter_index"] == 1
            assert ch2_mark["rewrite_plan"]["chapter_index"] == 2

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun)
                        .where(StageRun.task_id == task_id, StageRun.stage == "mark")
                        .order_by(StageRun.run_seq.asc())
                    )
                ).scalars().all()
                assert len(rows) >= 1
                row = rows[-1]
                assert row.status == "completed"
                assert row.chapters_total == 2
                assert row.chapters_done == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_analyze_without_provider_returns_validation_error(tmp_path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-c"
            task_id = "task-c"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id, provider_active=False)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                response = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert response.status_code == 400
                payload = response.json()
                assert payload["error"]["code"] == "VALIDATION_ERROR"

            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "analyze")
                    )
                ).scalars().one()
                assert row.status == "failed"
                assert row.error_message == "No active provider configured"
                assert row.chapters_total == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())
