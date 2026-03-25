from __future__ import annotations

from backend.app.models.core import Chapter, RewriteSegment, RewriteStrategy
from backend.app.services.marking import build_anchor
from backend.app.services.rewrite_pipeline import validate_rewrite_anchor


def _chapter(content: str) -> Chapter:
    return Chapter(
        id="chapter-1",
        index=1,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=3,
        start_offset=0,
        end_offset=len(content),
    )


def test_anchor_strength_detects_middle_text_changes_when_edge_hashes_match() -> None:
    original = _chapter(
        "\n\n".join(
            [
                "第一段起始文本保持不变。",
                "第二段中间文本初始版本。",
                "第三段结束文本保持不变。",
            ]
        )
    )
    changed = _chapter(
        "\n\n".join(
            [
                "第一段起始文本保持不变。",
                "第二段中间文本已经被改写过了，但首尾不变。",
                "第三段结束文本保持不变。",
            ]
        )
    )

    original_anchor = build_anchor(original, (1, 3))
    changed_anchor = build_anchor(changed, (1, 3))
    segment = RewriteSegment(
        paragraph_range=(1, 3),
        anchor=original_anchor,
        scene_type="战斗",
        original_chars=len("\n\n".join(original.content.split("\n\n"))),
        strategy=RewriteStrategy.REWRITE,
        target_ratio=1.2,
        target_chars=100,
        target_chars_min=90,
        target_chars_max=110,
        suggestion="测试锚点强度",
        source="auto",
        confirmed=False,
    )

    assert changed_anchor.paragraph_start_hash == original_anchor.paragraph_start_hash
    assert changed_anchor.paragraph_end_hash == original_anchor.paragraph_end_hash
    assert changed_anchor.range_text_hash != original_anchor.range_text_hash

    result = validate_rewrite_anchor(changed, segment)
    assert result.passed is False
    assert result.error_code == "ANCHOR_MISMATCH"
    mismatch = result.details["mismatch_fields"]["range_text_hash"]
    assert isinstance(mismatch, dict)
    values = {mismatch.get("expected"), mismatch.get("current")}
    assert original_anchor.range_text_hash in values
    assert changed_anchor.range_text_hash in values
