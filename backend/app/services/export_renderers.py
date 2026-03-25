from __future__ import annotations

import json
from collections import Counter, defaultdict
from difflib import unified_diff
from io import BytesIO
from typing import Any, Mapping, Sequence
from zipfile import ZIP_DEFLATED, ZipFile

from backend.app.models.core import RewritePlan, RewriteResultStatus


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    if not rows:
        rows = [["(empty)" for _ in headers]]
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, separator, *body])


def _sort_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted([dict(record) for record in records], key=lambda item: int(item.get("chapter_index") or 0))


def _format_range(raw: Any) -> str:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return f"{raw[0]}-{raw[1]}"
    return ""


def render_analysis_markdown(records: Sequence[Mapping[str, Any]], *, chapter_index: int | None = None) -> str:
    chapters = _sort_records(records)
    if chapter_index is not None:
        chapters = [chapter for chapter in chapters if int(chapter.get("chapter_index") or 0) == chapter_index]

    lines: list[str] = ["# Analyze Artifact Report", ""]
    if not chapters:
        return "\n".join([*lines, "No analysis artifacts available.", ""])

    lines.append("## Chapter Overview")
    overview_rows: list[list[Any]] = []
    for chapter in chapters:
        analysis = chapter.get("analysis") if isinstance(chapter.get("analysis"), Mapping) else {}
        overview_rows.append(
            [
                chapter.get("chapter_index", ""),
                chapter.get("chapter_title") or chapter.get("title") or "",
                str(analysis.get("location") or ""),
                str(analysis.get("tone") or ""),
                str(analysis.get("summary") or ""),
            ]
        )
    lines.append(_markdown_table(["Chapter", "Title", "Location", "Tone", "Summary"], overview_rows))
    lines.append("")

    character_rows: list[list[Any]] = []
    event_rows: list[list[Any]] = []
    scene_counts: Counter[str] = Counter()
    scene_chapters: dict[str, set[int]] = defaultdict(set)
    scene_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"rewritable": 0, "expandable": 0})

    for chapter in chapters:
        analysis = chapter.get("analysis") if isinstance(chapter.get("analysis"), Mapping) else {}
        chapter_number = int(chapter.get("chapter_index") or 0)
        chapter_title = str(chapter.get("chapter_title") or chapter.get("title") or "")

        for item in analysis.get("characters", []) if isinstance(analysis, Mapping) else []:
            if isinstance(item, Mapping):
                character_rows.append(
                    [
                        chapter_number,
                        chapter_title,
                        str(item.get("name") or ""),
                        str(item.get("emotion") or ""),
                        str(item.get("state") or ""),
                        str(item.get("role_in_chapter") or ""),
                    ]
                )

        for item in analysis.get("key_events", []) if isinstance(analysis, Mapping) else []:
            if isinstance(item, Mapping):
                event_rows.append(
                    [
                        chapter_number,
                        chapter_title,
                        str(item.get("event_type") or ""),
                        str(item.get("description") or ""),
                        str(item.get("importance") or ""),
                        _format_range(item.get("paragraph_range")),
                    ]
                )

        for item in analysis.get("scenes", []) if isinstance(analysis, Mapping) else []:
            if not isinstance(item, Mapping):
                continue
            scene_type = str(item.get("scene_type") or "")
            scene_counts[scene_type] += 1
            scene_chapters[scene_type].add(chapter_number)
            potential = item.get("rewrite_potential") if isinstance(item.get("rewrite_potential"), Mapping) else {}
            if bool(potential.get("rewritable")):
                scene_stats[scene_type]["rewritable"] += 1
            if bool(potential.get("expandable")):
                scene_stats[scene_type]["expandable"] += 1

    lines.append("## Characters")
    lines.append(_markdown_table(["Chapter", "Title", "Character", "Emotion", "State", "Role"], character_rows))
    lines.append("")
    lines.append("## Key Events")
    lines.append(_markdown_table(["Chapter", "Title", "Event Type", "Description", "Importance", "Range"], event_rows))
    lines.append("")
    scene_rows = [
        [
            scene_type,
            scene_counts[scene_type],
            ", ".join(str(index) for index in sorted(scene_chapters[scene_type])),
            scene_stats[scene_type]["rewritable"],
            scene_stats[scene_type]["expandable"],
        ]
        for scene_type in sorted(scene_counts)
    ]
    lines.append("## Scene Distribution")
    lines.append(_markdown_table(["Scene Type", "Count", "Chapters", "Rewritable", "Expandable"], scene_rows))
    lines.append("")
    return "\n".join(lines)


