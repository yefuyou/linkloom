"""Focused contracts for the new Agent Recovery public/Gold scorer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "integration"))
sys.path.insert(0, str(ROOT / "src"))

from scripts.recovery_evaluation import (  # noqa: E402
    _classify_product_terminal,
    _seed_product_case,
    execute_offline_public,
    load_recovery_datasets,
    score_recovery_row,
)
from run_real_agent_reliability import _load_public  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "agent_recovery"


def test_acceptance_has_exactly_twelve_public_cases_and_separate_gold():
    public, gold, freeze = load_recovery_datasets(
        FIXTURES / "public_frozen.json", FIXTURES / "gold_frozen.json"
    )
    assert public["partition"] == "frozen"
    assert public["dataset_role"] == "acceptance"
    assert len(public["cases"]) == 12
    assert len(gold["cases"]) == 12
    assert freeze is not None
    assert all(case.get("stage") == "acceptance" for case in public["cases"])
    assert {case["id"] for case in public["cases"]} == {case["id"] for case in gold["cases"]}


def test_development_has_only_two_canaries_and_twenty_call_cap():
    public, gold, _ = load_recovery_datasets(
        FIXTURES / "public_dev.json", FIXTURES / "gold_dev.json"
    )
    assert public["partition"] == "dev"
    assert public["dataset_role"] == "development-canary"
    assert public["canary"] is True
    assert public["canary_budget"]["max_provider_requests"] <= 20
    assert len(public["cases"]) == len(gold["cases"]) == 2
    assert all(case.get("stage") == "development" for case in public["cases"])


def test_independent_first_canary_is_missing_evidence_and_same_file_multipassage():
    public, gold, freeze = load_recovery_datasets(
        FIXTURES / "public_canary.json", FIXTURES / "gold_canary.json",
        freeze_path=FIXTURES / "freeze_canary.json",
    )
    assert public["dataset_role"] == "development-canary"
    assert public["canary_budget"]["max_provider_requests"] <= 20
    assert {case["name"] for case in public["cases"]} == {
        "development missing evidence", "development same-file multi-passage"
    }
    assert len(gold["cases"]) == 2 and freeze is not None
    same = next(case for case in public["cases"] if "same-file" in case["name"])
    assert "\n\n" in same["documents"][0]["content"]


def test_public_cases_have_no_gold_like_fields_or_invalid_source_update_marker():
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    forbidden = {"expected", "gold", "facts", "required_sources", "refusal_class", "exact_facts", "rubric"}

    def keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not forbidden.intersection(keys(public))
    source_update = next(case for case in public["cases"] if case["name"] == "real sourceupdate beforequery")
    assert source_update["source_updates"][0]["applied_before_query"] is True
    assert all("valid" not in document or document["valid"] is not False for document in source_update["documents"])


def test_observer_contract_uses_actual_verified_read_linkage_for_all_baselines():
    expected = {
        "outcome": "complete",
        "facts": ["mira fern"],
        "reason": None,
        "required_sources": ["owner.md"],
        "requires_verified_read": True,
    }
    a = score_recovery_row({"selected": "A", "baselines": {"A": {
        "outcome": "complete", "reason": None, "facts": ["mira fern"], "sources": ["owner.md"],
        "citation_valid": True, "claim_supported": True,
        # A is deterministic and has no verified-read dependency.
        "verified_read_dependency": False,
    }}}, expected)
    assert a["pass"] is False
    assert a["observer_contract"]["values"]["verified_read_dependency"] is False

    c = score_recovery_row({"selected": "C", "baselines": {"C": {
        "outcome": "complete", "reason": None, "facts": ["mira fern"], "sources": ["owner.md"],
        "citation_valid": True, "claim_supported": True,
        # The selected Agent path must expose this observer explicitly.
    }}}, expected)
    assert c["pass"] is False
    assert c["observer_contract"]["values"]["verified_read_dependency"] == "not_observed"


def test_product_projection_observer_links_citations_to_verified_reads():
    expected = {
        "outcome": "complete",
        "facts": ["mira fern"],
        "reason": None,
        "required_sources": ["owner.md"],
        "requires_verified_read": True,
    }
    row = {"selected": "A", "result": {
        "outcome": "complete", "reason": None, "facts": ["mira fern"], "sources": ["owner.md"],
        "product_projection": {
            "evidence": [{"id": "citation-1", "ref": "ev-owner", "verified": True}],
            "source_investigation": {"facts": [{"value": "Mira Fern", "evidence_refs": ["ev-owner"]}]},
            "tool_calls": [{"tool": "read_verified_note", "status": "completed",
                             "result": {"value": {"evidence_id": "ev-owner", "status": "verified"}}}],
        },
    }}
    score = score_recovery_row(row, expected)
    assert score["pass"] is True
    assert score["observer_contract"]["values"] == {
        "citation_valid": True,
        "claim_supported": True,
        "verified_read_dependency": True,
    }


def test_source_update_execution_uses_repository_version_update_not_valid_false(tmp_path):
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    case = next(case for case in public["cases"] if case["name"] == "real sourceupdate beforequery")
    app, workspace_id, receipts = _seed_product_case(case, tmp_path / "source-update")
    try:
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt["old_source_version"] != receipt["new_source_version"]
        current = app.repository.get_source(workspace_id, receipt["source_id"], include_content=True)
        assert current is not None
        assert receipt["fixture_current_content_sha256"] == hashlib.sha256(
            case["documents"][0]["content"].encode("utf-8")
        ).hexdigest()
        assert receipt["product_current_content_sha256"] == current["content_sha256"]
        assert "Sol Kim" in current["content"]
    finally:
        app.close()


def test_frozen_execution_requires_explicit_allow_frozen(tmp_path):
    with pytest.raises(ValueError, match="allow-frozen"):
        execute_offline_public(
            FIXTURES / "public_frozen.json",
            output=tmp_path / "frozen.raw.json",
        )


def test_product_terminal_classification_preserves_budget_and_provider_unknown():
    budget = _classify_product_terminal({
        "kind": "error",
        "error": {"code": "MODEL_BUDGET_EXHAUSTED", "classification": "budget_exhausted"},
    })
    assert budget == ("runtime_failure", "budget_exhausted", "budget_exhausted")

    provider = _classify_product_terminal({
        "kind": "error",
        "error": {"code": "PROVIDER_UNKNOWN", "classification": "provider_outcome_uncertain"},
    })
    assert provider == ("provider_uncertain", "provider_unknown", "provider_outcome_uncertain")


def test_unsupported_refusal_and_runtime_failure_stay_distinct():
    base = {"facts": [], "sources": [], "citation_valid": False, "claim_supported": False, "verified_read_dependency": False}
    rows = [
        ("unsupported", "planning_failed"),
        ("refusal", "conflicting_evidence"),
        ("runtime_failure", "budget_exhausted"),
    ]
    for outcome, reason in rows:
        score = score_recovery_row(
            {"selected": "C", "baselines": {"C": {**base, "outcome": outcome, "reason": reason}}},
            {"outcome": outcome, "facts": [], "reason": reason, "required_sources": []},
        )
        assert score["outcome_match"] is True
        assert score["reason_match"] is True
        assert score["pass"] is True


def test_freeze_hashes_are_raw_bytes_and_cover_all_four_datasets():
    freeze = json.loads((FIXTURES / "freeze.json").read_text(encoding="utf-8"))
    for name in ("public_frozen.json", "gold_frozen.json", "public_dev.json", "gold_dev.json"):
        assert freeze["files"][name] == hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()


def test_existing_evaluation_evidence_cannot_be_overwritten(tmp_path):
    from scripts.recovery_evaluation import write_code_freeze_manifest
    output = tmp_path / 'existing-report.json'
    output.write_bytes(b'original evidence')
    with pytest.raises(FileExistsError):
        execute_offline_public(FIXTURES / 'public_dev.json', output=output)
    assert output.read_bytes() == b'original evidence'
    manifest = tmp_path / 'existing-manifest.json'
    manifest.write_bytes(b'original freeze')
    public = FIXTURES / 'public_frozen.json'
    with pytest.raises(FileExistsError):
        write_code_freeze_manifest(manifest, public_path=public, public_raw=public.read_bytes())
    assert manifest.read_bytes() == b'original freeze'


@pytest.mark.parametrize("case_name", ["historicalasof", "instruction same-line", "similarnames", "noisysamefile"])
def test_requested_edge_case_names_are_in_frozen_set(case_name):
    public, _ = _load_public(FIXTURES / "public_frozen.json")
    assert case_name in {case["name"] for case in public["cases"]}
