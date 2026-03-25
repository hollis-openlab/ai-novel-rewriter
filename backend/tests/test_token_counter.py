from __future__ import annotations

import tiktoken

from backend.app.llm import token_counter


def test_count_text_tokens_uses_tiktoken_for_known_model() -> None:
    text = "Hello, token counter!"
    expected = len(tiktoken.encoding_for_model("gpt-4o-mini").encode(text))

    assert token_counter.count_text_tokens(text, model_name="gpt-4o-mini") == expected


def test_count_text_tokens_falls_back_when_tiktoken_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(token_counter, "_TIKTOKEN", None)

    assert token_counter.count_text_tokens("abcd", model_name="unknown-model") == 1
    assert token_counter.estimate_tokens("abcd") == 1
    assert token_counter.count_messages_tokens([{"role": "user", "content": "abcd"}]) == 7
