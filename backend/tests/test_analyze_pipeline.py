from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.llm.interface import CompletionResponse, GenerationParams, UsageInfo
from backend.app.llm.validation import AnalyzeValidationResult
from backend.app.models.core import ChapterAnalysis, CharacterState, KeyEvent, ProviderType, RewritePotential, SceneSegment
from backend.app.services.analyze_pipeline import (
    AnalyzeChapterRequest,
    AnalyzeChapterResult,
    ANALYZE_STAGE_NAME,
    analysis_aggregate_path,
    analyze_chapter,
    batch_analyze_chapters,
    build_analyze_completion_request,
    build_analyze_prompt_bundle,
    build_character_trajectory,
    chapter_analysis_from_artifact,
    chapter_analysis_path,
    chapter_analysis_summary,
    load_analysis_aggregate,
    persist_analysis_result,
    persist_analysis_results,
    update_analysis_artifact,
)
from backend.app.services.config_store import SceneRule


def _long_summary() -> str:
    sentence = "主角在城门外观察局势，判断敌我态势，随后与同伴交换情报，确认下一步行动路线，并在紧张的气氛中重新梳理自身处境与目标。"
    return (sentence * 4)[:320]


def _analysis(*, location: str = "城门", tone: str = "紧张", character_name: str = "主角") -> ChapterAnalysis:
    summary = _long_summary()
    return ChapterAnalysis(
        summary=summary,
        characters=[
            CharacterState(
                name=character_name,
                emotion="警惕",
                state="观察局势",
                role_in_chapter="主视角",
            )
        ],
        key_events=[
            KeyEvent(
                description="主角观察局势并交换情报",
                event_type="观察",
                importance=4,
                paragraph_range=(1, 2),
            )
        ],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(2, 4),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="增加动作和感官细节",
                    priority=5,
                ),
            )
        ],
        location=location,
        tone=tone,
    )


def _chapter_text() -> str:
    return "第一章\n\n主角在城门外观察局势，随后进入城中。"


def _request(*, chapter_index: int, chapter_text: str | None = None, chapter_title: str = "第一章") -> AnalyzeChapterRequest:
    return AnalyzeChapterRequest(
        novel_id="novel-1",
        task_id="task-1",
        chapter_index=chapter_index,
        chapter_title=chapter_title,
        chapter_id=f"chapter-{chapter_index}",
        chapter_text=chapter_text or _chapter_text(),
        global_prompt="你是一个严谨的章节分析器。",
        scene_rules=[
            SceneRule(scene_type="战斗", keywords=["战斗", "厮杀"], weight=1.2, enabled=True),
        ],
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model_name="gpt-test",
        generation=GenerationParams(temperature=0.2, max_tokens=512),
    )


def _completion_from_analysis(analysis: ChapterAnalysis) -> CompletionResponse:
    return CompletionResponse(
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        model_name="gpt-test",
        text=json.dumps(analysis.model_dump(mode="json"), ensure_ascii=False),
        latency_ms=18,
        usage=UsageInfo(prompt_tokens=120, completion_tokens=80, total_tokens=200),
        raw_response={"choices": [{"message": {"content": analysis.summary}}]},
    )


def _validation_from_analysis(analysis: ChapterAnalysis) -> AnalyzeValidationResult:
    return AnalyzeValidationResult(
        passed=True,
        parsed=analysis,
        details={"summary_chars": len(analysis.summary), "schema_name": "ChapterAnalysis"},
    )


class _AuditLogger:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_call(self, **kwargs) -> None:  # noqa: ANN003
        self.calls.append(kwargs)


def test_build_analyze_prompt_bundle_injects_schema_and_rules() -> None:
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "marker": {"type": "string"},
        },
        "required": ["summary", "marker"],
    }

    bundle = build_analyze_prompt_bundle(
        "章节正文",
        global_prompt="全局提示词",
        scene_rules=[SceneRule(scene_type="战斗", keywords=["战斗"], weight=1.0, enabled=True)],
        output_schema=schema,
    )

    assert bundle.stage == ANALYZE_STAGE_NAME
    assert bundle.context["output_schema"] == schema
    assert bundle.context["scene_rules"][0]["scene_type"] == "战斗"
    assert "全局提示词" in bundle.system_prompt
    assert "marker" in bundle.user_prompt
    assert "章节正文" in bundle.user_prompt


