from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from linkloom.agents.claim_grounding import (
    ClaimSupportStatus,
    assess_claim_text,
    verified_dependency_values,
)
from linkloom.agents.team_decision import TeamDecisionResult, claim_proposal_schema
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.task_plan import _passage_identity


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


@pytest.mark.parametrize("side", ["before", "after"])
def test_phase9_real_cn2_final_replays_against_exact_verified_passages(side):
    run = (Path(__file__).resolve().parents[2] / "work" / "phase9_gemini"
           / "runs" / f"p9-p0-cn2-{side}")
    observed = json.loads((run / "observed.json").read_text(encoding="utf-8"))
    evidence = {}
    for path in (run / "checkpoints" / "models" / "model").glob(
        "*/*/tool-results/*.json"
    ):
        result = json.loads(path.read_text(encoding="utf-8"))["model_tool_result"]["result"]
        if result["tool_id"] == "read_verified_note" and result["status"] == "ok":
            value = result["value"]
            evidence[value["evidence_id"]] = value
    assert {item["relative_path"] for item in evidence.values()} == {
        "phase5_cn/owner.md", "phase5_cn/leader.md",
    }
    workspace = next(iter(evidence.values()))["workspace_id"]
    assert all(_passage_identity(value, workspace) for value in evidence.values())

    decision = TeamDecisionResult.from_grounded_final(
        observed["final_raw"], list(evidence), evidence_by_ref=evidence,
    )
    decision.validate_claim_support(evidence)

    raw = json.loads(observed["final_raw"])
    for wrong in ("王芳报告给李明", "李明报告给张伟"):
        changed = json.loads(observed["final_raw"])
        changed["decision"]["value"] = raw["decision"]["value"].split(";")[0] + "; " + wrong
        invalid = TeamDecisionResult.from_grounded_final(
            json.dumps(changed, ensure_ascii=False), list(evidence),
        )
        with pytest.raises(ValidationError):
            invalid.validate_claim_support(evidence)
    unrelated = {ref: value for ref, value in evidence.items()
                 if value["relative_path"] == "phase5_cn/owner.md"}
    with pytest.raises(ValidationError):
        decision.validate_claim_support(unrelated)
    foreign = dict(next(iter(evidence.values())), workspace_id="foreign-workspace")
    assert _passage_identity(foreign, workspace) is None


def test_phase11_real_cn2_punctuated_final_replays_without_weakening_binding():
    run = (Path(__file__).resolve().parents[2] / "work" / "phase11" /
           "runs" / "p11-cn2-repeats-rep1")
    observed = json.loads((run / "observed.json").read_text(encoding="utf-8"))
    evidence = {}
    for path in (run / "checkpoints" / "models" / "model").glob(
        "*/*/tool-results/*.json"
    ):
        result = json.loads(path.read_text(encoding="utf-8"))["model_tool_result"]["result"]
        if result["tool_id"] == "read_verified_note" and result["status"] == "ok":
            value = result["value"]
            evidence[value["evidence_id"]] = value
    assert {value["relative_path"] for value in evidence.values()} == {
        "phase5_cn/owner.md", "phase5_cn/leader.md",
    }
    raw = json.loads(observed["final_raw"])
    decision = TeamDecisionResult.from_grounded_final(
        observed["final_raw"], list(evidence), evidence_by_ref=evidence,
    )
    decision.validate_claim_support(evidence)
    for paraphrase in ("李明直接向王芳汇报。", "李明向王芳直接汇报。"):
        changed = json.loads(observed["final_raw"])
        changed["decision"]["value"] = (
            raw["decision"]["value"].split(";")[0] + "; " + paraphrase
        )
        variant = TeamDecisionResult.from_grounded_final(
            json.dumps(changed, ensure_ascii=False), list(evidence),
            evidence_by_ref=evidence,
        )
        variant.validate_claim_support(evidence)
    for wrong in (
        "王芳报告直接给李明。", "李明报告直接给张伟。",
        "王芳直接向李明汇报。", "李明向张伟直接汇报。",
    ):
        changed = json.loads(observed["final_raw"])
        changed["decision"]["value"] = raw["decision"]["value"].split(";")[0] + "; " + wrong
        invalid = TeamDecisionResult.from_grounded_final(
            json.dumps(changed, ensure_ascii=False), list(evidence),
        )
        with pytest.raises(ValidationError):
            invalid.validate_claim_support(evidence)
    unrelated = {ref: item for ref, item in evidence.items()
                 if item["relative_path"] == "phase5_cn/owner.md"}
    with pytest.raises(ValidationError):
        decision.validate_claim_support(unrelated)


def _phase14_observed_and_read_evidence(run_id: str):
    run = Path(__file__).resolve().parents[2] / "work" / "phase14_gemini" / "runs" / run_id
    observed = json.loads((run / "observed.json").read_text(encoding="utf-8"))
    evidence = {}
    for path in (run / "checkpoints" / "models" / "model").glob(
        "*/*/tool-results/*.json"
    ):
        result = json.loads(path.read_text(encoding="utf-8"))["model_tool_result"]["result"]
        if result["tool_id"] == "read_verified_note" and result["status"] == "ok":
            value = result["value"]
            evidence[value["evidence_id"]] = value
    assert evidence
    return observed, evidence


@pytest.mark.parametrize("run_id", ["P14-ALIAS-A", "P14-ALIAS-B", "P14-CN2"])
def test_phase14_real_final_outputs_replay_against_the_exact_verified_reads(run_id: str):
    observed, evidence = _phase14_observed_and_read_evidence(run_id)
    workspace = next(iter(evidence.values()))["workspace_id"]
    assert all(_passage_identity(value, workspace) for value in evidence.values())

    decision = TeamDecisionResult.from_grounded_final(
        observed["final_raw"], list(evidence), evidence_by_ref=evidence,
        expected_workspace_id=workspace,
    )
    decision.validate_claim_support(evidence)


