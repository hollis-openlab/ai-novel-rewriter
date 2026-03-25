from __future__ import annotations

from backend.app.core.prompt_templates import PromptTemplateRegistry
from backend.app.llm.prompting import build_stage_prompts, stage_template_registry_descriptions


def test_prompt_template_registry_renders_conditionals_and_variables() -> None:
    registry = PromptTemplateRegistry()
    registry.register("name", "Display name", lambda: "Codex")

    rendered = registry.render(
        "{% if name == 'Codex' %}hello{% endif %} {{ name }} {{ now_iso }}",
        {"enabled": True},
    )

    assert rendered.startswith("hello")
    assert "Codex" in rendered
    assert "now_iso" not in rendered


def test_prompt_registry_descriptions_are_enumerable() -> None:
    descriptions = stage_template_registry_descriptions()
    assert "now_iso" in descriptions
    assert descriptions["now_iso"] == "Current UTC timestamp in ISO8601"


def test_stage_prompt_builder_injects_global_prompt_for_analyze_and_rewrite() -> None:
    analyze_bundle = build_stage_prompts(
        "analyze",
        global_prompt="你是一个严谨的分析助手",
        context={
            "chapter_text": "第一章：风起云涌。",
            "scene_rules": [{"scene_type": "战斗", "keywords": ["打斗", "交锋"]}],
            "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        },
    )

    assert "你是一个严谨的分析助手" in analyze_bundle.system_prompt
    assert "输出必须严格为 JSON" in analyze_bundle.system_prompt
    assert "JSON Schema" in analyze_bundle.user_prompt
    assert "场景识别规则" in analyze_bundle.user_prompt
    assert "第一章：风起云涌。" in analyze_bundle.user_prompt

    rewrite_bundle = build_stage_prompts(
        "rewrite",
        global_prompt="请保持文风统一",
        context={
            "chapter_summary": "主角进入城门。",
            "character_states": [{"name": "主角", "emotion": "紧张"}],
            "preceding_text": "前文",
            "following_text": "后文",
            "rewrite_mode": "expand",
            "anchor": {"paragraph_start_hash": "abc"},
            "segment_text": "原始片段",
            "rewrite_rules": [
                {
                    "scene_type": "战斗",
                    "strategies": ["rewrite", "expand"],
                    "primary_strategy": "expand",
                    "rewrite_guidance": "战斗场景重点强化临场张力。",
                    "target_ratio": 2.0,
                    "priority": 1,
                }
            ],
        },
    )

    assert "请保持文风统一" in rewrite_bundle.system_prompt
    assert "只输出“目标片段”的改写正文" in rewrite_bundle.system_prompt
    assert "原始片段" in rewrite_bundle.user_prompt
    assert "rewrite、expand" in rewrite_bundle.user_prompt
    assert "主策略=expand" in rewrite_bundle.user_prompt
    assert "战斗场景重点强化临场张力。" in rewrite_bundle.user_prompt


def test_stage_prompt_builder_prefers_expand_as_primary_when_missing_primary_strategy() -> None:
    rewrite_bundle = build_stage_prompts(
        "rewrite",
        global_prompt="请保持文风统一",
        context={
            "chapter_summary": "主角进入城门。",
            "character_states": [{"name": "主角", "emotion": "紧张"}],
            "preceding_text": "前文",
            "following_text": "后文",
            "rewrite_mode": "expand",
            "anchor": {"paragraph_start_hash": "abc"},
            "segment_text": "原始片段",
            "rewrite_rules": [
                {
                    "scene_type": "战斗",
                    "strategies": ["rewrite", "expand"],
                    "target_ratio": 2.0,
                    "priority": 1,
                }
            ],
        },
    )

    assert "rewrite、expand" in rewrite_bundle.user_prompt
    assert "主策略=expand" in rewrite_bundle.user_prompt
