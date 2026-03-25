from __future__ import annotations

from uuid import uuid4

from backend.app.models.core import Chapter, RewriteResult, RewriteResultStatus, RewriteStrategy, RewriteWindow
from backend.app.services.assemble_pipeline import AssembleThresholds, assemble_novel


def _chapter(index: int, title: str, paragraphs: list[str]) -> Chapter:
    content = "\n\n".join(paragraphs)
    return Chapter(
        id=f"chapter-{index}",
        index=index,
        title=title,
        content=content,
        char_count=len(content),
        paragraph_count=len(paragraphs),
        start_offset=0,
        end_offset=len(content),
    )


def _segment_text(chapter: Chapter, paragraph_range: tuple[int, int]) -> str:
    paragraphs = [part.strip() for part in chapter.content.split("\n\n") if part.strip()]
    start, end = paragraph_range
    return "\n\n".join(paragraphs[start - 1 : end])


def _rewrite_result(
    chapter: Chapter,
    paragraph_range: tuple[int, int],
    *,
    status: RewriteResultStatus,
    rewritten_text: str,
    segment_id: str | None = None,
    char_offset_range: tuple[int, int] | None = None,
    rewrite_windows: list[RewriteWindow] | None = None,
) -> RewriteResult:
    original_text = _segment_text(chapter, paragraph_range)
    if char_offset_range is not None:
        start, end = char_offset_range
        original_text = chapter.content[start:end]
    return RewriteResult(
        segment_id=segment_id or str(uuid4()),
        chapter_index=chapter.index,
        paragraph_range=paragraph_range,
        char_offset_range=char_offset_range,
        rewrite_windows=list(rewrite_windows or []),
        anchor_verified=True,
        strategy=RewriteStrategy.REWRITE,
        original_text=original_text,
        rewritten_text=rewritten_text,
        original_chars=len(original_text),
        rewritten_chars=len(rewritten_text),
        status=status,
        attempts=1,
        provider_used="openai_compatible",
    )


def test_assemble_replaces_accepted_segments_and_preserves_missing_chapter() -> None:
    chapter_one = _chapter(
        1,
        "第一章",
        [
            "第一段原文。",
            "第二段原文。",
            "第三段原文。",
        ],
    )
    chapter_two = _chapter(
        2,
        "第二章",
        [
            "第四段原文。",
            "第五段原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter_one,
                (1, 1),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="第一段改写。",
            ),
            _rewrite_result(
                chapter_one,
                (2, 2),
                status=RewriteResultStatus.REJECTED,
                rewritten_text="第二段不应使用。",
            ),
            _rewrite_result(
                chapter_one,
                (3, 3),
                status=RewriteResultStatus.ACCEPTED_EDITED,
                rewritten_text="第三段人工微调。",
            ),
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter_one, chapter_two],
        rewrite_results,
        stage_run_id="stage-run-1",
    )

    assert result.blocked is False
    assert result.stats.original_chars == len(chapter_one.content) + len(chapter_two.content)
    assert "第一段改写。" in result.assembled_text
    assert "第二段原文。" in result.assembled_text
    assert "第三段人工微调。" in result.assembled_text
    assert chapter_two.content in result.assembled_text
    assert result.chapters[1].assembled_text == "第二章（AI改写）\n\n第四段原文。\n\n第五段原文。"
    assert result.chapters[1].preserved_segments == 1
    assert result.chapters[0].rewritten_segments == 2
    assert result.chapters[0].failed_segments == 0
    assert result.quality_report.blocked is False
    assert result.export_manifest.risk_export is False
    assert "[original]" in result.compare_text
    assert "[assembled]" in result.compare_text


def test_assemble_invalid_range_falls_back_to_original_and_records_warning() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一段原文。",
            "第二段原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (2, 3),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="越界改写。",
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.assembled_text == "第一章（AI改写）\n\n第一段原文。\n\n第二段原文。"
    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n第一段原文。\n\n第二段原文。"
    assert result.chapters[0].rewritten_segments == 0
    assert result.chapters[0].failed_segments == 1
    assert result.warnings
    assert any(warning.code == "PARAGRAPH_RANGE_OUT_OF_BOUNDS" for warning in result.warnings)
    assert result.blocked is False


