"""Typed, read-only tools exposed to P4 specialists."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any, Callable

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import _assert_json_safe_primitive
from linkloom.tools.tool_policy import ToolPolicyEnforcer


def _require_callback(callback: Any, field_name: str) -> None:
    if not callable(callback):
        raise ValidationError(f"{field_name} must be callable.")


def _require_relative_note_ref(note_ref: Any, field_name: str = "note_ref") -> str:
    if not isinstance(note_ref, str) or not note_ref.strip():
        raise ValidationError(f"{field_name} must be a non-empty string.")
    normalized = note_ref.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if (
        Path(note_ref).is_absolute()
        or PureWindowsPath(note_ref).is_absolute()
        or normalized.startswith("/")
        or ".." in parts
        or any("gold" in part.casefold() for part in parts)
    ):
        raise ValidationError(f"{field_name} must be a verified relative note reference.")
    return note_ref


def _require_json_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must be a JSON object.")
    _assert_json_safe_primitive(value, field_name)
    return value


def _require_json_result(value: Any, field_name: str, expected_type: type) -> Any:
    if not isinstance(value, expected_type):
        raise ValidationError(f"{field_name} returned an unexpected type.")
    _assert_json_safe_primitive(value, field_name)
    return value


def search_notes(
    query: str,
    source_context: dict[str, Any],
    limit: int,
    policy_enforcer: ToolPolicyEnforcer,
    retrieval_func: Callable[[str, dict[str, Any], int], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Search only through an injected, already-authorized retrieval function."""
    if not isinstance(query, str) or not query.strip():
        raise ValidationError("query must be a non-empty string.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 50:
        raise ValidationError("limit must be an integer between 1 and 50.")
    _require_json_dict(source_context, "source_context")
    _require_callback(retrieval_func, "retrieval_func")
    policy_enforcer.authorize_call("search_notes")
    return _require_json_result(retrieval_func(query, source_context, limit), "search_notes result", list)


def read_verified_note(
    note_ref: str,
    policy_enforcer: ToolPolicyEnforcer,
    reader_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Read one note through an injected VaultReader-backed function."""
    _require_relative_note_ref(note_ref)
    _require_callback(reader_func, "reader_func")
    policy_enforcer.authorize_call("read_verified_note")
    return _require_json_result(reader_func(note_ref), "read_verified_note result", dict)


def build_pair_signals(
    left_ref: str,
    right_ref: str,
    policy_enforcer: ToolPolicyEnforcer,
    curator_func: Callable[[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build structural candidate signals without deciding a relation fact."""
    _require_relative_note_ref(left_ref, "left_ref")
    _require_relative_note_ref(right_ref, "right_ref")
    if left_ref == right_ref:
        raise ValidationError("left_ref and right_ref must identify different notes.")
    _require_callback(curator_func, "curator_func")
    policy_enforcer.authorize_call("build_pair_signals")
    return _require_json_result(curator_func(left_ref, right_ref), "build_pair_signals result", list)


def validate_evidence(
    evidence_ref: str,
    policy_enforcer: ToolPolicyEnforcer,
    validate_func: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Validate an evidence reference through the Reader/policy boundary."""
    _require_relative_note_ref(evidence_ref, "evidence_ref")
    _require_callback(validate_func, "validate_func")
    policy_enforcer.authorize_call("validate_evidence")
    return _require_json_result(validate_func(evidence_ref), "validate_evidence result", dict)


def validate_schema(
    result_type: str,
    payload: dict[str, Any],
    policy_enforcer: ToolPolicyEnforcer,
    validate_func: Callable[[str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Validate a typed specialist result without accepting Gold/expected labels."""
    if not isinstance(result_type, str) or not result_type.strip():
        raise ValidationError("result_type must be a non-empty string.")
    _require_json_dict(payload, "payload")
    if any(key.casefold() in {"gold", "expected", "label"} for key in payload):
        raise ValidationError("validate_schema payload cannot contain Gold or expected labels.")
    _require_callback(validate_func, "validate_func")
    policy_enforcer.authorize_call("validate_schema")
    return _require_json_result(validate_func(result_type, payload), "validate_schema result", dict)
