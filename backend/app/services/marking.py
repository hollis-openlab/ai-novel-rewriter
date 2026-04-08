from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from uuid import UUID

from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    RewriteAnchor,
    RewriteChapterPlan,
    RewritePlan,
    RewriteSegment,
    RewriteStrategy,
    RewriteWindow,
    SentenceBoundaryKind,
    SentenceSpan,
)
from backend.app.services.config_store import RewriteRule

logger = logging.getLogger(__name__)

PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n\s*){2,}")
DEFAULT_CONTEXT_WINDOW_SIZE = 1
DEFAULT_SECONDS_PER_LLM_CALL = 45.0
DEFAULT_CHAR_BUFFER_RATIO = 0.12
DEFAULT_FALLBACK_EXPAND_RATIO = 1.20
DEFAULT_FALLBACK_REWRITE_RATIO = 1.00
DEFAULT_WINDOW_CONTEXT_SENTENCES = 1
DEFAULT_WINDOW_MERGE_GAP_SENTENCES = -1
DEFAULT_WINDOW_MAX_CHARS = 1_200
DEFAULT_WINDOW_MIN_SPLIT_CHARS = 420
SOURCE_PARAGRAPH_MISMATCH_RATIO_THRESHOLD = 2.0
SOURCE_PARAGRAPH_MISMATCH_MIN_DELTA = 6
RULE_HIT_EVIDENCE_MIN_FRAGMENT_CHARS = 8
RULE_HIT_CLUSTER_GAP_SENTENCES = 1
SENTENCE_SPLITTER_VERSION = "cn-punct-v2"
WINDOW_PLANNER_VERSION = "window-planner-v1"
SENTENCE_TERMINATORS = {"。", "！", "？", "!", "?", "…"}
SENTENCE_CLOSERS = {'"', "'", "”", "’", "）", ")", "】", "]", "》", "」", "』"}
CHAPTER_HEADING_RE = re.compile(
    r"^(?:第[\d零一二三四五六七八九十百千万两〇]+(?:章|节|回|卷|部|篇|集)(?:\s*.+)?|序章|前言|楔子|尾声|后记|番外.*)$"
)
RULE_HIT_EVIDENCE_SPLIT_RE = re.compile(r"(?:\r?\n)+|(?:…{2,}|\.{3,})")


@dataclass(slots=True)
class RewritePlanEstimate:
    total_marked: int
    estimated_llm_calls: int
    estimated_added_chars: int
    estimated_duration_seconds: float


@dataclass(slots=True)
class MarkArtifactPaths:
    mark_plan_path: str
    chapter_paths: dict[int, str]


@dataclass(slots=True)
class ChapterSentenceIndex:
    paragraphs: list[str]
    paragraph_char_ranges: list[tuple[int, int]]
    paragraph_sentence_ranges: list[tuple[int, int] | None]
    sentence_spans: list[SentenceSpan]


@dataclass(slots=True)
class _SceneHit:
    scene_type: str
    strategy: RewriteStrategy
    target_ratio: float
    suggestion: str
    source: str
    confirmed: bool
    priority: int
    paragraph_range: tuple[int, int]
    sentence_range: tuple[int, int]
    char_offset_range: tuple[int, int]
    hit_source: str


def _split_paragraphs(content: str) -> list[str]:
    parts = [part.strip() for part in PARAGRAPH_SPLIT_RE.split(content) if part.strip()]
    return parts


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
    normalized_tail = _normalized_raw_span(content, cursor, len(content))
    if normalized_tail is not None:
        parts.append(normalized_tail)
    return parts


def _trimmed_subrange(text: str, start: int, end: int) -> tuple[int, int] | None:
    chunk = text[start:end]
    if not chunk:
        return None
    left = len(chunk) - len(chunk.lstrip())
    right = len(chunk.rstrip())
    if right <= left:
        return None
    return start + left, start + right


def _boundary_kind_for_sentence(text: str) -> SentenceBoundaryKind:
    stripped = text.strip()
    if not stripped:
        return SentenceBoundaryKind.FALLBACK
    if "\n" in stripped:
        return SentenceBoundaryKind.NEWLINE

    cursor = len(stripped) - 1
    while cursor >= 0 and stripped[cursor] in SENTENCE_CLOSERS:
        cursor -= 1
    if cursor >= 0 and stripped[cursor] in SENTENCE_TERMINATORS:
        return SentenceBoundaryKind.TERMINAL
    return SentenceBoundaryKind.FALLBACK


def _split_sentences(text: str) -> list[tuple[int, int, SentenceBoundaryKind]]:
    sentence_ranges: list[tuple[int, int, SentenceBoundaryKind]] = []
    cursor = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char not in SENTENCE_TERMINATORS:
            index += 1
            continue

        end = index + 1
        while end < length and text[end] in SENTENCE_TERMINATORS:
            end += 1
        while end < length and text[end] in SENTENCE_CLOSERS:
            end += 1

        trimmed = _trimmed_subrange(text, cursor, end)
        if trimmed is not None:
            start, finish = trimmed
            sentence_ranges.append((start, finish, _boundary_kind_for_sentence(text[start:finish])))
        cursor = end
        index = end

    tail = _trimmed_subrange(text, cursor, length)
    if tail is not None:
        start, finish = tail
        sentence_ranges.append((start, finish, _boundary_kind_for_sentence(text[start:finish])))
    return sentence_ranges


def _build_chapter_sentence_index(content: str) -> ChapterSentenceIndex:
    paragraph_spans = _split_paragraphs_with_ranges(content)
    paragraphs = [text for _, _, text in paragraph_spans]
    paragraph_char_ranges = [(start, end) for start, end, _ in paragraph_spans]

    sentence_cursor = 1
    paragraph_sentence_ranges: list[tuple[int, int] | None] = []
    all_sentence_spans: list[SentenceSpan] = []
    for paragraph_index, (paragraph_start_offset, _, paragraph) in enumerate(paragraph_spans, start=1):
        paragraph_sentence_spans = _split_sentences(paragraph)
        if not paragraph_sentence_spans:
            paragraph_sentence_ranges.append(None)
            continue
        start = sentence_cursor
        end = sentence_cursor + len(paragraph_sentence_spans) - 1
        for local_start, local_end, boundary_kind in paragraph_sentence_spans:
            all_sentence_spans.append(
                SentenceSpan(
                    sentence_index=sentence_cursor,
                    start_offset=paragraph_start_offset + local_start,
                    end_offset=paragraph_start_offset + local_end,
                    paragraph_index=paragraph_index,
                    boundary_kind=boundary_kind,
                )
            )
            sentence_cursor += 1
        paragraph_sentence_ranges.append((start, end))

    return ChapterSentenceIndex(
        paragraphs=paragraphs,
        paragraph_char_ranges=paragraph_char_ranges,
        paragraph_sentence_ranges=paragraph_sentence_ranges,
        sentence_spans=all_sentence_spans,
    )


