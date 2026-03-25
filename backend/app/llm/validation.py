from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError
from rapidfuzz.distance import Levenshtein

from backend.app.models.core import ChapterAnalysis


@dataclass(slots=True)
class AnalyzeValidationResult:
    passed: bool
    parsed: ChapterAnalysis | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RewriteValidationResult:
    passed: bool
    similarity: float = 0.0
    original_chars: int = 0
    rewritten_chars: int = 0
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _coerce_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return json.loads(payload)
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json")
    return payload


def _normalize_similarity_score(value: float) -> float:
    if value > 1.0:
        return value / 100.0
    return value


def _build_error(
    *,
    passed: bool,
    error_code: str | None = None,
    error_message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "passed": passed,
        "error_code": error_code,
        "error_message": error_message,
        "details": details or {},
    }


def validate_analyze_output(
    payload: Any,
    *,
    summary_min_chars: int | None = None,
    summary_max_chars: int | None = None,
) -> AnalyzeValidationResult:
    schema = ChapterAnalysis.model_json_schema()

    try:
        resolved = _coerce_payload(payload)
        Draft202012Validator(schema).validate(resolved)
        parsed = ChapterAnalysis.model_validate(resolved)
    except (json.JSONDecodeError, ValidationError) as exc:
        return AnalyzeValidationResult(
            **_build_error(
                passed=False,
                error_code="ANALYZE_SCHEMA_INVALID",
                error_message="Analyze output failed JSON schema validation",
                details={"error": str(exc)},
            ),
            parsed=None,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback for schema validation errors
        return AnalyzeValidationResult(
            **_build_error(
                passed=False,
                error_code="ANALYZE_SCHEMA_INVALID",
                error_message="Analyze output failed JSON schema validation",
                details={"error": str(exc)},
            ),
            parsed=None,
        )

    summary_chars = len(parsed.summary.strip())
    if summary_min_chars is not None and summary_chars < summary_min_chars:
        return AnalyzeValidationResult(
            **_build_error(
                passed=False,
                error_code="ANALYZE_SUMMARY_TOO_SHORT",
                error_message="Analyze summary is shorter than expected",
                details={"summary_chars": summary_chars, "summary_min_chars": summary_min_chars},
            ),
            parsed=parsed,
        )
    if summary_max_chars is not None and summary_chars > summary_max_chars:
        return AnalyzeValidationResult(
            **_build_error(
                passed=False,
                error_code="ANALYZE_SUMMARY_TOO_LONG",
                error_message="Analyze summary is longer than expected",
                details={"summary_chars": summary_chars, "summary_max_chars": summary_max_chars},
            ),
            parsed=parsed,
        )

    return AnalyzeValidationResult(
        passed=True,
        parsed=parsed,
        details={"summary_chars": summary_chars, "schema_name": "ChapterAnalysis"},
    )


def _similarity_score(original_text: str, rewritten_text: str) -> float:
    score = Levenshtein.normalized_similarity(original_text, rewritten_text)
    return _normalize_similarity_score(float(score))


def validate_rewrite_output(
    original_text: str,
    rewritten_text: str,
    *,
    target_chars: int | None = None,
    target_chars_min: int,
    target_chars_max: int,
    similarity_threshold: float = 0.90,
) -> RewriteValidationResult:
    original_chars = len(original_text)
    rewritten_chars = len(rewritten_text.strip())

    if rewritten_chars == 0:
        return RewriteValidationResult(
            passed=False,
            similarity=0.0,
            original_chars=original_chars,
            rewritten_chars=rewritten_chars,
            error_code="REWRITE_EMPTY",
            error_message="Rewrite output is empty",
            details={"actual_chars": rewritten_chars},
        )

    if rewritten_chars < target_chars_min or rewritten_chars > target_chars_max:
        return RewriteValidationResult(
            passed=False,
            similarity=0.0,
            original_chars=original_chars,
            rewritten_chars=rewritten_chars,
            error_code="REWRITE_LENGTH_OUT_OF_RANGE",
            error_message="Rewrite output length is outside the expected range",
            details={
                "target_chars": target_chars,
                "target_chars_min": target_chars_min,
                "target_chars_max": target_chars_max,
                "actual_chars": rewritten_chars,
                "rewritten_chars": rewritten_chars,
            },
        )

    similarity = _similarity_score(original_text, rewritten_text)
    if similarity >= similarity_threshold:
        return RewriteValidationResult(
            passed=False,
            similarity=similarity,
            original_chars=original_chars,
            rewritten_chars=rewritten_chars,
            error_code="REWRITE_TOO_SIMILAR",
            error_message="Rewrite output is too similar to the original text",
            details={"similarity_threshold": similarity_threshold},
        )

    return RewriteValidationResult(
        passed=True,
        similarity=similarity,
        original_chars=original_chars,
        rewritten_chars=rewritten_chars,
        details={"similarity_threshold": similarity_threshold},
    )


def validate_model_instance(model: BaseModel) -> bool:
    """Return whether a Pydantic model instance can be serialized cleanly."""
    try:
        model.model_dump(mode="json")
    except Exception:
        return False
    return True
