from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.prompt_templates import PromptTemplateRegistry
from backend.app.llm.interface import ChatMessage

StageNameLiteral = Literal["split", "analyze", "outline", "rewrite"]

DEFAULT_PROMPT_REGISTRY = PromptTemplateRegistry()

SPLIT_SYSTEM_TEMPLATE = """
你是一个章节切分助手。
你的唯一任务是根据正文中的章节边界标记切分文本，不要改写、总结或补充内容。
如果规则命中不稳定，请优先保持边界保守，不要凭空猜测。
""".strip()

SPLIT_USER_TEMPLATE = """
请根据以下正文切分章节，并返回切分边界信息：

{{ source_text }}
""".strip()

ANALYZE_SYSTEM_TEMPLATE = """
你是一个小说章节分析模型。
你的任务是提取章节事实信息，输出必须严格为 JSON，不要输出 Markdown、解释或额外文本。
""".strip()

ANALYZE_USER_TEMPLATE = """
请基于“整章全文”进行场景识别与结构化分析，不要按段落拆成多个独立分析任务。

规则：
1. 先从整章语义判断本章命中的场景，再输出场景列表。
2. `scenes` 中每个场景必须包含：
   - `scene_type`
   - `paragraph_range`（用于定位命中范围）
   - `sentence_range`（可选，建议提供命中句子范围 `[start, end]`）
   - `char_offset_range`（可选，建议提供命中字符范围 `[start, end)`）
   - `rule_hits`（列出命中的触发条件与对应原文证据）
   - `rewrite_potential`
3. `rule_hits` 每个元素包含：
   - `trigger_condition`：命中的触发条件
   - `evidence_text`：章节原文中的命中片段
4. 仅输出 JSON，不要输出额外文本。

{% if scene_rules %}
场景识别规则（仅用于命中判定）：
{% for rule in scene_rules %}
- {{ rule.scene_type }}:
  触发条件={{ rule.get("trigger_conditions", rule.get("keywords", [])) | join("、") }}
{% endfor %}
{% endif %}

{% if output_schema is defined %}
输出必须符合以下 JSON Schema：
{{ output_schema | tojson }}
{% endif %}

章节全文如下：
{{ chapter_text }}
""".strip()

OUTLINE_SYSTEM_TEMPLATE = """
你是一个小说改写规划模型。根据章节原文和已标记的改写片段，为每个片段规划改写范围和边界。
输出必须严格为 JSON，不要输出 Markdown、解释或额外文本。
""".strip()

OUTLINE_USER_TEMPLATE = """
请为以下章节的每个待改写片段规划改写范围。

{% if global_prompt %}
全局提示词：
{{ global_prompt }}
{% endif %}

章节摘要：
{{ chapter_summary }}

人物状态：
{{ character_states | tojson }}

关键事件：
{{ key_events | tojson }}

章节全文：
{{ chapter_text }}

待改写片段列表（按顺序）：
{% for seg in segments %}
- 片段 {{ seg.index }}/{{ seg.total }}:
  ID: {{ seg.segment_id }}
  场景类型: {{ seg.scene_type }}
  改写策略: {{ seg.strategy }}
  建议: {{ seg.suggestion }}
  原文摘录: {{ seg.original_text_preview }}
{% endfor %}

请输出 JSON，格式如下：
{
  "beats": [
    {
      "beat_index": 1,
      "segment_id": "对应的片段ID",
      "scope": "本段应覆盖的具体内容（人物动作、对话、场景描写等）",
      "boundary": "本段应在何处结束，绝对不应写入什么内容（明确指出后续情节点）",
      "tone": "本段基调（如：压抑、紧张、暧昧、温柔等）"
    }
  ]
}

关键要求：
1. 每个片段必须有明确的结束边界，防止剧情跑到后面片段的范围
2. 后续片段才出现的情节、对话、人物反应，绝不能提前出现在前面的片段中
3. 相邻片段之间要有自然的叙事过渡点
4. scope 要具体到"写什么"，不要笼统
5. boundary 要具体到"不写什么"，不要笼统
""".strip()

REWRITE_SYSTEM_TEMPLATE = """
你是一个小说内容改写模型。
你必须只输出”目标片段”的改写正文，不要输出解释、标题、代码块或 Markdown。
禁止输出任何元话术，例如”与原文不同””改写后如下””说明：”。
用户提示中使用 XML 标签标记不同区域：<context_before> 为只读前文上下文，<rewrite_target> 为你需要改写的目标片段。
重要：不要在改写中提前引入目标片段之后才出现的人物姓名、情节或信息。
""".strip()

REWRITE_USER_TEMPLATE = """
请在遵守上下文与规则的前提下改写 <rewrite_target> 中的目标片段。
你只能输出目标片段的最终正文，不能输出前文、后文、说明文字或比较原文的句子。
<context_before> 是只读前文上下文，禁止改写或输出其中的内容。

{% if rewrite_general_guidance %}
改写通用指导：
{{ rewrite_general_guidance }}
{% endif %}

当前片段场景：
{{ segment_scene_type }}

{% if rewrite_rules %}
可用改写规则（按场景）：
{% for rule in rewrite_rules %}
- {{ rule.scene_type }} -> {{ rule.strategies | join("、") }} (主策略={{ rule.primary_strategy }}, target_ratio={{ rule.target_ratio }}, priority={{ rule.priority }})
{% if rule.rewrite_guidance %}  场景指导：{{ rule.rewrite_guidance }}
{% endif %}
{% endfor %}
{% endif %}

章节摘要：
{{ chapter_summary }}

人物状态：
{{ character_states | tojson }}

{% if outline_beat is defined and outline_beat %}
你正在改写本章的第 {{ outline_beat.beat_index }}/{{ outline_total }} 个情节段落。

本段职责：{{ outline_beat.scope }}
本段边界：{{ outline_beat.boundary }}
本段基调：{{ outline_beat.tone }}

{% if following_beats is defined and following_beats %}
后续段落概要（只读，不要提前写入这些内容）：
{% for beat in following_beats %}
- 第{{ beat.beat_index }}段：{{ beat.scope }}
{% endfor %}
{% endif %}
{% endif %}

<context_before>
{{ preceding_context if preceding_context is defined else preceding_text }}
</context_before>

改写模式：
{{ rewrite_mode }}

<rewrite_target>
{{ window_text if window_text is defined else segment_text }}
</rewrite_target>
""".strip()


