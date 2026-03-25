from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

try:  # pragma: no cover - exercised through tests when dependency is installed
    import tiktoken as _TIKTOKEN
except Exception:  # pragma: no cover - fallback path is still tested
    _TIKTOKEN = None

_MODEL_ENCODING_CANDIDATES = ("o200k_base", "cl100k_base")


@lru_cache(maxsize=8)
def _get_encoding(name: str):
    if _TIKTOKEN is None:
        return None
    try:
        return _TIKTOKEN.get_encoding(name)
    except Exception:
        return None


def _encoding_for_model(model_name: str | None):
    if _TIKTOKEN is None:
        return None
    if model_name:
        try:
            return _TIKTOKEN.encoding_for_model(model_name)
        except Exception:
            pass
    for candidate in _MODEL_ENCODING_CANDIDATES:
        encoding = _get_encoding(candidate)
        if encoding is not None:
            return encoding
    return None


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    byte_length = len(text.encode("utf-8"))
    return max(1, math.ceil(byte_length / 4.0))


def count_text_tokens(text: str, model_name: str | None = None) -> int:
    if not text:
        return 0

    encoding = _encoding_for_model(model_name)
    if encoding is not None:
        return len(encoding.encode(text))
    return estimate_tokens(text)


def count_messages_tokens(messages: list[dict[str, Any]], model_name: str | None = None) -> int:
    total = 0
    for message in messages:
        content = str(message.get("content") or "")
        total += count_text_tokens(content, model_name=model_name)
        total += 4
        if message.get("name"):
            total += 1
    return total + 2


def count_chat_tokens(messages: list[Any], model_name: str | None = None) -> int:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, dict):
            normalized.append(message)
        else:
            normalized.append(
                {
                    "role": getattr(message, "role", "user"),
                    "content": getattr(message, "content", ""),
                    "name": getattr(message, "name", None),
                }
            )
    return count_messages_tokens(normalized, model_name=model_name)
