"""Recovery recommendations for durable pending tool executions.

This module deliberately recommends a disposition only.  It never retries,
replays, deduplicates, or executes a tool.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.runtime.models import _assert_json_safe_primitive


RECOVERY_DECISIONS = frozenset(
    {
        "safe_to_retry",
        "requires_verification",
        "requires_manual_decision",
        "non_retryable",
    }
)
DEFAULT_READ_ONLY_TOOL_IDS = frozenset({"search_notes", "read_verified_note"})


@dataclass(frozen=True)
class PendingRecoveryDecision:
    """A safe recommendation for a pending call after a crash or reload."""

    call_id: str
    tool_id: str
    decision: str
    reason_code: str
    message: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "PendingRecoveryDecision")
        for name, value in (
            ("call_id", self.call_id),
            ("tool_id", self.tool_id),
            ("reason_code", self.reason_code),
            ("message", self.message),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"PendingRecoveryDecision.{name} must be non-empty text.")
        if self.decision not in RECOVERY_DECISIONS:
            raise ValidationError(
                "PendingRecoveryDecision.decision must be one of "
                f"{sorted(RECOVERY_DECISIONS)}."
            )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _tool_set(values: Iterable[str] | None, field_name: str) -> set[str]:
    if values is None:
        return set()
    result = set(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValidationError(f"{field_name} must contain only non-empty tool IDs.")
    return result


def decide_pending_recovery(
    record: ToolExecutionRecord | None,
    *,
    read_only_tool_ids: Iterable[str] | None = None,
    verification_required_tool_ids: Iterable[str] | None = None,
    non_retryable_tool_ids: Iterable[str] | None = None,
) -> PendingRecoveryDecision:
    """Return a recovery recommendation without changing the ledger.

    The default read-only allow-list is intentionally narrow.  Every other
    tool requires an explicit decision because a pending record does not prove
    that an external side effect did not happen.
    """
    if not isinstance(record, ToolExecutionRecord):
        raise ValidationError("Pending recovery requires a ToolExecutionRecord.")
    if record.status != "pending":
        raise ValidationError("Pending recovery requires a record with status='pending'.")

    read_only = (
        set(DEFAULT_READ_ONLY_TOOL_IDS)
        if read_only_tool_ids is None
        else _tool_set(read_only_tool_ids, "read_only_tool_ids")
    )
    verification = _tool_set(
        verification_required_tool_ids,
        "verification_required_tool_ids",
    )
    non_retryable = _tool_set(non_retryable_tool_ids, "non_retryable_tool_ids")

    if record.tool_id in non_retryable:
        decision = "non_retryable"
        reason_code = "tool_marked_non_retryable"
        message = "The pending tool is explicitly non-retryable."
    elif record.tool_id in verification:
        decision = "requires_verification"
        reason_code = "external_state_requires_verification"
        message = "Verify external state before deciding whether to run the tool again."
    elif record.tool_id in read_only:
        decision = "safe_to_retry"
        reason_code = "approved_read_only_tool"
        message = "The registered tool is read-only; a caller may choose a controlled retry."
    else:
        decision = "requires_manual_decision"
        reason_code = "unknown_side_effect_profile"
        message = "The tool side-effect profile is not known to be safe for retry."

    return PendingRecoveryDecision(
        call_id=record.call_id,
        tool_id=record.tool_id,
        decision=decision,
        reason_code=reason_code,
        message=message,
    )


__all__ = [
    "DEFAULT_READ_ONLY_TOOL_IDS",
    "PendingRecoveryDecision",
    "RECOVERY_DECISIONS",
    "decide_pending_recovery",
]
