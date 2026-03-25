from __future__ import annotations

from backend.app.llm.generation import resolve_generation_params
from backend.app.llm.interface import GenerationParams


def test_generation_params_precedence_and_none_handling() -> None:
    provider_defaults = GenerationParams(
        temperature=0.2,
        max_tokens=512,
        top_p=0.8,
    )
    runtime_computed_fields = {
        "temperature": 0.35,
        "presence_penalty": 0.1,
        "top_p": None,
    }
    per_call_overrides = GenerationParams(
        max_tokens=128,
        frequency_penalty=0.2,
        stop=["END"],
    )

    resolved = resolve_generation_params(
        provider_defaults=provider_defaults,
        runtime_computed_fields=runtime_computed_fields,
        per_call_overrides=per_call_overrides,
    )

    assert resolved.temperature == 0.35
    assert resolved.max_tokens == 128
    assert resolved.top_p == 0.8
    assert resolved.presence_penalty == 0.1
    assert resolved.frequency_penalty == 0.2
    assert resolved.stop == ["END"]
