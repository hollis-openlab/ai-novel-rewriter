from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.app.llm.interface import GenerationParams

_PARAM_FIELDS = set(GenerationParams.model_fields)


def _to_param_dict(source: GenerationParams | Mapping[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, GenerationParams):
        return source.model_dump(exclude_none=True)
    if isinstance(source, Mapping):
        return {key: value for key, value in source.items() if key in _PARAM_FIELDS and value is not None}
    raise TypeError(f"Unsupported generation params source: {type(source).__name__}")


def resolve_generation_params(
    provider_defaults: GenerationParams | Mapping[str, Any] | None = None,
    runtime_computed_fields: GenerationParams | Mapping[str, Any] | None = None,
    per_call_overrides: GenerationParams | Mapping[str, Any] | None = None,
) -> GenerationParams:
    """
    Merge generation params using the precedence required by the design:
    provider defaults -> runtime-computed fields -> per-call overrides.
    """

    merged: dict[str, Any] = {}
    for source in (provider_defaults, runtime_computed_fields, per_call_overrides):
        merged.update(_to_param_dict(source))
    return GenerationParams.model_validate(merged)


def build_generation_params(
    *,
    provider_defaults: GenerationParams | Mapping[str, Any] | None = None,
    runtime_computed_fields: GenerationParams | Mapping[str, Any] | None = None,
    per_call_overrides: GenerationParams | Mapping[str, Any] | None = None,
) -> GenerationParams:
    return resolve_generation_params(
        provider_defaults=provider_defaults,
        runtime_computed_fields=runtime_computed_fields,
        per_call_overrides=per_call_overrides,
    )