def test_build_analyze_completion_request_forces_json_response() -> None:
    _, request = build_analyze_completion_request(
        "章节正文",
        model_name="gpt-test",
        global_prompt="全局提示词",
        scene_rules=[SceneRule(scene_type="战斗", keywords=["战斗"], weight=1.0, enabled=True)],
        generation=GenerationParams(temperature=0.3, max_tokens=256),
    )

    assert request.metadata["stage"] == ANALYZE_STAGE_NAME
    assert request.metadata["schema_name"] == "ChapterAnalysis"
    assert request.generation.response_format == {"type": "json_object"}
    assert request.generation.temperature == 0.3
    assert request.messages[0].role == "system"


def test_analyze_chapter_parses_and_records_valid_output() -> None:
    req = _request(chapter_index=1)
    analysis = _analysis()
    audit_logger = _AuditLogger()
    seen: dict[str, object] = {}

    async def fake_complete(api_key, base_url, request, *, provider_type, transport=None):  # noqa: ANN001, ANN003
        seen["api_key"] = api_key
        seen["base_url"] = base_url
        seen["provider_type"] = provider_type
        seen["request"] = request
        return _completion_from_analysis(analysis)

    result = asyncio.run(analyze_chapter(req, llm_complete=fake_complete, audit_logger=audit_logger))

    assert result.analysis.summary == analysis.summary
    assert result.validation.passed is True
    assert result.prompt_bundle.context["scene_rules"][0]["scene_type"] == "战斗"
    assert seen["api_key"] == "sk-test"
    assert seen["base_url"] == "https://example.invalid/v1"
    assert seen["provider_type"] == ProviderType.OPENAI_COMPATIBLE
    assert audit_logger.calls and audit_logger.calls[0]["validation"]["passed"] is True
    assert audit_logger.calls[0]["stage"] == ANALYZE_STAGE_NAME


def test_analyze_chapter_rejects_invalid_json() -> None:
    req = _request(chapter_index=1)

    async def fake_complete(api_key, base_url, request, *, provider_type, transport=None):  # noqa: ANN001, ANN003
        return CompletionResponse(
            provider_type=provider_type,
            model_name=request.model_name,
            text="{not json",
            latency_ms=5,
            raw_response={"text": "{not json"},
        )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(analyze_chapter(req, llm_complete=fake_complete))

    assert exc_info.value.code == ErrorCode.STAGE_FAILED
    assert exc_info.value.details["error_code"] == "ANALYZE_SCHEMA_INVALID"


def test_analyze_chapter_accepts_short_summary_with_warning() -> None:
    req = _request(chapter_index=1)
    short_analysis = _analysis()
    short_analysis = short_analysis.model_copy(update={"summary": "摘要过短"})
    audit_logger = _AuditLogger()

    async def fake_complete(api_key, base_url, request, *, provider_type, transport=None):  # noqa: ANN001, ANN003
        return _completion_from_analysis(short_analysis)

    result = asyncio.run(analyze_chapter(req, llm_complete=fake_complete, audit_logger=audit_logger))

    assert result.analysis.summary == "摘要过短"
    assert result.validation.passed is True
    assert result.validation.error_code == "ANALYZE_SUMMARY_TOO_SHORT"
    assert result.validation.details.get("accepted_with_warning") is True
    assert audit_logger.calls and audit_logger.calls[0]["validation"]["passed"] is True


def test_analyze_chapter_enriches_scene_rule_hits_from_chapter_text() -> None:
    chapter_text = "\n\n".join(
        [
            "第一段铺垫场景。",
            "第二段进入战斗现场。",
            "第三段持续战斗并升级冲突。",
            "第四段收束战斗并撤离。",
        ]
    )
    req = _request(chapter_index=1, chapter_text=chapter_text)
    analysis = _analysis()

    async def fake_complete(api_key, base_url, request, *, provider_type, transport=None):  # noqa: ANN001, ANN003
        return _completion_from_analysis(analysis)

    result = asyncio.run(analyze_chapter(req, llm_complete=fake_complete))

    assert result.validation.passed is True
    assert result.analysis.scenes
    first_scene_hits = result.analysis.scenes[0].rule_hits
    assert first_scene_hits
    assert any(hit.trigger_condition == "战斗" for hit in first_scene_hits)
    assert any("战斗" in hit.evidence_text for hit in first_scene_hits)


