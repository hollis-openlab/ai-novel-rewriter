from __future__ import annotations

from typing import Any

SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "x_api_key",
    "x-api-key",
    "authorization",
    "proxy_authorization",
    "proxy-authorization",
    "access_token",
    "refresh_token",
    "credential",
    "credentials",
    "secret",
    "password",
}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS:
        return True
    return normalized.endswith("_api_key") or normalized.endswith("_secret")


def sanitize_public_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_sensitive_key(str(key)):
                redacted[str(key)] = "***"
                continue
            redacted[str(key)] = sanitize_public_payload(value)
        return redacted
    if isinstance(payload, list):
        return [sanitize_public_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [sanitize_public_payload(item) for item in payload]
    return payload
