from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    STAGE_FAILED = "STAGE_FAILED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    CONFIG_INVALID = "CONFIG_INVALID"
    REGEX_INVALID = "REGEX_INVALID"
    REGEX_TIMEOUT = "REGEX_TIMEOUT"
    PREVIEW_STALE = "PREVIEW_STALE"
    QUALITY_GATE_BLOCKED = "QUALITY_GATE_BLOCKED"
    ANCHOR_MISMATCH = "ANCHOR_MISMATCH"


class ApiErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiErrorDetail


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loc: list[str | int] = Field(default_factory=list)
    msg: str
    type: str

