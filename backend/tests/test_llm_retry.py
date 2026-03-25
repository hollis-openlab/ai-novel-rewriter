from __future__ import annotations

import asyncio

from backend.app.llm.interface import ChatMessage, GenerationParams
from backend.app.llm.retry import RetryContext, retry_with_strategies


def test_retry_engine_applies_temperature_hint_and_fallback_provider() -> None:
    async def _run() -> None:
        delays: list[float] = []
        calls: list[tuple[str | None, float | None, str]] = []

        async def _sleep(delay: float) -> None:
            delays.append(delay)

        async def _operation(context: RetryContext) -> str:
            temperature = context.generation.temperature
            first_message = context.messages[0].content if context.messages else ""
            calls.append((context.provider_id, temperature, first_message))

            if context.provider_id == "primary":
                raise RuntimeError("primary provider failed")
            if temperature is None or temperature >= 0.7:
                raise RuntimeError("temperature not adjusted")
            if "重试提示" not in first_message:
                raise RuntimeError("hint not appended")
            return f"ok:{context.provider_id}:{temperature}"

        result = await retry_with_strategies(
            _operation,
            RetryContext(
                generation=GenerationParams(temperature=0.7),
                messages=[ChatMessage(role="system", content="原始 system prompt"), ChatMessage(role="user", content="请输出结果")],
                provider_id="primary",
                provider_candidates=["primary", "secondary"],
            ),
            max_attempts=4,
            base_delay_seconds=0.25,
            sleep=_sleep,
        )

        assert result == "ok:secondary:0.6"
        assert delays == [0.25, 0.5, 1.0]
        assert calls[0][0] == "primary"
        assert calls[-1][0] == "secondary"
        assert calls[-1][1] == 0.6
        assert "重试提示" in calls[-1][2]

    asyncio.run(_run())
