from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import signal
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from uuid import NAMESPACE_URL, uuid4, uuid5

from backend.app.contracts.api import (
    Chapter,
    SplitMatchedLine,
    SplitRuleCreateRequest,
    SplitRuleSpec,
    SplitRuleUpdateRequest,
    SplitRulesConfigRequest,
    SplitRulesConfigResponse,
    SplitRulesConfirmResponse,
    SplitRulesPreviewRequest,
    SplitRulesPreviewResponse,
)
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.settings import get_settings
from backend.app.models.core import Paragraph

MAX_PATTERN_LENGTH = 512
REGEX_TIMEOUT_SECONDS = 0.5
RULE_STORE_FILENAME = "split-rules.json"
MAX_PATTERN_QUANTIFIERS = 24

PARAGRAPH_SEPARATOR_RE = re.compile(r"(?:\r?\n\s*){2,}")
NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*[+*][^()]*\)\s*(?:[+*]|\{\d+(?:,\d*)?\})")
REPEATED_WILDCARD_RE = re.compile(r"(?:\.\*){2,}|(?:\.\+){2,}")


@dataclass(slots=True)
class SplitRulesState:
    rules_version: str
    builtin_rules: list[SplitRuleSpec]
    custom_rules: list[SplitRuleSpec]


@dataclass(slots=True)
class ParagraphSlice:
    index: int
    start_offset: int
    end_offset: int
    text: str


@dataclass(slots=True)
class SplitResult:
    preview_valid: bool
    failure_reason: str | None
    matched_count: int
    matched_lines: list[SplitMatchedLine]
    boundary_hash: str
    chapters: list[Chapter]
    selected_rule_id: str | None
    selected_rule_name: str | None


@dataclass(slots=True)
class PreviewTokenPayload:
    novel_id: str
    source_revision: str
    rules_version: str
    boundary_hash: str
    preview_valid: bool
    matched_count: int
    selected_rule_id: str | None
    created_at: str


class SplitRegexTimeout(Exception):
    pass


def _builtin_rules() -> list[SplitRuleSpec]:
    return [
        SplitRuleSpec(
            id="builtin-zh-number",
            name="中文数字章节号",
            pattern=r"^第[一二三四五六七八九十百千万零〇]+[章节回集卷部篇][\s：:·]?.*$",
            priority=10,
            enabled=True,
            builtin=True,
        ),
        SplitRuleSpec(
            id="builtin-arabic-number",
            name="阿拉伯数字章节号",
            pattern=r"^第\s*\d+\s*[章节回集卷部篇][\s：:·]?.*$",
            priority=20,
            enabled=True,
            builtin=True,
        ),
        SplitRuleSpec(
            id="builtin-plain-number",
            name="纯数字序号",
            pattern=r"^\d{1,4}[\.、\s]\s*\S+",
            priority=30,
            enabled=True,
            builtin=True,
        ),
        SplitRuleSpec(
            id="builtin-english-heading",
            name="英文章节标记",
            pattern=r"^(?:Chapter|CHAPTER|Part|PART|Book|BOOK|Vol(?:ume)?\.?)\s+\d+[\s：:.\-]?.*$",
            priority=40,
            enabled=True,
            builtin=True,
        ),
        SplitRuleSpec(
            id="builtin-special-divider",
            name="特殊分隔符",
            pattern=r"^(?:【.+】|〔.+〕|■.*|★.*|={3,}.*|-{3,}.*)$",
            priority=50,
            enabled=True,
            builtin=True,
        ),
        SplitRuleSpec(
            id="builtin-bracket-number",
            name="括号序号",
            pattern=r"^(?:（[一二三四五六七八九十]+）|（\d+）|\([一二三四五六七八九十]+\)|\(\d+\)).*$",
            priority=60,
            enabled=True,
            builtin=True,
        ),
    ]


def _resolve_builtin_rules(payload_rules: Iterable[SplitRuleSpec] | None = None) -> list[SplitRuleSpec]:
    canonical_rules = _builtin_rules()
    enabled_map = {rule.id: rule.enabled for rule in payload_rules or [] if rule.id}
    return [
        rule.model_copy(update={"enabled": enabled_map.get(rule.id, rule.enabled)})
        for rule in canonical_rules
    ]


