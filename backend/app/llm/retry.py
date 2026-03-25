from __future__ import annotations

import asyncio
import copy
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from backend.app.llm.interface import ChatMessage, GenerationParams


@dataclass(slots=True)
class RetryContext:
    generation: GenerationParams = field(default_factory=GenerationParams)
    messages: list[ChatMessage] = field(default_factory=list)
    provider_id: str | None = None
    provider_candidates: list[str] = field(default_factory=list)
    provider_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class RetryStrategy(Protocol):
    name: str

    def mutate(self, context: RetryContext, error: Exception) -> RetryContext:
        ...


@dataclass(slots=True)
class AdjustTemperatureStrategy:
    name: str = "adjust_temperature"
    step: float = 0.1
    minimum_temperature: float = 0.1
    fallback_temperature: float = 0.6

    def mutate(self, context: RetryContext, error: Exception) -> RetryContext:
        next_context = copy.deepcopy(context)
        current = next_context.generation.temperature
        if current is None:
            next_context.generation = next_context.generation.model_copy(update={"temperature": self.fallback_temperature})
            return next_context

        next_temperature = max(self.minimum_temperature, round(current - self.step, 3))
        next_context.generation = next_context.generation.model_copy(update={"temperature": next_temperature})
        return next_context


@dataclass(slots=True)
class AppendHintStrategy:
    name: str = "append_hint"
    hint: str = "请严格遵守输出格式，不要解释，不要添加多余文本。"

    def mutate(self, context: RetryContext, error: Exception) -> RetryContext:
        next_context = copy.deepcopy(context)
        hint_text = str(next_context.metadata.get("retry_hint") or self.hint).strip()
        next_context.metadata["retry_hint"] = hint_text

        if next_context.messages and next_context.messages[0].role == "system":
            next_context.messages[0] = next_context.messages[0].model_copy(
                update={"content": f"{next_context.messages[0].content}\n\n重试提示：{hint_text}"}
            )
            return next_context

        next_context.messages.append(ChatMessage(role="system", content=f"重试提示：{hint_text}"))
        return next_context


@dataclass(slots=True)
class FallbackProviderStrategy:
    name: str = "fallback_provider"

    def mutate(self, context: RetryContext, error: Exception) -> RetryContext:
        next_context = copy.deepcopy(context)
        if next_context.provider_candidates:
            next_index = min(next_context.provider_index + 1, len(next_context.provider_candidates) - 1)
            next_context.provider_index = next_index
            next_context.provider_id = next_context.provider_candidates[next_index]
            next_context.metadata["fallback_provider_used"] = next_context.provider_id
        return next_context


def build_default_retry_strategies(*, hint: str | None = None) -> list[RetryStrategy]:
    strategies: list[RetryStrategy] = [AdjustTemperatureStrategy(), AppendHintStrategy()]
    if hint is not None:
        strategies[1] = AppendHintStrategy(hint=hint)
    strategies.append(FallbackProviderStrategy())
    return strategies


async def retry_with_strategies(
    operation: Callable[[RetryContext], Awaitable[Any] | Any],
    context: RetryContext,
    *,
    strategies: list[RetryStrategy] | None = None,
    max_attempts: int | None = None,
    base_delay_seconds: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    active_strategies = strategies or build_default_retry_strategies()
    attempts = max_attempts or (len(active_strategies) + 1)
    working_context = copy.deepcopy(context)
    last_error: Exception | None = None

    for attempt_index in range(attempts):
        try:
            result = operation(working_context)
            if inspect.isawaitable(result):
                result = await result
            working_context.metadata["last_attempt"] = attempt_index + 1
            return result
        except Exception as exc:  # pragma: no cover - exercised via tests
            last_error = exc
            working_context.metadata["last_error"] = str(exc)
            working_context.metadata["last_attempt"] = attempt_index + 1
            if attempt_index >= attempts - 1:
                raise

            if attempt_index < len(active_strategies):
                working_context = active_strategies[attempt_index].mutate(working_context, exc)

            delay = base_delay_seconds * (2 ** attempt_index)
            await sleep(delay)

    if last_error is not None:  # pragma: no cover - defensive fallback
        raise last_error
    raise RuntimeError("retry_with_strategies exhausted without a terminal result")

