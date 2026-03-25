from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.stages import router as stages_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, Novel, StageRun, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session


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
        app.state.artifact_store = store
        yield

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


async def _seed_task(
    sessionmaker: async_sessionmaker,
    *,
    novel_id: str,
    task_id: str,
    chapters: list[tuple[int, str, str]],
) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Assemble Test",
                original_filename="demo.txt",
                file_format="txt",
                file_size=10,
                total_chars=20,
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
        for chapter_index, title, content in chapters:
            session.add(
                Chapter(
                    id=f"{task_id}:chapter-{chapter_index}",
                    task_id=task_id,
                    chapter_index=chapter_index,
                    title=title,
                    content=content,
                    start_offset=0,
                    end_offset=len(content),
                    char_count=len(content),
                    paragraph_count=len([part for part in content.split("\n\n") if part.strip()]),
                )
            )
        await session.commit()


def _seed_rewrite_aggregate(store: ArtifactStore, novel_id: str, task_id: str, chapters: list[dict[str, object]]) -> None:
    stage_dir = store.stage_dir(novel_id, task_id, "rewrite")
    stage_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_count": len(chapters),
        "updated_at": datetime.utcnow().isoformat(),
        "chapters": chapters,
    }
    store.ensure_json(stage_dir / "rewrites.json", payload)


def test_assemble_stage_success_writes_artifacts(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-assemble-ok"
            task_id = "task-assemble-ok"
            await _seed_task(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                chapters=[
                    (1, "第一章", "第一段。\n\n第二段。"),
                    (2, "第二章", "第三段。"),
                ],
            )
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)
                _seed_rewrite_aggregate(
                    store,
                    novel_id,
                    task_id,
                    [
                        {
                            "chapter_index": 1,
                            "segments": [
                                {
                                    "segment_id": str(uuid4()),
                                    "chapter_index": 1,
                                    "paragraph_range": [1, 1],
                                    "anchor_verified": True,
                                    "strategy": "rewrite",
                                    "original_text": "第一段。",
                                    "rewritten_text": "第一段（改写）。",
                                    "original_chars": 4,
                                    "rewritten_chars": 8,
                                    "status": "accepted",
                                    "attempts": 1,
                                }
                            ],
                        },
                        {"chapter_index": 2, "segments": []},
                    ],
                )

                response = client.post(f"/novels/{novel_id}/stages/assemble/run")
                assert response.status_code == 200
                run = response.json()["run"]
                assert run["status"] == "completed"
                assert run["chapters_total"] == 2
                assert run["chapters_done"] == 2

                assemble_dir = store.stage_dir(novel_id, task_id, "assemble")
                output_text = (assemble_dir / "output.txt").read_text(encoding="utf-8")
                quality = json.loads((assemble_dir / "quality_report.json").read_text(encoding="utf-8"))
                manifest = json.loads((assemble_dir / "export_manifest.json").read_text(encoding="utf-8"))
                assert "第一段（改写）。" in output_text
                assert quality["blocked"] is False
                assert quality["stats"]["rewritten_segments"] == 1
                assert manifest["risk_export"] is False
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_assemble_stage_quality_gate_blocked(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-assemble-blocked"
            task_id = "task-assemble-blocked"
            await _seed_task(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                chapters=[(1, "第一章", "第一段。")],
            )
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)
                _seed_rewrite_aggregate(
                    store,
                    novel_id,
                    task_id,
                    [
                        {
                            "chapter_index": 1,
                            "segments": [
                                {
                                    "segment_id": str(uuid4()),
                                    "chapter_index": 1,
                                    "paragraph_range": [1, 1],
                                    "anchor_verified": True,
                                    "strategy": "rewrite",
                                    "original_text": "第一段。",
                                    "rewritten_text": "",
                                    "original_chars": 4,
                                    "rewritten_chars": 0,
                                    "status": "pending",
                                    "attempts": 0,
                                }
                            ],
                        }
                    ],
                )

                response = client.post(f"/novels/{novel_id}/stages/assemble/run")
                assert response.status_code == 409
                payload = response.json()
                assert payload["error"]["code"] == "QUALITY_GATE_BLOCKED"

            async with sessionmaker() as session:
                row = (
                    await session.execute(
                        select(StageRun).where(StageRun.task_id == task_id, StageRun.stage == "assemble")
                    )
                ).scalars().one()
                assert row.status == "failed"
        finally:
            await engine.dispose()

    asyncio.run(_run())
