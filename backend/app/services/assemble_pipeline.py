from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.app.models.core import Chapter, RewriteResult, RewriteResultStatus

PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n\s*){2,}")
CHAPTER_HEADING_RE = re.compile(
    r"^(?:第[\d零一二三四五六七八九十百千万两〇]+(?:章|节|回|卷|部|篇|集)(?:\s*.+)?|序章|前言|楔子|尾声|后记|番外.*)$"
)
CHAPTER_INDEX_TOKEN_RE = re.compile(
    r"第[\d零一二三四五六七八九十百千万两〇]+(?:章|节|回|卷|部|篇|集)"
)
SOURCE_TEXT_WHITESPACE_RE = re.compile(r"[\s\u3000]+")
HEADING_AI_SUFFIX_RE = re.compile(r"\s*(?:[（(]\s*AI\s*改写\s*[)）]|AI\s*改写)\s*$", re.IGNORECASE)
AI_REWRITE_HEADING_SUFFIX = "（AI改写）"
ALLOWED_REWRITE_STATUSES = {
    RewriteResultStatus.ACCEPTED,
    RewriteResultStatus.COMPLETED,
    RewriteResultStatus.ACCEPTED_EDITED,
}


@dataclass(slots=True)
class AssembleWarning:
    code: str
    message: str
    chapter_index: int | None = None
    segment_id: str | None = None
    paragraph_range: tuple[int, int] | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssembleThresholds:
    max_failed_ratio: float = 0.25
    max_warning_count: int = 5


@dataclass(slots=True)
class AssembleStats:
    original_chars: int = 0
    final_chars: int = 0
    rewritten_segments: int = 0
    preserved_segments: int = 0
    failed_segments: int = 0
    rolled_back_segments: int = 0
    failed_ratio: float = 0.0
    warning_count: int = 0


@dataclass(slots=True)
class QualityReport:
    thresholds: dict[str, Any]
    stats: dict[str, Any]
    warnings: list[dict[str, Any]]
    blocked: bool
    block_reasons: list[str]
    allow_force_export: bool
    risk_signature: dict[str, Any] | None = None


@dataclass(slots=True)
class RiskSignature:
    task_id: str
    stage_run_id: str | None
    reasons: list[str]
    threshold_comparison: dict[str, Any]
    timestamp: str
    signature: str


@dataclass(slots=True)
class ExportManifest:
    novel_id: str
    task_id: str
    stage_run_id: str | None
    chapter_count: int
    assembled_at: str
    risk_export: bool
    risk_signature: dict[str, Any] | None
    files: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AssembledChapter:
    chapter_index: int
    chapter_id: str
    title: str
    original_text: str
    assembled_text: str
    compare_text: str
    rewritten_segments: int
    preserved_segments: int
    failed_segments: int
    rolled_back_segments: int = 0
    warnings: list[AssembleWarning] = field(default_factory=list)


@dataclass(slots=True)
class AssembleResult:
    novel_id: str
    task_id: str
    stage_run_id: str | None
    chapters: list[AssembledChapter]
    assembled_text: str
    compare_text: str
    stats: AssembleStats
    quality_report: QualityReport
    export_manifest: ExportManifest
    risk_signature: RiskSignature | None
    warnings: list[AssembleWarning] = field(default_factory=list)
    blocked: bool = False


@dataclass(slots=True)
class _ReplacementCandidate:
    segment_id: str
    chapter_index: int
    start_offset: int
    end_offset: int
    paragraph_range: tuple[int, int] | None
    rewritten_text: str
    status: RewriteResultStatus


def _normalize_for_invariance(text: str) -> str:
    """Normalize text for outside-window invariance comparison.

    Collapses runs of whitespace to a single space and strips leading/trailing
    whitespace so that minor whitespace drift does not cause a hard failure.
    """
    return re.sub(r"\s+", " ", text).strip()


def _invariance_char_diff(a: str, b: str) -> int:
    """Return the number of differing characters between two strings."""
    diff = sum(1 for x, y in zip(a, b) if x != y)
    diff += abs(len(a) - len(b))
    return diff


# Maximum number of differing characters (after whitespace normalisation) that
# are tolerated in outside-window text before the chapter is reverted.
_OUTSIDE_INVARIANCE_TOLERANCE = 3


