from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.models.core import RewriteChapterPlan, RewriteSegment, RewriteStrategy


def _segment(segment_id: str, paragraph_range: tuple[int, int]) -> RewriteSegment:
    return RewriteSegment(
        segment_id=segment_id,
        paragraph_range=paragraph_range,
        anchor={
            "paragraph_start_hash": "a",
            "paragraph_end_hash": "b",
            "range_text_hash": "c",
            "context_window_hash": "d",
            "paragraph_count_snapshot": 10,
        },
        scene_type="战斗",
        original_chars=120,
        strategy=RewriteStrategy.EXPAND,
        target_ratio=1.8,
        target_chars=200,
        target_chars_min=150,
        target_chars_max=260,
        suggestion="增强战斗节奏",
        source="auto",
        confirmed=True,
    )


def test_rewrite_chapter_plan_rejects_overlapping_ranges() -> None:
    with pytest.raises(ValidationError):
        RewriteChapterPlan(
            chapter_index=1,
            segments=[
                _segment("550e8400-e29b-41d4-a716-446655440000", (1, 3)),
                _segment("550e8400-e29b-41d4-a716-446655440001", (3, 5)),
            ],
        )


def test_rewrite_segment_requires_uuid_v4() -> None:
    with pytest.raises(ValidationError):
        _segment("550e8400-e29b-11d4-a716-446655440000", (1, 2))


def test_rewrite_chapter_plan_accepts_non_overlapping_ranges() -> None:
    plan = RewriteChapterPlan(
        chapter_index=1,
        segments=[
            _segment("550e8400-e29b-41d4-a716-446655440000", (1, 2)),
            _segment("550e8400-e29b-41d4-a716-446655440001", (3, 5)),
        ],
    )
    assert len(plan.segments) == 2


def test_rewrite_segment_auto_generates_uuid_v4() -> None:
    segment = RewriteSegment(
        paragraph_range=(1, 2),
        anchor={
            "paragraph_start_hash": "a",
            "paragraph_end_hash": "b",
            "range_text_hash": "c",
            "context_window_hash": "d",
            "paragraph_count_snapshot": 10,
        },
        scene_type="战斗",
        original_chars=120,
        strategy=RewriteStrategy.EXPAND,
        target_ratio=1.8,
        target_chars=200,
        target_chars_min=150,
        target_chars_max=260,
        suggestion="增强战斗节奏",
        source="auto",
        confirmed=True,
    )
    assert len(segment.segment_id) == 36


def test_rewrite_segment_accepts_sentence_and_char_offset_ranges() -> None:
    segment = RewriteSegment(
        paragraph_range=(1, 2),
        sentence_range=(3, 6),
        char_offset_range=(10, 42),
        anchor={
            "paragraph_start_hash": "a",
            "paragraph_end_hash": "b",
            "range_text_hash": "c",
            "context_window_hash": "d",
            "paragraph_count_snapshot": 10,
        },
        scene_type="战斗",
        original_chars=120,
        strategy=RewriteStrategy.EXPAND,
        target_ratio=1.8,
        target_chars=200,
        target_chars_min=150,
        target_chars_max=260,
        suggestion="增强战斗节奏",
        source="auto",
        confirmed=True,
    )

    assert segment.sentence_range == (3, 6)
    assert segment.char_offset_range == (10, 42)


def test_rewrite_segment_rejects_invalid_char_offset_range() -> None:
    with pytest.raises(ValidationError):
        RewriteSegment(
            paragraph_range=(1, 2),
            char_offset_range=(5, 5),
            anchor={
                "paragraph_start_hash": "a",
                "paragraph_end_hash": "b",
                "range_text_hash": "c",
                "context_window_hash": "d",
                "paragraph_count_snapshot": 10,
            },
            scene_type="战斗",
            original_chars=120,
            strategy=RewriteStrategy.EXPAND,
            target_ratio=1.8,
            target_chars=200,
            target_chars_min=150,
            target_chars_max=260,
            suggestion="增强战斗节奏",
            source="auto",
            confirmed=True,
        )
