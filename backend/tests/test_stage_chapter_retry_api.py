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
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import backend.app.api.routes.stages as stages_routes
from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, Novel, Provider, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.llm.interface import CompletionResponse, UsageInfo
from backend.app.llm.prompting import StagePromptBundle
from backend.app.llm.validation import AnalyzeValidationResult
from backend.app.models.core import (
    ChapterAnalysis,
    ProviderType,
    RewritePotential,
    RewriteResult,
    RewriteResultStatus,
    SceneSegment,
)
from backend.app.services.analyze_pipeline import AnalyzeChapterRequest, AnalyzeChapterResult
from backend.app.services.config_store import ConfigSnapshot, RewriteRule, SceneRule, save_snapshot
from backend.app.services.rewrite_pipeline import RewriteSegmentRequest
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
    seed_secondary_provider: bool = False,
) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Retry Test Novel",
                original_filename="retry.txt",
                file_format="txt",
                file_size=1024,
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
                    content="第三段",
                    start_offset=10,
                    end_offset=13,
                    char_count=3,
                    paragraph_count=1,
                ),
            ]
        )
        session.add(
            Provider(
                id="provider-1",
                name="Retry Provider",
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
        if seed_secondary_provider:
            session.add(
                Provider(
                    id="provider-2",
                    name="Retry Provider 2",
                    provider_type=ProviderType.OPENAI_COMPATIBLE.value,
                    credential_fingerprint="fingerprint-2",
                    api_key_encrypted="encrypted-key-2",
                    base_url="https://example2.com/v1",
                    model_name="gpt-4.1-mini",
                    temperature=0.3,
                    max_tokens=2048,
                    top_p=0.9,
                    presence_penalty=0.0,
                    frequency_penalty=0.0,
                    rpm_limit=30,
                    tpm_limit=50000,
                    is_active=True,
                    created_at=datetime.utcnow(),
                )
            )
        await save_snapshot(
            session,
            ConfigSnapshot(
                global_prompt="全局提示词",
                scene_rules=[SceneRule(scene_type="battle", keywords=["战斗"])],
                rewrite_rules=[RewriteRule(scene_type="battle", strategy="expand", target_ratio=1.2, priority=0)],
            ),
        )
        await session.commit()


def _analysis_for_chapter(chapter_index: int) -> ChapterAnalysis:
    if chapter_index == 1:
        scenes = [
            SceneSegment(
                scene_type="battle",
                paragraph_range=(1, 2),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="可拓展改写",
                    priority=3,
                ),
            )
        ]
        summary = "第一章分析"
    else:
        scenes = []
        summary = "第二章分析"
    return ChapterAnalysis(
        summary=summary,
        characters=[],
        key_events=[],
        scenes=scenes,
        location="城内",
        tone="平静",
    )


@pytest.fixture(autouse=True)
def _patch_stage_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _analysis_for_chapter(request.chapter_index)
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
                latency_ms=10,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            ),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    async def _fake_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        rewritten_text = f"重写:{request.chapter.index}:{request.segment.scene_type}"
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            char_offset_range=request.segment.char_offset_range,
            rewrite_windows=list(request.segment.rewrite_windows or []),
            scene_type=request.segment.scene_type,
            target_ratio=request.segment.target_ratio,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text="原文",
            rewritten_text=rewritten_text,
            original_chars=2,
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
            error_code=None,
            error_detail=None,
            manual_edited_text=None,
            rollback_snapshot=None,
            audit_trail=[],
        )

    monkeypatch.setattr(stages_routes, "decrypt_api_key", lambda value: "sk-test")
    monkeypatch.setattr(stages_routes, "analyze_chapter", _fake_analyze_chapter)
    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _fake_execute_rewrite_segment)