def _segment_sentence_and_offset_ranges(
    chapter_index: ChapterSentenceIndex,
    paragraph_range: tuple[int, int],
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    start, end = paragraph_range
    if start < 1 or end < start:
        return None, None
    if end > len(chapter_index.paragraph_char_ranges):
        return None, None

    offset_start = chapter_index.paragraph_char_ranges[start - 1][0]
    offset_end = chapter_index.paragraph_char_ranges[end - 1][1]
    char_offset_range = (offset_start, offset_end) if offset_end > offset_start else None

    start_sentence = chapter_index.paragraph_sentence_ranges[start - 1]
    end_sentence = chapter_index.paragraph_sentence_ranges[end - 1]
    sentence_range: tuple[int, int] | None = None
    if start_sentence is not None and end_sentence is not None:
        sentence_range = (start_sentence[0], end_sentence[1])

    return sentence_range, char_offset_range


def _normalize_scene_type(scene_type: str) -> str:
    return scene_type.strip().lower()


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_uuid4(seed: str) -> str:
    digest = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(digest)))


def _stable_plan_version(*, novel_id: str, created_at: datetime) -> str:
    raw = f"{novel_id}:{created_at.isoformat()}:{SENTENCE_SPLITTER_VERSION}:{WINDOW_PLANNER_VERSION}"
    return f"plan-{_hash_text(raw)[:16]}"


def _expand_sentence_context(
    sentence_range: tuple[int, int] | None,
    *,
    sentence_count: int,
    context_size: int = DEFAULT_WINDOW_CONTEXT_SENTENCES,
) -> tuple[int, int] | None:
    if sentence_range is None or sentence_count <= 0:
        return None
    start, end = sentence_range
    if start < 1 or end < start:
        return None
    return max(1, start - context_size), min(sentence_count, end + context_size)


def _sentence_char_range(
    sentence_spans: Sequence[SentenceSpan],
    sentence_range: tuple[int, int],
) -> tuple[int, int] | None:
    if not sentence_spans:
        return None
    start, end = sentence_range
    if start < 1 or end < start or end > len(sentence_spans):
        return None
    start_span = sentence_spans[start - 1]
    end_span = sentence_spans[end - 1]
    if end_span.end_offset <= start_span.start_offset:
        return None
    return start_span.start_offset, end_span.end_offset


def _window_paragraph_range_from_sentence_range(
    sentence_spans: Sequence[SentenceSpan],
    sentence_range: tuple[int, int],
) -> tuple[int, int] | None:
    start, end = sentence_range
    if start < 1 or end < start or end > len(sentence_spans):
        return None
    start_paragraph = sentence_spans[start - 1].paragraph_index
    end_paragraph = sentence_spans[end - 1].paragraph_index
    if end_paragraph < start_paragraph:
        return None
    return start_paragraph, end_paragraph


def _sentence_count_for_range(
    sentence_spans: Sequence[SentenceSpan],
    sentence_range: tuple[int, int],
) -> int:
    char_range = _sentence_char_range(sentence_spans, sentence_range)
    if char_range is None:
        return 0
    return max(0, char_range[1] - char_range[0])


def _segment_target_fields(original_chars: int, target_ratio: float) -> tuple[int, int, int]:
    target_chars, target_chars_min, target_chars_max = _estimate_target_chars(original_chars, target_ratio)
    return target_chars, target_chars_min, target_chars_max


def _build_scene_hit(
    *,
    scene: object,
    normalized_range: tuple[int, int],
    sentence_range: tuple[int, int] | None,
    char_offset_range: tuple[int, int] | None,
    strategy: RewriteStrategy,
    target_ratio: float,
    suggestion: str,
    source: str,
    confirmed: bool,
) -> _SceneHit | None:
    if sentence_range is None or char_offset_range is None:
        return None
    rewrite_potential = getattr(scene, "rewrite_potential", None)
    priority = int(getattr(rewrite_potential, "priority", 0) or 0)
    hit_source = "manual" if source == "manual" else "auto"
    return _SceneHit(
        scene_type=str(getattr(scene, "scene_type")),
        strategy=strategy,
        target_ratio=target_ratio,
        suggestion=suggestion,
        source=source,
        confirmed=confirmed,
        priority=priority,
        paragraph_range=normalized_range,
        sentence_range=sentence_range,
        char_offset_range=char_offset_range,
        hit_source=hit_source,
    )


def _merge_scene_hits(
    hits: Sequence[_SceneHit],
    *,
    merge_gap_sentences: int = DEFAULT_WINDOW_MERGE_GAP_SENTENCES,
) -> list[_SceneHit]:
    if not hits:
        return []
    sorted_hits = sorted(
        hits,
        key=lambda item: (
            item.sentence_range[0],
            item.sentence_range[1],
            item.paragraph_range[0],
            item.paragraph_range[1],
        ),
    )
    merged: list[_SceneHit] = []
    current = sorted_hits[0]
    for hit in sorted_hits[1:]:
        gap = hit.sentence_range[0] - current.sentence_range[1] - 1
        # merge_gap_sentences=-1 means only merge real overlap; adjacent
        # sentence hits (gap=0) remain independent rewrite windows.
        if gap <= merge_gap_sentences:
            chosen = current if current.priority >= hit.priority else hit
            current = _SceneHit(
                scene_type=chosen.scene_type,
                strategy=chosen.strategy,
                target_ratio=chosen.target_ratio,
                suggestion=chosen.suggestion,
                source=chosen.source,
                confirmed=chosen.confirmed,
                priority=max(current.priority, hit.priority),
                paragraph_range=(
                    min(current.paragraph_range[0], hit.paragraph_range[0]),
                    max(current.paragraph_range[1], hit.paragraph_range[1]),
                ),
                sentence_range=(
                    min(current.sentence_range[0], hit.sentence_range[0]),
                    max(current.sentence_range[1], hit.sentence_range[1]),
                ),
                char_offset_range=(
                    min(current.char_offset_range[0], hit.char_offset_range[0]),
                    max(current.char_offset_range[1], hit.char_offset_range[1]),
                ),
                hit_source=chosen.hit_source,
            )
            continue
        merged.append(current)
        current = hit
    merged.append(current)
    return merged