def _store_path() -> Path:
    return get_settings().data_dir / RULE_STORE_FILENAME


def _state_payload(state: SplitRulesState) -> dict[str, object]:
    return {
        "builtin_rules": [rule.model_dump(mode="json") for rule in state.builtin_rules],
        "custom_rules": [rule.model_dump(mode="json") for rule in state.custom_rules],
    }


def _compute_rules_version(state: SplitRulesState) -> str:
    payload = json.dumps(_state_payload(state), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_rule(rule: SplitRuleSpec, *, builtin: bool) -> SplitRuleSpec:
    return SplitRuleSpec(
        id=rule.id or (f"builtin-{uuid4().hex}" if builtin else str(uuid4())),
        name=rule.name,
        pattern=rule.pattern,
        priority=rule.priority,
        enabled=rule.enabled,
        builtin=builtin,
    )


def _validate_rules_state(state: SplitRulesState) -> None:
    for rule in state.builtin_rules + state.custom_rules:
        _validate_pattern(rule.pattern)


def _default_state() -> SplitRulesState:
    builtin_rules = _builtin_rules()
    state = SplitRulesState(rules_version="", builtin_rules=builtin_rules, custom_rules=[])
    state.rules_version = _compute_rules_version(state)
    return state


def load_split_rules_state() -> SplitRulesState:
    path = _store_path()
    if not path.exists():
        return _default_state()

    payload = json.loads(path.read_text(encoding="utf-8"))
    stored_builtin_rules = [SplitRuleSpec.model_validate(item) for item in payload.get("builtin_rules", [])]
    builtin_rules = _resolve_builtin_rules(stored_builtin_rules)
    custom_rules = [
        _normalize_rule(SplitRuleSpec.model_validate(item), builtin=False)
        for item in payload.get("custom_rules", [])
    ]
    state = SplitRulesState(rules_version="", builtin_rules=builtin_rules, custom_rules=custom_rules)
    state.rules_version = payload.get("rules_version") or _compute_rules_version(state)
    return state


def save_split_rules_state(state: SplitRulesState) -> SplitRulesState:
    resolved = SplitRulesState(
        rules_version="",
        builtin_rules=_resolve_builtin_rules(state.builtin_rules),
        custom_rules=[_normalize_rule(rule, builtin=False) for rule in state.custom_rules],
    )
    _validate_rules_state(resolved)
    resolved.rules_version = _compute_rules_version(resolved)

    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_state_payload(resolved) | {"rules_version": resolved.rules_version}, ensure_ascii=False, indent=2), encoding="utf-8")
    return resolved


def replace_split_rules_state(payload: SplitRulesConfigRequest) -> SplitRulesState:
    state = SplitRulesState(
        rules_version="",
        builtin_rules=_resolve_builtin_rules(payload.builtin_rules),
        custom_rules=[_normalize_rule(rule, builtin=False) for rule in payload.custom_rules],
    )
    return save_split_rules_state(state)


def get_split_rules_snapshot() -> SplitRulesConfigResponse:
    state = load_split_rules_state()
    return SplitRulesConfigResponse(
        rules_version=state.rules_version,
        builtin_rules=state.builtin_rules,
        custom_rules=state.custom_rules,
    )


def build_preview_split_rules_state(
    *,
    builtin_rules: list[SplitRuleSpec] | None = None,
    custom_rules: list[SplitRuleSpec] | None = None,
    fallback_state: SplitRulesState | None = None,
) -> SplitRulesState:
    base_state = fallback_state or load_split_rules_state()
    state = SplitRulesState(
        rules_version="",
        builtin_rules=_resolve_builtin_rules(builtin_rules if builtin_rules is not None else base_state.builtin_rules),
        custom_rules=[
            _normalize_rule(rule, builtin=False)
            for rule in (custom_rules if custom_rules is not None else base_state.custom_rules)
        ],
    )
    _validate_rules_state(state)
    state.rules_version = _compute_rules_version(state)
    return state