def test_retry_analyze_chapter_updates_analysis_and_mark(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-a"
            task_id = "task-retry-a"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                retry_resp = client.post(f"/novels/{novel_id}/stages/analyze/chapters/1/retry")
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["analysis_updated"] is True
                assert payload["mark_updated"] is True

                analyze_artifact = client.get(f"/novels/{novel_id}/stages/analyze/artifact")
                assert analyze_artifact.status_code == 200
                assert analyze_artifact.json()["artifact"]["chapter_retry"]["chapter_index"] == 1

                mark_artifact = client.get(f"/novels/{novel_id}/stages/mark/artifact")
                assert mark_artifact.status_code == 200
                assert mark_artifact.json()["artifact"]["chapter_retry"]["chapter_index"] == 1

            store = ArtifactStore(tmp_path / "artifacts")
            plan_path = store.stage_dir(novel_id, task_id, "mark") / "mark_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            assert len(plan["chapters"]) == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_updates_rewrite_artifacts(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-b"
            task_id = "task-retry-b"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200
                rewrite_run = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert rewrite_run.status_code == 200

                retry_resp = client.post(f"/novels/{novel_id}/stages/rewrite/chapters/1/retry")
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["segments_total"] >= 1

                rewrite_artifact = client.get(f"/novels/{novel_id}/stages/rewrite/artifact")
                assert rewrite_artifact.status_code == 200
                assert rewrite_artifact.json()["artifact"]["chapter_retry"]["chapter_index"] == 1

            store = ArtifactStore(tmp_path / "artifacts")
            aggregate_path = store.stage_dir(novel_id, task_id, "rewrite") / "rewrites.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
            chapter1 = next(item for item in aggregate["chapters"] if int(item["chapter_index"]) == 1)
            assert len(chapter1["segments"]) >= 1
            assert str(chapter1["segments"][0]["rewritten_text"]).startswith("重写:")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_applies_chapter_added_chars_override(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-b-override"
            task_id = "task-retry-b-override"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                retry_resp = client.post(
                    f"/novels/{novel_id}/stages/rewrite/chapters/1/retry",
                    json={"rewrite_target_added_chars": 180},
                )
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["rewrite_target_added_chars_override"] == 180

            store = ArtifactStore(tmp_path / "artifacts")
            aggregate_path = store.stage_dir(novel_id, task_id, "rewrite") / "rewrites.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
            chapter1 = next(item for item in aggregate["chapters"] if int(item["chapter_index"]) == 1)
            assert len(chapter1["segments"]) >= 1
            mark_plan_path = store.stage_dir(novel_id, task_id, "mark") / "mark_plan.json"
            mark_plan = json.loads(mark_plan_path.read_text(encoding="utf-8"))
            mark_chapter1 = next(item for item in mark_plan["chapters"] if int(item["chapter_index"]) == 1)
            marked_original_total = sum(int(segment["original_chars"]) for segment in mark_chapter1["segments"])
            rewritten_target_total = sum(int(segment["target_chars"]) for segment in chapter1["segments"])
            assert rewritten_target_total - marked_original_total == 180
            for segment in chapter1["segments"]:
                target_chars = int(segment["target_chars"])
                assert int(segment["target_chars_min"]) < target_chars
                assert int(segment["target_chars_max"]) > target_chars
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_uses_selected_provider_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested_models: list[str] = []

    async def _tracked_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        requested_models.append(request.model_name)
        rewritten_text = f"重写:{request.chapter.index}:{request.segment.scene_type}:{request.model_name}"
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            char_offset_range=request.segment.char_offset_range,
            rewrite_windows=list(request.segment.rewrite_windows or []),
            scene_type=request.segment.scene_type,
            target_ratio=request.segment.target_ratio,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text="原文",
            rewritten_text=rewritten_text,
            original_chars=2,
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
            error_code=None,
            error_detail=None,
            manual_edited_text=None,
            rollback_snapshot=None,
            audit_trail=[],
        )

    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _tracked_execute_rewrite_segment)

    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-b-provider-override"
            task_id = "task-retry-b-provider-override"
            await _seed_task_fixture(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                seed_secondary_provider=True,
            )
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                retry_resp = client.post(
                    f"/novels/{novel_id}/stages/rewrite/chapters/1/retry",
                    json={"provider_id": "provider-2"},
                )
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["segments_total"] >= 1

                run_detail = client.get(f"/novels/{novel_id}/stages/rewrite/runs/1")
                assert run_detail.status_code == 200
                snapshot = run_detail.json()["run"]["config_snapshot"]
                assert snapshot["provider_id"] == "provider-2"
                assert snapshot["model_name"] == "gpt-4.1-mini"

            assert requested_models
            assert all(model == "gpt-4.1-mini" for model in requested_models)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_applies_window_mode_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed_flags: list[tuple[bool, bool, bool]] = []

    async def _tracked_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        observed_flags.append(
            (
                bool(request.window_mode_enabled),
                bool(request.window_guardrail_enabled),
                bool(request.window_audit_enabled),
            )
        )
        rewritten_text = f"重写:{request.chapter.index}:{request.segment.scene_type}"
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            char_offset_range=request.segment.char_offset_range,
            rewrite_windows=list(request.segment.rewrite_windows or []),
            scene_type=request.segment.scene_type,
            target_ratio=request.segment.target_ratio,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text="原文",
            rewritten_text=rewritten_text,
            original_chars=2,
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
            error_code=None,
            error_detail=None,
            manual_edited_text=None,
            rollback_snapshot=None,
            audit_trail=[],
        )

    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _tracked_execute_rewrite_segment)

    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-b-window-mode"
            task_id = "task-retry-b-window-mode"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                retry_resp = client.post(
                    f"/novels/{novel_id}/stages/rewrite/chapters/1/retry",
                    json={
                        "rewrite_window_mode_enabled": False,
                        "rewrite_window_guardrail_enabled": False,
                        "rewrite_window_audit_enabled": False,
                    },
                )
                assert retry_resp.status_code == 200
                assert retry_resp.json()["status"] == "completed"

                run_detail = client.get(f"/novels/{novel_id}/stages/rewrite/runs/1")
                assert run_detail.status_code == 200
                snapshot = run_detail.json()["run"]["config_snapshot"]
                assert snapshot["rewrite_window_mode"]["enabled"] is False
                assert snapshot["rewrite_window_mode"]["guardrail_enabled"] is False
                assert snapshot["rewrite_window_mode"]["audit_enabled"] is False

            assert observed_flags
            assert all(flags == (False, False, False) for flags in observed_flags)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_force_rerun_reexecutes_completed_segments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str]] = []

    async def _tracked_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        calls.append((request.chapter.index, request.segment.segment_id))
        call_seq = len(calls)
        rewritten_text = f"重写:{request.chapter.index}:{call_seq}"
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            char_offset_range=request.segment.char_offset_range,
            rewrite_windows=list(request.segment.rewrite_windows or []),
            scene_type=request.segment.scene_type,
            target_ratio=request.segment.target_ratio,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text="原文",
            rewritten_text=rewritten_text,
            original_chars=2,
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
            error_code=None,
            error_detail=None,
            manual_edited_text=None,
            rollback_snapshot=None,
            audit_trail=[],
        )

    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _tracked_execute_rewrite_segment)

    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-b-force-rerun"
            task_id = "task-retry-b-force-rerun"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200
                rewrite_run = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert rewrite_run.status_code == 200
                baseline_calls = len(calls)
                assert baseline_calls >= 1

                # Default retry keeps "补跑缺失/失败" semantics and skips already completed segments.
                retry_no_force = client.post(f"/novels/{novel_id}/stages/rewrite/chapters/1/retry")
                assert retry_no_force.status_code == 200
                assert len(calls) == baseline_calls

                # Force rerun should execute the completed segment again.
                retry_force = client.post(
                    f"/novels/{novel_id}/stages/rewrite/chapters/1/retry",
                    json={"force_rerun": True},
                )
                assert retry_force.status_code == 200
                retry_force_payload = retry_force.json()
                assert retry_force_payload["status"] == "completed"
                assert retry_force_payload["force_rerun"] is True
                assert len(calls) == baseline_calls + 1

            store = ArtifactStore(tmp_path / "artifacts")
            chapter_path = store.stage_dir(novel_id, task_id, "rewrite") / "ch_001_rewrites.json"
            chapter_payload = json.loads(chapter_path.read_text(encoding="utf-8"))
            rewritten_text = str(chapter_payload["segments"][0]["rewritten_text"])
            assert rewritten_text == "重写:1:2"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_analyze_chapter_bootstraps_stage_run_without_prior_run(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-c"
            task_id = "task-retry-c"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                retry_resp = client.post(f"/novels/{novel_id}/stages/analyze/chapters/1/retry")
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["analysis_updated"] is True

                run_detail = client.get(f"/novels/{novel_id}/stages/analyze/runs/1")
                assert run_detail.status_code == 200
                run_payload = run_detail.json()["run"]
                assert run_payload["status"] == "paused"
                assert run_payload["chapters_done"] == 1
                assert run_payload["chapters_total"] == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_retry_rewrite_chapter_bootstraps_stage_run_without_prior_run(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-retry-d"
            task_id = "task-retry-d"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                retry_resp = client.post(f"/novels/{novel_id}/stages/rewrite/chapters/1/retry")
                assert retry_resp.status_code == 200
                payload = retry_resp.json()
                assert payload["status"] == "completed"
                assert payload["segments_total"] >= 1

                run_detail = client.get(f"/novels/{novel_id}/stages/rewrite/runs/1")
                assert run_detail.status_code == 200
                run_payload = run_detail.json()["run"]
                assert run_payload["status"] == "completed"
                assert run_payload["chapters_done"] == 2
                assert run_payload["chapters_total"] == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_resume_analyze_continues_only_pending_chapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _tracked_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analyzed_chapters.append(request.chapter_index)
        analysis = _analysis_for_chapter(request.chapter_index)
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
                latency_ms=10,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            ),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    analyzed_chapters: list[int] = []
    monkeypatch.setattr(stages_routes, "analyze_chapter", _tracked_analyze_chapter)

    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-resume-a"
            task_id = "task-resume-a"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                first = client.post(f"/novels/{novel_id}/stages/analyze/chapters/1/retry")
                assert first.status_code == 200
                assert first.json()["status"] == "completed"

                resumed = client.post(f"/novels/{novel_id}/stages/analyze/resume")
                assert resumed.status_code == 200
                run = resumed.json()["run"]
                assert run["status"] == "completed"
                assert run["chapters_done"] == 2
                assert run["chapters_total"] == 2

            assert analyzed_chapters.count(1) == 1
            assert analyzed_chapters.count(2) == 1
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_resume_rewrite_continues_only_pending_chapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _analysis_for_rewrite_resume(chapter_index: int) -> ChapterAnalysis:
        return ChapterAnalysis(
            summary=f"第{chapter_index}章分析",
            characters=[],
            key_events=[],
            scenes=[
                SceneSegment(
                    scene_type="battle",
                    paragraph_range=(1, 1),
                    rewrite_potential=RewritePotential(
                        expandable=True,
                        rewritable=True,
                        suggestion="可拓展改写",
                        priority=3,
                    ),
                )
            ],
            location="城内",
            tone="平静",
        )

    async def _tracked_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _analysis_for_rewrite_resume(request.chapter_index)
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
                latency_ms=10,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            ),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    async def _tracked_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        rewritten_chapters.append(request.chapter.index)
        rewritten_text = f"重写:{request.chapter.index}:{request.segment.scene_type}"
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            scene_type=request.segment.scene_type,
            target_ratio=request.segment.target_ratio,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text="原文",
            rewritten_text=rewritten_text,
            original_chars=2,
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
            error_code=None,
            error_detail=None,
            manual_edited_text=None,
            rollback_snapshot=None,
            audit_trail=[],
        )

    rewritten_chapters: list[int] = []
    monkeypatch.setattr(stages_routes, "analyze_chapter", _tracked_analyze_chapter)
    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _tracked_execute_rewrite_segment)

    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-resume-b"
            task_id = "task-resume-b"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                analyze_run = client.post(f"/novels/{novel_id}/stages/analyze/run")
                assert analyze_run.status_code == 200

                first = client.post(f"/novels/{novel_id}/stages/rewrite/chapters/1/retry")
                assert first.status_code == 200
                assert first.json()["status"] == "completed"

                resumed = client.post(f"/novels/{novel_id}/stages/rewrite/resume")
                assert resumed.status_code == 200
                run = resumed.json()["run"]
                assert run["status"] == "completed"
                assert run["chapters_done"] == 2
                assert run["chapters_total"] == 2

            assert rewritten_chapters.count(1) == 1
            assert rewritten_chapters.count(2) == 1
        finally:
            await engine.dispose()

    asyncio.run(_run())
