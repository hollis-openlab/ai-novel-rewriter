from __future__ import annotations

import asyncio
import json
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
    router as chapters_router,
)
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import get_db_session
from backend.app.db.base import Base
from backend.app.db.models import Chapter as ChapterRow
from backend.app.db.models import Novel, NovelFileFormat, Task, TaskStatus
from backend.app.models.core import (
    Chapter,
    RewriteResult,
    RewriteResultStatus,
    RewriteStrategy,
)


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
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    return app


def _chapter_content() -> str:
    return "\n\n".join(
        [
            "第一段战斗动作很快，气氛瞬间绷紧。",
            "第二段人物对话推进线索，信息逐渐明确。",
            "第三段环境收束，为后续冲突留出空间。",
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


def _rewrite_result(*, status: RewriteResultStatus, rewritten_text: str) -> RewriteResult:
    content = _chapter_content()
    original_text = content.split("\n\n")[0]
    return RewriteResult(
        segment_id=str(uuid4()),
        chapter_index=1,
        paragraph_range=(1, 1),
        anchor_verified=True,
        strategy=RewriteStrategy.EXPAND,
        original_text=original_text,
        rewritten_text=rewritten_text,
        original_chars=len(original_text),
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


def _chapter_rewrite_path(store: ArtifactStore) -> Path:
    return store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / CHAPTER_REWRITE_FILE_TEMPLATE.format(chapter_index=1)


def _aggregate_path(store: ArtifactStore) -> Path:
    return store.stage_dir("novel-1", "task-1", REWRITE_STAGE_NAME) / REWRITE_AGGREGATE_FILENAME


def test_rewrite_micro_tune_accept_then_edit_records_audit_trail(tmp_path: Path) -> None:
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
                accepted = client.put(
                    f"/novels/novel-1/chapters/1/rewrites/{segment_id}",
                    json={"action": "accept", "note": "确认可用"},
                )
                assert accepted.status_code == 200
                accepted_json = accepted.json()
                assert accepted_json["status"] == "accepted"
                assert accepted_json["audit_entries"] == 1

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
                assert edited_json["audit_entries"] == 2

                latest = client.get("/novels/novel-1/chapters/1/rewrites")
                assert latest.status_code == 200
                latest_json = latest.json()
                assert latest_json[0]["status"] == "accepted_edited"
                assert latest_json[0]["rewritten_text"] == edited_text
                assert latest_json[0]["manual_edited_text"] == edited_text
                assert latest_json[0]["rollback_snapshot"]["status"] == "accepted"
                assert [entry["action"] for entry in latest_json[0]["audit_trail"]] == ["accept", "edit"]

            artifact = json.loads(_chapter_rewrite_path(store).read_text(encoding="utf-8"))
            assert artifact["segments"][0]["status"] == "accepted_edited"
            assert artifact["segments"][0]["manual_edited_text"] == "人工微调后的改写版本。"
            assert [entry["action"] for entry in artifact["segments"][0]["audit_trail"]] == ["accept", "edit"]
            assert artifact["audit_trail"][-1]["action"] == "edit"
            assert _aggregate_path(store).exists()
        finally:
            await engine.dispose()

    asyncio.run(_run())
