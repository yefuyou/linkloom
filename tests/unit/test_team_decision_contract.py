from __future__ import annotations

import json
from copy import deepcopy

import pytest

from linkloom.runtime.errors import ValidationError


def _minimal_payload() -> dict[str, object]:
    return {
        "schema_version": "team-decision-result/v1",
        "decision": {
            "value": "Aster A",
            "status": "approved",
            "evidence_refs": ["ev_decision"],
        },
        "rationale": [
            {
                "point": "The approved record requires an auditable data boundary.",
                "evidence_refs": ["ev_rationale"],
            }
        ],
        "rejected_alternatives": [],
        "actions": [],
        "unresolved_items": [],
        "uncertainty": {
            "status": "none",
            "statement": None,
            "unknown_fields": [],
            "evidence_refs": [],
        },
        "evidence_refs": ["ev_decision", "ev_rationale"],
    }


def _fully_populated_payload() -> dict[str, object]:
    return {
        "schema_version": "team-decision-result/v1",
        "decision": {
            "value": "Recorded choice",
            "status": "approved",
            "evidence_refs": ["ev_decision"],
        },
        "rationale": [
            {
                "point": "Recorded rationale",
                "evidence_refs": ["ev_rationale"],
            }
        ],
        "rejected_alternatives": [
            {
                "alternative": "Recorded alternative",
                "reason": "Recorded rejection reason",
                "evidence_refs": ["ev_rejected"],
            }
        ],
        "actions": [
            {
                "description": "Recorded action",
                "owner": None,
                "deadline": None,
                "status": "pending",
                "evidence_refs": ["ev_action"],
            }
        ],
        "unresolved_items": [
            {
                "description": "Recorded unresolved item",
                "owner": None,
                "deadline": None,
                "status": "blocked",
                "evidence_refs": ["ev_unresolved"],
            }
        ],
        "uncertainty": {
            "status": "partial",
            "statement": "One requested field is not recorded.",
            "unknown_fields": ["decision.detail"],
            "evidence_refs": ["ev_uncertainty"],
        },
        "evidence_refs": [
            "ev_decision",
            "ev_rationale",
            "ev_rejected",
            "ev_action",
            "ev_unresolved",
            "ev_uncertainty",
        ],
    }


def _phase14_cn2_claim_proposal() -> dict[str, object]:
    return {
        "schema_version": "claim-proposal/v1",
        "claims": [
            {
                "subject": "青岚计划",
                "predicate": "owner_of_project",
                "object": "李明",
                "evidence_ids": ["ev_owner"],
                "quote": "截至 2025-03-01，李明是青岚计划的负责人。",
                "valid_on": "2025-03-01",
                "temporal_kind": "snapshot",
            },
            {
                "subject": "李明",
                "predicate": "reports_to",
                "object": "王芳",
                "evidence_ids": ["ev_leader"],
                "quote": "李明的直属领导是王芳。",
                "valid_on": None,
                "temporal_kind": None,
            },
        ],
    }


def _phase14_cn2_evidence(workspace: str = "ws-cn2") -> dict[str, dict[str, object]]:
    return {
        "ev_owner": {
            "evidence_id": "ev_owner", "status": "verified", "workspace_id": workspace,
            "quote": "截至 2025-03-01，李明是青岚计划的负责人。",
        },
        "ev_leader": {
            "evidence_id": "ev_leader", "status": "verified", "workspace_id": workspace,
            "quote": "李明的直属领导是王芳。",
        },
    }