def test_assemble_tolerates_whitespace_only_original_text_drift() -> None:
    content = "第一章\n\n　　正文第一段。\n　　正文第二段。"
    chapter = Chapter(
        id="chapter-1",
        index=1,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=2,
        start_offset=0,
        end_offset=len(content),
    )
    rewrite_results = {
        1: [
            RewriteResult(
                segment_id=str(uuid4()),
                chapter_index=1,
                paragraph_range=(1, 2),
                anchor_verified=True,
                strategy=RewriteStrategy.REWRITE,
                # Simulate rewrite-stage char-offset extraction that preserves
                # full-width indentation after heading separator.
                original_text=content,
                rewritten_text="改写后正文。",
                original_chars=len(content),
                rewritten_chars=len("改写后正文。"),
                status=RewriteResultStatus.COMPLETED,
                attempts=1,
                provider_used="openai_compatible",
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n改写后正文。"
    assert result.chapters[0].rewritten_segments == 1
    assert result.chapters[0].failed_segments == 0
    assert not any(warning.code == "ORIGINAL_TEXT_MISMATCH" for warning in result.warnings)


def test_assemble_restores_missing_heading_and_adds_ai_suffix() -> None:
    chapter = _chapter(
        1,
        "第二章",
        [
            "第二章",
            "正文原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (1, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="改写后的正文。",
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第二章（AI改写）\n\n改写后的正文。"
    assert result.blocked is False


def test_assemble_corrects_wrong_heading_number_with_original_index() -> None:
    chapter = _chapter(
        1,
        "第五章",
        [
            "第五章",
            "正文原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (1, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="第二章\n\n改写正文。",
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text.startswith("第五章（AI改写）\n\n")
    assert "第二章\n\n改写正文。" not in result.chapters[0].assembled_text


def test_assemble_deduplicates_redundant_leading_heading_in_rewrite_text() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一章",
            "正文原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (1, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="第一章\n\n第一章\n\n改写正文。",
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n改写正文。"


def test_assemble_missing_rewrite_artifact_preserves_original_chapter() -> None:
    chapter_one = _chapter(1, "第一章", ["第一段原文。"])
    chapter_two = _chapter(2, "第二章", ["第二段原文。", "第三段原文。"])

    result = assemble_novel("novel-1", "task-1", [chapter_one, chapter_two], {})

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n第一段原文。"
    assert result.chapters[1].assembled_text == "第二章（AI改写）\n\n第二段原文。\n\n第三段原文。"
    assert result.chapters[0].preserved_segments == 1
    assert result.chapters[1].preserved_segments == 1
    assert result.stats.failed_segments == 0
    assert result.blocked is False


def test_assemble_skips_heading_expansion_rewrite_result() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一章",
            "正文第一段原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (1, 1),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="第一章\n\n这里被错误扩写成正文，不应进入组装结果。",
            ),
            _rewrite_result(
                chapter,
                (2, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="正文第一段改写。",
            ),
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n正文第一段改写。"
    assert result.chapters[0].rewritten_segments == 1
    assert result.chapters[0].failed_segments == 1
    assert any(warning.code == "HEADING_REWRITE_EXPANSION" for warning in result.warnings)


def test_assemble_uses_char_offset_range_for_precise_replacement() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一章",
            "甲乙丙丁。戊己庚辛。壬癸。",
        ],
    )
    target = "戊己庚辛"
    start = chapter.content.index(target)
    end = start + len(target)
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (2, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="风雷激荡",
                char_offset_range=(start, end),
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n甲乙丙丁。风雷激荡。壬癸。"
    assert result.chapters[0].rewritten_segments == 1
    assert result.chapters[0].failed_segments == 0
    assert not any(warning.code == "ORIGINAL_TEXT_MISMATCH" for warning in result.warnings)


def test_assemble_keeps_text_outside_window_byte_exact() -> None:
    chapter = _chapter(
        1,
        "第一章（AI改写）",
        [
            "第一章（AI改写）",
            "甲乙丙丁。戊己庚辛。壬癸。",
        ],
    )
    target = "戊己庚辛"
    start = chapter.content.index(target)
    end = start + len(target)
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (2, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="风雷激荡",
                char_offset_range=(start, end),
                rewrite_windows=[
                    RewriteWindow(
                        window_id="w-invariance",
                        segment_id="seg-invariance",
                        chapter_index=1,
                        start_offset=start,
                        end_offset=end,
                        target_chars=4,
                        target_chars_min=3,
                        target_chars_max=6,
                    )
                ],
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    expected = chapter.content[:start] + "风雷激荡" + chapter.content[end:]
    assert result.chapters[0].assembled_text == expected
    assert not any(warning.code == "WINDOW_OUTSIDE_TEXT_CHANGED" for warning in result.warnings)


def test_assemble_blocks_overlapping_windows_in_single_result() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一章",
            "甲乙丙丁。戊己庚辛。壬癸。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (2, 2),
                status=RewriteResultStatus.ACCEPTED,
                rewritten_text="错误改写。",
                rewrite_windows=[
                    RewriteWindow(
                        window_id="w1",
                        segment_id="seg-1",
                        chapter_index=1,
                        start_offset=8,
                        end_offset=14,
                        target_chars=6,
                        target_chars_min=4,
                        target_chars_max=8,
                    ),
                    RewriteWindow(
                        window_id="w2",
                        segment_id="seg-1",
                        chapter_index=1,
                        start_offset=12,
                        end_offset=18,
                        target_chars=6,
                        target_chars_min=4,
                        target_chars_max=8,
                    ),
                ],
            )
        ]
    }

    result = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=1.0, max_warning_count=10),
    )

    assert result.chapters[0].assembled_text == "第一章（AI改写）\n\n甲乙丙丁。戊己庚辛。壬癸。"
    assert result.chapters[0].rewritten_segments == 0
    assert result.chapters[0].failed_segments == 1
    assert any(warning.code == "REWRITE_WINDOW_OVERLAP" for warning in result.warnings)


def test_assemble_quality_gate_blocks_and_force_builds_risk_signature() -> None:
    chapter = _chapter(
        1,
        "第一章",
        [
            "第一段原文。",
        ],
    )
    rewrite_results = {
        1: [
            _rewrite_result(
                chapter,
                (1, 1),
                status=RewriteResultStatus.PENDING,
                rewritten_text="待定改写。",
            )
        ]
    }

    blocked = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=0.0, max_warning_count=0),
        stage_run_id="stage-run-9",
        force=False,
    )
    forced = assemble_novel(
        "novel-1",
        "task-1",
        [chapter],
        rewrite_results,
        thresholds=AssembleThresholds(max_failed_ratio=0.0, max_warning_count=0),
        stage_run_id="stage-run-9",
        force=True,
    )

    assert blocked.blocked is True
    assert blocked.quality_report.blocked is True
    assert blocked.quality_report.allow_force_export is True
    assert blocked.quality_report.risk_signature is None
    assert blocked.risk_signature is None
    assert blocked.quality_report.block_reasons
    assert forced.blocked is True
    assert forced.risk_signature is not None
    assert forced.risk_signature.task_id == "task-1"
    assert forced.risk_signature.stage_run_id == "stage-run-9"
    assert forced.export_manifest.risk_export is True
    assert forced.export_manifest.risk_signature is not None
    assert forced.export_manifest.risk_signature["task_id"] == "task-1"
    assert forced.quality_report.risk_signature is not None
    assert forced.quality_report.risk_signature["task_id"] == "task-1"
    assert forced.risk_signature.reasons