def _validate_pattern(pattern: str) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise AppError(
            ErrorCode.REGEX_INVALID,
            f"Regex pattern exceeds maximum length of {MAX_PATTERN_LENGTH}",
            details={"pattern_length": len(pattern), "max_length": MAX_PATTERN_LENGTH},
        )
    _validate_pattern_complexity(pattern)
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise AppError(
            ErrorCode.REGEX_INVALID,
            "Regex compilation failed",
            details={"pattern": pattern, "error": str(exc)},
        ) from exc


def _validate_pattern_complexity(pattern: str) -> None:
    if NESTED_QUANTIFIER_RE.search(pattern):
        raise AppError(
            ErrorCode.REGEX_INVALID,
            "Regex complexity check failed",
            details={"pattern": pattern, "reason": "nested_quantifier"},
        )
    if REPEATED_WILDCARD_RE.search(pattern):
        raise AppError(
            ErrorCode.REGEX_INVALID,
            "Regex complexity check failed",
            details={"pattern": pattern, "reason": "repeated_wildcard"},
        )

    quantifier_count = len(re.findall(r"(?<!\\)(?:\*|\+|\?|\{\d+(?:,\d*)?\})", pattern))
    if quantifier_count > MAX_PATTERN_QUANTIFIERS:
        raise AppError(
            ErrorCode.REGEX_INVALID,
            "Regex complexity check failed",
            details={
                "pattern": pattern,
                "reason": "quantifier_limit",
                "quantifier_count": quantifier_count,
                "max_quantifiers": MAX_PATTERN_QUANTIFIERS,
            },
        )


def _create_rule_from_payload(payload: SplitRuleCreateRequest) -> SplitRuleSpec:
    _validate_pattern(payload.pattern)
    return SplitRuleSpec(
        id=str(uuid4()),
        name=payload.name,
        pattern=payload.pattern,
        priority=payload.priority,
        enabled=payload.enabled,
        builtin=False,
    )


def create_custom_rule(payload: SplitRuleCreateRequest) -> SplitRulesConfigResponse:
    state = load_split_rules_state()
    state.custom_rules.append(_create_rule_from_payload(payload))
    resolved = save_split_rules_state(state)
    return SplitRulesConfigResponse(
        rules_version=resolved.rules_version,
        builtin_rules=resolved.builtin_rules,
        custom_rules=resolved.custom_rules,
    )


def update_custom_rule(rule_id: str, payload: SplitRuleUpdateRequest) -> SplitRulesConfigResponse:
    state = load_split_rules_state()
    updated = False
    next_rules: list[SplitRuleSpec] = []

    for rule in state.custom_rules:
        if rule.id != rule_id:
            next_rules.append(rule)
            continue

        next_rule = SplitRuleSpec(
            id=rule.id,
            name=payload.name if payload.name is not None else rule.name,
            pattern=payload.pattern if payload.pattern is not None else rule.pattern,
            priority=payload.priority if payload.priority is not None else rule.priority,
            enabled=payload.enabled if payload.enabled is not None else rule.enabled,
            builtin=False,
        )
        _validate_pattern(next_rule.pattern)
        next_rules.append(next_rule)
        updated = True

    if not updated:
        raise AppError(ErrorCode.NOT_FOUND, f"Split rule `{rule_id}` not found", 404)

    state.custom_rules = next_rules
    resolved = save_split_rules_state(state)
    return SplitRulesConfigResponse(
        rules_version=resolved.rules_version,
        builtin_rules=resolved.builtin_rules,
        custom_rules=resolved.custom_rules,
    )


def delete_custom_rule(rule_id: str) -> SplitRulesConfigResponse:
    state = load_split_rules_state()
    next_rules = [rule for rule in state.custom_rules if rule.id != rule_id]
    if len(next_rules) == len(state.custom_rules):
        raise AppError(ErrorCode.NOT_FOUND, f"Split rule `{rule_id}` not found", 404)
    state.custom_rules = next_rules
    resolved = save_split_rules_state(state)
    return SplitRulesConfigResponse(
        rules_version=resolved.rules_version,
        builtin_rules=resolved.builtin_rules,
        custom_rules=resolved.custom_rules,
    )