def test_team_decision_model_instruction_exposes_the_complete_final_contract() -> None:
    from linkloom.agents.team_decision import TEAM_DECISION_REQUIRED_FIELDS
    from linkloom.agents.retrieval_agent import RetrievalAgent

    instruction = RetrievalAgent._retrieval_instruction(
        "Which provider was approved?",
        {"document_count": 1},
        workflow="team_decision",
    )

    marker = "team_decision_final_schema="
    assert marker in instruction
    assert "claim_proposal_schema=" not in instruction
    assert "claim-proposal/v1" not in instruction
    assert "choose one or more tool calls only" in instruction
    assert "stop using tools" in instruction
    assert "do not call tools only to populate optional sections" in instruction
    assert "directly supports the requested decision" in instruction
    assert "normal text response" in instruction
    assert "do not make a function call" in instruction
    assert "Every field listed in each required array is mandatory" in instruction
    assert '"unknown_fields":[]' in instruction
    assert "Do not wrap the JSON in Markdown fences" in instruction
    assert "Do not write any explanation before or after the JSON" in instruction
    encoded = instruction.split(marker, 1)[1].splitlines()[0]
    schema = json.loads(encoded)
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(TEAM_DECISION_REQUIRED_FIELDS["$"])
    assert schema["properties"]["schema_version"]["const"] == "team-decision-result/v1"
    decision_schema = schema["properties"]["decision"]
    assert decision_schema["required"] == list(TEAM_DECISION_REQUIRED_FIELDS["$.decision"])
    assert decision_schema["properties"]["value"]["type"] == ["string", "null"]
    assert decision_schema["properties"]["status"]["enum"] == [
        "approved",
        "insufficient_evidence",
        "not_found",
        "partial",
    ]
    actions_item = schema["properties"]["actions"]["items"]
    assert actions_item["required"] == list(TEAM_DECISION_REQUIRED_FIELDS["$.actions[]"])
    assert actions_item["properties"]["owner"]["type"] == ["string", "null"]
    uncertainty_schema = schema["properties"]["uncertainty"]
    assert uncertainty_schema["required"] == list(
        TEAM_DECISION_REQUIRED_FIELDS["$.uncertainty"]
    )
    assert uncertainty_schema["properties"]["statement"]["type"] == ["string", "null"]
    assert uncertainty_schema["properties"]["unknown_fields"]["type"] == "array"

    from linkloom.agents.team_decision import team_decision_final_instruction

    provider_neutral_instruction = team_decision_final_instruction()
    assert all(
        forbidden not in provider_neutral_instruction
        for forbidden in ("mps-001", "Aster A", "Atlas Lantern")
    )


def test_typed_claims_instruction_is_explicit_and_contains_only_its_selected_schema() -> None:
    from linkloom.agents.team_decision import claim_proposal_schema, team_decision_final_instruction
    from linkloom.agents.retrieval_agent import RetrievalAgent

    prompt = RetrievalAgent._retrieval_instruction(
        "陌生问题：Juniper Orbit 的负责人向谁汇报？",
        {"document_count": 1},
        workflow="team_decision",
        final_protocol="claim-proposal/v1",
    )
    assert "final_protocol=claim-proposal/v1" in prompt
    assert "team-decision-result/v1" not in prompt
    assert "decision.value" not in prompt
    assert "accountable owner" not in prompt
    assert "claim_proposal_schema=" not in prompt
    encoded = prompt.split("team_decision_final_schema=", 1)[1].splitlines()[0]
    assert json.loads(encoded) == claim_proposal_schema()

    legacy = team_decision_final_instruction()
    typed = team_decision_final_instruction(final_protocol="claim-proposal/v1")
    assert "claim-proposal/v1" not in legacy
    assert "team-decision-result/v1" not in typed


def test_typed_claim_relation_roles_are_visible_in_the_selected_final_protocol() -> None:
    from linkloom.agents.team_decision import claim_proposal_schema, team_decision_final_instruction
    from linkloom.agents.retrieval_agent import RetrievalAgent

    schema = claim_proposal_schema()
    roles = schema["x-linkloom-relation-definitions"]
    assert roles == [
        {
            "predicate": "alias_of_project",
            "subject_type": "alias",
            "object_type": "project",
            "direction": "The subject name is an alias for the object project; this says nothing about ownership.",
        },
        {
            "predicate": "owner_of_project",
            "subject_type": "project",
            "object_type": "person",
            "direction": "The subject project has the object person as its owner.",
        },
        {
            "predicate": "reports_to",
            "subject_type": "person",
            "object_type": "person",
            "direction": "The subject person reports to the object person.",
        },
    ]
    instruction = team_decision_final_instruction(final_protocol="claim-proposal/v1")
    assert "owner_of_project: subject=project, object=person" in instruction
    assert "reports_to: subject=person, object=person" in instruction
    assert "regardless of the word order in the source quote" in instruction
    runtime_prompt = RetrievalAgent._retrieval_instruction(
        "Who owns Cedar Harbor and whom do they report to?",
        {"document_count": 2}, workflow="team_decision",
        final_protocol="claim-proposal/v1",
    )
    assert "owner_of_project: subject=project, object=person" in runtime_prompt
    assert "reports_to: subject=person, object=person" in runtime_prompt


