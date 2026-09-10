"""Closed, versioned, JSON-safe trajectory evaluation contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping

from .fixture import validate_evidence_reference, validate_fixture_reference


CASE_SCHEMA_VERSION = "trajectory-case/v1"
OBSERVATION_SCHEMA_VERSION = "trajectory-observation/v1"
RESULT_SCHEMA_VERSION = "trajectory-result/v1"

TRAJECTORY_CATEGORIES = frozenset(
    {
        "routing",
        "tool_selection",
        "malformed_arguments",
        "permission_denied",
        "budget_exhausted",
        "tool_execution_failure",
        "invalid_output",
        "NOT_FOUND",
        "partial_retrieval_failure",
        "repeated_tool_call",
        "loop",
        "premature_termination",
        "checkpoint_pending_ambiguity",
        "stale_memory",
        "conflicting_memory",
    }
)

NONDETERMINISTIC_KEYS = frozenset(
    {
        "timestamp",
        "timestamps",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "generated_at",
        "run_id",
    }
)

CASE_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "category",
        "synthetic_input",
        "policy_budget",
        "fault_injection",
        "expected_trajectory",
        "expected_tool_calls",
        "expected_tool_results",
        "expected_termination",
        "expected_attribution",
        "deterministic_assertions",
        "optional_judge_rubric",
        "required_capabilities",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "trajectory",
        "tool_calls",
        "tool_results",
        "termination",
        "attribution",
        "execution",
        "metadata",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "case_id",
        "category",
        "status",
        "assertions",
        "failed_assertion",
        "expected",
        "actual",
        "primary_failure_attribution",
        "missing_capability",
        "judge",
    }
)
EXECUTION_FIELDS = frozenset(
    {
        "executor_invocations",
        "suppressed_call_ids",
        "budget",
        "ledger",
        "evidence_refs",
        "denied_accesses",
        "recovery_decisions",
        "signals",
    }
)

TOOL_CALL_FIELDS = frozenset({"call_id", "tool_id", "arguments", "sequence"})
TOOL_RESULT_FIELDS = frozenset(
    {"call_id", "tool_id", "status", "value", "business_status", "error"}
)
TOOL_ERROR_FIELDS = frozenset(
    {"code", "category", "message", "retryable", "safe_to_expose"}
)
ASSERTION_FIELDS = frozenset({"assertion", "expected"})
ASSERTION_RESULT_FIELDS = frozenset({"assertion", "passed", "expected", "actual"})

TOOL_RESULT_STATUSES = frozenset({"ok", "error"})
BUSINESS_STATUSES = frozenset({"FOUND", "NOT_FOUND"})
LEDGER_STATES = frozenset({"pending", "completed", "failed"})
RECOVERY_DECISIONS = frozenset(
    {"safe_to_retry", "requires_verification", "requires_manual_decision", "non_retryable"}
)
TOOL_ERROR_CATEGORIES = frozenset({"input", "permission", "budget", "runtime", "schema"})
TOOL_ERROR_CODES = frozenset(
    {
        "TOOL_INVALID_ARGUMENTS",
        "TOOL_PERMISSION_DENIED",
        "TOOL_BUDGET_EXCEEDED",
        "TOOL_EXECUTION_FAILED",
        "TOOL_PENDING_CHECKPOINT_FAILED",
        "TOOL_TERMINAL_CHECKPOINT_FAILED",
        "TOOL_INVALID_OUTPUT",
        "TOOL_LEDGER_CONFLICT",
        "REPEATED_TOOL_CALL",
        "LOOP_DETECTED",
    }
)
REGISTERED_TOOL_IDS = frozenset(
    {
        "search_notes",
        "read_verified_note",
        "write_file",
        "read_gold",
        "raw_filesystem",
        "side_effect_stub",
    }
)


def _fields(*names: str) -> frozenset[str]:
    return frozenset(names)


FAULT_INJECTION_REGISTRY: dict[str, frozenset[str]] = {
    kind: _fields("kind")
    for kind in (
        "none",
        "unsafe_write_request",
        "ambiguous_request",
        "canonical_equivalent_arguments",
        "empty_search_results",
        "zero_budget",
        "second_call_after_budget",
        "one_valid_one_not_found",
        "semantic_repeat_unique_ids",
        "repeated_failing_call",
        "final_before_retrieval",
        "final_after_search_before_read",
        "observe_pending_snapshot",
    )
}
FAULT_INJECTION_REGISTRY.update(
    {
        "forbidden_resource_request": _fields("kind", "resource"),
        "missing_required_argument": _fields("kind", "field"),
        "wrong_argument_type": _fields("kind", "field", "actual_type"),
        "traversal_and_extra_argument": _fields("kind", "fields"),
        "denied_tool_call": _fields("kind", "tool_id"),
        "executor_exception": _fields("kind", "tool_id", "exception"),
        "pending_checkpoint_failure": _fields("kind", "checkpoint"),
        "terminal_checkpoint_failure": _fields("kind", "checkpoint"),
        "invalid_executor_output": _fields(
            "kind", "tool_id", "raw_output", "expected_shape"
        ),
        "mismatched_result_identity": _fields(
            "kind", "returned_call_id", "returned_tool_id"
        ),
        "absent_note": _fields("kind", "note_path"),
        "one_executor_failure_one_valid": _fields("kind", "failed_path"),
        "one_invalid_output_one_valid": _fields("kind", "invalid_path"),
        "duplicate_call_id": _fields("kind", "call_id"),
        "cyclic_tool_pattern": _fields("kind", "pattern"),
        "resume_pending_call": _fields("kind", "preexisting_status"),
        "pending_read_recovery": _fields("kind", "preexisting_status"),
        "pending_unknown_side_effect": _fields("kind", "preexisting_status"),
        "expired_memory": _fields("kind", "memory"),
        "stale_source_fingerprint": _fields(
            "kind", "memory_fingerprint", "current_fingerprint"
        ),
        "contradictory_active_memories": _fields("kind", "memories"),
        "memory_policy_conflict": _fields("kind", "memory", "current_policy"),
    }
)

TRAJECTORY_EVENT_REGISTRY: dict[str, tuple[frozenset[str], ...]] = {
    "route_selected": (_fields("event", "route"),),
    "tool_requested": (
        _fields("event", "tool_id"),
        _fields("event", "call_id", "tool_id"),
    ),
    "final_answer": (
        _fields("event", "evidence_refs"),
        _fields("event", "claim"),
    ),
    "permission_refusal": (_fields("event", "resource"),),
    "clarification_requested": (
        _fields("event", "missing_detail"),
        _fields("event", "topic"),
    ),
    "tool_selected": (_fields("event", "tool_id"),),
    "retrieval_returned": (_fields("event", "count"),),
    "note_returned": (_fields("event", "note_path"),),
    "forbidden_intent_detected": (_fields("event", "resource"),),
    "tool_selection_suppressed": (_fields("event", "forbidden_tool"),),
    "argument_validation_failed": (
        _fields("event", "field", "executor_called"),
    ),
    "permission_denied": (_fields("event", "tool_id", "executor_called"),),
    "budget_denied": (
        _fields("event", "used", "remaining", "executor_called"),
    ),
    "tool_executed": (
        _fields("event", "tool_id"),
        _fields("event", "call_id"),
        _fields("event", "tool_id", "budget_used"),
    ),
    "ledger_transition": (
        _fields("event", "from", "to"),
        _fields("event", "call_id", "from", "to"),
    ),
    "executor_started": (
        _fields("event", "tool_id"),
        _fields("event", "execution_count"),
    ),
    "executor_completed": (_fields("event", "tool_id"),),
    "checkpoint_write_failed": (
        _fields("event", "record_status"),
        _fields("event", "record_status", "executor_called"),
    ),
    "output_validation_failed": (
        _fields("event", "expected_shape", "actual_shape"),
        _fields("event", "call_id", "actual_shape"),
    ),
    "output_identity_mismatch": (
        _fields("event", "expected_call_id", "actual_call_id"),
    ),
    "business_status_observed": (_fields("event", "business_status"),),
    "tool_result_observed": (
        _fields("event", "call_id", "business_status"),
        _fields("event", "call_id", "status"),
    ),
    "partial_answer": (_fields("event", "evidence_refs"),),
    "semantic_repeat_detected": (
        _fields("event", "call_id", "matches", "executor_called"),
    ),
    "ledger_conflict": (_fields("event", "reason", "executor_called"),),
    "loop_condition_detected": (
        _fields("event", "pattern_length", "executor_called"),
        _fields("event", "failure_signature", "executor_called"),
    ),
    "tool_failed": (_fields("event", "call_id"),),
    "final_attempted": (_fields("event", "evidence_refs"),),
    "premature_final_blocked": (_fields("event", "missing_step"),),
    "call_authorized": (_fields("event", "call_id"),),
    "checkpoint_saved": (_fields("event", "record_status"),),
    "checkpoint_loaded": (_fields("event", "record_status"),),
    "resume_decision": (_fields("event", "decision"),),
    "side_effect_classified": (_fields("event", "kind"),),
    "recovery_recommendation": (_fields("event", "decision"),),
    "memory_considered": (
        _fields("event", "status"),
        _fields("event", "fingerprint_match"),
    ),
    "memory_ignored": (_fields("event", "reason"),),
    "memory_rejected": (_fields("event", "reason"),),
    "memory_conflict_detected": (_fields("event", "memory_ids"),),
    "automatic_choice_suppressed": (_fields("event"),),
    "memory_policy_conflict_detected": (_fields("event", "resource"),),
    "current_policy_selected": (_fields("event", "gold_access"),),
    "tool_result": (
        _fields("event", "call_id", "status", "error_code"),
    ),
}

ASSERTION_REGISTRY: dict[str, str] = {
    "observation_present": "bool",
    "case_id": "text",
    "route": "text",
    "tool_ids": "text_or_text_list",
    "canonical_arguments": "object_or_object_list",
    "tool_sequence": "text_or_text_list",
    "executor_suppressed": "bool",
    "executor_count": "non_negative_int",
    "budget_used": "non_negative_int",
    "tool_result_status": "status_or_status_list",
    "tool_result_error_code": "error_code_or_list",
    "business_status": "business_status_or_list",
    "termination_status": "termination_status",
    "ledger_transition": "ledger_state_or_list",
    "repeated_call_detected": "bool",
    "loop_condition": "loop_condition",
    "premature_termination_blocked": "bool",
    "pending_recommendation": "recovery_decision",
    "evidence_refs_valid": "bool",
    "denied_resource_access": "bool",
    "safe_error_exposure": "bool",
    "stale_memory_ignored": "bool",
    "memory_conflict_detected": "bool",
    "attribution": "attribution",
}

ATTRIBUTION_TAXONOMY = frozenset(
    {
        ("budget_enforcer", "authorization"),
        ("caller_argument_construction", "argument_validation"),
        ("checkpoint_protocol", "pre_execution_durability"),
        ("checkpoint_protocol", "resume_recovery"),
        ("checkpoint_store", "post_execution_durability"),
        ("checkpoint_store", "pre_execution_durability"),
        ("executor", "tool_execution"),
        ("ledger_conflict", "pending_record"),
        ("loop_guard", "trajectory_guard"),
        ("memory_conflict_resolution", "memory_selection"),
        ("memory_freshness", "fingerprint_validation"),
        ("memory_freshness", "memory_selection"),
        ("policy_enforcer", "authorization"),
        ("policy_precedence", "memory_policy_resolution"),
        ("premature_final_guard", "termination_guard"),
        ("recovery_policy", "pending_classification"),
        ("repeat_guard", "pre_execution_guard"),
        ("retrieval_branch", "partial_result_aggregation"),
        ("retrieval_business_semantics", "tool_result_interpretation"),
        ("routing_decision", "route_selection"),
        ("routing_decision", "safety_routing"),
        ("tool_output_validator", "result_identity_validation"),
        ("tool_output_validator", "result_validation"),
        ("tool_selection", "model_action"),
    }
)
ATTRIBUTION_SIGNAL_PRIORITY: tuple[
    tuple[str, tuple[str, str]], ...
] = (
    ("termination.partial", ("retrieval_branch", "partial_result_aggregation")),
    ("recovery.present", ("recovery_policy", "pending_classification")),
    (
        "error.TOOL_TERMINAL_CHECKPOINT_FAILED",
        ("checkpoint_store", "post_execution_durability"),
    ),
    (
        "error.TOOL_PENDING_CHECKPOINT_FAILED",
        ("checkpoint_store", "pre_execution_durability"),
    ),
    ("error.TOOL_LEDGER_CONFLICT", ("ledger_conflict", "pending_record")),
    (
        "error.TOOL_INVALID_ARGUMENTS",
        ("caller_argument_construction", "argument_validation"),
    ),
    ("error.TOOL_PERMISSION_DENIED", ("policy_enforcer", "authorization")),
    ("error.TOOL_BUDGET_EXCEEDED", ("budget_enforcer", "authorization")),
    (
        "event.output_identity_mismatch",
        ("tool_output_validator", "result_identity_validation"),
    ),
    (
        "event.output_validation_failed",
        ("tool_output_validator", "result_validation"),
    ),
    (
        "error.TOOL_INVALID_OUTPUT",
        ("tool_output_validator", "result_validation"),
    ),
    ("error.TOOL_EXECUTION_FAILED", ("executor", "tool_execution")),
    (
        "business.NOT_FOUND",
        ("retrieval_business_semantics", "tool_result_interpretation"),
    ),
    (
        "event.checkpoint_saved",
        ("checkpoint_protocol", "pre_execution_durability"),
    ),
)
FAILURE_ATTRIBUTIONS = frozenset(
    {primary for primary, _stage in ATTRIBUTION_TAXONOMY} | {"evaluation_input"}
)

_TERMINATION_SIMPLE: dict[str, tuple[str, frozenset[str]]] = {}
for _reason in (
    "evidence_answered",
    "exact_note_read",
    "broad_search_completed",
    "not_found_reported",
    "final_after_required_retrieval",
    "final_after_verified_read",
    "pending_boundary_observed",
    "expired_memory_ignored",
    "source_refreshed",
):
    _TERMINATION_SIMPLE[_reason] = ("completed", _fields("status", "reason"))
for _reason in (
    "write_not_allowed",
    "gold_access_denied",
    "permission_denied",
    "raw_filesystem_denied",
    "current_policy_overrides_memory",
):
    _TERMINATION_SIMPLE[_reason] = ("refused", _fields("status", "reason"))
for _reason in ("ambiguous_intent", "conflicting_active_memories"):
    _TERMINATION_SIMPLE[_reason] = (
        "needs_clarification",
        _fields("status", "reason"),
    )
for _reason in (
    "malformed_arguments",
    "tool_execution_failure",
    "invalid_tool_output",
    "result_identity_mismatch",
    "semantic_repeat_detected",
    "ledger_conflict",
    "canonical_repeat_detected",
    "cyclic_tool_loop",
    "repeated_failure_loop",
):
    _TERMINATION_SIMPLE[_reason] = ("safe_error", _fields("status", "reason"))

TERMINATION_REGISTRY = {
    **_TERMINATION_SIMPLE,
    "zero_budget": ("budget_exhausted", _fields("status", "reason", "calls_used")),
    "second_call_denied": ("budget_exhausted", _fields("status", "reason", "calls_used")),
    "pending_checkpoint_failure": (
        "safe_error",
        _fields("status", "reason", "ledger_status"),
    ),
    "terminal_checkpoint_failure": (
        "durability_uncertain",
        _fields("status", "reason", "executor_completed"),
    ),
    "one_branch_not_found": (
        "partial",
        _fields("status", "reason", "valid_evidence_preserved"),
    ),
    "one_branch_failed": (
        "partial",
        _fields("status", "reason", "valid_evidence_preserved"),
    ),
    "one_branch_invalid": (
        "partial",
        _fields("status", "reason", "valid_evidence_preserved"),
    ),
    "resumed_exactly_once": (
        "completed",
        _fields("status", "reason", "execution_count"),
    ),
    "pending_read": (
        "recovery_decision",
        _fields("status", "reason", "recommendation"),
    ),
    "unknown_side_effect": (
        "recovery_decision",
        _fields("status", "reason", "recommendation"),
    ),
}
TERMINATION_STATUSES = frozenset(status for status, _ in TERMINATION_REGISTRY.values())


def _ensure_json_safe(value: Any, field_name: str = "value") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be JSON-safe")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_safe(item, f"{field_name}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} must be JSON-safe")
            _ensure_json_safe(item, f"{field_name}.{key}")
        return
    raise ValueError(f"{field_name} must be JSON-safe")


def _require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    _ensure_json_safe(value, field_name)
    return value


def _require_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    _ensure_json_safe(value, field_name)
    return value


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _require_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _require_registered_tool_id(value: Any, field_name: str) -> str:
    tool_id = _require_text(value, field_name)
    if tool_id not in REGISTERED_TOOL_IDS:
        raise ValueError(f"{field_name} is not a registered tool_id")
    return tool_id


def _require_text_list(value: Any, field_name: str, *, allow_empty: bool = True) -> list[str]:
    values = _require_list(value, field_name)
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    for index, item in enumerate(values):
        _require_text(item, f"{field_name}[{index}]")
    return values


def _require_exact_fields(
    data: Mapping[str, Any], expected_fields: frozenset[str], contract_name: str
) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{contract_name} must be a JSON object")
    actual_fields = set(data)
    missing = sorted(expected_fields - actual_fields)
    unknown = sorted(actual_fields - expected_fields)
    if missing:
        raise ValueError(f"{contract_name} missing required fields: {missing}")
    if unknown:
        raise ValueError(f"{contract_name} has unknown fields: {unknown}")


def _validate_evidence_reference_shape(value: Any, field_name: str) -> None:
    try:
        validate_evidence_reference(_require_text(value, field_name))
    except ValueError as exc:
        raise ValueError(f"{field_name} has invalid evidence reference: {exc}") from exc


def _validate_judge(value: Any, field_name: str, *, require_criteria: bool) -> None:
    data = _require_mapping(value, field_name)
    expected = _fields("status", "provider", "criteria") if require_criteria else _fields("status", "provider")
    _require_exact_fields(data, expected, field_name)
    if data["status"] != "NOT_EVALUATED":
        raise ValueError(f"{field_name}.status must be NOT_EVALUATED")
    if data["provider"] is not None:
        raise ValueError(f"{field_name}.provider must be null")
    if require_criteria:
        _require_text_list(data["criteria"], f"{field_name}.criteria", allow_empty=False)


def validate_fault_injection(value: Any, field_name: str = "fault_injection") -> None:
    data = _require_mapping(value, field_name)
    kind = _require_text(data.get("kind"), f"{field_name}.kind")
    if kind not in FAULT_INJECTION_REGISTRY:
        raise ValueError(f"{field_name}.kind is not registered")
    _require_exact_fields(data, FAULT_INJECTION_REGISTRY[kind], field_name)
    for name in (
        "resource", "field", "actual_type", "tool_id", "exception", "checkpoint",
        "expected_shape", "returned_call_id", "returned_tool_id", "call_id",
        "preexisting_status", "memory_fingerprint", "current_fingerprint",
    ):
        if name in data:
            _require_text(data[name], f"{field_name}.{name}")
    for name in ("note_path", "failed_path", "invalid_path"):
        if name in data:
            validate_fixture_reference(data[name])
    if "fields" in data:
        _require_text_list(data["fields"], f"{field_name}.fields", allow_empty=False)
    if "pattern" in data:
        _require_text_list(data["pattern"], f"{field_name}.pattern", allow_empty=False)
    if "memory" in data:
        memory = _require_mapping(data["memory"], f"{field_name}.memory")
        _require_exact_fields(memory, _fields("claim", "status"), f"{field_name}.memory")
        _require_text(memory["claim"], f"{field_name}.memory.claim")
        if memory["status"] not in {"active", "expired"}:
            raise ValueError(f"{field_name}.memory.status is invalid")
    if "memories" in data:
        memories = _require_list(data["memories"], f"{field_name}.memories")
        if len(memories) < 2:
            raise ValueError(f"{field_name}.memories must contain at least two records")
        for index, raw_memory in enumerate(memories):
            memory = _require_mapping(raw_memory, f"{field_name}.memories[{index}]")
            _require_exact_fields(memory, _fields("memory_id", "claim", "status"), f"{field_name}.memories[{index}]")
            _require_text(memory["memory_id"], f"{field_name}.memories[{index}].memory_id")
            _require_text(memory["claim"], f"{field_name}.memories[{index}].claim")
            if memory["status"] != "active":
                raise ValueError(f"{field_name}.memories[{index}].status is invalid")
    if "current_policy" in data:
        policy = _require_mapping(data["current_policy"], f"{field_name}.current_policy")
        _require_exact_fields(policy, _fields("gold_access"), f"{field_name}.current_policy")
        if policy["gold_access"] != "deny":
            raise ValueError(f"{field_name}.current_policy.gold_access is invalid")
    if kind == "wrong_argument_type" and data["actual_type"] not in {"string"}:
        raise ValueError(f"{field_name}.actual_type is invalid")
    if "checkpoint" in data and data["checkpoint"] not in {"before_executor", "after_executor"}:
        raise ValueError(f"{field_name}.checkpoint is invalid")
    if "expected_shape" in data and data["expected_shape"] not in {"array", "object"}:
        raise ValueError(f"{field_name}.expected_shape is invalid")
    if "preexisting_status" in data and data["preexisting_status"] != "pending":
        raise ValueError(f"{field_name}.preexisting_status is invalid")
    for name in ("tool_id", "returned_tool_id"):
        if name in data and data[name] not in REGISTERED_TOOL_IDS:
            raise ValueError(f"{field_name}.{name} is invalid")


def validate_trajectory_event(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    event = _require_text(data.get("event"), f"{field_name}.event")
    variants = TRAJECTORY_EVENT_REGISTRY.get(event)
    if variants is None:
        raise ValueError(f"{field_name}.event is not registered")
    if frozenset(data) not in variants:
        allowed = sorted(sorted(variant) for variant in variants)
        actual = set(data)
        if all(actual - set(variant) for variant in variants):
            raise ValueError(f"{field_name} has unknown fields for {event}; allowed={allowed}")
        raise ValueError(f"{field_name} has invalid fields for {event}; allowed={allowed}")
    for name in (
        "route", "tool_id", "call_id", "claim", "resource", "missing_detail",
        "topic", "forbidden_tool", "field", "expected_shape", "actual_shape",
        "expected_call_id", "actual_call_id", "matches", "reason",
        "failure_signature", "missing_step", "decision", "kind", "gold_access",
    ):
        if name in data:
            _require_text(data[name], f"{field_name}.{name}")
    for name in ("tool_id", "forbidden_tool"):
        if name in data:
            _require_registered_tool_id(data[name], f"{field_name}.{name}")
    for name in ("count", "used", "remaining", "budget_used", "execution_count", "pattern_length"):
        if name in data:
            _require_non_negative_int(data[name], f"{field_name}.{name}")
    for name in ("executor_called", "fingerprint_match"):
        if name in data:
            _require_bool(data[name], f"{field_name}.{name}")
    if "from" in data and data["from"] is not None and data["from"] not in LEDGER_STATES:
        raise ValueError(f"{field_name}.from is invalid")
    for name in ("to", "record_status"):
        if name in data and data[name] not in LEDGER_STATES:
            raise ValueError(f"{field_name}.{name} is invalid")
    if "status" in data:
        valid_statuses = {"expired"} if event == "memory_considered" else TOOL_RESULT_STATUSES
        if data["status"] not in valid_statuses:
            raise ValueError(f"{field_name}.status is invalid")
    if "business_status" in data and data["business_status"] not in BUSINESS_STATUSES:
        raise ValueError(f"{field_name}.business_status is invalid")
    if "route" in data and data["route"] not in {
        "retrieval",
        "exact_note",
        "unsafe_write_refusal",
        "clarification",
    }:
        raise ValueError(f"{field_name}.route is invalid")
    for name in ("expected_shape", "actual_shape"):
        if name in data and data[name] not in {"array", "object"}:
            raise ValueError(f"{field_name}.{name} is invalid")
    if "note_path" in data:
        validate_fixture_reference(data["note_path"])
    if "evidence_refs" in data:
        refs = _require_list(data["evidence_refs"], f"{field_name}.evidence_refs")
        for index, ref in enumerate(refs):
            _validate_evidence_reference_shape(ref, f"{field_name}.evidence_refs[{index}]")
    if "memory_ids" in data:
        _require_text_list(data["memory_ids"], f"{field_name}.memory_ids", allow_empty=False)
    if "error_code" in data and data["error_code"] is not None and data["error_code"] not in TOOL_ERROR_CODES:
        raise ValueError(f"{field_name}.error_code is invalid")
    if event == "tool_result":
        if data["status"] == "ok" and data["error_code"] is not None:
            raise ValueError(
                f"{field_name} tool_result status=ok requires error_code=null"
            )
        if data["status"] == "error" and data["error_code"] not in TOOL_ERROR_CODES:
            raise ValueError(
                f"{field_name} tool_result status=error requires a registered error_code"
            )
    if "decision" in data and data["decision"] not in RECOVERY_DECISIONS:
        raise ValueError(f"{field_name}.decision is invalid")
    if "kind" in data and data["kind"] not in {"read_only", "unknown"}:
        raise ValueError(f"{field_name}.kind is invalid")
    if "gold_access" in data and data["gold_access"] != "deny":
        raise ValueError(f"{field_name}.gold_access is invalid")


def validate_tool_call(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, TOOL_CALL_FIELDS, "ToolCall")
    _require_text(data["call_id"], f"{field_name}.call_id")
    _require_registered_tool_id(data["tool_id"], f"{field_name}.tool_id")
    _require_mapping(data["arguments"], f"{field_name}.arguments")
    _require_non_negative_int(data["sequence"], f"{field_name}.sequence")


def _validate_embedded_evidence_refs(value: Any, field_name: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "evidence_ref":
                _validate_evidence_reference_shape(nested, f"{field_name}.evidence_ref")
            else:
                _validate_embedded_evidence_refs(nested, f"{field_name}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_embedded_evidence_refs(nested, f"{field_name}[{index}]")


def validate_tool_result(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, TOOL_RESULT_FIELDS, "ToolResult")
    _require_text(data["call_id"], f"{field_name}.call_id")
    _require_registered_tool_id(data["tool_id"], f"{field_name}.tool_id")
    status = data["status"]
    if status not in TOOL_RESULT_STATUSES:
        raise ValueError(f"{field_name}.status is invalid")
    if status == "ok":
        if data["error"] is not None:
            raise ValueError(f"{field_name} status=ok requires error=null")
        if data["business_status"] not in BUSINESS_STATUSES:
            raise ValueError(f"{field_name} status=ok requires a valid business_status")
        _validate_embedded_evidence_refs(data["value"], f"{field_name}.value")
        return
    if data["error"] is None:
        raise ValueError(f"{field_name} status=error requires error")
    if data["value"] is not None or data["business_status"] is not None:
        raise ValueError(f"{field_name} status=error requires null value and business_status")
    error = _require_mapping(data["error"], f"{field_name}.error")
    _require_exact_fields(error, TOOL_ERROR_FIELDS, "ToolError")
    if error["code"] not in TOOL_ERROR_CODES:
        raise ValueError(f"{field_name}.error.code is invalid")
    if error["category"] not in TOOL_ERROR_CATEGORIES:
        raise ValueError(f"{field_name}.error.category is invalid")
    _require_text(error["message"], f"{field_name}.error.message")
    _require_bool(error["retryable"], f"{field_name}.error.retryable")
    _require_bool(error["safe_to_expose"], f"{field_name}.error.safe_to_expose")


def validate_termination(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    status = _require_text(data.get("status"), f"{field_name}.status")
    if status not in TERMINATION_STATUSES:
        raise ValueError(f"{field_name}.status is not registered")
    reason = _require_text(data.get("reason"), f"{field_name}.reason")
    registered = TERMINATION_REGISTRY.get(reason)
    if registered is None:
        raise ValueError(f"{field_name}.reason is not registered")
    expected_status, expected_fields = registered
    if status != expected_status:
        raise ValueError(f"{field_name} has an invalid status/reason combination")
    _require_exact_fields(data, expected_fields, field_name)
    for name in ("calls_used", "execution_count"):
        if name in data:
            _require_non_negative_int(data[name], f"{field_name}.{name}")
    for name in ("executor_completed", "valid_evidence_preserved"):
        if name in data:
            _require_bool(data[name], f"{field_name}.{name}")
    if "ledger_status" in data and data["ledger_status"] != "pending":
        raise ValueError(f"{field_name}.ledger_status is invalid")
    if "recommendation" in data and data["recommendation"] not in RECOVERY_DECISIONS:
        raise ValueError(f"{field_name}.recommendation is invalid")


def validate_attribution(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, _fields("primary", "stage"), field_name)
    primary = _require_text(data["primary"], f"{field_name}.primary")
    stage = _require_text(data["stage"], f"{field_name}.stage")
    if (primary, stage) not in ATTRIBUTION_TAXONOMY:
        raise ValueError(f"{field_name} attribution pair is not registered")


def _validate_single_or_list(value: Any, field_name: str, item_validator) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            item_validator(item, f"{field_name}[{index}]")
    else:
        item_validator(value, field_name)


def _enum_validator(values: frozenset[str] | set[str]):
    def validate(value: Any, field_name: str) -> None:
        if value not in values:
            raise ValueError(f"{field_name} is invalid")

    return validate


def validate_assertion(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, ASSERTION_FIELDS, field_name)
    name = _require_text(data["assertion"], f"{field_name}.assertion")
    rule = ASSERTION_REGISTRY.get(name)
    if rule is None:
        raise ValueError(f"{field_name}.assertion is not registered")
    expected = data["expected"]
    if rule == "bool":
        _require_bool(expected, f"{field_name}.expected")
    elif rule == "non_negative_int":
        _require_non_negative_int(expected, f"{field_name}.expected")
    elif rule == "text":
        _require_text(expected, f"{field_name}.expected")
    elif rule == "text_or_text_list":
        _validate_single_or_list(expected, f"{field_name}.expected", _require_text)
    elif rule == "object_or_object_list":
        _validate_single_or_list(expected, f"{field_name}.expected", _require_mapping)
    elif rule == "status_or_status_list":
        _validate_single_or_list(expected, f"{field_name}.expected", _enum_validator(TOOL_RESULT_STATUSES))
    elif rule == "business_status_or_list":
        _validate_single_or_list(expected, f"{field_name}.expected", _enum_validator(BUSINESS_STATUSES))
    elif rule == "error_code_or_list":
        def error_code(item: Any, item_field: str) -> None:
            if item is not None and item not in TOOL_ERROR_CODES:
                raise ValueError(f"{item_field} is invalid")

        _validate_single_or_list(expected, f"{field_name}.expected", error_code)
    elif rule == "termination_status":
        if expected not in TERMINATION_STATUSES:
            raise ValueError(f"{field_name}.expected is invalid")
    elif rule == "ledger_state_or_list":
        _validate_single_or_list(expected, f"{field_name}.expected", _enum_validator(LEDGER_STATES))
    elif rule == "recovery_decision":
        if expected not in RECOVERY_DECISIONS:
            raise ValueError(f"{field_name}.expected is invalid")
    elif rule == "loop_condition":
        condition = _require_mapping(expected, f"{field_name}.expected")
        variants = (_fields("detected", "pattern_length"), _fields("detected", "failure_count"))
        if frozenset(condition) not in variants:
            raise ValueError(f"{field_name}.expected has invalid loop fields")
        _require_bool(condition["detected"], f"{field_name}.expected.detected")
        counter = "pattern_length" if "pattern_length" in condition else "failure_count"
        _require_non_negative_int(condition[counter], f"{field_name}.expected.{counter}")
    elif rule == "attribution":
        validate_attribution(expected, f"{field_name}.expected")


def validate_assertion_result(value: Any, field_name: str) -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, ASSERTION_RESULT_FIELDS, field_name)
    validate_assertion({"assertion": data["assertion"], "expected": data["expected"]}, field_name)
    _require_bool(data["passed"], f"{field_name}.passed")
    _ensure_json_safe(data["actual"], f"{field_name}.actual")
    if data["passed"] and canonicalize(data["expected"]) != canonicalize(data["actual"]):
        raise ValueError(f"{field_name} passed result must match expected and actual")


def validate_execution(value: Any, field_name: str = "execution") -> None:
    data = _require_mapping(value, field_name)
    _require_exact_fields(data, EXECUTION_FIELDS, field_name)
    invocations = _require_list(data["executor_invocations"], f"{field_name}.executor_invocations")
    for index, raw in enumerate(invocations):
        invocation = _require_mapping(raw, f"{field_name}.executor_invocations[{index}]")
        _require_exact_fields(invocation, _fields("call_id", "tool_id"), f"{field_name}.executor_invocations[{index}]")
        _require_text(invocation["call_id"], f"{field_name}.executor_invocations[{index}].call_id")
        _require_registered_tool_id(
            invocation["tool_id"],
            f"{field_name}.executor_invocations[{index}].tool_id",
        )
    _require_text_list(data["suppressed_call_ids"], f"{field_name}.suppressed_call_ids")
    budget = _require_mapping(data["budget"], f"{field_name}.budget")
    _require_exact_fields(budget, _fields("used", "remaining"), f"{field_name}.budget")
    _require_non_negative_int(budget["used"], f"{field_name}.budget.used")
    _require_non_negative_int(budget["remaining"], f"{field_name}.budget.remaining")
    ledger = _require_mapping(data["ledger"], f"{field_name}.ledger")
    _require_exact_fields(ledger, _fields("history"), f"{field_name}.ledger")
    histories = _require_list(ledger["history"], f"{field_name}.ledger.history")
    for index, raw in enumerate(histories):
        history = _require_mapping(raw, f"{field_name}.ledger.history[{index}]")
        _require_exact_fields(history, _fields("call_id", "states"), f"{field_name}.ledger.history[{index}]")
        _require_text(history["call_id"], f"{field_name}.ledger.history[{index}].call_id")
        states = _require_list(history["states"], f"{field_name}.ledger.history[{index}].states")
        if not states or any(state not in LEDGER_STATES for state in states):
            raise ValueError(f"{field_name}.ledger.history[{index}].states is invalid")
    refs = _require_list(data["evidence_refs"], f"{field_name}.evidence_refs")
    for index, ref in enumerate(refs):
        _validate_evidence_reference_shape(ref, f"{field_name}.evidence_refs[{index}]")
    _require_text_list(data["denied_accesses"], f"{field_name}.denied_accesses")
    decisions = _require_list(data["recovery_decisions"], f"{field_name}.recovery_decisions")
    for index, raw in enumerate(decisions):
        decision = _require_mapping(raw, f"{field_name}.recovery_decisions[{index}]")
        _require_exact_fields(decision, _fields("call_id", "tool_id", "decision", "reason_code", "message"), f"{field_name}.recovery_decisions[{index}]")
        for name in ("call_id", "tool_id", "reason_code", "message"):
            _require_text(decision[name], f"{field_name}.recovery_decisions[{index}].{name}")
        _require_registered_tool_id(
            decision["tool_id"],
            f"{field_name}.recovery_decisions[{index}].tool_id",
        )
        if decision["decision"] not in RECOVERY_DECISIONS:
            raise ValueError(f"{field_name}.recovery_decisions[{index}].decision is invalid")
    signals = _require_mapping(data["signals"], f"{field_name}.signals")
    allowed_signals = _fields("repeated_call_detected", "loop_condition", "premature_termination_blocked", "stale_memory_ignored", "memory_conflict_detected")
    unknown = set(signals) - set(allowed_signals)
    if unknown:
        raise ValueError(f"{field_name}.signals has unknown fields: {sorted(unknown)}")
    for name in ("repeated_call_detected", "premature_termination_blocked", "stale_memory_ignored", "memory_conflict_detected"):
        if name in signals:
            _require_bool(signals[name], f"{field_name}.signals.{name}")
    if "loop_condition" in signals:
        validate_assertion({"assertion": "loop_condition", "expected": signals["loop_condition"]}, f"{field_name}.signals.loop_condition")


def validate_metadata(value: Any, field_name: str = "metadata") -> None:
    data = _require_mapping(value, field_name)
    if "run_id" not in data:
        raise ValueError(f"{field_name} missing required field: run_id")
    unknown = set(data) - set(NONDETERMINISTIC_KEYS)
    if unknown:
        raise ValueError(f"{field_name} has unknown fields: {sorted(unknown)}")
    for name, item in data.items():
        if name == "timestamps":
            _require_list(item, f"{field_name}.{name}")
        else:
            _require_text(item, f"{field_name}.{name}")


def canonicalize(value: Any) -> Any:
    """Return deterministic JSON data with non-semantic metadata removed."""
    _ensure_json_safe(value)
    if isinstance(value, dict):
        return {
            key: canonicalize(item)
            for key, item in sorted(value.items())
            if key.casefold() not in NONDETERMINISTIC_KEYS
        }
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    return value


@dataclass(frozen=True)
class TrajectoryCase:
    schema_version: str
    case_id: str
    category: str
    synthetic_input: dict[str, Any]
    policy_budget: dict[str, Any]
    fault_injection: dict[str, Any]
    expected_trajectory: list[dict[str, Any]]
    expected_tool_calls: list[dict[str, Any]]
    expected_tool_results: list[dict[str, Any]]
    expected_termination: dict[str, Any]
    expected_attribution: dict[str, Any]
    deterministic_assertions: list[dict[str, Any]]
    optional_judge_rubric: dict[str, Any]
    required_capabilities: list[str]

    def __post_init__(self) -> None:
        if self.schema_version != CASE_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {CASE_SCHEMA_VERSION}")
        _require_text(self.case_id, "case_id")
        if self.category not in TRAJECTORY_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(TRAJECTORY_CATEGORIES)}")
        synthetic_input = _require_mapping(self.synthetic_input, "synthetic_input")
        _require_exact_fields(synthetic_input, _fields("request", "fixture_paths"), "synthetic_input")
        _require_text(synthetic_input["request"], "synthetic_input.request")
        fixture_paths = _require_list(synthetic_input["fixture_paths"], "synthetic_input.fixture_paths")
        if not fixture_paths:
            raise ValueError("synthetic_input.fixture_paths must not be empty")
        for fixture_path in fixture_paths:
            validate_fixture_reference(fixture_path)
        policy = _require_mapping(self.policy_budget, "policy_budget")
        _require_exact_fields(policy, _fields("allowed_tools", "denied_tools", "max_tool_calls"), "policy_budget")
        allowed_tools = _require_text_list(
            policy["allowed_tools"], "policy_budget.allowed_tools"
        )
        denied_tools = _require_text_list(
            policy["denied_tools"], "policy_budget.denied_tools"
        )
        for index, tool_id in enumerate(allowed_tools):
            _require_registered_tool_id(
                tool_id, f"policy_budget.allowed_tools[{index}]"
            )
        for index, tool_id in enumerate(denied_tools):
            _require_registered_tool_id(
                tool_id, f"policy_budget.denied_tools[{index}]"
            )
        if len(policy["allowed_tools"]) != len(set(policy["allowed_tools"])):
            raise ValueError("policy_budget.allowed_tools must be unique")
        if len(policy["denied_tools"]) != len(set(policy["denied_tools"])):
            raise ValueError("policy_budget.denied_tools must be unique")
        if set(policy["allowed_tools"]) & set(policy["denied_tools"]):
            raise ValueError(
                "policy_budget.allowed_tools and denied_tools must not overlap"
            )
        _require_non_negative_int(policy["max_tool_calls"], "policy_budget.max_tool_calls")
        validate_fault_injection(self.fault_injection)
        trajectory = _require_list(self.expected_trajectory, "expected_trajectory")
        if not trajectory:
            raise ValueError("expected_trajectory must not be empty")
        for index, event in enumerate(trajectory):
            validate_trajectory_event(event, f"expected_trajectory[{index}]")
        calls = _require_list(self.expected_tool_calls, "expected_tool_calls")
        for index, call in enumerate(calls):
            validate_tool_call(call, f"expected_tool_calls[{index}]")
        results = _require_list(self.expected_tool_results, "expected_tool_results")
        for index, result in enumerate(results):
            validate_tool_result(result, f"expected_tool_results[{index}]")
        validate_termination(self.expected_termination, "expected_termination")
        validate_attribution(self.expected_attribution, "expected_attribution")
        assertions = _require_list(self.deterministic_assertions, "deterministic_assertions")
        if not assertions:
            raise ValueError("deterministic_assertions must not be empty")
        names: set[str] = set()
        for index, assertion in enumerate(assertions):
            validate_assertion(assertion, f"deterministic_assertions[{index}]")
            name = assertion["assertion"]
            if name in names:
                raise ValueError("deterministic_assertions must have unique assertion names")
            names.add(name)
        _validate_judge(self.optional_judge_rubric, "optional_judge_rubric", require_criteria=True)
        capabilities = _require_text_list(self.required_capabilities, "required_capabilities", allow_empty=False)
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("required_capabilities must be unique")
        _ensure_json_safe(asdict(self), "TrajectoryCase")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryCase":
        _require_exact_fields(data, CASE_FIELDS, "TrajectoryCase")
        return cls(**deepcopy(data))


@dataclass(frozen=True)
class TrajectoryObservation:
    schema_version: str
    case_id: str
    trajectory: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    termination: dict[str, Any]
    attribution: dict[str, Any]
    execution: dict[str, Any]
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {OBSERVATION_SCHEMA_VERSION}")
        _require_text(self.case_id, "case_id")
        trajectory = _require_list(self.trajectory, "trajectory")
        if not trajectory:
            raise ValueError("trajectory must not be empty")
        for index, event in enumerate(trajectory):
            validate_trajectory_event(event, f"trajectory[{index}]")
        calls = _require_list(self.tool_calls, "tool_calls")
        for index, call in enumerate(calls):
            validate_tool_call(call, f"tool_calls[{index}]")
        results = _require_list(self.tool_results, "tool_results")
        for index, result in enumerate(results):
            validate_tool_result(result, f"tool_results[{index}]")
        validate_termination(self.termination, "termination")
        validate_attribution(self.attribution, "attribution")
        validate_execution(self.execution)
        validate_metadata(self.metadata)
        _ensure_json_safe(asdict(self), "TrajectoryObservation")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryObservation":
        _require_exact_fields(data, OBSERVATION_FIELDS, "TrajectoryObservation")
        return cls(**deepcopy(data))


@dataclass(frozen=True)
class CaseResult:
    schema_version: str
    case_id: str
    category: str
    status: str
    assertions: list[dict[str, Any]]
    failed_assertion: Any
    expected: Any
    actual: Any
    primary_failure_attribution: str | None
    missing_capability: str | None
    judge: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != RESULT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {RESULT_SCHEMA_VERSION}")
        _require_text(self.case_id, "case_id")
        if self.category not in TRAJECTORY_CATEGORIES:
            raise ValueError(f"category must be one of {sorted(TRAJECTORY_CATEGORIES)}")
        if self.status not in {"PASS", "FAIL", "NOT_IMPLEMENTED"}:
            raise ValueError("status must be PASS, FAIL, or NOT_IMPLEMENTED")
        assertions = _require_list(self.assertions, "assertions")
        names: set[str] = set()
        for index, assertion in enumerate(assertions):
            validate_assertion_result(assertion, f"assertions[{index}]")
            if assertion["assertion"] in names:
                raise ValueError("assertions must have unique assertion names")
            names.add(assertion["assertion"])
        _ensure_json_safe(self.expected, "expected")
        _ensure_json_safe(self.actual, "actual")
        _validate_judge(self.judge, "judge", require_criteria=False)
        if self.status == "PASS":
            if not assertions or not all(assertion["passed"] for assertion in assertions):
                raise ValueError("PASS requires non-empty passing assertions")
            if any(value is not None for value in (self.failed_assertion, self.expected, self.actual, self.primary_failure_attribution, self.missing_capability)):
                raise ValueError("PASS cannot contain failure fields")
        elif self.status == "FAIL":
            failed_name = _require_text(self.failed_assertion, "failed_assertion")
            failed = next((assertion for assertion in assertions if assertion["assertion"] == failed_name and not assertion["passed"]), None)
            if failed is None:
                raise ValueError("FAIL failed_assertion must name a failed assertion result")
            if canonicalize(self.expected) != canonicalize(failed["expected"]):
                raise ValueError("FAIL expected must match failed assertion result")
            if canonicalize(self.actual) != canonicalize(failed["actual"]):
                raise ValueError("FAIL actual must match failed assertion result")
            _require_text(self.primary_failure_attribution, "primary_failure_attribution")
            if self.primary_failure_attribution not in FAILURE_ATTRIBUTIONS:
                raise ValueError("primary_failure_attribution is not registered")
            if self.missing_capability is not None:
                raise ValueError("FAIL cannot contain missing_capability")
        else:
            _require_text(self.missing_capability, "missing_capability")
            if assertions:
                raise ValueError("NOT_IMPLEMENTED cannot contain assertion results")
            if any(value is not None for value in (self.failed_assertion, self.expected, self.actual)):
                raise ValueError("NOT_IMPLEMENTED cannot contain failure assertion fields")
            if self.primary_failure_attribution != "capability_gap":
                raise ValueError("NOT_IMPLEMENTED attribution must be capability_gap")
        _ensure_json_safe(asdict(self), "CaseResult")

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(asdict(self))

    def canonical_dict(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaseResult":
        _require_exact_fields(data, RESULT_FIELDS, "CaseResult")
        return cls(**deepcopy(data))


__all__ = [
    "ASSERTION_REGISTRY",
    "ATTRIBUTION_SIGNAL_PRIORITY",
    "ATTRIBUTION_TAXONOMY",
    "CASE_SCHEMA_VERSION",
    "FAULT_INJECTION_REGISTRY",
    "OBSERVATION_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "TERMINATION_REGISTRY",
    "TRAJECTORY_CATEGORIES",
    "TRAJECTORY_EVENT_REGISTRY",
    "CaseResult",
    "TrajectoryCase",
    "TrajectoryObservation",
    "canonicalize",
    "validate_assertion",
    "validate_attribution",
    "validate_fault_injection",
    "validate_termination",
    "validate_tool_call",
    "validate_tool_result",
    "validate_trajectory_event",
]
