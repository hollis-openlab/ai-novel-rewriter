from __future__ import annotations

import asyncio
import json

import httpx

from backend.app.llm.client import complete, fetch_models, test_connection as provider_test_connection
from backend.app.llm.interface import ChatMessage, CompletionRequest, GenerationParams
from backend.app.models.core import ProviderType


def _mock_transport() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-5.4"},
                        {"id": "gpt-5.4-mini"},
                        {"id": "gpt-4.1"},
                    ]
                },
            )

        if request.url.path.endswith("/chat/completions"):
            payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": f"ok:{payload.get('model')}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )

        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(_handler)


def _mock_transport_with_extra_usage_fields() -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "completion_tokens_details": {"reasoning_tokens": 0},
                        "prompt_tokens_details": {"cached_tokens": 0},
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 10,
                    },
                },
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    return httpx.MockTransport(_handler)


def test_fetch_models_openai_compatible() -> None:
    async def _run() -> None:
        models = await fetch_models(
            "sk-test",
            "https://example.com/v1",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            transport=_mock_transport(),
        )
        assert models[:2] == ["gpt-5.4", "gpt-5.4-mini"]

    asyncio.run(_run())


def test_complete_supports_response_format_json_mode() -> None:
    async def _run() -> None:
        request = CompletionRequest(
            model_name="gpt-5.4",
            messages=[ChatMessage(role="user", content="hello")],
            generation=GenerationParams(
                max_tokens=64,
                temperature=0,
                response_format={"type": "json_object"},
            ),
        )
        response = await complete(
            "sk-test",
            "https://example.com/v1",
            request,
            provider_type=ProviderType.OPENAI,
            transport=_mock_transport(),
        )
        assert response.provider_type == ProviderType.OPENAI
        assert response.model_name == "gpt-5.4"
        assert response.text.startswith("ok:")
        assert response.usage is not None
        assert response.usage.total_tokens == 15

    asyncio.run(_run())


def test_test_connection_openai_provider() -> None:
    async def _run() -> None:
        result = await provider_test_connection(
            "sk-test",
            "https://example.com/v1",
            "gpt-4.1",
            provider_type=ProviderType.OPENAI,
            transport=_mock_transport(),
        )
        assert result.success is True
        assert result.model_name == "gpt-4.1"
        assert result.provider_type == ProviderType.OPENAI
        assert result.latency_ms is not None

    asyncio.run(_run())


def test_test_connection_tolerates_extended_usage_fields() -> None:
    async def _run() -> None:
        result = await provider_test_connection(
            "sk-test",
            "https://example.com/v1",
            "gpt-4.1",
            provider_type=ProviderType.OPENAI_COMPATIBLE,
            transport=_mock_transport_with_extra_usage_fields(),
        )
        assert result.success is True
        assert result.error is None
        assert result.raw_response["usage"]["total_tokens"] == 15
        assert "completion_tokens_details" not in result.raw_response["usage"]

    asyncio.run(_run())