def test_missing_uncertainty_unknown_fields_is_rejected_without_parser_defaults() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["uncertainty"] = {
        "status": "none",
        "statement": None,
        "evidence_refs": [],
    }

    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(payload)
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_json(json.dumps(payload))


def test_complete_uncertainty_accepts_required_empty_arrays() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["rationale"] = []
    payload["uncertainty"] = {
        "status": "none",
        "statement": None,
        "unknown_fields": [],
        "evidence_refs": [],
    }
    payload["evidence_refs"] = ["ev_decision"]

    result = TeamDecisionResult.from_json(json.dumps(payload))

    assert result.uncertainty["unknown_fields"] == []
    assert result.rationale == []


def test_final_schema_required_fields_match_every_strict_validator_object() -> None:
    from linkloom.agents.team_decision import (
        TEAM_DECISION_REQUIRED_FIELDS,
        TeamDecisionResult,
        team_decision_final_schema,
    )

    schema = team_decision_final_schema()
    schema_required = {
        "$": tuple(schema["required"]),
        "$.decision": tuple(schema["properties"]["decision"]["required"]),
        "$.rationale[]": tuple(schema["properties"]["rationale"]["items"]["required"]),
        "$.rejected_alternatives[]": tuple(
            schema["properties"]["rejected_alternatives"]["items"]["required"]
        ),
        "$.actions[]": tuple(schema["properties"]["actions"]["items"]["required"]),
        "$.unresolved_items[]": tuple(
            schema["properties"]["unresolved_items"]["items"]["required"]
        ),
        "$.uncertainty": tuple(schema["properties"]["uncertainty"]["required"]),
    }
    assert schema_required == TEAM_DECISION_REQUIRED_FIELDS
    schema_properties = {
        "$": tuple(schema["properties"]),
        "$.decision": tuple(schema["properties"]["decision"]["properties"]),
        "$.rationale[]": tuple(schema["properties"]["rationale"]["items"]["properties"]),
        "$.rejected_alternatives[]": tuple(
            schema["properties"]["rejected_alternatives"]["items"]["properties"]
        ),
        "$.actions[]": tuple(schema["properties"]["actions"]["items"]["properties"]),
        "$.unresolved_items[]": tuple(
            schema["properties"]["unresolved_items"]["items"]["properties"]
        ),
        "$.uncertainty": tuple(schema["properties"]["uncertainty"]["properties"]),
    }
    assert schema_properties == TEAM_DECISION_REQUIRED_FIELDS

    for path, fields in TEAM_DECISION_REQUIRED_FIELDS.items():
        for field in fields:
            payload = _fully_populated_payload()
            if path == "$":
                node = payload
            elif path.endswith("[]"):
                node = payload[path.removeprefix("$.").removesuffix("[]")][0]
            else:
                node = payload[path.removeprefix("$.")]
            node.pop(field)
            with pytest.raises(ValidationError):
                TeamDecisionResult.from_dict(payload)


def test_team_decision_result_round_trips_a_strict_claim_grounded_payload() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()

    result = TeamDecisionResult.from_dict(payload)

    assert result.to_dict() == payload
    assert result.claim_evidence_refs() == ["ev_decision", "ev_rationale"]


