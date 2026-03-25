from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from difflib import SequenceMatcher
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.contracts.api import CreateProviderRequest, ProviderTestConnectionRequest
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.secrets import decrypt_api_key, encrypt_api_key
from backend.app.db import Provider, get_db_session
from backend.app.llm.client import fetch_models as fetch_provider_models
from backend.app.llm.client import test_connection as test_provider_connection
from backend.app.models.core import ProviderType as CoreProviderType

router = APIRouter(prefix="/providers", tags=["providers"])

ALLOWED_PROVIDER_TYPE_VALUES = {"openai", "openai_compatible"}
MODEL_CATALOG: dict[str, list[str]] = {
    "openai": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-4.1",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1-mini",
    ],
    "openai_compatible": [
        "Qwen2.5-72B-Instruct",
        "Qwen2.5-32B-Instruct",
        "DeepSeek-V3",
        "DeepSeek-R1",
        "gpt-4o-mini",
        "gpt-4.1-mini",
    ],
}


class UpdateApiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str


class UpdateProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    provider_type: CoreProviderType | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None


class ProviderModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    provider_type: CoreProviderType | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "ProviderModelsRequest":
        if self.provider_id:
            return self
        if not self.api_key or not self.base_url or self.provider_type is None:
            raise ValueError("Either provider_id or api_key/base_url/provider_type must be provided")
        return self


class ProviderModelsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str]
    fetched_at: datetime
    source: str
    provider_id: str | None = None
    provider_type: str


class ProviderTestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    success: bool
    latency_ms: int | None = None
    error: str | None = None
    provider_id: str | None = None
    provider_type: str | None = None
    model_name: str | None = None


def _fingerprint(api_key: str, base_url: str) -> str:
    payload = f"{api_key.strip()}::{base_url.strip().lower()}"
    return sha256(payload.encode("utf-8")).hexdigest()


def _mask(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


def _decode_stored_api_key(stored_value: str) -> str:
    try:
        return decrypt_api_key(stored_value)
    except AppError:
        return stored_value


def _serialize_provider(row: Provider) -> dict[str, object]:
    api_key = _decode_stored_api_key(row.api_key_encrypted)
    return {
        "id": row.id,
        "name": row.name,
        "provider_type": row.provider_type,
        "api_key_masked": _mask(api_key),
        "base_url": row.base_url,
        "model_name": row.model_name,
        "temperature": row.temperature,
        "max_tokens": row.max_tokens,
        "top_p": row.top_p,
        "presence_penalty": row.presence_penalty,
        "frequency_penalty": row.frequency_penalty,
        "rpm_limit": row.rpm_limit,
        "tpm_limit": row.tpm_limit,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else _now_utc_naive().isoformat(),
    }


def _provider_type_value(provider_type: object) -> str:
    value = getattr(provider_type, "value", provider_type)
    value = str(value)
    if value not in ALLOWED_PROVIDER_TYPE_VALUES:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"Unsupported provider_type `{value}`", status.HTTP_400_BAD_REQUEST)
    return value


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _provider_models(provider_type: str) -> list[str]:
    return MODEL_CATALOG[_provider_type_value(provider_type)].copy()


def _model_score(query: str, candidate: str) -> tuple[int, int, int, str]:
    q = query.strip().lower()
    c = candidate.strip().lower()
    if not q:
        return (0, len(c), 0, c)
    if c == q:
        return (10_000, 0, -len(c), c)
    if c.startswith(q):
        return (9_000, -len(c), 0, c)
    if q in c:
        return (8_000 - c.index(q), -len(c), 0, c)
    matcher = SequenceMatcher(None, q, c)
    ratio = int(matcher.ratio() * 1000)
    if ratio <= 0:
        return (-1, -len(c), 0, c)
    return (ratio, -len(c), 0, c)


def _filter_and_rank_models(models: list[str], query: str | None) -> list[str]:
    if not query:
        return sorted(models)
    scored = [(candidate, _model_score(query, candidate)) for candidate in models]
    filtered = [item for item in scored if item[1][0] > 0]
    filtered.sort(key=lambda item: item[1], reverse=True)
    return [candidate for candidate, _ in filtered]


