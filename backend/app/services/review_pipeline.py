"""Post-rewrite review pipeline.

After all segments in a chapter are rewritten, this module reviews the
assembled text for plot advancement issues (剧情超跑) — cases where a
segment's rewrite includes events that only appear in later original text.

If issues are found, the problematic segments can be re-rewritten with
explicit fix constraints.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

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
    RewriteResult,
    RewriteResultStatus,
)

REVIEW_STAGE_NAME = "review"
CHAPTER_REVIEW_FILE_TEMPLATE = "ch_{chapter_index:03d}_review.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str
    problem: str
    fix_boundary: str


class ChapterReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_index: int = Field(ge=1)
    issues: list[ReviewIssue] = Field(default_factory=list)
    all_passed: bool = True


@dataclass(slots=True)
class ReviewChapterRequest:
    novel_id: str
    task_id: str
    chapter: Chapter
    analysis: ChapterAnalysis
    rewrite_results: Sequence[RewriteResult]
    outline: ChapterOutline | None = None
    following_original_text: str = ""
    global_prompt: str = ""
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    generation: GenerationParams | Mapping[str, Any] | None = None
    prompt_registry: PromptTemplateRegistry | None = None


@dataclass(slots=True)
class ReviewChapterResponse:
    request: ReviewChapterRequest
    review: ChapterReviewResult
    completion: CompletionResponse
    prompt_bundle: StagePromptBundle


def build_assembled_text_for_review(chapter: Chapter, results: Sequence[RewriteResult]) -> str:
    """Assemble chapter text with completed rewrites replacing original segments."""
    content = chapter.content
    sorted_results = sorted(
        [r for r in results if r.status == RewriteResultStatus.COMPLETED and r.char_offset_range],
        key=lambda r: r.char_offset_range[0],
        reverse=True,
    )
    for r in sorted_results:
        start, end = r.char_offset_range
        content = content[:start] + r.rewritten_text + content[end:]
    return content


def build_review_completion_request(
    request: ReviewChapterRequest,
    assembled_text: str,
    *,
    registry: PromptTemplateRegistry | None = None,
) -> tuple[StagePromptBundle, CompletionRequest]:
    segments_info = []
    for idx, result in enumerate(request.rewrite_results, 1):
        if result.status != RewriteResultStatus.COMPLETED:
            continue
        beat = None
        if request.outline:
            beat = next((b for b in request.outline.beats if b.segment_id == result.segment_id), None)
        segments_info.append({
            "index": idx,
            "segment_id": result.segment_id,
            "original_text_preview": (result.original_text or "")[:100],
            "rewritten_text_preview": (result.rewritten_text or "")[:200],
            "boundary": beat.boundary if beat else "",
        })

    context = {
        "assembled_text": assembled_text,
        "following_original_text": request.following_original_text,
        "segments": segments_info,
        "global_prompt": request.global_prompt,
    }

    prompt_bundle = build_stage_prompts(
        REVIEW_STAGE_NAME,
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
        metadata={"stage": REVIEW_STAGE_NAME, "chapter_index": request.chapter.index},
    )

    return prompt_bundle, completion_request


def _parse_review(raw_text: str, chapter_index: int, valid_segment_ids: set[str]) -> ChapterReviewResult:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return ChapterReviewResult(chapter_index=chapter_index, issues=[], all_passed=True)

    all_passed = bool(data.get("all_passed", True))
    issues_data = data.get("issues", [])

    if not isinstance(issues_data, list):
        return ChapterReviewResult(chapter_index=chapter_index, issues=[], all_passed=all_passed)

    issues: list[ReviewIssue] = []
    for item in issues_data:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("segment_id", ""))
        if sid not in valid_segment_ids:
            continue
        issues.append(ReviewIssue(
            segment_id=sid,
            problem=str(item.get("problem", "")),
            fix_boundary=str(item.get("fix_boundary", "")),
        ))

    return ChapterReviewResult(
        chapter_index=chapter_index,
        issues=issues,
        all_passed=all_passed and len(issues) == 0,
    )


async def review_chapter_rewrites(
    request: ReviewChapterRequest,
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    transport: Any | None = None,
) -> ReviewChapterResponse:
    completed_results = [r for r in request.rewrite_results if r.status == RewriteResultStatus.COMPLETED]
    if not completed_results:
        empty_review = ChapterReviewResult(chapter_index=request.chapter.index, issues=[], all_passed=True)
        noop_bundle = StagePromptBundle(stage="review", system_prompt="", user_prompt="", context={})
        noop_completion = CompletionResponse(
            provider_type=request.provider_type,
            model_name=request.model_name,
            text="{}",
            finish_reason="skip",
            usage={},
        )
        return ReviewChapterResponse(
            request=request, review=empty_review, completion=noop_completion, prompt_bundle=noop_bundle,
        )

    assembled_text = build_assembled_text_for_review(request.chapter, request.rewrite_results)
    prompt_bundle, completion_request = build_review_completion_request(request, assembled_text)

    completion = await llm_complete(
        request.api_key,
        request.base_url,
        completion_request,
        provider_type=request.provider_type,
        transport=transport,
    )

    valid_ids = {r.segment_id for r in completed_results}
    review = _parse_review(completion.text, request.chapter.index, valid_ids)

    return ReviewChapterResponse(
        request=request,
        review=review,
        completion=completion,
        prompt_bundle=prompt_bundle,
    )


def persist_review_result(
    artifact_store: ArtifactStore,
    novel_id: str,
    task_id: str,
    result: ReviewChapterResponse,
) -> Path:
    stage_dir = artifact_store.stage_dir(novel_id, task_id, "rewrite")
    filename = CHAPTER_REVIEW_FILE_TEMPLATE.format(chapter_index=result.request.chapter.index)
    path = stage_dir / filename
    payload = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_index": result.request.chapter.index,
        "review": result.review.model_dump(mode="json"),
        "all_passed": result.review.all_passed,
        "issues_count": len(result.review.issues),
        "provider_used": result.completion.provider_type.value,
        "model_name": result.completion.model_name,
        "usage": result.completion.usage.model_dump(mode="json") if result.completion.usage else {},
        "updated_at": _now_utc().isoformat(),
    }
    artifact_store.ensure_json(path, payload)
    return path
