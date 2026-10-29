from __future__ import annotations

import json
import hashlib

import pytest






def test_junit_counts_supports_pytest_testsuites_wrapper() -> None:
    import xml.etree.ElementTree as ET

    import scripts.run_public_memory_clean_eval as runner

    root = ET.fromstring(
        "<testsuites><testsuite tests='20' failures='0' errors='0' />"
        "<testsuite tests='5' failures='1' errors='0' /></testsuites>"
    )

    assert runner._junit_counts(root) == (25, 1, 0)








def test_h3_1_budget_reconciliation_is_idempotent_and_releases_reservation() -> None:
    import scripts.run_public_memory_clean_eval as runner

    ledger = {
        "schema_version": "linkloom-public-memory-global-budget/v1",
        "hard_cap_usd": 10.0,
        "active_run_id": "bc850e0cfe4745d79117660dd5a74367",
        "runs": [
            {
                "run_id": "bc850e0cfe4745d79117660dd5a74367",
                "cell": "sh_32k",
                "status": "RUNNING",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 6.0,
                "estimated_spend_upper_bound_usd": 0.0,
            }
        ],
    }

    changed = runner._reconcile_budget_row(
        ledger,
        run_id="bc850e0cfe4745d79117660dd5a74367",
        status="HARNESS_STRESS_PASS",
        estimated_spend_upper_bound_usd=0.8019,
        expected_reserved_usd=6.0,
        hard_cap_usd=10.0,
    )
    again = runner._reconcile_budget_row(
        ledger,
        run_id="bc850e0cfe4745d79117660dd5a74367",
        status="HARNESS_STRESS_PASS",
        estimated_spend_upper_bound_usd=0.8019,
        expected_reserved_usd=6.0,
        hard_cap_usd=10.0,
    )

    assert changed is True
    assert again is False
    assert ledger["active_run_id"] is None
    assert ledger["runs"][0]["status"] == "HARNESS_STRESS_PASS"
    assert ledger["runs"][0]["estimated_spend_upper_bound_usd"] == 0.8019
    assert ledger["runs"][0]["reserved_usd"] == 0.0
    with pytest.raises(runner.CleanEvalBlocked, match="BUDGET_RECONCILIATION_CONFLICT"):
        runner._reconcile_budget_row(
            ledger,
            run_id="bc850e0cfe4745d79117660dd5a74367",
            status="HARNESS_STRESS_PASS",
            estimated_spend_upper_bound_usd=0.8018,
            expected_reserved_usd=6.0,
            hard_cap_usd=10.0,
        )


def test_budget_reconciliation_journal_is_append_only_and_idempotent(tmp_path) -> None:
    import scripts.run_public_memory_clean_eval as runner

    journal = tmp_path / "budget_reconciliation_events.jsonl"
    payload = {
        "event_type": "SETTLE_DIAGNOSTIC_RESERVATION",
        "run_id": "bc850e0cfe4745d79117660dd5a74367",
        "estimated_spend_upper_bound_usd": 0.8019,
        "reservation_released_usd": 6.0,
    }

    first = runner._append_reconciliation_event(journal, payload)
    second = runner._append_reconciliation_event(journal, payload)

    assert first == second
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1
    assert runner._read_reconciliation_events(journal) == [first]
    journal.write_text(journal.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(runner.CleanEvalBlocked, match="BUDGET_RECONCILIATION_LOG_INTEGRITY_FAILURE"):
        runner._read_reconciliation_events(journal)


def test_formal_budget_is_separate_and_includes_all_sealed_formal_sh_history() -> None:
    import scripts.run_public_memory_clean_eval as runner

    ledger = runner._formal_budget_ledger_from_history(
        [
            {
                "run_id": "formal-sh-v1",
                "cell": "sh_32k",
                "status": "PROVIDER_COMPLETION_BELOW_90_PERCENT",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.004374,
            },
            {
                "run_id": "formal-sh-v2",
                "cell": "sh_32k",
                "status": "STOPPED",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.080115,
            },
            {
                "run_id": "formal-sh-v3",
                "cell": "sh_32k",
                "status": "STOPPED",
                "hard_cost_cap_usd": 6.0,
                "reserved_usd": 0.0,
                "estimated_spend_upper_bound_usd": 0.184356,
            },
        ]
    )

    assert ledger["budget_scope"] == "FORMAL_EVALUATION"
    assert ledger["hard_cap_usd"] == 30.0
    assert sum(row["estimated_spend_upper_bound_usd"] for row in ledger["runs"]) == pytest.approx(0.268845)
    assert "bc850e0cfe4745d79117660dd5a74367" not in {row["run_id"] for row in ledger["runs"]}
    assert runner._cost_is_below_cap(
        runner._ledger_reserved_total(ledger) + runner.CELLS["mh_6k"].hard_cost_cap_usd,
        30.0,
    )


def test_score_outcome_metrics_use_fixed_planned_denominator() -> None:
    import scripts.run_public_memory_clean_eval as runner

    metrics = runner._score_outcome_metrics(
        [
            {"score": True, "provider_outcome": "PROVIDER_COMPLETE"},
            {"score": False, "provider_outcome": "PROVIDER_COMPLETE"},
            {"score": None, "provider_outcome": "PROVIDER_ERROR"},
            {"score": None, "provider_outcome": "LOCAL_HARNESS_BLOCK"},
        ],
        planned_denominator=100,
    )

    assert metrics == {
        "correct": 1,
        "incorrect": 1,
        "not_evaluated": 98,
        "provider_failure": 1,
        "local_harness_block": 1,
        "scored_coverage": 0.02,
        "primary_accuracy": 0.01,
    }