def test_team_decision_result_rejects_unknown_fields_and_missing_material_claim_refs() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    unknown = _minimal_payload()
    unknown["suggested_action"] = "Do not accept model suggestions in M1.1."
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(unknown)

    missing_claim_ref = _minimal_payload()
    decision = dict(missing_claim_ref["decision"])
    decision["evidence_refs"] = []
    missing_claim_ref["decision"] = decision
    missing_claim_ref["evidence_refs"] = ["ev_rationale"]
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(missing_claim_ref)


@pytest.mark.parametrize("status", ["approved", "partial"])
def test_material_decision_status_cannot_be_null_even_when_other_claims_are_grounded(
    status: str,
) -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["decision"] = {
        "value": None,
        "status": status,
        "evidence_refs": [],
    }
    payload["evidence_refs"] = ["ev_rationale"]

    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(payload)


@pytest.mark.parametrize("status", ["not_found", "insufficient_evidence"])
def test_only_non_conclusion_statuses_allow_a_null_decision_value(status: str) -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["decision"] = {"value": None, "status": status, "evidence_refs": []}
    payload["rationale"] = []
    payload["evidence_refs"] = []

    assert TeamDecisionResult.from_dict(payload).decision["value"] is None


def test_team_decision_result_parses_only_claims_grounded_in_observed_evidence() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    assert TeamDecisionResult.from_grounded_final(
        json.dumps(payload), ["ev_decision", "ev_rationale"]
    ).to_dict() == payload

    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final("not-json", ["ev_decision", "ev_rationale"])
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(json.dumps(payload), ["ev_decision"])


def test_grounded_final_normalizes_only_complete_reference_permutations_in_workspace() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["evidence_refs"] = ["ev_rationale", "ev_decision"]
    evidence = {
        ref: {"evidence_id": ref, "workspace_id": "ws-current", "status": "verified"}
        for ref in ("ev_decision", "ev_rationale")
    }

    # The standalone contract remains strict; only the grounded runtime boundary
    # can normalize after checking this run's complete verified evidence set.
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(payload)

    result = TeamDecisionResult.from_grounded_final(
        json.dumps(payload),
        ["ev_decision", "ev_rationale"],
        evidence_by_ref=evidence,
        expected_workspace_id="ws-current",
    )
    assert result.evidence_refs == ["ev_decision", "ev_rationale"]


@pytest.mark.parametrize(
    "mutation",
    ["missing_nested_reference", "extra_reference", "unobserved_reference", "foreign_workspace"],
)
def test_grounded_final_does_not_normalize_incomplete_or_untrusted_references(mutation: str) -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["evidence_refs"] = ["ev_rationale", "ev_decision"]
    observed = ["ev_decision", "ev_rationale"]
    evidence = {
        ref: {"evidence_id": ref, "workspace_id": "ws-current", "status": "verified"}
        for ref in observed
    }
    if mutation == "missing_nested_reference":
        payload["evidence_refs"] = ["ev_rationale"]
    elif mutation == "extra_reference":
        payload["evidence_refs"] = ["ev_rationale", "ev_decision", "ev_extra"]
    elif mutation == "unobserved_reference":
        payload["evidence_refs"] = ["ev_rationale", "ev_decision"]
        observed.remove("ev_rationale")
    elif mutation == "foreign_workspace":
        evidence["ev_rationale"]["workspace_id"] = "ws-foreign"

    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            json.dumps(payload), observed, evidence_by_ref=evidence,
            expected_workspace_id="ws-current",
        )


def test_team_decision_result_preserves_missing_owner_and_deadline_as_null() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["actions"] = [
        {
            "description": "Complete the recorded security review.",
            "owner": None,
            "deadline": None,
            "status": "pending",
            "evidence_refs": ["ev_action"],
        }
    ]
    payload["evidence_refs"] = ["ev_decision", "ev_rationale", "ev_action"]

    result = TeamDecisionResult.from_dict(payload)

    assert result.to_dict()["actions"][0]["owner"] is None
    assert result.to_dict()["actions"][0]["deadline"] is None


