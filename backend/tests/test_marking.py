from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.artifact_store import ArtifactStore
from backend.app.models.core import Chapter, ChapterAnalysis, Paragraph, RewritePotential, SceneSegment, RewriteStrategy
from backend.app.services.config_store import RewriteRule
from backend.app.services.marking import (
    SENTENCE_SPLITTER_VERSION,
    WINDOW_PLANNER_VERSION,
    build_anchor,
    build_chapter_mark_plan,
    build_rewrite_plan,
    merge_manual_segments,
    replace_manual_segments,
    write_mark_artifacts,
)
from backend.app.models.core import RewriteSegment


def _chapter() -> Chapter:
    content = "\n\n".join(
        [
            "第一段战斗动作很快。",
            "第二段对话推进情节。",
            "第三段继续战斗细节。",
            "第四段收束。",
        ]
    )
    return Chapter(
        id="chapter-1",
        index=1,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=4,
        start_offset=0,
        end_offset=len(content),
        paragraphs=[
            Paragraph(index=1, start_offset=0, end_offset=10, char_count=10),
            Paragraph(index=2, start_offset=12, end_offset=20, char_count=8),
            Paragraph(index=3, start_offset=22, end_offset=32, char_count=10),
            Paragraph(index=4, start_offset=34, end_offset=40, char_count=6),
        ],
    )


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="主角进入战斗并与对手交锋。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充动作和感官细节",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="对话",
                paragraph_range=(2, 2),
                rewrite_potential=RewritePotential(
                    expandable=False,
                    rewritable=False,
                    suggestion="保留原文",
                    priority=1,
                ),
            ),
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(3, 3),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充第二轮交锋",
                    priority=4,
                ),
            ),
        ],
        location="城门",
        tone="紧张",
    )


def _analysis_with_overlapping_scenes() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="同一段落被重复识别。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="第一次命中",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="重复命中",
                    priority=4,
                ),
            ),
        ],
        location="城门",
        tone="紧张",
    )


def _empty_analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="本章暂时没有可自动标记的场景。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="环境描写",
                paragraph_range=(1, 1),
                    rewrite_potential=RewritePotential(
                        expandable=False,
                        rewritable=False,
                        suggestion="暂不处理",
                        priority=5,
                    ),
            )
        ],
        location="空白场景",
        tone="平静",
    )


def _analysis_with_out_of_range_scenes() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="场景命中使用了超出段落总数的索引。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="洗澡场景",
                paragraph_range=(1, 5),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="命中洗澡场景，建议扩写。",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="同睡场景",
                paragraph_range=(18, 23),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="命中同睡场景，建议扩写。",
                    priority=4,
                ),
            ),
        ],
        location="卧室",
        tone="暧昧",
    )


def _chapter_with_heading() -> Chapter:
    content = "\n\n".join(
        [
            "第一章",
            "正文第一段。",
            "正文第二段。",
        ]
    )
    return Chapter(
        id="chapter-heading",
        index=2,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=3,
        start_offset=0,
        end_offset=len(content),
    )


def _analysis_with_heading_scene() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="章节标题被错误命中。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="错误命中标题。",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(2, 2),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="正文命中。",
                    priority=4,
                ),
            ),
        ],
        location="室内",
        tone="平静",
    )


def _chapter_two_paragraph_eight_sentences() -> Chapter:
    content = "\n\n".join(
        [
            "甲一。甲二。甲三。甲四。",
            "乙一。乙二。乙三。乙四。",
        ]
    )
    return Chapter(
        id="chapter-sentence-scale",
        index=3,
        title="第三章",
        content=content,
        char_count=len(content),
        paragraph_count=2,
        start_offset=0,
        end_offset=len(content),
    )


def _analysis_with_explicit_sentence_scene() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="分析结果给出精确句子范围。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 2),
                sentence_range=(4, 4),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="仅改第四句。",
                    priority=5,
                ),
            ),
        ],
        location="室内",
        tone="平静",
    )


def _analysis_with_source_scale_mismatch_scene() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="源范围索引尺度远大于章节段落数。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(10, 12),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="命中中段句子。",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="环境描写",
                paragraph_range=(30, 34),
                rewrite_potential=RewritePotential(
                    expandable=False,
                    rewritable=False,
                    suggestion="仅用于抬高源范围上限。",
                    priority=1,
                ),
            ),
        ],
        location="走廊",
        tone="紧张",
    )


def _chapter_for_rule_hit_grounding() -> Chapter:
    content = "\n\n".join(
        [
            "第一句只是背景交代。第二句还是普通叙事。第三句两人开始在走廊里拥吻。第四句他伸手撩起裙摆。第五句两人继续纠缠。",
        ]
    )
    return Chapter(
        id="chapter-rule-hit-grounding",
        index=4,
        title="第四章",
        content=content,
        char_count=len(content),
        paragraph_count=1,
        start_offset=0,
        end_offset=len(content),
    )


