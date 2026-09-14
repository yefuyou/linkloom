"""Strict provider-neutral contract for M1.1 Team Decision results."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any

from linkloom.runtime.errors import ValidationError


SCHEMA_VERSION = "team-decision-result/v1"
DECISION_STATUSES = {"approved", "partial", "insufficient_evidence", "not_found"}
NULL_DECISION_STATUSES = {"insufficient_evidence", "not_found"}
ACTION_STATUSES = {"pending", "in_progress", "blocked", "completed", "unassigned"}
UNRESOLVED_STATUSES = {"pending", "in_progress", "blocked", "unassigned"}
UNCERTAINTY_STATUSES = {"none", "partial", "insufficient_evidence", "conflicting_evidence"}
NON_WHITESPACE_TEXT_PATTERN = r"[\s\S]*\S[\s\S]*"
ISO_DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

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


def team_decision_final_instruction() -> str:
    """Describe the strict Final boundary without provider or case-specific content."""

    schema = json.dumps(
        team_decision_final_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "When the observed evidence is sufficient, do not call another tool. Return Final as "
        "exactly one complete JSON object. Every field listed in each required array is mandatory, "
        "including every nested field, even when its value is empty or null. For an array with no "
        "items, output []; for a nullable field with no supported value, output null. In particular, "
        'uncertainty must always contain "unknown_fields"; when none are unknown, output exactly '
        '"unknown_fields":[] . Do not wrap the JSON in Markdown fences. Do not write any explanation '
        "before or after the JSON. Final is the normal text response and must not be a function call. "
        "Use only observed verified evidence ids. Every material claim needs non-empty evidence_refs, "
        "and top-level evidence_refs must be the ordered deduplicated union of all nested claim refs.\n"
        f"team_decision_final_schema={schema}"
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
    ) -> "TeamDecisionResult":
        """Parse a model Final only when every claim cites observed evidence."""
        observed = _require_refs(
            observed_evidence_refs,
            "observed_evidence_refs",
            required=False,
        )
        result = cls.from_json(text)
        if any(ref not in observed for ref in result.claim_evidence_refs()):
            raise ValidationError(
                "Team Decision claims reference evidence not observed by the model."
            )
        return result

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
