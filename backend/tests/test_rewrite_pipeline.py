from __future__ import annotations

import asyncio
from pathlib import Path
from dataclasses import replace

from backend.app.llm.interface import CompletionResponse, UsageInfo
from backend.app.models.core import (
    Chapter,
    ChapterAnalysis,
    CharacterState,
    Paragraph,
    ProviderType,
    RewriteAnchor,
    RewriteResult,
    RewriteResultStatus,
    RewriteSegment,
    RewriteStrategy,
    RewritePotential,
    SceneSegment,
)
from backend.app.services.config_store import RewriteRule
from backend.app.services.marking import build_anchor
from backend.app.services.rewrite_pipeline import (
    _normalize_rewrite_completion_text,
    RewriteChapterRequest,
    RewriteSegmentRequest,
    batch_rewrite_chapters,
    build_rewrite_prompt_bundle,
    extract_segment_source_text,
    execute_rewrite_chapter,
    execute_rewrite_segment,
    validate_rewrite_anchor,
)


def _chapter() -> Chapter:
    content = "\n\n".join(
        [
            "第一段战斗动作很快，敌我交锋十分激烈。",
            "第二段主角稳住呼吸，继续推进。",
            "第三段对手后撤，气氛再次绷紧。",
            "第四段战斗结束，众人短暂沉默。",
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
            Paragraph(index=1, start_offset=0, end_offset=18, char_count=18),
            Paragraph(index=2, start_offset=20, end_offset=31, char_count=11),
            Paragraph(index=3, start_offset=33, end_offset=46, char_count=13),
            Paragraph(index=4, start_offset=48, end_offset=61, char_count=13),
        ],
    )


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="主角在城门外与敌人交锋，局势紧张。",
        characters=[
            CharacterState(name="主角", emotion="紧张", state="防守", role_in_chapter="主视角"),
            CharacterState(name="敌人", emotion="凶狠", state="进攻", role_in_chapter="对手"),
        ],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 2),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="增强动作与感官描写",
                    priority=5,
                ),
            ),
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(3, 4),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充对手反应",
                    priority=4,
                ),
            ),
        ],
        location="城门外",
        tone="紧张",
    )


def _rewrite_rule() -> RewriteRule:
    return RewriteRule(scene_type="战斗", strategy="rewrite", target_ratio=1.2, priority=0, enabled=True)


def _segment(chapter: Chapter, paragraph_range: tuple[int, int] = (1, 2)) -> RewriteSegment:
    original_text = "\n\n".join(chapter.content.split("\n\n")[paragraph_range[0] - 1 : paragraph_range[1]])
    return RewriteSegment(
        anchor=build_anchor(chapter, paragraph_range),
        paragraph_range=paragraph_range,
        scene_type="战斗",
        original_chars=len(original_text),
        strategy=RewriteStrategy.REWRITE,
        target_ratio=1.2,
        target_chars=max(1, len(original_text) + 4),
        target_chars_min=1,
        target_chars_max=1000,
        suggestion="建议改写",
        source="auto",
        confirmed=False,
    )


def test_build_rewrite_prompt_bundle_injects_context() -> None:
    chapter = _chapter()
    analysis = _analysis()
    segment = _segment(chapter, (1, 2))
    preceding_text = "前文" * 220
    following_text = "后文" * 220

    bundle = build_rewrite_prompt_bundle(
        chapter,
        analysis,
        segment,
        global_prompt="请保持文风统一",
        rewrite_rules=[_rewrite_rule()],
        preceding_text=preceding_text,
        following_text=following_text,
        rewrite_mode="rewrite",
    )

    assert "请保持文风统一" in bundle.system_prompt
    assert "主角在城门外与敌人交锋" in bundle.user_prompt
    assert "主角" in bundle.user_prompt
    assert "rewrite" in bundle.user_prompt
    assert "第一段战斗动作很快" in bundle.user_prompt
    assert "可改写窗口正文" in bundle.user_prompt
    assert "只读，不可改写" in bundle.user_prompt
    assert bundle.context["preceding_text"] == preceding_text[-300:]
    assert bundle.context["following_text"] == following_text[:300]
    assert bundle.context["window_text"].startswith("第一段战斗动作很快")
    assert bundle.context["rewrite_mode"] == "rewrite"
    assert bundle.context["segment_text"].startswith("第一段战斗动作很快")