def test_team_decision_result_enforces_status_date_and_nested_ref_boundaries() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    invalid_decision = _minimal_payload()
    invalid_decision["decision"] = {**invalid_decision["decision"], "status": "guessed"}
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(invalid_decision)

    invalid_action = _minimal_payload()
    invalid_action["actions"] = [{
        "description": "A recorded action.", "owner": None, "deadline": None,
        "status": "suggested", "evidence_refs": ["ev_action"],
    }]
    invalid_action["evidence_refs"] = ["ev_decision", "ev_rationale", "ev_action"]
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(invalid_action)

    invalid_deadline = deepcopy(invalid_action)
    invalid_deadline["actions"][0]["status"] = "pending"
    invalid_deadline["actions"][0]["deadline"] = "next-week"
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(invalid_deadline)

    basic_iso_deadline = deepcopy(invalid_action)
    basic_iso_deadline["actions"][0]["status"] = "pending"
    basic_iso_deadline["actions"][0]["deadline"] = "20260914"
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(basic_iso_deadline)

    missing_union_ref = _minimal_payload()
    missing_union_ref["rationale"] = [{
        "point": "A separate grounded point.", "evidence_refs": ["ev_nested"],
    }]
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(missing_union_ref)


def test_final_schema_matches_validator_text_and_date_lexical_boundaries() -> None:
    from linkloom.agents.team_decision import (
        ISO_DATE_PATTERN,
        NON_WHITESPACE_TEXT_PATTERN,
        TeamDecisionResult,
        team_decision_final_schema,
    )

    schema = team_decision_final_schema()
    assert "(?" not in NON_WHITESPACE_TEXT_PATTERN
    assert schema["properties"]["decision"]["properties"]["value"]["pattern"] == (
        NON_WHITESPACE_TEXT_PATTERN
    )
    assert schema["properties"]["actions"]["items"]["properties"]["deadline"][
        "pattern"
    ] == ISO_DATE_PATTERN

    whitespace_value = _minimal_payload()
    whitespace_value["decision"]["value"] = "   "
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_dict(whitespace_value)


def test_team_decision_result_preserves_explicit_unassigned_distinct_from_null() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    payload = _minimal_payload()
    payload["actions"] = [{
        "description": "The source records no assignee.",
        "owner": "unassigned",
        "deadline": None,
        "status": "unassigned",
        "evidence_refs": ["ev_action"],
    }]
    payload["evidence_refs"] = ["ev_decision", "ev_rationale", "ev_action"]

    assert TeamDecisionResult.from_dict(payload).to_dict()["actions"][0]["owner"] == "unassigned"


def test_phase14_cn2_claim_proposal_verifies_and_generates_existing_result_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from linkloom.agents import team_decision
    from linkloom.agents.team_decision import TeamDecisionResult

    evidence = _phase14_cn2_evidence()
    result = TeamDecisionResult.from_grounded_final(
        json.dumps(_phase14_cn2_claim_proposal(), ensure_ascii=False),
        ["ev_owner", "ev_leader"], evidence_by_ref=evidence,
        expected_workspace_id="ws-cn2",
        final_protocol="claim-proposal/v1",
    )

    assert result.schema_version == "team-decision-result/v1"
    assert result.decision == {
        "value": "截至 2025-03-01，青岚计划的负责人是李明；李明直接向王芳汇报",
        "status": "approved",
        "evidence_refs": ["ev_owner", "ev_leader"],
    }
    assert result.to_dict()["evidence_refs"] == ["ev_owner", "ev_leader"]

    def forbid_answer_text_reparse(*args, **kwargs):
        raise AssertionError("verified typed claims must not be reparsed from generated prose")

    monkeypatch.setattr(team_decision, "assess_claim_text", forbid_answer_text_reparse)
    result.validate_claim_support(evidence)


