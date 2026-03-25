from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
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
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> ErrorDetail:
        return ErrorDetail(code=self.code, message=self.message, details=self.details)


def error_payload(code: ErrorCode, message: str, **details: Any) -> dict[str, Any]:
    return {
        "error": {
            "code": code.value,
            "message": message,
            "details": details,
        }
    }