def test_normalize_rewrite_completion_text_strips_meta_phrases() -> None:
    text = "与原文不同，此刻她抬起眼。"
    assert _normalize_rewrite_completion_text(text) == "此刻她抬起眼。"

    fenced = "```text\n改写后如下：\n她轻轻点头。\n```"
    assert _normalize_rewrite_completion_text(fenced) == "她轻轻点头。"


def test_validate_rewrite_anchor_detects_mismatch() -> None:
    chapter = _chapter()
    segment = _segment(chapter, (1, 2))

    success = validate_rewrite_anchor(chapter, segment)
    assert success.passed is True
    assert success.expected_anchor == segment.anchor

    mismatch = segment.model_copy(update={"anchor": segment.anchor.model_copy(update={"range_text_hash": "bad"})})
    failed = validate_rewrite_anchor(chapter, mismatch)
    assert failed.passed is False
    assert failed.error_code == "ANCHOR_MISMATCH"
    assert failed.details["mismatch_fields"]["range_text_hash"]["current"] == "bad"


def test_extract_segment_source_text_prefers_char_offset_range() -> None:
    chapter = _chapter()
    segment = _segment(chapter, (1, 2))
    selected = "第二段主角稳住呼吸，继续推进。"
    start = chapter.content.index(selected)
    end = start + len(selected)
    segment = segment.model_copy(update={"char_offset_range": (start, end)})

    source_text, details = extract_segment_source_text(chapter, segment)

    assert source_text == selected
    assert details["source"] == "char_offset_range"


def test_extract_segment_source_text_falls_back_to_paragraph_range() -> None:
    chapter = _chapter()
    segment = _segment(chapter, (1, 2)).model_copy(
        update={"char_offset_range": (len(chapter.content) + 2, len(chapter.content) + 9)}
    )

    source_text, details = extract_segment_source_text(chapter, segment)

    expected = "\n\n".join(chapter.content.split("\n\n")[0:2])
    assert source_text == expected
    assert details["source"] == "paragraph_range"
    assert details["fallback_reason"]["reason"] == "char_offset_range_out_of_bounds"


def test_execute_rewrite_segment_returns_completed_result_and_skips_on_anchor_mismatch() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2))
        calls: list[str] = []

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            calls.append(request.metadata["segment_id"])
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="改写后的章节正文",
                latency_ms=12,
                usage=UsageInfo(prompt_tokens=12, completion_tokens=18, total_tokens=30),
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.anchor_verified is True
        assert result.rewritten_text == "改写后的章节正文"
        assert result.provider_used == "openai_compatible"
        assert result.attempts == 1
        assert result.completion_kind == "normal"
        assert result.has_warnings is False
        assert result.warning_count == 0
        assert result.warning_codes == []
        assert result.char_offset_range == segment.char_offset_range
        assert len(result.rewrite_windows) == len(segment.rewrite_windows)
        assert calls == [segment.segment_id]

        bad_request = replace(
            request,
            segment=segment.model_copy(update={"anchor": segment.anchor.model_copy(update={"context_window_hash": "bad"})}),
        )
        failed = await execute_rewrite_segment(bad_request, llm_complete=fake_complete)
        assert failed.status == RewriteResultStatus.FAILED
        assert failed.error_code == "ANCHOR_MISMATCH"
        assert failed.anchor_verified is False
        assert failed.has_warnings is True
        assert failed.warning_codes == ["ANCHOR_MISMATCH"]
        assert len(calls) == 1

    asyncio.run(_run())


