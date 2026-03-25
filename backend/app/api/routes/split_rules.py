from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, status

from backend.app.contracts.api import (
    SplitConfirmRequest,
    SplitRuleCreateRequest,
    SplitRuleUpdateRequest,
    SplitRulesConfigRequest,
    SplitRulesConfigResponse,
    SplitRulesConfirmResponse,
    SplitRulesPreviewRequest,
    SplitRulesPreviewResponse,
)
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.settings import get_settings
from backend.app.services.splitting import (
    build_preview_split_rules_state,
    confirm_split_preview,
    create_custom_rule,
    decode_preview_token,
    delete_custom_rule,
    get_split_rules_snapshot,
    load_split_rules_state,
    make_split_preview,
    replace_split_rules_state,
    update_custom_rule,
)

router = APIRouter(prefix="/split-rules", tags=["split-rules"])


def _artifact_store(request: Request) -> ArtifactStore:
    store = getattr(request.app.state, "artifact_store", None)
    if store is not None:
        return store
    return ArtifactStore(get_settings().data_dir)


def _raw_text_path(request: Request, novel_id: str) -> Path:
    return _artifact_store(request).novel_dir(novel_id) / "raw.txt"


def _load_raw_text(request: Request, novel_id: str) -> str:
    path = _raw_text_path(request, novel_id)
    if not path.exists():
        raise AppError(ErrorCode.NOT_FOUND, f"raw.txt not found for novel `{novel_id}`", status.HTTP_404_NOT_FOUND)
    return path.read_text(encoding="utf-8")


@router.get("", response_model=SplitRulesConfigResponse)
async def get_split_rules() -> SplitRulesConfigResponse:
    return get_split_rules_snapshot()


@router.put("", response_model=SplitRulesConfigResponse)
async def replace_split_rules(payload: SplitRulesConfigRequest) -> SplitRulesConfigResponse:
    return replace_split_rules_state(payload)


@router.post("/custom", response_model=SplitRulesConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_split_rule(payload: SplitRuleCreateRequest) -> SplitRulesConfigResponse:
    return create_custom_rule(payload)


@router.put("/custom/{rule_id}", response_model=SplitRulesConfigResponse)
async def update_split_rule(rule_id: str, payload: SplitRuleUpdateRequest) -> SplitRulesConfigResponse:
    return update_custom_rule(rule_id, payload)


@router.delete("/custom/{rule_id}", response_model=SplitRulesConfigResponse)
async def delete_split_rule(rule_id: str) -> SplitRulesConfigResponse:
    return delete_custom_rule(rule_id)


@router.post("/preview", response_model=SplitRulesPreviewResponse)
async def preview_split_rules(payload: SplitRulesPreviewRequest, request: Request) -> SplitRulesPreviewResponse:
    raw_text = _load_raw_text(request, payload.novel_id)
    persisted_state = load_split_rules_state()
    state = build_preview_split_rules_state(
        builtin_rules=payload.builtin_rules,
        custom_rules=payload.custom_rules,
        fallback_state=persisted_state,
    )
    source_revision = payload.source_revision or None
    rules_version = payload.rules_version or state.rules_version
    return make_split_preview(
        payload.novel_id,
        raw_text,
        source_revision,
        rules_version,
        sample_size=payload.sample_size,
        state=state,
        selected_rule_id=payload.selected_rule_id,
    )


@router.post("/confirm", response_model=SplitRulesConfirmResponse)
async def confirm_split_rules(payload: SplitConfirmRequest, request: Request) -> SplitRulesConfirmResponse:
    token_payload = decode_preview_token(payload.preview_token)
    raw_text = _load_raw_text(request, token_payload.novel_id)
    state = load_split_rules_state()
    return confirm_split_preview(
        token_payload.novel_id,
        payload.preview_token,
        raw_text,
        state=state,
    )
