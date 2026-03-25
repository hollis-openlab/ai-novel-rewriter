from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.novels import router as novels_router
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, error_payload
from backend.app.db import Chapter, Novel, Task
from backend.app.db.base import Base
from backend.app.db.engine import get_db_session
from backend.app.models.core import Chapter as CoreChapter
from backend.app.models.core import RewriteResult
from backend.app.services.assemble_pipeline import assemble_novel, assemble_results_to_dict


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


async def _seed_task(
    sessionmaker: async_sessionmaker,
    *,
    novel_id: str,
    task_id: str,
    chapters: list[tuple[int, str, str]],
    novel_title: str = "Export Test Novel",
) -> None:
    async with sessionmaker() as session:
        session.add(
            Novel(
                id=novel_id,
                title=novel_title,
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
    rewrite_dir = store.stage_dir(novel_id, task_id, "rewrite")
    rewrite_dir.mkdir(parents=True, exist_ok=True)
    store.ensure_json(
        rewrite_dir / "rewrites.json",
        {
            "novel_id": novel_id,
            "task_id": task_id,
            "chapter_count": len(chapters),
            "updated_at": datetime.utcnow().isoformat(),
            "chapters": chapters,
        },
    )


def _load_rewrite_results_map(store: ArtifactStore, novel_id: str, task_id: str) -> dict[int, list[RewriteResult]]:
    rewrite_path = store.stage_dir(novel_id, task_id, "rewrite") / "rewrites.json"
    payload = json.loads(rewrite_path.read_text(encoding="utf-8"))
    mapped: dict[int, list[RewriteResult]] = {}
    for chapter in payload.get("chapters", []):
        if not isinstance(chapter, dict):
            continue
        chapter_index = int(chapter.get("chapter_index") or 0)
        if chapter_index < 1:
            continue
        segments = chapter.get("segments", [])
        if not isinstance(segments, list):
            segments = []
        mapped[chapter_index] = [RewriteResult.model_validate(item) for item in segments if isinstance(item, dict)]
    return mapped


async def _seed_assemble_artifacts(
    sessionmaker: async_sessionmaker,
    store: ArtifactStore,
    *,
    novel_id: str,
    task_id: str,
    force: bool = False,
) -> None:
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Chapter)
                .where(Chapter.task_id == task_id)
                .order_by(Chapter.chapter_index.asc())
            )
        ).scalars().all()

    chapters = [
        CoreChapter(
            id=row.id,
            index=row.chapter_index,
            title=row.title,
            content=row.content,
            char_count=row.char_count,
            paragraph_count=row.paragraph_count,
            start_offset=row.start_offset,
            end_offset=row.end_offset,
        )
        for row in rows
    ]
    rewrite_results_map = _load_rewrite_results_map(store, novel_id, task_id)
    assembled = assemble_novel(
        novel_id,
        task_id,
        chapters,
        rewrite_results_map,
        stage_run_id=f"{task_id}-assemble-seeded",
        force=force,
    )
    assembled_payload = assemble_results_to_dict(assembled)
    stage_dir = store.stage_dir(novel_id, task_id, "assemble")
    stage_dir.mkdir(parents=True, exist_ok=True)
    store.ensure_json(stage_dir / "assemble_result.json", assembled_payload)
    (stage_dir / "output.txt").write_text(str(assembled_payload.get("assembled_text") or ""), encoding="utf-8")
    (stage_dir / "output.compare.txt").write_text(str(assembled_payload.get("compare_text") or ""), encoding="utf-8")
    quality_payload = assembled_payload.get("quality_report")
    store.ensure_json(
        stage_dir / "quality_report.json",
        {
            **(quality_payload if isinstance(quality_payload, dict) else {}),
            "novel_id": novel_id,
            "task_id": task_id,
            "stage": "assemble",
        },
    )
    manifest_payload = assembled_payload.get("export_manifest")
    store.ensure_json(
        stage_dir / "export_manifest.json",
        {
            **(manifest_payload if isinstance(manifest_payload, dict) else {}),
            "novel_id": novel_id,
            "task_id": task_id,
        },
    )


