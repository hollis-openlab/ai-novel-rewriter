from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.settings import Settings, get_settings

API_KEY_ENCRYPTION_FILENAME = "api_key_encryption.key"


def _secret_key_path(settings: Settings) -> Path:
    return settings.data_dir / "secrets" / API_KEY_ENCRYPTION_FILENAME


def _derive_fernet_key(secret: str) -> bytes:
    raw = secret.strip()
    if not raw:
        raise AppError(ErrorCode.VALIDATION_ERROR, "API key encryption secret is required")
    try:
        Fernet(raw.encode("utf-8"))
        return raw.encode("utf-8")
    except Exception:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)


def _load_or_create_fernet_key(settings: Settings | None = None) -> bytes:
    resolved_settings = settings or get_settings()
    secret = resolved_settings.api_key_encryption_key
    if secret:
        return _derive_fernet_key(secret)

    key_path = _secret_key_path(resolved_settings)
    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
        if not key:
            raise AppError(ErrorCode.CONFIG_INVALID, "API key encryption file is empty", details={"path": str(key_path)})
        return _derive_fernet_key(key)

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode("utf-8")
    key_path.write_text(key, encoding="utf-8")
    return key.encode("utf-8")


def get_fernet(settings: Settings | None = None) -> Fernet:
    return Fernet(_load_or_create_fernet_key(settings))


def encrypt_api_key(api_key: str, settings: Settings | None = None) -> str:
    if not api_key or not api_key.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "api_key is required")
    token = get_fernet(settings).encrypt(api_key.strip().encode("utf-8"))
    return token.decode("utf-8")


def decrypt_api_key(encrypted_api_key: str, settings: Settings | None = None) -> str:
    if not encrypted_api_key or not encrypted_api_key.strip():
        raise AppError(ErrorCode.VALIDATION_ERROR, "encrypted_api_key is required")
    try:
        plaintext = get_fernet(settings).decrypt(encrypted_api_key.strip().encode("utf-8"))
    except InvalidToken as exc:
        raise AppError(
            ErrorCode.CONFIG_INVALID,
            "Failed to decrypt API key",
            details={"reason": "invalid_token"},
        ) from exc
    return plaintext.decode("utf-8")
