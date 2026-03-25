from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.core import (
    Chapter,
    ChapterAnalysis,
    FileFormat,
    NovelMeta,
    ProviderType,
    StageName,
    StageRunInfo,
    StageStatus,
)


class ImportNovelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    meta: NovelMeta
    stage_runs: list[StageRunInfo] = Field(default_factory=list)


class NovelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[NovelDetailResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1, default=1)
    per_page: int = Field(ge=1, default=20)


class NovelDetailResponse(NovelMeta):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = None
    active_task_id: str | None = None
    pipeline_status: dict[StageName, StageRunInfo] = Field(default_factory=dict)


class ChapterListItem(Chapter):
    model_config = ConfigDict(extra="forbid")

    status: StageStatus = StageStatus.PENDING
    stages: dict[StageName, StageStatus] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_stage_statuses(self) -> "ChapterListItem":
        normalized = {stage: status for stage, status in self.stages.items()}
        for stage in StageName:
            normalized.setdefault(stage, StageStatus.PENDING)
        self.stages = normalized
        return self


class ChapterListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    total: int = Field(ge=0)
    data: list[ChapterListItem] = Field(default_factory=list)


class ChapterUpdateAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: ChapterAnalysis


class ChapterUpdateMarksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marks: list[dict[str, Any]]


class StageActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_idempotency_key: str | None = None
    force: bool = False
    split_rule_id: str | None = None
    provider_id: str | None = None
    rewrite_target_chars: int | None = Field(default=None, ge=0)
    rewrite_target_added_chars: int | None = Field(default=None, ge=0)
    rewrite_window_mode_enabled: bool | None = None
    rewrite_window_guardrail_enabled: bool | None = None
    rewrite_window_audit_enabled: bool | None = None


class StageChapterRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    rewrite_target_chars: int | None = Field(default=None, ge=0)
    rewrite_target_added_chars: int | None = Field(default=None, ge=0)
    rewrite_window_mode_enabled: bool | None = None
    rewrite_window_guardrail_enabled: bool | None = None
    rewrite_window_audit_enabled: bool | None = None


class StageActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    task_id: str
    stage: StageName
    run: StageRunInfo


class SplitConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str


class SplitRuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str
    pattern: str
    priority: int = Field(ge=0, default=0)
    enabled: bool = True
    builtin: bool = False


class SplitRulesPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    source_revision: str | None = None
    rules_version: str | None = None
    sample_size: int = Field(ge=1, default=10)
    selected_rule_id: str | None = None
    builtin_rules: list[SplitRuleSpec] | None = None
    custom_rules: list[SplitRuleSpec] | None = None


class SplitMatchedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_index: int = Field(ge=1)
    line_number: int = Field(ge=1)
    text: str
    rule_id: str | None = None
    rule_name: str | None = None


class SplitRulesPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str
    novel_id: str
    source_revision: str
    rules_version: str
    preview_valid: bool = True
    failure_reason: str | None = None
    matched_count: int = Field(ge=0, default=0)
    estimated_chapters: int = Field(ge=0)
    matched_lines: list[SplitMatchedLine] = Field(default_factory=list)
    boundary_hash: str
    chapters: list[Chapter] = Field(default_factory=list)


class SplitRulesConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    builtin_rules: list[SplitRuleSpec] = Field(default_factory=list)
    custom_rules: list[SplitRuleSpec] = Field(default_factory=list)


class SplitRulesConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules_version: str
    builtin_rules: list[SplitRuleSpec] = Field(default_factory=list)
    custom_rules: list[SplitRuleSpec] = Field(default_factory=list)


class SplitRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    pattern: str
    priority: int = Field(ge=0, default=0)
    enabled: bool = True


class SplitRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    pattern: str | None = None
    priority: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class SplitRulesConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_token: str
    novel_id: str
    source_revision: str
    rules_version: str
    boundary_hash: str
    preview_valid: bool = True
    chapter_count: int = Field(ge=0)
    chapters: list[Chapter] = Field(default_factory=list)


class ProviderListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[dict[str, Any]]
    total: int = Field(ge=0)


class CreateProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider_type: ProviderType
    api_key: str
    base_url: str
    model_name: str
    temperature: float = Field(ge=0, le=2, default=0.7)
    max_tokens: int = Field(ge=1, default=4000)
    top_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    rpm_limit: int = Field(ge=1, default=60)
    tpm_limit: int = Field(ge=1, default=100000)


class ProviderTestConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    provider_type: ProviderType | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "ProviderTestConnectionRequest":
        if self.provider_id:
            return self
        if not self.provider_type or not self.api_key or not self.base_url or not self.model_name:
            raise ValueError("Either provider_id or provider_type/api_key/base_url/model_name must be provided")
        return self


class ModelFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str
    base_url: str
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE


class ModelFetchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list)
    fetched_at: datetime
    source: Literal["draft", "saved"]
