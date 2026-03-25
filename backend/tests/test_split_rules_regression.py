from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from backend.app.api.routes.split_rules import confirm_split_rules, create_split_rule, preview_split_rules
from backend.app.contracts.api import (
    SplitConfirmRequest,
    SplitRuleCreateRequest,
    SplitRulesConfigRequest,
    SplitRulesPreviewRequest,
    SplitRuleSpec,
)
from backend.app.core.artifact_store import ArtifactStore
from backend.app.core.errors import AppError, ErrorCode
from backend.app.services.splitting import (
    build_preview_split_rules_state,
    confirm_split_preview,
    decode_preview_token,
    get_split_rules_snapshot,
    load_split_rules_state,
    make_split_preview,
    replace_split_rules_state,
    split_text_to_chapters,
)

pytestmark = pytest.mark.usefixtures("isolated_data_dir")


class FakeRequest:
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(artifact_store=artifact_store))


def test_split_rules_confirm_allows_preview_valid_false(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-1")

        # Normalize global split rules to a deterministic baseline for this test.
        snapshot = get_split_rules_snapshot()
        replace_split_rules_state(
            SplitRulesConfigRequest(
                builtin_rules=[rule.model_copy(update={"enabled": True}) for rule in snapshot.builtin_rules],
                custom_rules=[],
            )
        )

        raw_text = "第一章\n内容一\n\n第二章\n内容二"
        (store.novel_dir("novel-1") / "raw.txt").write_text(raw_text, encoding="utf-8")

        request = FakeRequest(store)
        state = load_split_rules_state()
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        preview = await preview_split_rules(
            SplitRulesPreviewRequest(
                novel_id="novel-1",
                source_revision=source_revision,
                rules_version=state.rules_version,
                sample_size=10,
            ),
            request=request,
        )

        assert preview.preview_valid is False
        assert preview.estimated_chapters == 2

        confirmed = await confirm_split_rules(
            SplitConfirmRequest(preview_token=preview.preview_token),
            request=request,
        )

        assert confirmed.preview_token == preview.preview_token
        assert confirmed.preview_valid is False
        assert confirmed.chapter_count == 2

    asyncio.run(_run())


def test_split_rules_confirm_rejects_stale_source_revision(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-2")

        snapshot = get_split_rules_snapshot()
        replace_split_rules_state(
            SplitRulesConfigRequest(
                builtin_rules=[rule.model_copy(update={"enabled": True}) for rule in snapshot.builtin_rules],
                custom_rules=[],
            )
        )

        raw_text = "第一章\n内容一\n\n第二章\n内容二\n\n第三章\n内容三"
        raw_path = store.novel_dir("novel-2") / "raw.txt"
        raw_path.write_text(raw_text, encoding="utf-8")

        request = FakeRequest(store)
        state = load_split_rules_state()
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        preview = await preview_split_rules(
            SplitRulesPreviewRequest(
                novel_id="novel-2",
                source_revision=source_revision,
                rules_version=state.rules_version,
                sample_size=10,
            ),
            request=request,
        )
        assert preview.preview_valid is True
        assert preview.estimated_chapters == 3

        raw_path.write_text(raw_text + "\n尾注", encoding="utf-8")

        with pytest.raises(AppError) as exc_info:
            await confirm_split_rules(
                SplitConfirmRequest(preview_token=preview.preview_token),
                request=request,
            )
        assert exc_info.value.code == ErrorCode.PREVIEW_STALE

    asyncio.run(_run())


def test_split_rules_preview_respects_custom_rule_priority(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-3")

        snapshot = get_split_rules_snapshot()
        replace_split_rules_state(
            SplitRulesConfigRequest(
                builtin_rules=[rule.model_copy(update={"enabled": False}) for rule in snapshot.builtin_rules],
                custom_rules=[
                    SplitRuleSpec(
                        id="custom-ch",
                        name="CH Rule",
                        pattern=r"CH\s+\d+",
                        priority=1,
                        enabled=True,
                        builtin=False,
                    )
                ],
            )
        )

        raw_text = "CH 1\n内容一\n\nCH 2\n内容二\n\nCH 3\n内容三"
        (store.novel_dir("novel-3") / "raw.txt").write_text(raw_text, encoding="utf-8")

        request = FakeRequest(store)
        state = load_split_rules_state()
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        preview = await preview_split_rules(
            SplitRulesPreviewRequest(
                novel_id="novel-3",
                source_revision=source_revision,
                rules_version=state.rules_version,
                sample_size=10,
            ),
            request=request,
        )

        assert preview.matched_count == 3
        assert preview.estimated_chapters == 3
        assert preview.matched_lines
        assert preview.matched_lines[0].rule_name == "CH Rule"

    asyncio.run(_run())


def test_split_rules_preview_returns_all_chapters(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-sample")

        snapshot = get_split_rules_snapshot()
        replace_split_rules_state(
            SplitRulesConfigRequest(
                builtin_rules=[rule.model_copy(update={"enabled": True}) for rule in snapshot.builtin_rules],
                custom_rules=[],
            )
        )

        raw_text = "\n\n".join(
            [
                "第一章",
                "内容一",
                "第二章",
                "内容二",
                "第三章",
                "内容三",
                "第四章",
                "内容四",
                "第五章",
                "内容五",
            ]
        )
        (store.novel_dir("novel-sample") / "raw.txt").write_text(raw_text, encoding="utf-8")

        request = FakeRequest(store)
        state = load_split_rules_state()
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        preview = await preview_split_rules(
            SplitRulesPreviewRequest(
                novel_id="novel-sample",
                source_revision=source_revision,
                rules_version=state.rules_version,
                sample_size=2,
            ),
            request=request,
        )

        assert preview.preview_valid is True
        assert preview.estimated_chapters == 5
        assert len(preview.chapters) == 5

        confirmed = await confirm_split_rules(
            SplitConfirmRequest(preview_token=preview.preview_token),
            request=request,
        )
        assert confirmed.chapter_count == 5
        assert confirmed.boundary_hash == preview.boundary_hash

    asyncio.run(_run())


@pytest.mark.usefixtures("isolated_data_dir")
def test_selected_rule_preview_confirm_remains_consistent() -> None:
    replace_split_rules_state(
        SplitRulesConfigRequest(
            builtin_rules=[],
            custom_rules=[
                SplitRuleSpec(
                    id="custom-two",
                    name="Custom Two",
                    pattern=r"^CHAPTER\s+[12]$",
                    priority=1,
                    enabled=True,
                    builtin=False,
                ),
                SplitRuleSpec(
                    id="custom-three",
                    name="Custom Three",
                    pattern=r"^CHAPTER\s+\d+$",
                    priority=2,
                    enabled=True,
                    builtin=False,
                ),
            ],
        )
    )
    state = load_split_rules_state()
    text = "CHAPTER 1\n\n内容一\n\nCHAPTER 2\n\n内容二\n\nCHAPTER 3\n\n内容三"
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    preview = make_split_preview(
        "novel-selected",
        text,
        source_revision,
        state.rules_version,
        state=state,
        selected_rule_id="custom-two",
    )
    assert preview.preview_valid is False
    assert preview.matched_count == 2
    assert preview.estimated_chapters == 2

    auto_selected = split_text_to_chapters(
        text,
        source_revision=source_revision,
        rules_version=state.rules_version,
        state=state,
    )
    assert auto_selected.matched_count == 3

    confirmed = confirm_split_preview(
        "novel-selected",
        preview.preview_token,
        text,
        state=state,
    )
    assert confirmed.preview_token == preview.preview_token
    assert confirmed.preview_valid is False
    assert confirmed.chapter_count == 2

    token_payload = decode_preview_token(preview.preview_token)
    assert token_payload.selected_rule_id == "custom-two"


@pytest.mark.usefixtures("isolated_data_dir")
def test_selected_rule_validation_errors() -> None:
    replace_split_rules_state(
        SplitRulesConfigRequest(
            builtin_rules=[],
            custom_rules=[
                SplitRuleSpec(
                    id="disabled-rule",
                    name="Disabled Rule",
                    pattern=r"^CHAPTER\s+\d+$",
                    priority=1,
                    enabled=False,
                    builtin=False,
                ),
            ],
        )
    )
    state = load_split_rules_state()
    text = "CHAPTER 1\n\n内容一\n\nCHAPTER 2\n\n内容二\n\nCHAPTER 3\n\n内容三"
    source_revision = hashlib.sha256(text.encode("utf-8")).hexdigest()

    with pytest.raises(AppError) as missing_exc:
        split_text_to_chapters(
            text,
            source_revision=source_revision,
            rules_version=state.rules_version,
            state=state,
            selected_rule_id="missing-rule",
        )
    assert missing_exc.value.code == ErrorCode.VALIDATION_ERROR
    assert missing_exc.value.details["reason"] == "rule_not_found"

    with pytest.raises(AppError) as disabled_exc:
        split_text_to_chapters(
            text,
            source_revision=source_revision,
            rules_version=state.rules_version,
            state=state,
            selected_rule_id="disabled-rule",
        )
    assert disabled_exc.value.code == ErrorCode.VALIDATION_ERROR
    assert disabled_exc.value.details["reason"] == "rule_disabled"


@pytest.mark.usefixtures("isolated_data_dir")
def test_split_rules_preview_supports_draft_rules_without_persisting(tmp_path) -> None:
    async def _run() -> None:
        store = ArtifactStore(tmp_path / "data")
        store.ensure_novel_dirs("novel-draft")
        raw_text = "DRAFT 1\n\n内容一\n\nDRAFT 2\n\n内容二\n\nDRAFT 3\n\n内容三"
        (store.novel_dir("novel-draft") / "raw.txt").write_text(raw_text, encoding="utf-8")

        snapshot = get_split_rules_snapshot()
        replace_split_rules_state(
            SplitRulesConfigRequest(
                builtin_rules=[rule.model_copy(update={"enabled": True}) for rule in snapshot.builtin_rules],
                custom_rules=[],
            )
        )
        persisted_before = load_split_rules_state()

        draft_builtin = [rule.model_copy(update={"enabled": False}) for rule in persisted_before.builtin_rules]
        draft_custom = [
            SplitRuleSpec(
                id="draft-rule",
                name="Draft Rule",
                pattern=r"^DRAFT\s+\d+$",
                priority=1,
                enabled=True,
                builtin=False,
            )
        ]
        draft_state = build_preview_split_rules_state(
            builtin_rules=draft_builtin,
            custom_rules=draft_custom,
            fallback_state=persisted_before,
        )
        source_revision = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        request = FakeRequest(store)

        preview = await preview_split_rules(
            SplitRulesPreviewRequest(
                novel_id="novel-draft",
                source_revision=source_revision,
                rules_version=draft_state.rules_version,
                sample_size=10,
                selected_rule_id="draft-rule",
                builtin_rules=draft_builtin,
                custom_rules=draft_custom,
            ),
            request=request,
        )

        assert preview.matched_count == 3
        assert preview.estimated_chapters == 3
        assert preview.preview_valid is True
        assert decode_preview_token(preview.preview_token).selected_rule_id == "draft-rule"

        persisted_after = load_split_rules_state()
        assert persisted_after.rules_version == persisted_before.rules_version
        assert persisted_after.custom_rules == persisted_before.custom_rules
        assert persisted_after.builtin_rules == persisted_before.builtin_rules

    asyncio.run(_run())


def test_create_split_rule_rejects_invalid_pattern() -> None:
    async def _run() -> None:
        with pytest.raises(AppError) as exc_info:
            await create_split_rule(
                SplitRuleCreateRequest(
                    name="broken",
                    pattern="(",
                    priority=1,
                    enabled=True,
                )
            )
        assert exc_info.value.code == ErrorCode.REGEX_INVALID

    asyncio.run(_run())