def test_execute_rewrite_segment_records_slice_fallback_details() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"char_offset_range": (-1, 5)})

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="改写后的章节正文",
                latency_ms=12,
                usage=UsageInfo(prompt_tokens=12, completion_tokens=18, total_tokens=30),
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.validation_details is not None
        assert result.validation_details["source_slice"]["source"] == "paragraph_range"
        assert result.validation_details["source_slice"]["fallback_reason"]["reason"] == "invalid_char_offset_range"

    asyncio.run(_run())


def test_execute_rewrite_segment_keeps_output_when_length_out_of_range() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"target_chars_min": 200, "target_chars_max": 260})

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="长度较短的改写文本",
                latency_ms=9,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=11, total_tokens=21),
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        expected_original = "\n\n".join(chapter.content.split("\n\n")[0:2])
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.rewritten_text == expected_original
        assert result.has_warnings is True
        assert "REWRITE_LENGTH_SEVERE_OUTLIER" in (result.warning_codes or [])
        assert len(result.window_attempts) == 2
        assert result.validation_details is not None
        assert result.validation_details["actual_chars"] == len("长度较短的改写文本")

    asyncio.run(_run())


def test_execute_rewrite_segment_keeps_overshoot_output_without_error() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"target_chars_min": 40, "target_chars_max": 80})
        overshoot_text = "这是明显超长的改写内容。" * 20

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text=overshoot_text,
                latency_ms=9,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=11, total_tokens=21),
                raw_response={"choices": [{"finish_reason": "stop"}], "request_id": "req-length-overshoot"},
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.rewritten_text == overshoot_text
        assert result.error_code is None
        assert result.has_warnings is True
        assert "REWRITE_LENGTH_SEVERE_OUTLIER" in (result.warning_codes or [])
        assert len(result.window_attempts) == 1
        assert result.window_attempts[0].action == "accepted"

    asyncio.run(_run())


def test_execute_rewrite_segment_retries_on_fragment_and_accepts_second_attempt() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"target_chars_min": 6, "target_chars_max": 120})
        call = 0

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            nonlocal call
            call += 1
            if call == 1:
                text = "，突然加速，气氛骤紧。"
            else:
                text = "他突然加速，气氛骤紧。"
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text=text,
                latency_ms=8,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=12, total_tokens=22),
                raw_response={"choices": [{"finish_reason": "stop"}], "request_id": f"req-{call}"},
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.rewritten_text == "他突然加速，气氛骤紧。"
        assert len(result.window_attempts) == 2
        assert result.window_attempts[0].action == "retry"
        assert result.window_attempts[-1].action == "accepted"

    asyncio.run(_run())


