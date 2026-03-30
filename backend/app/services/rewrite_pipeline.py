from __future__ import annotations

import asyncio
import json
import re
from bisect import bisect_left, bisect_right
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.app.core.errors import AppError, ErrorCode
from backend.app.llm.client import complete as default_complete
from backend.app.llm.generation import build_generation_params
from backend.app.llm.interface import CompletionRequest, CompletionResponse, GenerationParams
from backend.app.llm.prompting import PromptTemplateRegistry, StagePromptBundle, build_stage_prompts
from backend.app.llm.validation import RewriteValidationResult, validate_rewrite_output
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    ProviderType,
    RewriteAnchor,
    RewriteChapterPlan,
    RewriteResult,
    RewriteResultStatus,
    RewriteSegment,
    RewriteWindow,
    WindowAttempt,
    WindowAttemptAction,
    WindowGuardrail,
    WindowGuardrailLevel,
)
from backend.app.services.config_store import RewriteRule
from backend.app.services.marking import build_anchor, build_chapter_mark_plan

PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n\s*){2,}")
DEFAULT_CONTEXT_WINDOW_SIZE = 1
DEFAULT_CONTEXT_CHARS = 300
DEFAULT_AUTO_SPLIT_TRIGGER_CHARS = 6_000
DEFAULT_AUTO_SPLIT_MIN_PART_CHARS = 700
DEFAULT_AUTO_SPLIT_MAX_PART_CHARS = 2_800
DEFAULT_AUTO_SPLIT_OUTPUT_BUDGET_RATIO = 0.65
AUTO_SPLIT_SENTENCE_TERMINATORS = {"。", "！", "？", "!", "?", "；", ";", "…"}
AUTO_SPLIT_SENTENCE_CLOSERS = {'"', "'", "”", "’", "）", ")", "】", "]", "》", "」", "』"}
DEFAULT_WINDOW_MAX_RETRY = 2
SEVERE_LENGTH_UNDERSHOOT_RATIO = 0.55
SEVERE_LENGTH_OVERSHOOT_RATIO = 1.8
MILD_LENGTH_UNDERSHOOT_RATIO = 0.85
MILD_LENGTH_OVERSHOOT_RATIO = 1.3
FRAGMENT_START_PUNCT = "，、。；：？！,.;:!?）】》」』”’)]}"
FRAGMENT_END_CONNECTOR = "，、；：,:;—-…"
_REWRITE_META_PREFIX_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*与原文不同(?:[，,:：]\s*|\s+)"), ""),
    (re.compile(r"^\s*不同于原文(?:[，,:：]\s*|\s+)"), ""),
    (re.compile(r"^\s*改写后(?:如下)?(?:[，,:：]\s*|\s+)"), ""),
    (re.compile(r"^\s*重写后(?:如下)?(?:[，,:：]\s*|\s+)"), ""),
)
_REWRITE_META_STANDALONE_LINE_RE = re.compile(
    r"^\s*(?:以下(?:为|是)?改写(?:内容|版本)?|改写(?:内容|版本)|说明|注[:：]?|与原文不同|不同于原文)\s*$"
)

SubmitFn = Callable[["RewriteSegmentRequest"], Awaitable[RewriteResult]]


@dataclass(slots=True)
class RewriteAnchorValidationResult:
    """Validation outcome for a rewrite anchor comparison."""

    passed: bool
    expected_anchor: RewriteAnchor | None = None
    current_anchor: RewriteAnchor | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RewriteSegmentRequest:
    """Execution context for a single rewrite segment."""

    novel_id: str
    task_id: str
    chapter: Chapter
    analysis: ChapterAnalysis
    segment: RewriteSegment
    rewrite_rules: Sequence[RewriteRule] = field(default_factory=tuple)
    global_prompt: str = ""
    rewrite_general_guidance: str = ""
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    generation: GenerationParams | Mapping[str, Any] | None = None
    prompt_registry: PromptTemplateRegistry | None = None
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE
    context_chars: int = DEFAULT_CONTEXT_CHARS
    stage_run_seq: int | None = None
    window_mode_enabled: bool = True
    window_guardrail_enabled: bool = True
    window_audit_enabled: bool = True


@dataclass(slots=True)
class RewriteAutoSplitPart:
    index: int
    source_text: str
    preceding_text: str
    following_text: str
    target_chars: int
    target_chars_min: int
    target_chars_max: int


@dataclass(slots=True)
class RewriteAutoSplitPlan:
    parts: list[RewriteAutoSplitPart]
    trigger_reason: str
    max_tokens: int | None
    safe_output_chars: int
    max_original_chars_per_part: int
    min_original_chars_per_part: int


@dataclass(slots=True)
class GuardrailEvaluation:
    level: WindowGuardrailLevel
    codes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_hard_fail(self) -> bool:
        return self.level == WindowGuardrailLevel.HARD_FAIL and bool(self.codes)


@dataclass(slots=True)
class RewriteChapterRequest:
    """Batch execution context for all rewrite segments in one chapter."""

    novel_id: str
    task_id: str
    chapter: Chapter
    analysis: ChapterAnalysis
    rewrite_rules: Sequence[RewriteRule] = field(default_factory=tuple)
    global_prompt: str = ""
    rewrite_general_guidance: str = ""
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    generation: GenerationParams | Mapping[str, Any] | None = None
    prompt_registry: PromptTemplateRegistry | None = None
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE
    context_chars: int = DEFAULT_CONTEXT_CHARS
    stage_run_seq: int | None = None
    window_mode_enabled: bool = True
    window_guardrail_enabled: bool = True
    window_audit_enabled: bool = True


def _split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in PARAGRAPH_SPLIT_RE.split(text) if part.strip()]


def _extract_segment_text_by_paragraph_range(chapter: Chapter, segment: RewriteSegment) -> tuple[list[str], str, str, str]:
    paragraphs = _split_paragraphs(chapter.content)
    start, end = segment.paragraph_range
    if start < 1 or end < start:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid paragraph_range for rewrite segment",
            details={"paragraph_range": segment.paragraph_range, "chapter_index": chapter.index},
        )
    if end > len(paragraphs):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "rewrite segment paragraph_range exceeds the chapter paragraph count",
            details={
                "paragraph_range": segment.paragraph_range,
                "paragraph_count": len(paragraphs),
                "chapter_index": chapter.index,
            },
        )

    segment_paragraphs = paragraphs[start - 1 : end]
    original_text = "\n\n".join(segment_paragraphs)
    preceding_text = "\n\n".join(paragraphs[: start - 1])
    following_text = "\n\n".join(paragraphs[end:])
    return paragraphs, original_text, preceding_text, following_text


