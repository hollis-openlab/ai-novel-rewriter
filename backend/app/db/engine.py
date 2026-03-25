from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import event, text
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .base import Base


DEFAULT_DB_FILENAME = "ai-novel-backend.db"
DATABASE_URL_ENV = "AI_NOVEL_DATABASE_URL"

_ENGINE: AsyncEngine | None = None
_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def get_database_path() -> Path:
    return _project_root() / "data" / DEFAULT_DB_FILENAME


def get_database_url() -> str:
    db_url = os.environ.get(DATABASE_URL_ENV)
    if db_url:
        return db_url
    db_path = get_database_path()
    return f"sqlite+aiosqlite:///{db_path}"


def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:  # noqa: ANN001, ARG001
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception:
        # Keep startup resilient; PRAGMA support is best-effort for non-SQLite backends.
        pass


def get_engine(echo: bool = False) -> AsyncEngine:
    global _ENGINE
    if _ENGINE is None:
        database_url = get_database_url()
        _ENGINE = create_async_engine(database_url, echo=echo, future=True)
        if database_url.startswith("sqlite"):
            event.listen(_ENGINE.sync_engine, "connect", _configure_sqlite_connection)
    return _ENGINE


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _SESSION_MAKER
    if _SESSION_MAKER is None:
        _SESSION_MAKER = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _SESSION_MAKER


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def init_db(engine: Optional[AsyncEngine] = None) -> None:
    active_engine = engine or get_engine()
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with active_engine.begin() as conn:
        if active_engine.url.get_backend_name() == "sqlite":
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine(engine: Optional[AsyncEngine] = None) -> None:
    active_engine = engine or _ENGINE
    if active_engine is not None:
        await active_engine.dispose()
