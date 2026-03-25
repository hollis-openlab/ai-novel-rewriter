from __future__ import annotations

import asyncio
import json
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
from backend.app.llm.interface import CompletionResponse, UsageInfo
from backend.app.llm.prompting import StagePromptBundle
from backend.app.llm.validation import AnalyzeValidationResult
from backend.app.models.core import (
    ChapterAnalysis,
    ProviderType,
    RewritePotential,
    SceneSegment,
    StageName,
    StageStatus,
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


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="章节摘要",
        characters=[],
        key_events=[],
        scenes=[
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
        ],
        location="城门",
        tone="紧张",
    )


def _completion_from_analysis(analysis: ChapterAnalysis) -> CompletionResponse:
    return CompletionResponse(
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        model_name="gpt-4o-mini",
        text=json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
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
def _patch_stage_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _analysis()
        return AnalyzeChapterResult(
            request=request,
            analysis=analysis,
            validation=_validation_from_analysis(analysis),
            completion=_completion_from_analysis(analysis),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    monkeypatch.setattr(stages_routes, "decrypt_api_key", lambda value: "sk-test")
    monkeypatch.setattr(stages_routes, "analyze_chapter", _fake_analyze_chapter)


def test_stage_run_history_records_snapshot_and_artifacts(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-a"
            task_id = "task-a"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id, provider_active=True)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                first = client.post(f"/novels/{novel_id}/stages/analyze/run", params={"run_idempotency_key": "run-1"})
                second = client.post(f"/novels/{novel_id}/stages/analyze/run", params={"run_idempotency_key": "run-2"})

                assert first.status_code == 200
                assert second.status_code == 200
                assert first.json()["run"]["run_seq"] == 1
                assert second.json()["run"]["run_seq"] == 2

                latest = client.get(f"/novels/{novel_id}/stages/analyze/run")
                assert latest.status_code == 200
                latest_payload = latest.json()
                assert latest_payload["run"]["run_seq"] == 2
                assert latest_payload["run"]["status"] == "completed"
                assert latest_payload["run"]["config_snapshot"]["model_name"] == "gpt-4o-mini"
                assert latest_payload["run"]["config_snapshot"]["generation_params"]["rpm_limit"] == 60

                history = client.get(f"/novels/{novel_id}/stages/analyze/runs")
                assert history.status_code == 200
                history_payload = history.json()
                assert history_payload["total"] == 2
                assert history_payload["data"][0]["run_seq"] == 2
                assert history_payload["data"][0]["is_latest"] is True
                assert history_payload["data"][1]["run_seq"] == 1
                assert history_payload["data"][1]["is_latest"] is False

                detail = client.get(f"/novels/{novel_id}/stages/analyze/runs/1")
                assert detail.status_code == 200
                detail_payload = detail.json()
                assert detail_payload["run"]["run_seq"] == 1
                assert detail_payload["run"]["artifact_path"].endswith("/stages/analyze/runs/1/run.json")

                artifact = client.get(f"/novels/{novel_id}/stages/analyze/artifact", params={"run_seq": 1})
                assert artifact.status_code == 200
                artifact_payload = artifact.json()
                assert artifact_payload["run_seq"] == 1
                assert artifact_payload["artifact"]["run_seq"] == 1
                assert artifact_payload["latest_artifact"]["run_seq"] == 2

            store = ArtifactStore(tmp_path / "artifacts")
            run1 = store.stage_run_manifest_path(novel_id, task_id, "analyze", 1)
            run2 = store.stage_run_manifest_path(novel_id, task_id, "analyze", 2)
            latest_manifest = store.stage_run_latest_manifest_path(novel_id, task_id, "analyze")

            assert run1.exists()
            assert run2.exists()
            assert latest_manifest.exists()

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == StageName.ANALYZE.value)
                    )
                ).scalars().all()
                assert [row.run_seq for row in rows] == [1, 2]
                assert rows[-1].status == StageStatus.COMPLETED.value
                assert rows[-1].config_snapshot_json is not None
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_stage_run_single_flight_reuses_running_run(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-b"
            task_id = "task-b"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id, provider_active=True)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            async with sessionmaker() as session:
                config_snapshot = ConfigSnapshot(
                    global_prompt="全局提示词",
                    scene_rules=[SceneRule(scene_type="battle", keywords=["战斗"])],
                    rewrite_rules=[RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)],
                )
                stage_snapshot = stages_routes._build_stage_config_snapshot(  # type: ignore[attr-defined]
                    config_snapshot,
                    await session.get(Provider, "provider-1"),
                )
                session.add(
                    StageRun(
                        id=f"{task_id}-analyze-1",
                        task_id=task_id,
                        stage=StageName.ANALYZE.value,
                        run_seq=1,
                        status=StageStatus.RUNNING.value,
                        started_at=datetime.utcnow(),
                        run_idempotency_key=None,
                        config_snapshot_json=stage_snapshot.model_dump_json(),
                    )
                )
                await session.commit()

            with TestClient(app) as client:
                response = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert response.status_code == 200
                payload = response.json()
                assert payload["run"]["run_seq"] == 1
                assert payload["run"]["status"] == "running"

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == StageName.ANALYZE.value)
                    )
                ).scalars().all()
                assert len(rows) == 1
                assert rows[0].run_seq == 1
                assert rows[0].status == StageStatus.RUNNING.value
        finally:
            await engine.dispose()

    asyncio.run(_run())