def _assert_outside_window_invariance(
    *,
    chapter: Chapter,
    original_text: str,
    assembled_text: str,
    candidates: Sequence[_ReplacementCandidate],
) -> list[AssembleWarning]:
    """Check that text outside rewrite windows has not changed.

    Returns a list of warnings.  An empty list means the check passed.
    Warnings with code ``OUTSIDE_TEXT_MINOR_DRIFT`` are informational (the
    assembly is still usable).  Warnings with code
    ``WINDOW_OUTSIDE_TEXT_CHANGED`` indicate a hard failure that should trigger
    a revert to the original chapter text.
    """
    if not candidates:
        return []

    warnings: list[AssembleWarning] = []
    hard_fail = False

    original_cursor = 0
    assembled_cursor = 0
    for candidate in candidates:
        if candidate.start_offset < original_cursor:
            return [_make_warning(
                "WINDOW_OUTSIDE_TEXT_CHANGED",
                "assemble detected non-monotonic replacement ranges",
                chapter_index=chapter.index,
                segment_id=candidate.segment_id,
                paragraph_range=candidate.paragraph_range,
                details={
                    "original_cursor": original_cursor,
                    "start_offset": candidate.start_offset,
                },
            )]

        preserved_len = candidate.start_offset - original_cursor
        original_slice = original_text[original_cursor:candidate.start_offset]
        assembled_slice = assembled_text[assembled_cursor : assembled_cursor + preserved_len]
        if len(assembled_slice) != preserved_len or assembled_slice != original_slice:
            # Exact match failed — try normalised comparison with tolerance
            norm_orig = _normalize_for_invariance(original_slice)
            norm_asm = _normalize_for_invariance(assembled_slice)
            if norm_orig == norm_asm:
                pass  # pure whitespace difference, no warning needed
            else:
                diff_chars = _invariance_char_diff(norm_orig, norm_asm)
                if diff_chars <= _OUTSIDE_INVARIANCE_TOLERANCE:
                    warnings.append(_make_warning(
                        "OUTSIDE_TEXT_MINOR_DRIFT",
                        f"minor whitespace drift outside rewrite window ({diff_chars} char(s))",
                        chapter_index=chapter.index,
                        segment_id=candidate.segment_id,
                        paragraph_range=candidate.paragraph_range,
                        details={
                            "before_window_range": [original_cursor, candidate.start_offset],
                            "diff_chars": diff_chars,
                        },
                    ))
                else:
                    warnings.append(_make_warning(
                        "WINDOW_OUTSIDE_TEXT_CHANGED",
                        "text outside rewrite windows changed unexpectedly",
                        chapter_index=chapter.index,
                        segment_id=candidate.segment_id,
                        paragraph_range=candidate.paragraph_range,
                        details={
                            "before_window_range": [original_cursor, candidate.start_offset],
                            "diff_chars": diff_chars,
                        },
                    ))
                    hard_fail = True

        assembled_cursor += preserved_len + len(candidate.rewritten_text)
        original_cursor = candidate.end_offset

    if original_cursor <= len(original_text):
        tail_len = len(original_text) - original_cursor
        original_tail = original_text[original_cursor:]
        assembled_tail = assembled_text[assembled_cursor : assembled_cursor + tail_len]
        if len(assembled_tail) != tail_len or assembled_tail != original_tail:
            norm_orig = _normalize_for_invariance(original_tail)
            norm_asm = _normalize_for_invariance(assembled_tail)
            if norm_orig == norm_asm:
                pass  # pure whitespace difference
            else:
                diff_chars = _invariance_char_diff(norm_orig, norm_asm)
                if diff_chars <= _OUTSIDE_INVARIANCE_TOLERANCE:
                    warnings.append(_make_warning(
                        "OUTSIDE_TEXT_MINOR_DRIFT",
                        f"minor whitespace drift in chapter tail ({diff_chars} char(s))",
                        chapter_index=chapter.index,
                        details={
                            "tail_range": [original_cursor, len(original_text)],
                            "diff_chars": diff_chars,
                        },
                    ))
                else:
                    warnings.append(_make_warning(
                        "WINDOW_OUTSIDE_TEXT_CHANGED",
                        "chapter tail outside rewrite windows changed unexpectedly",
                        chapter_index=chapter.index,
                        details={
                            "tail_range": [original_cursor, len(original_text)],
                            "diff_chars": diff_chars,
                        },
                    ))
                    hard_fail = True

    if hard_fail:
        return warnings
    # Return only informational warnings (minor drift)
    return warnings


def _split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in PARAGRAPH_SPLIT_RE.split(text) if part.strip()]