@pytest.mark.parametrize(
    "mutation",
    ["reverse_reporting", "wrong_date", "wrong_owner", "wrong_project"],
)
def test_phase14_cn2_final_rejects_wrong_relation_entity_or_time(mutation: str):
    observed, evidence = _phase14_observed_and_read_evidence("P14-CN2")
    payload = deepcopy(json.loads(observed["final_raw"]))
    if mutation == "reverse_reporting":
        payload["decision"]["value"] = payload["decision"]["value"].replace(
            "李明 reports directly to 王芳", "王芳 reports directly to 李明",
        )
    elif mutation == "wrong_date":
        payload["decision"]["value"] = payload["decision"]["value"].replace(
            "2025-03-01", "2025-03-02",
        )
    elif mutation == "wrong_owner":
        payload["decision"]["value"] = payload["decision"]["value"].replace(
            "李明", "张伟",
        )
    elif mutation == "wrong_project":
        payload["decision"]["value"] = payload["decision"]["value"].replace(
            "青岚计划", "Harbor 计划",
        )
    workspace = next(iter(evidence.values()))["workspace_id"]
    decision = TeamDecisionResult.from_grounded_final(
        json.dumps(payload, ensure_ascii=False), list(evidence),
        evidence_by_ref=evidence, expected_workspace_id=workspace,
    )
    with pytest.raises(ValidationError):
        decision.validate_claim_support(evidence)


@pytest.mark.parametrize("repeat", ["rep1", "rep2"])
def test_phase11_alias_final_replays_with_rejected_historical_candidate(repeat):
    run = (Path(__file__).resolve().parents[2] / "work" / "phase11" /
           "runs" / f"p11-alias-repeats-{repeat}")
    observed = json.loads((run / "observed.json").read_text(encoding="utf-8"))
    evidence = {}
    for path in (run / "checkpoints" / "models" / "model").glob(
        "*/*/tool-results/*.json"
    ):
        result = json.loads(path.read_text(encoding="utf-8"))["model_tool_result"]["result"]
        if result["tool_id"] == "read_verified_note" and result["status"] == "ok":
            value = result["value"]
            evidence[value["evidence_id"]] = value
    assert {value["relative_path"] for value in evidence.values()} == {
        "05-cobalt-alias.md", "07-cobalt-old-owner.md", "03-cobalt-owner.md",
    }
    workspace = next(iter(evidence.values()))["workspace_id"]
    assert all(_passage_identity(value, workspace) for value in evidence.values())
    decision = TeamDecisionResult.from_grounded_final(
        observed["final_raw"], list(evidence), evidence_by_ref=evidence,
    )
    decision.validate_claim_support(evidence)


@pytest.mark.parametrize("mutation", [
    "wrong_current_owner", "wrong_historical_owner", "negated_candidate",
    "wrong_draft_date", "wrong_current_date", "missing_draft_ref",
    "unrelated_draft_ref", "wrong_project", "missing_current_ref",
])
def test_phase11_alias_rejected_candidate_still_requires_both_scoped_facts(mutation):
    run = (Path(__file__).resolve().parents[2] / "work" / "phase11" /
           "runs" / "p11-alias-repeats-rep1")
    observed = json.loads((run / "observed.json").read_text(encoding="utf-8"))
    evidence = {}
    for path in (run / "checkpoints" / "models" / "model").glob(
        "*/*/tool-results/*.json"
    ):
        result = json.loads(path.read_text(encoding="utf-8"))["model_tool_result"]["result"]
        if result["tool_id"] == "read_verified_note" and result["status"] == "ok":
            value = result["value"]
            evidence[value["evidence_id"]] = value
    by_path = {value["relative_path"]: ref for ref, value in evidence.items()}
    raw = deepcopy(json.loads(observed["final_raw"]))
    item = raw["rejected_alternatives"][0]
    if mutation == "wrong_current_owner":
        raw["decision"]["value"] = raw["decision"]["value"].replace(
            "Mira Chen", "Leo Park",
        )
    elif mutation == "wrong_historical_owner":
        item["alternative"] = "Nia Vale is the owner of Cobalt B."
    elif mutation == "negated_candidate":
        item["alternative"] = "Leo Park is not the owner of Cobalt B."
    elif mutation == "wrong_draft_date":
        item["reason"] = item["reason"].replace("2025-01-20", "2025-01-21")
    elif mutation == "wrong_current_date":
        item["reason"] = item["reason"].replace("2025-02-15", "2025-02-16")
    elif mutation == "missing_draft_ref":
        item["evidence_refs"] = [by_path["03-cobalt-owner.md"]]
    elif mutation == "unrelated_draft_ref":
        item["evidence_refs"] = [
            by_path["05-cobalt-alias.md"], by_path["03-cobalt-owner.md"],
        ]
    elif mutation == "wrong_project":
        raw["decision"]["value"] = raw["decision"]["value"].replace(
            "owner of Cobalt B;", "owner of Cobalt B for Harbor;",
        )
    elif mutation == "missing_current_ref":
        item["evidence_refs"] = [by_path["07-cobalt-old-owner.md"]]
    raw["evidence_refs"] = list(dict.fromkeys(
        raw["decision"]["evidence_refs"] + item["evidence_refs"]
    ))
    result = TeamDecisionResult.from_grounded_final(
        json.dumps(raw, ensure_ascii=False), list(evidence),
        evidence_by_ref=evidence,
    )
    with pytest.raises(ValidationError):
        result.validate_claim_support(evidence)


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
