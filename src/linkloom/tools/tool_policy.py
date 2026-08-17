"""Runtime-enforced read-only tool policy for P4 specialists."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import _assert_json_safe_primitive, _require_keys


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
    """Authorize typed tools and count only successful calls."""

    def __init__(self, policy: ToolCallPolicy):
        self.policy = policy
        self.call_count = 0

    def authorize_call(self, tool_id: str) -> None:
        if not isinstance(tool_id, str) or not tool_id:
            raise ValidationError("tool_id must be a non-empty string.")
        if tool_id in DENIED_TOOL_IDS:
            raise ValidationError(f"Tool {tool_id} violates P4 core safety limits.")
        if tool_id in self.policy.denied_tool_ids:
            raise ValidationError(f"Tool {tool_id} is explicitly denied.")
        if tool_id not in self.policy.allowed_tool_ids:
            raise ValidationError(f"Tool {tool_id} is not in allowed list.")
        if self.call_count >= self.policy.max_calls:
            raise ValidationError(f"Exceeded max tool calls: {self.policy.max_calls}")
        self.call_count += 1

    @property
    def remaining_calls(self) -> int:
        return max(0, self.policy.max_calls - self.call_count)