def _normalized_raw_span(content: str, start: int, end: int) -> tuple[int, int, str] | None:
    raw = content[start:end]
    stripped = raw.strip()
    if not stripped:
        return None
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    normalized_start = start + leading
    normalized_end = start + trailing
    if normalized_end <= normalized_start:
        return None
    return normalized_start, normalized_end, stripped


def _split_paragraphs_with_ranges(content: str) -> list[tuple[int, int, str]]:
    parts: list[tuple[int, int, str]] = []
    cursor = 0
    for match in PARAGRAPH_SPLIT_RE.finditer(content):
        normalized = _normalized_raw_span(content, cursor, match.start())
        if normalized is not None:
            parts.append(normalized)
        cursor = match.end()
    tail = _normalized_raw_span(content, cursor, len(content))
    if tail is not None:
        parts.append(tail)
    return parts


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value]
    return value


def _chapter_text(chapter: Chapter) -> str:
    return chapter.content


def _normalize_source_text_for_match(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return SOURCE_TEXT_WHITESPACE_RE.sub("", normalized)


def _strip_heading_ai_suffix(text: str) -> str:
    return HEADING_AI_SUFFIX_RE.sub("", text).strip()


def _append_heading_ai_suffix(text: str) -> str:
    base = _strip_heading_ai_suffix(text)
    if not base:
        return AI_REWRITE_HEADING_SUFFIX
    return f"{base}{AI_REWRITE_HEADING_SUFFIX}"


def _extract_chapter_token(text: str) -> str | None:
    match = CHAPTER_INDEX_TOKEN_RE.search(text.strip())
    return match.group(0) if match is not None else None


def _split_first_paragraph(text: str) -> tuple[str, str]:
    normalized = text.strip()
    if not normalized:
        return "", ""
    separator = PARAGRAPH_SPLIT_RE.search(normalized)
    if separator is None:
        return normalized, ""
    first = normalized[: separator.start()].strip()
    rest = normalized[separator.end() :].strip()
    return first, rest


def _resolve_heading_base(chapter: Chapter, first_paragraph: str | None) -> str:
    expected_heading = _strip_heading_ai_suffix(chapter.title.strip())
    if first_paragraph is None:
        return expected_heading or f"第{chapter.index}章"

    current_heading = _strip_heading_ai_suffix(first_paragraph)
    if not current_heading:
        return expected_heading or f"第{chapter.index}章"

    expected_token = _extract_chapter_token(expected_heading) if expected_heading else None
    current_token = _extract_chapter_token(current_heading)
    if expected_token and current_token and expected_token != current_token:
        return expected_heading
    if expected_token and current_token is None:
        return expected_heading
    return current_heading


def _ensure_chapter_heading(chapter: Chapter, assembled_text: str) -> str:
    first, rest = _split_first_paragraph(assembled_text)
    if first and _is_heading_like_paragraph(first):
        heading = _append_heading_ai_suffix(_resolve_heading_base(chapter, first))
        deduplicated_rest = _drop_redundant_leading_headings(heading, rest)
        return f"{heading}\n\n{deduplicated_rest}" if deduplicated_rest else heading

    heading = _append_heading_ai_suffix(_resolve_heading_base(chapter, None))
    if not first and not rest:
        return heading
    if not rest:
        return f"{heading}\n\n{first}"
    return f"{heading}\n\n{first}\n\n{rest}"


def _drop_redundant_leading_headings(canonical_heading: str, body: str) -> str:
    remaining = body.strip()
    if not remaining:
        return ""

    canonical_base = _strip_heading_ai_suffix(canonical_heading)
    canonical_token = _extract_chapter_token(canonical_base)
    while remaining:
        first, rest = _split_first_paragraph(remaining)
        if not first or not _is_heading_like_paragraph(first):
            break

        first_base = _strip_heading_ai_suffix(first)
        first_token = _extract_chapter_token(first_base)
        same_heading = False
        if canonical_token and first_token:
            same_heading = canonical_token == first_token
        else:
            same_heading = first_base == canonical_base

        if not same_heading:
            break
        remaining = rest.strip()
    return remaining


def _is_heading_like_paragraph(text: str) -> bool:
    normalized = text.strip().replace("\u3000", " ")
    if not normalized:
        return False
    if len(normalized) > 40:
        return False
    if re.search(r"[。！？!?；;，,:：]", normalized):
        return False
    return CHAPTER_HEADING_RE.match(normalized) is not None


def _looks_like_heading_expansion(*, original_text: str, rewritten_text: str, is_heading_only: bool) -> bool:
    if not is_heading_only:
        return False
    if not _is_heading_like_paragraph(original_text):
        return False
    normalized_rewrite = rewritten_text.strip()
    if not normalized_rewrite:
        return False
    return "\n" in normalized_rewrite or len(normalized_rewrite) > max(24, len(original_text.strip()) * 4)


def _chapter_identifier(chapter: Chapter) -> str:
    return str(chapter.id or f"chapter-{chapter.index}")


def _make_warning(
    code: str,
    message: str,
    *,
    chapter_index: int | None = None,
    segment_id: str | None = None,
    paragraph_range: tuple[int, int] | None = None,
    details: dict[str, Any] | None = None,
) -> AssembleWarning:
    return AssembleWarning(
        code=code,
        message=message,
        chapter_index=chapter_index,
        segment_id=segment_id,
        paragraph_range=paragraph_range,
        details=dict(details or {}),
    )


def _stable_signature_payload(
    *,
    task_id: str,
    stage_run_id: str | None,
    reasons: list[str],
    threshold_comparison: dict[str, Any],
    timestamp: str,
) -> str:
    payload = {
        "task_id": task_id,
        "stage_run_id": stage_run_id,
        "reasons": reasons,
        "threshold_comparison": threshold_comparison,
        "timestamp": timestamp,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _validate_chapter_continuity(chapters: Sequence[Chapter]) -> list[AssembleWarning]:
    warnings: list[AssembleWarning] = []
    if not chapters:
        return warnings

    indices = [chapter.index for chapter in chapters]
    expected = list(range(1, len(chapters) + 1))
    if indices != expected:
        missing = [index for index in expected if index not in indices]
        duplicates = sorted({index for index in indices if indices.count(index) > 1})
        warnings.append(
            _make_warning(
                "CHAPTER_INDEX_CONTINUITY",
                "chapter_index is not continuous",
                details={
                    "actual": indices,
                    "expected": expected,
                    "missing": missing,
                    "duplicates": duplicates,
                },
            )
        )
    return warnings


def _validate_status(result: RewriteResult) -> bool:
    return result.status in ALLOWED_REWRITE_STATUSES


def _normalize_chapters(chapters: Sequence[Chapter]) -> list[Chapter]:
    return sorted(list(chapters), key=lambda item: (item.index, item.id))


def _rewrite_status_warning(result: RewriteResult) -> AssembleWarning:
    return _make_warning(
        "UNSUPPORTED_REWRITE_STATUS",
        f"rewrite result status `{result.status.value}` falls back to original text",
        chapter_index=result.chapter_index,
        segment_id=result.segment_id,
        paragraph_range=result.paragraph_range,
    )


def _resolve_result_char_range(
    chapter: Chapter,
    result: RewriteResult,
    *,
    paragraph_ranges: list[tuple[int, int, str]],
) -> tuple[tuple[int, int] | None, AssembleWarning | None]:
    chapter_text = _chapter_text(chapter)
    chapter_len = len(chapter_text)

    if result.rewrite_windows:
        windows = sorted(list(result.rewrite_windows), key=lambda item: (item.start_offset, item.end_offset))
        for index, window in enumerate(windows):
            if window.start_offset < 0 or window.end_offset <= window.start_offset or window.end_offset > chapter_len:
                return None, _make_warning(
                    "REWRITE_WINDOW_OUT_OF_BOUNDS",
                    "rewrite window range is invalid",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                    details={
                        "window_id": window.window_id,
                        "start_offset": window.start_offset,
                        "end_offset": window.end_offset,
                        "content_length": chapter_len,
                    },
                )
            if index > 0 and window.start_offset < windows[index - 1].end_offset:
                return None, _make_warning(
                    "REWRITE_WINDOW_OVERLAP",
                    "rewrite windows overlap within one segment",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                    details={
                        "previous_window_id": windows[index - 1].window_id,
                        "current_window_id": window.window_id,
                    },
                )
        return (windows[0].start_offset, windows[-1].end_offset), None

    if result.char_offset_range is not None:
        start_offset, end_offset = result.char_offset_range
        if start_offset < 0 or end_offset <= start_offset:
            return None, _make_warning(
                "CHAR_OFFSET_RANGE_INVALID",
                "rewrite result char_offset_range is invalid",
                chapter_index=chapter.index,
                segment_id=result.segment_id,
                paragraph_range=result.paragraph_range,
                details={
                    "char_offset_range": list(result.char_offset_range),
                    "content_length": chapter_len,
                },
            )
        if end_offset > chapter_len:
            return None, _make_warning(
                "CHAR_OFFSET_RANGE_OUT_OF_BOUNDS",
                "rewrite result char_offset_range exceeds chapter content length",
                chapter_index=chapter.index,
                segment_id=result.segment_id,
                paragraph_range=result.paragraph_range,
                details={
                    "char_offset_range": list(result.char_offset_range),
                    "content_length": chapter_len,
                },
            )
        return (start_offset, end_offset), None

    start, end = result.paragraph_range
    if start < 1 or end < start or end > len(paragraph_ranges):
        return None, _make_warning(
            "PARAGRAPH_RANGE_OUT_OF_BOUNDS",
            "rewrite result paragraph_range is invalid",
            chapter_index=chapter.index,
            segment_id=result.segment_id,
            paragraph_range=result.paragraph_range,
            details={"paragraph_count": len(paragraph_ranges)},
        )
    return (paragraph_ranges[start - 1][0], paragraph_ranges[end - 1][1]), None


def _preflight_candidates(
    chapter: Chapter,
    rewrite_results: Sequence[RewriteResult],
    *,
    global_seen_segment_ids: set[str],
    warnings: list[AssembleWarning],
) -> tuple[list[_ReplacementCandidate], int, int]:
    """Build replacement candidates from rewrite results.

    Returns ``(candidates, failed_segments, rolled_back_segments)``.
    """
    chapter_text = _chapter_text(chapter)
    paragraphs = _split_paragraphs(chapter_text)
    paragraph_ranges = _split_paragraphs_with_ranges(chapter_text)
    candidates: list[_ReplacementCandidate] = []
    local_seen_segment_ids: set[str] = set()
    occupied_ranges: list[tuple[int, int]] = []
    failed_segments = 0
    rolled_back_segments = 0

    for result in rewrite_results:
        # Explicitly rejected segments are intentional preserves and should
        # not count as assemble failures.
        if result.status == RewriteResultStatus.REJECTED:
            continue

        # Rolled-back segments are intentional fallbacks to original text.
        # They are not failures — simply skip assembly and preserve original.
        if result.status.value == "rolled_back":
            rolled_back_segments += 1
            warnings.append(_make_warning(
                "SEGMENT_ROLLED_BACK",
                "rewrite result was rolled back; preserving original text",
                chapter_index=chapter.index,
                segment_id=result.segment_id,
                paragraph_range=result.paragraph_range,
            ))
            continue

        if not _validate_status(result):
            warnings.append(_rewrite_status_warning(result))
            failed_segments += 1
            continue

        if not result.segment_id.strip():
            warnings.append(
                _make_warning(
                    "SEGMENT_ID_UNMAPPABLE",
                    "rewrite result segment_id is empty",
                    chapter_index=chapter.index,
                    paragraph_range=result.paragraph_range,
                )
            )
            failed_segments += 1
            continue

        if result.segment_id in global_seen_segment_ids or result.segment_id in local_seen_segment_ids:
            warnings.append(
                _make_warning(
                    "SEGMENT_ID_DUPLICATE",
                    "rewrite result segment_id is duplicated",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                )
            )
            failed_segments += 1
            continue

        resolved_char_range, range_warning = _resolve_result_char_range(
            chapter,
            result,
            paragraph_ranges=paragraph_ranges,
        )
        if resolved_char_range is None:
            if range_warning is not None:
                warnings.append(range_warning)
            failed_segments += 1
            continue
        start_offset, end_offset = resolved_char_range

        original_text = chapter_text[start_offset:end_offset]
        if result.original_text.strip():
            if result.original_text.strip() != original_text.strip():
                # Rewrite stage may source text by char offsets while assemble
                # reconstructs source text by paragraph ranges; this can differ
                # on pure whitespace (for example full-width indentation).
                expected_normalized = _normalize_source_text_for_match(original_text)
                provided_normalized = _normalize_source_text_for_match(result.original_text)
                if expected_normalized != provided_normalized:
                    warnings.append(
                        _make_warning(
                            "ORIGINAL_TEXT_MISMATCH",
                            "rewrite result original_text does not match chapter content",
                            chapter_index=chapter.index,
                            segment_id=result.segment_id,
                            paragraph_range=result.paragraph_range,
                        )
                    )
                    failed_segments += 1
                    continue

        if not result.rewritten_text.strip():
            warnings.append(
                _make_warning(
                    "EMPTY_REWRITTEN_TEXT",
                    "rewrite result has no rewritten_text and falls back to original",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                )
            )
            failed_segments += 1
            continue

        start, end = result.paragraph_range
        if _looks_like_heading_expansion(
            original_text=original_text,
            rewritten_text=result.rewritten_text,
            is_heading_only=(
                start == end
                and 1 <= start <= len(paragraphs)
                and _is_heading_like_paragraph(paragraphs[start - 1])
            ),
        ):
            warnings.append(
                _make_warning(
                    "HEADING_REWRITE_EXPANSION",
                    "rewrite result expands a heading paragraph into body text; fallback to original heading",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                    details={
                        "original_chars": len(original_text.strip()),
                        "rewritten_chars": len(result.rewritten_text.strip()),
                    },
                )
            )
            failed_segments += 1
            continue

        overlap = any(
            not (end_offset <= occupied_start or start_offset >= occupied_end)
            for occupied_start, occupied_end in occupied_ranges
        )
        if overlap:
            warnings.append(
                _make_warning(
                    "CHAR_OFFSET_RANGE_OVERLAP",
                    "rewrite result char_offset_range overlaps another accepted segment",
                    chapter_index=chapter.index,
                    segment_id=result.segment_id,
                    paragraph_range=result.paragraph_range,
                )
            )
            failed_segments += 1
            continue

        candidate = _ReplacementCandidate(
            segment_id=result.segment_id,
            chapter_index=chapter.index,
            start_offset=start_offset,
            end_offset=end_offset,
            paragraph_range=result.paragraph_range,
            rewritten_text=result.rewritten_text.strip(),
            status=result.status,
        )
        candidates.append(candidate)
        local_seen_segment_ids.add(result.segment_id)
        global_seen_segment_ids.add(result.segment_id)
        occupied_ranges.append((start_offset, end_offset))

    candidates.sort(key=lambda item: (item.start_offset, item.end_offset, item.segment_id))
    return candidates, failed_segments, rolled_back_segments


def _assemble_chapter(
    chapter: Chapter,
    rewrite_results: Sequence[RewriteResult],
    *,
    global_seen_segment_ids: set[str],
) -> tuple[AssembledChapter, int, int, int, int]:
    """Assemble a single chapter.

    Returns ``(assembled_chapter, rewritten, preserved, failed, rolled_back)``.
    """
    warnings: list[AssembleWarning] = []
    original_text = _chapter_text(chapter)

    if not rewrite_results:
        assembled_text = _ensure_chapter_heading(chapter, original_text)
        compare_text = _build_compare_text(
            chapter=chapter,
            original_text=original_text,
            assembled_text=assembled_text,
            rewritten_segments=0,
            preserved_segments=1,
            failed_segments=0,
            rolled_back_segments=0,
            warnings=warnings,
        )
        assembled = AssembledChapter(
            chapter_index=chapter.index,
            chapter_id=_chapter_identifier(chapter),
            title=chapter.title,
            original_text=original_text,
            assembled_text=assembled_text,
            compare_text=compare_text,
            rewritten_segments=0,
            preserved_segments=1,
            failed_segments=0,
            rolled_back_segments=0,
            warnings=warnings,
        )
        return assembled, 0, 1, 0, 0

    candidates, failed_segments, rolled_back_segments = _preflight_candidates(
        chapter,
        rewrite_results,
        global_seen_segment_ids=global_seen_segment_ids,
        warnings=warnings,
    )
    rewritten_segments = len(candidates)

    assembled_parts: list[str] = []
    cursor = 0
    for candidate in candidates:
        if cursor < candidate.start_offset:
            assembled_parts.append(original_text[cursor:candidate.start_offset])
        assembled_parts.append(candidate.rewritten_text)
        cursor = candidate.end_offset
    if cursor < len(original_text):
        assembled_parts.append(original_text[cursor:])

    assembled_text = "".join(assembled_parts) if assembled_parts else original_text
    outside_warnings = _assert_outside_window_invariance(
        chapter=chapter,
        original_text=original_text,
        assembled_text=assembled_text,
        candidates=candidates,
    )
    if outside_warnings:
        has_hard_fail = any(w.code == "WINDOW_OUTSIDE_TEXT_CHANGED" for w in outside_warnings)
        warnings.extend(outside_warnings)
        if has_hard_fail:
            failed_segments += max(1, len(candidates))
            rewritten_segments = 0
            assembled_text = original_text

    assembled_text = _ensure_chapter_heading(chapter, assembled_text)
    compare_text = _build_compare_text(
        chapter=chapter,
        original_text=original_text,
        assembled_text=assembled_text,
        rewritten_segments=rewritten_segments,
        preserved_segments=0,
        failed_segments=failed_segments,
        rolled_back_segments=rolled_back_segments,
        warnings=warnings,
    )
    assembled = AssembledChapter(
        chapter_index=chapter.index,
        chapter_id=_chapter_identifier(chapter),
        title=chapter.title,
        original_text=original_text,
        assembled_text=assembled_text,
        compare_text=compare_text,
        rewritten_segments=rewritten_segments,
        preserved_segments=0,
        failed_segments=failed_segments,
        rolled_back_segments=rolled_back_segments,
        warnings=warnings,
    )
    return assembled, rewritten_segments, 0, failed_segments, rolled_back_segments


def _build_compare_text(
    *,
    chapter: Chapter,
    original_text: str,
    assembled_text: str,
    rewritten_segments: int,
    preserved_segments: int,
    failed_segments: int,
    rolled_back_segments: int = 0,
    warnings: Sequence[AssembleWarning],
) -> str:
    warning_lines = "\n".join(f"- {warning.code}: {warning.message}" for warning in warnings) if warnings else "- none"
    return (
        f"=== Chapter {chapter.index}: {chapter.title} ===\n"
        f"[original]\n{original_text}\n\n"
        f"[assembled]\n{assembled_text}\n\n"
        f"[stats]\n"
        f"rewritten_segments={rewritten_segments}\n"
        f"preserved_segments={preserved_segments}\n"
        f"failed_segments={failed_segments}\n"
        f"rolled_back_segments={rolled_back_segments}\n"
        f"[warnings]\n{warning_lines}"
    )


def _build_quality_report(
    *,
    stats: AssembleStats,
    thresholds: AssembleThresholds,
    warnings: Sequence[AssembleWarning],
    blocked: bool,
    block_reasons: Sequence[str],
    risk_signature: RiskSignature | None,
) -> QualityReport:
    return QualityReport(
        thresholds=asdict(thresholds),
        stats=asdict(stats),
        warnings=[asdict(warning) for warning in warnings],
        blocked=blocked,
        block_reasons=list(block_reasons),
        allow_force_export=blocked,
        risk_signature=asdict(risk_signature) if risk_signature is not None else None,
    )


def _build_risk_signature(
    *,
    task_id: str,
    stage_run_id: str | None,
    stats: AssembleStats,
    thresholds: AssembleThresholds,
    block_reasons: Sequence[str],
) -> RiskSignature:
    timestamp = _now_iso()
    threshold_comparison = {
        "failed_ratio": {
            "value": stats.failed_ratio,
            "threshold": thresholds.max_failed_ratio,
            "exceeded": stats.failed_ratio > thresholds.max_failed_ratio,
        },
        "warning_count": {
            "value": stats.warning_count,
            "threshold": thresholds.max_warning_count,
            "exceeded": stats.warning_count > thresholds.max_warning_count,
        },
    }
    signature = _stable_signature_payload(
        task_id=task_id,
        stage_run_id=stage_run_id,
        reasons=list(block_reasons),
        threshold_comparison=threshold_comparison,
        timestamp=timestamp,
    )
    return RiskSignature(
        task_id=task_id,
        stage_run_id=stage_run_id,
        reasons=list(block_reasons),
        threshold_comparison=threshold_comparison,
        timestamp=timestamp,
        signature=signature,
    )


def assemble_novel(
    novel_id: str,
    task_id: str,
    chapters: Sequence[Chapter],
    rewrite_results_by_chapter: Mapping[int, Sequence[RewriteResult]] | None = None,
    *,
    stage_run_id: str | None = None,
    thresholds: AssembleThresholds | Mapping[str, Any] | None = None,
    force: bool = False,
) -> AssembleResult:
    normalized_chapters = _normalize_chapters(chapters)
    rewritten_map = rewrite_results_by_chapter or {}

    resolved_thresholds = thresholds
    if resolved_thresholds is None:
        resolved_thresholds = AssembleThresholds()
    elif isinstance(resolved_thresholds, Mapping):
        resolved_thresholds = AssembleThresholds(
            max_failed_ratio=float(resolved_thresholds.get("max_failed_ratio", 0.25)),
            max_warning_count=int(resolved_thresholds.get("max_warning_count", 5)),
        )

    warnings: list[AssembleWarning] = _validate_chapter_continuity(normalized_chapters)
    assembled_chapters: list[AssembledChapter] = []
    global_seen_segment_ids: set[str] = set()

    total_rewritten_segments = 0
    total_preserved_segments = 0
    total_failed_segments = 0
    total_rolled_back_segments = 0

    for chapter in normalized_chapters:
        chapter_results = list(rewritten_map.get(chapter.index, []))
        assembled_chapter, rewritten_count, preserved_count, failed_count, rolled_back_count = _assemble_chapter(
            chapter,
            chapter_results,
            global_seen_segment_ids=global_seen_segment_ids,
        )
        assembled_chapters.append(assembled_chapter)
        warnings.extend(assembled_chapter.warnings)
        total_rewritten_segments += rewritten_count
        total_preserved_segments += preserved_count
        total_failed_segments += failed_count
        total_rolled_back_segments += rolled_back_count

    assembled_text = "\n\n".join(chapter.assembled_text for chapter in assembled_chapters)
    compare_text = "\n\n".join(chapter.compare_text for chapter in assembled_chapters)
    original_chars = sum(len(chapter.content) for chapter in normalized_chapters)
    final_chars = len(assembled_text)

    stats = AssembleStats(
        original_chars=original_chars,
        final_chars=final_chars,
        rewritten_segments=total_rewritten_segments,
        preserved_segments=total_preserved_segments,
        failed_segments=total_failed_segments,
        rolled_back_segments=total_rolled_back_segments,
    )
    stats.warning_count = len(warnings)
    total_segments = stats.rewritten_segments + stats.preserved_segments + stats.failed_segments
    stats.failed_ratio = (stats.failed_segments / total_segments) if total_segments else 0.0

    block_reasons: list[str] = []
    if warnings and any(warning.code == "CHAPTER_INDEX_CONTINUITY" for warning in warnings):
        block_reasons.append("chapter_index_continuity")
    if normalized_chapters and len({chapter.index for chapter in normalized_chapters}) != len(normalized_chapters):
        block_reasons.append("duplicate_chapter_index")
    output_indices = [chapter.chapter_index for chapter in assembled_chapters]
    expected_indices = list(range(1, len(assembled_chapters) + 1))
    if output_indices != expected_indices or len(set(output_indices)) != len(output_indices):
        block_reasons.append("chapter_coverage_mismatch")

    if stats.failed_ratio > resolved_thresholds.max_failed_ratio:
        block_reasons.append("failed_ratio_exceeded")
    if stats.warning_count > resolved_thresholds.max_warning_count:
        block_reasons.append("warning_count_exceeded")

    blocked = bool(block_reasons)
    risk_signature = _build_risk_signature(
        task_id=task_id,
        stage_run_id=stage_run_id,
        stats=stats,
        thresholds=resolved_thresholds,
        block_reasons=block_reasons,
    ) if force and blocked else None
    quality_report = _build_quality_report(
        stats=stats,
        thresholds=resolved_thresholds,
        warnings=warnings,
        blocked=blocked,
        block_reasons=block_reasons,
        risk_signature=risk_signature,
    )

    export_manifest = ExportManifest(
        novel_id=novel_id,
        task_id=task_id,
        stage_run_id=stage_run_id,
        chapter_count=len(assembled_chapters),
        assembled_at=_now_iso(),
        risk_export=bool(force and blocked),
        risk_signature=asdict(risk_signature) if risk_signature is not None else None,
        files={
            "assembled_txt": "assembled.txt",
            "compare_txt": "assembled_compare.txt",
            "quality_report": "quality_report.json",
            "export_manifest": "export_manifest.json",
        },
    )

    return AssembleResult(
        novel_id=novel_id,
        task_id=task_id,
        stage_run_id=stage_run_id,
        chapters=assembled_chapters,
        assembled_text=assembled_text,
        compare_text=compare_text,
        stats=stats,
        quality_report=quality_report,
        export_manifest=export_manifest,
        risk_signature=risk_signature,
        warnings=warnings,
        blocked=blocked,
    )


def assemble_results_to_dict(result: AssembleResult) -> dict[str, Any]:
    return asdict(result)
