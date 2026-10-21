import json

import pytest

from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.runtime.errors import ValidationError


def _evidence(ref, quote, *, workspace="ws-a"):
    return {
        "evidence_id": ref,
        "relative_path": f"{ref}.md",
        "content_sha256": "a" * 64,
        "quote": quote,
        "quote_sha256": "b" * 64,
        "line_start": 1,
        "line_end": 1,
        "status": "verified",
        "workspace_id": workspace,
    }


def _final(value, refs, *, status="approved", rationale=()):
    return json.dumps({
        "schema_version": "team-decision-result/v1",
        "decision": {"value": value, "status": status, "evidence_refs": list(refs)},
        "rationale": [
            {"point": point, "evidence_refs": list(item_refs)}
            for point, item_refs in rationale
        ],
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": [],
        "uncertainty": {
            "status": "none" if status == "approved" else "insufficient_evidence",
            "statement": None,
            "unknown_fields": [] if status == "approved" else ["owner"],
            "evidence_refs": [],
        },
        "evidence_refs": list(dict.fromkeys([*refs, *(ref for _, refs in rationale for ref in refs)])),
    })


def test_same_scope_read_counterevidence_cannot_be_hidden_by_final_refs():
    positive = _evidence("positive", "Mira Chen is the accountable owner of Lantern Plan.")
    negative = _evidence("negative", "Mira Chen is not the accountable owner of Lantern Plan.")
    evidence = {"positive": positive, "negative": negative}
    result = TeamDecisionResult.from_grounded_final(
        _final("Mira Chen is the accountable owner of Lantern Plan.", ["positive"]),
        ["positive"], evidence_by_ref={"positive": positive},
        expected_workspace_id="ws-a", require_read_evidence=True,
        read_evidence_ids=["positive", "negative"],
    )
    with pytest.raises(ValidationError, match="conflict|support"):
        result.validate_claim_support(
            {"positive": positive},
            observed_evidence_by_ref=evidence,
        )


def test_source_claim_requires_successful_read_but_search_to_read_remains_valid():
    source = _evidence("source", "We selected Acme as the supplier for the Atlas project.")
    final = _final("Acme", ["source"], rationale=(("The observed note names Acme.", ["source"]),))
    with pytest.raises(ValidationError, match="successful read"):
        TeamDecisionResult.from_grounded_final(
            final, ["source"], evidence_by_ref={"source": source},
            expected_workspace_id="ws-a", require_read_evidence=True,
            read_evidence_ids=[],
        )
    accepted = TeamDecisionResult.from_grounded_final(
        final, ["source"], evidence_by_ref={"source": source},
        expected_workspace_id="ws-a", require_read_evidence=True,
        read_evidence_ids=["source"],
    )
    accepted.validate_claim_support({"source": source}, observed_evidence_by_ref={"source": source},
                                    question="Which supplier was selected for the Atlas project?")


def test_different_valid_dates_do_not_create_global_conflict():
    old = _evidence("old", "On 2025-01-01 Mira Chen is the accountable owner of Lantern Plan.")
    new = _evidence("new", "On 2025-02-01 Jordan Lee is the accountable owner of Lantern Plan.")
    final = _final("On 2025-01-01 Mira Chen is the accountable owner of Lantern Plan.", ["old"])
    result = TeamDecisionResult.from_grounded_final(
        final, ["old"], evidence_by_ref={"old": old}, expected_workspace_id="ws-a",
        require_read_evidence=True, read_evidence_ids=["old", "new"],
    )
    result.validate_claim_support({"old": old}, observed_evidence_by_ref={"old": old, "new": new})


def test_evidence_free_abstention_is_allowed():
    final = _final(None, [], status="insufficient_evidence")
    result = TeamDecisionResult.from_grounded_final(
        final, [], evidence_by_ref={}, expected_workspace_id="ws-a",
        require_read_evidence=True, read_evidence_ids=[],
    )
    result.validate_claim_support({}, observed_evidence_by_ref={})


