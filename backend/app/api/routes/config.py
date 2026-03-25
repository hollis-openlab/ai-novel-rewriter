from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db import get_db_session
from backend.app.services.config_store import (
    ConfigApplyRequest,
    ImportDiffSummary,
    ConfigParseRequest,
    ConfigSnapshot,
    RewriteRule,
    SceneRule,
    apply_patch,
    create_rewrite_rule,
    create_scene_rule,
    delete_rewrite_rule,
    delete_scene_rule,
    export_snapshot,
    import_snapshot,
    load_snapshot,
    parse_instruction,
    preview_import_snapshot,
    set_global_prompt,
    update_rewrite_rule,
    update_scene_rule,
)

router = APIRouter(prefix="/config", tags=["config"])


class GlobalPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_prompt: str = Field(default="")


class SceneRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_type: str
    trigger_conditions: list[str] = Field(default_factory=list)
    weight: float = Field(default=1.0, ge=0)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_keywords(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "trigger_conditions" not in payload and "keywords" in payload:
            payload["trigger_conditions"] = payload.get("keywords")
        return payload


class SceneRuleUpdateRequest(SceneRuleCreateRequest):
    id: str


class SceneRuleDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class RewriteRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_type: str
    strategies: list[str] = Field(default_factory=list)
    strategy: str | None = None
    rewrite_guidance: str = Field(default="")
    target_ratio: float = Field(gt=0)
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
        return payload

    @field_validator("strategies", mode="before")
    @classmethod
    def coerce_strategies(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @model_validator(mode="after")
    def validate_strategies(self) -> "RewriteRuleCreateRequest":
        if not self.strategies:
            raise ValueError("strategies must contain at least one strategy")
        return self


class RewriteGeneralGuidanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewrite_general_guidance: str = Field(default="")


class RewriteRuleUpdateRequest(RewriteRuleCreateRequest):
    id: str


class RewriteRuleDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class ImportJsonPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "preview"
    summary: ImportDiffSummary
    snapshot: ConfigSnapshot
    requires_confirmation: bool = True


def _snapshot_payload(snapshot: ConfigSnapshot) -> dict[str, object]:
    return snapshot.model_dump(mode="json")


def _build_rewrite_rule(payload: RewriteRuleCreateRequest) -> RewriteRule:
    return RewriteRule.model_validate(
        payload.model_dump(mode="json", exclude_none=True),
    )


@router.get("/global-prompt", response_model=ConfigSnapshot)
async def get_global_prompt(db: AsyncSession = Depends(get_db_session)) -> ConfigSnapshot:
    return await load_snapshot(db)


@router.put("/global-prompt", response_model=ConfigSnapshot)
async def update_global_prompt(
    payload: GlobalPromptRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await set_global_prompt(db, payload.global_prompt)


@router.get("/scene-rules", response_model=ConfigSnapshot)
async def get_scene_rules(db: AsyncSession = Depends(get_db_session)) -> ConfigSnapshot:
    return await load_snapshot(db)


@router.post("/scene-rules", response_model=ConfigSnapshot, status_code=status.HTTP_201_CREATED)
async def create_scene_rules(
    payload: SceneRuleCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await create_scene_rule(
        db,
        SceneRule(
            scene_type=payload.scene_type,
            trigger_conditions=payload.trigger_conditions,
            weight=payload.weight,
            enabled=payload.enabled,
        ),
    )


@router.put("/scene-rules", response_model=ConfigSnapshot)
async def update_scene_rules(
    payload: SceneRuleUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await update_scene_rule(
        db,
        payload.id,
        SceneRule(
            id=payload.id,
            scene_type=payload.scene_type,
            trigger_conditions=payload.trigger_conditions,
            weight=payload.weight,
            enabled=payload.enabled,
        ),
    )


@router.delete("/scene-rules", response_model=ConfigSnapshot)
async def delete_scene_rules(
    payload: SceneRuleDeleteRequest = Body(...),
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await delete_scene_rule(db, payload.id)


@router.get("/rewrite-rules", response_model=ConfigSnapshot)
async def get_rewrite_rules(db: AsyncSession = Depends(get_db_session)) -> ConfigSnapshot:
    return await load_snapshot(db)


@router.put("/rewrite-general-guidance", response_model=ConfigSnapshot)
async def update_rewrite_general_guidance(
    payload: RewriteGeneralGuidanceRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await apply_patch(
        db,
        patch=ConfigApplyRequest(
            patch={
                "rewrite_general_guidance": payload.rewrite_general_guidance,
            }
        ).patch,
    )


@router.post("/rewrite-rules", response_model=ConfigSnapshot, status_code=status.HTTP_201_CREATED)
async def create_rewrite_rules(
    payload: RewriteRuleCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await create_rewrite_rule(db, _build_rewrite_rule(payload))


@router.put("/rewrite-rules", response_model=ConfigSnapshot)
async def update_rewrite_rules(
    payload: RewriteRuleUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await update_rewrite_rule(db, payload.id, _build_rewrite_rule(payload))


@router.delete("/rewrite-rules", response_model=ConfigSnapshot)
async def delete_rewrite_rules(
    payload: RewriteRuleDeleteRequest = Body(...),
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot:
    return await delete_rewrite_rule(db, payload.id)


@router.get("/export-json")
async def export_json(db: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    snapshot = await export_snapshot(db)
    return _snapshot_payload(snapshot)


@router.post("/import-json", response_model=ConfigSnapshot | ImportJsonPreviewResponse)
async def import_json(
    payload: dict[str, Any],
    confirm: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
) -> ConfigSnapshot | ImportJsonPreviewResponse:
    if not confirm:
        preview_snapshot, diff = await preview_import_snapshot(db, payload)
        return ImportJsonPreviewResponse(summary=diff, snapshot=preview_snapshot)
    return await import_snapshot(db, payload)


@router.post("/ai-parse")
async def ai_parse(payload: ConfigParseRequest, db: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    result = await parse_instruction(db, payload.instruction)
    return result.model_dump()


@router.post("/ai-apply", response_model=ConfigSnapshot)
async def ai_apply(payload: ConfigApplyRequest, db: AsyncSession = Depends(get_db_session)) -> ConfigSnapshot:
    return await apply_patch(db, payload.patch)
