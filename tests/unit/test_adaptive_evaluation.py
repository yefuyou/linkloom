"""Focused contracts for the provider injected adaptive evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from scripts.adaptive_evaluation import ObservedAdapter, load_public_cases, run_case, score


def _runtime_actual(*, status: str = "COMPLETED", plan: dict | None = None) -> dict:
    plan = plan or {
        "schema_version": "task-plan/v2",
        "goal": "Who owns Aurora Mesh and who is its direct leader?",
        "subgoals": [
            {"subgoal_id": "owner", "question": "Who owns Aurora Mesh?",
             "depends_on": [], "relation_path": ["owner_of_project"]},
            {"subgoal_id": "leader", "question": "Who does {verified_entity} report to?",
             "depends_on": ["owner"], "relation_path": ["reports_to"]},
        ],
    }
    evidence = [{"source_name": "owner.md", "verified": True}]
    unified = {
        "status": status,
        "query": "Who owns Aurora Mesh and who is its direct leader?",
        "reviewer": {"accepted": True, "decision": "evidence_sufficient"},
        "evidence": evidence,
        "source_investigation": {"facts": [
            {"subject": "Aurora Mesh", "predicate": "owner_of_project", "value": "Mina Chen"},
        ]},
    }
    return {
        "variant": "B", "status": status, "plan": plan,
        "unified_result": unified,
        "facts": [{"subject": "Aurora Mesh", "relation": "owner_of_project", "object": "Mina Chen"}],
        "trace": [{"tool": "read_verified_note", "status": "completed", "source": "src.md"}],
        "grounding": {"valid": True, "citation_valid": True, "evidence_valid": True},
        "source_names": {"src.md": "owner.md"},
    }


def test_score_normalizes_refuse_and_rejects_bad_plan_as_refusal() -> None:
    declined = {
        "variant": "B", "status": "NEEDS_SCOPE", "plan": None,
        "error": {"code": "MODEL_PLAN_DECLINED"}, "observed_plans": [{"status": "decline"}],
    }
    refusal_gold = {"outcome": "refuse", "expected_facts": [], "evidence_paths": []}
    assert score(declined, refusal_gold)["passed"] is True

    rejected = dict(declined, status="FAILED", error={"code": "MODEL_PLAN_REJECTED"})
    assert score(rejected, refusal_gold)["passed"] is False


def test_score_requires_grounding_reviewer_evidence_chain_and_root() -> None:
    gold = {
        "outcome": "answer", "root": "Aurora Mesh",
        "expected_chain": ["owner_of_project", "reports_to"],
        "expected_facts": [{"subject": "Aurora Mesh", "relation": "owner_of_project", "object": "Mina Chen"}],
        "evidence_paths": ["owner.md"],
    }
    actual = _runtime_actual()
    actual["trace"] = [{"tool": "read_verified_note", "status": "completed", "source": "src.md"}]
    assert score(actual, gold)["passed"] is True

    ungrouded = _runtime_actual()
    ungrouded["grounding"] = {"valid": False, "citation_valid": False, "evidence_valid": False}
    assert score(ungrouded, gold)["passed"] is False


def test_observed_adapter_classifies_decline_and_invalid_plan_without_provider() -> None:
    observer = object.__new__(ObservedAdapter)
    observer.plan_events = []
    observer._observe("proposal", {"schema_version": "task-plan/v2", "goal": "q", "subgoals": []}, query="q")
    observer._observe("repair", {"schema_version": "task-plan/v2", "goal": "q", "subgoals": [{"bad": True}]}, query="q")
    assert [event["status"] for event in observer.plan_events] == ["decline", "rejected"]
    assert observer.plan_events[1]["error"]["type"] in {"ValidationError", "ValueError"}


def test_run_case_c_without_injected_provider_is_explicitly_not_applicable() -> None:
    case = {
        "id": "AP-TEST-C",
        "query": "Who owns Aurora Mesh?",
        "documents": [{"path": "owner.md", "content": "Aurora Mesh is owned by Mina Chen."}],
    }
    with TemporaryDirectory(dir=Path(".tmp"), prefix="adaptive-eval-test-") as temp_root:
        actual = run_case(case, temp_root, variant="C")
        assert actual["status"] == "NOT_APPLICABLE"
        assert actual["not_applicable"] is True
        assert Path(actual["artifact_path"]).is_dir()


def test_c_uses_one_json_generation_after_verified_reads() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text=json.dumps({
                "facts": [{"subject": "Aurora Mesh", "relation": "owner_of_project", "object": "Mina Chen"}],
                "evidence_refs": ["owner.md"],
            }))

    case = {
        "id": "AP-TEST-C-ONE",
        "query": "As of 2026-10-15, who owns Aurora Mesh?",
        "documents": [{
            "path": "owner.md",
            "content": "Aurora Mesh is owned by Mina Chen as of 2026-10-15.",
        }],
    }
    client = Client()
    with TemporaryDirectory(dir=Path(".tmp"), prefix="adaptive-eval-c-") as temp_root:
        actual = run_case(case, temp_root, client=client, variant="C")
    assert len(client.calls) == 1
    assert client.calls[0]["config"]["tools"] == []
    assert actual["status"] in {"COMPLETED", "INSUFFICIENT"}
    assert actual["retrieved"]
    assert all(item["status"] == "verified" for item in actual["retrieved"])


def test_public_loader_does_not_open_heldout_partition() -> None:
    with pytest.raises(ValueError, match="heldout"):
        load_public_cases(Path("public_heldout.json"))