@pytest.mark.parametrize(("claim", "other"), [
    ("Mira Chen is the accountable owner of Lantern Plan.",
     "Jordan Lee is the accountable owner of Lantern Plan for Beacon."),
    ("Mira Chen is the accountable owner of Lantern Plan for Atlas.",
     "Jordan Lee is the accountable owner of Lantern Plan for Beacon."),
    ("Meridian A was replaced by Cobalt B on 2025-02-14.",
     "Cobalt B replaced Meridian A on 2025-03-14."),
])
def test_unrelated_scope_and_other_event_date_do_not_block_read_fact(claim, other):
    cited = _evidence("cited", claim)
    unrelated = _evidence("other", other)
    result = TeamDecisionResult.from_grounded_final(
        _final(claim, ["cited"]), ["cited"], evidence_by_ref={"cited": cited},
        expected_workspace_id="ws-a", require_read_evidence=True,
        read_evidence_ids=["cited", "other"],
    )
    result.validate_claim_support(
        {"cited": cited}, observed_evidence_by_ref={"cited": cited, "other": unrelated},
    )


def test_legitimate_partial_keeps_its_supported_fact():
    quote = "Mira Chen is the accountable owner of Lantern Plan."
    evidence = {"read": _evidence("read", quote)}
    result = TeamDecisionResult.from_grounded_final(
        _final(quote, ["read"], status="partial"), ["read"],
        evidence_by_ref=evidence, expected_workspace_id="ws-a",
        require_read_evidence=True, read_evidence_ids=["read"],
    )
    result.validate_claim_support(evidence, observed_evidence_by_ref=evidence)
    assert result.decision["status"] == "partial"


def _typed(evidence, *, incomplete=False):
    payload = {"schema_version": "claim-proposal/v1", "claims": [{
        "subject": "Lantern Plan", "predicate": "owner_of_project", "object": "Mira Chen",
        "evidence_ids": ["positive"], "quote": evidence["positive"]["quote"],
        "valid_on": None, "temporal_kind": None,
    }]}
    if incomplete:
        payload["uncertainty"] = {"status": "conflicting_evidence", "unknown_fields": ["owner"]}
    return json.dumps(payload)


def test_typed_source_final_cannot_bypass_read_or_omit_known_conflict():
    evidence = {
        "positive": _evidence("positive", "Mira Chen is the accountable owner of Lantern Plan."),
        "negative": _evidence("negative", "Mira Chen is not the accountable owner of Lantern Plan."),
    }
    kwargs = dict(evidence_by_ref=evidence, expected_workspace_id="ws-a",
                  final_protocol="claim-proposal/v1", require_read_evidence=True)
    with pytest.raises(ValidationError, match="successful read"):
        TeamDecisionResult.from_grounded_final(_typed(evidence), list(evidence),
                                              read_evidence_ids=[], **kwargs)
    result = TeamDecisionResult.from_grounded_final(
        _typed(evidence), list(evidence), read_evidence_ids=list(evidence), **kwargs,
    )
    with pytest.raises(ValidationError):
        result.validate_claim_support(evidence, observed_evidence_by_ref=evidence)
    uncertain = TeamDecisionResult.from_grounded_final(
        _typed(evidence, incomplete=True), list(evidence), read_evidence_ids=list(evidence), **kwargs,
    )
    uncertain.validate_claim_support(evidence, observed_evidence_by_ref=evidence)
    assert uncertain.decision["value"] is None
    assert uncertain.uncertainty["status"] == "conflicting_evidence"


def test_successful_read_cannot_authorize_another_evidence_id():
    quote = "Mira Chen is the accountable owner of Lantern Plan."
    with pytest.raises(ValidationError, match="successful read"):
        TeamDecisionResult.from_grounded_final(
            _final(quote, ["search"]), ["search"],
            evidence_by_ref={"search": _evidence("search", quote)},
            expected_workspace_id="ws-a", require_read_evidence=True,
            read_evidence_ids=["different-read"],
        )


def test_typed_quote_truncation_cannot_discard_original_project_scope():
    evidence = {
        "positive": _evidence("positive", "Mira Chen is the accountable owner of Lantern Plan for Atlas."),
        "counter": _evidence("counter", "Jordan Lee is the accountable owner of Lantern Plan for Atlas."),
    }
    payload = json.loads(_typed(evidence))
    payload["claims"][0]["quote"] = "Mira Chen is the accountable owner of Lantern Plan"
    result = TeamDecisionResult.from_grounded_final(
        json.dumps(payload), list(evidence), evidence_by_ref=evidence,
        expected_workspace_id="ws-a", final_protocol="claim-proposal/v1",
        require_read_evidence=True, read_evidence_ids=list(evidence),
    )
    with pytest.raises(ValidationError):
        result.validate_claim_support(evidence, observed_evidence_by_ref=evidence)
