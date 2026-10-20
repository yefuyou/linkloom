"""Offline guards plus the opt-in DeepSeek business-case evaluation."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.smoke import deepseek_business_harness as harness


def test_business_case_registry_is_exact_bounded_and_gold_free() -> None:
    assert tuple(harness.CASES) == ("aer-002", "iti-005")
    assert harness.MODEL == "deepseek-flash"
    assert [case.max_model_turns for case in harness.CASES.values()] == [6, 4]
    assert [case.max_provider_requests for case in harness.CASES.values()] == [6, 4]

    serialized = json.dumps(
        [case.to_public_dict() for case in harness.CASES.values()],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "expected_" not in serialized
    assert "gold" not in serialized.lower()
    assert "What is the current evaluation rollout boundary" in serialized
    assert "Who is assigned to own the export checksum review" in serialized


def test_execution_functions_have_no_gold_or_expected_dependency() -> None:
    source = inspect.getsource(harness._run_case) + inspect.getsource(
        harness.run_business_evaluation
    )

    assert "expected_" not in source
    assert "load_gold" not in source
    assert "evaluate_sealed_case" not in source


def test_posthoc_evaluator_refuses_to_read_gold_before_observed_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"deepseek-evaluator-{uuid4().hex}"
    )
    root.mkdir(parents=True)
    dataset = root / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"case_id": "aer-002", "expected_decision": {}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "DATASET_PATH", dataset)

    with pytest.raises(harness.SmokeBlocked, match="SEALED_OBSERVED_RUN"):
        harness.evaluate_sealed_case(root, "aer-002")


def test_posthoc_evaluator_loads_gold_only_after_valid_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"deepseek-evaluator-{uuid4().hex}"
    )
    root.mkdir(parents=True)
    observed = {
        "case_id": "iti-005",
        "trajectory": [{"action": {"kind": "final"}}],
        "visible_evidence_refs": ["ev-1"],
        "team_decision_result": {
            "decision": {
                "value": None,
                "status": "insufficient_evidence",
                "evidence_refs": ["ev-1"],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "insufficient_evidence",
                "statement": "Assignment is not recorded.",
                "unknown_fields": ["owner", "deadline"],
                "evidence_refs": ["ev-1"],
            },
            "evidence_refs": ["ev-1"],
        },
        "runtime": {"status": "completed", "error": None},
    }
    observed_path = root / "observed_summary.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    (root / "observed_summary.seal.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "gold_available": False,
                "observed_summary_sha256": hashlib.sha256(
                    observed_path.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    dataset = root / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "iti-005",
                "expected_decision": {
                    "value": None,
                    "status": "insufficient_evidence",
                },
                "forbidden_claims": ["Mira Sol owns export retention."],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "DATASET_PATH", dataset)
    monkeypatch.setattr(
        harness.baseline,
        "FROZEN_DATASET_SHA256",
        hashlib.sha256(dataset.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        .upper(),
    )

    evaluation = harness.evaluate_sealed_case(root, "iti-005")

    assert evaluation["infrastructure"] == "PASS"
    assert evaluation["contract"] == "PASS"
    assert evaluation["grounding"] == "PASS"
    assert evaluation["decision"]["status_match"] is True
    assert evaluation["gold_accessed"] is True


def test_posthoc_evaluator_refuses_dataset_drift_after_observed_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"deepseek-evaluator-{uuid4().hex}"
    )
    root.mkdir(parents=True)
    observed_path = root / "observed_summary.json"
    observed_path.write_text(
        json.dumps({"case_id": "aer-002", "runtime": {"status": "failed"}}),
        encoding="utf-8",
    )
    (root / "observed_summary.seal.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "gold_available": False,
                "observed_summary_sha256": hashlib.sha256(
                    observed_path.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    dataset = root / "dataset.jsonl"
    dataset.write_text(
        json.dumps({"case_id": "aer-002", "expected_decision": {}}) + "\n",
        encoding="utf-8",
    )
    frozen_sha = (
        hashlib.sha256(dataset.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        .upper()
    )
    monkeypatch.setattr(harness, "DATASET_PATH", dataset)
    monkeypatch.setattr(harness.baseline, "FROZEN_DATASET_SHA256", frozen_sha)
    dataset.write_text(
        json.dumps({"case_id": "aer-002", "expected_decision": {"value": "drift"}})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(harness.SmokeBlocked, match="GOLDEN_DATASET_DRIFT"):
        harness.evaluate_sealed_case(root, "aer-002")


def test_posthoc_evaluator_marks_business_layers_not_evaluated_after_infra_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"deepseek-evaluator-{uuid4().hex}"
    )
    root.mkdir(parents=True)
    observed_path = root / "observed_summary.json"
    observed_path.write_text(
        json.dumps(
            {
                "case_id": "aer-002",
                "trajectory": [{"action": None, "status": "failed"}],
                "visible_evidence_refs": [],
                "team_decision_result": None,
                "runtime": {
                    "status": "failed",
                    "error": {
                        "code": "MODEL_RESPONSE_UNSUPPORTED",
                        "details": {"reason": "multiple_tool_calls"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "observed_summary.seal.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "gold_available": False,
                "observed_summary_sha256": hashlib.sha256(
                    observed_path.read_bytes()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    dataset = root / "dataset.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "aer-002",
                "expected_decision": {"status": "partial", "value": "rollout"},
                "forbidden_claims": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "DATASET_PATH", dataset)
    monkeypatch.setattr(
        harness.baseline,
        "FROZEN_DATASET_SHA256",
        hashlib.sha256(dataset.read_bytes().replace(b"\r\n", b"\n"))
        .hexdigest()
        .upper(),
    )

    evaluation = harness.evaluate_sealed_case(root, "aer-002")

    assert evaluation["infrastructure"] == "FAIL"
    assert evaluation["contract"] == "N/E"
    assert evaluation["grounding"] == "N/E"
    assert evaluation["decision"] == {
        "status": "N/E",
        "status_match": None,
        "value_exact_match": None,
    }
    assert evaluation["scope"]["status"] == "N/E"
    assert evaluation["uncertainty"]["status"] == "N/E"
    assert evaluation["overall"] == "BLOCKED_INFRASTRUCTURE"


def test_observed_bounds_reject_nonterminal_or_over_budget_results() -> None:
    case = harness.CASES["aer-002"]
    observed = {
        "runtime": {
            "status": "running",
            "provider_requests": case.max_provider_requests + 1,
            "model_turns": case.max_model_turns + 1,
        },
        "semantic_retry_count": 0,
        "usage": {"reported_reasoning_tokens": 0},
        "cost": {"estimated_peak_usd": "0.01", "hard_ceiling_usd": "0.10"},
        "guard": {"state": "WITHIN_BUDGET"},
    }

    with pytest.raises(harness.SmokeBlocked, match="NONTERMINAL_RESULT"):
        harness._assert_observed_bounds(observed, case)


def test_provider_failure_validator_accepts_durable_multi_tool_prefix_before_final_transient() -> None:
    case = harness.CASES["aer-002"]
    completed = SimpleNamespace(
        status="tool_results_durable",
        normalized_proposal={
            "kind": "tool_calls",
            "tool_calls": [{"provider_call_id": "a"}, {"provider_call_id": "b"}],
        },
        tool_result_refs=[{"ordinal": 0}, {"ordinal": 1}],
        provider_error=None,
        run_id="run-crossed",
    )
    failed = SimpleNamespace(
        status="failed",
        normalized_action=None,
        normalized_proposal=None,
        provider_error={
            "code": "MODEL_TRANSIENT_FAILURE",
            "outcome": "unknown_provider_outcome",
        },
        run_id="run-crossed",
    )
    state = SimpleNamespace(
        status="failed",
        termination=SimpleNamespace(status="failed"),
        model_executions=[completed, failed],
        tool_ledger=[SimpleNamespace(status="completed") for _ in range(2)],
        run_id="run-crossed",
        workflow="team_decision",
    )

    harness._assert_provider_failure_state(case, state)


@pytest.mark.skipif(
    os.environ.get(harness.OPT_IN_ENV) != "1",
    reason="DeepSeek business evaluation requires exact opt-in",
)
def test_real_deepseek_business_evaluation() -> None:
    run_root = harness.run_business_evaluation()

    for case_id in ("aer-002", "iti-005"):
        case_root = run_root / case_id
        assert (case_root / "observed_summary.seal.json").is_file()
        assert (case_root / "posthoc_evaluation.json").is_file()