def _split_hit_by_sentence_budget(
    hit: _SceneHit,
    *,
    sentence_spans: Sequence[SentenceSpan],
    max_window_chars: int = DEFAULT_WINDOW_MAX_CHARS,
    min_split_chars: int = DEFAULT_WINDOW_MIN_SPLIT_CHARS,
) -> list[_SceneHit]:
    start_sentence, end_sentence = hit.sentence_range
    if start_sentence < 1 or end_sentence < start_sentence:
        return [hit]

    max_chars = max(1, int(max_window_chars))
    min_chars = max(1, min(int(min_split_chars), max_chars))
    total_chars = _sentence_count_for_range(sentence_spans, hit.sentence_range)
    if total_chars <= max_chars:
        return [hit]

    chunks: list[tuple[int, int]] = []
    chunk_start = start_sentence
    chunk_chars = 0
    sentence_cursor = start_sentence

    while sentence_cursor <= end_sentence:
        span = sentence_spans[sentence_cursor - 1]
        span_chars = max(1, span.end_offset - span.start_offset)
        would_exceed = chunk_chars > 0 and (chunk_chars + span_chars > max_chars)
        has_min_budget = chunk_chars >= min_chars
        if would_exceed and has_min_budget:
            chunks.append((chunk_start, sentence_cursor - 1))
            chunk_start = sentence_cursor
            chunk_chars = 0
        chunk_chars += span_chars
        sentence_cursor += 1

    if chunk_start <= end_sentence:
        chunks.append((chunk_start, end_sentence))

    split_hits: list[_SceneHit] = []
    for sentence_range in chunks:
        char_range = _sentence_char_range(sentence_spans, sentence_range)
        paragraph_range = _window_paragraph_range_from_sentence_range(sentence_spans, sentence_range)
        if char_range is None or paragraph_range is None:
            continue
        split_hits.append(
            _SceneHit(
                scene_type=hit.scene_type,
                strategy=hit.strategy,
                target_ratio=hit.target_ratio,
                suggestion=hit.suggestion,
                source=hit.source,
                confirmed=hit.confirmed,
                priority=hit.priority,
                paragraph_range=paragraph_range,
                sentence_range=sentence_range,
                char_offset_range=char_range,
                hit_source=hit.hit_source,
            )
        )
    return split_hits or [hit]


def _segment_char_offset_range(
    chapter_sentence_index: ChapterSentenceIndex,
    segment: RewriteSegment,
) -> tuple[int, int] | None:
    if segment.char_offset_range is not None:
        return segment.char_offset_range
    start, end = segment.paragraph_range
    if start < 1 or end > len(chapter_sentence_index.paragraph_char_ranges) or end < start:
        return None
    range_start = chapter_sentence_index.paragraph_char_ranges[start - 1][0]
    range_end = chapter_sentence_index.paragraph_char_ranges[end - 1][1]
    if range_end <= range_start:
        return None
    return range_start, range_end


def _window_id(
    *,
    start_offset: int,
    end_offset: int,
    plan_version: str | None,
    source_fingerprint: str | None,
) -> str:
    stable_source = f"{start_offset}:{end_offset}:{plan_version or ''}:{source_fingerprint or ''}"
    return f"window-{_hash_text(stable_source)[:20]}"


def _segment_with_windows(
    segment: RewriteSegment,
    *,
    chapter_index: int,
    chapter_sentence_index: ChapterSentenceIndex,
    source_fingerprint: str,
    plan_version: str,
    context_range_override: tuple[int, int] | None = None,
) -> RewriteSegment:
    char_range = _segment_char_offset_range(chapter_sentence_index, segment)
    if char_range is None:
        return segment.model_copy(update={"source_fingerprint": source_fingerprint, "plan_version": plan_version})

    start_offset, end_offset = char_range
    context_range = context_range_override or _expand_sentence_context(
        segment.sentence_range,
        sentence_count=len(chapter_sentence_index.sentence_spans),
        context_size=DEFAULT_WINDOW_CONTEXT_SENTENCES,
    )
    window = RewriteWindow(
        window_id=_window_id(
            start_offset=start_offset,
            end_offset=end_offset,
            plan_version=plan_version,
            source_fingerprint=source_fingerprint,
        ),
        segment_id=segment.segment_id,
        chapter_index=chapter_index,
        start_offset=start_offset,
        end_offset=end_offset,
        hit_sentence_range=segment.sentence_range,
        context_sentence_range=context_range,
        target_chars=segment.target_chars,
        target_chars_min=segment.target_chars_min,
        target_chars_max=segment.target_chars_max,
        source_fingerprint=source_fingerprint,
        plan_version=plan_version,
    )
    return segment.model_copy(
        update={
            "char_offset_range": (start_offset, end_offset),
            "rewrite_windows": [window],
            "source_fingerprint": source_fingerprint,
            "plan_version": plan_version,
        }
    )


def _chapter_paragraph_text(chapter: Chapter, paragraph_range: tuple[int, int]) -> tuple[list[str], str]:
    paragraphs = _split_paragraphs(chapter.content)
    start, end = paragraph_range
    if start < 1 or end < start:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid paragraph range `{paragraph_range}`",
            details={"paragraph_range": paragraph_range},
        )
    if end > len(paragraphs):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "paragraph_range is outside the chapter paragraph count",
            details={"paragraph_range": paragraph_range, "paragraph_count": len(paragraphs)},
        )
    selected = paragraphs[start - 1 : end]
    return paragraphs, "\n\n".join(selected)


def build_anchor(
    chapter: Chapter,
    paragraph_range: tuple[int, int],
    *,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
) -> RewriteAnchor:
    paragraphs, selected_text = _chapter_paragraph_text(chapter, paragraph_range)
    start, end = paragraph_range
    window_start = max(1, start - context_window_size)
    window_end = min(len(paragraphs), end + context_window_size)
    context_text = "\n\n".join(paragraphs[window_start - 1 : window_end])

    return RewriteAnchor(
        paragraph_start_hash=_hash_text(paragraphs[start - 1]),
        paragraph_end_hash=_hash_text(paragraphs[end - 1]),
        range_text_hash=_hash_text(selected_text),
        context_window_hash=_hash_text(context_text),
        paragraph_count_snapshot=len(paragraphs),
    )


