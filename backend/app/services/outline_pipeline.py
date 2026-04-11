"""Chapter rewrite outline generation.

Generates a per-chapter narrative outline before rewriting, so each segment
knows its scope, boundary, and tone — preventing plot advancement that
overlaps with later segments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.prompt_templates import PromptTemplateRegistry
from backend.app.llm import build_generation_params
from backend.app.llm.client import complete as default_complete
from backend.app.llm.interface import CompletionRequest, CompletionResponse, GenerationParams
from backend.app.llm.prompting import StagePromptBundle, build_stage_prompts
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    ChapterOutline,
    NarrativeBeat,
    ProviderType,
    RewriteSegment,
)

OUTLINE_STAGE_NAME = "outline"
CHAPTER_OUTLINE_FILE_TEMPLATE = "ch_{chapter_index:03d}_outline.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class OutlineChapterRequest:
    novel_id: str
    task_id: str
    chapter: Chapter
    analysis: ChapterAnalysis
    segments: Sequence[RewriteSegment]
    global_prompt: str = ""
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    generation: GenerationParams | Mapping[str, Any] | None = None
    prompt_registry: PromptTemplateRegistry | None = None


@dataclass(slots=True)
class OutlineChapterResult:
    request: OutlineChapterRequest
    outline: ChapterOutline
    completion: CompletionResponse
    prompt_bundle: StagePromptBundle


def _segment_preview(chapter: Chapter, segment: RewriteSegment, max_chars: int = 200) -> str:
    if segment.char_offset_range:
        start, end = segment.char_offset_range
        text = chapter.content[start:end]
    else:
        import re
        paragraphs = [p.strip() for p in re.split(r"(?:\r?\n\s*){2,}", chapter.content) if p.strip()]
        s, e = segment.paragraph_range
        text = "\n\n".join(paragraphs[s - 1 : e])
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def build_outline_completion_request(
    request: OutlineChapterRequest,
    *,
    registry: PromptTemplateRegistry | None = None,
) -> tuple[StagePromptBundle, CompletionRequest]:
    segment_items = []
    for idx, seg in enumerate(request.segments, 1):
        segment_items.append({
            "index": idx,
            "total": len(request.segments),
            "segment_id": seg.segment_id,
            "scene_type": seg.scene_type,
            "strategy": seg.strategy.value if hasattr(seg.strategy, "value") else str(seg.strategy),
            "original_text_preview": _segment_preview(request.chapter, seg),
            "suggestion": seg.suggestion,
        })

    context = {
        "chapter_summary": request.analysis.summary,
        "character_states": [c.model_dump(mode="json") for c in request.analysis.characters],
        "key_events": [e.model_dump(mode="json") for e in request.analysis.key_events],
        "chapter_text": request.chapter.content,
        "segments": segment_items,
        "global_prompt": request.global_prompt,
    }

    prompt_bundle = build_stage_prompts(
        OUTLINE_STAGE_NAME,
        global_prompt=request.global_prompt,
        context=context,
        registry=registry or request.prompt_registry,
    )

    resolved_generation = build_generation_params(
        provider_defaults=request.generation,
        per_call_overrides={"response_format": {"type": "json_object"}},
    )

    completion_request = CompletionRequest(
        model_name=request.model_name,
        messages=prompt_bundle.messages,
        generation=resolved_generation,
        metadata={"stage": OUTLINE_STAGE_NAME, "chapter_index": request.chapter.index},
    )

    return prompt_bundle, completion_request


def _parse_outline(raw_text: str, chapter_index: int, segments: Sequence[RewriteSegment]) -> ChapterOutline:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return _fallback_outline(chapter_index, segments)

    beats_data = data.get("beats", [])
    if not isinstance(beats_data, list) or not beats_data:
        return _fallback_outline(chapter_index, segments)

    segment_ids = {seg.segment_id for seg in segments}
    beats: list[NarrativeBeat] = []
    for item in beats_data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("segment_id", ""))
        if sid not in segment_ids:
            continue
        beats.append(NarrativeBeat(
            beat_index=int(item.get("beat_index", len(beats) + 1)),
            segment_id=sid,
            scope=str(item.get("scope", "")),
            boundary=str(item.get("boundary", "")),
            tone=str(item.get("tone", "")),
        ))

    if not beats:
        return _fallback_outline(chapter_index, segments)

    return ChapterOutline(
        chapter_index=chapter_index,
        total_beats=len(beats),
        beats=beats,
    )


def _fallback_outline(chapter_index: int, segments: Sequence[RewriteSegment]) -> ChapterOutline:
    beats = [
        NarrativeBeat(
            beat_index=idx + 1,
            segment_id=seg.segment_id,
            scope=seg.suggestion or f"{seg.scene_type}场景改写",
            boundary="",
            tone="",
        )
        for idx, seg in enumerate(segments)
    ]
    return ChapterOutline(
        chapter_index=chapter_index,
        total_beats=len(beats),
        beats=beats,
    )


async def generate_chapter_outline(
    request: OutlineChapterRequest,
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> OutlineChapterResult:
    if not request.segments:
        empty_outline = ChapterOutline(chapter_index=request.chapter.index, total_beats=0, beats=[])
        noop_bundle = StagePromptBundle(stage="outline", system_prompt="", user_prompt="", context={})
        noop_completion = CompletionResponse(
            provider_type=request.provider_type,
            model_name=request.model_name,
            text="{}",
            finish_reason="skip",
            usage={},
        )
        return OutlineChapterResult(
            request=request, outline=empty_outline, completion=noop_completion, prompt_bundle=noop_bundle,
        )

    prompt_bundle, completion_request = build_outline_completion_request(request)

    completion = await llm_complete(
        request.api_key,
        request.base_url,
        completion_request,
        provider_type=request.provider_type,
        transport=transport,
    )

    outline = _parse_outline(completion.text, request.chapter.index, request.segments)

    return OutlineChapterResult(
        request=request,
        outline=outline,
        completion=completion,
        prompt_bundle=prompt_bundle,
    )


def persist_outline_result(
    artifact_store: ArtifactStore,
    novel_id: str,
    task_id: str,
    result: OutlineChapterResult,
) -> Path:
    stage_dir = artifact_store.stage_dir(novel_id, task_id, "rewrite")
    filename = CHAPTER_OUTLINE_FILE_TEMPLATE.format(chapter_index=result.request.chapter.index)
    path = stage_dir / filename
    payload = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_index": result.request.chapter.index,
        "outline": result.outline.model_dump(mode="json"),
        "provider_used": result.completion.provider_type.value,
        "model_name": result.completion.model_name,
        "usage": result.completion.usage.model_dump(mode="json") if result.completion.usage else {},
        "updated_at": _now_utc().isoformat(),
    }
    artifact_store.ensure_json(path, payload)
    return path
