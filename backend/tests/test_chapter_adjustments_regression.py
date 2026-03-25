from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from backend.app.api.routes.chapters import rename_chapter, split_chapter
from backend.app.api.schemas import ChapterRenameRequest, ChapterSplitRequest
from backend.app.core.artifact_store import ArtifactStore
from backend.app.db.base import Base
from backend.app.db.models import Chapter as ChapterRow
from backend.app.db.models import Novel, NovelFileFormat, Task, TaskStatus


class FakeRequest:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(artifact_store=artifact_store))


async def _prepare_session(db_path) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_chapter_rename_and_split_survive_duplicate_preview_id(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        request = FakeRequest(store)

        engine, sessionmaker = await _prepare_session(tmp_path / "chapters.db")
        try:
            async with sessionmaker() as session:
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
                        artifact_root=str(tmp_path / "data" / "novels" / "novel-1" / "tasks" / "task-1"),
                    )
                )
                session.add_all(
                    [
                        ChapterRow(
                            id="chapter-1",
                            task_id="task-1",
                            chapter_index=1,
                            title="第一章",
                            content="第一段。\n\n第二段。\n\n第三段。",
                            start_offset=0,
                            end_offset=19,
                            char_count=19,
                            paragraph_count=3,
                        ),
                        ChapterRow(
                            id="chapter-1-split",
                            task_id="task-1",
                            chapter_index=2,
                            title="占位章",
                            content="占位内容。",
                            start_offset=19,
                            end_offset=23,
                            char_count=4,
                            paragraph_count=1,
                        ),
                    ]
                )
                await session.commit()

                renamed = await rename_chapter(
                    "novel-1",
                    1,
                    ChapterRenameRequest(title="重命名后的章节"),
                    request=request,
                    db=session,
                )
                assert renamed.total == 2
                assert renamed.data[0].title == "重命名后的章节"

                adjusted = await split_chapter(
                    "novel-1",
                    1,
                    ChapterSplitRequest(split_at_paragraph_index=2),
                    request=request,
                    db=session,
                )

                assert adjusted.total == 3
                ids = [chapter.id for chapter in adjusted.data]
                assert len(ids) == len(set(ids))

                rows = (
                    await session.execute(
                        select(ChapterRow).where(ChapterRow.task_id == "task-1").order_by(ChapterRow.chapter_index.asc())
                    )
                ).scalars().all()
                assert len(rows) == 3
                assert len({row.id for row in rows}) == 3
                assert rows[0].title == "重命名后的章节"

                status_payload = json.loads(
                    (store.stage_dir("novel-1", "task-1", "split") / "status.json").read_text(encoding="utf-8")
                )
                chapters_payload = json.loads(
                    (store.stage_dir("novel-1", "task-1", "split") / "chapters.json").read_text(encoding="utf-8")
                )
                assert status_payload["status"] == "completed"
                assert chapters_payload["status"] == "completed"
                assert len(chapters_payload["chapters"]) == 3
        finally:
            await engine.dispose()

    asyncio.run(_run())
