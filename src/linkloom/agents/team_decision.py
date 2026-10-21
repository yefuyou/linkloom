"""Strict provider-neutral contract for M1.1 Team Decision results."""

from __future__ import annotations

import json
import hashlib
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from linkloom.runtime.errors import ValidationError
from linkloom.agents.claim_grounding import (
    ClaimSupportStatus, TYPED_CLAIM_RELATIONS, assess_claim_text, assess_rejected_owner_alternative,
    evidence_supports_typed_claim,
)


SCHEMA_VERSION = "team-decision-result/v1"
DECISION_STATUSES = {"approved", "partial", "insufficient_evidence", "not_found"}
NULL_DECISION_STATUSES = {"insufficient_evidence", "not_found"}
ACTION_STATUSES = {"pending", "in_progress", "blocked", "completed", "unassigned"}
UNRESOLVED_STATUSES = {"pending", "in_progress", "blocked", "unassigned"}
UNCERTAINTY_STATUSES = {"none", "partial", "insufficient_evidence", "conflicting_evidence"}
NON_WHITESPACE_TEXT_PATTERN = r"[\s\S]*\S[\s\S]*"
ISO_DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
CLAIM_PROPOSAL_SCHEMA_VERSION = "claim-proposal/v1"
CLAIM_PROPOSAL_PREDICATES = frozenset(TYPED_CLAIM_RELATIONS)

TEAM_DECISION_REQUIRED_FIELDS = {
    "$": (
        "schema_version",
        "decision",
        "rationale",
        "rejected_alternatives",
        "actions",
        "unresolved_items",
        "uncertainty",
        "evidence_refs",
    ),
    "$.decision": ("value", "status", "evidence_refs"),
    "$.rationale[]": ("point", "evidence_refs"),
    "$.rejected_alternatives[]": ("alternative", "reason", "evidence_refs"),
    "$.actions[]": ("description", "owner", "deadline", "status", "evidence_refs"),
    "$.unresolved_items[]": (
        "description",
        "owner",
        "deadline",
        "status",
        "evidence_refs",
    ),
    "$.uncertainty": ("status", "statement", "unknown_fields", "evidence_refs"),
}


def _evidence_refs_schema(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "uniqueItems": True,
        "description": description,
    }


def _strict_object_schema(
    path: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(TEAM_DECISION_REQUIRED_FIELDS[path]),
        "properties": properties,
    }


def _claim_array_schema(path: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _strict_object_schema(path, properties),
    }


def team_decision_final_schema() -> dict[str, Any]:
    """Return the model-facing schema generated from the strict validator field source."""

    text = {
        "type": "string",
        "minLength": 1,
        "pattern": NON_WHITESPACE_TEXT_PATTERN,
    }
    nullable_text = {
        "type": ["string", "null"],
        "minLength": 1,
        "pattern": NON_WHITESPACE_TEXT_PATTERN,
    }
    item_refs = _evidence_refs_schema(
        "Required and non-empty for each material claim; use only observed verified evidence ids."
    )
    properties = {
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "decision": _strict_object_schema(
            "$.decision",
            {
                "value": nullable_text,
                "status": {"type": "string", "enum": sorted(DECISION_STATUSES)},
                "evidence_refs": item_refs,
            },
        ),
        "rationale": _claim_array_schema(
            "$.rationale[]",
            {
                "point": text,
                "evidence_refs": item_refs,
            },
        ),
        "rejected_alternatives": _claim_array_schema(
            "$.rejected_alternatives[]",
            {
                "alternative": text,
                "reason": text,
                "evidence_refs": item_refs,
            },
        ),
        "actions": _claim_array_schema(
            "$.actions[]",
            {
                "description": text,
                "owner": nullable_text,
                "deadline": {
                    "type": ["string", "null"],
                    "pattern": ISO_DATE_PATTERN,
                },
                "status": {"type": "string", "enum": sorted(ACTION_STATUSES)},
                "evidence_refs": item_refs,
            },
        ),
        "unresolved_items": _claim_array_schema(
            "$.unresolved_items[]",
            {
                "description": text,
                "owner": nullable_text,
                "deadline": {
                    "type": ["string", "null"],
                    "pattern": ISO_DATE_PATTERN,
                },
                "status": {"type": "string", "enum": sorted(UNRESOLVED_STATUSES)},
                "evidence_refs": item_refs,
            },
        ),
        "uncertainty": _strict_object_schema(
            "$.uncertainty",
            {
                "status": {"type": "string", "enum": sorted(UNCERTAINTY_STATUSES)},
                "statement": nullable_text,
                "unknown_fields": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Required even when empty; use [] when no fields are unknown.",
                },
                "evidence_refs": item_refs,
            },
        ),
        "evidence_refs": _evidence_refs_schema(
            "Exact ordered, deduplicated union of every nested claim evidence_refs array."
        ),
    }
    schema = _strict_object_schema("$", properties)
    schema["title"] = "TeamDecisionResult"
    schema["description"] = (
        "Strict provider-neutral Final contract. Every object property is required and extra "
        "properties are rejected."
    )
    schema["x-linkloom-validation-rules"] = [
        "decision.value is null only for insufficient_evidence or not_found; material decisions require a value.",
        "Every material claim requires at least one observed verified evidence reference.",
        "Top-level evidence_refs equals the ordered deduplicated union of all nested claim references.",
    ]
    return schema