def _slice_with_char_offset_range(
    chapter: Chapter,
    segment: RewriteSegment,
) -> tuple[tuple[str, str, str], dict[str, Any] | None]:
    char_offset_range = segment.char_offset_range
    source = "char_offset_range"
    if segment.rewrite_windows:
        sorted_windows = sorted(segment.rewrite_windows, key=lambda item: (item.start_offset, item.end_offset))
        first = sorted_windows[0]
        last = sorted_windows[-1]
        char_offset_range = (first.start_offset, last.end_offset)
        source = "rewrite_windows"
    if char_offset_range is None:
        return ("", "", ""), {
            "reason": "missing_char_offset_range",
            "chapter_index": chapter.index,
            "paragraph_range": list(segment.paragraph_range),
        }

    start, end = char_offset_range
    if start < 0 or end <= start:
        return ("", "", ""), {
            "reason": "invalid_char_offset_range",
            "chapter_index": chapter.index,
            "char_offset_range": list(char_offset_range),
            "content_length": len(chapter.content),
        }
    if end > len(chapter.content):
        return ("", "", ""), {
            "reason": "char_offset_range_out_of_bounds",
            "chapter_index": chapter.index,
            "char_offset_range": list(char_offset_range),
            "content_length": len(chapter.content),
        }

    original_text = chapter.content[start:end]
    if not original_text.strip():
        return ("", "", ""), {
            "reason": "empty_slice",
            "chapter_index": chapter.index,
            "char_offset_range": list(char_offset_range),
            "content_length": len(chapter.content),
        }
    return (original_text, chapter.content[:start], chapter.content[end:]), {"source": source}


def _extract_segment_text(chapter: Chapter, segment: RewriteSegment) -> tuple[list[str], str, str, str, dict[str, Any]]:
    extracted, fallback = _slice_with_char_offset_range(chapter, segment)
    if fallback is not None and fallback.get("source") in {"char_offset_range", "rewrite_windows"}:
        original_text, preceding_text, following_text = extracted
        return _split_paragraphs(chapter.content), original_text, preceding_text, following_text, {"source": fallback["source"]}

    try:
        paragraphs, original_text, preceding_text, following_text = _extract_segment_text_by_paragraph_range(chapter, segment)
    except AppError as exc:
        raise AppError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            details={
                **exc.details,
                "char_offset_fallback": fallback,
            },
        ) from exc
    return (
        paragraphs,
        original_text,
        preceding_text,
        following_text,
        {
            "source": "paragraph_range",
            "fallback_reason": fallback,
        },
    )


def extract_segment_source_text(chapter: Chapter, segment: RewriteSegment) -> tuple[str, dict[str, Any]]:
    _, original_text, _, _, slice_details = _extract_segment_text(chapter, segment)
    return original_text, slice_details


def _trim_context(text: str, *, limit_chars: int, tail: bool) -> str:
    if limit_chars <= 0 or not text:
        return ""
    if len(text) <= limit_chars:
        return text
    return text[-limit_chars:] if tail else text[:limit_chars]


def _json_detail_text(details: Mapping[str, Any] | None) -> str | None:
    if not details:
        return None
    return json.dumps(details, ensure_ascii=False)


