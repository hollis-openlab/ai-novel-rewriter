# Rewrite Window Mode Rollout & Ops Runbook

## 1. Scope

This runbook defines rollout, rollback, and troubleshooting procedures for sentence-window rewrite mode.

Feature gates:
- `AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED`
- `AI_NOVEL_REWRITE_WINDOW_MODE_GUARDRAIL_ENABLED`
- `AI_NOVEL_REWRITE_WINDOW_MODE_AUDIT_ENABLED`
- `AI_NOVEL_REWRITE_WINDOW_MODE_NOVEL_ALLOWLIST`
- `AI_NOVEL_REWRITE_WINDOW_MODE_TASK_ALLOWLIST`

## 2. Metrics and Definitions

Rewrite run detail exposes:
- `windows_total`: total logical rewrite windows in the run
- `windows_retried`: windows with attempt count > 1
- `windows_hard_failed`: windows that hit hard-fail guardrail
- `windows_rollback`: windows rolled back to original text
- `windows_avg_chars`: average window size (chars)
- `window_retry_rate`: `windows_retried / windows_total`
- `window_hard_fail_rate`: `windows_hard_failed / windows_total`
- `window_rollback_rate`: `windows_rollback / windows_total`

## 3. Rollout Plan

1. Enable audit + guardrail first in a controlled subset (novel/task allowlist).
2. Start with 5%-10% traffic (internal novels/tasks only).
3. Expand to 30% after metrics stay stable for at least one full processing day.
4. Expand to 100% when release gate in section 4 passes for 3 consecutive days.

## 4. Release Gate and Alert Thresholds

Hard gate (must pass):
- `window_rollback_rate <= 0.05`
- `window_hard_fail_rate <= 0.08`
- `window_retry_rate <= 0.25`

Warning gate (needs manual review before expansion):
- `window_rollback_rate` in `(0.05, 0.10]`
- `window_hard_fail_rate` in `(0.08, 0.15]`
- `window_retry_rate` in `(0.25, 0.40]`

Blocker (stop expansion and investigate):
- `window_rollback_rate > 0.10`
- `window_hard_fail_rate > 0.15`
- Frequent chapter-level warnings from same `warning_codes`

## 5. One-Click Rollback

Rollback switch:
- Set `AI_NOVEL_REWRITE_WINDOW_MODE_ENABLED=false`

Expected effect:
- New rewrite runs fall back to legacy segment execution path.
- Existing artifacts remain readable via compatibility path.
- In-flight run is not force-aborted; rollback applies from next run.

## 6. Troubleshooting Workflow

1. Identify affected chapter from rewrite run detail (`chapter_index`).
2. Check chapter rewrite detail for window-level fields:
   - `window_id`, `segment_id`, `run_seq`, `warning_codes`, `window_attempts`
3. Determine failure type:
   - Guardrail hard-fail with retry success: monitor only
   - Guardrail hard-fail with `rollback_original`: inspect prompt and boundaries
   - Truncation (`finish_reason=length`): lower window size or adjust model token budget
4. Confirm assemble integrity:
   - If chapter has `WINDOW_OUTSIDE_TEXT_CHANGED`, use original text fallback and re-run rewrite after fixing source window data.
5. If issue is widespread, trigger rollback switch and collect sample windows for offline analysis.

## 7. Operational Checklist

Before enabling broader traffic:
- Verify chapter and stage APIs return `warning_count/warning_codes` correctly.
- Verify chapter detail shows window attempt chain and rollback reasons.
- Verify replay/retry skips already completed windows by window identity key.
- Verify rollback switch returns stable outputs on a smoke novel.
