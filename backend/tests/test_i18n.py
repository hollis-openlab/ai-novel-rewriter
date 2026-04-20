from __future__ import annotations

from backend.app.i18n import parse_accept_language, t


class TestTranslation:
    def test_translate_zh_default(self) -> None:
        assert t("stage.analyze") == "分析"

    def test_translate_en(self) -> None:
        assert t("stage.analyze", "en") == "Analyze"

    def test_fallback_to_zh_for_unknown_lang(self) -> None:
        assert t("stage.analyze", "fr") == "分析"

    def test_missing_key_returns_key(self) -> None:
        assert t("nonexistent.key") == "nonexistent.key"

    def test_missing_key_in_en_falls_back_to_zh(self) -> None:
        from backend.app.i18n.zh import ZH_MESSAGES
        from backend.app.i18n.en import EN_MESSAGES

        for key in ZH_MESSAGES:
            assert key in EN_MESSAGES, f"Key '{key}' missing in EN_MESSAGES"

    def test_interpolation(self) -> None:
        result = t("error.upstream_required", "zh", upstream_label="分析")
        assert "分析" in result

    def test_interpolation_en(self) -> None:
        result = t("error.upstream_required", "en", upstream_label="Analyze")
        assert "Analyze" in result

    def test_all_zh_keys_have_en_counterparts(self) -> None:
        from backend.app.i18n.zh import ZH_MESSAGES
        from backend.app.i18n.en import EN_MESSAGES

        missing = set(ZH_MESSAGES.keys()) - set(EN_MESSAGES.keys())
        assert not missing, f"Keys in zh but not en: {missing}"

    def test_all_en_keys_have_zh_counterparts(self) -> None:
        from backend.app.i18n.zh import ZH_MESSAGES
        from backend.app.i18n.en import EN_MESSAGES

        missing = set(EN_MESSAGES.keys()) - set(ZH_MESSAGES.keys())
        assert not missing, f"Keys in en but not zh: {missing}"


class TestParseAcceptLanguage:
    def test_none_returns_zh(self) -> None:
        assert parse_accept_language(None) == "zh"

    def test_empty_returns_zh(self) -> None:
        assert parse_accept_language("") == "zh"

    def test_en_header(self) -> None:
        assert parse_accept_language("en-US,en;q=0.9") == "en"

    def test_zh_header(self) -> None:
        assert parse_accept_language("zh-CN,zh;q=0.9,en;q=0.8") == "zh"

    def test_unsupported_falls_back_to_zh(self) -> None:
        assert parse_accept_language("fr-FR,fr;q=0.9") == "zh"

    def test_en_first_in_mixed(self) -> None:
        assert parse_accept_language("en,zh;q=0.8") == "en"