def test_typed_final_can_preserve_verified_partial_fact_without_claiming_missing_hop() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult, claim_proposal_schema

    owner_quote = "As of 2025-04-01 Ada Wynn is the accountable owner of Saffron Program."
    evidence = {
        "ev_saffron_owner": {
            "evidence_id": "ev_saffron_owner", "status": "verified",
            "workspace_id": "ws-saffron", "quote": owner_quote,
        },
    }
    claim = {
        "subject": "Saffron Program", "predicate": "owner_of_project",
        "object": "Ada Wynn", "evidence_ids": ["ev_saffron_owner"],
        "quote": owner_quote, "valid_on": "2025-04-01",
        "temporal_kind": "snapshot",
    }
    assert claim_proposal_schema()["properties"]["claims"]["minItems"] == 0
    partial = {
        "schema_version": "claim-proposal/v1", "claims": [claim],
        "uncertainty": {
            "status": "insufficient_evidence", "unknown_fields": ["leader"],
        },
    }
    result = TeamDecisionResult.from_grounded_final(
        json.dumps(partial), ["ev_saffron_owner"], evidence_by_ref=evidence,
        expected_workspace_id="ws-saffron", final_protocol="claim-proposal/v1",
    )
    assert result.decision == {
        "value": None, "status": "insufficient_evidence", "evidence_refs": [],
    }
    assert result.uncertainty["unknown_fields"] == ["leader"]
    assert result.rationale[0]["evidence_refs"] == ["ev_saffron_owner"]
    result.validate_claim_support(evidence)

    unsupported = deepcopy(partial)
    unsupported["claims"][0]["object"] = "Bea Roe"
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            json.dumps(unsupported), ["ev_saffron_owner"], evidence_by_ref=evidence,
            expected_workspace_id="ws-saffron", final_protocol="claim-proposal/v1",
        )

    for bad in (
        {"schema_version": "claim-proposal/v1", "claims": []},
        {"schema_version": "claim-proposal/v1", "claims": [claim],
         "uncertainty": {"status": "insufficient_evidence", "unknown_fields": []}},
    ):
        with pytest.raises(ValidationError):
            TeamDecisionResult.from_grounded_final(
                json.dumps(bad), ["ev_saffron_owner"], evidence_by_ref=evidence,
                expected_workspace_id="ws-saffron", final_protocol="claim-proposal/v1",
            )


def test_g38_004_sealed_final_copy_keeps_direction_and_relation_checks_strict() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    # Minimal copy of the sealed turn-5 Final and the two verified read results.
    owner_id = "ev_v2_c4627fe6bcd28e191233b26fa612663473fb00ef7be01c1cfb3fbca19bd950d3"
    leader_id = "ev_v2_b6b1cd488f13a65dd1bb347c0c2d1498d0003bb97991456b5b9a4202bc74b1c4"
    workspace = "root_26383988dc2f593f"
    evidence = {
        owner_id: {
            "evidence_id": owner_id, "status": "verified", "workspace_id": workspace,
            "quote": "截至 2025-03-01，李明是青岚计划的负责人。",
        },
        leader_id: {
            "evidence_id": leader_id, "status": "verified", "workspace_id": workspace,
            "quote": "李明的直属领导是王芳。",
        },
    }
    sealed_final = {
        "schema_version": "claim-proposal/v1",
        "claims": [
            {
                "subject": "李明", "predicate": "owner_of_project", "object": "青岚计划",
                "evidence_ids": [owner_id], "quote": evidence[owner_id]["quote"],
                "valid_on": "2025-03-01", "temporal_kind": "snapshot",
            },
            {
                "subject": "李明", "predicate": "reports_to", "object": "王芳",
                "evidence_ids": [leader_id], "quote": evidence[leader_id]["quote"],
                "valid_on": None, "temporal_kind": None,
            },
        ],
    }

    def validate(payload: dict[str, object]) -> TeamDecisionResult:
        return TeamDecisionResult.from_grounded_final(
            json.dumps(payload, ensure_ascii=False), [owner_id, leader_id],
            evidence_by_ref=evidence, expected_workspace_id=workspace,
            final_protocol="claim-proposal/v1",
        )

    with pytest.raises(ValidationError, match="relation or temporal scope"):
        validate(sealed_final)

    corrected = deepcopy(sealed_final)
    corrected["claims"][0]["subject"] = "青岚计划"
    corrected["claims"][0]["object"] = "李明"
    result = validate(corrected)
    result.validate_claim_support(evidence)
    assert result.decision["status"] == "approved"

    unsupported = deepcopy(corrected)
    unsupported["claims"][0]["predicate"] = "reports_to"
    with pytest.raises(ValidationError, match="relation or temporal scope"):
        validate(unsupported)

    reversed_report = deepcopy(corrected)
    reversed_report["claims"][1]["subject"] = "王芳"
    reversed_report["claims"][1]["object"] = "李明"
    with pytest.raises(ValidationError, match="relation or temporal scope"):
        validate(reversed_report)


