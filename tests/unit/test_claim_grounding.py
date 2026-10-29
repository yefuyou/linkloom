from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from linkloom.agents.claim_grounding import (
    ClaimSupportStatus,
    assess_claim_text,
    verified_dependency_values,
)
from linkloom.agents.team_decision import TeamDecisionResult, claim_proposal_schema
from linkloom.runtime.errors import ValidationError


def _evidence(evidence_id: str, quote: str) -> dict[str, object]:
    return {
        "evidence_id": evidence_id,
        "relative_path": "fixture.md",
        "content_sha256": "a" * 64,
        "quote": quote,
        "quote_sha256": "b" * 64,
        "line_start": 1,
        "line_end": 1,
        "status": "verified",
    }


def test_typed_alias_relation_is_visible_and_requires_exact_scoped_source_fact():
    definitions = {row["predicate"]: row for row in claim_proposal_schema()["x-linkloom-relation-definitions"]}
    assert definitions["alias_of_project"]["subject_type"] == "alias"
    assert definitions["alias_of_project"]["object_type"] == "project"
    quote = "Nimbus-Launch is an internal alias for the Cedar Harbor project."
    evidence = {"ev-alias": {**_evidence("ev-alias", quote), "workspace_id": "workspace-a"}}

    def proposal(subject="Nimbus-Launch", object_value="Cedar Harbor"):
        return json.dumps({"schema_version": "claim-proposal/v1", "claims": [{
            "subject": subject, "predicate": "alias_of_project", "object": object_value,
            "evidence_ids": ["ev-alias"], "quote": quote,
            "valid_on": None, "temporal_kind": None,
        }]})

    accepted = TeamDecisionResult.from_grounded_final(
        proposal(), ["ev-alias"], evidence_by_ref=evidence,
        expected_workspace_id="workspace-a", final_protocol="claim-proposal/v1",
    )
    accepted.validate_claim_support(evidence)
    assert accepted.decision["status"] == "approved"
    for subject, object_value in (("Cedar Harbor", "Nimbus-Launch"),
                                  ("Nimbus-Launch", "Other Harbor")):
        with pytest.raises(ValidationError):
            TeamDecisionResult.from_grounded_final(
                proposal(subject, object_value), ["ev-alias"], evidence_by_ref=evidence,
                expected_workspace_id="workspace-a", final_protocol="claim-proposal/v1",
            )
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            proposal(), ["ev-alias"], evidence_by_ref=evidence,
            expected_workspace_id="workspace-b", final_protocol="claim-proposal/v1",
        )


def test_supported_replacement_claim_requires_the_same_relation_and_entities() -> None:
    evidence = {
        "ev-successor": _evidence(
            "ev-successor",
            "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
        )
    }

    assessment = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-14",
        ["ev-successor"],
        evidence,
    )

    assert assessment.status is ClaimSupportStatus.SUPPORTED
    assert len(assessment.claims) == 1
    assert assessment.claims[0].relation == "superseded_by"


def test_timestamped_source_body_keeps_exact_replacement_direction_and_date() -> None:
    evidence = {"ev-message": _evidence(
        "ev-message",
        "[2025-02-14T12:00:00Z] Alice: Cobalt B replaced Meridian A on 2025-02-14 as the current Atlas rollout plan.",
    )}
    supported = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-14", ["ev-message"], evidence,
    )
    wrong_direction = assess_claim_text(
        "Cobalt B was replaced by Meridian A on 2025-02-14", ["ev-message"], evidence,
    )
    wrong_date = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-15", ["ev-message"], evidence,
    )
    malformed_header = {"ev-message": _evidence(
        "ev-message", "[not-a-date] Alice: Cobalt B replaced Meridian A on 2025-02-14.",
    )}
    assert supported.status is ClaimSupportStatus.SUPPORTED
    assert wrong_direction.status is not ClaimSupportStatus.SUPPORTED
    assert wrong_date.status is not ClaimSupportStatus.SUPPORTED
    assert assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-14", ["ev-message"], malformed_header,
    ).status is not ClaimSupportStatus.SUPPORTED


def test_valid_but_unrelated_evidence_does_not_support_a_fabricated_successor() -> None:
    evidence = {
        "ev-successor": _evidence(
            "ev-successor",
            "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
        ),
        "ev-checklist": _evidence(
            "ev-checklist",
            "Every rollout plan should identify a current owner before launch.",
        ),
    }

    assessment = assess_claim_text(
        "Phoenix Z replaced Meridian A",
        ["ev-successor", "ev-checklist"],
        evidence,
    )

    assert assessment.status is ClaimSupportStatus.CONTRADICTED
    assert assessment.claims[0].subject == "meridian a"
    assert assessment.claims[0].value == "phoenix z"


