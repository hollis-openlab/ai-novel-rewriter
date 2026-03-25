from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.novels import router as novels_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Novel, Task
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
    app.include_router(novels_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(_, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


async def _seed_novel_and_task(sessionmaker: async_sessionmaker, *, novel_id: str, task_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Report Test",
                original_filename="novel.txt",
                file_format="txt",
                file_size=12,
                total_chars=34,
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
        await session.commit()


def test_get_quality_report_reads_active_task_artifact(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-quality-a"
            task_id = "task-quality-a"
            await _seed_novel_and_task(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                store = app.state.artifact_store
                report_path = store.stage_dir(novel_id, task_id, "assemble") / "quality_report.json"
                store.ensure_json(
                    report_path,
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "blocked": False,
                        "statistics": {"failed_segments": 0, "warning_count": 1},
                    },
                )
                response = client.get(f"/novels/{novel_id}/quality-report")
                assert response.status_code == 200
                payload = response.json()
                assert payload["novel_id"] == novel_id
                assert payload["task_id"] == task_id
                assert payload["blocked"] is False
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_get_quality_report_missing_file_returns_not_found(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-quality-b"
            task_id = "task-quality-b"
            await _seed_novel_and_task(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")

            with TestClient(app) as client:
                response = client.get(f"/novels/{novel_id}/quality-report")
                assert response.status_code == 404
                assert response.json()["error"]["code"] == "NOT_FOUND"
        finally:
            await engine.dispose()

    asyncio.run(_run())
