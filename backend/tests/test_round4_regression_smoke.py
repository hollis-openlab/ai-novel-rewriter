from __future__ import annotations

import asyncio
from pathlib import Path

from backend.app.core.artifact_store import ArtifactStore
from backend.app.models.core import Chapter, ChapterAnalysis, Paragraph, RewritePotential, SceneSegment
from backend.app.services.analyze_pipeline import build_analyze_prompt_bundle
from backend.app.services.marking import build_rewrite_plan, write_mark_artifacts
from backend.app.services.config_store import RewriteRule
from backend.app.services.worker_pool import WorkerPool


def _chapter() -> Chapter:
    content = "\n\n".join(["第一段战斗动作很快。", "第二段对话推进情节。"])
    return Chapter(
        id="chapter-smoke",
        index=1,
        title="第一章",
        content=content,
        char_count=len(content),
        paragraph_count=2,
        start_offset=0,
        end_offset=len(content),
        paragraphs=[
            Paragraph(index=1, start_offset=0, end_offset=10, char_count=10),
            Paragraph(index=2, start_offset=12, end_offset=22, char_count=10),
        ],
    )


def _analysis() -> ChapterAnalysis:
    return ChapterAnalysis(
        summary="主角在战斗中推进剧情。",
        characters=[],
        key_events=[],
        scenes=[
            SceneSegment(
                scene_type="战斗",
                paragraph_range=(1, 1),
                rewrite_potential=RewritePotential(
                    expandable=True,
                    rewritable=True,
                    suggestion="补充动作细节",
                    priority=5,
                ),
            )
        ],
        location="城门",
        tone="紧张",
    )


def _rewrite_rules() -> list[RewriteRule]:
    return [RewriteRule(scene_type="战斗", strategy="expand", target_ratio=1.5, priority=1, enabled=True)]


def test_round4_worker_pool_analyze_mark_smoke(tmp_path: Path) -> None:
    async def _run() -> tuple[str, int, bool, int]:
        pool = WorkerPool(initial_workers=1)
        await pool.start()
        chapter = _chapter()
        analysis = _analysis()
        rules = _rewrite_rules()

        async def job() -> tuple[str, int, bool]:
            bundle = build_analyze_prompt_bundle(chapter.content, global_prompt="全局提示词")
            plan = build_rewrite_plan("novel-smoke", [chapter], {1: analysis}, rules)
            paths = write_mark_artifacts(ArtifactStore(tmp_path), "novel-smoke", "task-smoke", plan)
            return bundle.stage, plan.total_marked, Path(paths.mark_plan_path).exists()

        stage, total_marked, mark_plan_exists = await pool.submit(job, priority=1)
        status = pool.status()
        await pool.close()
        return stage, total_marked, mark_plan_exists, status.completed_total

    stage, total_marked, mark_plan_exists, completed_total = asyncio.run(_run())

    assert stage == "analyze"
    assert total_marked == 1
    assert mark_plan_exists is True
    assert completed_total == 1