def test_supported_multi_hop_answer_uses_relation_specific_verified_lines() -> None:
    evidence = {
        "ev-successor": _evidence(
            "ev-successor",
            "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
        ),
        "ev-owner": _evidence(
            "ev-owner",
            "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B for Atlas.",
        ),
        "ev-leader": _evidence(
            "ev-leader",
            "As of 2025-02-16 Mira Chen reports directly to Kai Wu in the Atlas project.",
        ),
    }

    assessment = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-14; "
        "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B; "
        "As of 2025-02-16 Mira Chen reports directly to Kai Wu",
        ["ev-successor", "ev-owner", "ev-leader"],
        evidence,
    )

    assert assessment.status is ClaimSupportStatus.SUPPORTED
    assert [claim.relation for claim in assessment.claims] == [
        "superseded_by",
        "owner_of",
        "direct_leader_of",
    ]


def test_same_named_leader_from_other_project_cannot_complete_owner_chain() -> None:
    evidence = {
        "ev-owner": _evidence(
            "ev-owner", "Mira Chen is the accountable owner of Cobalt B for Atlas.",
        ),
        "ev-foreign-leader": _evidence(
            "ev-foreign-leader",
            "Mira Chen reports directly to Nia Vale in the Harbor project.",
        ),
        "ev-atlas-leader": _evidence(
            "ev-atlas-leader",
            "Mira Chen reports directly to Kai Wu in the Atlas project.",
        ),
    }
    claim = (
        "Mira Chen is the accountable owner of Cobalt B; "
        "Mira Chen reports directly to Nia Vale"
    )

    wrong = assess_claim_text(
        claim, ["ev-owner", "ev-foreign-leader"], evidence,
    )
    correct = assess_claim_text(
        claim.replace("Nia Vale", "Kai Wu"),
        ["ev-owner", "ev-atlas-leader"], evidence,
    )

    assert wrong.status is ClaimSupportStatus.INSUFFICIENT
    assert correct.status is ClaimSupportStatus.SUPPORTED


def test_unparseable_claim_is_unverifiable_instead_of_lexically_accepted() -> None:
    evidence = {
        "ev-successor": _evidence(
            "ev-successor",
            "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
        )
    }

    assessment = assess_claim_text(
        "The evidence clearly proves Phoenix Z is the only sensible choice",
        ["ev-successor"],
        evidence,
    )

    assert assessment.status is ClaimSupportStatus.UNVERIFIABLE
    assert assessment.claims == ()


def test_claims_cannot_read_evidence_outside_their_own_citations() -> None:
    evidence = {
        "ev-successor": _evidence(
            "ev-successor",
            "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
        ),
        "ev-checklist": _evidence(
            "ev-checklist",
            "Every rollout plan should identify a current owner before launch.",
        ),
    }

    assessment = assess_claim_text(
        "Meridian A was replaced by Cobalt B",
        ["ev-checklist"],
        evidence,
    )

    assert assessment.status is ClaimSupportStatus.INSUFFICIENT


def test_leader_hop_binds_owner_value_even_when_query_mentions_replacement():
    owner = _evidence(
        "ev-owner",
        "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B for Atlas.",
    )

    assert verified_dependency_values(
        "Who owns the verified replacement plan?",
        "Who is the direct leader of the verified plan owner?",
        [owner],
        expected_parent_entity="Cobalt B",
    ) == ("mira chen",)


def test_relation_mismatch_and_invalid_reference_do_not_count_as_support():
    evidence = {"ev-source": _evidence(
        "ev-source", "Cobalt B replaced Meridian A on 2025-02-14."
    )}

    wrong_relation = assess_claim_text(
        "Cobalt B aliases Meridian A", ["ev-source"], evidence
    )
    invalid_ref = assess_claim_text(
        "Meridian A was replaced by Cobalt B", ["ev-missing"], evidence
    )

    assert wrong_relation.status is ClaimSupportStatus.INSUFFICIENT
    assert invalid_ref.status is ClaimSupportStatus.INSUFFICIENT