def _analysis_with_overwide_sentence_range_and_rule_hit() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="模型给了过宽范围，但 evidence 是精确的。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="亲吻场景",
                paragraph_range=(1, 1),
                sentence_range=(1, 5),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="只应命中后段亲密动作。",
                    priority=5,
                ),
                rule_hits=[
                    {
                        "trigger_condition": "任何嘴唇接触描述",
                        "evidence_text": "第三句两人开始在走廊里拥吻。第四句他伸手撩起裙摆。",
                    }
                ],
            ),
        ],
        location="走廊",
        tone="暧昧",
    )


def _rewrite_rules() -> list[RewriteRule]:
    return [
        RewriteRule(scene_type="战斗", strategies=["rewrite", "expand"], target_ratio=2.0, priority=1, enabled=True),
        RewriteRule(scene_type="对话", strategy="preserve", target_ratio=1.0, priority=1, enabled=True),
        RewriteRule(scene_type="环境描写", strategy="rewrite", target_ratio=1.2, priority=2, enabled=False),
    ]


def test_build_chapter_mark_plan_selects_applicable_rules_and_builds_anchor() -> None:
    chapter = _chapter()
    plan = build_chapter_mark_plan(chapter, _analysis(), _rewrite_rules())

    assert plan.chapter_index == 1
    assert plan.sentence_splitter_version == "cn-punct-v2"
    assert plan.window_planner_version == "window-planner-v1"
    assert plan.plan_version
    assert plan.source_fingerprint
    assert len(plan.sentence_spans) >= 4
    assert len(plan.segments) == 2
    assert plan.segments[0].paragraph_range == (1, 1)
    assert plan.segments[0].strategy == RewriteStrategy.EXPAND
    assert _rewrite_rules()[0].strategies == ["rewrite", "expand"]
    assert _rewrite_rules()[0].strategy == "expand"
    assert plan.segments[0].source == "auto"
    assert plan.segments[0].confirmed is False
    assert plan.segments[0].anchor.paragraph_count_snapshot == 4
    assert plan.segments[0].anchor.range_text_hash != ""
    assert plan.segments[0].sentence_range == (1, 1)
    assert plan.segments[0].char_offset_range is not None
    first_start, first_end = plan.segments[0].char_offset_range
    assert chapter.content[first_start:first_end] == "第一段战斗动作很快。"
    assert len(plan.segments[0].rewrite_windows) == 1
    first_window = plan.segments[0].rewrite_windows[0]
    assert first_window.start_offset == first_start
    assert first_window.end_offset == first_end
    assert first_window.hit_sentence_range == (1, 1)
    assert first_window.context_sentence_range == (1, 2)
    assert first_window.plan_version == plan.plan_version
    assert first_window.source_fingerprint == plan.source_fingerprint
    assert plan.segments[1].paragraph_range == (3, 3)
    assert plan.segments[1].sentence_range == (3, 3)
    assert plan.segments[1].char_offset_range is not None
    second_start, second_end = plan.segments[1].char_offset_range
    assert chapter.content[second_start:second_end] == "第三段继续战斗细节。"


def test_build_rewrite_plan_estimates_mark_costs() -> None:
    chapter = _chapter()
    plan = build_rewrite_plan("novel-1", [chapter], {1: _analysis()}, _rewrite_rules())

    assert plan.novel_id == "novel-1"
    assert plan.plan_version
    assert plan.source_fingerprint
    assert plan.sentence_splitter_version == "cn-punct-v2"
    assert plan.window_planner_version == "window-planner-v1"
    assert plan.total_marked == 2
    assert plan.estimated_llm_calls == 2
    assert plan.estimated_added_chars > 0
    assert len(plan.chapters) == 1


def test_build_chapter_mark_plan_drops_overlapping_segments() -> None:
    chapter = _chapter()
    plan = build_chapter_mark_plan(chapter, _analysis_with_overlapping_scenes(), _rewrite_rules())

    assert len(plan.segments) == 1
    assert plan.segments[0].paragraph_range == (1, 1)


def test_build_chapter_mark_plan_normalizes_out_of_range_scene_paragraphs() -> None:
    chapter = _chapter()
    plan = build_chapter_mark_plan(chapter, _analysis_with_out_of_range_scenes(), [])

    assert len(plan.segments) == 2
    assert plan.segments[0].paragraph_range == (1, 1)
    assert plan.segments[1].paragraph_range == (3, 4)
    assert all(1 <= start <= end <= 4 for start, end in (segment.paragraph_range for segment in plan.segments))


def test_build_chapter_mark_plan_prefers_explicit_sentence_range() -> None:
    chapter = _chapter_two_paragraph_eight_sentences()
    plan = build_chapter_mark_plan(chapter, _analysis_with_explicit_sentence_scene(), _rewrite_rules())

    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert segment.paragraph_range == (1, 1)
    assert segment.sentence_range == (4, 4)
    assert segment.char_offset_range is not None
    start, end = segment.char_offset_range
    assert chapter.content[start:end] == "甲四。"


