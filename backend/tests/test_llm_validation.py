from __future__ import annotations

from backend.app.llm.validation import validate_analyze_output, validate_rewrite_output


def test_validate_analyze_output_accepts_structured_payload() -> None:
    result = validate_analyze_output(
        {
            "summary": "主角进入城门并观察局势。",
            "characters": [
                {
                    "name": "主角",
                    "emotion": "紧张",
                    "state": "警惕",
                    "role_in_chapter": "主视角",
                }
            ],
            "key_events": [],
            "scenes": [],
            "location": "城门",
            "tone": "紧张",
        },
        summary_min_chars=2,
        summary_max_chars=40,
    )

    assert result.passed is True
    assert result.parsed is not None
    assert result.parsed.location == "城门"


def test_validate_analyze_output_rejects_short_summary() -> None:
    result = validate_analyze_output(
        {
            "summary": "短",
            "characters": [],
            "key_events": [],
            "scenes": [],
            "location": "城门",
            "tone": "紧张",
        },
        summary_min_chars=2,
    )

    assert result.passed is False
    assert result.error_code == "ANALYZE_SUMMARY_TOO_SHORT"


def test_validate_rewrite_output_checks_length_and_similarity() -> None:
    success = validate_rewrite_output(
        "原文段落内容并不复杂。",
        "这是一个长度合适且表达不同的改写版本。",
        target_chars_min=5,
        target_chars_max=40,
    )

    assert success.passed is True
    assert 0 <= success.similarity < 0.9

    too_similar = validate_rewrite_output(
        "第一章 夜色很深，城门缓缓打开。",
        "第一章 夜色很深，城门缓缓打开。",
        target_chars_min=5,
        target_chars_max=40,
    )

    assert too_similar.passed is False
    assert too_similar.error_code == "REWRITE_TOO_SIMILAR"
    assert too_similar.similarity >= 0.9

