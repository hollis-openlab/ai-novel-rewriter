from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_serializer, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db import Config, ConfigScope

CONFIG_VERSION = "1.0"
GLOBAL_CONFIG_ROW_ID = "global-config"
FORBIDDEN_PROVIDER_PARAMS = (
    "temperature",
    "top_p",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "rpm_limit",
    "tpm_limit",
)


class RewriteStrategy(StrEnum):
    EXPAND = "expand"
    REWRITE = "rewrite"
    CONDENSE = "condense"
    PRESERVE = "preserve"


class SceneRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    scene_type: str = Field(min_length=1)
    trigger_conditions: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("trigger_conditions", "keywords"),
    )
    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True

    @field_validator("trigger_conditions", mode="before")
    @classmethod
    def coerce_trigger_conditions(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return _normalize_trigger_conditions(value)
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if item is None:
                    continue
                normalized.extend(_normalize_trigger_conditions(str(item)))
            return normalized
        return value

    @field_validator("trigger_conditions")
    @classmethod
    def normalize_trigger_conditions(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            cleaned = item.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @property
    def keywords(self) -> list[str]:
        # Backward-compatible read path for existing call sites/tests.
        return self.trigger_conditions


class RewriteRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    scene_type: str = Field(min_length=1)
    strategies: list[Literal["expand", "rewrite", "condense", "preserve"]] = Field(default_factory=list)
    rewrite_guidance: str = ""
    target_ratio: float = Field(gt=0)
    target_chars: int | None = Field(default=None, ge=1)
    priority: int = Field(default=0, ge=0)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_strategy(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if not payload.get("strategies") and payload.get("strategy"):
            payload["strategies"] = payload.get("strategy")
        payload.pop("strategy", None)
        return payload

    @field_validator("strategies", mode="before")
    @classmethod
    def coerce_strategies(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("strategies")
    @classmethod
    def normalize_strategies(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("strategies must contain at least one strategy")
        return normalized

    @field_validator("rewrite_guidance", mode="before")
    @classmethod
    def normalize_rewrite_guidance(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def primary_strategy(self) -> str:
        if RewriteStrategy.EXPAND.value in self.strategies:
            return RewriteStrategy.EXPAND.value
        return self.strategies[0]

    @property
    def strategy(self) -> str:
        return self.primary_strategy

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        data["strategies"] = list(self.strategies)
        data["strategy"] = self.primary_strategy
        return data


class ConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = CONFIG_VERSION
    global_prompt: str = ""
    rewrite_general_guidance: str = ""
    scene_rules: list[SceneRule] = Field(default_factory=list)
    rewrite_rules: list[RewriteRule] = Field(default_factory=list)
    updated_at: datetime | None = None


class ConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_prompt: str | None = None
    rewrite_general_guidance: str | None = None
    scene_rules: list[SceneRule] | None = None
    rewrite_rules: list[RewriteRule] | None = None


class ConfigImportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = CONFIG_VERSION
    global_prompt: str
    rewrite_general_guidance: str = ""
    scene_rules: list[SceneRule] = Field(default_factory=list)
    rewrite_rules: list[RewriteRule] = Field(default_factory=list)


class ConfigParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)


class ConfigParseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "clarification_needed"] = "ok"
    clarification: str | None = None
    diff_summary: list[str] = Field(default_factory=list)
    patch: ConfigPatch = Field(default_factory=ConfigPatch)
    snapshot: ConfigSnapshot


class ConfigApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: ConfigPatch


class ImportDiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_prompt_changed: bool = False
    scene_rules_added: int = 0
    scene_rules_updated: int = 0
    rewrite_rules_added: int = 0
    rewrite_rules_updated: int = 0
    conflicts: list[str] = Field(default_factory=list)


def _snapshot_payload(snapshot: ConfigSnapshot) -> dict[str, Any]:
    return {
        "version": snapshot.version,
        "global_prompt": snapshot.global_prompt,
        "rewrite_general_guidance": snapshot.rewrite_general_guidance,
        "scene_rules": [
            {
                "id": rule.id,
                "scene_type": rule.scene_type,
                "trigger_conditions": list(rule.trigger_conditions),
                "weight": rule.weight,
                "enabled": rule.enabled,
            }
            for rule in snapshot.scene_rules
        ],
        "rewrite_rules": [rule.model_dump(mode="json") for rule in snapshot.rewrite_rules],
    }


def _snapshot_from_payload(payload: dict[str, Any], *, updated_at: datetime | None = None) -> ConfigSnapshot:
    scene_rules = [SceneRule.model_validate(item) for item in payload.get("scene_rules", [])]
    rewrite_rules = [RewriteRule.model_validate(item) for item in payload.get("rewrite_rules", [])]
    return ConfigSnapshot(
        version=str(payload.get("version") or CONFIG_VERSION),
        global_prompt=str(payload.get("global_prompt") or ""),
        rewrite_general_guidance=str(payload.get("rewrite_general_guidance") or ""),
        scene_rules=scene_rules,
        rewrite_rules=rewrite_rules,
        updated_at=updated_at,
    )


def _normalize_trigger_conditions(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []

    text = re.sub(
        r"(?:识别点(?:是|为)?|触发条件(?:是|为)?|关键词(?:是|为)?|trigger(?:\s*_)?conditions?|keywords?)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    parts = [
        item.strip(" \t\r\n、,，/;；。:：")
        for item in re.split(r"[、,，/;；。\n]+", text)
    ]
    normalized: list[str] = []
    for item in parts:
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _forbidden_parameter_message(instruction: str) -> str | None:
    lowered = instruction.lower()
    if any(term in lowered for term in FORBIDDEN_PROVIDER_PARAMS):
        return "请到 provider 配置页调整"
    if "温度" in instruction or "采样" in instruction:
        return "请到 provider 配置页调整"
    return None


def _extract_global_prompt(instruction: str) -> str | None:
    match = re.search(
        r"(?:全局提示词|global\s*prompt|global_prompt)\s*(?:改成|设置为|修改为|调整为|设为|:|：)\s*(.+)",
        instruction,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip().strip('"\'“”')
    return None


def _extract_rewrite_general_guidance(instruction: str) -> str | None:
    match = re.search(
        r"(?:改写通用指导|rewrite\s*general\s*guidance|rewrite_general_guidance|通用指导)\s*"
        r"(?:改成|设置为|修改为|调整为|设为|:|：)\s*(.+)",
        instruction,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip().strip('"\'“”')
    return None


def _scene_type_key(value: str) -> str:
    return value.strip().lower()


def _build_default_rewrite_rule(scene_type: str) -> RewriteRule:
    return RewriteRule(
        scene_type=scene_type,
        strategies=[RewriteStrategy.REWRITE.value],
        target_ratio=1.0,
        priority=0,
        enabled=True,
    )


def _sync_rewrite_rules_with_scene_rules(
    scene_rules: list[SceneRule],
    rewrite_rules: list[RewriteRule],
) -> list[RewriteRule]:
    scene_by_key: dict[str, SceneRule] = {}
    for rule in scene_rules:
        key = _scene_type_key(rule.scene_type)
        if not key:
            continue
        if key in scene_by_key:
            raise AppError(
                ErrorCode.CONFIG_INVALID,
                f"Duplicate scene_type `{rule.scene_type}` in scene_rules",
                details={"scene_type": rule.scene_type},
            )
        scene_by_key[key] = rule

    rewrite_by_key: dict[str, RewriteRule] = {}
    for rule in sorted(rewrite_rules, key=lambda item: (item.priority, item.scene_type, item.id)):
        key = _scene_type_key(rule.scene_type)
        if key not in scene_by_key:
            continue
        if key in rewrite_by_key:
            raise AppError(
                ErrorCode.CONFIG_INVALID,
                f"Duplicate rewrite rule for scene_type `{rule.scene_type}`",
                details={"scene_type": rule.scene_type},
            )
        # Keep rewrite rule scene_type canonical with the source scene rule.
        rewrite_by_key[key] = rule.model_copy(update={"scene_type": scene_by_key[key].scene_type})

    synced: list[RewriteRule] = []
    for scene_rule in scene_rules:
        key = _scene_type_key(scene_rule.scene_type)
        synced.append(rewrite_by_key.get(key) or _build_default_rewrite_rule(scene_rule.scene_type))
    return synced


def _extract_scene_rule(instruction: str) -> SceneRule | None:
    match = re.search(
        r"(?:新增|添加|创建|设定)(?:一个)?场景规则[:：]?\s*(?P<body>.+)",
        instruction,
    )
    body = match.group("body").strip() if match else None
    if body is None and "场景规则" not in instruction:
        return None

    if body is None:
        body = instruction

    scene_type = body
    trigger_conditions: list[str] = []
    weight = 1.0

    keyword_match = re.search(
        r"(?:识别点(?:是|为)?|触发条件(?:是|为)?|trigger(?:\s*_)?conditions?|关键词(?:是|为)?|keywords?)[:：]?\s*(.+)",
        body,
        re.IGNORECASE,
    )
    if keyword_match:
        trigger_conditions = _normalize_trigger_conditions(keyword_match.group(1))
        scene_type = body[: keyword_match.start()].strip("，,：: ").strip()
    else:
        parts = re.split(r"[，,]", body, maxsplit=1)
        if len(parts) == 2 and (
            "识别点" in parts[1]
            or "触发条件" in parts[1]
            or "关键词" in parts[1]
            or "trigger condition" in parts[1].lower()
            or "keyword" in parts[1].lower()
        ):
            scene_type = parts[0].strip()

    weight_match = re.search(r"(?:权重|weight)[:：]?\s*([0-9]+(?:\.[0-9]+)?)", body, re.IGNORECASE)
    if weight_match:
        weight = float(weight_match.group(1))

    scene_type = scene_type.strip("：:，, ").strip()
    if not scene_type:
        return None
    if not trigger_conditions:
        trigger_conditions = [scene_type]
    return SceneRule(scene_type=scene_type, trigger_conditions=trigger_conditions, weight=weight, enabled=True)


def _extract_rewrite_rule(instruction: str, snapshot: ConfigSnapshot) -> RewriteRule | None:
    lowered = instruction.lower()
    if "改写策略" not in instruction and "改写规则" not in instruction and "rewrite" not in lowered:
        return None

    scene_type = ""
    scene_match = re.search(r"(.+?)(?:场景)?改写(?:策略|规则)", instruction)
    if scene_match:
        scene_type = scene_match.group(1).strip("：:，, ")

    strategy_map = {
        "expand": RewriteStrategy.EXPAND.value,
        "rewrite": RewriteStrategy.REWRITE.value,
        "condense": RewriteStrategy.CONDENSE.value,
        "preserve": RewriteStrategy.PRESERVE.value,
        "扩写": RewriteStrategy.EXPAND.value,
        "改写": RewriteStrategy.REWRITE.value,
        "精简": RewriteStrategy.CONDENSE.value,
        "保留": RewriteStrategy.PRESERVE.value,
    }
    strategies: list[str] = []
    strategy_match = re.search(
        r"(?:strategies|strategy|策略)\s*[:=：]\s*(.+)",
        instruction,
        re.IGNORECASE,
    )
    if strategy_match:
        raw_items = [item.strip() for item in re.split(r"[、,，/;；\s]+", strategy_match.group(1)) if item.strip()]
        for raw_item in raw_items:
            normalized = strategy_map.get(raw_item.lower(), strategy_map.get(raw_item))
            if normalized and normalized not in strategies:
                strategies.append(normalized)
    if not strategies:
        for key, value in strategy_map.items():
            haystack = lowered if key.isascii() else instruction
            if key in haystack:
                strategies.append(value)
                break
    if not strategies:
        strategies = [RewriteStrategy.REWRITE.value]

    ratio = 1.0
    ratio_match = re.search(r"(?:倍率|比例|target_ratio)[:：]?\s*([0-9]+(?:\.[0-9]+)?)", instruction, re.IGNORECASE)
    if ratio_match:
        ratio = float(ratio_match.group(1))

    priority = 0
    priority_match = re.search(r"(?:优先级|priority)[:：]?\s*(\d+)", instruction, re.IGNORECASE)
    if priority_match:
        priority = int(priority_match.group(1))

    scene_lookup = {_scene_type_key(item.scene_type): item.scene_type for item in snapshot.scene_rules}
    if scene_lookup and scene_type:
        scene_type = scene_lookup.get(_scene_type_key(scene_type), scene_type)
    if scene_lookup and _scene_type_key(scene_type) not in scene_lookup:
        # AI 指令必须绑定到现有场景规则，保持一一对应。
        return None
    if not scene_type:
        if len(snapshot.scene_rules) == 1:
            scene_type = snapshot.scene_rules[0].scene_type
        else:
            return None

    return RewriteRule(
        scene_type=scene_type,
        strategies=strategies,
        target_ratio=ratio,
        priority=priority,
        enabled=True,
    )


def _merge_snapshot(current: ConfigSnapshot, patch: ConfigPatch) -> ConfigSnapshot:
    scene_rules = patch.scene_rules if patch.scene_rules is not None else current.scene_rules
    rewrite_rules = patch.rewrite_rules if patch.rewrite_rules is not None else current.rewrite_rules
    synced_rewrite_rules = _sync_rewrite_rules_with_scene_rules(
        list(scene_rules),
        list(rewrite_rules),
    )
    return ConfigSnapshot(
        version=current.version,
        global_prompt=patch.global_prompt if patch.global_prompt is not None else current.global_prompt,
        rewrite_general_guidance=(
            patch.rewrite_general_guidance
            if patch.rewrite_general_guidance is not None
            else current.rewrite_general_guidance
        ),
        scene_rules=scene_rules,
        rewrite_rules=synced_rewrite_rules,
        updated_at=datetime.utcnow(),
    )


def _normalize_snapshot(snapshot: ConfigSnapshot) -> ConfigSnapshot:
    synced_rewrite_rules = _sync_rewrite_rules_with_scene_rules(
        list(snapshot.scene_rules),
        list(snapshot.rewrite_rules),
    )
    return snapshot.model_copy(update={"rewrite_rules": synced_rewrite_rules, "updated_at": datetime.utcnow()})


async def _get_or_create_row(db: AsyncSession) -> Config:
    row = (
        await db.execute(
            select(Config).where(Config.id == GLOBAL_CONFIG_ROW_ID, Config.scope == ConfigScope.GLOBAL.value)
        )
    ).scalars().first()
    if row is not None:
        return row

    fallback = (
        await db.execute(
            select(Config).where(Config.scope == ConfigScope.GLOBAL.value, Config.novel_id.is_(None))
        )
    ).scalars().first()
    if fallback is not None:
        fallback.id = GLOBAL_CONFIG_ROW_ID
        await db.commit()
        await db.refresh(fallback)
        return fallback

    snapshot = ConfigSnapshot(updated_at=datetime.utcnow())
    row = Config(
        id=GLOBAL_CONFIG_ROW_ID,
        scope=ConfigScope.GLOBAL.value,
        novel_id=None,
        config_json=json.dumps(_snapshot_payload(snapshot), ensure_ascii=False),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def load_snapshot(db: AsyncSession) -> ConfigSnapshot:
    row = await _get_or_create_row(db)
    payload = json.loads(row.config_json) if row.config_json else {}
    snapshot = _snapshot_from_payload(payload, updated_at=row.updated_at)
    # Read path should also keep data consistent for legacy rows.
    return _normalize_snapshot(snapshot).model_copy(update={"updated_at": row.updated_at})


async def save_snapshot(db: AsyncSession, snapshot: ConfigSnapshot) -> ConfigSnapshot:
    row = await _get_or_create_row(db)
    snapshot = _normalize_snapshot(snapshot)
    row.config_json = json.dumps(_snapshot_payload(snapshot), ensure_ascii=False)
    row.scope = ConfigScope.GLOBAL.value
    row.novel_id = None
    await db.commit()
    await db.refresh(row)
    return snapshot.model_copy(update={"updated_at": row.updated_at})


async def get_global_prompt(db: AsyncSession) -> dict[str, Any]:
    snapshot = await load_snapshot(db)
    return {
        "version": snapshot.version,
        "global_prompt": snapshot.global_prompt,
        "rewrite_general_guidance": snapshot.rewrite_general_guidance,
        "updated_at": snapshot.updated_at,
    }


async def set_global_prompt(db: AsyncSession, global_prompt: str) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    return await save_snapshot(db, snapshot.model_copy(update={"global_prompt": global_prompt}))


async def list_scene_rules(db: AsyncSession) -> list[SceneRule]:
    return (await load_snapshot(db)).scene_rules


async def create_scene_rule(db: AsyncSession, payload: SceneRule) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    return await save_snapshot(db, snapshot.model_copy(update={"scene_rules": [*snapshot.scene_rules, payload]}))


async def update_scene_rule(db: AsyncSession, rule_id: str, payload: SceneRule) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    updated = False
    previous_scene_type: str | None = None
    next_rules: list[SceneRule] = []
    for rule in snapshot.scene_rules:
        if rule.id == rule_id:
            previous_scene_type = rule.scene_type
            next_rules.append(payload.model_copy(update={"id": rule_id}))
            updated = True
        else:
            next_rules.append(rule)
    if not updated:
        raise AppError(ErrorCode.NOT_FOUND, f"Scene rule `{rule_id}` not found", 404)

    next_rewrite_rules = list(snapshot.rewrite_rules)
    if previous_scene_type is not None and _scene_type_key(previous_scene_type) != _scene_type_key(payload.scene_type):
        normalized_previous = _scene_type_key(previous_scene_type)
        next_rewrite_rules = [
            rule.model_copy(update={"scene_type": payload.scene_type})
            if _scene_type_key(rule.scene_type) == normalized_previous
            else rule
            for rule in next_rewrite_rules
        ]

    return await save_snapshot(
        db,
        snapshot.model_copy(
            update={
                "scene_rules": next_rules,
                "rewrite_rules": next_rewrite_rules,
            }
        ),
    )


async def delete_scene_rule(db: AsyncSession, rule_id: str) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    next_rules = [rule for rule in snapshot.scene_rules if rule.id != rule_id]
    if len(next_rules) == len(snapshot.scene_rules):
        raise AppError(ErrorCode.NOT_FOUND, f"Scene rule `{rule_id}` not found", 404)
    return await save_snapshot(db, snapshot.model_copy(update={"scene_rules": next_rules}))


async def list_rewrite_rules(db: AsyncSession) -> list[RewriteRule]:
    return (await load_snapshot(db)).rewrite_rules


async def create_rewrite_rule(db: AsyncSession, payload: RewriteRule) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    scene_lookup = {_scene_type_key(item.scene_type): item.scene_type for item in snapshot.scene_rules}
    canonical_scene_type = scene_lookup.get(_scene_type_key(payload.scene_type))
    if canonical_scene_type is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Scene rule `{payload.scene_type}` not found",
            details={"scene_type": payload.scene_type},
        )
    if any(_scene_type_key(rule.scene_type) == _scene_type_key(canonical_scene_type) for rule in snapshot.rewrite_rules):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Rewrite rule for scene `{canonical_scene_type}` already exists",
            details={"scene_type": canonical_scene_type},
        )
    payload = payload.model_copy(update={"scene_type": canonical_scene_type})
    return await save_snapshot(db, snapshot.model_copy(update={"rewrite_rules": [*snapshot.rewrite_rules, payload]}))


async def update_rewrite_rule(db: AsyncSession, rule_id: str, payload: RewriteRule) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    scene_lookup = {_scene_type_key(item.scene_type): item.scene_type for item in snapshot.scene_rules}
    canonical_scene_type = scene_lookup.get(_scene_type_key(payload.scene_type))
    if canonical_scene_type is None:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            f"Scene rule `{payload.scene_type}` not found",
            details={"scene_type": payload.scene_type},
        )

    for rule in snapshot.rewrite_rules:
        if rule.id == rule_id:
            continue
        if _scene_type_key(rule.scene_type) == _scene_type_key(canonical_scene_type):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"Rewrite rule for scene `{canonical_scene_type}` already exists",
                details={"scene_type": canonical_scene_type},
            )

    updated = False
    next_rules: list[RewriteRule] = []
    for rule in snapshot.rewrite_rules:
        if rule.id == rule_id:
            next_rules.append(payload.model_copy(update={"id": rule_id, "scene_type": canonical_scene_type}))
            updated = True
        else:
            next_rules.append(rule)
    if not updated:
        raise AppError(ErrorCode.NOT_FOUND, f"Rewrite rule `{rule_id}` not found", 404)
    return await save_snapshot(db, snapshot.model_copy(update={"rewrite_rules": next_rules}))


async def delete_rewrite_rule(db: AsyncSession, rule_id: str) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    target = next((rule for rule in snapshot.rewrite_rules if rule.id == rule_id), None)
    if target is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Rewrite rule `{rule_id}` not found", 404)

    if any(_scene_type_key(item.scene_type) == _scene_type_key(target.scene_type) for item in snapshot.scene_rules):
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Rewrite rule is paired with an existing scene rule; remove the scene rule or disable this rewrite rule instead",
            details={"scene_type": target.scene_type, "rule_id": rule_id},
        )

    next_rules = [rule for rule in snapshot.rewrite_rules if rule.id != rule_id]
    return await save_snapshot(db, snapshot.model_copy(update={"rewrite_rules": next_rules}))


async def export_snapshot(db: AsyncSession) -> ConfigSnapshot:
    return await load_snapshot(db)


def validate_import_payload(payload: dict[str, Any]) -> ConfigSnapshot:
    try:
        imported = ConfigImportPayload.model_validate(payload)
    except ValidationError as exc:
        raise AppError(ErrorCode.CONFIG_INVALID, "Invalid config JSON", 400, details={"errors": exc.errors()}) from exc
    snapshot = ConfigSnapshot(
        version=imported.version,
        global_prompt=imported.global_prompt,
        rewrite_general_guidance=imported.rewrite_general_guidance,
        scene_rules=imported.scene_rules,
        rewrite_rules=imported.rewrite_rules,
        updated_at=datetime.utcnow(),
    )
    return _normalize_snapshot(snapshot)


def _build_import_diff(current: ConfigSnapshot, incoming: ConfigSnapshot) -> ImportDiffSummary:
    current_scene_by_type = {rule.scene_type: rule for rule in current.scene_rules}
    current_rewrite_by_type = {rule.scene_type: rule for rule in current.rewrite_rules}

    scene_rules_added = 0
    scene_rules_updated = 0
    rewrite_rules_added = 0
    rewrite_rules_updated = 0
    conflicts: list[str] = []

    for rule in incoming.scene_rules:
        existing = current_scene_by_type.get(rule.scene_type)
        if existing is None:
            scene_rules_added += 1
            continue
        if existing.model_dump(exclude={"id"}) != rule.model_dump(exclude={"id"}):
            scene_rules_updated += 1
            conflicts.append(f"scene_rule:{rule.scene_type}")

    for rule in incoming.rewrite_rules:
        existing = current_rewrite_by_type.get(rule.scene_type)
        if existing is None:
            rewrite_rules_added += 1
            continue
        if existing.model_dump(exclude={"id"}) != rule.model_dump(exclude={"id"}):
            rewrite_rules_updated += 1
            conflicts.append(f"rewrite_rule:{rule.scene_type}")

    return ImportDiffSummary(
        global_prompt_changed=(current.global_prompt != incoming.global_prompt),
        scene_rules_added=scene_rules_added,
        scene_rules_updated=scene_rules_updated,
        rewrite_rules_added=rewrite_rules_added,
        rewrite_rules_updated=rewrite_rules_updated,
        conflicts=conflicts,
    )


async def preview_import_snapshot(db: AsyncSession, payload: dict[str, Any]) -> tuple[ConfigSnapshot, ImportDiffSummary]:
    incoming = validate_import_payload(payload)
    current = await load_snapshot(db)
    diff = _build_import_diff(current, incoming)
    return incoming, diff


async def import_snapshot(db: AsyncSession, payload: dict[str, Any]) -> ConfigSnapshot:
    snapshot = validate_import_payload(payload)
    return await save_snapshot(db, snapshot)


async def parse_instruction(db: AsyncSession, instruction: str) -> ConfigParseResponse:
    snapshot = await load_snapshot(db)
    clarification = _forbidden_parameter_message(instruction)
    if clarification:
        return ConfigParseResponse(
            status="clarification_needed",
            clarification=clarification,
            diff_summary=[],
            patch=ConfigPatch(),
            snapshot=snapshot,
        )

    patch = ConfigPatch()
    diff_summary: list[str] = []

    global_prompt = _extract_global_prompt(instruction)
    if global_prompt is not None:
        patch.global_prompt = global_prompt
        diff_summary.append("更新全局提示词")

    rewrite_general_guidance = _extract_rewrite_general_guidance(instruction)
    if rewrite_general_guidance is not None:
        patch.rewrite_general_guidance = rewrite_general_guidance
        diff_summary.append("更新改写通用指导")

    scene_rule = _extract_scene_rule(instruction)
    if scene_rule is not None:
        patch.scene_rules = [*snapshot.scene_rules, scene_rule]
        diff_summary.append(f"新增场景规则：{scene_rule.scene_type}")

    snapshot_for_rewrite = snapshot.model_copy(
        update={"scene_rules": patch.scene_rules if patch.scene_rules is not None else snapshot.scene_rules}
    )
    rewrite_rule = _extract_rewrite_rule(instruction, snapshot_for_rewrite)
    if rewrite_rule is not None:
        next_rules = [rule for rule in snapshot.rewrite_rules if rule.scene_type != rewrite_rule.scene_type]
        next_rules.append(rewrite_rule)
        patch.rewrite_rules = sorted(next_rules, key=lambda item: (item.priority, item.scene_type, item.id))
        diff_summary.append(f"更新改写规则：{rewrite_rule.scene_type}")

    preview_snapshot = _merge_snapshot(snapshot, patch) if patch.model_dump(exclude_none=True) else snapshot
    return ConfigParseResponse(
        status="ok",
        clarification=None,
        diff_summary=diff_summary,
        patch=patch,
        snapshot=preview_snapshot,
    )


async def apply_patch(db: AsyncSession, patch: ConfigPatch) -> ConfigSnapshot:
    snapshot = await load_snapshot(db)
    merged = _merge_snapshot(snapshot, patch)
    return await save_snapshot(db, merged)
