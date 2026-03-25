from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.contracts.api import (
    ChapterListResponse,
    ChapterUpdateAnalysisRequest,
    ChapterUpdateMarksRequest,
    CreateProviderRequest,
    ImportNovelResponse,
    ModelFetchRequest,
    ModelFetchResponse,
    NovelDetailResponse,
    NovelListResponse,
    ProviderListResponse,
    ProviderTestConnectionRequest,
    SplitConfirmRequest,
    SplitRuleSpec,
    SplitRulesPreviewRequest,
    SplitRulesPreviewResponse,
    StageActionRequest,
    StageActionResponse,
)
from backend.app.contracts.errors import ApiErrorDetail, ApiErrorResponse, ErrorCode, ValidationIssue
from backend.app.contracts.ws import (
    WsClientMessage,
    WsMessageType,
    WsServerMessage,
)
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    FileFormat,
    NovelMeta,
    ProviderType,
    RewriteResult,
    StageName,
    StageRunInfo,
    StageStatus,
)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    app_name: str
    version: str


class WorkerStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: int = Field(default=0, ge=0)
    idle: int = Field(default=0, ge=0)
    queue_size: int = Field(default=0, ge=0)


class OrphanArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    reason: str
    path: str


class OrphanArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[OrphanArtifactResponse] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


class ArtifactScaffoldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    created_stages: list[StageName] = Field(default_factory=list)
    active_task_id: str | None = None


class StageRunHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[StageRunInfo] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


class PromptLogUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class PromptLogValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PromptLogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    novel_id: str
    chapter_index: int
    stage: str
    attempt: int = Field(ge=1, default=1)
    timestamp: datetime
    provider: str
    model_name: str | None = None
    duration_ms: int = Field(ge=0, default=0)
    system_prompt: str
    user_prompt: str
    response: Any = None
    params: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    tokens: PromptLogUsageResponse = Field(default_factory=PromptLogUsageResponse)
    validation: PromptLogValidationResponse = Field(default_factory=PromptLogValidationResponse)


class PromptLogListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    chapter_idx: int = Field(ge=1)
    total: int = Field(default=0, ge=0)
    data: list[PromptLogEntryResponse] = Field(default_factory=list)


class PromptLogRetryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    chapter_idx: int = Field(ge=1)
    call_id: str
    stage: str
    status: Literal["queued"] = "queued"
    replay_mode: Literal["degraded"] = "degraded"
    message: str


class OpenApiExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_path: str
    schema_title: str
    schema_version: str


class SplitPreviewChapterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int = Field(ge=1)
    title: str
    content: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    char_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)


class SplitStagePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    stage: Literal["split"] = "split"
    status: Literal["paused"] = "paused"
    run_id: str
    run_seq: int = Field(ge=1)
    preview_token: str
    source_revision: str
    rules_version: str
    boundary_hash: str
    estimated_chapters: int = Field(ge=0)
    chapters: list[SplitPreviewChapterResponse] = Field(default_factory=list)
    created_at: datetime


class SplitStageConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    stage: Literal["split"] = "split"
    status: Literal["completed"] = "completed"
    preview_token: str
    chapter_count: int = Field(ge=0)
    run_id: str
    run_seq: int = Field(ge=1)
    completed_at: datetime


class ChapterRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)


class ChapterSplitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split_at_paragraph_index: int = Field(ge=1)


class ChapterAdjustResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    total: int = Field(ge=0)
    data: list[Chapter] = Field(default_factory=list)
