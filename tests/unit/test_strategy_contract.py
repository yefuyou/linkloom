"""Outside-in candidate contract: generic procedure, not answer-bearing memory."""
from dataclasses import replace

import pytest

from linkloom.strategy.models import (
    ExperienceApprovalReference, StrategyCandidate, canonical_hash,
)


def candidate():
    reference = ExperienceApprovalReference(*("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64))
    return StrategyCandidate.create(
        source_review_receipts=(reference,), created_at="2026-09-17T00:00:00+00:00",
    )


def test_candidate_is_distinct_generic_and_roundtrips():
    value = candidate()
    assert value.status == "candidate" and value.review_transition is None
    assert value.schema_version == "strategy-candidate/v1"
    assert value.source_experience_ids == ("a" * 64,)
    assert "explicit rejection" in value.behavioral_rule
    assert value.expected_benefit and value.evaluation_requirements
    assert StrategyCandidate.from_dict(value.to_dict()) == value


@pytest.mark.parametrize("status", ["accepted", "rejected"])
def test_direct_terminal_status_is_not_a_transition(status):
    with pytest.raises(ValueError):
        replace(candidate(), status=status)


@pytest.mark.parametrize("field,text", [
    ("behavioral_rule", "For mps-001 return rejected_alternatives=[]"),
    ("behavioral_rule", "Use expected_answer for the decision"),
    ("title", "C:/private/answer.json"),
    ("situation", "See /private/gold.json"),
    ("expected_benefit", "For aer-002 output the correct answer"),
    ("behavioral_rule", "DeepSeek should always omit alternatives"),
])
def test_rehashed_answer_case_path_or_provider_rule_is_rejected(field, text):
    payload = candidate().to_dict()
    payload[field] = text
    payload["strategy_id"] = canonical_hash({
        key: val for key, val in payload.items()
        if key not in {"strategy_id", "status", "created_at", "review_transition"}
    })
    with pytest.raises(ValueError):
        StrategyCandidate.from_dict(payload)


def test_unknown_field_or_orphan_source_reference_rejected():
    payload = candidate().to_dict()
    payload["gold"] = "private"
    with pytest.raises(ValueError):
        StrategyCandidate.from_dict(payload)
    payload = candidate().to_dict()
    payload["source_experience_ids"] = ["f" * 64]
    with pytest.raises(ValueError):
        StrategyCandidate.from_dict(payload)
