"""Contract checks for the new Gold-separated offline reliability evaluator."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "src"))

from run_real_agent_reliability import (  # noqa: E402
    _case_documents,
    _load_gold,
    _load_public,
    _score_row,
)


FIXTURES = ROOT / "tests" / "fixtures" / "real_agent_reliability"


def test_public_and_gold_are_separate_and_case_counts_are_in_range():
    public, _ = _load_public(FIXTURES / "public_dev.json")
    gold, _ = _load_gold(
        FIXTURES / "gold_dev.json",
        expected_partition="dev",
        case_ids={row["id"] for row in public["cases"]},
    )
    assert 8 <= len(public["cases"]) <= 36
    assert len(public["cases"]) == len(gold["cases"])
    assert all("expected" not in row for row in public["cases"])


def test_new_case_total_is_within_spec_range():
    dev, _ = _load_public(FIXTURES / "public_dev.json")
    frozen, _ = _load_public(FIXTURES / "public_frozen.json")
    assert 24 <= len(dev["cases"]) + len(frozen["cases"]) <= 36


def test_frozen_union_covers_requested_a_to_p_categories_without_running_it():
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    categories = {
        category
        for row in public["cases"]
        for category in row.get("category", [])
        if category in set("ABCDEFGHIJKLMNOP")
    }
    assert categories == set("ABCDEFGHIJKLMNOP")


def test_public_loader_rejects_gold_like_fields(tmp_path):
    path = tmp_path / "public.json"
    path.write_text(json.dumps({
        "schema_version": "real-agent-reliability-public/v1",
        "partition": "dev",
        "cases": [{"id": "x", "expected": {}}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="Gold-like"):
        _load_public(path)


@pytest.mark.parametrize(
    ("actual", "expected", "passed"),
    [
        ({"outcome": "unsupported", "reason": "planning_failed", "facts": [], "sources": []},
         {"outcome": "unsupported", "reason": "planning_failed", "facts": [], "required_sources": []}, True),
        ({"outcome": "refusal", "reason": "conflicting_evidence", "facts": [], "sources": []},
         {"outcome": "refusal", "reason": "conflicting_evidence", "facts": [], "required_sources": []}, True),
        ({"outcome": "runtime_failure", "reason": "budget_exhausted", "facts": [], "sources": []},
         {"outcome": "runtime_failure", "reason": "budget_exhausted", "facts": [], "required_sources": []}, True),
    ],
)
def test_score_keeps_unsupported_refusal_and_runtime_failure_distinct(actual, expected, passed):
    row = {"selected": "C", "baselines": {"C": {
        **actual,
        "citation_valid": False,
        "claim_supported": False,
        "verified_read_dependency": False,
    }}}
    score = _score_row(row, expected)
    assert score["outcome_match"] is passed
    assert score["reason_match"] is passed


def test_frozen_gold_matches_frozen_public_ids_without_execution():
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    gold, _ = _load_gold(
        FIXTURES / "gold_frozen.json",
        expected_partition="frozen",
        case_ids={row["id"] for row in public["cases"]},
    )
    assert len(gold["cases"]) == 16


def test_runtime_fixture_documents_exclude_invalid_and_foreign_workspace_sources():
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    case = next(row for row in public["cases"] if row["id"] == "RAR-F-L")
    documents = _case_documents(case)
    assert [row["path"] for row in documents] == ["l-owner.md"]
