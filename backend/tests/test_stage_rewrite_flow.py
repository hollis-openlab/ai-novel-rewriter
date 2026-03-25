from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import backend.app.api.routes.stages as stages_routes
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, ChapterState, Novel, Provider, StageRun, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.models.core import (
    Chapter as CoreChapter,
    ChapterAnalysis,
    CharacterState,
    ProviderType,
    RewriteWindow,
    RewritePotential,
    RewriteResult,
    RewriteResultStatus,
    SceneSegment,
    WindowAttempt,
    WindowAttemptAction,
    WindowGuardrail,
    WindowGuardrailLevel,
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
) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Rewrite Test Novel",
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
                    id=f"{task_id}:chapter-1",
                    task_id=task_id,
                    chapter_index=1,
                    title="第一章",
                    content="第一段战斗开场。\n\n第二段继续推进。",
                    start_offset=0,
                    end_offset=16,
                    char_count=16,
                    paragraph_count=2,
                ),
                Chapter(
                    id=f"{task_id}:chapter-2",
                    task_id=task_id,
                    chapter_index=2,
                    title="第二章",
                    content="第三段收束剧情。",
                    start_offset=17,
                    end_offset=25,
                    char_count=8,
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


def _chapter_models(task_id: str) -> list[CoreChapter]:
    chapter_one_content = "第一段战斗开场。\n\n第二段继续推进。"
    chapter_two_content = "第三段收束剧情。"
    return [
        CoreChapter(
            id=f"{task_id}:chapter-1",
            index=1,
            title="第一章",
            content=chapter_one_content,
            char_count=len(chapter_one_content),
            paragraph_count=2,
            start_offset=0,
            end_offset=len(chapter_one_content),
        ),
        CoreChapter(
            id=f"{task_id}:chapter-2",
            index=2,
            title="第二章",
            content=chapter_two_content,
            char_count=len(chapter_two_content),
            paragraph_count=1,
            start_offset=len(chapter_one_content) + 2,
            end_offset=len(chapter_one_content) + 2 + len(chapter_two_content),
        ),
    ]


def _analysis_one() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="第一章发生战斗冲突。",
        characters=[CharacterState(name="主角", emotion="紧张", state="迎战", role_in_chapter="主视角")],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="battle",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充动作细节",
                    priority=5,
                ),
            )
        ],
        location="城门",
        tone="紧张",
    )


def _analysis_two() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="第二章以收束为主。",
        characters=[CharacterState(name="主角", emotion="平静", state="收尾", role_in_chapter="主视角")],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="narration",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=False,
                    rewritable=False,
                    suggestion="无需改写",
                    priority=1,
                ),
            )
        ],
        location="营地",
        tone="平静",
    )