def _sort_rules(state: SplitRulesState) -> list[SplitRuleSpec]:
    indexed_rules: list[tuple[int, int, int, SplitRuleSpec]] = []
    for index, rule in enumerate(state.custom_rules + state.builtin_rules):
        indexed_rules.append((0 if not rule.builtin else 1, rule.priority, index, rule))
    indexed_rules.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in indexed_rules]


def _extract_paragraphs(text: str) -> list[ParagraphSlice]:
    if not text.strip():
        return []

    paragraphs: list[ParagraphSlice] = []
    start = 0
    paragraph_index = 1
    for match in PARAGRAPH_SEPARATOR_RE.finditer(text):
        chunk = text[start:match.start()]
        if chunk.strip():
            paragraphs.append(ParagraphSlice(paragraph_index, start, match.start(), chunk))
            paragraph_index += 1
        start = match.end()

    tail = text[start:]
    if tail.strip():
        paragraphs.append(ParagraphSlice(paragraph_index, start, len(text), tail))

    return paragraphs


def _extract_paragraphs_for_rule(text: str, compiled: re.Pattern[str]) -> list[ParagraphSlice]:
    """Split text into paragraph slices while forcing matched heading lines into standalone slices.

    This keeps compatibility with the existing blank-line paragraph splitting, but avoids
    missing chapter boundaries when heading lines are glued to surrounding body text with
    only single newlines.
    """
    base_paragraphs = _extract_paragraphs(text)
    if not base_paragraphs:
        return []

    slices: list[ParagraphSlice] = []
    next_index = 1

    for paragraph in base_paragraphs:
        chunk = paragraph.text
        if not chunk.strip():
            continue

        current_offset = paragraph.start_offset
        buffered_start: int | None = None
        buffered_parts: list[str] = []

        for line in chunk.splitlines(keepends=True):
            line_start = current_offset
            line_end = line_start + len(line)
            current_offset = line_end

            stripped = line.strip()
            is_heading = bool(stripped) and bool(compiled.match(stripped))
            if is_heading:
                if buffered_start is not None:
                    buffered_text = "".join(buffered_parts)
                    if buffered_text.strip():
                        slices.append(
                            ParagraphSlice(
                                index=next_index,
                                start_offset=buffered_start,
                                end_offset=line_start,
                                text=buffered_text,
                            )
                        )
                        next_index += 1
                buffered_start = None
                buffered_parts = []
                slices.append(
                    ParagraphSlice(
                        index=next_index,
                        start_offset=line_start,
                        end_offset=line_end,
                        text=line,
                    )
                )
                next_index += 1
                continue

            if buffered_start is None:
                buffered_start = line_start
                buffered_parts = [line]
            else:
                buffered_parts.append(line)

        if buffered_start is not None:
            buffered_text = "".join(buffered_parts)
            if buffered_text.strip():
                slices.append(
                    ParagraphSlice(
                        index=next_index,
                        start_offset=buffered_start,
                        end_offset=paragraph.end_offset,
                        text=buffered_text,
                    )
                )
                next_index += 1

    return slices


def _truncate_excerpt(text: str, limit: int = 120) -> str:
    excerpt = " ".join(text.strip().split())
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 1] + "…"


@contextmanager
def _regex_timeout(seconds: float = REGEX_TIMEOUT_SECONDS):
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise_timeout(_signum, _frame):  # noqa: ANN001
        raise SplitRegexTimeout()

    try:
        previous_handler = signal.signal(signal.SIGALRM, _raise_timeout)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except ValueError:
        yield
        return
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@lru_cache(maxsize=256)
def _compile_pattern(pattern: str) -> re.Pattern[str]:
    return _validate_pattern(pattern)


def _scan_rule(
    rule: SplitRuleSpec,
    paragraphs: list[ParagraphSlice],
    *,
    compiled: re.Pattern[str] | None = None,
) -> tuple[list[int], list[SplitMatchedLine]]:
    compiled_pattern = compiled or _compile_pattern(rule.pattern)
    matched_indexes: list[int] = []
    matched_lines: list[SplitMatchedLine] = []

    with _regex_timeout():
        for paragraph in paragraphs:
            if not compiled_pattern.match(paragraph.text.strip()):
                continue
            matched_indexes.append(paragraph.index - 1)
            matched_lines.append(
                SplitMatchedLine(
                    paragraph_index=paragraph.index,
                    line_number=paragraph.index,
                    text=_truncate_excerpt(paragraph.text),
                    rule_id=rule.id,
                    rule_name=rule.name,
                )
            )

    return matched_indexes, matched_lines