def test_persist_analysis_result_writes_chapter_and_aggregate_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    result = AnalyzeChapterResult(
        request=_request(chapter_index=1),
        analysis=_analysis(),
        validation=_validation_from_analysis(_analysis()),
        completion=_completion_from_analysis(_analysis()),
        prompt_bundle=build_analyze_prompt_bundle(_chapter_text(), global_prompt="全局提示词"),
    )

    aggregate_path = persist_analysis_result(store, result)

    per_chapter_path = chapter_analysis_path(store, "novel-1", "task-1", 1)
    aggregate = json.loads(Path(aggregate_path).read_text(encoding="utf-8"))
    chapter_payload = json.loads(per_chapter_path.read_text(encoding="utf-8"))

    assert Path(aggregate_path).exists()
    assert per_chapter_path.exists()
    assert aggregate["chapter_count"] == 1
    assert aggregate["chapters"][0]["analysis"]["location"] == "城门"
    assert chapter_payload["chapter_index"] == 1
    assert chapter_payload["summary"] == result.analysis.summary


def test_rebuild_and_update_analysis_artifact_overwrite_aggregate(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    first = AnalyzeChapterResult(
        request=_request(chapter_index=1, chapter_title="第一章"),
        analysis=_analysis(location="城门", tone="紧张", character_name="主角"),
        validation=_validation_from_analysis(_analysis(location="城门", tone="紧张", character_name="主角")),
        completion=_completion_from_analysis(_analysis(location="城门", tone="紧张", character_name="主角")),
        prompt_bundle=build_analyze_prompt_bundle(_chapter_text(), global_prompt="全局提示词"),
    )
    second = AnalyzeChapterResult(
        request=_request(chapter_index=2, chapter_title="第二章"),
        analysis=_analysis(location="客栈", tone="平静", character_name="同伴"),
        validation=_validation_from_analysis(_analysis(location="客栈", tone="平静", character_name="同伴")),
        completion=_completion_from_analysis(_analysis(location="客栈", tone="平静", character_name="同伴")),
        prompt_bundle=build_analyze_prompt_bundle(_chapter_text(), global_prompt="全局提示词"),
    )

    persist_analysis_results(store, [first, second])
    updated_path = update_analysis_artifact(
        store,
        "novel-1",
        "task-1",
        2,
        _analysis(location="山路", tone="压迫", character_name="同伴"),
        chapter_title="第二章",
        metadata={"edited": True},
    )

    aggregate = load_analysis_aggregate(store, "novel-1", "task-1")
    summary = chapter_analysis_summary(aggregate)
    trajectory = build_character_trajectory(aggregate, "同伴")

    assert Path(updated_path) == analysis_aggregate_path(store, "novel-1", "task-1")
    assert aggregate["chapter_count"] == 2
    assert summary[1]["location"] == "山路"
    assert trajectory[0]["chapter_index"] == 2
    assert trajectory[0]["chapter_title"] == "第二章"


def test_batch_analyze_chapters_uses_submit_hook() -> None:
    req1 = _request(chapter_index=1)
    req2 = _request(chapter_index=2, chapter_title="第二章")
    seen: list[int] = []

    async def submit(item: AnalyzeChapterRequest) -> AnalyzeChapterResult:
        seen.append(item.chapter_index)
        analysis = _analysis(character_name=f"人物{item.chapter_index}")
        return AnalyzeChapterResult(
            request=item,
            analysis=analysis,
            validation=_validation_from_analysis(analysis),
            completion=_completion_from_analysis(analysis),
            prompt_bundle=build_analyze_prompt_bundle(item.chapter_text, global_prompt=item.global_prompt),
        )

    results = asyncio.run(batch_analyze_chapters([req1, req2], submit=submit))

    assert seen == [1, 2]
    assert [item.request.chapter_index for item in results] == [1, 2]


def test_chapter_analysis_from_artifact_and_load_defaults(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    aggregate = load_analysis_aggregate(store, "novel-1", "task-1")

    assert aggregate["chapter_count"] == 0
    assert aggregate["chapters"] == []

    chapter_payload = {
        "analysis": _analysis().model_dump(mode="json"),
    }
    parsed = chapter_analysis_from_artifact(chapter_payload)

    assert parsed.location == "城门"
