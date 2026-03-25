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
from backend.app.api.routes.chapters import router as chapters_router
from backend.app.api.routes.novels import router as novels_router
from backend.app.api.routes.stages import router as stages_router
from backend.app.contracts.api import SplitRuleSpec, SplitRulesConfigRequest
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Novel, Provider, StageRun
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
    RewriteStrategy,
    SceneSegment,
)
from backend.app.services.analyze_pipeline import AnalyzeChapterRequest, AnalyzeChapterResult
from backend.app.services.config_store import ConfigSnapshot, RewriteRule, SceneRule, save_snapshot
from backend.app.services.rewrite_pipeline import RewriteSegmentRequest
from backend.app.services.splitting import get_split_rules_snapshot, replace_split_rules_state
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
    app.include_router(novels_router)
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


def _chapter_text() -> str:
    return "\n\n".join(
        [
            "第一章",
            "第一段战斗开始。",
            "第二章",
            "第二段战斗继续。",
            "第三章",
            "第三段战斗收束。",
        ]
    )


def _analysis_for_chapter(chapter_index: int) -> ChapterAnalysis:
    return ChapterAnalysis(
        summary=f"第{chapter_index}章摘要",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="battle",
                paragraph_range=(2, 2),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充动作与细节",
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
        text=json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
        latency_ms=10,
        usage=UsageInfo(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        raw_response={"choices": [{"message": {"content": analysis.summary}}]},
    )


def _validation_from_analysis(analysis: ChapterAnalysis) -> AnalyzeValidationResult:
    return AnalyzeValidationResult(
        passed=True,
        parsed=analysis,
        details={"summary_chars": len(analysis.summary)},
    )


def _rewrite_text(request: RewriteSegmentRequest) -> str:
    paragraphs = [part.strip() for part in request.chapter.content.split("\n\n") if part.strip()]
    start, end = request.segment.paragraph_range
    original_text = "\n\n".join(paragraphs[start - 1 : end])
    return f"{original_text}（改写版）"


@pytest.fixture(autouse=True)
def _patch_llm_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_analyze_chapter(request: AnalyzeChapterRequest, **_: object):
        analysis = _analysis_for_chapter(request.chapter_index)
        return AnalyzeChapterResult(
            request=request,
            analysis=analysis,
            validation=_validation_from_analysis(analysis),
            completion=_completion_from_analysis(analysis),
            prompt_bundle=StagePromptBundle(stage="analyze", system_prompt="", user_prompt=""),
        )

    async def _fake_execute_rewrite_segment(request: RewriteSegmentRequest, **_: object) -> RewriteResult:
        rewritten_text = _rewrite_text(request)
        original_paragraphs = [part.strip() for part in request.chapter.content.split("\n\n") if part.strip()]
        start, end = request.segment.paragraph_range
        original_text = "\n\n".join(original_paragraphs[start - 1 : end])
        return RewriteResult(
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            paragraph_range=request.segment.paragraph_range,
            anchor_verified=True,
            strategy=request.segment.strategy,
            original_text=original_text,
            rewritten_text=rewritten_text,
            original_chars=len(original_text),
            rewritten_chars=len(rewritten_text),
            status=RewriteResultStatus.COMPLETED,
            attempts=1,
            provider_used=request.provider_type.value,
        )

    monkeypatch.setattr(stages_routes, "decrypt_api_key", lambda _: "sk-test")
    monkeypatch.setattr(stages_routes, "analyze_chapter", _fake_analyze_chapter)
    monkeypatch.setattr(stages_routes, "execute_rewrite_segment", _fake_execute_rewrite_segment)


@pytest.mark.usefixtures("isolated_data_dir")
def test_pipeline_import_to_assemble_e2e(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            app = _build_app(sessionmaker, tmp_path / "data")
            raw_text = _chapter_text()

            with TestClient(app) as client:
                imported = client.post(
                    "/novels/import",
                    files={"file": ("novel.txt", raw_text.encode("utf-8"), "text/plain")},
                )
                assert imported.status_code == 200
                imported_json = imported.json()
                novel_id = imported_json["novel_id"]
                task_id = imported_json["task_id"]

                async with sessionmaker() as session:
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

                split_preview = client.post(f"/novels/{novel_id}/stages/split/run", params={"run_idempotency_key": "split-1"})
                assert split_preview.status_code == 200
                split_json = split_preview.json()
                assert split_json["status"] == "paused"
                assert split_json["estimated_chapters"] == 3
                assert len(split_json["chapters"]) == 3

                confirm = client.post(
                    f"/novels/{novel_id}/stages/split/confirm",
                    json={"preview_token": split_json["preview_token"]},
                )
                assert confirm.status_code == 200
                assert confirm.json()["chapter_count"] == 3

                analyze = client.post(f"/novels/{novel_id}/stages/analyze/run", params={"run_idempotency_key": "analyze-1"})
                assert analyze.status_code == 200
                analyze_json = analyze.json()
                assert analyze_json["run"]["status"] == "completed"
                assert analyze_json["run"]["run_seq"] == 2

                mark = client.post(f"/novels/{novel_id}/stages/mark/run", params={"run_idempotency_key": "mark-1"})
                assert mark.status_code == 200
                assert mark.json()["run"]["status"] == "completed"

                rewrite = client.post(f"/novels/{novel_id}/stages/rewrite/run", params={"run_idempotency_key": "rewrite-1"})
                assert rewrite.status_code == 200
                rewrite_json = rewrite.json()
                assert rewrite_json["run"]["status"] == "completed"

                assemble = client.post(
                    f"/novels/{novel_id}/stages/assemble/run",
                    json={"force": True},
                    params={"run_idempotency_key": "assemble-1"},
                )
                assert assemble.status_code == 200
                assert assemble.json()["run"]["status"] == "completed"

                latest_analyze = client.get(f"/novels/{novel_id}/stages/analyze/run")
                assert latest_analyze.status_code == 200
                assert latest_analyze.json()["run"]["config_snapshot"]["model_name"] == "gpt-4o-mini"

                export = client.get(f"/novels/{novel_id}/export", params={"format": "txt", "force": True})
                assert export.status_code == 200
                assert "改写版" in export.text

            store = app.state.artifact_store
            output_path = store.stage_dir(novel_id, task_id, "assemble") / "output.txt"
            quality_path = store.stage_dir(novel_id, task_id, "assemble") / "quality_report.json"
            assert output_path.exists()
            assert quality_path.exists()
            assert "改写版" in output_path.read_text(encoding="utf-8")

            async with sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "analyze")
                    )
                ).scalars().all()
                assert any(row.status == "completed" for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@pytest.mark.usefixtures("isolated_data_dir")
def test_split_stage_run_respects_split_rule_id(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            app = _build_app(sessionmaker, tmp_path / "data")
            raw_text = "\n\n".join(
                [
                    "CHAPTER 1",
                    "第一段",
                    "CHAPTER 2",
                    "第二段",
                    "CHAPTER 3",
                    "第三段",
                ]
            )
            snapshot = get_split_rules_snapshot()
            replace_split_rules_state(
                SplitRulesConfigRequest(
                    builtin_rules=[rule.model_copy(update={"enabled": False}) for rule in snapshot.builtin_rules],
                    custom_rules=[
                        SplitRuleSpec(
                            id="rule-two-only",
                            name="Two Only",
                            pattern=r"^CHAPTER\s+[12]$",
                            priority=1,
                            enabled=True,
                            builtin=False,
                        ),
                        SplitRuleSpec(
                            id="rule-all",
                            name="All Chapters",
                            pattern=r"^CHAPTER\s+\d+$",
                            priority=2,
                            enabled=True,
                            builtin=False,
                        ),
                    ],
                )
            )

            with TestClient(app) as client:
                imported = client.post(
                    "/novels/import",
                    files={"file": ("novel.txt", raw_text.encode("utf-8"), "text/plain")},
                )
                assert imported.status_code == 200
                novel_id = imported.json()["novel_id"]

                split_preview = client.post(
                    f"/novels/{novel_id}/stages/split/run",
                    json={"split_rule_id": "rule-two-only"},
                )
                assert split_preview.status_code == 200
                split_payload = split_preview.json()
                assert split_payload["status"] == "paused"
                assert split_payload["estimated_chapters"] == 2
                assert len(split_payload["chapters"]) == 2
                assert [item["title"] for item in split_payload["chapters"]] == ["CHAPTER 1", "CHAPTER 2"]
        finally:
            await engine.dispose()

    asyncio.run(_run())