def _chapter_id(boundary_hash: str, chapter_index: int, start_offset: int, end_offset: int) -> str:
    seed = f"{boundary_hash}:{chapter_index}:{start_offset}:{end_offset}"
    return str(uuid5(NAMESPACE_URL, seed))


def _build_chapters(
    text: str,
    paragraphs: list[ParagraphSlice],
    chapter_starts: list[int],
    boundary_hash: str,
    *,
    has_intro: bool = False,
) -> list[Chapter]:
    if not paragraphs:
        return []

    if not chapter_starts:
        chapter_starts = [0]

    chapters: list[Chapter] = []
    start_positions = sorted(set(chapter_starts))
    if start_positions[0] != 0:
        start_positions = [0, *start_positions]

    for chapter_index, start_paragraph_index in enumerate(start_positions, start=1):
        next_start = start_positions[chapter_index] if chapter_index < len(start_positions) else None
        start_slice = paragraphs[start_paragraph_index]
        end_paragraph_index = (next_start - 1) if next_start is not None else (len(paragraphs) - 1)
        if end_paragraph_index < start_paragraph_index:
            continue

        end_slice = paragraphs[end_paragraph_index]
        chapter_start_offset = start_slice.start_offset
        chapter_end_offset = end_slice.end_offset
        chapter_content = text[chapter_start_offset:chapter_end_offset]
        chunk_paragraphs = paragraphs[start_paragraph_index : end_paragraph_index + 1]
        paragraph_models = [
            Paragraph(
                index=item.index,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                char_count=max(0, item.end_offset - item.start_offset),
            )
            for item in chunk_paragraphs
        ]
        first_text = chunk_paragraphs[0].text.strip()
        if has_intro and chapter_index == 1 and start_paragraph_index == 0:
            title = "前言"
        else:
            title = first_text.splitlines()[0].strip() if first_text else f"第{chapter_index}章"

        chapters.append(
            Chapter(
                id=_chapter_id(boundary_hash, chapter_index, chapter_start_offset, chapter_end_offset),
                index=chapter_index,
                title=title[:64],
                content=chapter_content,
                char_count=len(chapter_content),
                paragraph_count=len(chunk_paragraphs),
                start_offset=chapter_start_offset,
                end_offset=chapter_end_offset,
                paragraphs=paragraph_models,
            )
        )

    return chapters


