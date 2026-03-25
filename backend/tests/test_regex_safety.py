from __future__ import annotations

import backend.app.services.splitting as splitting_module
import pytest

from backend.app.contracts.api import SplitRuleCreateRequest, SplitRuleSpec, SplitRulesConfigRequest
from backend.app.core.errors import AppError, ErrorCode
from backend.app.services.splitting import create_custom_rule, get_split_rules_snapshot, replace_split_rules_state, split_text_to_chapters


@pytest.mark.usefixtures("isolated_data_dir")
@pytest.mark.parametrize(
    "pattern",
    [
        r"^(a+)+$",
        r".*.*",
        "a?" * 25,
    ],
)
def test_regex_complexity_rejects_dangerous_patterns(pattern: str) -> None:
    with pytest.raises(AppError) as exc_info:
        create_custom_rule(
            SplitRuleCreateRequest(
                name="danger",
                pattern=pattern,
                priority=1,
                enabled=True,
            )
        )

    assert exc_info.value.code == ErrorCode.REGEX_INVALID


@pytest.mark.usefixtures("isolated_data_dir")
def test_regex_timeout_interrupts_catastrophic_backtracking(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = get_split_rules_snapshot()
    replace_split_rules_state(
        SplitRulesConfigRequest(
            builtin_rules=[rule.model_copy(update={"enabled": False}) for rule in snapshot.builtin_rules],
            custom_rules=[
                SplitRuleSpec(
                    id="catastrophic-rule",
                    name="Catastrophic",
                    pattern=r"^(?:a|aa)+$",
                    priority=1,
                    enabled=True,
                    builtin=False,
                )
            ],
        )
    )

    monkeypatch.setattr(splitting_module, "REGEX_TIMEOUT_SECONDS", 0.01)
    state = splitting_module.load_split_rules_state()
    text = "a" * 50000 + "!"

    with pytest.raises(AppError) as exc_info:
        split_text_to_chapters(
            text,
            source_revision="revision",
            rules_version=state.rules_version,
        )

    assert exc_info.value.code == ErrorCode.REGEX_TIMEOUT
    assert exc_info.value.details["rule_name"] == "Catastrophic"