def test_final_protocol_selection_never_infers_or_falls_back_between_contracts() -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    evidence = _phase14_cn2_evidence()
    proposal = json.dumps(_phase14_cn2_claim_proposal(), ensure_ascii=False)
    legacy = json.dumps(_minimal_payload())
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            proposal, ["ev_owner", "ev_leader"], evidence_by_ref=evidence,
            expected_workspace_id="ws-cn2",
        )
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            legacy, ["ev_owner", "ev_leader"], evidence_by_ref=evidence,
            expected_workspace_id="ws-cn2", final_protocol="claim-proposal/v1",
        )


def test_run_request_persists_explicit_final_protocol_and_keeps_legacy_default() -> None:
    from linkloom.runtime.models import RunRequest

    legacy = RunRequest(request_id="legacy", workflow="team_decision", query="owner?")
    assert "final_protocol" not in legacy.to_dict()
    assert RunRequest.from_dict(legacy.to_dict()).final_protocol == "team-decision-result/v1"

    typed = RunRequest(
        request_id="typed", workflow="team_decision", query="owner?",
        final_protocol="claim-proposal/v1",
    )
    restored = RunRequest.from_dict(typed.to_dict())
    assert restored.final_protocol == "claim-proposal/v1"
    assert typed.to_dict()["final_protocol"] == "claim-proposal/v1"
    with pytest.raises(ValidationError):
        RunRequest(
            request_id="unsupported", workflow="ask", query="owner?",
            final_protocol="claim-proposal/v1",
        )


@pytest.mark.parametrize(
    ("mutation", "workspace", "observed"),
    [
        ("wrong_owner", "ws-cn2", ["ev_owner", "ev_leader"]),
        ("reverse_relation", "ws-cn2", ["ev_owner", "ev_leader"]),
        ("wrong_date", "ws-cn2", ["ev_owner", "ev_leader"]),
        ("unrelated_quote", "ws-cn2", ["ev_owner", "ev_leader"]),
        ("foreign_workspace", "ws-foreign", ["ev_owner", "ev_leader"]),
        ("unobserved_id", "ws-cn2", ["ev_owner"]),
        ("unsupported_predicate", "ws-cn2", ["ev_owner", "ev_leader"]),
    ],
)
def test_phase14_cn2_claim_proposal_rejects_bad_claims_quotes_and_provenance(
    mutation: str, workspace: str, observed: list[str],
) -> None:
    from linkloom.agents.team_decision import TeamDecisionResult

    proposal = _phase14_cn2_claim_proposal()
    if mutation == "wrong_owner":
        proposal["claims"][0]["object"] = "赵强"
    elif mutation == "reverse_relation":
        proposal["claims"][1]["subject"] = "王芳"
        proposal["claims"][1]["object"] = "李明"
    elif mutation == "wrong_date":
        proposal["claims"][0]["valid_on"] = "2025-03-02"
    elif mutation == "unrelated_quote":
        proposal["claims"][0]["quote"] = "青岚计划存在风险。"
    elif mutation == "unsupported_predicate":
        proposal["claims"][0]["predicate"] = "directs"
    elif mutation == "unobserved_id":
        proposal["claims"][1]["evidence_ids"] = ["ev_foreign"]
    evidence = _phase14_cn2_evidence(workspace)

    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            json.dumps(proposal, ensure_ascii=False), observed,
            evidence_by_ref=evidence, expected_workspace_id="ws-cn2",
            final_protocol="claim-proposal/v1",
        )