def _select_rewrite_rule(scene_type: str, rewrite_rules: Sequence[RewriteRule]) -> RewriteRule | None:
    normalized = _normalize_scene_type(scene_type)

    # 1. Exact match (case-insensitive)
    exact_candidates = [
        rule
        for rule in rewrite_rules
        if rule.enabled and _normalize_scene_type(rule.scene_type) == normalized
    ]
    if exact_candidates:
        return sorted(exact_candidates, key=lambda rule: (rule.priority, rule.scene_type, rule.id))[0]

    # 2. Fuzzy fallback: substring containment
    fuzzy_candidates = [
        rule
        for rule in rewrite_rules
        if rule.enabled and (
            _normalize_scene_type(rule.scene_type) in normalized
            or normalized in _normalize_scene_type(rule.scene_type)
        )
    ]
    if fuzzy_candidates:
        winner = sorted(fuzzy_candidates, key=lambda rule: (rule.priority, rule.scene_type, rule.id))[0]
        logger.info(
            "Fuzzy scene_type match: '%s' matched rule '%s' via substring containment",
            scene_type, winner.scene_type,
        )
        return winner

    return None


def _is_rewrite_applicable(scene: object, rule: RewriteRule) -> bool:
    rewrite_potential = getattr(scene, "rewrite_potential", None)
    if rewrite_potential is None:
        return rule.primary_strategy != RewriteStrategy.PRESERVE.value

    if rule.primary_strategy == RewriteStrategy.PRESERVE.value:
        return False
    if rule.primary_strategy == RewriteStrategy.EXPAND.value:
        return bool(getattr(rewrite_potential, "expandable", False))
    return bool(getattr(rewrite_potential, "rewritable", False))


def _estimate_target_chars(original_chars: int, target_ratio: float) -> tuple[int, int, int]:
    target_chars = max(1, round(original_chars * target_ratio))
    buffer = max(1, round(target_chars * DEFAULT_CHAR_BUFFER_RATIO))
    return target_chars, max(1, target_chars - buffer), target_chars + buffer


def _normalize_paragraph_range(
    paragraph_range: tuple[int, int],
    *,
    paragraph_count: int,
    source_upper_bound: int | None = None,
) -> tuple[int, int]:
    if paragraph_count <= 0:
        return paragraph_range

    start, end = paragraph_range
    start = max(1, int(start))
    end = max(start, int(end))

    if end <= paragraph_count:
        return start, end

    if source_upper_bound is not None and source_upper_bound > paragraph_count:
        mapped_start = int(math.floor((start - 1) * paragraph_count / source_upper_bound)) + 1
        mapped_end = int(math.ceil(end * paragraph_count / source_upper_bound))
        mapped_start = max(1, min(paragraph_count, mapped_start))
        mapped_end = max(mapped_start, min(paragraph_count, mapped_end))
        return mapped_start, mapped_end

    clamped_start = max(1, min(paragraph_count, start))
    clamped_end = max(clamped_start, min(paragraph_count, end))
    return clamped_start, clamped_end


