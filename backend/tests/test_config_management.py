from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.routes.config import (
    GlobalPromptRequest,
    RewriteRuleCreateRequest,
    RewriteRuleDeleteRequest,
    RewriteRuleUpdateRequest,
    SceneRuleCreateRequest,
    SceneRuleDeleteRequest,
    SceneRuleUpdateRequest,
    ai_parse,
    create_rewrite_rules,
    create_scene_rules,
    delete_rewrite_rules,
    delete_scene_rules,
    export_json,
    import_json,
    update_global_prompt,
    update_rewrite_rules,
    update_scene_rules,
)
from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.base import Base
from backend.app.db.models import Config
from backend.app.services.config_store import ConfigParseRequest, load_snapshot


async def _session_factory(db_path: Path) -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_blank_initialization(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                snapshot = await load_snapshot(session)
                assert snapshot.global_prompt == ""
                assert snapshot.scene_rules == []
                assert snapshot.rewrite_rules == []

                rows = (await session.execute(select(Config))).scalars().all()
                assert len(rows) == 1
                assert rows[0].scope == "global"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_scene_and_rewrite_crud(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                scene_created = await create_scene_rules(
                    SceneRuleCreateRequest(scene_type="修炼突破", trigger_conditions=["突破", "丹田"], weight=1.2, enabled=True),
                    db=session,
                )
                assert len(scene_created.scene_rules) == 1
                assert len(scene_created.rewrite_rules) == 1
                assert scene_created.rewrite_rules[0].scene_type == "修炼突破"
                scene_id = scene_created.scene_rules[0].id

                scene_updated = await update_scene_rules(
                    SceneRuleUpdateRequest(
                        id=scene_id,
                        scene_type="修炼突破",
                        trigger_conditions=["突破", "进阶"],
                        weight=1.5,
                        enabled=False,
                    ),
                    db=session,
                )
                assert scene_updated.scene_rules[0].keywords == ["突破", "进阶"]
                assert scene_updated.scene_rules[0].enabled is False

                with pytest.raises(AppError) as delete_rewrite_exc:
                    await delete_rewrite_rules(RewriteRuleDeleteRequest(id=scene_updated.rewrite_rules[0].id), db=session)
                assert delete_rewrite_exc.value.code == ErrorCode.VALIDATION_ERROR

                scene_deleted = await delete_scene_rules(SceneRuleDeleteRequest(id=scene_id), db=session)
                assert scene_deleted.scene_rules == []
                assert scene_deleted.rewrite_rules == []

                # 必须先存在场景规则才能创建改写规则。
                with pytest.raises(AppError) as create_rewrite_exc:
                    await create_rewrite_rules(
                        RewriteRuleCreateRequest(
                            scene_type="修炼突破",
                            strategy="expand",
                            target_ratio=2.2,
                            priority=4,
                            enabled=True,
                        ),
                        db=session,
                    )
                assert create_rewrite_exc.value.code == ErrorCode.VALIDATION_ERROR

                scene_recreated = await create_scene_rules(
                    SceneRuleCreateRequest(scene_type="修炼突破", trigger_conditions=["突破", "丹田"], weight=1.2, enabled=True),
                    db=session,
                )
                rewrite_id = scene_recreated.rewrite_rules[0].id
                rewrite_updated = await update_rewrite_rules(
                    RewriteRuleUpdateRequest(
                        id=rewrite_id,
                        scene_type="修炼突破",
                        strategy="condense",
                        target_ratio=1.2,
                        priority=1,
                        enabled=False,
                    ),
                    db=session,
                )
                assert rewrite_updated.rewrite_rules[0].strategy == "condense"
                assert rewrite_updated.rewrite_rules[0].enabled is False
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_rewrite_rule_strategies_round_trip_and_primary_selection(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                await create_scene_rules(
                    SceneRuleCreateRequest(scene_type="修炼突破", trigger_conditions=["突破", "丹田"], weight=1.0, enabled=True),
                    db=session,
                )
                current_snapshot = await load_snapshot(session)
                assert current_snapshot.rewrite_rules

                updated = await update_rewrite_rules(
                    RewriteRuleUpdateRequest(
                        id=current_snapshot.rewrite_rules[0].id,
                        scene_type="修炼突破",
                        strategies=["rewrite", "expand"],
                        rewrite_guidance="战斗场景重点强化临场张力，禁止新增设定。",
                        target_ratio=2.2,
                        priority=4,
                        enabled=True,
                    ),
                    db=session,
                )
                assert len(updated.rewrite_rules) == 1
                rule = updated.rewrite_rules[0]
                assert rule.strategies == ["rewrite", "expand"]
                assert rule.strategy == "expand"
                assert rule.rewrite_guidance == "战斗场景重点强化临场张力，禁止新增设定。"
                dumped = rule.model_dump(mode="json")
                assert dumped["strategies"] == ["rewrite", "expand"]
                assert dumped["strategy"] == "expand"
                exported = await export_json(db=session)
                assert exported["rewrite_rules"][0]["strategies"] == ["rewrite", "expand"]
                assert exported["rewrite_rules"][0]["strategy"] == "expand"
                assert exported["rewrite_rules"][0]["rewrite_guidance"] == "战斗场景重点强化临场张力，禁止新增设定。"
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_trigger_condition_label_parsing_for_create_and_ai_parse(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                created = await create_scene_rules(
                    SceneRuleCreateRequest(
                        scene_type="战斗",
                        trigger_conditions=["识别点：厮杀、交锋。关键词：对砍、刀光。"],
                        weight=1.0,
                        enabled=True,
                    ),
                    db=session,
                )
                assert created.scene_rules[0].trigger_conditions == ["厮杀", "交锋", "对砍", "刀光"]

                parsed = await ai_parse(
                    ConfigParseRequest(instruction="新增场景规则：追逐，识别点：奔跑、追赶。关键词：急促、逃离。"),
                    db=session,
                )
                assert parsed["status"] == "ok"
                assert parsed["patch"]["scene_rules"][-1]["scene_type"] == "追逐"
                assert parsed["patch"]["scene_rules"][-1]["trigger_conditions"] == ["奔跑", "追赶", "急促", "逃离"]
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_export_import_and_invalid_payload(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                await update_global_prompt(GlobalPromptRequest(global_prompt="你是一个专业的网络小说改写助手"), db=session)
                await create_scene_rules(
                    SceneRuleCreateRequest(scene_type="修炼突破", trigger_conditions=["突破", "丹田"], weight=1.0, enabled=True),
                    db=session,
                )
                current_snapshot = await load_snapshot(session)
                assert current_snapshot.rewrite_rules
                await update_rewrite_rules(
                    RewriteRuleUpdateRequest(
                        id=current_snapshot.rewrite_rules[0].id,
                        scene_type="修炼突破",
                        strategy="expand",
                        target_ratio=2.2,
                        priority=4,
                        enabled=True,
                    ),
                    db=session,
                )

                exported = await export_json(db=session)
                assert exported["version"] == "1.0"
                assert exported["global_prompt"] == "你是一个专业的网络小说改写助手"
                assert len(exported["scene_rules"]) == 1
                assert len(exported["rewrite_rules"]) == 1

                incoming_payload = {
                    "version": "1.0",
                    "global_prompt": "导入后的提示词",
                    "rewrite_general_guidance": "保持叙事连贯，不新增设定",
                    "scene_rules": [
                        {
                            "scene_type": "战斗",
                            "trigger_conditions": "识别点：交手、厮杀。关键词：刀光、拳脚。",
                            "weight": 0.8,
                            "enabled": True,
                        }
                    ],
                    "rewrite_rules": [
                        {
                            "scene_type": "战斗",
                            "strategies": ["rewrite", "expand"],
                            "rewrite_guidance": "对战斗段落加强动作连贯性。",
                            "target_ratio": 1.0,
                            "priority": 2,
                            "enabled": True,
                        }
                    ],
                }
                preview = await import_json(incoming_payload, confirm=False, db=session)
                assert preview.status == "preview"
                assert preview.requires_confirmation is True
                assert preview.summary.global_prompt_changed is True
                assert preview.summary.scene_rules_added == 1
                assert preview.summary.rewrite_rules_added == 1

                imported = await import_json(incoming_payload, confirm=True, db=session)
                assert imported.global_prompt == "导入后的提示词"
                assert imported.rewrite_general_guidance == "保持叙事连贯，不新增设定"
                assert len(imported.scene_rules) == 1
                assert len(imported.rewrite_rules) == 1
                assert imported.scene_rules[0].trigger_conditions == ["交手", "厮杀", "刀光", "拳脚"]
                assert imported.rewrite_rules[0].strategies == ["rewrite", "expand"]
                assert imported.rewrite_rules[0].strategy == "expand"
                assert imported.rewrite_rules[0].rewrite_guidance == "对战斗段落加强动作连贯性。"

                current = await load_snapshot(session)
                assert current.global_prompt == "导入后的提示词"
                assert current.scene_rules[0].scene_type == "战斗"

                with pytest.raises(AppError) as exc_info:
                    await import_json(
                        {
                            "version": "1.0",
                            "scene_rules": [],
                            "rewrite_rules": [],
                        },
                        db=session,
                    )
                assert exc_info.value.code == ErrorCode.CONFIG_INVALID
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_ai_parse_rejects_provider_params(tmp_path: Path) -> None:
    async def _run() -> None:
        engine, sessionmaker = await _session_factory(tmp_path / "config.db")
        try:
            async with sessionmaker() as session:
                result = await ai_parse(ConfigParseRequest(instruction="把 temperature 调到 0.8"), db=session)
                assert result["status"] == "clarification_needed"
                assert result["clarification"] == "请到 provider 配置页调整"
                snapshot = await load_snapshot(session)
                assert snapshot.global_prompt == ""
                assert snapshot.scene_rules == []
                assert snapshot.rewrite_rules == []
        finally:
            await engine.dispose()

    asyncio.run(_run())
