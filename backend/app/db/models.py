from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class NovelFileFormat(str, Enum):
    TXT = "txt"
    EPUB = "epub"


class TaskStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class StageName(str, Enum):
    IMPORT = "import"
    SPLIT = "split"
    ANALYZE = "analyze"
    MARK = "mark"
    REWRITE = "rewrite"
    ASSEMBLE = "assemble"


class StageRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    STALE = "stale"


class ChapterStateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProviderType(str, Enum):
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"


class ConfigScope(str, Enum):
    GLOBAL = "global"
    NOVEL = "novel"


class Novel(Base):
    __tablename__ = "novels"
    __table_args__ = (
        CheckConstraint("file_format IN ('txt', 'epub')", name="ck_novels_file_format"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    file_format: Mapped[NovelFileFormat] = mapped_column(String, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    total_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    config_override_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tasks: Mapped[list["Task"]] = relationship(back_populates="novel", cascade="all, delete-orphan")
    configs: Mapped[list["Config"]] = relationship(back_populates="novel")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_tasks_status"),
        Index("idx_tasks_one_active_per_novel", "novel_id", unique=True, sqlite_where=text("status = 'active'")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    novel_id: Mapped[str] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(String, nullable=False, default=TaskStatus.ACTIVE.value)
    source_task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    auto_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    artifact_root: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    novel: Mapped["Novel"] = relationship(back_populates="tasks", foreign_keys=[novel_id])
    source_task: Mapped[Optional["Task"]] = relationship(remote_side=[id], foreign_keys=[source_task_id])
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    stage_runs: Mapped[list["StageRun"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("task_id", "chapter_index", name="uq_chapters_task_chapter_index"),
        Index("idx_chapters_task_index", "task_id", "chapter_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_count: Mapped[int] = mapped_column(Integer, nullable=False)

    task: Mapped["Task"] = relationship(back_populates="chapters", foreign_keys=[task_id])


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('import', 'split', 'analyze', 'mark', 'rewrite', 'assemble')",
            name="ck_stage_runs_stage",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'paused', 'stale')",
            name="ck_stage_runs_status",
        ),
        UniqueConstraint("task_id", "stage", "run_seq", name="uq_stage_runs_task_stage_seq"),
        Index("idx_stage_runs_singleflight", "task_id", "stage", unique=True, sqlite_where=text("status = 'running'")),
        Index(
            "idx_stage_runs_idempotency",
            "task_id",
            "stage",
            "run_idempotency_key",
            unique=True,
            sqlite_where=text("run_idempotency_key IS NOT NULL"),
        ),
        Index("idx_stage_runs_latest", "task_id", "stage", "run_seq"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[StageName] = mapped_column(String, nullable=False)
    run_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StageRunStatus] = mapped_column(String, nullable=False, default=StageRunStatus.PENDING.value)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    config_snapshot_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
    )

    task: Mapped["Task"] = relationship(back_populates="stage_runs", foreign_keys=[task_id])
    chapter_states: Mapped[list["ChapterState"]] = relationship(back_populates="stage_run", cascade="all, delete-orphan")


class ChapterState(Base):
    __tablename__ = "chapter_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_chapter_states_status",
        ),
        UniqueConstraint("stage_run_id", "chapter_index", name="uq_chapter_states_run_chapter"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    stage_run_id: Mapped[str] = mapped_column(ForeignKey("stage_runs.id", ondelete="CASCADE"), nullable=False)
    chapter_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ChapterStateStatus] = mapped_column(String, nullable=False, default=ChapterStateStatus.PENDING.value)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    stage_run: Mapped["StageRun"] = relationship(back_populates="chapter_states", foreign_keys=[stage_run_id])


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (
        CheckConstraint(
            "provider_type IN ('openai', 'openai_compatible')",
            name="ck_providers_provider_type",
        ),
        UniqueConstraint(
            "provider_type",
            "base_url",
            "credential_fingerprint",
            name="uq_providers_credentials",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider_type: Mapped[ProviderType] = mapped_column(String, nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=4000)
    top_p: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    presence_penalty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    frequency_penalty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model_list_cache_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_list_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    rpm_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    tpm_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100000)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class Config(Base):
    __tablename__ = "configs"
    __table_args__ = (
        CheckConstraint("scope IN ('global', 'novel')", name="ck_configs_scope"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[ConfigScope] = mapped_column(String, nullable=False, default=ConfigScope.GLOBAL.value)
    novel_id: Mapped[Optional[str]] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    novel: Mapped[Optional["Novel"]] = relationship(back_populates="configs", foreign_keys=[novel_id])
