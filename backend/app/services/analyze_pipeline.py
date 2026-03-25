from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from fastapi import status
from pydantic import BaseModel

from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.prompt_templates import PromptTemplateRegistry
from backend.app.llm import build_generation_params
from backend.app.llm.client import complete as default_complete
from backend.app.llm.interface import CompletionRequest, CompletionResponse, GenerationParams
from backend.app.llm.prompting import StagePromptBundle, build_stage_prompts
from backend.app.llm.validation import AnalyzeValidationResult, validate_analyze_output
from backend.app.models.core import ChapterAnalysis, ProviderType, SceneRuleHit
from backend.app.services.config_store import SceneRule

ANALYZE_STAGE_NAME = "analyze"
CHAPTER_ANALYSIS_FILE_TEMPLATE = "ch_{chapter_index:03d}_analysis.json"
ANALYSIS_AGGREGATE_FILENAME = "analysis.json"
PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n\s*){2,}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _normalize_scene_rules(scene_rules: Sequence[Any] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for rule in scene_rules or []:
        if isinstance(rule, Mapping):
            trigger_conditions = list(rule.get("trigger_conditions") or rule.get("keywords") or [])
            payload = {
                "scene_type": str(rule.get("scene_type", "")),
                "trigger_conditions": trigger_conditions,
                "weight": float(rule.get("weight", 1.0)),
                "enabled": bool(rule.get("enabled", True)),
            }
            normalized.append(payload)
            continue
        if isinstance(rule, SceneRule):
            normalized.append(rule.model_dump(mode="json"))
            continue
        trigger_conditions = list(
            getattr(rule, "trigger_conditions", None)
            or getattr(rule, "keywords", [])
            or []
        )
        normalized.append(
            {
                "scene_type": str(getattr(rule, "scene_type", "")),
                "trigger_conditions": trigger_conditions,
                "weight": float(getattr(rule, "weight", 1.0)),
                "enabled": bool(getattr(rule, "enabled", True)),
            }
        )
    return normalized


def _split_paragraphs(text: str) -> list[str]:
    parts = [part.strip() for part in PARAGRAPH_SPLIT_RE.split(text) if part.strip()]
    if not parts:
        stripped = text.strip()
        return [stripped] if stripped else []
    return parts


def _range_text(chapter_text: str, paragraph_range: tuple[int, int]) -> str:
    paragraphs = _split_paragraphs(chapter_text)
    if not paragraphs:
        return chapter_text
    start, end = paragraph_range
    if start < 1 or end < start or end > len(paragraphs):
        return chapter_text
    return "\n\n".join(paragraphs[start - 1 : end])


def _extract_hit_evidence(text: str, trigger_condition: str, *, window_chars: int = 48) -> str | None:
    keyword = trigger_condition.strip()
    if not keyword:
        return None
    index = text.find(keyword)
    if index < 0:
        return None
    start = max(0, index - window_chars)
    end = min(len(text), index + len(keyword) + window_chars)
    return text[start:end].strip()


def _enrich_scene_rule_hits(
    analysis: ChapterAnalysis,
    *,
    chapter_text: str,
    scene_rules: Sequence[Any] | None,
) -> ChapterAnalysis:
    normalized_rules = _normalize_scene_rules(scene_rules)
    conditions_by_scene: dict[str, list[str]] = {}
    for item in normalized_rules:
        scene_type = str(item.get("scene_type") or "").strip()
        if not scene_type:
            continue
        scene_key = scene_type.lower()
        values = [str(value).strip() for value in list(item.get("trigger_conditions") or []) if str(value).strip()]
        if not values:
            continue
        bucket = conditions_by_scene.setdefault(scene_key, [])
        for value in values:
            if value not in bucket:
                bucket.append(value)

    if not conditions_by_scene:
        return analysis

    updated_scenes = []
    for scene in analysis.scenes:
        scene_key = scene.scene_type.strip().lower()
        trigger_conditions = conditions_by_scene.get(scene_key)
        if not trigger_conditions:
            updated_scenes.append(scene)
            continue

        area_text = _range_text(chapter_text, scene.paragraph_range)
        existing_pairs = {(hit.trigger_condition, hit.evidence_text) for hit in scene.rule_hits}
        merged_hits = list(scene.rule_hits)

        for condition in trigger_conditions:
            evidence = _extract_hit_evidence(area_text, condition)
            if evidence is None:
                evidence = _extract_hit_evidence(chapter_text, condition)
            if evidence is None:
                continue
            pair = (condition, evidence)
            if pair in existing_pairs:
                continue
            merged_hits.append(SceneRuleHit(trigger_condition=condition, evidence_text=evidence))
            existing_pairs.add(pair)

        updated_scenes.append(scene.model_copy(update={"rule_hits": merged_hits}))

    return analysis.model_copy(update={"scenes": updated_scenes})


def build_analyze_prompt_bundle(
    chapter_text: str,
    *,
    global_prompt: str = "",
    scene_rules: Sequence[Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    registry: PromptTemplateRegistry | None = None,
) -> StagePromptBundle:
    schema = output_schema or ChapterAnalysis.model_json_schema()
    context = {
        "chapter_text": chapter_text,
        "scene_rules": _normalize_scene_rules(scene_rules),
        "output_schema": schema,
    }
    return build_stage_prompts("analyze", global_prompt=global_prompt, context=context, registry=registry)


def build_analyze_completion_request(
    chapter_text: str,
    *,
    model_name: str,
    global_prompt: str = "",
    scene_rules: Sequence[Any] | None = None,
    generation: GenerationParams | Mapping[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    registry: PromptTemplateRegistry | None = None,
) -> tuple[StagePromptBundle, CompletionRequest]:
    prompt_bundle = build_analyze_prompt_bundle(
        chapter_text,
        global_prompt=global_prompt,
        scene_rules=scene_rules,
        output_schema=output_schema,
        registry=registry,
    )
    resolved_generation = build_generation_params(
        provider_defaults=generation,
        per_call_overrides={"response_format": {"type": "json_object"}},
    )
    request = CompletionRequest(
        model_name=model_name,
        messages=prompt_bundle.messages,
        generation=resolved_generation,
        metadata={"stage": ANALYZE_STAGE_NAME, "schema_name": "ChapterAnalysis"},
    )
    return prompt_bundle, request


@dataclass(slots=True)
class AnalyzeChapterRequest:
    novel_id: str
    task_id: str
    chapter_index: int
    chapter_text: str
    chapter_title: str = ""
    chapter_id: str | None = None
    global_prompt: str = ""
    scene_rules: Sequence[Any] = field(default_factory=tuple)
    provider_type: ProviderType = ProviderType.OPENAI_COMPATIBLE
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    generation: GenerationParams | Mapping[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    summary_min_chars: int = 200
    summary_max_chars: int = 500


@dataclass(slots=True)
class AnalyzeChapterResult:
    request: AnalyzeChapterRequest
    analysis: ChapterAnalysis
    validation: AnalyzeValidationResult
    completion: CompletionResponse
    prompt_bundle: StagePromptBundle
    artifact_path: Path | None = None


AnalyzeRunner = Callable[[AnalyzeChapterRequest], Awaitable[AnalyzeChapterResult]]
SubmitFn = Callable[[AnalyzeChapterRequest], Awaitable[AnalyzeChapterResult]]


async def analyze_chapter(
    request: AnalyzeChapterRequest,
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    audit_logger: Any | None = None,
    transport: Any | None = None,
) -> AnalyzeChapterResult:
    if not request.model_name.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "model_name is required")
    if not request.chapter_text.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "chapter_text is required")

    prompt_bundle, completion_request = build_analyze_completion_request(
        request.chapter_text,
        model_name=request.model_name,
        global_prompt=request.global_prompt,
        scene_rules=request.scene_rules,
        generation=request.generation,
        output_schema=request.output_schema,
    )
    started_at = _now_utc()
    completion = await llm_complete(
        request.api_key,
        request.base_url,
        completion_request,
        provider_type=request.provider_type,
        transport=transport,
    )
    duration_ms = max(0, int(((_now_utc() - started_at).total_seconds()) * 1000))
    validation = validate_analyze_output(
        completion.text,
        summary_min_chars=request.summary_min_chars,
        summary_max_chars=request.summary_max_chars,
    )
    # Keep analyze stage resilient for real providers: if schema is valid but summary is shorter
    # than preferred, continue with a soft warning instead of hard-failing the whole stage.
    if (
        not validation.passed
        and validation.error_code == "ANALYZE_SUMMARY_TOO_SHORT"
        and validation.parsed is not None
    ):
        validation = AnalyzeValidationResult(
            passed=True,
            parsed=validation.parsed,
            error_code=validation.error_code,
            error_message=validation.error_message,
            details={
                **dict(validation.details),
                "accepted_with_warning": True,
            },
        )

    if not validation.passed or validation.parsed is None:
        if audit_logger is not None:
            audit_logger.record_call(
                novel_id=request.novel_id,
                chapter_index=request.chapter_index,
                stage=ANALYZE_STAGE_NAME,
                system_prompt=prompt_bundle.system_prompt,
                user_prompt=prompt_bundle.user_prompt,
                params=completion_request.generation.model_dump(exclude_none=True, mode="json"),
                provider=request.provider_type.value,
                model_name=request.model_name,
                response=completion.raw_response or completion.text,
                usage=completion.usage,
                validation=asdict(validation),
                duration_ms=duration_ms,
            )
        raise AppError(
            ErrorCode.STAGE_FAILED,
            validation.error_message or "Analyze output validation failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            details={
                "chapter_index": request.chapter_index,
                "error_code": validation.error_code,
                "validation": validation.details,
            },
        )

    analysis = _enrich_scene_rule_hits(
        validation.parsed,
        chapter_text=request.chapter_text,
        scene_rules=request.scene_rules,
    )
    if audit_logger is not None:
        audit_logger.record_call(
            novel_id=request.novel_id,
            chapter_index=request.chapter_index,
            stage=ANALYZE_STAGE_NAME,
            system_prompt=prompt_bundle.system_prompt,
            user_prompt=prompt_bundle.user_prompt,
            params=completion_request.generation.model_dump(exclude_none=True, mode="json"),
            provider=request.provider_type.value,
            model_name=request.model_name,
            response=completion.raw_response or completion.text,
            usage=completion.usage,
            validation=asdict(validation),
            duration_ms=duration_ms,
        )

    return AnalyzeChapterResult(
        request=request,
        analysis=analysis,
        validation=validation,
        completion=completion,
        prompt_bundle=prompt_bundle,
    )


async def batch_analyze_chapters(
    requests: Sequence[AnalyzeChapterRequest],
    *,
    llm_complete: Callable[..., Awaitable[CompletionResponse]] = default_complete,
    audit_logger: Any | None = None,
    submit: SubmitFn | None = None,
    max_concurrency: int = 4,
    transport: Any | None = None,
) -> list[AnalyzeChapterResult]:
    if submit is not None:
        return await asyncio.gather(*(submit(item) for item in requests))

    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _run(item: AnalyzeChapterRequest) -> AnalyzeChapterResult:
        async with semaphore:
            return await analyze_chapter(
                item,
                llm_complete=llm_complete,
                audit_logger=audit_logger,
                transport=transport,
            )

    return await asyncio.gather(*(_run(item) for item in requests))


def _analysis_artifact_payload(result: AnalyzeChapterResult, *, source: str) -> dict[str, Any]:
    request = result.request
    return {
        "novel_id": request.novel_id,
        "task_id": request.task_id,
        "chapter_index": request.chapter_index,
        "chapter_id": request.chapter_id,
        "chapter_title": request.chapter_title,
        "source": source,
        "analysis": result.analysis.model_dump(mode="json"),
        "summary": result.analysis.summary,
        "validation": _json_safe(result.validation),
        "provider_used": result.completion.provider_type.value,
        "model_name": result.completion.model_name,
        "usage": result.completion.usage.model_dump(mode="json") if result.completion.usage else None,
        "updated_at": _now_utc().isoformat(),
    }


def chapter_analysis_filename(chapter_index: int) -> str:
    return CHAPTER_ANALYSIS_FILE_TEMPLATE.format(chapter_index=chapter_index)


def chapter_analysis_path(artifact_store: ArtifactStore, novel_id: str, task_id: str, chapter_index: int) -> Path:
    return artifact_store.stage_dir(novel_id, task_id, ANALYZE_STAGE_NAME) / chapter_analysis_filename(chapter_index)


def analysis_aggregate_path(artifact_store: ArtifactStore, novel_id: str, task_id: str) -> Path:
    return artifact_store.stage_dir(novel_id, task_id, ANALYZE_STAGE_NAME) / ANALYSIS_AGGREGATE_FILENAME


def write_analysis_artifact(
    artifact_store: ArtifactStore,
    result: AnalyzeChapterResult,
    *,
    source: str = "llm",
) -> Path:
    path = chapter_analysis_path(artifact_store, result.request.novel_id, result.request.task_id, result.request.chapter_index)
    artifact_store.ensure_json(path, _analysis_artifact_payload(result, source=source))
    result.artifact_path = path
    return path


def _load_analysis_artifact(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild_analysis_aggregate(
    artifact_store: ArtifactStore,
    novel_id: str,
    task_id: str,
) -> Path:
    stage_dir = artifact_store.stage_dir(novel_id, task_id, ANALYZE_STAGE_NAME)
    stage_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(stage_dir.glob("ch_*_analysis.json")):
        payload = _load_analysis_artifact(path)
        records.append(payload)
    records.sort(key=lambda item: int(item.get("chapter_index", 0)))
    aggregate = {
        "novel_id": novel_id,
        "task_id": task_id,
        "chapter_count": len(records),
        "updated_at": _now_utc().isoformat(),
        "chapters": records,
    }
    path = analysis_aggregate_path(artifact_store, novel_id, task_id)
    artifact_store.ensure_json(path, aggregate)
    return path


def persist_analysis_result(
    artifact_store: ArtifactStore,
    result: AnalyzeChapterResult,
    *,
    source: str = "llm",
) -> Path:
    write_analysis_artifact(artifact_store, result, source=source)
    return rebuild_analysis_aggregate(artifact_store, result.request.novel_id, result.request.task_id)


def persist_analysis_results(
    artifact_store: ArtifactStore,
    results: Sequence[AnalyzeChapterResult],
    *,
    source: str = "llm",
) -> Path | None:
    for result in results:
        write_analysis_artifact(artifact_store, result, source=source)
    if not results:
        return None
    return rebuild_analysis_aggregate(artifact_store, results[0].request.novel_id, results[0].request.task_id)


def update_analysis_artifact(
    artifact_store: ArtifactStore,
    novel_id: str,
    task_id: str,
    chapter_index: int,
    analysis: ChapterAnalysis,
    *,
    chapter_id: str | None = None,
    chapter_title: str = "",
    source: str = "manual",
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    result = AnalyzeChapterResult(
        request=AnalyzeChapterRequest(
            novel_id=novel_id,
            task_id=task_id,
            chapter_index=chapter_index,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            chapter_text="",
            model_name="",
        ),
        analysis=analysis,
        validation=AnalyzeValidationResult(
            passed=True,
            parsed=analysis,
            details={"source": source, **dict(metadata or {})},
        ),
        completion=CompletionResponse(
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            model_name="",
            text=json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
            latency_ms=0,
            raw_response={},
        ),
        prompt_bundle=StagePromptBundle(stage=ANALYZE_STAGE_NAME, system_prompt="", user_prompt=""),
    )
    return persist_analysis_result(artifact_store, result, source=source)


def load_analysis_aggregate(
    artifact_store: ArtifactStore,
    novel_id: str,
    task_id: str,
) -> dict[str, Any]:
    path = analysis_aggregate_path(artifact_store, novel_id, task_id)
    if not path.exists():
        return {
            "novel_id": novel_id,
            "task_id": task_id,
            "chapter_count": 0,
            "updated_at": None,
            "chapters": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def build_character_trajectory(
    analysis_aggregate: Mapping[str, Any],
    character_name: str,
) -> list[dict[str, Any]]:
    chapters = analysis_aggregate.get("chapters", []) if isinstance(analysis_aggregate, Mapping) else []
    trajectory: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, Mapping):
            continue
        chapter_index = int(chapter.get("chapter_index") or 0)
        chapter_title = str(chapter.get("chapter_title") or "")
        analysis = chapter.get("analysis")
        if not isinstance(analysis, Mapping):
            continue
        for character in analysis.get("characters", []):
            if not isinstance(character, Mapping):
                continue
            if str(character.get("name") or "") != character_name:
                continue
            trajectory.append(
                {
                    "chapter_index": chapter_index,
                    "chapter_title": chapter_title,
                    "emotion": character.get("emotion"),
                    "state": character.get("state"),
                    "role_in_chapter": character.get("role_in_chapter"),
                }
            )
    return trajectory


def chapter_analysis_from_artifact(payload: Mapping[str, Any]) -> ChapterAnalysis:
    analysis = payload.get("analysis")
    if not isinstance(analysis, Mapping):
        raise AppError(ErrorCode.CONFIG_INVALID, "Invalid analysis artifact payload")
    return ChapterAnalysis.model_validate(analysis)


def chapter_analysis_summary(
    analysis_aggregate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for chapter in analysis_aggregate.get("chapters", []):
        if not isinstance(chapter, Mapping):
            continue
        payload = chapter.get("analysis")
        if not isinstance(payload, Mapping):
            continue
        summary.append(
            {
                "chapter_index": int(chapter.get("chapter_index") or 0),
                "chapter_title": str(chapter.get("chapter_title") or ""),
                "summary": str(payload.get("summary") or ""),
                "location": str(payload.get("location") or ""),
                "tone": str(payload.get("tone") or ""),
            }
        )
    return summary
