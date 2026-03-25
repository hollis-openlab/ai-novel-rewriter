from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from backend.app.core.errors import AppError, ErrorCode
from backend.app.llm.interface import (
    CompletionRequest,
    CompletionResponse,
    ConnectionTestResult,
    GenerationParams,
    LlmProvider,
    UsageInfo,
)
from backend.app.models.core import ProviderType


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise AppError(ErrorCode.VALIDATION_ERROR, "base_url is required")

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "base_url must be a valid http or https URL",
            details={"base_url": base_url},
        )
    return normalized


def _join_url(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def _validate_required(api_key: str, base_url: str, model_name: str | None = None) -> None:
    if not api_key.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "api_key is required")
    _normalize_base_url(base_url)
    if model_name is not None and not model_name.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "model_name is required")


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        error_obj = payload.get("error")
        if isinstance(error_obj, dict):
            for key in ("message", "detail", "type"):
                value = error_obj.get(key)
                if value:
                    return str(value)
        for key in ("message", "detail", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return response.text.strip() or f"HTTP {response.status_code}"


def _extract_response_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    return {"payload_type": type(payload).__name__}


def _format_httpx_error(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _parse_models(payload: dict[str, Any]) -> list[str]:
    raw_models = payload.get("data", [])
    if not isinstance(raw_models, list):
        raise AppError(
            ErrorCode.PROVIDER_ERROR,
            "Provider returned an invalid models payload",
            details={"payload_type": type(raw_models).__name__},
        )

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_models:
        model_name: str | None = None
        if isinstance(item, str):
            model_name = item.strip() or None
        elif isinstance(item, dict):
            candidate = item.get("id") or item.get("name") or item.get("model")
            if candidate is not None:
                model_name = str(candidate).strip() or None
        if model_name and model_name not in seen:
            models.append(model_name)
            seen.add(model_name)
    return models


def _build_completion_payload(request: CompletionRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": request.model_name,
        "messages": [message.model_dump(exclude_none=True, mode="json") for message in request.messages],
        "stream": request.stream,
    }

    params: GenerationParams = request.generation
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.max_tokens is not None:
        payload["max_tokens"] = params.max_tokens
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    if params.presence_penalty is not None:
        payload["presence_penalty"] = params.presence_penalty
    if params.frequency_penalty is not None:
        payload["frequency_penalty"] = params.frequency_penalty
    if params.stop is not None:
        payload["stop"] = params.stop
    if params.seed is not None:
        payload["seed"] = params.seed
    if params.response_format is not None:
        payload["response_format"] = params.response_format
    return payload


def _parse_usage_info(usage_payload: dict[str, Any]) -> UsageInfo | None:
    allowed_keys = {"prompt_tokens", "completion_tokens", "total_tokens"}
    normalized = {key: usage_payload[key] for key in allowed_keys if key in usage_payload}

    prompt_tokens = normalized.get("prompt_tokens")
    completion_tokens = normalized.get("completion_tokens")
    if normalized.get("total_tokens") is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        normalized["total_tokens"] = prompt_tokens + completion_tokens

    if not normalized:
        return None
    try:
        return UsageInfo.model_validate(normalized)
    except ValidationError:
        return None


def _extract_completion_text(payload: dict[str, Any]) -> tuple[str, str | None, UsageInfo | None]:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise AppError(
            ErrorCode.PROVIDER_ERROR,
            "Provider returned an invalid completion payload",
            details={"missing": "choices"},
        )

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise AppError(
            ErrorCode.PROVIDER_ERROR,
            "Provider returned an invalid completion choice",
            details={"choice_type": type(first_choice).__name__},
        )

    message = first_choice.get("message") or {}
    content = ""
    if isinstance(message, dict):
        content = str(message.get("content") or "")
    elif first_choice.get("text") is not None:
        content = str(first_choice.get("text") or "")

    usage_payload = payload.get("usage")
    usage: UsageInfo | None = None
    if isinstance(usage_payload, dict):
        usage = _parse_usage_info(usage_payload)

    return content, first_choice.get("finish_reason"), usage


class OpenAIProvider(LlmProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        provider_type: ProviderType,
        timeout: float = 600.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _validate_required(api_key, base_url)
        self.api_key = api_key.strip()
        self.base_url = _normalize_base_url(base_url)
        self.provider_type = provider_type
        self.timeout = timeout
        self._transport = transport

    async def _request_json(self, method: str, suffix: str, *, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
        url = _join_url(self.base_url, suffix)
        started_at = time.perf_counter()
        fallback_ipv4_used = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport, follow_redirects=True) as client:
                response = await client.request(method, url, headers=_auth_headers(self.api_key), json=payload)
        except httpx.TimeoutException as exc:
            if self._transport is None:
                try:
                    async with httpx.AsyncClient(
                        timeout=self.timeout,
                        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
                        follow_redirects=True,
                    ) as client:
                        response = await client.request(method, url, headers=_auth_headers(self.api_key), json=payload)
                    fallback_ipv4_used = True
                except httpx.TimeoutException as fallback_exc:
                    raise AppError(
                        ErrorCode.PROVIDER_ERROR,
                        "Provider request timed out; please check network/DNS reachability and retry",
                        details={
                            "method": method,
                            "url": url,
                            "timeout_seconds": self.timeout,
                            "error": _format_httpx_error(fallback_exc),
                            "ipv4_fallback_attempted": True,
                        },
                    ) from fallback_exc
                except httpx.RequestError as fallback_exc:
                    raise AppError(
                        ErrorCode.PROVIDER_ERROR,
                        "Provider request failed",
                        details={
                            "method": method,
                            "url": url,
                            "error": _format_httpx_error(fallback_exc),
                            "ipv4_fallback_attempted": True,
                        },
                    ) from fallback_exc
            else:
                raise AppError(
                    ErrorCode.PROVIDER_ERROR,
                    "Provider request timed out; please check network/DNS reachability and retry",
                    details={
                        "method": method,
                        "url": url,
                        "timeout_seconds": self.timeout,
                        "error": _format_httpx_error(exc),
                    },
                ) from exc
        except httpx.RequestError as exc:
            raise AppError(
                ErrorCode.PROVIDER_ERROR,
                "Provider request failed",
                details={"method": method, "url": url, "error": _format_httpx_error(exc)},
            ) from exc

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        if response.status_code >= 400:
            response_body = _extract_response_body(response)
            raise AppError(
                ErrorCode.PROVIDER_ERROR,
                _extract_error_message(response),
                details={
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "ipv4_fallback_used": fallback_ipv4_used,
                    "response_body": response_body,
                    "response_text": response.text.strip() if response.text.strip() else None,
                },
            )

        try:
            payload_data = response.json()
        except ValueError as exc:
            raise AppError(
                ErrorCode.PROVIDER_ERROR,
                "Provider returned a non-JSON response",
                details={
                    "method": method,
                    "url": url,
                    "status_code": response.status_code,
                    "response_text": response.text.strip() if response.text.strip() else None,
                },
            ) from exc

        if not isinstance(payload_data, dict):
            raise AppError(
                ErrorCode.PROVIDER_ERROR,
                "Provider returned an invalid JSON structure",
                details={
                    "method": method,
                    "url": url,
                    "payload_type": type(payload_data).__name__,
                    "response_body": _extract_response_body(response),
                },
            )

        return payload_data, latency_ms

    async def fetch_models(self) -> list[str]:
        payload, _ = await self._request_json("GET", "/models")
        models = _parse_models(payload)
        if not models:
            raise AppError(
                ErrorCode.PROVIDER_ERROR,
                "Provider returned an empty model list",
                details={"provider_type": self.provider_type.value, "base_url": self.base_url},
            )
        return models

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        if request.model_name.strip() == "":
            raise AppError(ErrorCode.VALIDATION_ERROR, "model_name is required")
        if not request.messages:
            raise AppError(ErrorCode.VALIDATION_ERROR, "messages are required")

        payload = _build_completion_payload(request)
        response_payload, latency_ms = await self._request_json("POST", "/chat/completions", payload=payload)
        text, finish_reason, usage = _extract_completion_text(response_payload)
        return CompletionResponse(
            provider_type=self.provider_type,
            model_name=request.model_name,
            text=text,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            usage=usage,
            raw_response=response_payload,
        )

    async def test_connection(self, model_name: str) -> ConnectionTestResult:
        _validate_required(self.api_key, self.base_url, model_name)
        request = CompletionRequest(
            model_name=model_name,
            messages=[{"role": "user", "content": "ping"}],
            generation=GenerationParams(max_tokens=1, temperature=0),
            metadata={"purpose": "connection_test"},
        )
        try:
            completion = await self.complete(request)
        except AppError as exc:
            return ConnectionTestResult(
                provider_type=self.provider_type,
                model_name=model_name,
                success=False,
                error=exc.message,
                raw_response=exc.details,
            )
        return ConnectionTestResult(
            provider_type=self.provider_type,
            model_name=model_name,
            success=True,
            latency_ms=completion.latency_ms,
            raw_response={
                "finish_reason": completion.finish_reason,
                "usage": completion.usage.model_dump(mode="json") if completion.usage else None,
            },
        )