def _select_rule_candidate(
    text: str,
    state: SplitRulesState,
    sample_size: int,
    selected_rule_id: str | None = None,
) -> tuple[SplitRuleSpec | None, list[int], list[SplitMatchedLine], bool, str | None, list[ParagraphSlice]]:
    fallback_paragraphs = _extract_paragraphs(text)

    if selected_rule_id:
        selected_rule = next(
            (rule for rule in state.custom_rules + state.builtin_rules if rule.id == selected_rule_id),
            None,
        )
        if selected_rule is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"selected_rule_id `{selected_rule_id}` does not exist",
                details={"selected_rule_id": selected_rule_id, "reason": "rule_not_found"},
            )
        if not selected_rule.enabled:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"selected_rule_id `{selected_rule_id}` is disabled",
                details={"selected_rule_id": selected_rule_id, "reason": "rule_disabled"},
            )

        compiled = _compile_pattern(selected_rule.pattern)
        paragraphs = _extract_paragraphs_for_rule(text, compiled)
        try:
            matched_indexes, matched_lines = _scan_rule(selected_rule, paragraphs, compiled=compiled)
        except SplitRegexTimeout as exc:
            raise AppError(
                ErrorCode.REGEX_TIMEOUT,
                f"Regex rule `{selected_rule.name}` timed out",
                details={"rule_id": selected_rule.id, "rule_name": selected_rule.name, "pattern": selected_rule.pattern},
            ) from exc

        preview_valid = len(matched_indexes) >= 3
        if preview_valid:
            failure_reason: str | None = None
        elif matched_indexes:
            failure_reason = "MATCH_COUNT_TOO_LOW"
        else:
            failure_reason = "NO_MATCH"
        return selected_rule, matched_indexes, matched_lines[:sample_size], preview_valid, failure_reason, paragraphs

    best_rule: SplitRuleSpec | None = None
    best_matches: list[int] = []
    best_samples: list[SplitMatchedLine] = []
    failure_reason: str | None = "NO_MATCH"
    best_paragraphs: list[ParagraphSlice] = fallback_paragraphs

    for rule in _sort_rules(state):
        if not rule.enabled:
            continue

        compiled = _compile_pattern(rule.pattern)
        paragraphs = _extract_paragraphs_for_rule(text, compiled)
        try:
            matched_indexes, matched_lines = _scan_rule(rule, paragraphs, compiled=compiled)
        except SplitRegexTimeout as exc:
            raise AppError(
                ErrorCode.REGEX_TIMEOUT,
                f"Regex rule `{rule.name}` timed out",
                details={"rule_id": rule.id, "rule_name": rule.name, "pattern": rule.pattern},
            ) from exc

        if len(matched_indexes) > len(best_matches):
            best_rule = rule
            best_matches = matched_indexes
            best_samples = matched_lines[:sample_size]
            failure_reason = None if len(matched_indexes) >= 3 else "MATCH_COUNT_TOO_LOW"
            best_paragraphs = paragraphs

        if len(matched_indexes) >= 3:
            return rule, matched_indexes, matched_lines[:sample_size], True, None, paragraphs

    return best_rule, best_matches, best_samples, False, failure_reason, best_paragraphs