def test_build_chapter_mark_plan_uses_sentence_scale_mapping_when_source_mismatch_is_large() -> None:
    chapter = _chapter_two_paragraph_eight_sentences()
    plan = build_chapter_mark_plan(chapter, _analysis_with_source_scale_mismatch_scene(), _rewrite_rules())

    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert segment.paragraph_range == (1, 1)
    assert segment.sentence_range == (3, 3)
    assert segment.char_offset_range is not None
    start, end = segment.char_offset_range
    assert start > 0
    assert chapter.content[start:end] == "甲三。"


def test_build_chapter_mark_plan_prefers_rule_hit_grounding_over_overwide_scene_range() -> None:
    chapter = _chapter_for_rule_hit_grounding()
    plan = build_chapter_mark_plan(chapter, _analysis_with_overwide_sentence_range_and_rule_hit(), _rewrite_rules())

    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert segment.sentence_range == (3, 4)
    assert segment.char_offset_range is not None
    start, end = segment.char_offset_range
    assert chapter.content[start:end] == "第三句两人开始在走廊里拥吻。第四句他伸手撩起裙摆。"


def test_build_chapter_mark_plan_skips_heading_only_segments() -> None:
    chapter = _chapter_with_heading()
    plan = build_chapter_mark_plan(chapter, _analysis_with_heading_scene(), _rewrite_rules())

    ranges = [segment.paragraph_range for segment in plan.segments]
    assert (1, 1) not in ranges
    assert (2, 2) in ranges


def test_build_rewrite_plan_keeps_empty_chapter_for_manual_marking() -> None:
    chapter = _chapter()
    plan = build_rewrite_plan("novel-1", [chapter], {1: _empty_analysis()}, _rewrite_rules())

    assert plan.total_marked == 0
    assert plan.estimated_llm_calls == 0
    assert len(plan.chapters) == 1
    assert plan.chapters[0].chapter_index == 1
    assert plan.chapters[0].segments == []


def test_manual_merge_and_replace_segments_keep_manual_source() -> None:
    chapter = _chapter()
    plan = build_rewrite_plan("novel-1", [chapter], {1: _analysis()}, _rewrite_rules())
    manual_segment = RewriteSegment(
        paragraph_range=(4, 4),
        anchor=build_anchor(chapter, (4, 4)),
        scene_type="战斗",
        original_chars=6,
        strategy=RewriteStrategy.REWRITE,
        target_ratio=1.3,
        target_chars=8,
        target_chars_min=7,
        target_chars_max=10,
        suggestion="手动修正",
        source="manual",
        confirmed=False,
    )

    merged = merge_manual_segments(plan, 1, [manual_segment])
    replaced = replace_manual_segments(plan, 1, [manual_segment])

    assert len(plan.chapters[0].segments) == 2
    assert len(merged.chapters[0].segments) == 3
    assert len(replaced.chapters[0].segments) == 1
    assert merged.chapters[0].segments[-1].source == "manual"
    assert merged.chapters[0].segments[-1].confirmed is True
    assert replaced.chapters[0].segments[0].source == "manual"


def test_write_mark_artifacts_creates_plan_and_per_chapter_files(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    plan = build_rewrite_plan("novel-1", [_chapter()], {1: _analysis()}, _rewrite_rules())

    paths = write_mark_artifacts(store, "novel-1", "task-1", plan)

    plan_path = Path(paths.mark_plan_path)
    assert plan_path.exists()
    chapter_path = Path(paths.chapter_paths[1])
    assert chapter_path.exists()

    plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    chapter_payload = json.loads(chapter_path.read_text(encoding="utf-8"))

    assert plan_payload["novel_id"] == "novel-1"
    assert plan_payload["total_marked"] == 2
    assert chapter_payload["chapter_index"] == 1
    assert chapter_payload["rewrite_plan"]["chapter_index"] == 1


def test_build_chapter_mark_plan_is_deterministic_for_same_input() -> None:
    chapter = _chapter()
    first = build_chapter_mark_plan(chapter, _analysis(), _rewrite_rules())
    second = build_chapter_mark_plan(chapter, _analysis(), _rewrite_rules())

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_build_rewrite_plan_changes_version_after_splitter_or_planner_upgrade(monkeypatch) -> None:
    chapter = _chapter()
    fixed_created = datetime(2026, 3, 25, tzinfo=timezone.utc)
    baseline = build_rewrite_plan("novel-1", [chapter], {1: _analysis()}, _rewrite_rules(), created_at=fixed_created)

    monkeypatch.setattr("backend.app.services.marking.SENTENCE_SPLITTER_VERSION", f"{SENTENCE_SPLITTER_VERSION}-next")
    monkeypatch.setattr("backend.app.services.marking.WINDOW_PLANNER_VERSION", f"{WINDOW_PLANNER_VERSION}-next")
    upgraded = build_rewrite_plan("novel-1", [chapter], {1: _analysis()}, _rewrite_rules(), created_at=fixed_created)

    assert baseline.plan_version != upgraded.plan_version
    assert baseline.sentence_splitter_version != upgraded.sentence_splitter_version
    assert baseline.window_planner_version != upgraded.window_planner_version