def test_rewrite_stage_writes_artifacts_and_preserves_unmodified_chapters(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-rewrite"
            task_id = "task-rewrite"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                chapters = _chapter_models(task_id)
                update_analysis_artifact(store, novel_id, task_id, 1, _analysis_one(), chapter_id=chapters[0].id, chapter_title=chapters[0].title)
                update_analysis_artifact(store, novel_id, task_id, 2, _analysis_two(), chapter_id=chapters[1].id, chapter_title=chapters[1].title)

                rules = [RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)]
                plan = build_rewrite_plan(novel_id, chapters, {1: _analysis_one(), 2: _analysis_two()}, rules)
                write_mark_artifacts(store, novel_id, task_id, plan)

                async def _fake_execute(segment_request, **kwargs):
                    start_offset, end_offset = segment_request.segment.char_offset_range or (0, 2)
                    window_id = f"{segment_request.segment.segment_id}:window-1"
                    return RewriteResult(
                        segment_id=segment_request.segment.segment_id,
                        chapter_index=segment_request.chapter.index,
                        paragraph_range=segment_request.segment.paragraph_range,
                        anchor_verified=False,
                        strategy=segment_request.segment.strategy,
                        original_text="原文",
                        rewritten_text="",
                        original_chars=2,
                        rewritten_chars=0,
                        status=RewriteResultStatus.FAILED,
                        attempts=1,
                        provider_used=segment_request.provider_type.value,
                        error_code="ANCHOR_MISMATCH",
                        error_detail="hash mismatch",
                        rewrite_windows=[
                            RewriteWindow(
                                window_id=window_id,
                                segment_id=segment_request.segment.segment_id,
                                chapter_index=segment_request.chapter.index,
                                start_offset=start_offset,
                                end_offset=end_offset,
                                hit_sentence_range=segment_request.segment.sentence_range,
                                context_sentence_range=segment_request.segment.sentence_range,
                                target_chars=segment_request.segment.target_chars,
                                target_chars_min=segment_request.segment.target_chars_min,
                                target_chars_max=segment_request.segment.target_chars_max,
                                source_fingerprint=segment_request.segment.source_fingerprint,
                                plan_version=segment_request.segment.plan_version,
                            )
                        ],
                        window_attempts=[
                            WindowAttempt(
                                window_id=window_id,
                                attempt_seq=1,
                                action=WindowAttemptAction.RETRY,
                                guardrail=WindowGuardrail(
                                    level=WindowGuardrailLevel.HARD_FAIL,
                                    codes=["REWRITE_START_FRAGMENT_BROKEN"],
                                ),
                            ),
                            WindowAttempt(
                                window_id=window_id,
                                attempt_seq=2,
                                action=WindowAttemptAction.ROLLBACK_ORIGINAL,
                                guardrail=WindowGuardrail(
                                    level=WindowGuardrailLevel.HARD_FAIL,
                                    codes=["REWRITE_LENGTH_SEVERE_OUTLIER"],
                                ),
                            ),
                        ],
                        manual_edited_text=None,
                        rollback_snapshot=None,
                        audit_trail=[],
                    )

                stages_routes.decrypt_api_key = lambda value: "sk-test"
                stages_routes.execute_rewrite_segment = _fake_execute

                response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert response.status_code == 200
                payload = response.json()
                assert payload["run"]["status"] == "completed"
                assert payload["run"]["warnings_count"] == 1
                assert payload["run"]["chapters_total"] == 2
                assert payload["run"]["chapters_done"] == 2

                rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
                ch1 = json.loads((rewrite_dir / "ch_001_rewrites.json").read_text(encoding="utf-8"))
                ch2 = json.loads((rewrite_dir / "ch_002_rewrites.json").read_text(encoding="utf-8"))
                aggregate = json.loads((rewrite_dir / "rewrites.json").read_text(encoding="utf-8"))

                assert len(ch1["segments"]) == 1
                assert ch1["segments"][0]["status"] == "failed"
                assert ch1["segments"][0]["error_code"] == "ANCHOR_MISMATCH"
                assert ch1["windows_total"] == 1
                assert ch1["windows_retried"] == 1
                assert ch1["windows_hard_failed"] == 1
                assert ch1["windows_rollback"] == 1
                assert ch2["segments"] == []
                assert aggregate["chapter_count"] == 2
                assert [item["chapter_index"] for item in aggregate["chapters"]] == [1, 2]

                run_seq = int(payload["run"]["run_seq"])
                latest = client.get(f"/novels/{novel_id}/stages/rewrite/run")
                assert latest.status_code == 200
                latest_payload = latest.json()
                assert latest_payload["window_metrics"] == {
                    "windows_total": 1,
                    "windows_retried": 1,
                    "windows_hard_failed": 1,
                    "windows_rollback": 1,
                    "windows_avg_chars": 8.0,
                    "window_retry_rate": 1.0,
                    "window_hard_fail_rate": 1.0,
                    "window_rollback_rate": 1.0,
                }

                detail = client.get(f"/novels/{novel_id}/stages/rewrite/runs/{run_seq}")
                assert detail.status_code == 200
                detail_payload = detail.json()
                assert detail_payload["window_metrics"] == latest_payload["window_metrics"]

            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "rewrite")
                    )
                ).scalars().one()
                assert row.status == "completed"
                assert row.warnings_count == 1
                assert row.chapters_total == 2
                assert row.chapters_done == 2
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_stage_run_skips_chapters_with_existing_terminal_results(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-rewrite-skip-existing"
            task_id = "task-rewrite-skip-existing"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                chapters = _chapter_models(task_id)
                update_analysis_artifact(store, novel_id, task_id, 1, _analysis_one(), chapter_id=chapters[0].id, chapter_title=chapters[0].title)
                update_analysis_artifact(store, novel_id, task_id, 2, _analysis_two(), chapter_id=chapters[1].id, chapter_title=chapters[1].title)

                rules = [RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)]
                plan = build_rewrite_plan(novel_id, chapters, {1: _analysis_one(), 2: _analysis_two()}, rules)
                write_mark_artifacts(store, novel_id, task_id, plan)

                chapter_one_plan = next(item for item in plan.chapters if item.chapter_index == 1)
                segment = chapter_one_plan.segments[0]
                existing_result = RewriteResult(
                    segment_id=segment.segment_id,
                    chapter_index=1,
                    paragraph_range=segment.paragraph_range,
                    char_offset_range=segment.char_offset_range,
                    rewrite_windows=list(segment.rewrite_windows or []),
                    anchor_verified=True,
                    strategy=segment.strategy,
                    original_text="原文",
                    rewritten_text="已完成改写",
                    original_chars=2,
                    rewritten_chars=5,
                    status=RewriteResultStatus.COMPLETED,
                    attempts=1,
                    provider_used=ProviderType.OPENAI_COMPATIBLE.value,
                    error_code=None,
                    error_detail=None,
                    manual_edited_text=None,
                    rollback_snapshot=None,
                    audit_trail=[],
                )
                existing_payload = {
                    "novel_id": novel_id,
                    "task_id": task_id,
                    "chapter_index": 1,
                    "updated_at": datetime.utcnow().isoformat(),
                    "segments": [existing_result.model_dump(mode="json")],
                    "audit_trail": [],
                }
                rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
                rewrite_dir.mkdir(parents=True, exist_ok=True)
                store.ensure_json(rewrite_dir / "ch_001_rewrites.json", existing_payload)
                store.ensure_json(
                    rewrite_dir / "rewrites.json",
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "chapter_count": 1,
                        "updated_at": datetime.utcnow().isoformat(),
                        "chapters": [existing_payload],
                    },
                )

                calls: list[int] = []

                async def _fake_execute(segment_request, **kwargs):
                    calls.append(int(segment_request.chapter.index))
                    return existing_result

                original_decrypt = stages_routes.decrypt_api_key
                original_execute = stages_routes.execute_rewrite_segment
                stages_routes.decrypt_api_key = lambda value: "sk-test"
                stages_routes.execute_rewrite_segment = _fake_execute
                try:
                    response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                finally:
                    stages_routes.decrypt_api_key = original_decrypt
                    stages_routes.execute_rewrite_segment = original_execute

                assert response.status_code == 200
                payload = response.json()
                assert payload["run"]["status"] == "completed"
                assert calls == []

                rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
                aggregate = json.loads((rewrite_dir / "rewrites.json").read_text(encoding="utf-8"))
                assert [item["chapter_index"] for item in aggregate["chapters"]] == [1, 2]
                by_index = {int(item["chapter_index"]): item for item in aggregate["chapters"]}
                assert by_index[1]["segments"][0]["status"] == "completed"
                assert by_index[2]["segments"] == []
                assert by_index[2]["completion_kind"] == "noop"
                assert by_index[2]["reason_code"] == "NO_REWRITE_WINDOW"
                assert by_index[2]["rewrite_status"] == "completed"

            async with sessionmaker() as session:
                latest_run = (
                    await session.execute(
                        select(StageRun)
                        .where(StageRun.task_id == task_id, StageRun.stage == "rewrite")
                        .order_by(StageRun.run_seq.desc())
                    )
                ).scalars().first()
                assert latest_run is not None
                assert latest_run.status == "completed"
                assert latest_run.chapters_total == 2
                assert latest_run.chapters_done == 2

                chapter_states = (
                    await session.execute(
                        select(ChapterState).where(ChapterState.stage_run_id == latest_run.id)
                    )
                ).scalars().all()
                chapter_status_by_index = {int(item.chapter_index): str(item.status) for item in chapter_states}
                assert chapter_status_by_index[1] == "completed"
                assert chapter_status_by_index[2] == "completed"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_stage_run_reruns_when_window_identity_changes(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-rewrite-window-identity"
            task_id = "task-rewrite-window-identity"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                chapters = _chapter_models(task_id)
                update_analysis_artifact(store, novel_id, task_id, 1, _analysis_one(), chapter_id=chapters[0].id, chapter_title=chapters[0].title)
                update_analysis_artifact(store, novel_id, task_id, 2, _analysis_two(), chapter_id=chapters[1].id, chapter_title=chapters[1].title)

                rules = [RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)]
                plan = build_rewrite_plan(novel_id, chapters, {1: _analysis_one(), 2: _analysis_two()}, rules)
                write_mark_artifacts(store, novel_id, task_id, plan)

                chapter_one_plan = next(item for item in plan.chapters if item.chapter_index == 1)
                segment = chapter_one_plan.segments[0]
                assert segment.rewrite_windows
                old_window = segment.rewrite_windows[0].model_copy(update={"plan_version": "old-plan-version"})
                stale_result = RewriteResult(
                    segment_id=segment.segment_id,
                    chapter_index=1,
                    paragraph_range=segment.paragraph_range,
                    char_offset_range=segment.char_offset_range,
                    rewrite_windows=[old_window],
                    anchor_verified=True,
                    strategy=segment.strategy,
                    original_text="原文",
                    rewritten_text="旧窗口结果",
                    original_chars=2,
                    rewritten_chars=5,
                    status=RewriteResultStatus.COMPLETED,
                    attempts=1,
                    provider_used=ProviderType.OPENAI_COMPATIBLE.value,
                )
                existing_payload = {
                    "novel_id": novel_id,
                    "task_id": task_id,
                    "chapter_index": 1,
                    "updated_at": datetime.utcnow().isoformat(),
                    "segments": [stale_result.model_dump(mode="json")],
                    "audit_trail": [],
                }
                rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
                rewrite_dir.mkdir(parents=True, exist_ok=True)
                store.ensure_json(rewrite_dir / "ch_001_rewrites.json", existing_payload)
                store.ensure_json(
                    rewrite_dir / "rewrites.json",
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "chapter_count": 1,
                        "updated_at": datetime.utcnow().isoformat(),
                        "chapters": [existing_payload],
                    },
                )

                calls: list[int] = []

                async def _fake_execute(segment_request, **kwargs):
                    calls.append(int(segment_request.chapter.index))
                    source = "第一段战斗开场。"
                    return RewriteResult(
                        segment_id=segment_request.segment.segment_id,
                        chapter_index=segment_request.chapter.index,
                        paragraph_range=segment_request.segment.paragraph_range,
                        char_offset_range=segment_request.segment.char_offset_range,
                        rewrite_windows=list(segment_request.segment.rewrite_windows or []),
                        anchor_verified=True,
                        strategy=segment_request.segment.strategy,
                        original_text=source,
                        rewritten_text=f"{source}（新结果）",
                        original_chars=len(source),
                        rewritten_chars=len(f"{source}（新结果）"),
                        status=RewriteResultStatus.COMPLETED,
                        attempts=1,
                        provider_used=ProviderType.OPENAI_COMPATIBLE.value,
                    )

                original_decrypt = stages_routes.decrypt_api_key
                original_execute = stages_routes.execute_rewrite_segment
                stages_routes.decrypt_api_key = lambda value: "sk-test"
                stages_routes.execute_rewrite_segment = _fake_execute
                try:
                    response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                finally:
                    stages_routes.decrypt_api_key = original_decrypt
                    stages_routes.execute_rewrite_segment = original_execute

                assert response.status_code == 200
                assert calls == [1]

                aggregate = json.loads((rewrite_dir / "rewrites.json").read_text(encoding="utf-8"))
                chapter_payload = next(item for item in aggregate["chapters"] if int(item["chapter_index"]) == 1)
                assert chapter_payload["segments"][0]["rewritten_text"].endswith("（新结果）")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_stage_persists_chapter_artifacts_during_progress(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-rewrite-progress"
            task_id = "task-rewrite-progress"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                chapters = _chapter_models(task_id)
                update_analysis_artifact(store, novel_id, task_id, 1, _analysis_one(), chapter_id=chapters[0].id, chapter_title=chapters[0].title)
                update_analysis_artifact(store, novel_id, task_id, 2, _analysis_two(), chapter_id=chapters[1].id, chapter_title=chapters[1].title)

                rules = [RewriteRule(scene_type="battle", strategy="rewrite", target_ratio=1.2, priority=0)]
                plan = build_rewrite_plan(novel_id, chapters, {1: _analysis_one(), 2: _analysis_two()}, rules)
                write_mark_artifacts(store, novel_id, task_id, plan)

                async def _fake_execute(segment_request, **kwargs):
                    return RewriteResult(
                        segment_id=segment_request.segment.segment_id,
                        chapter_index=segment_request.chapter.index,
                        paragraph_range=segment_request.segment.paragraph_range,
                        anchor_verified=True,
                        strategy=segment_request.segment.strategy,
                        original_text="原文",
                        rewritten_text="改写结果",
                        original_chars=2,
                        rewritten_chars=4,
                        status=RewriteResultStatus.COMPLETED,
                        attempts=1,
                        provider_used=segment_request.provider_type.value,
                        error_code=None,
                        error_detail=None,
                        manual_edited_text=None,
                        rollback_snapshot=None,
                        audit_trail=[],
                    )

                observed_progress_artifacts: list[tuple[int, bool, bool]] = []
                original_sync = stages_routes._sync_stage_run_artifacts

                async def _sync_with_progress_checks(request, novel_id_value, run, config_snapshot, *, extra=None):
                    result = await original_sync(
                        request,
                        novel_id_value,
                        run,
                        config_snapshot,
                        extra=extra,
                    )
                    chapter_progress = extra.get("chapter_progress") if isinstance(extra, dict) else None
                    if run.stage == "rewrite" and isinstance(chapter_progress, dict):
                        chapter_index = int(chapter_progress.get("chapter_index") or 0)
                        rewrite_dir = request.app.state.artifact_store.stage_dir(novel_id, task_id, "rewrite")
                        chapter_path = rewrite_dir / f"ch_{chapter_index:03d}_rewrites.json"
                        aggregate_path = rewrite_dir / "rewrites.json"
                        observed_progress_artifacts.append((chapter_index, chapter_path.exists(), aggregate_path.exists()))
                        assert chapter_path.exists()
                        assert aggregate_path.exists()
                        aggregate_payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
                        chapter_indexes = [int(item.get("chapter_index") or 0) for item in aggregate_payload.get("chapters", [])]
                        assert chapter_index in chapter_indexes
                    return result

                original_decrypt = stages_routes.decrypt_api_key
                original_execute = stages_routes.execute_rewrite_segment
                stages_routes.decrypt_api_key = lambda value: "sk-test"
                stages_routes.execute_rewrite_segment = _fake_execute
                stages_routes._sync_stage_run_artifacts = _sync_with_progress_checks
                try:
                    response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                finally:
                    stages_routes.decrypt_api_key = original_decrypt
                    stages_routes.execute_rewrite_segment = original_execute
                    stages_routes._sync_stage_run_artifacts = original_sync

                assert response.status_code == 200
                assert any(item[0] == 1 for item in observed_progress_artifacts)
                assert any(item[0] == 2 for item in observed_progress_artifacts)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_stage_without_mark_plan_fails_stage_run(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-rewrite-no-mark"
            task_id = "task-rewrite-no-mark"
            await _seed_task_fixture(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                response = client.post(f"/novels/{novel_id}/stages/rewrite/run")
                assert response.status_code == 404
                payload = response.json()
                assert payload["error"]["code"] == "NOT_FOUND"

            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "rewrite")
                    )
                ).scalars().one()
                assert row.status == "failed"
                assert row.error_message == "mark_plan.json not found for rewrite stage"
        finally:
            await engine.dispose()

    asyncio.run(_run())