def _with_slice_details(
    validation_details: Mapping[str, Any] | None,
    *,
    slice_details: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(validation_details or {})
    merged["source_slice"] = dict(slice_details)
    return merged


def _distribute_target_chars(weights: Sequence[int], target_total: int) -> list[int]:
    if not weights:
        return []

    safe_weights = [max(1, int(weight)) for weight in weights]
    total_weight = sum(safe_weights)
    if total_weight <= 0:
        return [max(1, int(target_total // len(safe_weights) or 1)) for _ in safe_weights]

    target_total = max(len(safe_weights), int(target_total))
    distributed = [max(1, int(round(target_total * weight / total_weight))) for weight in safe_weights]
    delta = target_total - sum(distributed)
    if delta == 0:
        return distributed

    order = sorted(range(len(safe_weights)), key=lambda idx: safe_weights[idx], reverse=(delta > 0))
    cursor = 0
    while delta != 0 and order:
        index = order[cursor % len(order)]
        if delta > 0:
            distributed[index] += 1
            delta -= 1
        else:
            if distributed[index] > 1:
                distributed[index] -= 1
                delta += 1
        cursor += 1
        if cursor > len(order) * max(1, abs(delta) + 1):
            break
    return distributed


def _sentence_boundary_indices(text: str) -> list[int]:
    boundaries: set[int] = set()
    length = len(text)
    index = 0

    while index < length:
        char = text[index]
        if char not in AUTO_SPLIT_SENTENCE_TERMINATORS:
            index += 1
            continue

        end = index + 1
        while end < length and text[end] in AUTO_SPLIT_SENTENCE_TERMINATORS:
            end += 1
        while end < length and text[end] in AUTO_SPLIT_SENTENCE_CLOSERS:
            end += 1
        boundaries.add(end)
        index = end

    for match in PARAGRAPH_SPLIT_RE.finditer(text):
        boundaries.add(match.end())
    for match in re.finditer(r"\n+", text):
        boundaries.add(match.end())

    boundaries.add(length)
    return sorted(boundary for boundary in boundaries if 0 < boundary <= length)


def _chunk_ranges_by_boundaries(
    text: str,
    *,
    max_chars: int,
    min_chars: int,
) -> list[tuple[int, int]]:
    length = len(text)
    if length == 0:
        return []
    if length <= max_chars:
        return [(0, length)]

    boundaries = _sentence_boundary_indices(text)
    if not boundaries:
        return [(0, length)]

    ranges: list[tuple[int, int]] = []
    start = 0
    safe_max = max(1, int(max_chars))
    safe_min = max(1, min(int(min_chars), safe_max))

    while start < length:
        remaining = length - start
        if remaining <= safe_max:
            ranges.append((start, length))
            break

        lower = min(length, start + safe_min)
        upper = min(length, start + safe_max)
        left = bisect_left(boundaries, lower)
        right = bisect_right(boundaries, upper)

        if left < right:
            end = boundaries[right - 1]
        else:
            after = bisect_right(boundaries, upper)
            end = boundaries[after] if after < len(boundaries) else length
            if end - start > int(safe_max * 1.35):
                end = upper

        if end <= start:
            end = min(length, start + safe_max)
        ranges.append((start, end))
        start = end

    return ranges


def _build_auto_split_plan(
    *,
    original_text: str,
    preceding_text: str,
    following_text: str,
    segment: RewriteSegment,
    generation: GenerationParams,
) -> RewriteAutoSplitPlan | None:
    source = original_text.strip()
    original_chars = len(source)
    if original_chars == 0:
        return None

    max_tokens = int(generation.max_tokens or 0) or None
    safe_output_chars = max(
        DEFAULT_AUTO_SPLIT_MIN_PART_CHARS,
        int((max_tokens or DEFAULT_AUTO_SPLIT_MAX_PART_CHARS) * DEFAULT_AUTO_SPLIT_OUTPUT_BUDGET_RATIO),
    )

    target_ratio = max(0.8, float(segment.target_ratio or 1.0))
    max_original_chars_per_part = max(
        DEFAULT_AUTO_SPLIT_MIN_PART_CHARS,
        min(DEFAULT_AUTO_SPLIT_MAX_PART_CHARS, int(safe_output_chars / target_ratio)),
    )
    min_original_chars_per_part = max(
        300,
        min(max_original_chars_per_part, int(max_original_chars_per_part * 0.55)),
    )

    trigger_by_length = original_chars >= DEFAULT_AUTO_SPLIT_TRIGGER_CHARS
    trigger_by_budget = bool(max_tokens and (segment.target_chars_max or 0) > int(safe_output_chars * 1.1))
    needs_split = original_chars > max_original_chars_per_part
    if not (needs_split or trigger_by_length or trigger_by_budget):
        return None

    ranges = _chunk_ranges_by_boundaries(
        source,
        max_chars=max_original_chars_per_part,
        min_chars=min_original_chars_per_part,
    )
    chunk_texts = [source[start:end].strip() for start, end in ranges if source[start:end].strip()]
    if len(chunk_texts) <= 1:
        return None

    weights = [max(1, len(chunk)) for chunk in chunk_texts]
    default_total_target = max(1, int(round(sum(weights) * float(segment.target_ratio or 1.0))))
    total_target_chars = max(1, int(segment.target_chars or default_total_target))
    distributed_targets = _distribute_target_chars(weights, total_target_chars)

    parts: list[RewriteAutoSplitPart] = []
    part_total = len(chunk_texts)
    for idx, (chunk, target_chars) in enumerate(zip(chunk_texts, distributed_targets), start=1):
        buffer = max(1, int(round(target_chars * 0.12)))
        parts.append(
            RewriteAutoSplitPart(
                index=idx,
                source_text=chunk,
                preceding_text=preceding_text if idx == 1 else chunk_texts[idx - 2],
                following_text=following_text if idx == part_total else chunk_texts[idx],
                target_chars=target_chars,
                target_chars_min=max(1, target_chars - buffer),
                target_chars_max=target_chars + buffer,
            )
        )

    trigger_reason = "segment_chars_exceed_limit" if trigger_by_length or needs_split else "target_chars_exceed_budget"
    return RewriteAutoSplitPlan(
        parts=parts,
        trigger_reason=trigger_reason,
        max_tokens=max_tokens,
        safe_output_chars=safe_output_chars,
        max_original_chars_per_part=max_original_chars_per_part,
        min_original_chars_per_part=min_original_chars_per_part,
    )


def _part_rewrite_guidance(base_guidance: str, *, part_index: int, part_total: int) -> str:
    split_hint = (
        f"【自动拆分改写子段 {part_index}/{part_total}】"
        "只改写当前子段文本，保持与上下文连贯，不要重复或补写其他子段内容。"
    )
    trimmed = base_guidance.strip()
    return f"{trimmed}\n\n{split_hint}" if trimmed else split_hint


def _completion_finish_reason(raw_response: Mapping[str, Any] | None) -> str | None:
    if not isinstance(raw_response, Mapping):
        return None
    choices = raw_response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    reason = first.get("finish_reason")
    if reason is None:
        return None
    text = str(reason).strip()
    return text or None


def _is_length_validation_undershoot(validation: RewriteValidationResult) -> bool:
    if validation.error_code != "REWRITE_LENGTH_OUT_OF_RANGE":
        return False
    details = validation.details if isinstance(validation.details, Mapping) else {}
    try:
        actual_chars = int(details.get("actual_chars") or details.get("rewritten_chars") or 0)
        target_chars_min = int(details.get("target_chars_min") or 0)
    except (TypeError, ValueError):
        return False
    return target_chars_min > 0 and actual_chars < target_chars_min


def _response_request_id(raw_response: Mapping[str, Any] | None) -> str | None:
    if not isinstance(raw_response, Mapping):
        return None
    request_id = raw_response.get("request_id") or raw_response.get("id")
    if request_id is None:
        return None
    text = str(request_id).strip()
    return text or None


def _looks_like_start_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return stripped[0] in FRAGMENT_START_PUNCT


def _looks_like_end_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return stripped[-1] in FRAGMENT_END_CONNECTOR


def _length_outlier_level(
    *,
    actual_chars: int,
    target_min: int | None,
    target_max: int | None,
) -> tuple[WindowGuardrailLevel | None, str | None]:
    if actual_chars <= 0:
        return WindowGuardrailLevel.HARD_FAIL, "REWRITE_EMPTY"
    minimum = int(target_min or 0)
    maximum = int(target_max or 0)
    if minimum <= 0 and maximum <= 0:
        return None, None
    if minimum > 0 and actual_chars < max(1, int(minimum * SEVERE_LENGTH_UNDERSHOOT_RATIO)):
        return WindowGuardrailLevel.HARD_FAIL, "REWRITE_LENGTH_SEVERE_OUTLIER"
    # Over-length outputs should not be treated as hard failures; keep them as warnings
    # so the rewritten content can still be kept for review/acceptance.
    if maximum > 0 and actual_chars > int(maximum * SEVERE_LENGTH_OVERSHOOT_RATIO):
        return WindowGuardrailLevel.WARNING, "REWRITE_LENGTH_SEVERE_OUTLIER"
    if minimum > 0 and actual_chars < max(1, int(minimum * MILD_LENGTH_UNDERSHOOT_RATIO)):
        return WindowGuardrailLevel.WARNING, "REWRITE_LENGTH_MILD_OUTLIER"
    if maximum > 0 and actual_chars > int(maximum * MILD_LENGTH_OVERSHOOT_RATIO):
        return WindowGuardrailLevel.WARNING, "REWRITE_LENGTH_MILD_OUTLIER"
    return None, None


def _evaluate_guardrail(
    *,
    rewritten_text: str,
    validation: RewriteValidationResult,
    finish_reason: str | None,
    window_range_valid: bool,
) -> GuardrailEvaluation:
    stripped = rewritten_text.strip()
    codes: list[str] = []
    details: dict[str, Any] = {}
    hard = False

    if not window_range_valid:
        hard = True
        codes.append("REWRITE_WINDOW_OUT_OF_BOUNDS")
    if not stripped:
        hard = True
        codes.append("REWRITE_EMPTY")
    if stripped and _looks_like_start_fragment(stripped):
        hard = True
        codes.append("REWRITE_START_FRAGMENT_BROKEN")
    if stripped and _looks_like_end_fragment(stripped):
        hard = True
        codes.append("REWRITE_END_FRAGMENT_BROKEN")

    outlier_level, outlier_code = _length_outlier_level(
        actual_chars=len(stripped),
        target_min=validation.details.get("target_chars_min") if isinstance(validation.details, Mapping) else None,
        target_max=validation.details.get("target_chars_max") if isinstance(validation.details, Mapping) else None,
    )
    if outlier_level == WindowGuardrailLevel.HARD_FAIL and outlier_code:
        hard = True
        codes.append(outlier_code)
    elif outlier_level == WindowGuardrailLevel.WARNING and outlier_code:
        codes.append(outlier_code)

    if finish_reason == "length":
        details["finish_reason"] = finish_reason
        # Treat truncation as hard fail only when output integrity already looks broken.
        if "REWRITE_START_FRAGMENT_BROKEN" in codes or "REWRITE_END_FRAGMENT_BROKEN" in codes:
            hard = True
            codes.append("REWRITE_TRUNCATED")

    if validation.error_code == "REWRITE_TOO_SIMILAR":
        codes.append(validation.error_code)
    elif validation.error_code and validation.error_code != "REWRITE_LENGTH_OUT_OF_RANGE":
        hard = True
        codes.append(validation.error_code)
    elif validation.error_code == "REWRITE_LENGTH_OUT_OF_RANGE" and not hard:
        codes.append(validation.error_code)

    deduped_codes: list[str] = []
    for code in codes:
        if code and code not in deduped_codes:
            deduped_codes.append(code)

    if hard:
        return GuardrailEvaluation(level=WindowGuardrailLevel.HARD_FAIL, codes=deduped_codes, details=details)
    if deduped_codes:
        return GuardrailEvaluation(level=WindowGuardrailLevel.WARNING, codes=deduped_codes, details=details)
    return GuardrailEvaluation(level=WindowGuardrailLevel.INFO, codes=[], details=details)


def _window_guardrail_payload(evaluation: GuardrailEvaluation) -> WindowGuardrail | None:
    if not evaluation.codes and not evaluation.details:
        return None
    return WindowGuardrail(level=evaluation.level, codes=list(evaluation.codes), details=dict(evaluation.details))


def _result_finish_reason(result: RewriteResult) -> str | None:
    raw_response = result.provider_raw_response
    reason = _completion_finish_reason(raw_response)
    if reason:
        return reason
    if not isinstance(raw_response, Mapping):
        return None
    auto_split = raw_response.get("auto_split")
    if not isinstance(auto_split, Mapping):
        return None
    parts = auto_split.get("parts")
    if not isinstance(parts, list):
        return None
    for part in reversed(parts):
        if not isinstance(part, Mapping):
            continue
        finish = part.get("finish_reason")
        if finish is None:
            continue
        text = str(finish).strip()
        if text:
            return text
    return None


def _window_range_valid(window: RewriteWindow | None, chapter: Chapter) -> bool:
    if window is None:
        return True
    if window.start_offset < 0:
        return False
    if window.end_offset <= window.start_offset:
        return False
    return window.end_offset <= len(chapter.content)


def _result_validation_payload(result: RewriteResult) -> RewriteValidationResult:
    details = dict(result.validation_details or {})
    if "actual_chars" not in details:
        details["actual_chars"] = int(result.rewritten_chars or result.actual_chars or 0)
    if "target_chars_min" not in details and result.target_chars_min is not None:
        details["target_chars_min"] = result.target_chars_min
    if "target_chars_max" not in details and result.target_chars_max is not None:
        details["target_chars_max"] = result.target_chars_max
    passed = result.status == RewriteResultStatus.COMPLETED and result.error_code is None
    return RewriteValidationResult(
        passed=passed,
        similarity=0.0,
        original_chars=result.original_chars,
        rewritten_chars=result.rewritten_chars,
        error_code=result.error_code,
        error_message=result.error_detail,
        details=details,
    )


def _normalize_rewrite_completion_text(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return _clean_rewrite_meta_text(stripped)

    lines = stripped.splitlines()
    if len(lines) == 1:
        return _clean_rewrite_meta_text(stripped.strip("`").strip())
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return _clean_rewrite_meta_text("\n".join(body).strip())


def _clean_rewrite_meta_text(text: str) -> str:
    if not text:
        return ""

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        for pattern, replacement in _REWRITE_META_PREFIX_REPLACEMENTS:
            line = pattern.sub(replacement, line)
        if _REWRITE_META_STANDALONE_LINE_RE.match(line.strip()):
            continue
        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def build_rewrite_prompt_bundle(
    chapter: Chapter,
    analysis: ChapterAnalysis,
    segment: RewriteSegment,
    *,
    global_prompt: str = "",
    rewrite_general_guidance: str = "",
    rewrite_rules: Sequence[RewriteRule] | None = None,
    preceding_text: str = "",
    following_text: str = "",
    rewrite_mode: str | None = None,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    segment_text_override: str | None = None,
    rewrite_part_index: int | None = None,
    rewrite_part_total: int | None = None,
    registry: PromptTemplateRegistry | None = None,
) -> StagePromptBundle:
    """Build the rewrite prompt bundle for a single segment."""

    if segment_text_override is not None:
        original_text = segment_text_override.strip()
    else:
        _, original_text, _, _, _ = _extract_segment_text(chapter, segment)
    anchor = build_anchor(chapter, segment.paragraph_range, context_window_size=context_window_size)
    context = {
        "chapter_summary": analysis.summary,
        "character_states": [character.model_dump(mode="json") for character in analysis.characters],
        "preceding_text": _trim_context(preceding_text, limit_chars=context_chars, tail=True),
        "following_text": _trim_context(following_text, limit_chars=context_chars, tail=False),
        "preceding_context": _trim_context(preceding_text, limit_chars=context_chars, tail=True),
        "following_context": _trim_context(following_text, limit_chars=context_chars, tail=False),
        "rewrite_mode": rewrite_mode or segment.strategy.value,
        "anchor": anchor.model_dump(mode="json"),
        "segment_scene_type": segment.scene_type,
        "segment_text": original_text,
        "window_text": original_text,
        "rewrite_general_guidance": rewrite_general_guidance,
        "rewrite_rules": list(rewrite_rules or []),
    }
    if rewrite_part_index is not None and rewrite_part_total is not None:
        context["rewrite_part_index"] = rewrite_part_index
        context["rewrite_part_total"] = rewrite_part_total
    return build_stage_prompts("rewrite", global_prompt=global_prompt, context=context, registry=registry)


def build_rewrite_completion_request(
    chapter: Chapter,
    analysis: ChapterAnalysis,
    segment: RewriteSegment,
    *,
    global_prompt: str = "",
    rewrite_general_guidance: str = "",
    rewrite_rules: Sequence[RewriteRule] | None = None,
    preceding_text: str = "",
    following_text: str = "",
    rewrite_mode: str | None = None,
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE,
    model_name: str = "",
    generation: GenerationParams | Mapping[str, Any] | None = None,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    segment_text_override: str | None = None,
    rewrite_part_index: int | None = None,
    rewrite_part_total: int | None = None,
    metadata_extra: Mapping[str, Any] | None = None,
    registry: PromptTemplateRegistry | None = None,
) -> tuple[StagePromptBundle, CompletionRequest]:
    """Build the prompt bundle and completion request for a rewrite segment."""

    prompt_bundle = build_rewrite_prompt_bundle(
        chapter,
        analysis,
        segment,
        global_prompt=global_prompt,
        rewrite_general_guidance=rewrite_general_guidance,
        rewrite_rules=rewrite_rules,
        preceding_text=preceding_text,
        following_text=following_text,
        rewrite_mode=rewrite_mode,
        context_window_size=context_window_size,
        context_chars=context_chars,
        segment_text_override=segment_text_override,
        rewrite_part_index=rewrite_part_index,
        rewrite_part_total=rewrite_part_total,
        registry=registry,
    )
    resolved_generation = build_generation_params(provider_defaults=generation)
    metadata = {
        "stage": "rewrite",
        "chapter_index": chapter.index,
        "segment_id": segment.segment_id,
        "paragraph_range": list(segment.paragraph_range),
    }
    if metadata_extra:
        metadata.update(dict(metadata_extra))
    request = CompletionRequest(
        model_name=model_name,
        messages=prompt_bundle.messages,
        generation=resolved_generation,
        metadata=metadata,
    )
    return prompt_bundle, request


def validate_rewrite_anchor(
    chapter: Chapter,
    segment: RewriteSegment,
    *,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
) -> RewriteAnchorValidationResult:
    """Validate that the stored anchor still matches the current chapter text."""

    try:
        expected_anchor = build_anchor(chapter, segment.paragraph_range, context_window_size=context_window_size)
    except AppError as exc:
        return RewriteAnchorValidationResult(
            passed=False,
            current_anchor=segment.anchor,
            error_code=exc.code.value,
            error_message=exc.message,
            details=exc.details,
        )

    current_anchor = segment.anchor
    details = {
        "chapter_index": chapter.index,
        "paragraph_range": list(segment.paragraph_range),
        "expected": expected_anchor.model_dump(mode="json"),
        "current": current_anchor.model_dump(mode="json"),
    }
    if expected_anchor != current_anchor:
        mismatch_fields = {
            key: {
                "expected": getattr(expected_anchor, key),
                "current": getattr(current_anchor, key),
            }
            for key in (
                "paragraph_start_hash",
                "paragraph_end_hash",
                "range_text_hash",
                "context_window_hash",
                "paragraph_count_snapshot",
            )
            if getattr(expected_anchor, key) != getattr(current_anchor, key)
        }
        details["mismatch_fields"] = mismatch_fields
        return RewriteAnchorValidationResult(
            passed=False,
            expected_anchor=expected_anchor,
            current_anchor=current_anchor,
            error_code=ErrorCode.ANCHOR_MISMATCH.value,
            error_message="Rewrite anchor mismatch",
            details=details,
        )

    return RewriteAnchorValidationResult(
        passed=True,
        expected_anchor=expected_anchor,
        current_anchor=current_anchor,
        details=details,
    )


def _build_rewrite_result(
    request: RewriteSegmentRequest,
    *,
    status: RewriteResultStatus,
    original_text: str,
    rewritten_text: str,
    attempts: int,
    anchor_verified: bool,
    provider_used: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    provider_raw_response: dict[str, Any] | None = None,
    validation_details: dict[str, Any] | None = None,
) -> RewriteResult:
    actual_chars = len(rewritten_text.strip())
    warning_codes = [error_code] if error_code else []
    return RewriteResult(
        segment_id=request.segment.segment_id,
        chapter_index=request.chapter.index,
        paragraph_range=request.segment.paragraph_range,
        char_offset_range=request.segment.char_offset_range,
        rewrite_windows=list(request.segment.rewrite_windows or []),
        scene_type=request.segment.scene_type,
        suggestion=request.segment.suggestion,
        target_ratio=request.segment.target_ratio,
        target_chars=request.segment.target_chars,
        target_chars_min=request.segment.target_chars_min,
        target_chars_max=request.segment.target_chars_max,
        completion_kind="normal",
        reason_code=None,
        has_warnings=bool(warning_codes),
        warning_count=len(warning_codes),
        warning_codes=warning_codes,
        anchor_verified=anchor_verified,
        strategy=request.segment.strategy,
        original_text=original_text,
        rewritten_text=rewritten_text,
        original_chars=len(original_text),
        rewritten_chars=actual_chars,
        actual_chars=actual_chars,
        status=status,
        attempts=attempts,
        provider_used=provider_used,
        error_code=error_code,
        error_detail=error_detail,
        provider_raw_response=provider_raw_response,
        validation_details=validation_details,
        manual_edited_text=None,
    )


async def _execute_rewrite_segment_once(
    request: RewriteSegmentRequest,
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> RewriteResult:
    """Execute one rewrite segment and return a structured result."""

    try:
        _, original_text, preceding_text, following_text, slice_details = _extract_segment_text(request.chapter, request.segment)
    except AppError as exc:
        return _build_rewrite_result(
            request,
            status=RewriteResultStatus.FAILED,
            original_text="",
            rewritten_text="",
            attempts=0,
            anchor_verified=False,
            provider_used=request.provider_type.value,
            error_code=exc.code.value,
            error_detail=_json_detail_text(exc.details) or exc.message,
        )
    anchor_validation = validate_rewrite_anchor(request.chapter, request.segment, context_window_size=request.context_window_size)
    if not anchor_validation.passed:
        return _build_rewrite_result(
            request,
            status=RewriteResultStatus.FAILED,
            original_text=original_text,
            rewritten_text="",
            attempts=0,
            anchor_verified=False,
            provider_used=request.provider_type.value,
            error_code=anchor_validation.error_code or ErrorCode.ANCHOR_MISMATCH.value,
            error_detail=_json_detail_text(anchor_validation.details) or anchor_validation.error_message,
            validation_details=_with_slice_details(anchor_validation.details, slice_details=slice_details),
        )

    resolved_generation = build_generation_params(provider_defaults=request.generation)
    auto_split_plan = _build_auto_split_plan(
        original_text=original_text,
        preceding_text=preceding_text,
        following_text=following_text,
        segment=request.segment,
        generation=resolved_generation,
    )

    if auto_split_plan is None:
        _prompt_bundle, completion_request = build_rewrite_completion_request(
            request.chapter,
            request.analysis,
            request.segment,
            global_prompt=request.global_prompt,
            rewrite_general_guidance=request.rewrite_general_guidance,
            rewrite_rules=request.rewrite_rules,
            preceding_text=preceding_text,
            following_text=following_text,
            rewrite_mode=request.segment.strategy.value,
            provider_type=request.provider_type,
            model_name=request.model_name,
            generation=resolved_generation,
            context_window_size=request.context_window_size,
            context_chars=request.context_chars,
            registry=request.prompt_registry,
        )

        try:
            completion = await llm_complete(
                request.api_key,
                request.base_url,
                completion_request,
                provider_type=request.provider_type,
                transport=transport,
            )
        except AppError as exc:
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text="",
                attempts=1,
                anchor_verified=True,
                provider_used=request.provider_type.value,
                error_code=exc.code.value,
                error_detail=_json_detail_text(exc.details) or exc.message,
                provider_raw_response=exc.details or None,
                validation_details=_with_slice_details(None, slice_details=slice_details),
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text="",
                attempts=1,
                anchor_verified=True,
                provider_used=request.provider_type.value,
                error_code=ErrorCode.STAGE_FAILED.value,
                error_detail=f"{exc.__class__.__name__}: {exc}",
                validation_details=_with_slice_details(None, slice_details=slice_details),
            )

        rewritten_text = _normalize_rewrite_completion_text(completion.text)
        validation = validate_rewrite_output(
            original_text,
            rewritten_text,
            target_chars=request.segment.target_chars,
            target_chars_min=request.segment.target_chars_min,
            target_chars_max=request.segment.target_chars_max,
        )
        if not validation.passed:
            if validation.error_code == "REWRITE_LENGTH_OUT_OF_RANGE":
                length_undershoot = _is_length_validation_undershoot(validation)
                return _build_rewrite_result(
                    request,
                    status=RewriteResultStatus.COMPLETED,
                    original_text=original_text,
                    rewritten_text=rewritten_text,
                    attempts=1,
                    anchor_verified=True,
                    provider_used=completion.provider_type.value,
                    # Keep undershoot as a validation error, but do not treat
                    # over-length output as an error.
                    error_code=validation.error_code if length_undershoot else None,
                    error_detail=(_json_detail_text(validation.details) or validation.error_message) if length_undershoot else None,
                    provider_raw_response=completion.raw_response,
                    validation_details=_with_slice_details(validation.details, slice_details=slice_details),
                )
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text=rewritten_text,
                attempts=1,
                anchor_verified=True,
                provider_used=completion.provider_type.value,
                error_code=validation.error_code or "REWRITE_VALIDATION_FAILED",
                error_detail=_json_detail_text(validation.details) or validation.error_message,
                provider_raw_response=completion.raw_response,
                validation_details=_with_slice_details(validation.details, slice_details=slice_details),
            )

        return _build_rewrite_result(
            request,
            status=RewriteResultStatus.COMPLETED,
            original_text=original_text,
            rewritten_text=rewritten_text,
            attempts=1,
            anchor_verified=True,
            provider_used=completion.provider_type.value,
            provider_raw_response=completion.raw_response,
            validation_details=_with_slice_details(validation.details, slice_details=slice_details),
        )

    rewritten_parts: list[str] = []
    part_records: list[dict[str, Any]] = []
    part_raw_payloads: list[dict[str, Any]] = []
    part_length_warnings: list[dict[str, Any]] = []
    attempts = 0
    provider_used = request.provider_type.value

    for part in auto_split_plan.parts:
        attempts += 1
        _prompt_bundle, completion_request = build_rewrite_completion_request(
            request.chapter,
            request.analysis,
            request.segment,
            global_prompt=request.global_prompt,
            rewrite_general_guidance=_part_rewrite_guidance(
                request.rewrite_general_guidance,
                part_index=part.index,
                part_total=len(auto_split_plan.parts),
            ),
            rewrite_rules=request.rewrite_rules,
            preceding_text=part.preceding_text,
            following_text=part.following_text,
            rewrite_mode=request.segment.strategy.value,
            provider_type=request.provider_type,
            model_name=request.model_name,
            generation=resolved_generation,
            context_window_size=request.context_window_size,
            context_chars=request.context_chars,
            segment_text_override=part.source_text,
            rewrite_part_index=part.index,
            rewrite_part_total=len(auto_split_plan.parts),
            metadata_extra={
                "auto_split": True,
                "rewrite_part_index": part.index,
                "rewrite_part_total": len(auto_split_plan.parts),
            },
            registry=request.prompt_registry,
        )

        try:
            completion = await llm_complete(
                request.api_key,
                request.base_url,
                completion_request,
                provider_type=request.provider_type,
                transport=transport,
            )
        except AppError as exc:
            failure_details = _with_slice_details(None, slice_details=slice_details)
            failure_details["auto_split"] = {
                "enabled": True,
                "trigger_reason": auto_split_plan.trigger_reason,
                "parts_total": len(auto_split_plan.parts),
                "failed_part_index": part.index,
                "processed_parts": len(part_records),
            }
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text="",
                attempts=attempts,
                anchor_verified=True,
                provider_used=request.provider_type.value,
                error_code=exc.code.value,
                error_detail=_json_detail_text(exc.details) or exc.message,
                provider_raw_response=exc.details or None,
                validation_details=failure_details,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            failure_details = _with_slice_details(None, slice_details=slice_details)
            failure_details["auto_split"] = {
                "enabled": True,
                "trigger_reason": auto_split_plan.trigger_reason,
                "parts_total": len(auto_split_plan.parts),
                "failed_part_index": part.index,
                "processed_parts": len(part_records),
            }
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text="",
                attempts=attempts,
                anchor_verified=True,
                provider_used=request.provider_type.value,
                error_code=ErrorCode.STAGE_FAILED.value,
                error_detail=f"{exc.__class__.__name__}: {exc}",
                validation_details=failure_details,
            )

        provider_used = completion.provider_type.value
        part_rewritten = _normalize_rewrite_completion_text(completion.text)
        finish_reason = _completion_finish_reason(completion.raw_response)
        part_validation = validate_rewrite_output(
            part.source_text,
            part_rewritten,
            target_chars=part.target_chars,
            target_chars_min=part.target_chars_min,
            target_chars_max=part.target_chars_max,
        )

        part_record = {
            "part_index": part.index,
            "source_chars": len(part.source_text),
            "target_chars": part.target_chars,
            "target_chars_min": part.target_chars_min,
            "target_chars_max": part.target_chars_max,
            "actual_chars": len(part_rewritten.strip()),
            "status": "completed",
            "finish_reason": finish_reason,
        }
        if not part_validation.passed:
            part_record["error_code"] = part_validation.error_code
            part_record["error_detail"] = part_validation.details

        length_out_of_range = part_validation.error_code == "REWRITE_LENGTH_OUT_OF_RANGE"
        length_undershoot = length_out_of_range and _is_length_validation_undershoot(part_validation)

        if finish_reason == "length" or (length_out_of_range and not length_undershoot):
            part_length_warnings.append(
                {
                    "part_index": part.index,
                    "finish_reason": finish_reason,
                    "validation": part_validation.details,
                }
            )

        if not part_validation.passed and (part_validation.error_code != "REWRITE_LENGTH_OUT_OF_RANGE" or length_undershoot):
            failure_details = _with_slice_details(part_validation.details, slice_details=slice_details)
            failure_details["auto_split"] = {
                "enabled": True,
                "trigger_reason": auto_split_plan.trigger_reason,
                "parts_total": len(auto_split_plan.parts),
                "failed_part_index": part.index,
                "processed_parts": len(part_records),
                "parts": part_records + [part_record],
            }
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.FAILED,
                original_text=original_text,
                rewritten_text=part_rewritten,
                attempts=attempts,
                anchor_verified=True,
                provider_used=provider_used,
                error_code=part_validation.error_code or "REWRITE_VALIDATION_FAILED",
                error_detail=_json_detail_text(part_validation.details) or part_validation.error_message,
                provider_raw_response=completion.raw_response,
                validation_details=failure_details,
            )

        rewritten_parts.append(part_rewritten)
        part_records.append(part_record)
        part_raw_payloads.append(
            {
                "part_index": part.index,
                "finish_reason": finish_reason,
                "raw_response": completion.raw_response,
            }
        )

    merged_rewritten_text = "".join(item for item in rewritten_parts if item).strip()
    final_validation = validate_rewrite_output(
        original_text,
        merged_rewritten_text,
        target_chars=request.segment.target_chars,
        target_chars_min=request.segment.target_chars_min,
        target_chars_max=request.segment.target_chars_max,
    )
    final_validation_details = _with_slice_details(final_validation.details, slice_details=slice_details)
    final_validation_details["auto_split"] = {
        "enabled": True,
        "trigger_reason": auto_split_plan.trigger_reason,
        "parts_total": len(auto_split_plan.parts),
        "max_tokens": auto_split_plan.max_tokens,
        "safe_output_chars": auto_split_plan.safe_output_chars,
        "max_original_chars_per_part": auto_split_plan.max_original_chars_per_part,
        "min_original_chars_per_part": auto_split_plan.min_original_chars_per_part,
        "parts": part_records,
    }
    if part_length_warnings:
        final_validation_details["auto_split"]["warnings"] = part_length_warnings

    provider_raw_response = {
        "auto_split": {
            "parts": part_raw_payloads,
        }
    }

    if not final_validation.passed:
        if final_validation.error_code == "REWRITE_LENGTH_OUT_OF_RANGE":
            length_undershoot = _is_length_validation_undershoot(final_validation)
            return _build_rewrite_result(
                request,
                status=RewriteResultStatus.COMPLETED,
                original_text=original_text,
                rewritten_text=merged_rewritten_text,
                attempts=attempts,
                anchor_verified=True,
                provider_used=provider_used,
                # Keep undershoot as a validation error, but do not treat
                # over-length output as an error.
                error_code=final_validation.error_code if length_undershoot else None,
                error_detail=(_json_detail_text(final_validation.details) or final_validation.error_message) if length_undershoot else None,
                provider_raw_response=provider_raw_response,
                validation_details=final_validation_details,
            )
        return _build_rewrite_result(
            request,
            status=RewriteResultStatus.FAILED,
            original_text=original_text,
            rewritten_text=merged_rewritten_text,
            attempts=attempts,
            anchor_verified=True,
            provider_used=provider_used,
            error_code=final_validation.error_code or "REWRITE_VALIDATION_FAILED",
            error_detail=_json_detail_text(final_validation.details) or final_validation.error_message,
            provider_raw_response=provider_raw_response,
            validation_details=final_validation_details,
        )

    return _build_rewrite_result(
        request,
        status=RewriteResultStatus.COMPLETED,
        original_text=original_text,
        rewritten_text=merged_rewritten_text,
        attempts=attempts,
        anchor_verified=True,
        provider_used=provider_used,
        provider_raw_response=provider_raw_response,
        validation_details=final_validation_details,
    )


def _primary_window_for_request(request: RewriteSegmentRequest) -> RewriteWindow | None:
    windows = sorted(
        list(request.segment.rewrite_windows or []),
        key=lambda item: (item.start_offset, item.end_offset, item.window_id),
    )
    if windows:
        return windows[0]
    if request.segment.char_offset_range is not None:
        start_offset, end_offset = request.segment.char_offset_range
        if start_offset < 0 or end_offset <= start_offset:
            return None
        if end_offset > len(request.chapter.content):
            return None
        return RewriteWindow(
            window_id=f"legacy-{request.segment.segment_id}",
            segment_id=request.segment.segment_id,
            chapter_index=request.chapter.index,
            start_offset=start_offset,
            end_offset=end_offset,
            hit_sentence_range=request.segment.sentence_range,
            context_sentence_range=request.segment.sentence_range,
            target_chars=max(1, int(request.segment.target_chars or 1)),
            target_chars_min=max(1, int(request.segment.target_chars_min or 1)),
            target_chars_max=max(1, int(request.segment.target_chars_max or max(1, int(request.segment.target_chars or 1)))),
            source_fingerprint=request.segment.source_fingerprint,
            plan_version=request.segment.plan_version,
        )
    return None


def _merge_warning_codes(*code_lists: Sequence[str]) -> list[str]:
    merged: list[str] = []
    for codes in code_lists:
        for code in codes:
            if code and code not in merged:
                merged.append(code)
    return merged


async def execute_rewrite_segment(
    request: RewriteSegmentRequest,
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> RewriteResult:
    """Execute one rewrite segment with window-level guardrail retries and audit."""

    if not request.window_mode_enabled:
        base_result = await _execute_rewrite_segment_once(
            request,
            llm_complete=llm_complete,
            transport=transport,
        )
        if not request.window_audit_enabled:
            return base_result.model_copy(update={"window_attempts": []})
        finish_reason = _result_finish_reason(base_result)
        attempt = WindowAttempt(
            window_id=f"legacy-{request.segment.segment_id}",
            attempt_seq=1,
            run_seq=request.stage_run_seq,
            provider_id=request.provider_type.value,
            model_name=request.model_name or None,
            finish_reason=finish_reason,
            raw_response_ref=_response_request_id(base_result.provider_raw_response),
            guardrail=None,
            action=WindowAttemptAction.ACCEPTED,
        )
        return base_result.model_copy(update={"window_attempts": [attempt]})

    window = _primary_window_for_request(request)
    window_id = window.window_id if window is not None else f"legacy-{request.segment.segment_id}"
    max_retry = max(1, DEFAULT_WINDOW_MAX_RETRY)
    retriable_codes = {
        "REWRITE_EMPTY",
        "REWRITE_LENGTH_OUT_OF_RANGE",
        "REWRITE_TOO_SIMILAR",
        "REWRITE_START_FRAGMENT_BROKEN",
        "REWRITE_END_FRAGMENT_BROKEN",
        "REWRITE_LENGTH_SEVERE_OUTLIER",
        "REWRITE_TRUNCATED",
        "REWRITE_VALIDATION_FAILED",
    }

    attempts: list[WindowAttempt] = []
    first_hard_codes: list[str] = []
    last_result: RewriteResult | None = None

    if not request.window_guardrail_enabled:
        result = await _execute_rewrite_segment_once(
            request,
            llm_complete=llm_complete,
            transport=transport,
        )
        warning_codes = _merge_warning_codes(result.warning_codes or [], [result.error_code] if result.error_code else [])
        if not request.window_audit_enabled:
            return result.model_copy(
                update={
                    "window_attempts": [],
                    "has_warnings": bool(warning_codes),
                    "warning_count": len(warning_codes),
                    "warning_codes": warning_codes,
                }
            )
        finish_reason = _result_finish_reason(result)
        attempts.append(
            WindowAttempt(
                window_id=window_id,
                attempt_seq=1,
                run_seq=request.stage_run_seq,
                provider_id=request.provider_type.value,
                model_name=request.model_name or None,
                finish_reason=finish_reason,
                raw_response_ref=_response_request_id(result.provider_raw_response),
                guardrail=None,
                action=WindowAttemptAction.ACCEPTED,
            )
        )
        return result.model_copy(
            update={
                "window_attempts": attempts,
                "has_warnings": bool(warning_codes),
                "warning_count": len(warning_codes),
                "warning_codes": warning_codes,
            }
        )

    for attempt_seq in range(1, max_retry + 1):
        result = await _execute_rewrite_segment_once(
            request,
            llm_complete=llm_complete,
            transport=transport,
        )
        last_result = result
        finish_reason = _result_finish_reason(result)
        validation = _result_validation_payload(result)

        non_retriable_failure = (
            result.status == RewriteResultStatus.FAILED
            and (result.error_code or "") not in retriable_codes
        )

        guardrail_eval = _evaluate_guardrail(
            rewritten_text=result.rewritten_text,
            validation=validation,
            finish_reason=finish_reason,
            window_range_valid=_window_range_valid(window, request.chapter),
        )
        guardrail_payload = _window_guardrail_payload(guardrail_eval)

        action = WindowAttemptAction.ACCEPTED
        if guardrail_eval.is_hard_fail:
            if not first_hard_codes:
                first_hard_codes = list(guardrail_eval.codes)
            action = WindowAttemptAction.RETRY if attempt_seq < max_retry else WindowAttemptAction.ROLLBACK_ORIGINAL

        if request.window_audit_enabled:
            attempts.append(
                WindowAttempt(
                    window_id=window_id,
                    attempt_seq=attempt_seq,
                    run_seq=request.stage_run_seq,
                    provider_id=request.provider_type.value,
                    model_name=request.model_name or None,
                    finish_reason=finish_reason,
                    raw_response_ref=_response_request_id(result.provider_raw_response),
                    guardrail=guardrail_payload,
                    action=action,
                )
            )

        if non_retriable_failure:
            warning_codes = _merge_warning_codes(result.warning_codes or [], [result.error_code] if result.error_code else [])
            return result.model_copy(
                update={
                    "window_attempts": attempts if request.window_audit_enabled else [],
                    "has_warnings": bool(warning_codes),
                    "warning_count": len(warning_codes),
                    "warning_codes": warning_codes,
                }
            )

        if guardrail_eval.is_hard_fail and attempt_seq < max_retry:
            continue

        if guardrail_eval.is_hard_fail:
            rollback_text = result.original_text
            warning_codes = _merge_warning_codes(
                first_hard_codes,
                guardrail_eval.codes,
                result.warning_codes or [],
                [result.error_code] if result.error_code else [],
            )
            return result.model_copy(
                update={
                    "status": RewriteResultStatus.COMPLETED,
                    "rewritten_text": rollback_text,
                    "rewritten_chars": len(rollback_text.strip()),
                    "actual_chars": len(rollback_text.strip()),
                    "error_code": (warning_codes[0] if warning_codes else "ROLLBACK_ORIGINAL"),
                    "error_detail": _json_detail_text(
                        {
                            "rollback_original": True,
                            "window_id": window_id,
                            "attempts": len(attempts),
                            "warning_codes": warning_codes,
                        }
                    ),
                    "window_attempts": attempts if request.window_audit_enabled else [],
                    "has_warnings": True,
                    "warning_count": len(warning_codes),
                    "warning_codes": warning_codes,
                }
            )

        warning_codes = _merge_warning_codes(
            result.warning_codes or [],
            guardrail_eval.codes,
            [result.error_code] if result.error_code else [],
        )
        return result.model_copy(
            update={
                "window_attempts": attempts if request.window_audit_enabled else [],
                "has_warnings": bool(warning_codes),
                "warning_count": len(warning_codes),
                "warning_codes": warning_codes,
            }
        )

    if last_result is not None:
        return last_result.model_copy(update={"window_attempts": attempts if request.window_audit_enabled else []})
    return _build_rewrite_result(
        request,
        status=RewriteResultStatus.FAILED,
        original_text="",
        rewritten_text="",
        attempts=0,
        anchor_verified=False,
        provider_used=request.provider_type.value,
        error_code=ErrorCode.STAGE_FAILED.value,
        error_detail="Rewrite execution exited without result",
        validation_details=None,
    )


def build_rewrite_segment_requests(chapter_request: RewriteChapterRequest) -> list[RewriteSegmentRequest]:
    """Build segment requests for a chapter in plan order."""

    chapter_plan = build_chapter_mark_plan(
        chapter_request.chapter,
        chapter_request.analysis,
        chapter_request.rewrite_rules,
        context_window_size=chapter_request.context_window_size,
    )
    return [
        RewriteSegmentRequest(
            novel_id=chapter_request.novel_id,
            task_id=chapter_request.task_id,
            chapter=chapter_request.chapter,
            analysis=chapter_request.analysis,
            segment=segment,
            rewrite_rules=chapter_request.rewrite_rules,
            global_prompt=chapter_request.global_prompt,
            rewrite_general_guidance=chapter_request.rewrite_general_guidance,
            provider_type=chapter_request.provider_type,
            api_key=chapter_request.api_key,
            base_url=chapter_request.base_url,
            model_name=chapter_request.model_name,
            generation=chapter_request.generation,
            prompt_registry=chapter_request.prompt_registry,
            context_window_size=chapter_request.context_window_size,
            context_chars=chapter_request.context_chars,
            stage_run_seq=chapter_request.stage_run_seq,
            window_mode_enabled=chapter_request.window_mode_enabled,
            window_guardrail_enabled=chapter_request.window_guardrail_enabled,
            window_audit_enabled=chapter_request.window_audit_enabled,
        )
        for segment in chapter_plan.segments
    ]


async def execute_rewrite_chapter(
    chapter_request: RewriteChapterRequest,
    *,
    submit: SubmitFn | None = None,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> list[RewriteResult]:
    """Execute one chapter sequentially and its segments in parallel."""

    segment_requests = build_rewrite_segment_requests(chapter_request)
    if not segment_requests:
        return []

    if submit is not None:
        return list(
            await asyncio.gather(
                *(submit(item) for item in segment_requests),
            )
        )

    return list(
        await asyncio.gather(
            *(execute_rewrite_segment(item, llm_complete=llm_complete, transport=transport) for item in segment_requests),
        )
    )


async def batch_rewrite_chapters(
    chapter_requests: Sequence[RewriteChapterRequest],
    *,
    submit: SubmitFn | None = None,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> list[RewriteResult]:
    """Execute rewrite chapter batches in chapter index order."""

    results: list[RewriteResult] = []
    for chapter_request in sorted(chapter_requests, key=lambda item: item.chapter.index):
        chapter_results = await execute_rewrite_chapter(
            chapter_request,
            submit=submit,
            llm_complete=llm_complete,
            transport=transport,
        )
        results.extend(chapter_results)
    return results
