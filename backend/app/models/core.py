from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrEnum(str, Enum):
    """String-valued enum base class."""


class StageName(StrEnum):
    IMPORT = "import"
    SPLIT = "split"
    ANALYZE = "analyze"
    MARK = "mark"
    REWRITE = "rewrite"
    ASSEMBLE = "assemble"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    STALE = "stale"


class FileFormat(StrEnum):
    TXT = "txt"
    EPUB = "epub"


class ProviderType(StrEnum):
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"


class RewriteStrategy(StrEnum):
    EXPAND = "expand"
    REWRITE = "rewrite"
    CONDENSE = "condense"
    PRESERVE = "preserve"


class RewriteResultStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    ACCEPTED = "accepted"
    ACCEPTED_EDITED = "accepted_edited"
    REJECTED = "rejected"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class RewriteReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REGENERATE = "regenerate"
    EDIT = "edit"


class SentenceBoundaryKind(StrEnum):
    TERMINAL = "terminal"
    NEWLINE = "newline"
    FALLBACK = "fallback"


class WindowGuardrailLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HARD_FAIL = "hard_fail"


class WindowAttemptAction(StrEnum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    ROLLBACK_ORIGINAL = "rollback_original"


class NovelMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    original_filename: str
    file_format: FileFormat
    file_size: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    imported_at: datetime
    chapter_count: int = Field(default=0, ge=0)
    config_override_json: str | None = None


class Paragraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    char_count: int = Field(ge=0)

    @field_validator("end_offset")
    @classmethod
    def validate_offset_order(cls, value: int, info):
        start = info.data.get("start_offset")
        if start is not None and value < start:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return value


class Chapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    index: int = Field(ge=1)
    title: str
    content: str
    char_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    paragraphs: list[Paragraph] = Field(default_factory=list)

    @field_validator("end_offset")
    @classmethod
    def validate_range_order(cls, value: int, info):
        start = info.data.get("start_offset")
        if start is not None and value < start:
            raise ValueError("end_offset must be greater than or equal to start_offset")
        return value


class CharacterState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    emotion: str
    state: str
    role_in_chapter: str


class KeyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    event_type: str
    importance: int = Field(ge=1, le=5)
    paragraph_range: tuple[int, int]


class RewritePotential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expandable: bool
    rewritable: bool
    suggestion: str
    priority: int = Field(ge=1, le=5)


class SceneRuleHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger_condition: str
    evidence_text: str


class SceneSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_type: str
    paragraph_range: tuple[int, int]
    sentence_range: tuple[int, int] | None = None
    char_offset_range: tuple[int, int] | None = None
    rewrite_potential: RewritePotential
    rule_hits: list[SceneRuleHit] = Field(default_factory=list)

    @field_validator("paragraph_range")
    @classmethod
    def validate_paragraph_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        start, end = value
        if start < 1:
            raise ValueError("paragraph_range start must be greater than or equal to 1")
        if end < start:
            raise ValueError("paragraph_range end must be greater than or equal to start")
        return value

    @field_validator("sentence_range")
    @classmethod
    def validate_sentence_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return None
        start, end = value
        if start < 1:
            raise ValueError("sentence_range start must be greater than or equal to 1")
        if end < start:
            raise ValueError("sentence_range end must be greater than or equal to start")
        return value

    @field_validator("char_offset_range")
    @classmethod
    def validate_char_offset_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return None
        start, end = value
        if start < 0:
            raise ValueError("char_offset_range start must be greater than or equal to 0")
        if end <= start:
            raise ValueError("char_offset_range end must be greater than start")
        return value


class ChapterAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    characters: list[CharacterState] = Field(default_factory=list)
    key_events: list[KeyEvent] = Field(default_factory=list)
    scenes: list[SceneSegment] = Field(default_factory=list)
    location: str
    tone: str


class RewriteAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_start_hash: str
    paragraph_end_hash: str
    range_text_hash: str
    context_window_hash: str
    paragraph_count_snapshot: int = Field(ge=0)


class SentenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence_index: int = Field(ge=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    paragraph_index: int = Field(ge=1)
    boundary_kind: SentenceBoundaryKind = SentenceBoundaryKind.FALLBACK

    @field_validator("end_offset")
    @classmethod
    def validate_sentence_offsets(cls, value: int, info):
        start = info.data.get("start_offset")
        if start is not None and value <= start:
            raise ValueError("sentence end_offset must be greater than start_offset")
        return value


class RewriteWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_id: str = Field(default_factory=lambda: str(uuid4()))
    segment_id: str
    chapter_index: int = Field(ge=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    hit_sentence_range: tuple[int, int] | None = None
    context_sentence_range: tuple[int, int] | None = None
    target_chars: int = Field(ge=0)
    target_chars_min: int = Field(ge=0)
    target_chars_max: int = Field(ge=0)
    source_fingerprint: str | None = None
    plan_version: str | None = None

    @field_validator("end_offset")
    @classmethod
    def validate_window_offsets(cls, value: int, info):
        start = info.data.get("start_offset")
        if start is not None and value <= start:
            raise ValueError("window end_offset must be greater than start_offset")
        return value

    @field_validator("target_chars_max")
    @classmethod
    def validate_window_target_range(cls, value: int, info):
        target_min = info.data.get("target_chars_min")
        if target_min is not None and value < target_min:
            raise ValueError("window target_chars_max must be greater than or equal to target_chars_min")
        return value


class WindowGuardrail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: WindowGuardrailLevel = WindowGuardrailLevel.INFO
    codes: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class WindowAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_id: str
    attempt_seq: int = Field(ge=1)
    run_seq: int | None = Field(default=None, ge=1)
    provider_id: str | None = None
    model_name: str | None = None
    finish_reason: str | None = None
    raw_response_ref: str | None = None
    guardrail: WindowGuardrail | None = None
    action: WindowAttemptAction = WindowAttemptAction.ACCEPTED


class RewriteSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(default_factory=lambda: str(uuid4()))
    paragraph_range: tuple[int, int]
    sentence_range: tuple[int, int] | None = None
    char_offset_range: tuple[int, int] | None = None
    anchor: RewriteAnchor
    scene_type: str
    original_chars: int = Field(ge=0)
    strategy: RewriteStrategy
    target_ratio: float = Field(gt=0)
    target_chars: int = Field(ge=0)
    target_chars_min: int = Field(ge=0)
    target_chars_max: int = Field(ge=0)
    rewrite_windows: list[RewriteWindow] = Field(default_factory=list)
    source_fingerprint: str | None = None
    plan_version: str | None = None
    suggestion: str
    source: Literal["auto", "manual"]
    confirmed: bool = False

    @field_validator("segment_id")
    @classmethod
    def validate_segment_id_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise ValueError("segment_id must be a valid UUID") from exc
        if parsed.version != 4:
            raise ValueError("segment_id must be a UUID v4")
        return value

    @field_validator("paragraph_range")
    @classmethod
    def validate_paragraph_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        start, end = value
        if start < 1:
            raise ValueError("paragraph_range start must be greater than or equal to 1")
        if end < start:
            raise ValueError("paragraph_range end must be greater than or equal to start")
        return value

    @field_validator("sentence_range")
    @classmethod
    def validate_sentence_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return None
        start, end = value
        if start < 1:
            raise ValueError("sentence_range start must be greater than or equal to 1")
        if end < start:
            raise ValueError("sentence_range end must be greater than or equal to start")
        return value

    @field_validator("char_offset_range")
    @classmethod
    def validate_char_offset_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is None:
            return None
        start, end = value
        if start < 0:
            raise ValueError("char_offset_range start must be greater than or equal to 0")
        if end <= start:
            raise ValueError("char_offset_range end must be greater than start")
        return value

    @field_validator("target_chars_max")
    @classmethod
    def validate_target_range(cls, value: int, info):
        target_min = info.data.get("target_chars_min")
        if target_min is not None and value < target_min:
            raise ValueError("target_chars_max must be greater than or equal to target_chars_min")
        return value


class RewriteChapterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_index: int = Field(ge=1)
    sentence_spans: list[SentenceSpan] = Field(default_factory=list)
    sentence_splitter_version: str | None = None
    window_planner_version: str | None = None
    plan_version: str | None = None
    source_fingerprint: str | None = None
    segments: list[RewriteSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_non_overlapping_ranges(self) -> "RewriteChapterPlan":
        def _segment_sort_key(segment: RewriteSegment) -> tuple[int, int, int, int]:
            if segment.char_offset_range is not None:
                start, end = segment.char_offset_range
                return (0, start, end, segment.paragraph_range[0])
            start, end = segment.paragraph_range
            return (1, start, end, start)

        sorted_segments = sorted(self.segments, key=_segment_sort_key)
        for index in range(1, len(sorted_segments)):
            prev = sorted_segments[index - 1]
            cur = sorted_segments[index]
            if prev.char_offset_range is not None and cur.char_offset_range is not None:
                prev_start, prev_end = prev.char_offset_range
                cur_start, cur_end = cur.char_offset_range
                if cur_start < prev_end:
                    raise ValueError(
                        "segment char_offset_range must not overlap "
                        f"(previous={prev_start}-{prev_end}, current={cur_start}-{cur_end})"
                    )
                continue

            prev_start, prev_end = prev.paragraph_range
            cur_start, cur_end = cur.paragraph_range
            if cur_start <= prev_end:
                raise ValueError(
                    "segment paragraph_range must not overlap "
                    f"(previous={prev_start}-{prev_end}, current={cur_start}-{cur_end})"
                )
        return self


class RewritePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_id: str
    created_at: datetime
    sentence_splitter_version: str | None = None
    window_planner_version: str | None = None
    plan_version: str | None = None
    source_fingerprint: str | None = None
    total_marked: int = Field(ge=0)
    estimated_llm_calls: int = Field(ge=0)
    estimated_added_chars: int = Field(ge=0)
    chapters: list[RewriteChapterPlan] = Field(default_factory=list)


class RewriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    chapter_index: int = Field(ge=1)
    paragraph_range: tuple[int, int]
    scene_type: str | None = None
    suggestion: str | None = None
    target_ratio: float | None = None
    target_chars: int | None = None
    target_chars_min: int | None = None
    target_chars_max: int | None = None
    char_offset_range: tuple[int, int] | None = None
    rewrite_windows: list[RewriteWindow] = Field(default_factory=list)
    window_attempts: list[WindowAttempt] = Field(default_factory=list)
    completion_kind: Literal["normal", "noop"] = "normal"
    reason_code: str | None = None
    has_warnings: bool = False
    warning_count: int = Field(default=0, ge=0)
    warning_codes: list[str] = Field(default_factory=list)
    anchor_verified: bool = False
    strategy: RewriteStrategy
    original_text: str
    rewritten_text: str
    original_chars: int = Field(ge=0)
    rewritten_chars: int = Field(ge=0)
    actual_chars: int | None = None
    status: RewriteResultStatus
    attempts: int = Field(ge=0)
    provider_used: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    provider_raw_response: dict[str, Any] | None = None
    validation_details: dict[str, Any] | None = None
    manual_edited_text: str | None = None
    rollback_snapshot: dict[str, Any] | None = None
    audit_trail: list["RewriteAuditEntry"] = Field(default_factory=list)


class RewriteAuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RewriteReviewAction
    from_status: RewriteResultStatus
    to_status: RewriteResultStatus
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)
    note: str | None = None
    previous_rewritten_text: str | None = None
    manual_edited_text: str | None = None
    rollback_snapshot: dict[str, Any] | None = None


class RewriteWindowModeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    guardrail_enabled: bool = True
    audit_enabled: bool = True
    source: str | None = None


class StageConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    provider_name: str | None = None
    provider_type: ProviderType | None = None
    model_name: str | None = None
    base_url: str | None = None
    global_prompt_version: str | None = None
    scene_rules_hash: str | None = None
    rewrite_rules_hash: str | None = None
    generation_params: dict[str, Any] = Field(default_factory=dict)
    rewrite_window_mode: RewriteWindowModeSnapshot = Field(default_factory=RewriteWindowModeSnapshot)
    captured_at: datetime = Field(default_factory=datetime.utcnow)


class StageRunInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_seq: int = Field(ge=1)
    stage: StageName
    status: StageStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    run_idempotency_key: str | None = None
    warnings_count: int = Field(default=0, ge=0)
    chapters_total: int = Field(default=0, ge=0)
    chapters_done: int = Field(default=0, ge=0)
    config_snapshot: StageConfigSnapshot | None = None
    artifact_path: str | None = None
    is_latest: bool = True