def _coerce_int_pair(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        start = int(value[0])
        end = int(value[1])
    except (TypeError, ValueError):
        return None
    return start, end


def _clamp_sentence_range(
    sentence_range: tuple[int, int] | None,
    *,
    sentence_count: int,
) -> tuple[int, int] | None:
    if sentence_range is None or sentence_count <= 0:
        return None
    start, end = sentence_range
    start = max(1, min(sentence_count, int(start)))
    end = max(start, min(sentence_count, int(end)))
    return start, end


def _should_use_sentence_scale_mapping(*, paragraph_count: int, source_upper_bound: int) -> bool:
    if paragraph_count <= 0 or source_upper_bound <= paragraph_count:
        return False
    threshold = max(
        int(math.ceil(paragraph_count * SOURCE_PARAGRAPH_MISMATCH_RATIO_THRESHOLD)),
        paragraph_count + SOURCE_PARAGRAPH_MISMATCH_MIN_DELTA,
    )
    return source_upper_bound >= threshold


def _normalize_sentence_range_from_source(
    paragraph_range: tuple[int, int],
    *,
    sentence_count: int,
    source_upper_bound: int,
) -> tuple[int, int] | None:
    if sentence_count <= 0:
        return None

    start, end = paragraph_range
    start = max(1, int(start))
    end = max(start, int(end))

    upper = max(1, int(source_upper_bound or end))
    mapped_start = int(math.floor((start - 1) * sentence_count / upper)) + 1
    mapped_end = int(math.ceil(end * sentence_count / upper))

    mapped_start = max(1, min(sentence_count, mapped_start))
    mapped_end = max(mapped_start, min(sentence_count, mapped_end))
    return mapped_start, mapped_end


def _sentence_range_from_char_offsets(
    sentence_spans: Sequence[SentenceSpan],
    char_offset_range: tuple[int, int],
) -> tuple[int, int] | None:
    start, end = char_offset_range
    if start < 0 or end <= start:
        return None
    hit_sentence_indexes: list[int] = []
    for span in sentence_spans:
        if span.end_offset <= start:
            continue
        if span.start_offset >= end:
            break
        hit_sentence_indexes.append(span.sentence_index)
    if not hit_sentence_indexes:
        return None
    return min(hit_sentence_indexes), max(hit_sentence_indexes)


def _evidence_fragments(evidence_text: str) -> list[str]:
    raw = evidence_text.strip()
    if not raw:
        return []

    fragments: list[str] = []
    seen: set[str] = set()
    for part in RULE_HIT_EVIDENCE_SPLIT_RE.split(raw):
        cleaned = part.strip().strip("\"'“”‘’`()[]{}<>《》")
        if len(cleaned) < RULE_HIT_EVIDENCE_MIN_FRAGMENT_CHARS:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        fragments.append(cleaned)

    if fragments:
        return fragments

    fallback = raw.strip().strip("\"'“”‘’`()[]{}<>《》")
    if len(fallback) >= RULE_HIT_EVIDENCE_MIN_FRAGMENT_CHARS:
        return [fallback]
    return []


def _all_literal_match_spans(text: str, fragment: str, *, limit: int = 6) -> list[tuple[int, int]]:
    if not text or not fragment:
        return []
    matches: list[tuple[int, int]] = []
    cursor = 0
    while len(matches) < limit:
        start = text.find(fragment, cursor)
        if start < 0:
            break
        end = start + len(fragment)
        matches.append((start, end))
        cursor = start + 1
    return matches


def _ground_scene_range_from_rule_hits(
    scene: object,
    *,
    chapter_text: str,
    chapter_sentence_index: ChapterSentenceIndex,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
    sentence_spans = chapter_sentence_index.sentence_spans
    if not chapter_text or not sentence_spans:
        return None

    raw_hits = list(getattr(scene, "rule_hits", None) or [])
    if not raw_hits:
        return None

    clusters: list[dict[str, object]] = []
    for hit_index, rule_hit in enumerate(raw_hits):
        evidence_text = str(getattr(rule_hit, "evidence_text", "") or "").strip()
        if not evidence_text:
            continue
        for fragment in _evidence_fragments(evidence_text):
            for start, end in _all_literal_match_spans(chapter_text, fragment):
                sentence_range = _sentence_range_from_char_offsets(sentence_spans, (start, end))
                if sentence_range is None:
                    continue
                weight = len(fragment)
                merged = False
                for cluster in clusters:
                    cluster_sentence_range = cluster["sentence_range"]
                    if not isinstance(cluster_sentence_range, tuple):
                        continue
                    cluster_start = int(cluster_sentence_range[0])
                    cluster_end = int(cluster_sentence_range[1])
                    separated = (
                        sentence_range[0] > cluster_end + RULE_HIT_CLUSTER_GAP_SENTENCES + 1
                        or cluster_start > sentence_range[1] + RULE_HIT_CLUSTER_GAP_SENTENCES + 1
                    )
                    if separated:
                        continue
                    cluster["sentence_range"] = (
                        min(cluster_start, sentence_range[0]),
                        max(cluster_end, sentence_range[1]),
                    )
                    cluster["char_offset_range"] = (
                        min(int(cluster["char_offset_range"][0]), start),
                        max(int(cluster["char_offset_range"][1]), end),
                    )
                    cluster["weight"] = int(cluster["weight"]) + weight
                    hit_ids = cluster["hit_ids"]
                    if isinstance(hit_ids, set):
                        hit_ids.add(hit_index)
                    merged = True
                    break
                if merged:
                    continue
                clusters.append(
                    {
                        "sentence_range": sentence_range,
                        "char_offset_range": (start, end),
                        "weight": weight,
                        "hit_ids": {hit_index},
                    }
                )

    if not clusters:
        return None

    best = sorted(
        clusters,
        key=lambda item: (
            -len(item["hit_ids"]) if isinstance(item["hit_ids"], set) else 0,
            -int(item["weight"]),
            (int(item["sentence_range"][1]) - int(item["sentence_range"][0]) + 1),
            int(item["sentence_range"][0]),
        ),
    )[0]
    best_sentence_range = best["sentence_range"]
    if not isinstance(best_sentence_range, tuple):
        return None
    paragraph_range = _window_paragraph_range_from_sentence_range(sentence_spans, best_sentence_range)
    char_offset_range = _sentence_char_range(sentence_spans, best_sentence_range)
    if paragraph_range is None or char_offset_range is None:
        return None
    return paragraph_range, best_sentence_range, char_offset_range


def _resolve_scene_ranges(
    scene: object,
    *,
    chapter_text: str,
    chapter_sentence_index: ChapterSentenceIndex,
    paragraph_count: int,
    source_upper_bound: int,
    chapter_length: int,
) -> tuple[tuple[int, int], tuple[int, int] | None, tuple[int, int] | None]:
    raw_paragraph_range = tuple(getattr(scene, "paragraph_range"))
    sentence_count = len(chapter_sentence_index.sentence_spans)

    grounded_from_hits = _ground_scene_range_from_rule_hits(
        scene,
        chapter_text=chapter_text,
        chapter_sentence_index=chapter_sentence_index,
    )

    scene_sentence_range = _clamp_sentence_range(
        _coerce_int_pair(getattr(scene, "sentence_range", None)),
        sentence_count=sentence_count,
    )
    if grounded_from_hits is not None:
        return grounded_from_hits
    if scene_sentence_range is not None:
        paragraph_range = _window_paragraph_range_from_sentence_range(
            chapter_sentence_index.sentence_spans,
            scene_sentence_range,
        )
        char_offset_range = _sentence_char_range(chapter_sentence_index.sentence_spans, scene_sentence_range)
        if paragraph_range is not None and char_offset_range is not None:
            return paragraph_range, scene_sentence_range, char_offset_range

    scene_char_range = _coerce_int_pair(getattr(scene, "char_offset_range", None))
    if scene_char_range is not None:
        char_start = max(0, scene_char_range[0])
        char_end = min(chapter_length, scene_char_range[1])
        if char_end > char_start:
            clamped_char_range = (char_start, char_end)
            sentence_range_from_char = _sentence_range_from_char_offsets(
                chapter_sentence_index.sentence_spans,
                clamped_char_range,
            )
            if sentence_range_from_char is not None:
                paragraph_range = _window_paragraph_range_from_sentence_range(
                    chapter_sentence_index.sentence_spans,
                    sentence_range_from_char,
                )
                if paragraph_range is not None:
                    return paragraph_range, sentence_range_from_char, clamped_char_range

    if _should_use_sentence_scale_mapping(
        paragraph_count=paragraph_count,
        source_upper_bound=source_upper_bound,
    ):
        sentence_range = _normalize_sentence_range_from_source(
            raw_paragraph_range,
            sentence_count=sentence_count,
            source_upper_bound=source_upper_bound,
        )
        if sentence_range is not None:
            paragraph_range = _window_paragraph_range_from_sentence_range(
                chapter_sentence_index.sentence_spans,
                sentence_range,
            )
            char_offset_range = _sentence_char_range(chapter_sentence_index.sentence_spans, sentence_range)
            if paragraph_range is not None and char_offset_range is not None:
                return paragraph_range, sentence_range, char_offset_range

    normalized_paragraph_range = _normalize_paragraph_range(
        raw_paragraph_range,
        paragraph_count=paragraph_count,
        source_upper_bound=source_upper_bound,
    )
    sentence_range, char_offset_range = _segment_sentence_and_offset_ranges(
        chapter_sentence_index,
        normalized_paragraph_range,
    )
    return normalized_paragraph_range, sentence_range, char_offset_range


def _is_heading_like_paragraph(text: str) -> bool:
    candidate = text.strip().replace("\u3000", " ")
    if not candidate:
        return False
    if len(candidate) > 40:
        return False
    if re.search(r"[。！？!?；;，,:：]", candidate):
        return False
    return CHAPTER_HEADING_RE.match(candidate) is not None


def _is_heading_only_range(chapter_index: ChapterSentenceIndex, paragraph_range: tuple[int, int]) -> bool:
    start, end = paragraph_range
    if start != end:
        return False
    if start < 1 or end > len(chapter_index.paragraphs):
        return False
    return _is_heading_like_paragraph(chapter_index.paragraphs[start - 1])


def build_segment_from_scene(
    chapter: Chapter,
    scene: object,
    rewrite_rule: RewriteRule,
    *,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
    paragraph_range_override: tuple[int, int] | None = None,
    sentence_range_override: tuple[int, int] | None = None,
    char_offset_range_override: tuple[int, int] | None = None,
) -> RewriteSegment:
    paragraph_range = paragraph_range_override or tuple(getattr(scene, "paragraph_range"))
    anchor = build_anchor(chapter, paragraph_range, context_window_size=context_window_size)
    _, range_text = _chapter_paragraph_text(chapter, paragraph_range)
    original_chars = len(range_text)
    target_chars, target_chars_min, target_chars_max = _estimate_target_chars(original_chars, rewrite_rule.target_ratio)
    rewrite_potential = getattr(scene, "rewrite_potential", None)
    suggestion = str(
        getattr(rewrite_potential, "suggestion", "") or f"{rewrite_rule.scene_type} 场景建议按 {rewrite_rule.primary_strategy} 处理"
    )

    return RewriteSegment(
        paragraph_range=paragraph_range,
        sentence_range=sentence_range_override,
        char_offset_range=char_offset_range_override,
        anchor=anchor,
        scene_type=str(getattr(scene, "scene_type")),
        original_chars=original_chars,
        strategy=RewriteStrategy(rewrite_rule.primary_strategy),
        target_ratio=rewrite_rule.target_ratio,
        target_chars=target_chars,
        target_chars_min=target_chars_min,
        target_chars_max=target_chars_max,
        suggestion=suggestion,
        source="auto",
        confirmed=False,
    )


def build_fallback_segment_from_scene(
    chapter: Chapter,
    scene: object,
    *,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
    paragraph_range_override: tuple[int, int] | None = None,
    sentence_range_override: tuple[int, int] | None = None,
    char_offset_range_override: tuple[int, int] | None = None,
) -> RewriteSegment | None:
    paragraph_range = paragraph_range_override or tuple(getattr(scene, "paragraph_range"))
    anchor = build_anchor(chapter, paragraph_range, context_window_size=context_window_size)
    _, range_text = _chapter_paragraph_text(chapter, paragraph_range)
    original_chars = len(range_text)
    rewrite_potential = getattr(scene, "rewrite_potential", None)

    expandable = bool(getattr(rewrite_potential, "expandable", True)) if rewrite_potential is not None else True
    rewritable = bool(getattr(rewrite_potential, "rewritable", True)) if rewrite_potential is not None else True
    if not expandable and not rewritable:
        return None

    strategy = RewriteStrategy.EXPAND if expandable else RewriteStrategy.REWRITE
    target_ratio = DEFAULT_FALLBACK_EXPAND_RATIO if strategy == RewriteStrategy.EXPAND else DEFAULT_FALLBACK_REWRITE_RATIO
    target_chars, target_chars_min, target_chars_max = _estimate_target_chars(original_chars, target_ratio)
    suggestion = str(
        getattr(rewrite_potential, "suggestion", "")
        or ("该段以拓展式改写为主，保持剧情方向不偏移" if strategy == RewriteStrategy.EXPAND else "该段执行中性改写，保持信息完整与语义等价")
    )

    return RewriteSegment(
        paragraph_range=paragraph_range,
        sentence_range=sentence_range_override,
        char_offset_range=char_offset_range_override,
        anchor=anchor,
        scene_type=str(getattr(scene, "scene_type")),
        original_chars=original_chars,
        strategy=strategy,
        target_ratio=target_ratio,
        target_chars=target_chars,
        target_chars_min=target_chars_min,
        target_chars_max=target_chars_max,
        suggestion=suggestion,
        source="auto",
        confirmed=False,
    )


def _build_segment_from_scene_hit(
    chapter: Chapter,
    hit: _SceneHit,
    *,
    context_window_size: int,
    chapter_sentence_index: ChapterSentenceIndex,
    source_fingerprint: str,
    plan_version: str,
) -> RewriteSegment | None:
    start_offset, end_offset = hit.char_offset_range
    if start_offset < 0 or end_offset <= start_offset or end_offset > len(chapter.content):
        return None

    paragraph_range = hit.paragraph_range
    try:
        anchor = build_anchor(chapter, paragraph_range, context_window_size=context_window_size)
    except AppError:
        return None

    original_text = chapter.content[start_offset:end_offset]
    original_chars = len(original_text.strip()) or len(original_text)
    if original_chars <= 0:
        return None

    target_chars, target_chars_min, target_chars_max = _segment_target_fields(original_chars, hit.target_ratio)
    segment_identity = (
        f"{chapter.id}:{plan_version}:{source_fingerprint}:"
        f"{hit.scene_type}:{hit.strategy.value}:{hit.sentence_range[0]}:{hit.sentence_range[1]}"
    )
    segment = RewriteSegment(
        segment_id=_stable_uuid4(segment_identity),
        paragraph_range=paragraph_range,
        sentence_range=hit.sentence_range,
        char_offset_range=hit.char_offset_range,
        anchor=anchor,
        scene_type=hit.scene_type,
        original_chars=original_chars,
        strategy=hit.strategy,
        target_ratio=hit.target_ratio,
        target_chars=target_chars,
        target_chars_min=target_chars_min,
        target_chars_max=target_chars_max,
        suggestion=hit.suggestion,
        source=hit.source,
        confirmed=hit.confirmed,
    )
    context_range = _expand_sentence_context(
        hit.sentence_range,
        sentence_count=len(chapter_sentence_index.sentence_spans),
        context_size=DEFAULT_WINDOW_CONTEXT_SENTENCES,
    )
    return _segment_with_windows(
        segment,
        chapter_index=chapter.index,
        chapter_sentence_index=chapter_sentence_index,
        source_fingerprint=source_fingerprint,
        plan_version=plan_version,
        context_range_override=context_range,
    )


def build_chapter_mark_plan(
    chapter: Chapter,
    analysis: ChapterAnalysis,
    rewrite_rules: Sequence[RewriteRule],
    *,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
    plan_version: str | None = None,
) -> RewriteChapterPlan:
    chapter_sentence_index = _build_chapter_sentence_index(chapter.content)
    chapter_fingerprint = _hash_text(chapter.content)
    resolved_plan_version = plan_version or _hash_text(f"{chapter.id}:{chapter_fingerprint}")[:16]
    paragraph_count = len(chapter_sentence_index.paragraphs)
    chapter_length = len(chapter.content)
    source_upper_bound = max(
        (int(getattr(scene, "paragraph_range")[1]) for scene in analysis.scenes),
        default=paragraph_count,
    )
    raw_hits: list[_SceneHit] = []
    for scene in analysis.scenes:
        try:
            normalized_range, sentence_range, char_offset_range = _resolve_scene_ranges(
                scene,
                chapter_text=chapter.content,
                chapter_sentence_index=chapter_sentence_index,
                paragraph_count=paragraph_count,
                source_upper_bound=source_upper_bound,
                chapter_length=chapter_length,
            )
            if _is_heading_only_range(chapter_sentence_index, normalized_range):
                # Chapter headings should stay stable and are not valid rewrite targets.
                continue
            rule = _select_rewrite_rule(scene.scene_type, rewrite_rules)
            if rule is None:
                rewrite_potential = getattr(scene, "rewrite_potential", None)
                fallback_expandable = bool(getattr(rewrite_potential, "expandable", True))
                fallback_rewritable = bool(getattr(rewrite_potential, "rewritable", True))
                if not fallback_expandable and not fallback_rewritable:
                    continue
                fallback_segment = _build_scene_hit(
                    scene=scene,
                    normalized_range=normalized_range,
                    sentence_range=sentence_range,
                    char_offset_range=char_offset_range,
                    strategy=(
                        RewriteStrategy.EXPAND
                        if fallback_expandable
                        else RewriteStrategy.REWRITE
                    ),
                    target_ratio=(
                        DEFAULT_FALLBACK_EXPAND_RATIO
                        if fallback_expandable
                        else DEFAULT_FALLBACK_REWRITE_RATIO
                    ),
                    suggestion=str(
                        getattr(getattr(scene, "rewrite_potential", None), "suggestion", "")
                        or "自动回退策略：保持剧情语义并补足信息密度"
                    ),
                    source="auto",
                    confirmed=False,
                )
                if fallback_segment is not None:
                    raw_hits.append(fallback_segment)
                continue
            if not _is_rewrite_applicable(scene, rule):
                continue
            hit = _build_scene_hit(
                scene=scene,
                normalized_range=normalized_range,
                sentence_range=sentence_range,
                char_offset_range=char_offset_range,
                strategy=RewriteStrategy(rule.primary_strategy),
                target_ratio=rule.target_ratio,
                suggestion=str(
                    getattr(getattr(scene, "rewrite_potential", None), "suggestion", "")
                    or f"{rule.scene_type} 场景建议按 {rule.primary_strategy} 处理"
                ),
                source="auto",
                confirmed=False,
            )
            if hit is None:
                continue
            raw_hits.append(hit)
        except AppError as exc:
            # Ignore malformed scene ranges from analysis output and keep other
            # valid segments runnable for the chapter.
            if exc.code == ErrorCode.VALIDATION_ERROR:
                continue
            raise

    merged_hits = _merge_scene_hits(raw_hits)
    window_hits: list[_SceneHit] = []
    for hit in merged_hits:
        window_hits.extend(
            _split_hit_by_sentence_budget(
                hit,
                sentence_spans=chapter_sentence_index.sentence_spans,
                max_window_chars=DEFAULT_WINDOW_MAX_CHARS,
                min_split_chars=DEFAULT_WINDOW_MIN_SPLIT_CHARS,
            )
        )

    segments: list[RewriteSegment] = []
    for hit in window_hits:
        # Recompute paragraph range from sentence bounds after budget split to
        # avoid spanning unrelated paragraphs when one scene is split.
        recomputed_paragraph = _window_paragraph_range_from_sentence_range(
            chapter_sentence_index.sentence_spans,
            hit.sentence_range,
        )
        recomputed_char = _sentence_char_range(chapter_sentence_index.sentence_spans, hit.sentence_range)
        if recomputed_paragraph is None or recomputed_char is None:
            continue
        segment = _build_segment_from_scene_hit(
            chapter,
            _SceneHit(
                scene_type=hit.scene_type,
                strategy=hit.strategy,
                target_ratio=hit.target_ratio,
                suggestion=hit.suggestion,
                source=hit.source,
                confirmed=hit.confirmed,
                priority=hit.priority,
                paragraph_range=recomputed_paragraph,
                sentence_range=hit.sentence_range,
                char_offset_range=recomputed_char,
                hit_source=hit.hit_source,
            ),
            context_window_size=context_window_size,
            chapter_sentence_index=chapter_sentence_index,
            source_fingerprint=chapter_fingerprint,
            plan_version=resolved_plan_version,
        )
        if segment is not None:
            segments.append(segment)

    deduped_segments, dropped_count = _drop_overlapping_segments(segments)
    if dropped_count:
        logger.warning(
            "Chapter %d: dropped %d overlapping segment(s)", chapter.index, dropped_count,
        )

    return RewriteChapterPlan(
        chapter_index=chapter.index,
        sentence_spans=chapter_sentence_index.sentence_spans,
        sentence_splitter_version=SENTENCE_SPLITTER_VERSION,
        window_planner_version=WINDOW_PLANNER_VERSION,
        plan_version=resolved_plan_version,
        source_fingerprint=chapter_fingerprint,
        segments=deduped_segments,
    )


def build_rewrite_plan(
    novel_id: str,
    chapters: Sequence[Chapter],
    analyses_by_chapter: Mapping[int, ChapterAnalysis],
    rewrite_rules: Sequence[RewriteRule],
    *,
    created_at: datetime | None = None,
    context_window_size: int = DEFAULT_CONTEXT_WINDOW_SIZE,
) -> RewritePlan:
    created = created_at or datetime.now(timezone.utc)
    plan_version = _stable_plan_version(novel_id=novel_id, created_at=created)
    source_fingerprint = _hash_text(
        "|".join(f"{chapter.index}:{_hash_text(chapter.content)}" for chapter in sorted(chapters, key=lambda item: item.index))
    )
    chapter_plans: list[RewriteChapterPlan] = []
    for chapter in chapters:
        analysis = analyses_by_chapter.get(chapter.index)
        if analysis is None:
            continue
        plan = build_chapter_mark_plan(
            chapter,
            analysis,
            rewrite_rules,
            context_window_size=context_window_size,
            plan_version=plan_version,
        )
        chapter_plans.append(plan)

    estimate = estimate_rewrite_plan(chapter_plans)
    return RewritePlan(
        novel_id=novel_id,
        created_at=created,
        sentence_splitter_version=SENTENCE_SPLITTER_VERSION,
        window_planner_version=WINDOW_PLANNER_VERSION,
        plan_version=plan_version,
        source_fingerprint=source_fingerprint,
        total_marked=estimate.total_marked,
        estimated_llm_calls=estimate.estimated_llm_calls,
        estimated_added_chars=estimate.estimated_added_chars,
        chapters=chapter_plans,
    )


def estimate_rewrite_plan(
    plan_or_chapters: RewritePlan | Sequence[RewriteChapterPlan],
    *,
    seconds_per_llm_call: float = DEFAULT_SECONDS_PER_LLM_CALL,
) -> RewritePlanEstimate:
    chapters = plan_or_chapters.chapters if isinstance(plan_or_chapters, RewritePlan) else plan_or_chapters
    total_marked = sum(len(chapter.segments) for chapter in chapters)
    estimated_llm_calls = sum(1 for chapter in chapters for segment in chapter.segments if segment.strategy != RewriteStrategy.PRESERVE)
    estimated_added_chars = sum(
        max(0, segment.target_chars - segment.original_chars)
        for chapter in chapters
        for segment in chapter.segments
        if segment.strategy != RewriteStrategy.PRESERVE
    )
    return RewritePlanEstimate(
        total_marked=total_marked,
        estimated_llm_calls=estimated_llm_calls,
        estimated_added_chars=estimated_added_chars,
        estimated_duration_seconds=estimated_llm_calls * seconds_per_llm_call,
    )


def _normalize_manual_segment(segment: RewriteSegment) -> RewriteSegment:
    return segment.model_copy(update={"source": "manual", "confirmed": True})


def _sorted_segments(segments: Sequence[RewriteSegment]) -> list[RewriteSegment]:
    def _char_start(item: RewriteSegment) -> int:
        if item.char_offset_range is None:
            return item.paragraph_range[0] * 1_000_000
        return item.char_offset_range[0]

    return sorted(
        segments,
        key=lambda item: (_char_start(item), item.paragraph_range[0], item.paragraph_range[1], item.segment_id),
    )


def _drop_overlapping_segments(segments: Sequence[RewriteSegment]) -> tuple[list[RewriteSegment], int]:
    selected: list[RewriteSegment] = []
    dropped_count = 0
    for segment in _sorted_segments(segments):
        char_range = segment.char_offset_range
        if selected:
            prev = selected[-1]
            prev_char_range = prev.char_offset_range
            if char_range is not None and prev_char_range is not None:
                if char_range[0] < prev_char_range[1]:
                    # Keep first hit to avoid hard failures on noisy/duplicate
                    # scene detection or aggressive context expansion.
                    logger.warning(
                        "Dropping overlapping segment %s (scene=%s, range=%s) — overlaps with %s (scene=%s, range=%s)",
                        segment.segment_id[:12], segment.scene_type, segment.char_offset_range,
                        prev.segment_id[:12], prev.scene_type, prev.char_offset_range,
                    )
                    dropped_count += 1
                    continue
            else:
                start, end = segment.paragraph_range
                _, prev_end = prev.paragraph_range
                if start <= prev_end:
                    logger.warning(
                        "Dropping overlapping segment %s (scene=%s, range=%s) — overlaps with %s (scene=%s, range=%s)",
                        segment.segment_id[:12], segment.scene_type, segment.char_offset_range,
                        prev.segment_id[:12], prev.scene_type, prev.char_offset_range,
                    )
                    dropped_count += 1
                    continue
        selected.append(segment)
    return selected, dropped_count


def merge_chapter_segments(
    existing: RewriteChapterPlan,
    manual_segments: Sequence[RewriteSegment],
) -> RewriteChapterPlan:
    segment_map = {segment.segment_id: segment for segment in existing.segments}
    for segment in manual_segments:
        normalized = _normalize_manual_segment(segment)
        segment_map[normalized.segment_id] = normalized
    return RewriteChapterPlan(chapter_index=existing.chapter_index, segments=_sorted_segments(segment_map.values()))


def replace_chapter_segments(
    existing: RewriteChapterPlan,
    manual_segments: Sequence[RewriteSegment],
) -> RewriteChapterPlan:
    normalized = [_normalize_manual_segment(segment) for segment in manual_segments]
    return RewriteChapterPlan(chapter_index=existing.chapter_index, segments=_sorted_segments(normalized))


def merge_manual_segments(
    plan: RewritePlan,
    chapter_index: int,
    manual_segments: Sequence[RewriteSegment],
) -> RewritePlan:
    return _replace_chapter_plan(plan, chapter_index, manual_segments, mode="merge")


def replace_manual_segments(
    plan: RewritePlan,
    chapter_index: int,
    manual_segments: Sequence[RewriteSegment],
) -> RewritePlan:
    return _replace_chapter_plan(plan, chapter_index, manual_segments, mode="replace")


def _replace_chapter_plan(
    plan: RewritePlan,
    chapter_index: int,
    manual_segments: Sequence[RewriteSegment],
    *,
    mode: str,
) -> RewritePlan:
    chapters: list[RewriteChapterPlan] = []
    replaced = False
    for chapter in plan.chapters:
        if chapter.chapter_index != chapter_index:
            chapters.append(chapter)
            continue
        replaced = True
        if mode == "merge":
            chapters.append(merge_chapter_segments(chapter, manual_segments))
        elif mode == "replace":
            chapters.append(replace_chapter_segments(chapter, manual_segments))
        else:
            raise AppError(ErrorCode.INTERNAL_ERROR, f"Unsupported replacement mode `{mode}`")

    if not replaced:
        raise AppError(ErrorCode.NOT_FOUND, f"Chapter `{chapter_index}` not found in rewrite plan")

    estimate = estimate_rewrite_plan(chapters)
    return plan.model_copy(
        update={
            "total_marked": estimate.total_marked,
            "estimated_llm_calls": estimate.estimated_llm_calls,
            "estimated_added_chars": estimate.estimated_added_chars,
            "chapters": chapters,
        }
    )


def write_mark_artifacts(
    store: ArtifactStore,
    novel_id: str,
    task_id: str,
    plan: RewritePlan,
) -> MarkArtifactPaths:
    stage_dir = store.stage_dir(novel_id, task_id, "mark")
    stage_dir.mkdir(parents=True, exist_ok=True)

    mark_plan_path = stage_dir / "mark_plan.json"
    store.ensure_json(mark_plan_path, plan.model_dump(mode="json"))

    chapter_paths: dict[int, str] = {}
    for chapter in plan.chapters:
        chapter_path = stage_dir / f"ch_{chapter.chapter_index}_mark.json"
        store.ensure_json(
            chapter_path,
            {
                "novel_id": novel_id,
                "task_id": task_id,
                "chapter_index": chapter.chapter_index,
                "rewrite_plan": chapter.model_dump(mode="json"),
            },
        )
        chapter_paths[chapter.chapter_index] = str(chapter_path)

    return MarkArtifactPaths(mark_plan_path=str(mark_plan_path), chapter_paths=chapter_paths)