def test_execute_rewrite_segment_auto_splits_long_text_and_merges() -> None:
    async def _run() -> None:
        sentence = "她抬头望向夜色，心跳渐快。"
        paragraphs = [sentence * 34 for _ in range(18)]
        content = "\n\n".join(paragraphs)
        chapter = Chapter(
            id="chapter-long",
            index=1,
            title="超长章节",
            content=content,
            char_count=len(content),
            paragraph_count=len(paragraphs),
            start_offset=0,
            end_offset=len(content),
            paragraphs=[],
        )
        analysis = ChapterAnalysis(
            summary="长章节改写测试。",
            characters=[],
            key_events=[],
            scenes=[],
            location="城市夜色",
            tone="紧张",
        )
        segment = RewriteSegment(
            anchor=build_anchor(chapter, (1, len(paragraphs))),
            paragraph_range=(1, len(paragraphs)),
            scene_type="战斗",
            original_chars=len(content),
            strategy=RewriteStrategy.REWRITE,
            target_ratio=1.1,
            target_chars=max(1, int(len(content) * 1.1)),
            target_chars_min=1,
            target_chars_max=200000,
            suggestion="超长段自动拆分",
            source="auto",
            confirmed=False,
        )

        calls: list[int] = []

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            calls.append(len(calls) + 1)
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text=f"改写片段{len(calls)}：" + ("续" * 3200),
                latency_ms=10,
                usage=UsageInfo(prompt_tokens=40, completion_tokens=40, total_tokens=80),
                raw_response={"choices": [{"finish_reason": "stop"}]},
            )

        rewrite_request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2, "max_tokens": 1024},
        )

        result = await execute_rewrite_segment(rewrite_request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.attempts > 1
        assert len(calls) == result.attempts
        assert "改写片段1" in result.rewritten_text
        assert result.validation_details is not None
        auto_split = result.validation_details.get("auto_split")
        assert isinstance(auto_split, dict)
        assert int(auto_split["parts_total"]) > 1
        assert isinstance(result.provider_raw_response, dict)
        assert "auto_split" in result.provider_raw_response

    asyncio.run(_run())


def test_execute_rewrite_segment_auto_split_fails_when_part_output_too_short() -> None:
    async def _run() -> None:
        sentence = "她抬头望向夜色，心跳渐快。"
        paragraphs = [sentence * 34 for _ in range(18)]
        content = "\n\n".join(paragraphs)
        chapter = Chapter(
            id="chapter-long-short-output",
            index=1,
            title="超长章节",
            content=content,
            char_count=len(content),
            paragraph_count=len(paragraphs),
            start_offset=0,
            end_offset=len(content),
            paragraphs=[],
        )
        analysis = ChapterAnalysis(
            summary="长章节改写测试。",
            characters=[],
            key_events=[],
            scenes=[],
            location="城市夜色",
            tone="紧张",
        )
        segment = RewriteSegment(
            anchor=build_anchor(chapter, (1, len(paragraphs))),
            paragraph_range=(1, len(paragraphs)),
            scene_type="战斗",
            original_chars=len(content),
            strategy=RewriteStrategy.REWRITE,
            target_ratio=1.1,
            target_chars=max(1, int(len(content) * 1.1)),
            target_chars_min=1,
            target_chars_max=200000,
            suggestion="超长段自动拆分",
            source="auto",
            confirmed=False,
        )

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="过短输出",
                latency_ms=10,
                usage=UsageInfo(prompt_tokens=40, completion_tokens=40, total_tokens=80),
                raw_response={"choices": [{"finish_reason": "stop"}]},
            )

        rewrite_request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2, "max_tokens": 1024},
        )

        result = await execute_rewrite_segment(rewrite_request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.has_warnings is True
        assert "REWRITE_LENGTH_SEVERE_OUTLIER" in (result.warning_codes or [])
        assert len(result.window_attempts) == 2
        assert result.validation_details is not None
        auto_split = result.validation_details.get("auto_split")
        assert isinstance(auto_split, dict)
        assert int(auto_split["failed_part_index"]) == 1

    asyncio.run(_run())


def test_execute_rewrite_chapter_and_batch_keep_chapter_order() -> None:
    async def _run() -> None:
        chapter = _chapter()
        chapter_two_content = "\n\n".join(["第二章第一段继续战斗。", "第二章第二段收束。"])
        chapter_two = Chapter(
            id="chapter-2",
            index=2,
            title="第二章",
            content=chapter_two_content,
            char_count=len(chapter_two_content),
            paragraph_count=2,
            start_offset=0,
            end_offset=len(chapter_two_content),
            paragraphs=[
                Paragraph(index=1, start_offset=0, end_offset=10, char_count=10),
                Paragraph(index=2, start_offset=12, end_offset=18, char_count=6),
            ],
        )
        analysis_one = _analysis()
        analysis_two = ChapterAnalysis(
            summary="第二章继续推进战斗。",
            characters=[CharacterState(name="主角", emotion="警惕", state="继续作战", role_in_chapter="主视角")],
            key_events=[],
            scenes=[
                SceneSegment(
                    scene_type="战斗",
                    paragraph_range=(1, 1),
                    rewrite_potential=RewritePotential(
                        expandable=True,
                        rewritable=True,
                        suggestion="补足第二章战斗节奏",
                        priority=5,
                    ),
                )
            ],
            location="第二章地点",
            tone="紧张",
        )

        chapter_request = RewriteChapterRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis_one,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )
        chapter_two_request = RewriteChapterRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter_two,
            analysis=analysis_two,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
        )

        started: list[tuple[int, str]] = []
        release = asyncio.Event()

        async def fake_submit(item: RewriteSegmentRequest) -> RewriteResult:
            started.append((item.chapter.index, item.segment.segment_id))
            if len(started) >= 2:
                release.set()
            await release.wait()
            return RewriteResult(
                segment_id=item.segment.segment_id,
                chapter_index=item.chapter.index,
                paragraph_range=item.segment.paragraph_range,
                anchor_verified=True,
                strategy=item.segment.strategy,
                original_text="原文",
                rewritten_text="改写文",
                original_chars=2,
                rewritten_chars=3,
                status=RewriteResultStatus.COMPLETED,
                attempts=1,
                provider_used=item.provider_type.value,
        )

        chapter_task = asyncio.create_task(execute_rewrite_chapter(chapter_request, submit=fake_submit))
        await asyncio.sleep(0.05)
        assert len(started) >= 1
        release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        first_chapter_started = len(started)
        assert first_chapter_started >= 1
        release.set()
        chapter_results = await chapter_task
        assert [item.chapter_index for item in chapter_results] == [1] * first_chapter_started

        started.clear()
        results = await batch_rewrite_chapters([chapter_two_request, chapter_request], submit=fake_submit)
        assert started and started[0][0] == 1
        assert started[-1][0] == 2
        chapter_indexes = [item.chapter_index for item in results]
        assert chapter_indexes == sorted(chapter_indexes)

    asyncio.run(_run())