def _load_cached_models(row: Provider) -> list[str]:
    if not row.model_list_cache_json:
        return [row.model_name]
    try:
        payload = json.loads(row.model_list_cache_json)
    except json.JSONDecodeError:
        return [row.model_name]
    if isinstance(payload, list):
        models = [str(item) for item in payload if str(item).strip()]
        return models or [row.model_name]
    return [row.model_name]


def _write_model_cache(row: Provider, models: list[str]) -> None:
    row.model_list_cache_json = json.dumps(models, ensure_ascii=False)
    row.model_list_fetched_at = _now_utc_naive()


async def _load_provider_or_404(db: AsyncSession, provider_id: str) -> Provider:
    row = await db.get(Provider, provider_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Provider `{provider_id}` not found", status.HTTP_404_NOT_FOUND)
    return row


async def _persist_provider_model_cache(db: AsyncSession, row: Provider, models: list[str]) -> None:
    _write_model_cache(row, models)
    await db.commit()
    await db.refresh(row)


@router.get("")
async def list_providers(db: AsyncSession = Depends(get_db_session)) -> dict[str, object]:
    rows = (await db.execute(select(Provider).order_by(Provider.created_at.desc()))).scalars().all()
    payload = [_serialize_provider(row) for row in rows]
    return {"providers": payload, "total": len(payload)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_provider(
    request: CreateProviderRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    provider_type_value = _provider_type_value(request.provider_type)
    normalized_base = _normalize_base_url(request.base_url)
    fingerprint = _fingerprint(request.api_key, normalized_base)

    existing = (
        await db.execute(
            select(Provider).where(
                Provider.provider_type == provider_type_value,
                Provider.base_url == normalized_base,
                Provider.credential_fingerprint == fingerprint,
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.name = request.name
        existing.model_name = request.model_name
        existing.temperature = request.temperature
        existing.max_tokens = request.max_tokens
        existing.top_p = request.top_p
        existing.presence_penalty = request.presence_penalty
        existing.frequency_penalty = request.frequency_penalty
        existing.rpm_limit = request.rpm_limit
        existing.tpm_limit = request.tpm_limit
        existing.api_key_encrypted = encrypt_api_key(request.api_key)
        await db.commit()
        await db.refresh(existing)
        return _serialize_provider(existing)

    row = Provider(
        id=str(uuid4()),
        name=request.name,
        provider_type=provider_type_value,
        credential_fingerprint=fingerprint,
        api_key_encrypted=request.api_key,
        base_url=normalized_base,
        model_name=request.model_name,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        top_p=request.top_p,
        presence_penalty=request.presence_penalty,
        frequency_penalty=request.frequency_penalty,
        rpm_limit=request.rpm_limit,
        tpm_limit=request.tpm_limit,
        is_active=True,
    )
    row.api_key_encrypted = encrypt_api_key(request.api_key)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _serialize_provider(row)


@router.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    request: UpdateProviderRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    row = await db.get(Provider, provider_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Provider `{provider_id}` not found", status.HTTP_404_NOT_FOUND)

    if request.name is not None:
        row.name = request.name
    if request.provider_type is not None:
        row.provider_type = _provider_type_value(request.provider_type)
    if request.base_url is not None:
        row.base_url = _normalize_base_url(request.base_url)
    if request.model_name is not None:
        row.model_name = request.model_name
    if request.temperature is not None:
        row.temperature = request.temperature
    if request.max_tokens is not None:
        row.max_tokens = request.max_tokens
    if request.top_p is not None:
        row.top_p = request.top_p
    if request.presence_penalty is not None:
        row.presence_penalty = request.presence_penalty
    if request.frequency_penalty is not None:
        row.frequency_penalty = request.frequency_penalty
    if request.rpm_limit is not None:
        row.rpm_limit = request.rpm_limit
    if request.tpm_limit is not None:
        row.tpm_limit = request.tpm_limit
    if request.api_key is not None:
        row.api_key_encrypted = encrypt_api_key(request.api_key)

    row.credential_fingerprint = _fingerprint(_decode_stored_api_key(row.api_key_encrypted), row.base_url)

    await db.commit()
    await db.refresh(row)
    return _serialize_provider(row)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(provider_id: str, db: AsyncSession = Depends(get_db_session)) -> None:
    row = await db.get(Provider, provider_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, f"Provider `{provider_id}` not found", status.HTTP_404_NOT_FOUND)
    await db.delete(row)
    await db.commit()


@router.post("/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(provider_id: str, db: AsyncSession = Depends(get_db_session)) -> ProviderTestResponse:
    row = await _load_provider_or_404(db, provider_id)
    result = await test_provider_connection(
        _decode_stored_api_key(row.api_key_encrypted),
        row.base_url,
        row.model_name,
        provider_type=CoreProviderType(row.provider_type),
    )
    return ProviderTestResponse(
        status="success" if result.success else "failed",
        success=result.success,
        latency_ms=result.latency_ms,
        error=result.error,
        provider_id=provider_id,
        provider_type=row.provider_type,
        model_name=row.model_name,
    )


@router.post("/test-connection", response_model=ProviderTestResponse)
async def test_connection(
    request: ProviderTestConnectionRequest,
    db: AsyncSession = Depends(get_db_session),
) -> ProviderTestResponse:
    if request.provider_id:
        row = await _load_provider_or_404(db, request.provider_id)
        model_name = request.model_name or row.model_name
        result = await test_provider_connection(
            _decode_stored_api_key(row.api_key_encrypted),
            row.base_url,
            model_name,
            provider_type=CoreProviderType(row.provider_type),
        )
        return ProviderTestResponse(
            status="success" if result.success else "failed",
            success=result.success,
            latency_ms=result.latency_ms,
            error=result.error,
            provider_id=row.id,
            provider_type=row.provider_type,
            model_name=model_name,
        )

    result = await test_provider_connection(
        request.api_key or "",
        request.base_url or "",
        request.model_name or "",
        provider_type=request.provider_type or CoreProviderType.OPENAI_COMPATIBLE,
    )
    return ProviderTestResponse(
        status="success" if result.success else "failed",
        success=result.success,
        latency_ms=result.latency_ms,
        error=result.error,
        provider_type=(request.provider_type.value if request.provider_type else None),
        model_name=request.model_name,
    )


@router.post("/fetch-models", response_model=ProviderModelsResponse)
async def fetch_models(request: ProviderModelsRequest, db: AsyncSession = Depends(get_db_session)) -> ProviderModelsResponse:
    if request.provider_id:
        row = await _load_provider_or_404(db, request.provider_id)
        provider_type = row.provider_type
        models = await fetch_provider_models(
            _decode_stored_api_key(row.api_key_encrypted),
            row.base_url,
            provider_type=CoreProviderType(provider_type),
        )
        await _persist_provider_model_cache(db, row, models)
        return ProviderModelsResponse(
            models=models,
            fetched_at=row.model_list_fetched_at or _now_utc_naive(),
            source="saved",
            provider_id=row.id,
            provider_type=provider_type,
        )

    provider_type = _provider_type_value(request.provider_type or "openai_compatible")
    models = await fetch_provider_models(
        request.api_key,
        request.base_url,
        provider_type=CoreProviderType(provider_type),
    )
    return ProviderModelsResponse(
        models=models,
        fetched_at=_now_utc_naive(),
        source="draft",
        provider_type=provider_type,
    )


@router.get("/{provider_id}/models")
async def list_models(
    provider_id: str,
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    row = await _load_provider_or_404(db, provider_id)
    if row.model_list_cache_json:
        models = _load_cached_models(row)
    else:
        models = await fetch_provider_models(
            _decode_stored_api_key(row.api_key_encrypted),
            row.base_url,
            provider_type=CoreProviderType(row.provider_type),
        )
        await _persist_provider_model_cache(db, row, models)
    return {
        "provider_id": provider_id,
        "provider_type": row.provider_type,
        "models": _filter_and_rank_models(models, q),
        "cached": bool(row.model_list_cache_json),
        "fetched_at": row.model_list_fetched_at.isoformat() if row.model_list_fetched_at else None,
    }


@router.put("/{provider_id}/api-key")
async def update_api_key(
    provider_id: str,
    request: UpdateApiKeyRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    row = await _load_provider_or_404(db, provider_id)
    row.api_key_encrypted = encrypt_api_key(request.api_key)
    row.credential_fingerprint = _fingerprint(request.api_key, row.base_url)
    await db.commit()
    return {"status": "updated", "provider_id": provider_id}