def test_temporal_mismatch_and_conflicting_evidence_are_not_silently_supported():
    evidence = {
        "ev-current": _evidence(
            "ev-current", "Cobalt B replaced Meridian A on 2025-02-14."
        ),
        "ev-conflict": _evidence(
            "ev-conflict", "Meridian A was not replaced by Cobalt B."
        ),
    }

    wrong_date = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-15",
        ["ev-current"], evidence,
    )
    conflict = assess_claim_text(
        "Meridian A was replaced by Cobalt B",
        ["ev-current", "ev-conflict"], evidence,
    )

    assert wrong_date.status is ClaimSupportStatus.CONTRADICTED
    assert conflict.status is ClaimSupportStatus.CONTRADICTED

    invalid_date = assess_claim_text(
        "Meridian A was replaced by Cobalt B on 2025-02-30",
        ["ev-current"], evidence,
    )
    assert invalid_date.status is ClaimSupportStatus.UNVERIFIABLE


def test_owner_validity_dates_are_exact_and_later_values_do_not_conflict_with_history():
    evidence = {
        "ev-old-owner": _evidence(
            "ev-old-owner",
            "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B.",
        ),
        "ev-new-owner": _evidence(
            "ev-new-owner",
            "As of 2025-06-15 Leo Park is the accountable owner of Cobalt B.",
        ),
    }

    historical = assess_claim_text(
        "As of 2025-02-15 Mira Chen is the accountable owner of Cobalt B",
        ["ev-old-owner", "ev-new-owner"],
        evidence,
    )
    unqualified = assess_claim_text(
        "Mira Chen is the accountable owner of Cobalt B",
        ["ev-old-owner"],
        evidence,
    )

    assert historical.status is ClaimSupportStatus.SUPPORTED
    assert unqualified.status is ClaimSupportStatus.INSUFFICIENT


def test_wrong_owner_and_partially_supported_atomic_claims_fail_closed():
    evidence = {
        "ev-owner": _evidence(
            "ev-owner", "Mira Chen is the accountable owner of Cobalt B."
        ),
        "ev-replacement": _evidence(
            "ev-replacement", "Cobalt B replaced Meridian A."
        ),
    }

    wrong_owner = assess_claim_text(
        "Leo Park is the accountable owner of Cobalt B",
        ["ev-owner"], evidence,
    )
    partial = assess_claim_text(
        "Meridian A was replaced by Cobalt B; Mira Chen reports directly to Kai Wu",
        ["ev-replacement"], evidence,
    )

    assert wrong_owner.status is ClaimSupportStatus.CONTRADICTED
    assert partial.status is ClaimSupportStatus.INSUFFICIENT
    assert [claim.status for claim in partial.claims] == [
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.INSUFFICIENT,
    ]


def test_bare_supplier_answer_is_bound_to_question_and_verified_selection_relation():
    evidence = {
        "ev-supplier": _evidence(
            "ev-supplier", "We selected Acme as the supplier for the Atlas project."
        )
    }

    supported = assess_claim_text(
        "Acme",
        ["ev-supplier"],
        evidence,
        question="Which supplier was selected for the Atlas project?",
    )
    unsupported = assess_claim_text(
        "Acme",
        ["ev-mention"],
        {
            "ev-mention": _evidence(
                "ev-mention", "Acme submitted a proposal for the Atlas project."
            )
        },
        question="Which supplier was selected for the Atlas project?",
    )

    assert supported.status is ClaimSupportStatus.SUPPORTED
    assert supported.claims[0].relation == "selected_supplier_for"
    assert unsupported.status is ClaimSupportStatus.INSUFFICIENT


def test_approved_model_provider_and_bounded_approval_rationale_are_supported():
    evidence = {
        "ev-provider": _evidence(
            "ev-provider",
            "The Atlas Lantern team selected Aster A as the approved model provider "
            "for the synthetic pilot and its next integration slice.",
        )
    }

    decision = assess_claim_text(
        "Aster A is the approved model provider for the Atlas Lantern pilot.",
        ["ev-provider"],
        evidence,
    )
    rationale = assess_claim_text(
        "The observed decision record establishes the approval.",
        ["ev-provider"],
        evidence,
    )
    fabricated = assess_claim_text(
        "Borealis B is the approved model provider for the Atlas Lantern pilot.",
        ["ev-provider"],
        evidence,
    )

    assert decision.status is ClaimSupportStatus.SUPPORTED
    assert rationale.status is ClaimSupportStatus.SUPPORTED
    assert fabricated.status is ClaimSupportStatus.CONTRADICTED


