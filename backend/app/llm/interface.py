from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.core import ProviderType


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None


class GenerationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop: list[str] | str | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str
    messages: list[ChatMessage] = Field(default_factory=list)
    generation: GenerationParams = Field(default_factory=GenerationParams)
    metadata: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


class UsageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class CompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: ProviderType
    model_name: str
    text: str
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = None
    usage: UsageInfo | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: ProviderType
    model_name: str
    success: bool
    latency_ms: int | None = Field(default=None, ge=0)
    error: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    tested_at: datetime = Field(default_factory=datetime.utcnow)


@runtime_checkable
class LlmProvider(Protocol):
    provider_type: ProviderType

    async def fetch_models(self) -> list[str]:
        ...

    async def test_connection(self, model_name: str) -> ConnectionTestResult:
        ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        ...