def test_export_txt_and_compare_success(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-ok"
            task_id = "task-export-ok"
            await _seed_task(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                chapters=[
                    (1, "第一章", "第一段。"),
                    (2, "第二章", "第二段。"),
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
                await _seed_assemble_artifacts(
                    sessionmaker,
                    store,
                    novel_id=novel_id,
                    task_id=task_id,
                )

                txt = client.get(f"/novels/{novel_id}/export", params={"format": "txt", "scope": "all"})
                assert txt.status_code == 200
                assert txt.headers["content-type"].startswith("text/plain")
                assert "第一段（改写）。" in txt.text

                compare = client.get(f"/novels/{novel_id}/export", params={"format": "compare", "scope": "all"})
                assert compare.status_code == 200
                assert compare.headers["content-type"].startswith("text/html")
                assert "原文" in compare.text
                assert "改写" in compare.text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_txt_sentence_linebreak_reflow_does_not_mutate_storage(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-reflow"
            task_id = "task-export-reflow"
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
                                    "rewritten_text": "第一句。第二句！第三句？",
                                    "original_chars": 4,
                                    "rewritten_chars": 12,
                                    "status": "accepted",
                                    "attempts": 1,
                                }
                            ],
                        }
                    ],
                )
                await _seed_assemble_artifacts(
                    sessionmaker,
                    store,
                    novel_id=novel_id,
                    task_id=task_id,
                )

                baseline = client.get(
                    f"/novels/{novel_id}/export",
                    params={"format": "txt", "scope": "all"},
                )
                assert baseline.status_code == 200
                assert "第一句。第二句！第三句？" in baseline.text

                reflowed = client.get(
                    f"/novels/{novel_id}/export",
                    params={"format": "txt", "scope": "all", "reflow": "sentence_linebreak"},
                )
                assert reflowed.status_code == 200
                assert "第一句。\n第二句！\n第三句？" in reflowed.text

                output_path = store.stage_dir(novel_id, task_id, "assemble") / "output.txt"
                assert output_path.exists()
                output_text = output_path.read_text(encoding="utf-8")
                assert "第一句。第二句！第三句？" in output_text
                assert "第一句。\n第二句！" not in output_text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_rejects_unknown_reflow_mode(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-bad-reflow"
            task_id = "task-export-bad-reflow"
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
                _seed_rewrite_aggregate(store, novel_id, task_id, [{"chapter_index": 1, "segments": []}])

                response = client.get(
                    f"/novels/{novel_id}/export",
                    params={"format": "txt", "scope": "all", "reflow": "unknown"},
                )
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_requires_assemble_artifact(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-requires-assemble"
            task_id = "task-export-requires-assemble"
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
                _seed_rewrite_aggregate(store, novel_id, task_id, [{"chapter_index": 1, "segments": []}])

                response = client.get(
                    f"/novels/{novel_id}/export",
                    params={"format": "txt", "scope": "all"},
                )
                assert response.status_code == 409
                payload = response.json()
                assert payload["error"]["code"] == "VALIDATION_ERROR"
                assert "run assemble first" in payload["error"]["message"]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_quality_gate_blocked_and_force_succeeds(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-block"
            task_id = "task-export-block"
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
                await _seed_assemble_artifacts(
                    sessionmaker,
                    store,
                    novel_id=novel_id,
                    task_id=task_id,
                    force=False,
                )

                blocked = client.get(f"/novels/{novel_id}/export", params={"format": "txt", "scope": "all"})
                assert blocked.status_code == 409
                assert blocked.json()["error"]["code"] == "QUALITY_GATE_BLOCKED"

                forced = client.get(
                    f"/novels/{novel_id}/export",
                    params={"format": "txt", "scope": "all", "force": "true"},
                )
                assert forced.status_code == 200
                assert forced.headers.get("x-risk-signature")
                assert "RISK EXPORT" in forced.text
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_epub_produces_epub_archive(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-epub"
            task_id = "task-export-epub"
            await _seed_task(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                chapters=[(1, "第一章", "第一段。"), (2, "第二章", "第二段。")],
            )
            app = _build_app(sessionmaker, tmp_path / "artifacts")
            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)
                _seed_rewrite_aggregate(store, novel_id, task_id, [{"chapter_index": 1, "segments": []}, {"chapter_index": 2, "segments": []}])
                await _seed_assemble_artifacts(
                    sessionmaker,
                    store,
                    novel_id=novel_id,
                    task_id=task_id,
                )

                response = client.get(f"/novels/{novel_id}/export", params={"format": "epub", "scope": "all"})
                assert response.status_code == 200
                assert response.headers["content-type"] == "application/epub+zip"
                with ZipFile(Path(tmp_path / "out.epub"), "w") as _:
                    pass
                epub_path = tmp_path / "export.epub"
                epub_path.write_bytes(response.content)
                with ZipFile(epub_path) as archive:
                    names = set(archive.namelist())
                    assert "mimetype" in names
                    assert "META-INF/container.xml" in names
                    assert "OEBPS/content.opf" in names
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_txt_with_non_ascii_title_uses_rfc5987_filename(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _prepare_session(tmp_path / "db.sqlite")
        try:
            novel_id = "novel-export-non-ascii"
            task_id = "task-export-non-ascii"
            await _seed_task(
                sessionmaker,
                novel_id=novel_id,
                task_id=task_id,
                chapters=[(1, "第一章", "第一段。")],
                novel_title="中文标题《测试》",
            )
            app = _build_app(sessionmaker, tmp_path / "artifacts")
            with TestClient(app) as client:
                store = app.state.artifact_store
                store.ensure_novel_dirs(novel_id)
                store.ensure_task_scaffold(novel_id, task_id)
                store.write_active_task_id(novel_id, task_id)
                _seed_rewrite_aggregate(store, novel_id, task_id, [{"chapter_index": 1, "segments": []}])
                await _seed_assemble_artifacts(
                    sessionmaker,
                    store,
                    novel_id=novel_id,
                    task_id=task_id,
                )

                response = client.get(f"/novels/{novel_id}/export", params={"format": "txt", "scope": "all"})
                assert response.status_code == 200
                disposition = response.headers["content-disposition"]
                assert "filename*=" in disposition
                assert "UTF-8''" in disposition
                assert "%E3%80%90AI%E3%80%91" in disposition
                assert ".txt" in disposition
                assert "第一段。" in response.text
        finally:
            await engine.dispose()

    asyncio.run(_run())