def test_approved_plan_hop_is_bound_to_project_in_parent_question():
    evidence = [
        _evidence("ev-atlas", "The Atlas project approved Cedar Plan for its rollout."),
        _evidence(
            "ev-other-project",
            "The Borealis project approved Harbor Plan for its rollout.",
        ),
    ]

    atlas_target = verified_dependency_values(
        "Which plan did the Atlas project approve for its rollout?",
        "Which plan replaced the verified plan?",
        evidence,
    )
    wrong_project = verified_dependency_values(
        "Which plan did the Cypress project approve for its rollout?",
        "Which plan replaced the verified plan?",
        evidence,
    )

    assert atlas_target == ("cedar plan",)
    assert wrong_project == ()


@pytest.fixture
def verified_claim_source(tmp_path: Path):
    """Generate a real scanned source; no archived run or placeholder hash."""
    from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
    from linkloom.scanner import scan_vault

    vault = tmp_path / "vault"
    vault.mkdir()
    quote = "Mira Chen reports directly to Kai Wu."
    (vault / "reporting.md").write_text(quote + "\n", encoding="utf-8")
    adapter = RuntimeAgentAdapter(
        vault, scan_vault(vault, tmp_path / "index").index_path,
        retrieval_mode="bm25", decision_memory_enabled=False,
    )
    passages = adapter._search_notes("Mira Chen reports directly", {}, 5)
    assert len(passages) == 1
    evidence = passages[0]
    assert evidence["quote"] == quote
    assert evidence["quote_sha256"] == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    assert evidence["content_sha256"] == hashlib.sha256(
        (vault / "reporting.md").read_bytes()
    ).hexdigest()
    assert adapter._read_verified_note_for_tool(evidence["evidence_id"]) == evidence
    return adapter, evidence


@pytest.mark.parametrize(
    "tampered_quote",
    [
        "Mira Chen reports directly to Kai Wuu.",
        "Mira Chen reports directly to Kai Wu",
        "Mira Chen reports directly to Kai Xu.",
    ],
    ids=["one_character_added", "one_character_removed", "one_character_replaced"],
)
def test_verified_claim_source_rejects_tampered_quote(verified_claim_source, tampered_quote):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    # Keep the observed ID and hashes: changing even punctuation is tampering.
    adapter._evidence_by_id[ref] = {**evidence, "quote": tampered_quote}

    with pytest.raises(ValueError, match="no longer matches its source version"):
        adapter._read_verified_note_for_tool(ref)


def test_verified_claim_source_rejects_tampered_source_hash(verified_claim_source):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    digest = evidence["content_sha256"]
    tampered_hash = ("0" if digest[0] != "0" else "1") + digest[1:]
    adapter._evidence_by_id[ref] = {**evidence, "content_sha256": tampered_hash}

    with pytest.raises(ValueError, match="no longer matches its source version"):
        adapter._read_verified_note_for_tool(ref)


@pytest.mark.parametrize("protocol", ["team-decision-result/v1", "claim-proposal/v1"])
def test_grounded_final_rejects_valid_but_unobserved_evidence(verified_claim_source, protocol):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    if protocol == "claim-proposal/v1":
        payload = {
            "schema_version": protocol,
            "claims": [{
                "subject": "Mira Chen", "predicate": "reports_to", "object": "Kai Wu",
                "evidence_ids": [ref], "quote": evidence["quote"],
                "valid_on": None, "temporal_kind": None,
            }],
        }
    else:
        payload = {
            "schema_version": protocol,
            "decision": {"value": evidence["quote"], "status": "approved", "evidence_refs": [ref]},
            "rationale": [], "rejected_alternatives": [], "actions": [], "unresolved_items": [],
            "uncertainty": {
                "status": "none", "statement": None, "unknown_fields": [], "evidence_refs": [],
            },
            "evidence_refs": [ref],
        }
    text = json.dumps(payload)
    kwargs = dict(
        evidence_by_ref={ref: evidence}, expected_workspace_id=adapter.workspace_id,
        final_protocol=protocol, require_read_evidence=True, read_evidence_ids=[ref],
    )
    accepted = TeamDecisionResult.from_grounded_final(text, [ref], **kwargs)
    accepted.validate_claim_support({ref: evidence})
    assert accepted.decision["status"] == "approved"

    # All source facts remain valid; only the model's observed allowlist changes.
    with pytest.raises(ValidationError, match="not observed by the model"):
        TeamDecisionResult.from_grounded_final(text, [], **kwargs)
