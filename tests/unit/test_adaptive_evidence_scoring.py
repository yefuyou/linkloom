"""Regression tests for conservative adaptive-planning evidence scoring.

These tests intentionally exercise the public scoring boundary with synthetic
results.  They do not invoke a provider, read a heldout partition, or depend
on a live runtime artifact.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scripts.adaptive_evaluation import _ground_c_facts, score


def _valid_plan() -> dict[str, Any]:
    return {
        "schema_version": "task-plan/v2",
        "goal": "Who owns Aurora Mesh and who is their direct leader?",
        "subgoals": [
            {
                "subgoal_id": "owner",
                "question": "Who owns Aurora Mesh?",
                "depends_on": [],
                "relation_path": ["owner_of_project"],
            },
            {
                "subgoal_id": "leader",
                "question": "Who is the direct leader of {verified_entity}?",
                "depends_on": ["owner"],
                "relation_path": ["reports_to"],
            },
        ],
    }


def _answer_gold() -> dict[str, Any]:
    return {
        "outcome": "answer",
        "root": "Aurora Mesh",
        "expected_chain": ["owner_of_project", "reports_to"],
        "expected_facts": [
            {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Mina Chen",
            },
            {
                "subject": "Mina Chen",
                "relation": "reports_to",
                "object": "Owen Park",
            },
        ],
        "evidence_paths": ["owner.md"],
    }


def _runtime_actual(
    *,
    status: str = "COMPLETED",
    plan: dict[str, Any] | None = None,
    facts: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    actual_facts = facts or [
        {
            "subject": "Aurora Mesh",
            "relation": "owner_of_project",
            "object": "Mina Chen",
        },
        {
            "subject": "Mina Chen",
            "relation": "reports_to",
            "object": "Owen Park",
        },
    ]
    return {
        "variant": "B",
        "status": status,
        "plan": deepcopy(plan if plan is not None else _valid_plan()),
        "unified_result": {
            "status": status,
            "reviewer": {"accepted": True, "decision": "evidence_sufficient"},
            "evidence": [{"source_name": "owner.md", "verified": True}],
            "source_investigation": {
                "facts": [
                    {
                        "subject": item["subject"],
                        "predicate": item["relation"],
                        "value": item["object"],
                    }
                    for item in actual_facts
                ]
            },
        },
        "facts": deepcopy(actual_facts),
        "trace": [
            {
                "tool": "read_verified_note",
                "status": "completed",
                "source": "src_owner.md",
            }
        ],
        "grounding": {
            "valid": True,
            "citation_valid": True,
            "evidence_valid": True,
        },
        "source_names": {"src_owner.md": "owner.md"},
    }


def test_score_rejects_plan_missing_required_fields_even_when_facts_are_grounded() -> None:
    invalid_plan = {
        # It has enough relation_path data to fool a presence-only scorer, but
        # omits the task-plan schema and subgoal identity/dependency fields.
        "subgoals": [
            {"question": "Who owns Aurora Mesh?", "relation_path": ["owner_of_project"]},
            {"question": "nonsense", "relation_path": ["reports_to"]},
        ]
    }

    result = score(_runtime_actual(plan=invalid_plan), _answer_gold())

    assert result["plan_valid"] is False
    assert result["passed"] is False


def test_score_rejects_extra_unverified_fact_for_insufficient_outcome() -> None:
    gold = {
        "outcome": "insufficient",
        "root": "Aurora Mesh",
        "expected_chain": ["owner_of_project", "reports_to"],
        "expected_facts": [
            {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Mina Chen",
            }
        ],
        "evidence_paths": ["owner.md"],
    }
    actual = _runtime_actual(
        status="INSUFFICIENT",
        facts=[
            {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Mina Chen",
            },
            {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Hallucinated Owner",
            },
        ],
    )

    result = score(actual, gold)

    assert result["fact_exact"] is False
    assert result["passed"] is False


def test_c_source_path_resolves_to_the_exact_verified_read_id_for_grounding() -> None:
    retrieved = [
        {
            "path": "owner.md",
            "content": (
                "[2026-10-15T00:00:00Z] Synthetic: "
                "Mina Chen is the accountable owner of Aurora Mesh."
            ),
            "evidence_id": "ev_v2_owner",
            "status": "verified",
            "line_start": 1,
            "line_end": 1,
            "content_sha256": "content-sha",
            "quote_sha256": "quote-sha",
            "source_version": "artifact-v1",
            "workspace_id": "adaptive-eval-c",
        }
    ]

    grounding = _ground_c_facts(
        [
            {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Mina Chen",
            }
        ],
        ["owner.md"],
        retrieved,
        "Who owns Aurora Mesh?",
    )

    assert grounding["citation_valid"] is True
    assert grounding["valid"] is True
    assert grounding["checks"] == [
        {
            "fact": {
                "subject": "Aurora Mesh",
                "relation": "owner_of_project",
                "object": "Mina Chen",
            },
            "status": "SUPPORTED",
            "valid": True,
        }
    ]
