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
    assert "choose either one next tool call or Final" in instruction
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