def _boundary_hash(
    chapters: list[Chapter],
    selected_rule: SplitRuleSpec | None,
    source_revision: str,
    rules_version: str,
) -> str:
    payload = {
        "source_revision": source_revision,
        "rules_version": rules_version,
        "rule_id": selected_rule.id if selected_rule else None,
        "chapters": [
            {
                "index": chapter.index,
                "title": chapter.title,
                "start_offset": chapter.start_offset,
                "end_offset": chapter.end_offset,
                "paragraph_count": chapter.paragraph_count,
            }
            for chapter in chapters
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _make_preview_token(payload: PreviewTokenPayload) -> str:
    encoded = json.dumps(asdict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _read_preview_token(token: str) -> PreviewTokenPayload:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return PreviewTokenPayload(
            novel_id=payload["novel_id"],
            source_revision=payload["source_revision"],
            rules_version=payload["rules_version"],
            boundary_hash=payload["boundary_hash"],
            preview_valid=payload["preview_valid"],
            matched_count=payload["matched_count"],
            selected_rule_id=payload.get("selected_rule_id"),
            created_at=payload["created_at"],
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise AppError(ErrorCode.PREVIEW_STALE, "Invalid preview token", details={"reason": "malformed"}) from exc


def decode_preview_token(preview_token: str) -> PreviewTokenPayload:
    return _read_preview_token(preview_token)


def split_text_to_chapters(
    text: str,
    *,
    source_revision: str,
    rules_version: str,
    state: SplitRulesState | None = None,
    sample_size: int = 10,
    selected_rule_id: str | None = None,
) -> SplitResult:
    state = state or load_split_rules_state()
    selected_rule, chapter_starts, matched_lines, preview_valid, failure_reason, paragraphs = _select_rule_candidate(
        text,
        state,
        sample_size,
        selected_rule_id,
    )
    boundary_hash = ""
    chapters: list[Chapter] = []

    if chapter_starts:
        has_intro = min(chapter_starts) > 0
        chapters = _build_chapters(text, paragraphs, chapter_starts, boundary_hash="pending", has_intro=has_intro)
        boundary_hash = _boundary_hash(chapters, selected_rule, source_revision, rules_version)
        chapters = _build_chapters(text, paragraphs, chapter_starts, boundary_hash=boundary_hash, has_intro=has_intro)
    else:
        boundary_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if preview_valid and len(chapters) < 3:
        preview_valid = False
        failure_reason = "MATCH_COUNT_TOO_LOW"

    return SplitResult(
        preview_valid=preview_valid,
        failure_reason=failure_reason,
        matched_count=len(chapter_starts),
        matched_lines=matched_lines,
        boundary_hash=boundary_hash,
        chapters=chapters,
        selected_rule_id=selected_rule.id if selected_rule else None,
        selected_rule_name=selected_rule.name if selected_rule else None,
    )


def make_split_preview(
    novel_id: str,
    text: str,
    source_revision: str | None,
    rules_version: str | None,
    *,
    sample_size: int = 10,
    state: SplitRulesState | None = None,
    selected_rule_id: str | None = None,
) -> SplitRulesPreviewResponse:
    resolved_state = state or load_split_rules_state()
    if rules_version is not None and rules_version != resolved_state.rules_version:
        raise AppError(
            ErrorCode.PREVIEW_STALE,
            "Split rules version is stale",
            details={"expected": resolved_state.rules_version, "received": rules_version},
        )

    actual_source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if source_revision is not None and source_revision != actual_source_revision:
        raise AppError(
            ErrorCode.PREVIEW_STALE,
            "Source revision does not match current novel content",
            details={"expected": actual_source_revision, "received": source_revision},
        )

    result = split_text_to_chapters(
        text,
        source_revision=actual_source_revision,
        rules_version=resolved_state.rules_version,
        state=resolved_state,
        sample_size=sample_size,
        selected_rule_id=selected_rule_id,
    )
    estimated_chapters = len(result.chapters)
    chapters = result.chapters

    token = _make_preview_token(
        PreviewTokenPayload(
            novel_id=novel_id,
            source_revision=actual_source_revision,
            rules_version=resolved_state.rules_version,
            boundary_hash=result.boundary_hash,
            preview_valid=result.preview_valid,
            matched_count=result.matched_count,
            selected_rule_id=result.selected_rule_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )
    return SplitRulesPreviewResponse(
        preview_token=token,
        novel_id=novel_id,
        source_revision=actual_source_revision,
        rules_version=resolved_state.rules_version,
        preview_valid=result.preview_valid,
        failure_reason=result.failure_reason,
        matched_count=result.matched_count,
        estimated_chapters=estimated_chapters,
        matched_lines=result.matched_lines,
        boundary_hash=result.boundary_hash,
        chapters=chapters,
    )


def confirm_split_preview(
    novel_id: str,
    preview_token: str,
    text: str,
    *,
    state: SplitRulesState | None = None,
) -> SplitRulesConfirmResponse:
    token_payload = _read_preview_token(preview_token)
    resolved_state = state or load_split_rules_state()
    actual_source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if token_payload.novel_id != novel_id:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token novel_id mismatch")
    if token_payload.source_revision != actual_source_revision:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token source revision mismatch")
    if token_payload.rules_version != resolved_state.rules_version:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token rules version mismatch")

    result = split_text_to_chapters(
        text,
        source_revision=actual_source_revision,
        rules_version=resolved_state.rules_version,
        state=resolved_state,
        selected_rule_id=token_payload.selected_rule_id,
    )
    if token_payload.boundary_hash != result.boundary_hash:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token boundary hash mismatch")

    return SplitRulesConfirmResponse(
        preview_token=preview_token,
        novel_id=novel_id,
        source_revision=actual_source_revision,
        rules_version=resolved_state.rules_version,
        boundary_hash=result.boundary_hash,
        preview_valid=result.preview_valid,
        chapter_count=len(result.chapters),
        chapters=result.chapters,
    )


def validate_preview_token(
    preview_token: str,
    *,
    novel_id: str,
    source_revision: str,
    rules_version: str,
    boundary_hash: str,
) -> PreviewTokenPayload:
    payload = _read_preview_token(preview_token)
    if payload.novel_id != novel_id:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token novel_id mismatch")
    if payload.source_revision != source_revision:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token source revision mismatch")
    if payload.rules_version != rules_version:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token rules version mismatch")
    if payload.boundary_hash != boundary_hash:
        raise AppError(ErrorCode.PREVIEW_STALE, "Preview token boundary hash mismatch")
    return payload