def claim_proposal_schema() -> dict[str, Any]:
    """Provider-neutral schema for bounded facts with source evidence."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "claims"],
        "x-linkloom-relation-definitions": [
            {
                "predicate": predicate,
                "subject_type": definition["subject_type"],
                "object_type": definition["object_type"],
                "direction": definition["direction"],
            }
            for predicate, definition in TYPED_CLAIM_RELATIONS.items()
        ],
        "properties": {
            "schema_version": {
                "type": "string", "const": CLAIM_PROPOSAL_SCHEMA_VERSION,
            },
            "claims": {
                "type": "array", "minItems": 0, "maxItems": 8,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": [
                        "subject", "predicate", "object", "evidence_ids",
                        "quote", "valid_on", "temporal_kind",
                    ],
                    "properties": {
                        "subject": {"type": "string", "minLength": 1, "maxLength": 128},
                        "predicate": {
                            "type": "string", "enum": sorted(CLAIM_PROPOSAL_PREDICATES),
                        },
                        "object": {"type": "string", "minLength": 1, "maxLength": 128},
                        "evidence_ids": {
                            "type": "array", "minItems": 1, "maxItems": 4,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 128},
                        },
                        "quote": {"type": "string", "minLength": 1, "maxLength": 2048},
                        "valid_on": {
                            "type": ["string", "null"], "pattern": ISO_DATE_PATTERN,
                        },
                        "temporal_kind": {
                            "type": ["string", "null"],
                            "enum": ["event", "snapshot", None],
                        },
                    },
                },
            },
            "temporal_decision": {
                "type": "object", "additionalProperties": False,
                "description": (
                    "Optional reviewed Decision Memory claim. Copy IDs and as_of from a "
                    "reviewed temporal search result; cite verified source reads for both "
                    "the effective decision and its predecessor. The Runtime rechecks the "
                    "receipt, valid time, replacement and source identity independently."
                ),
                "required": [
                    "subject", "relation", "as_of", "decision_id", "receipt_id",
                    "value", "supersedes_id", "evidence_ids", "prior_evidence_ids",
                ],
                "properties": {
                    "subject": {"type": "string", "minLength": 1, "maxLength": 512},
                    "relation": {"type": "string", "minLength": 1, "maxLength": 512},
                    "as_of": {"type": "string", "minLength": 1, "maxLength": 64},
                    "decision_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "receipt_id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "value": {"type": "string", "minLength": 1, "maxLength": 512},
                    "supersedes_id": {"type": ["string", "null"], "maxLength": 128},
                    "evidence_ids": {"type": "array", "minItems": 1, "maxItems": 4,
                                     "uniqueItems": True, "items": {"type": "string"}},
                    "prior_evidence_ids": {"type": "array", "maxItems": 4,
                                           "uniqueItems": True, "items": {"type": "string"}},
                },
            },
            "uncertainty": {
                "type": "object", "additionalProperties": False,
                "required": ["status", "unknown_fields"],
                "properties": {
                    "status": {"type": "string", "enum": [
                        "insufficient_evidence", "conflicting_evidence",
                    ]},
                    "unknown_fields": {
                        "type": "array", "minItems": 1, "maxItems": 5,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1, "maxLength": 64},
                    },
                },
            },
        },
    }


def team_decision_final_instruction(final_protocol: str = SCHEMA_VERSION) -> str:
    """Describe exactly one explicitly selected, provider-neutral Final protocol."""

    if final_protocol == SCHEMA_VERSION:
        schema = team_decision_final_schema()
        protocol_rules = (
            "Return exactly one TeamDecisionResult/v1 JSON object. Every field listed in each "
            "required array is mandatory, including nested fields with empty or null values. "
            "For an empty array, output []; for a nullable field with no supported value, output null. "
            'uncertainty must always contain "unknown_fields"; when none are unknown, output '
            'exactly "unknown_fields":[] . '
            "Every material claim needs non-empty evidence_refs, and top-level evidence_refs must "
            "be the ordered deduplicated union of all nested claim refs. For insufficient_evidence "
            "with no verified passage supporting an uncertainty statement, set "
            "uncertainty.statement=null and uncertainty.evidence_refs=[]. Preserve a dated "
            "snapshot with an 'As of YYYY-MM-DD' qualifier.\n"
        )
    elif final_protocol == CLAIM_PROPOSAL_SCHEMA_VERSION:
        schema = claim_proposal_schema()
        relation_roles = " ".join(
            f"{predicate}: subject={definition['subject_type']}, "
            f"object={definition['object_type']}; {definition['direction']}"
            for predicate, definition in TYPED_CLAIM_RELATIONS.items()
        )
        protocol_rules = (
            "Return exactly one claim-proposal/v1 JSON object containing typed facts, exact quotes "
            "copied from verified reads, and their observed evidence_ids. Do not write an answer "
            "sentence or TeamDecisionResult fields. The Runtime independently checks each claim "
            "against its cited active-workspace evidence, then projects verified facts into the "
            "existing TeamDecisionResult/v1 result for review. Copy date scope into valid_on and "
            "temporal_kind when the cited source provides it. "
            "Relation roles apply regardless of the word order in the source quote: "
            f"{relation_roles} "
            "When a required hop remains unverified, keep only independently verified facts "
            "in claims and add uncertainty with status=insufficient_evidence and the exact "
            "unresolved task_plan subgoal IDs in unknown_fields. Use claims=[] when no fact "
            "was verified. Conflicting verified sources require status=conflicting_evidence. "
            "Do not mark a tool or Provider failure as missing source evidence. "
            "For a reviewed temporal decision, optionally add temporal_decision using "
            "the exact subject, relation, as_of, decision_id and receipt_id returned by "
            "search_decision_memory with subject and relation. Cite source evidence IDs "
            "that you actually read, including the predecessor if supersedes_id is set. "
            "An unverified replacement reason belongs in uncertainty. "
            "Omit uncertainty only when the full requested result is supported.\n"
        )
    else:
        raise ValidationError("Unsupported Team Decision Final protocol.")

    encoded_schema = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return (
        f"final_protocol={final_protocol}\n"
        "When observed evidence is sufficient, do not call another tool. Return Final as exactly "
        "one complete JSON object using only the explicitly selected protocol below. Do not wrap "
        "the JSON in Markdown fences. Do not write any explanation before or after the JSON. Final is the normal "
        "text response and must not be a function call. Use only observed verified evidence IDs.\n"
        f"{protocol_rules}"
        f"team_decision_final_schema={encoded_schema}"
    )


def _require_exact_keys(value: object, keys: Collection[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValidationError(f"{name} has an invalid field set.")
    return value


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(NON_WHITESPACE_TEXT_PATTERN, value) is None:
        raise ValidationError(f"{name} must be non-empty text.")
    return value


def _require_refs(value: object, name: str, *, required: bool) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(ref, str) or not ref for ref in value):
        raise ValidationError(f"{name} must be a list of non-empty evidence references.")
    if len(set(value)) != len(value):
        raise ValidationError(f"{name} must not contain duplicate evidence references.")
    if required and not value:
        raise ValidationError(f"{name} is required for a material claim.")
    return list(value)


def _validate_owner(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, name)


def _validate_deadline(value: object, name: str) -> str | None:
    if value is None:
        return None
    deadline = _require_text(value, name)
    if re.fullmatch(ISO_DATE_PATTERN, deadline) is None:
        raise ValidationError(f"{name} must be an ISO YYYY-MM-DD date or null.")
    try:
        date.fromisoformat(deadline)
    except ValueError as error:
        raise ValidationError(f"{name} must be an ISO YYYY-MM-DD date or null.") from error
    return deadline


def _append_refs(target: list[str], refs: list[str]) -> None:
    for ref in refs:
        if ref not in target:
            target.append(ref)


def _claim_refs_from_validated_payload(data: Mapping[str, Any]) -> list[str]:
    """Return the canonical claim-reference union after nested fields validated."""
    refs: list[str] = []
    rows = [data["decision"], *data["rationale"], *data["rejected_alternatives"],
            *data["actions"], *data["unresolved_items"], data["uncertainty"]]
    for row in rows:
        _append_refs(refs, list(row["evidence_refs"]))
    return refs


@dataclass(frozen=True)
class _VerifiedClaim:
    subject: str
    predicate: str
    object_value: str
    evidence_ids: tuple[str, ...]
    quote: str
    valid_on: str | None
    temporal_kind: str | None


@dataclass(frozen=True)
class _VerifiedClaimSet:
    workspace_id: str
    claims: tuple[_VerifiedClaim, ...]
    result_sha256: str
    temporal_claim: Mapping[str, Any] | None = None
    temporal_validator: Callable[..., Mapping[str, Any]] | None = field(
        default=None, repr=False, compare=False,
    )
    read_evidence_ids: frozenset[str] = frozenset()


def _result_digest(result: "TeamDecisionResult") -> str:
    encoded = json.dumps(
        result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_claim_proposal_shape(payload: object) -> tuple[_VerifiedClaim, ...]:
    required = {"schema_version", "claims"}
    if isinstance(payload, dict) and "uncertainty" in payload:
        required.add("uncertainty")
    if isinstance(payload, dict) and "temporal_decision" in payload:
        required.add("temporal_decision")
    proposal = _require_exact_keys(payload, required, "ClaimProposal")
    if proposal["schema_version"] != CLAIM_PROPOSAL_SCHEMA_VERSION:
        raise ValidationError("ClaimProposal schema version is invalid.")
    rows = proposal["claims"]
    if not isinstance(rows, list) or not 0 <= len(rows) <= 8:
        raise ValidationError("ClaimProposal claims must contain at most eight items.")
    if not rows and "uncertainty" not in proposal and "temporal_decision" not in proposal:
        raise ValidationError("ClaimProposal without claims requires explicit uncertainty.")
    if "uncertainty" in proposal:
        uncertainty = _require_exact_keys(
            proposal["uncertainty"], {"status", "unknown_fields"},
            "ClaimProposal uncertainty",
        )
        if uncertainty["status"] not in {"insufficient_evidence", "conflicting_evidence"}:
            raise ValidationError("ClaimProposal uncertainty status is invalid.")
        unknown = uncertainty["unknown_fields"]
        if (not isinstance(unknown, list) or not 1 <= len(unknown) <= 5
                or any(not isinstance(item, str) or not item.strip() or len(item) > 64
                       for item in unknown)
                or len(set(unknown)) != len(unknown)):
            raise ValidationError("ClaimProposal unknown_fields must contain bounded unique IDs.")
    claims: list[_VerifiedClaim] = []
    required = {
        "subject", "predicate", "object", "evidence_ids", "quote",
        "valid_on", "temporal_kind",
    }
    for row in rows:
        data = _require_exact_keys(row, required, "ClaimProposal claim")
        subject = _require_text(data["subject"], "ClaimProposal subject")
        object_value = _require_text(data["object"], "ClaimProposal object")
        if len(subject) > 128 or len(object_value) > 128:
            raise ValidationError("ClaimProposal entities exceed the supported length.")
        predicate = data["predicate"]
        if not isinstance(predicate, str) or predicate not in CLAIM_PROPOSAL_PREDICATES:
            raise ValidationError("ClaimProposal predicate is unsupported.")
        evidence_ids = _require_refs(data["evidence_ids"], "ClaimProposal evidence_ids", required=True)
        if len(evidence_ids) > 4 or any(len(ref) > 128 for ref in evidence_ids):
            raise ValidationError("ClaimProposal evidence_ids exceed the supported bounds.")
        quote = _require_text(data["quote"], "ClaimProposal quote")
        if len(quote) > 2048:
            raise ValidationError("ClaimProposal quote exceeds the supported length.")
        valid_on = data["valid_on"]
        if valid_on is not None:
            valid_on = _validate_deadline(valid_on, "ClaimProposal valid_on")
        temporal_kind = data["temporal_kind"]
        if temporal_kind is not None and (
            not isinstance(temporal_kind, str)
            or temporal_kind not in {"snapshot", "event"}
        ):
            raise ValidationError("ClaimProposal temporal_kind is unsupported.")
        if (valid_on is None) != (temporal_kind is None):
            raise ValidationError("ClaimProposal date and temporal_kind must be supplied together.")
        claims.append(_VerifiedClaim(
            subject=subject,
            predicate=predicate,
            object_value=object_value,
            evidence_ids=tuple(evidence_ids),
            quote=quote,
            valid_on=valid_on,
            temporal_kind=temporal_kind,
        ))
    return tuple(claims)


def _parse_temporal_decision_claim(payload: object) -> dict[str, Any]:
    keys = {
        "subject", "relation", "as_of", "decision_id", "receipt_id",
        "value", "supersedes_id", "evidence_ids", "prior_evidence_ids",
    }
    claim = _require_exact_keys(payload, keys, "ClaimProposal temporal_decision")
    for key in ("subject", "relation", "as_of", "decision_id", "receipt_id", "value"):
        if len(_require_text(claim[key], f"temporal_decision.{key}")) > (512 if key in {"subject", "relation", "value"} else 128):
            raise ValidationError(f"temporal_decision.{key} is too long.")
    try:
        moment = datetime.fromisoformat(claim["as_of"])
    except ValueError as error:
        raise ValidationError("temporal_decision.as_of must be an ISO timestamp.") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValidationError("temporal_decision.as_of must be timezone-aware.")
    predecessor = claim["supersedes_id"]
    if predecessor is not None and (
        not isinstance(predecessor, str) or not predecessor.strip() or len(predecessor) > 128
    ):
        raise ValidationError("temporal_decision.supersedes_id is invalid.")
    for key, required in (("evidence_ids", True), ("prior_evidence_ids", predecessor is not None)):
        refs = _require_refs(claim[key], f"temporal_decision.{key}", required=required)
        if len(refs) > 4 or any(len(ref) > 128 for ref in refs):
            raise ValidationError(f"temporal_decision.{key} exceeds the supported bounds.")
    if predecessor is None and claim["prior_evidence_ids"]:
        raise ValidationError("temporal_decision has prior evidence without a predecessor.")
    return dict(claim)


@dataclass(frozen=True)
class TeamDecisionResult:
    """A strict, JSON-safe business result; runtime validates only its boundary."""

    decision: dict[str, Any]
    rationale: list[dict[str, Any]]
    rejected_alternatives: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    unresolved_items: list[dict[str, Any]]
    uncertainty: dict[str, Any]
    evidence_refs: list[str]
    schema_version: str = SCHEMA_VERSION
    verified_claims: _VerifiedClaimSet | None = field(
        default=None, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("Unsupported TeamDecisionResult schema version.")

        decision = _require_exact_keys(
            self.decision, TEAM_DECISION_REQUIRED_FIELDS["$.decision"], "decision"
        )
        value = decision["value"]
        if value is not None:
            _require_text(value, "decision.value")
        if decision["status"] not in DECISION_STATUSES:
            raise ValidationError("decision.status is invalid.")
        if value is None and decision["status"] not in NULL_DECISION_STATUSES:
            raise ValidationError("A material decision status requires a decision value.")
        if value is not None and decision["status"] in NULL_DECISION_STATUSES:
            raise ValidationError("A null-decision status cannot contain a decision value.")
        _require_refs(decision["evidence_refs"], "decision.evidence_refs", required=value is not None)

        if not isinstance(self.rationale, list):
            raise ValidationError("rationale must be a list.")
        for index, item in enumerate(self.rationale):
            item = _require_exact_keys(
                item,
                TEAM_DECISION_REQUIRED_FIELDS["$.rationale[]"],
                f"rationale[{index}]",
            )
            _require_text(item["point"], f"rationale[{index}].point")
            _require_refs(item["evidence_refs"], f"rationale[{index}].evidence_refs", required=True)

        if not isinstance(self.rejected_alternatives, list):
            raise ValidationError("rejected_alternatives must be a list.")
        for index, item in enumerate(self.rejected_alternatives):
            item = _require_exact_keys(
                item,
                TEAM_DECISION_REQUIRED_FIELDS["$.rejected_alternatives[]"],
                f"rejected_alternatives[{index}]",
            )
            _require_text(item["alternative"], f"rejected_alternatives[{index}].alternative")
            _require_text(item["reason"], f"rejected_alternatives[{index}].reason")
            _require_refs(item["evidence_refs"], f"rejected_alternatives[{index}].evidence_refs", required=True)

        self._validate_action_items(self.actions, "actions", ACTION_STATUSES)
        self._validate_action_items(self.unresolved_items, "unresolved_items", UNRESOLVED_STATUSES)

        uncertainty = _require_exact_keys(
            self.uncertainty,
            TEAM_DECISION_REQUIRED_FIELDS["$.uncertainty"],
            "uncertainty",
        )
        if uncertainty["status"] not in UNCERTAINTY_STATUSES:
            raise ValidationError("uncertainty.status is invalid.")
        statement = uncertainty["statement"]
        if statement is not None:
            _require_text(statement, "uncertainty.statement")
        if not isinstance(uncertainty["unknown_fields"], list) or any(
            not isinstance(field, str) or not field for field in uncertainty["unknown_fields"]
        ):
            raise ValidationError("uncertainty.unknown_fields must be a list of non-empty strings.")
        _require_refs(
            uncertainty["evidence_refs"],
            "uncertainty.evidence_refs",
            required=statement is not None,
        )

        declared_refs = _require_refs(self.evidence_refs, "evidence_refs", required=False)
        if declared_refs != self.claim_evidence_refs():
            raise ValidationError("evidence_refs must equal the ordered union of material claim references.")

    @staticmethod
    def _validate_action_items(items: object, name: str, valid_statuses: set[str]) -> None:
        if not isinstance(items, list):
            raise ValidationError(f"{name} must be a list.")
        for index, item in enumerate(items):
            item = _require_exact_keys(
                item,
                TEAM_DECISION_REQUIRED_FIELDS[f"$.{name}[]"],
                f"{name}[{index}]",
            )
            _require_text(item["description"], f"{name}[{index}].description")
            _validate_owner(item["owner"], f"{name}[{index}].owner")
            _validate_deadline(item["deadline"], f"{name}[{index}].deadline")
            if item["status"] not in valid_statuses:
                raise ValidationError(f"{name}[{index}].status is invalid.")
            _require_refs(item["evidence_refs"], f"{name}[{index}].evidence_refs", required=True)

    @classmethod
    def from_dict(cls, data: object) -> "TeamDecisionResult":
        data = _require_exact_keys(
            data,
            TEAM_DECISION_REQUIRED_FIELDS["$"],
            "TeamDecisionResult",
        )
        return cls(
            schema_version=data["schema_version"],
            decision=data["decision"],
            rationale=data["rationale"],
            rejected_alternatives=data["rejected_alternatives"],
            actions=data["actions"],
            unresolved_items=data["unresolved_items"],
            uncertainty=data["uncertainty"],
            evidence_refs=data["evidence_refs"],
        )

    @classmethod
    def from_json(cls, text: object) -> "TeamDecisionResult":
        if not isinstance(text, str):
            raise ValidationError("TeamDecisionResult Final must be JSON text.")
        try:
            return cls.from_dict(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValidationError("TeamDecisionResult Final is not valid JSON.") from error

    @classmethod
    def from_grounded_final(
        cls,
        text: object,
        observed_evidence_refs: object,
        *,
        evidence_by_ref: Mapping[str, Mapping[str, Any]] | None = None,
        expected_workspace_id: str | None = None,
        require_claim_support: bool = False,
        final_protocol: str = SCHEMA_VERSION,
        temporal_validator: Callable[..., Mapping[str, Any]] | None = None,
        read_evidence_ids: Collection[str] = (),
        observed_temporal_decision_ids: Collection[str] = (),
        require_read_evidence: bool = False,
    ) -> "TeamDecisionResult":
        """Parse the explicitly selected protocol and verify it against observed evidence."""
        if final_protocol not in {SCHEMA_VERSION, CLAIM_PROPOSAL_SCHEMA_VERSION}:
            raise ValidationError("Unsupported Team Decision Final protocol.")
        observed = _require_refs(
            observed_evidence_refs,
            "observed_evidence_refs",
            required=False,
        )
        if not isinstance(text, str):
            raise ValidationError("TeamDecisionResult Final must be JSON text.")
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValidationError("TeamDecisionResult Final is not valid JSON.") from error
        if final_protocol == CLAIM_PROPOSAL_SCHEMA_VERSION:
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != CLAIM_PROPOSAL_SCHEMA_VERSION
            ):
                raise ValidationError(
                    "Final does not match the explicitly selected claim-proposal/v1 protocol."
                )
            return cls._from_claim_proposal(
                payload,
                observed,
                evidence_by_ref=evidence_by_ref,
                expected_workspace_id=expected_workspace_id,
                temporal_validator=temporal_validator,
                read_evidence_ids=read_evidence_ids,
                observed_temporal_decision_ids=observed_temporal_decision_ids,
                require_read_evidence=require_read_evidence,
            )
        if (isinstance(payload, dict)
                and payload.get("schema_version") == CLAIM_PROPOSAL_SCHEMA_VERSION):
            raise ValidationError(
                "ClaimProposal Final requires explicit claim-proposal/v1 selection."
            )
        try:
            result = cls.from_dict(payload)
        except ValidationError as error:
            if (error.message !=
                    "evidence_refs must equal the ordered union of material claim references."
                    or not isinstance(payload, dict)):
                raise
            declared_refs = _require_refs(
                payload.get("evidence_refs"), "evidence_refs", required=False,
            )
            canonical_refs = _claim_refs_from_validated_payload(payload)
            if len(declared_refs) != len(canonical_refs) or set(declared_refs) != set(canonical_refs):
                raise
            normalized = dict(payload)
            normalized["evidence_refs"] = canonical_refs
            result = cls.from_dict(normalized)
        if any(ref not in observed for ref in result.claim_evidence_refs()):
            raise ValidationError(
                "Team Decision claims reference evidence not observed by the model."
            )
        if require_read_evidence and any(
            ref not in set(read_evidence_ids) for ref in result.claim_evidence_refs()
        ):
            raise ValidationError(
                "Team Decision source claims must cite evidence returned by a successful read."
            )
        if expected_workspace_id is not None:
            if not isinstance(expected_workspace_id, str) or not expected_workspace_id.strip():
                raise ValidationError("Expected Team Decision workspace ID is invalid.")
            if evidence_by_ref is None:
                raise ValidationError("Workspace-scoped Final validation requires verified evidence.")
            for ref in result.claim_evidence_refs():
                evidence = evidence_by_ref.get(ref)
                if (not isinstance(evidence, Mapping)
                        or evidence.get("evidence_id") != ref
                        or evidence.get("status") != "verified"
                        or evidence.get("workspace_id") != expected_workspace_id):
                    raise ValidationError(
                        "Team Decision claims must cite verified evidence in the active workspace."
                    )
        if require_claim_support:
            if evidence_by_ref is None:
                raise ValidationError("Team Decision claim support requires verified evidence.")
            result.validate_claim_support(evidence_by_ref)
        return result

    @classmethod
    def _from_claim_proposal(
        cls,
        payload: object,
        observed: list[str],
        *,
        evidence_by_ref: Mapping[str, Mapping[str, Any]] | None,
        expected_workspace_id: str | None,
        temporal_validator: Callable[..., Mapping[str, Any]] | None,
        read_evidence_ids: Collection[str],
        observed_temporal_decision_ids: Collection[str],
        require_read_evidence: bool,
    ) -> "TeamDecisionResult":
        if (not isinstance(expected_workspace_id, str)
                or not expected_workspace_id.strip()
                or evidence_by_ref is None):
            raise ValidationError(
                "ClaimProposal requires verified evidence in the active workspace."
            )
        claims = _parse_claim_proposal_shape(payload)
        uncertainty = payload.get("uncertainty") if isinstance(payload, dict) else None
        refs: list[str] = []
        clauses: list[str] = []
        temporal_claim = None
        if isinstance(payload, dict) and "temporal_decision" in payload:
            if claims:
                raise ValidationError(
                    "Temporal Decision Final must keep unrelated source claims in a separate run."
                )
            temporal_claim = _parse_temporal_decision_claim(payload["temporal_decision"])
            if temporal_validator is None:
                raise ValidationError("Temporal Decision Final requires a trusted product validator.")
            if temporal_claim["decision_id"] not in observed_temporal_decision_ids:
                raise ValidationError("Temporal Decision Final was not observed in a reviewed lookup.")
            for evidence_id in [*temporal_claim["evidence_ids"],
                                *temporal_claim["prior_evidence_ids"]]:
                if evidence_id not in observed or evidence_id not in read_evidence_ids:
                    raise ValidationError("Temporal Decision Final must cite an observed source read.")
            recovery = temporal_validator(
                expected_workspace_id, temporal_claim, evidence_by_ref,
                set(read_evidence_ids),
            )
            effective = recovery["effective"]
            clause = (
                f"截至 {temporal_claim['as_of']}，{temporal_claim['subject']}的"
                f"{temporal_claim['relation']}为{effective['value']}"
            )
            clauses.append(clause)
            for evidence_id in temporal_claim["evidence_ids"]:
                _append_refs(refs, [evidence_id])
            if temporal_claim["supersedes_id"] is not None:
                prior = next(item for item in recovery["history"]
                             if item["decision_id"] == temporal_claim["supersedes_id"])
                clauses.append(
                    f"{effective['value']}于{effective['valid_from']}替代{prior['value']}"
                )
                for evidence_id in temporal_claim["prior_evidence_ids"]:
                    _append_refs(refs, [evidence_id])
        for claim in claims:
            if claim.predicate == "owner_of_project":
                clause = (
                    f"截至 {claim.valid_on}，{claim.subject}的负责人是{claim.object_value}"
                    if claim.temporal_kind == "snapshot"
                    else f"在 {claim.valid_on}，{claim.subject}的负责人是{claim.object_value}"
                    if claim.temporal_kind == "event"
                    else f"{claim.subject}的负责人是{claim.object_value}"
                )
            elif claim.predicate == "alias_of_project":
                clause = f"{claim.subject}是{claim.object_value}的别名"
            else:
                relation = f"{claim.subject}直接向{claim.object_value}汇报"
                clause = (
                    f"截至 {claim.valid_on}，{relation}"
                    if claim.temporal_kind == "snapshot"
                    else f"在 {claim.valid_on}，{relation}"
                    if claim.temporal_kind == "event"
                    else relation
                )
            clauses.append(clause)
            for evidence_id in claim.evidence_ids:
                if evidence_id not in observed:
                    raise ValidationError(
                        "ClaimProposal references evidence not observed by the model."
                    )
                evidence = evidence_by_ref.get(evidence_id)
                if require_read_evidence and evidence_id not in set(read_evidence_ids):
                    raise ValidationError(
                        "ClaimProposal source claims must cite evidence returned by a successful read."
                    )
                source_quote = evidence.get("quote") if isinstance(evidence, Mapping) else None
                if (
                    not isinstance(evidence, Mapping)
                    or evidence.get("evidence_id") != evidence_id
                    or evidence.get("status") != "verified"
                    or evidence.get("workspace_id") != expected_workspace_id
                    or not isinstance(source_quote, str)
                    or claim.quote not in source_quote
                ):
                    raise ValidationError(
                        "ClaimProposal evidence must be an exact quote from verified active-workspace evidence."
                    )
                exact_quote_evidence = dict(evidence)
                exact_quote_evidence["quote"] = claim.quote
                if not evidence_supports_typed_claim(
                    exact_quote_evidence,
                    predicate=claim.predicate,
                    subject=claim.subject,
                    object_value=claim.object_value,
                    valid_on=claim.valid_on,
                    temporal_kind=claim.temporal_kind,
                ):
                    raise ValidationError(
                        "ClaimProposal relation or temporal scope is not supported by its quote."
                    )
                _append_refs(refs, [evidence_id])

        incomplete = uncertainty is not None
        temporal_partial = incomplete and temporal_claim is not None
        result = cls(
            decision={
                "value": None if incomplete and not temporal_partial else "；".join(clauses),
                "status": "partial" if temporal_partial else "insufficient_evidence" if incomplete else "approved",
                "evidence_refs": [] if incomplete and not temporal_partial else refs,
            },
            rationale=(
                [{"point": clause, "evidence_refs": list(claim.evidence_ids)}
                 for clause, claim in zip(clauses, claims)]
                if incomplete and not temporal_partial else []
            ),
            rejected_alternatives=[],
            actions=[],
            unresolved_items=[],
            uncertainty={
                "status": uncertainty["status"] if incomplete else "none",
                "statement": None,
                "unknown_fields": list(uncertainty["unknown_fields"]) if incomplete else [],
                "evidence_refs": [],
            },
            evidence_refs=list(refs),
        )
        verified = _VerifiedClaimSet(
            workspace_id=expected_workspace_id,
            claims=claims,
            result_sha256=_result_digest(result),
            temporal_claim=temporal_claim,
            temporal_validator=temporal_validator,
            read_evidence_ids=frozenset(read_evidence_ids),
        )
        return replace(result, verified_claims=verified)

    def validate_claim_support(
        self,
        evidence_by_ref: Mapping[str, Mapping[str, Any]],
        *,
        question: str | None = None,
        observed_evidence_by_ref: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        """Fail closed unless every supported product-slice assertion is grounded."""
        if self.verified_claims is not None:
            if _result_digest(self) != self.verified_claims.result_sha256:
                raise ValidationError("Verified ClaimProposal result changed after validation.")
            if self.verified_claims.temporal_claim is not None:
                validator = self.verified_claims.temporal_validator
                if validator is None:
                    raise ValidationError("Temporal Decision validator is unavailable before review.")
                validator(
                    self.verified_claims.workspace_id,
                    dict(self.verified_claims.temporal_claim),
                    evidence_by_ref,
                    set(self.verified_claims.read_evidence_ids),
                )
            for claim in self.verified_claims.claims:
                for evidence_id in claim.evidence_ids:
                    evidence = evidence_by_ref.get(evidence_id)
                    source_quote = evidence.get("quote") if isinstance(evidence, Mapping) else None
                    if (
                        not isinstance(evidence, Mapping)
                        or evidence.get("evidence_id") != evidence_id
                        or evidence.get("status") != "verified"
                        or evidence.get("workspace_id") != self.verified_claims.workspace_id
                        or not isinstance(source_quote, str)
                        or claim.quote not in source_quote
                    ):
                        raise ValidationError(
                            "Verified ClaimProposal evidence changed before review."
                        )
                    # Preserve the full read scope when checking known conflict;
                    # a model-selected substring cannot discard project or time.
                    if not evidence_supports_typed_claim(
                        evidence,
                        predicate=claim.predicate,
                        subject=claim.subject,
                        object_value=claim.object_value,
                        valid_on=claim.valid_on,
                        temporal_kind=claim.temporal_kind,
                        observed_evidence_by_ref=(observed_evidence_by_ref
                            if self.decision["status"] in {"approved", "partial"} else None),
                    ):
                        raise ValidationError(
                            "Verified ClaimProposal relation no longer matches its evidence."
                        )
            return
        claims: list[tuple[str, list[str], str | None]] = []
        if isinstance(self.decision["value"], str):
            claims.append((self.decision["value"], self.decision["evidence_refs"], question))
        claims.extend(
            (item["point"], item["evidence_refs"], None) for item in self.rationale
        )
        if self.actions or self.unresolved_items:
            raise ValidationError(
                "Team Decision action claims are outside the verified relation vocabulary."
            )
        if isinstance(self.uncertainty["statement"], str):
            claims.append((self.uncertainty["statement"], self.uncertainty["evidence_refs"], None))

        for text, refs, claim_question in claims:
            assessment = assess_claim_text(
                text, refs, evidence_by_ref, question=claim_question,
                observed_evidence_by_ref=observed_evidence_by_ref,
            )
            if assessment.status is not ClaimSupportStatus.SUPPORTED:
                raise ValidationError(
                    "Team Decision contains a claim without deterministic source support."
                )
        for item in self.rejected_alternatives:
            if not isinstance(self.decision["value"], str) or (
                assess_rejected_owner_alternative(
                    item["alternative"], item["reason"], item["evidence_refs"],
                    self.decision["value"], evidence_by_ref,
                    alias_evidence_refs=self.decision["evidence_refs"],
                ) is not ClaimSupportStatus.SUPPORTED
            ):
                raise ValidationError(
                    "Team Decision rejected alternative lacks historical and current support."
                )

    def claim_evidence_refs(self) -> list[str]:
        refs: list[str] = []
        _append_refs(refs, list(self.decision["evidence_refs"]))
        for item in self.rationale:
            _append_refs(refs, list(item["evidence_refs"]))
        for item in self.rejected_alternatives:
            _append_refs(refs, list(item["evidence_refs"]))
        for item in self.actions:
            _append_refs(refs, list(item["evidence_refs"]))
        for item in self.unresolved_items:
            _append_refs(refs, list(item["evidence_refs"]))
        _append_refs(refs, list(self.uncertainty["evidence_refs"]))
        return refs

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision,
            "rationale": self.rationale,
            "rejected_alternatives": self.rejected_alternatives,
            "actions": self.actions,
            "unresolved_items": self.unresolved_items,
            "uncertainty": self.uncertainty,
            "evidence_refs": self.evidence_refs,
        }
