from __future__ import annotations

from backend.app.i18n.zh import ZH_MESSAGES
from backend.app.i18n.en import EN_MESSAGES

SUPPORTED_LANGUAGES = ("zh", "en")
DEFAULT_LANGUAGE = "zh"

_MESSAGES: dict[str, dict[str, str]] = {
    "zh": ZH_MESSAGES,
    "en": EN_MESSAGES,
}


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs: object) -> str:
    """Translate a message key to the given language.

    Falls back to Chinese if the key is missing for the requested language.
    Supports interpolation via keyword arguments: t("hello", name="World")
    """
    resolved_lang = lang if lang in _MESSAGES else DEFAULT_LANGUAGE
    messages = _MESSAGES[resolved_lang]
    template = messages.get(key) or _MESSAGES[DEFAULT_LANGUAGE].get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def parse_accept_language(header: str | None) -> str:
    """Extract the preferred supported language from an Accept-Language header."""
    if not header:
        return DEFAULT_LANGUAGE
    for part in header.split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("en"):
            return "en"
        if tag.startswith("zh"):
            return "zh"
    return DEFAULT_LANGUAGE