def render_mark_markdown(plan: RewritePlan, *, chapter_index: int | None = None) -> str:
    chapters = plan.chapters
    if chapter_index is not None:
        chapters = [chapter for chapter in chapters if chapter.chapter_index == chapter_index]

    lines: list[str] = ["# Rewrite Plan", ""]
    lines.extend(
        [
            f"- Novel: `{plan.novel_id}`",
            f"- Total marked: `{plan.total_marked}`",
            f"- Estimated LLM calls: `{plan.estimated_llm_calls}`",
            f"- Estimated added chars: `{plan.estimated_added_chars}`",
            "",
        ]
    )

    if not chapters:
        lines.append("No rewrite segments available.")
        return "\n".join(lines)

    for chapter in chapters:
        lines.append(f"## Chapter {chapter.chapter_index}")
        if not chapter.segments:
            lines.append("No rewrite segments.")
            lines.append("")
            continue
        rows: list[list[Any]] = []
        for segment in chapter.segments:
            rows.append(
                [
                    _format_range(segment.paragraph_range),
                    segment.scene_type,
                    segment.strategy.value,
                    segment.source,
                    f"{segment.target_ratio:.2f}",
                    segment.target_chars,
                    segment.suggestion,
                    "yes" if segment.confirmed else "no",
                ]
            )
        lines.append(
            _markdown_table(
                ["Range", "Scene Type", "Strategy", "Source", "Target Ratio", "Target Chars", "Suggestion", "Confirmed"],
                rows,
            )
        )
        lines.append("")
    return "\n".join(lines)


def _split_paragraphs(content: str) -> list[str]:
    return [part.strip() for part in content.split("\n\n") if part.strip()]


def _segment_diff_text(original_text: str, rewritten_text: str, *, fromfile: str = "original", tofile: str = "rewritten") -> str:
    diff = list(
        unified_diff(
            original_text.splitlines(),
            rewritten_text.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    return "\n".join(diff)


def _rewrite_segment_block(segment: Mapping[str, Any], *, chapter_content: str | None = None) -> str:
    original_text = str(segment.get("original_text") or "")
    rewritten_text = str(segment.get("rewritten_text") or "")
    status = str(segment.get("status") or "")
    paragraph_range = _format_range(segment.get("paragraph_range"))

    lines = [f"### Segment {segment.get('segment_id', '')}", f"- Range: `{paragraph_range}`", f"- Status: `{status}`"]
    if status in {RewriteResultStatus.FAILED.value, RewriteResultStatus.PENDING.value} and not rewritten_text:
        lines.append("- No rewritten text available.")
        if original_text:
            lines.extend(["", "#### Original", original_text])
        return "\n".join(lines)

    if not original_text and chapter_content:
        start, end = segment.get("paragraph_range", (0, 0))
        if isinstance(start, int) and isinstance(end, int):
            paragraphs = _split_paragraphs(chapter_content)
            if 1 <= start <= end <= len(paragraphs):
                original_text = "\n\n".join(paragraphs[start - 1 : end])

    diff_text = _segment_diff_text(original_text, rewritten_text)
    if not diff_text.strip():
        diff_text = "No textual changes."
    lines.extend(["", diff_text])
    return "\n".join(lines)


def render_rewrite_diff(
    records: Sequence[Mapping[str, Any]],
    *,
    split_chapters: Mapping[int, Mapping[str, Any]] | None = None,
    chapter_index: int | None = None,
) -> str:
    chapters = _sort_records(records)
    if chapter_index is not None:
        chapters = [chapter for chapter in chapters if int(chapter.get("chapter_index") or 0) == chapter_index]

    lines: list[str] = ["# Rewrite Diff", ""]
    if not chapters:
        return "\n".join([*lines, "No rewrite artifacts available.", ""])

    for chapter in chapters:
        index = int(chapter.get("chapter_index") or 0)
        title = str(chapter.get("chapter_title") or chapter.get("title") or "")
        lines.append(f"## Chapter {index}{' - ' + title if title else ''}")
        segments = list(chapter.get("segments", [])) if isinstance(chapter.get("segments"), list) else []
        if not segments:
            lines.append("No rewrite segments. Chapter preserved as-is.")
            split_payload = split_chapters.get(index) if split_chapters else None
            if split_payload and isinstance(split_payload, Mapping):
                content = str(split_payload.get("content") or "")
                if content:
                    lines.extend(["", "#### Original Chapter", content])
            lines.append("")
            continue

        chapter_content = None
        if split_chapters and index in split_chapters:
            chapter_content = str(split_chapters[index].get("content") or "")
        for segment in segments:
            if isinstance(segment, Mapping):
                lines.extend([_rewrite_segment_block(segment, chapter_content=chapter_content), ""])
    return "\n".join(lines).rstrip() + "\n"


def build_split_zip(chapters: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for chapter in _sort_records(chapters):
            index = int(chapter.get("chapter_index") or 0)
            content = str(chapter.get("content") or "")
            archive.writestr(f"chapter_{index:03d}.txt", content)
    return buffer.getvalue()


def build_rewrite_zip(
    records: Sequence[Mapping[str, Any]],
    *,
    split_chapters: Mapping[int, Mapping[str, Any]] | None = None,
) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        for chapter in _sort_records(records):
            index = int(chapter.get("chapter_index") or 0)
            archive.writestr(
                f"chapter_{index:03d}.diff",
                render_rewrite_diff([chapter], split_chapters=split_chapters, chapter_index=index),
            )
    return buffer.getvalue()
