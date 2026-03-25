from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.artifacts import router as artifacts_router
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
    app.include_router(artifacts_router)

    @app.exception_handler(AppError)
    async def _app_error_handler(_, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, **exc.details))

    async def override_get_db_session():
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


async def _seed_task(sessionmaker: async_sessionmaker, *, novel_id: str, task_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title="Artifact Export Test",
                original_filename="demo.txt",
                file_format="txt",
                file_size=12,
                total_chars=40,
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
                content="第一段。",
                start_offset=0,
                end_offset=4,
                char_count=4,
                paragraph_count=1,
            )
        )
        session.add_all(
            [
                StageRun(
                    id=f"{task_id}-split-1",
                    task_id=task_id,
                    stage="split",
                    run_seq=1,
                    status="completed",
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                ),
                StageRun(
                    id=f"{task_id}-analyze-1",
                    task_id=task_id,
                    stage="analyze",
                    run_seq=1,
                    status="completed",
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                ),
                StageRun(
                    id=f"{task_id}-rewrite-1",
                    task_id=task_id,
                    stage="rewrite",
                    run_seq=1,
                    status="completed",
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                ),
            ]
        )
        await session.commit()


def test_export_artifact_markdown_diff_and_zip(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-artifacts"
            task_id = "task-artifacts"
            await _seed_task(sessionmaker, novel_id=novel_id, task_id=task_id)
            app = _build_app(sessionmaker, tmp_path / "artifacts")
            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)

                split_dir = store.stage_dir(novel_id, task_id, "split")
                store.ensure_json(
                    split_dir / "chapters.json",
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "chapters": [
                            {
                                "id": f"{task_id}:chapter-1",
                                "index": 1,
                                "chapter_index": 1,
                                "title": "第一章",
                                "content": "第一段。",
                                "char_count": 4,
                                "paragraph_count": 1,
                                "start_offset": 0,
                                "end_offset": 4,
                            }
                        ],
                    },
                )

                analyze_dir = store.stage_dir(novel_id, task_id, "analyze")
                store.ensure_json(
                    analyze_dir / "analysis.json",
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "chapter_count": 1,
                        "chapters": [
                            {
                                "chapter_index": 1,
                                "chapter_title": "第一章",
                                "analysis": {
                                    "summary": "摘要",
                                    "characters": [],
                                    "key_events": [],
                                    "scenes": [],
                                    "location": "城门",
                                    "tone": "平静",
                                },
                            }
                        ],
                    },
                )
                store.ensure_json(
                    analyze_dir / "ch_001_analysis.json",
                    {
                        "chapter_index": 1,
                        "chapter_title": "第一章",
                        "analysis": {
                            "summary": "摘要",
                            "characters": [],
                            "key_events": [],
                            "scenes": [],
                            "location": "城门",
                            "tone": "平静",
                        },
                    },
                )

                rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
                store.ensure_json(
                    rewrite_dir / "rewrites.json",
                    {
                        "novel_id": novel_id,
                        "task_id": task_id,
                        "chapter_count": 1,
                        "chapters": [
                            {
                                "chapter_index": 1,
                                "segments": [
                                    {
                                        "segment_id": "seg-1",
                                        "chapter_index": 1,
                                        "paragraph_range": [1, 1],
                                        "status": "accepted",
                                        "strategy": "rewrite",
                                        "original_text": "第一段。",
                                        "rewritten_text": "第一段（改写）。",
                                    }
                                ],
                            }
                        ],
                    },
                )
                store.ensure_json(
                    rewrite_dir / "ch_001_rewrites.json",
                    {
                        "chapter_index": 1,
                        "segments": [
                            {
                                "segment_id": "seg-1",
                                "chapter_index": 1,
                                "paragraph_range": [1, 1],
                                "status": "accepted",
                                "strategy": "rewrite",
                                "original_text": "第一段。",
                                "rewritten_text": "第一段（改写）。",
                            }
                        ],
                    },
                )

                analyze_md = client.get(f"/artifacts/novels/{novel_id}/stages/analyze/artifact", params={"format": "markdown"})
                assert analyze_md.status_code == 200
                assert analyze_md.headers["content-type"].startswith("text/markdown")
                assert "Analyze Artifact Report" in analyze_md.text

                rewrite_diff = client.get(f"/artifacts/novels/{novel_id}/stages/rewrite/artifact", params={"format": "diff"})
                assert rewrite_diff.status_code == 200
                assert rewrite_diff.headers["content-type"].startswith("text/x-diff")
                assert "Rewrite Diff" in rewrite_diff.text

                split_zip = client.get(f"/artifacts/novels/{novel_id}/stages/split/artifact", params={"format": "zip"})
                assert split_zip.status_code == 200
                zip_path = tmp_path / "split.zip"
                zip_path.write_bytes(split_zip.content)
                with ZipFile(zip_path) as archive:
                    assert "chapter_001.txt" in set(archive.namelist())
        finally:
            await engine.dispose()

    asyncio.run(_run())
