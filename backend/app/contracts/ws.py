from __future__ import annotations

from enum import Enum
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ..models.core import StageName


class WsMessageType(str, Enum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    PONG = "pong"
    STAGE_PROGRESS = "stage_progress"
    CHAPTER_COMPLETED = "chapter_completed"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    CHAPTER_FAILED = "chapter_failed"
    TASK_PAUSED = "task_paused"
    TASK_RESUMED = "task_resumed"
    STAGE_STALE = "stage_stale"
    WORKER_POOL_STATUS = "worker_pool_status"


class WsBaseMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WsMessageType


class WsSubscribeMessage(WsBaseMessage):
    type: Literal[WsMessageType.SUBSCRIBE] = WsMessageType.SUBSCRIBE
    novel_id: str


class WsUnsubscribeMessage(WsBaseMessage):
    type: Literal[WsMessageType.UNSUBSCRIBE] = WsMessageType.UNSUBSCRIBE
    novel_id: str


class WsPingMessage(WsBaseMessage):
    type: Literal[WsMessageType.PING] = WsMessageType.PING
    nonce: str | None = None


class WsPongMessage(WsBaseMessage):
    type: Literal[WsMessageType.PONG] = WsMessageType.PONG
    nonce: str | None = None


class WsStageProgressMessage(WsBaseMessage):
    type: Literal[WsMessageType.STAGE_PROGRESS] = WsMessageType.STAGE_PROGRESS
    novel_id: str
    stage: StageName
    chapters_done: int = Field(ge=0)
    chapters_total: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class WsChapterCompletedMessage(WsBaseMessage):
    type: Literal[WsMessageType.CHAPTER_COMPLETED] = WsMessageType.CHAPTER_COMPLETED
    novel_id: str
    stage: StageName
    chapter_index: int = Field(ge=1)


class WsStageCompletedMessage(WsBaseMessage):
    type: Literal[WsMessageType.STAGE_COMPLETED] = WsMessageType.STAGE_COMPLETED
    novel_id: str
    stage: StageName
    duration_ms: int = Field(ge=0)


class WsStageFailedMessage(WsBaseMessage):
    type: Literal[WsMessageType.STAGE_FAILED] = WsMessageType.STAGE_FAILED
    novel_id: str
    stage: StageName
    error: str


class WsChapterFailedMessage(WsBaseMessage):
    type: Literal[WsMessageType.CHAPTER_FAILED] = WsMessageType.CHAPTER_FAILED
    novel_id: str
    stage: StageName
    chapter_index: int = Field(ge=1)
    error: str
    retries_exhausted: bool


class WsTaskPausedMessage(WsBaseMessage):
    type: Literal[WsMessageType.TASK_PAUSED] = WsMessageType.TASK_PAUSED
    novel_id: str
    stage: StageName
    at_chapter: int | None = None


class WsTaskResumedMessage(WsBaseMessage):
    type: Literal[WsMessageType.TASK_RESUMED] = WsMessageType.TASK_RESUMED
    novel_id: str
    stage: StageName
    resume_from_chapter: int | None = None


class WsStageStaleMessage(WsBaseMessage):
    type: Literal[WsMessageType.STAGE_STALE] = WsMessageType.STAGE_STALE
    novel_id: str
    stage: StageName
    caused_by: str | None = None


class WsWorkerPoolStatusMessage(WsBaseMessage):
    type: Literal[WsMessageType.WORKER_POOL_STATUS] = WsMessageType.WORKER_POOL_STATUS
    active_workers: int = Field(ge=0)
    idle_workers: int = Field(ge=0)
    queue_length: int = Field(ge=0)


WsClientMessage = WsSubscribeMessage | WsUnsubscribeMessage | WsPingMessage | WsPongMessage
WsServerMessage = (
    WsPingMessage
    | WsPongMessage
    | WsStageProgressMessage
    | WsChapterCompletedMessage
    | WsStageCompletedMessage
    | WsStageFailedMessage
    | WsChapterFailedMessage
    | WsTaskPausedMessage
    | WsTaskResumedMessage
    | WsStageStaleMessage
    | WsWorkerPoolStatusMessage
)
