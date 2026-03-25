from __future__ import annotations

import hashlib

import pytest

from backend.app.contracts.api import SplitRulesConfigRequest
from backend.app.contracts.api import SplitRuleSpec
from backend.app.services.splitting import (
    get_split_rules_snapshot,
    load_split_rules_state,
    replace_split_rules_state,
    split_text_to_chapters,
)


def _enable_builtin_rules() -> None:
    snapshot = get_split_rules_snapshot()
    replace_split_rules_state(
        SplitRulesConfigRequest(
            builtin_rules=[rule.model_copy(update={"enabled": True}) for rule in snapshot.builtin_rules],
            custom_rules=[],
        )
    )


@pytest.mark.usefixtures("isolated_data_dir")
@pytest.mark.parametrize(
    ("text", "expected_rule_name", "expected_titles"),
    [
        (
            "第一章\n\n内容甲。\n\n第二章\n\n内容乙。\n\n第三章\n\n内容丙。",
            "中文数字章节号",
            ["第一章", "第二章", "第三章"],
        ),
        (
            "Chapter 1\n\nBody one.\n\nChapter 2\n\nBody two.\n\nChapter 3\n\nBody three.",
            "英文章节标记",
            ["Chapter 1", "Chapter 2", "Chapter 3"],
        ),
        (
            "1. 序章\n\n第一段内容。\n\n2. 发展\n\n第二段内容。\n\n3. 收束\n\n第三段内容。",
            "纯数字序号",
            ["1. 序章", "2. 发展", "3. 收束"],
        ),
    ],
)
def test_split_rules_cover_builtin_heading_formats(
    text: str,
    expected_rule_name: str,
    expected_titles: list[str],
) -> None:
    _enable_builtin_rules()
    state = load_split_rules_state()
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    result = split_text_to_chapters(
        text,
        source_revision=source_revision,
        rules_version=state.rules_version,
    )

    assert result.preview_valid is True
    assert result.selected_rule_name == expected_rule_name
    assert result.matched_count >= 3
    assert [chapter.title for chapter in result.chapters] == expected_titles


@pytest.mark.usefixtures("isolated_data_dir")
def test_split_rules_handle_preface_before_first_heading() -> None:
    _enable_builtin_rules()
    state = load_split_rules_state()
    text = "序言段落。\n\n第一章\n\n内容甲。\n\n第二章\n\n内容乙。\n\n第三章\n\n内容丙。"
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    result = split_text_to_chapters(
        text,
        source_revision=source_revision,
        rules_version=state.rules_version,
    )

    assert result.preview_valid is True
    assert result.matched_count >= 3
    assert len(result.chapters) == 4
    assert result.chapters[0].title == "前言"
    assert result.chapters[1].title == "第一章"


@pytest.mark.usefixtures("isolated_data_dir")
def test_split_rules_mark_preview_invalid_when_too_few_matches() -> None:
    replace_split_rules_state(
        SplitRulesConfigRequest(
            builtin_rules=[],
            custom_rules=[
                SplitRuleSpec(
                    id="strict-zh-heading",
                    name="Strict Chinese Heading",
                    pattern=r"^第[一二三四五六七八九十百千零两0-9]+章$",
                    priority=1,
                    enabled=True,
                    builtin=False,
                ),
            ],
        )
    )
    state = load_split_rules_state()
    text = "第一章\n\n内容甲。\n\n第二章\n\n内容乙。"
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    result = split_text_to_chapters(
        text,
        source_revision=source_revision,
        rules_version=state.rules_version,
    )

    assert result.preview_valid is False
    assert result.failure_reason == "MATCH_COUNT_TOO_LOW"
    assert result.matched_count == 2
    assert len(result.chapters) == 2


@pytest.mark.usefixtures("isolated_data_dir")
def test_split_rules_detect_headings_without_blank_line_separators() -> None:
    _enable_builtin_rules()
    state = load_split_rules_state()
    text = "\n".join(
        [
            "前文段落A",
            "第一章",
            "内容1",
            "第二章",
            "内容2",
            "第三章",
            "内容3",
            "第四章",
            "内容4",
        ]
    )
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    result = split_text_to_chapters(
        text,
        source_revision=source_revision,
        rules_version=state.rules_version,
    )

    assert result.selected_rule_name == "中文数字章节号"
    assert result.matched_count == 4
    assert len(result.chapters) == 5
    assert [chapter.title for chapter in result.chapters] == ["前言", "第一章", "第二章", "第三章", "第四章"]
