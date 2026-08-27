"""Durable tool-execution ledger primitives for Linkloom P8.2.

The ledger records authorization hand-off and normalized outcomes.  It does
not authorize, execute, retry, replay, or deduplicate tool calls; those
responsibilities remain with ``ToolPolicyEnforcer`` and the caller-owned
runtime lifecycle.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Iterable

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.tools.contracts import ToolCall, ToolError, ToolResult


def _ledger_failure(reason: str, message: str, **details: object) -> ValidationError:
    return ValidationError(message, details={"reason": reason, **details})


class ToolExecutionLedger:
    """An explicit, append-by-transition ledger for one runtime execution."""

    def __init__(
        self,
        records: Iterable[ToolExecutionRecord | dict] | None = None,
    ) -> None:
        self._records: list[ToolExecutionRecord] = []
        self._index: dict[str, int] = {}
        for raw_record in records or ():
            record = (
                raw_record
                if isinstance(raw_record, ToolExecutionRecord)
                else ToolExecutionRecord.from_dict(raw_record)
            )
            if record.call_id in self._index:
                raise _ledger_failure(
                    "duplicate_call_id",
                    "Tool ledger cannot contain duplicate call_id values.",
                    call_id=record.call_id,
                )
            self._index[record.call_id] = len(self._records)
            self._records.append(self._copy_record(record))

    @classmethod
    def from_list(
        cls,
        records: Iterable[ToolExecutionRecord | dict] | None,
    ) -> "ToolExecutionLedger":
        """Rehydrate the ledger from the checkpoint-owned record list."""
        return cls(records)

    @staticmethod
    def _copy_record(record: ToolExecutionRecord) -> ToolExecutionRecord:
        return ToolExecutionRecord.from_dict(deepcopy(record.to_dict()))

    @staticmethod
    def _require_call(call: ToolCall) -> None:
        if not isinstance(call, ToolCall):
            raise ValidationError("ToolExecutionLedger call must be a ToolCall.")

    def _existing(self, call: ToolCall) -> ToolExecutionRecord:
        self._require_call(call)
        index = self._index.get(call.call_id)
        if index is None:
            raise _ledger_failure(
                "unknown_call_id",
                "Tool ledger has no record for call_id.",
                call_id=call.call_id,
            )
        record = self._records[index]
        if (
            record.tool_id != call.tool_id
            or record.run_id != call.run_id
            or record.task_id != call.task_id
            or record.agent_id != call.agent_id
            or record.sequence != call.sequence
        ):
            raise _ledger_failure(
                "call_identity_mismatch",
                "Tool call identity does not match its ledger record.",
                call_id=call.call_id,
            )
        return record

    def get(self, call_id: str) -> ToolExecutionRecord | None:
        """Return a defensive copy of a record, or ``None`` if absent."""
        index = self._index.get(call_id)
        if index is None:
            return None
        return self._copy_record(self._records[index])

    def list_for_run(self, run_id: str | None) -> list[ToolExecutionRecord]:
        """Return defensive copies in original execution sequence order."""
        return [
            self._copy_record(record)
            for record in self._records
            if record.run_id == run_id
        ]

    def to_list(self) -> list[ToolExecutionRecord]:
        """Return defensive copies suitable for ``RuntimeState.tool_ledger``."""
        return [self._copy_record(record) for record in self._records]

    def record_pending(self, call: ToolCall) -> ToolExecutionRecord:
        """Record the authorized-before-executor boundary exactly once."""
        self._require_call(call)
        if call.call_id in self._index:
            raise _ledger_failure(
                "duplicate_call_id",
                "Tool ledger already contains this call_id; no record was overwritten.",
                call_id=call.call_id,
            )

        record = ToolExecutionRecord(
            call_id=call.call_id,
            run_id=call.run_id,
            task_id=call.task_id,
            agent_id=call.agent_id,
            tool_id=call.tool_id,
            sequence=call.sequence,
            status="pending",
            arguments=deepcopy(call.arguments),
        )
        self._index[call.call_id] = len(self._records)
        self._records.append(record)
        return self._copy_record(record)

    def record_completed(self, call: ToolCall, result: ToolResult) -> ToolExecutionRecord:
        """Move a pending call to completed with a normalized successful result."""
        record = self._existing(call)
        if record.status != "pending":
            raise _ledger_failure(
                "invalid_transition",
                "Only a pending tool call can become completed.",
                call_id=call.call_id,
                current_status=record.status,
                next_status="completed",
            )
        self._validate_result(call, result, expected_status="ok")
        updated = replace(
            record,
            status="completed",
            result=deepcopy(result.to_dict()),
            error=None,
        )
        self._records[self._index[call.call_id]] = updated
        return self._copy_record(updated)

    def record_failed(
        self,
        call: ToolCall,
        result_or_error: ToolResult | ToolError,
    ) -> ToolExecutionRecord:
        """Move a pending call to failed with a safe normalized error."""
        record = self._existing(call)
        if record.status != "pending":
            raise _ledger_failure(
                "invalid_transition",
                "Only a pending tool call can become failed.",
                call_id=call.call_id,
                current_status=record.status,
                next_status="failed",
            )

        if isinstance(result_or_error, ToolResult):
            self._validate_result(call, result_or_error, expected_status="error")
            error = result_or_error.error
            assert error is not None
            result_dict = deepcopy(result_or_error.to_dict())
            error_dict = deepcopy(error.to_dict())
        elif isinstance(result_or_error, ToolError):
            result_dict = None
            error_dict = deepcopy(result_or_error.to_dict())
        else:
            raise ValidationError("Tool ledger failure must be a ToolResult or ToolError.")

        updated = replace(
            record,
            status="failed",
            result=result_dict,
            error=error_dict,
        )
        self._records[self._index[call.call_id]] = updated
        return self._copy_record(updated)

    @staticmethod
    def _validate_result(
        call: ToolCall,
        result: ToolResult,
        *,
        expected_status: str,
    ) -> None:
        if not isinstance(result, ToolResult):
            raise ValidationError("Tool ledger result must be a ToolResult.")
        if result.call_id != call.call_id or result.tool_id != call.tool_id:
            raise _ledger_failure(
                "result_identity_mismatch",
                "Tool result identity does not match its ToolCall.",
                call_id=call.call_id,
            )
        if result.status != expected_status:
            raise _ledger_failure(
                "invalid_result_status",
                "Tool result status is not valid for this ledger transition.",
                call_id=call.call_id,
                expected_status=expected_status,
                actual_status=result.status,
            )


__all__ = ["ToolExecutionLedger"]
