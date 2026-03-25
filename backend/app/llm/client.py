from __future__ import annotations

import httpx

from backend.app.core.settings import get_settings
from backend.app.core.errors import AppError, ErrorCode
from backend.app.llm.interface import CompletionRequest, CompletionResponse, ConnectionTestResult, LlmProvider
from backend.app.llm.openai_provider import OpenAIProvider
from backend.app.models.core import ProviderType


def _coerce_provider_type(provider_type: ProviderType | str) -> ProviderType:
    if isinstance(provider_type, ProviderType):
        return provider_type
    try:
        return ProviderType(provider_type)
    except ValueError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Unsupported provider type",
            details={"provider_type": provider_type},
        ) from exc


def _resolve_timeout(timeout: float | None) -> float:
    if timeout is not None:
        resolved = float(timeout)
        if resolved <= 0:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "timeout must be greater than 0",
                details={"timeout": timeout},
            )
        return resolved
    return float(get_settings().llm_timeout_seconds)


def build_provider(
    provider_type: ProviderType | str,
    api_key: str,
    base_url: str,
    *,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LlmProvider:
    resolved_type = _coerce_provider_type(provider_type)
    if resolved_type not in {ProviderType.OPENAI, ProviderType.OPENAI_COMPATIBLE}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Only OpenAI and OpenAI-compatible providers are supported",
            details={"provider_type": resolved_type.value},
        )
    return OpenAIProvider(
        api_key=api_key,
        base_url=base_url,
        provider_type=resolved_type,
        timeout=_resolve_timeout(timeout),
        transport=transport,
    )


async def fetch_models(
    api_key: str,
    base_url: str,
    *,
    provider_type: ProviderType | str = ProviderType.OPENAI_COMPATIBLE,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[str]:
    provider = build_provider(provider_type, api_key, base_url, timeout=timeout, transport=transport)
    return await provider.fetch_models()


async def test_connection(
    api_key: str,
    base_url: str,
    model_name: str,
    *,
    provider_type: ProviderType | str = ProviderType.OPENAI_COMPATIBLE,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectionTestResult:
    provider = build_provider(provider_type, api_key, base_url, timeout=timeout, transport=transport)
    return await provider.test_connection(model_name)


async def complete(
    api_key: str,
    base_url: str,
    request: CompletionRequest,
    *,
    provider_type: ProviderType | str = ProviderType.OPENAI_COMPATIBLE,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CompletionResponse:
    provider = build_provider(provider_type, api_key, base_url, timeout=timeout, transport=transport)
    return await provider.complete(request)