@dataclass(slots=True)
class StagePromptBundle:
    stage: StageNameLiteral
    system_prompt: str
    user_prompt: str
    messages: list[ChatMessage] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


def get_prompt_registry() -> PromptTemplateRegistry:
    return DEFAULT_PROMPT_REGISTRY


def _validate_stage(stage: str) -> StageNameLiteral:
    if stage not in {"split", "analyze", "rewrite"}:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Unsupported prompt stage `{stage}`")
    return stage  # type: ignore[return-value]


def _normalize_context(context: dict[str, Any] | None = None) -> dict[str, Any]:
    def _as_dict(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        if isinstance(item, dict):
            return dict(item)
        return {"value": item}

    def _normalize_rewrite_rule(item: Any) -> dict[str, Any]:
        payload = _as_dict(item)
        strategies = list(payload.get("strategies") or [])
        if not strategies and payload.get("strategy"):
            strategies = [str(payload["strategy"])]
        primary_from_strategies = (
            "expand"
            if "expand" in strategies
            else (str(strategies[0]) if strategies else str(payload.get("strategy") or ""))
        )
        payload["strategies"] = strategies
        payload["primary_strategy"] = str(payload.get("primary_strategy") or primary_from_strategies)
        payload["rewrite_guidance"] = str(payload.get("rewrite_guidance") or "").strip()
        return payload

    payload = {
        "source_text": "",
        "chapter_text": "",
        "chapter_summary": "",
        "character_states": [],
        "preceding_text": "",
        "following_text": "",
        "preceding_context": "",
        "following_context": "",
        "rewrite_mode": "",
        "anchor": {},
        "segment_scene_type": "",
        "window_text": "",
        "rewrite_general_guidance": "",
        "scene_rules": [],
        "rewrite_rules": [],
    }
    if context:
        payload.update(context)
    if not payload.get("preceding_context"):
        payload["preceding_context"] = payload.get("preceding_text") or ""
    if not payload.get("following_context"):
        payload["following_context"] = payload.get("following_text") or ""
    if not payload.get("window_text"):
        payload["window_text"] = payload.get("segment_text") or ""
    payload["scene_rules"] = [_as_dict(item) for item in (payload.get("scene_rules") or [])]
    payload["rewrite_rules"] = [_normalize_rewrite_rule(item) for item in (payload.get("rewrite_rules") or [])]
    return payload


def _render(template: str, context: dict[str, Any] | None = None, *, registry: PromptTemplateRegistry | None = None) -> str:
    active_registry = registry or get_prompt_registry()
    return active_registry.render(template, context)


def build_stage_prompts(
    stage: str,
    *,
    global_prompt: str = "",
    context: dict[str, Any] | None = None,
    registry: PromptTemplateRegistry | None = None,
) -> StagePromptBundle:
    resolved_stage = _validate_stage(stage)
    active_registry = registry or get_prompt_registry()
    payload = _normalize_context(context)
    payload["global_prompt"] = global_prompt.strip()
    payload["stage_name"] = resolved_stage

    if resolved_stage == "split":
        system_prompt = _render(SPLIT_SYSTEM_TEMPLATE, payload, registry=active_registry)
        user_prompt = _render(SPLIT_USER_TEMPLATE, payload, registry=active_registry)
    elif resolved_stage == "analyze":
        stage_system_prompt = _render(ANALYZE_SYSTEM_TEMPLATE, payload, registry=active_registry)
        system_prompt = (
            f"{stage_system_prompt}\n\n[全局提示词]\n{payload['global_prompt']}"
            if payload["global_prompt"]
            else stage_system_prompt
        )
        user_prompt = _render(ANALYZE_USER_TEMPLATE, payload, registry=active_registry)
    elif resolved_stage == "outline":
        system_prompt = _render(OUTLINE_SYSTEM_TEMPLATE, payload, registry=active_registry)
        user_prompt = _render(OUTLINE_USER_TEMPLATE, payload, registry=active_registry)
    else:
        stage_system_prompt = _render(REWRITE_SYSTEM_TEMPLATE, payload, registry=active_registry)
        system_prompt = (
            f"{stage_system_prompt}\n\n[全局提示词]\n{payload['global_prompt']}"
            if payload["global_prompt"]
            else stage_system_prompt
        )
        user_prompt = _render(REWRITE_USER_TEMPLATE, payload, registry=active_registry)

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]
    return StagePromptBundle(
        stage=resolved_stage,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        messages=messages,
        context=payload,
    )


def build_global_system_prompt(global_prompt: str, stage: str, *, context: dict[str, Any] | None = None) -> str:
    bundle = build_stage_prompts(stage, global_prompt=global_prompt, context=context)
    return bundle.system_prompt


def stage_template_registry_descriptions() -> dict[str, str]:
    return get_prompt_registry().registered_variables()