def test_execute_rewrite_segment_skips_window_guardrail_when_disabled() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"target_chars_min": 200, "target_chars_max": 260})

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="长度较短的改写文本",
                latency_ms=9,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=11, total_tokens=21),
                raw_response={"choices": [{"finish_reason": "length"}], "request_id": "req-guardrail-off"},
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
            window_guardrail_enabled=False,
            window_audit_enabled=True,
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert result.rewritten_text == "长度较短的改写文本"
        assert len(result.window_attempts) == 1
        assert result.window_attempts[0].action == "accepted"
        assert result.window_attempts[0].finish_reason == "length"

    asyncio.run(_run())


def test_execute_rewrite_segment_falls_back_to_legacy_mode_when_window_mode_disabled() -> None:
    async def _run() -> None:
        chapter = _chapter()
        analysis = _analysis()
        segment = _segment(chapter, (1, 2)).model_copy(update={"target_chars_min": 6, "target_chars_max": 120})
        calls = 0

        async def fake_complete(api_key: str, base_url: str, request, *, provider_type=ProviderType.OPENAI_COMPATIBLE, transport=None):
            nonlocal calls
            calls += 1
            return CompletionResponse(
                provider_type=provider_type,
                model_name=request.model_name,
                text="，突然加速，气氛骤紧。",
                latency_ms=8,
                usage=UsageInfo(prompt_tokens=10, completion_tokens=12, total_tokens=22),
                raw_response={"choices": [{"finish_reason": "stop"}], "request_id": f"req-{calls}"},
            )

        request = RewriteSegmentRequest(
            novel_id="novel-1",
            task_id="task-1",
            chapter=chapter,
            analysis=analysis,
            segment=segment,
            rewrite_rules=[_rewrite_rule()],
            global_prompt="请保持文风统一",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            api_key="sk-test",
            base_url="https://example.com/v1",
            model_name="gpt-4o-mini",
            generation={"temperature": 0.2},
            window_mode_enabled=False,
            window_audit_enabled=False,
        )

        result = await execute_rewrite_segment(request, llm_complete=fake_complete)
        assert result.status == RewriteResultStatus.COMPLETED
        assert calls == 1
        assert result.window_attempts == []

    asyncio.run(_run())
