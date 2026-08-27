"""Runtime-enforced read-only tool policy for P4 specialists."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    ToolExecutionRecord,
    _assert_json_safe_primitive,
    _require_keys,
)


P4_TOOL_POLICY_VERSION = "p4-readonly-tools-v1"
KNOWN_TOOL_IDS = frozenset({
    "search_notes",
    "read_verified_note",
    "build_pair_signals",
    "validate_evidence",
    "validate_schema",
})
DENIED_TOOL_IDS = frozenset({"write_file", "rename_file", "read_gold", "raw_filesystem"})


@dataclass(frozen=True)
class ToolCallPolicy:
    policy_version: str
    agent_id: str
    allowed_tool_ids: list[str]
    denied_tool_ids: list[str]
    max_calls: int
    network: str
    vault_write: str
    gold_access: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolCallPolicy")
        if self.policy_version != P4_TOOL_POLICY_VERSION:
            raise ValidationError(f"policy_version must be {P4_TOOL_POLICY_VERSION}.")
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ValidationError("agent_id must be a non-empty string.")
        if not isinstance(self.allowed_tool_ids, list) or any(
            tool_id not in KNOWN_TOOL_IDS for tool_id in self.allowed_tool_ids
        ):
            raise ValidationError("allowed_tool_ids must contain only known tools.")
        if not isinstance(self.denied_tool_ids, list) or any(
            tool_id not in KNOWN_TOOL_IDS | DENIED_TOOL_IDS for tool_id in self.denied_tool_ids
        ):
            raise ValidationError("denied_tool_ids must contain only known or denied tools.")
        if set(self.allowed_tool_ids) & set(self.denied_tool_ids):
            raise ValidationError("allowed_tool_ids and denied_tool_ids must not overlap.")
        if self.network != "deny":
            raise ValidationError("P4 ToolCallPolicy must deny network.")
        if self.vault_write != "deny":
            raise ValidationError("P4 ToolCallPolicy must deny vault_write.")
        if self.gold_access != "deny":
            raise ValidationError("P4 ToolCallPolicy must deny gold_access.")
        if isinstance(self.max_calls, bool) or not isinstance(self.max_calls, int) or self.max_calls < 0:
            raise ValidationError("max_calls must be a non-negative integer.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCallPolicy":
        if not isinstance(data, dict):
            raise ValidationError("ToolCallPolicy must be a JSON object.")
        _require_keys(
            data,
            {
                "policy_version", "agent_id", "allowed_tool_ids", "denied_tool_ids",
                "max_calls", "network", "vault_write", "gold_access"
            },
            "ToolCallPolicy",
        )
        return cls(**{key: data[key] for key in (
            "policy_version", "agent_id", "allowed_tool_ids", "denied_tool_ids",
            "max_calls", "network", "vault_write", "gold_access"
        )})


class ToolPolicyEnforcer:
    """Authorize typed tools and count each authorized call."""

    def __init__(self, policy: ToolCallPolicy):
        self.policy = policy
        self.call_count = 0

    def _check_call_eligibility(self, tool_id: str) -> None:
        if not isinstance(tool_id, str) or not tool_id:
            raise ValidationError("tool_id must be a non-empty string.")
        if tool_id in DENIED_TOOL_IDS:
            raise ValidationError(
                f"Tool {tool_id} violates P4 core safety limits.",
                details={"reason": "core_denial", "tool_id": tool_id},
            )
        if tool_id in self.policy.denied_tool_ids:
            raise ValidationError(
                f"Tool {tool_id} is explicitly denied.",
                details={"reason": "explicit_denial", "tool_id": tool_id},
            )
        if tool_id not in self.policy.allowed_tool_ids:
            raise ValidationError(
                f"Tool {tool_id} is not in allowed list.",
                details={"reason": "not_allowed", "tool_id": tool_id},
            )
        if self.call_count >= self.policy.max_calls:
            raise ValidationError(
                f"Exceeded max tool calls: {self.policy.max_calls}",
                details={"reason": "budget_exhausted", "tool_id": tool_id},
            )

    def check_call_eligibility(self, tool_id: str) -> None:
        """Check permission and remaining budget without consuming a call.

        ToolRuntime uses this check before it creates the durable pending
        ledger record.  Keeping the check on the existing policy enforcer
        preserves one permission/budget authority while allowing durability
        to be the commitment boundary.
        """
        self._check_call_eligibility(tool_id)

    def commit_call(self, tool_id: str) -> None:
        """Consume exactly one eligible call after its pending boundary.

        The eligibility check is repeated so a caller cannot commit a call
        after the policy has become ineligible.  This method remains on the
        same enforcer; it is not a second budget implementation.
        """
        self._check_call_eligibility(tool_id)
        self.call_count += 1

    def authorize_call(self, tool_id: str) -> None:
        """Backward-compatible authorize-and-consume entry point.

        Legacy direct tool wrappers call this method.  New durable runtimes
        must use ``check_call_eligibility`` followed by ``commit_call`` after
        their pending checkpoint succeeds.
        """
        self.commit_call(tool_id)

    def rehydrate_from_ledger(
        self,
        records: Iterable[ToolExecutionRecord],
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        """Restore consumed-call count from durable authorized executions.

        The ledger remains the durable source of facts; this method only
        rehydrates the policy enforcer's process-local counter.  A durable
        pending record is already past the budget commitment boundary, so it
        counts once just like completed and failed records.  When a runtime
        supplies its execution scope, only records for that run, task, and
        policy agent are counted.  Out-of-scope records remain ledger facts
        but cannot consume this policy's budget.
        """
        if records is None:
            raise ValidationError("Tool policy ledger records are required.")
        for field_name, value in (
            ("run_id", run_id),
            ("task_id", task_id),
            ("agent_id", agent_id),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValidationError(f"{field_name} must be a non-empty string or None.")
        if agent_id is not None and agent_id != self.policy.agent_id:
            raise ValidationError(
                "Tool policy agent scope does not match the enforcer policy.",
                details={
                    "reason": "scope_mismatch",
                    "policy_agent_id": self.policy.agent_id,
                    "agent_id": agent_id,
                },
            )

        records_list = list(records)
        seen_call_ids: set[str] = set()
        for record in records_list:
            if not isinstance(record, ToolExecutionRecord):
                raise ValidationError(
                    "Tool policy ledger records must be ToolExecutionRecord values."
                )
            if record.call_id in seen_call_ids:
                raise ValidationError(
                    "Tool policy ledger records cannot contain duplicate call_id values.",
                    details={"reason": "duplicate_call_id", "call_id": record.call_id},
                )
            seen_call_ids.add(record.call_id)

        scoped_records = [
            record
            for record in records_list
            if record.agent_id == self.policy.agent_id
            and (run_id is None or record.run_id == run_id)
            and (task_id is None or record.task_id == task_id)
        ]
        consumed_calls = len(scoped_records)
        if consumed_calls > self.policy.max_calls:
            raise ValidationError(
                "Durable tool ledger exceeds the policy max_calls boundary.",
                details={
                    "reason": "budget_exhausted",
                    "consumed_calls": consumed_calls,
                    "max_calls": self.policy.max_calls,
                    "scope": {
                        "run_id": run_id,
                        "task_id": task_id,
                        "agent_id": self.policy.agent_id,
                    },
                },
            )
        self.call_count = consumed_calls

    @property
    def remaining_calls(self) -> int:
        return max(0, self.policy.max_calls - self.call_count)
