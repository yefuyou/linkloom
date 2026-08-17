"""Read-only security policy and execution guardrails for Linkloom Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from linkloom.runtime.errors import BudgetExceededError, PermissionDeniedError, ValidationError


@dataclass(frozen=True)
class ReadOnlyPolicy:
    """Explicitly read-only security policy that enforces safety bounds and forbids write capabilities."""

    policy_version: str = "p2-readonly-v1"
    write_capability: bool = False
    network_capability: bool = False
    max_steps: int = 12
    max_provider_requests: int = 0

    def __post_init__(self) -> None:
        if self.write_capability is not False:
            raise ValidationError(
                "ReadOnlyPolicy strictly forbids write_capability=True.",
                details={"policy_version": self.policy_version},
            )
        if self.network_capability is not False:
            raise ValidationError(
                "ReadOnlyPolicy strictly forbids network_capability=True.",
                details={"policy_version": self.policy_version},
            )
        if self.max_steps < 1:
            raise ValidationError(
                f"max_steps must be at least 1, got {self.max_steps}.",
                details={"max_steps": self.max_steps},
            )
        if self.max_provider_requests < 0:
            raise ValidationError(
                f"max_provider_requests must be non-negative, got {self.max_provider_requests}.",
                details={"max_provider_requests": self.max_provider_requests},
            )

    def check_can_write(self) -> None:
        """Enforce that write operations are rejected immediately under ReadOnlyPolicy."""
        raise PermissionDeniedError(
            "Write operations are strictly forbidden under the active read-only policy.",
            details={"policy_version": self.policy_version},
        )

    def check_step_limit(self, current_step_count: int) -> None:
        """Enforce bounded execution steps."""
        if current_step_count > self.max_steps:
            raise BudgetExceededError(
                f"Execution step limit exceeded: {current_step_count} > {self.max_steps}.",
                details={"current_step_count": current_step_count, "max_steps": self.max_steps},
            )

    def to_snapshot(self) -> Any:
        from linkloom.runtime.models import PolicySnapshot

        return PolicySnapshot(
            policy_version=self.policy_version,
            write_capability=self.write_capability,
            network_capability=self.network_capability,
            max_steps=self.max_steps,
            max_provider_requests=self.max_provider_requests,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "write_capability": self.write_capability,
            "network_capability": self.network_capability,
            "max_steps": self.max_steps,
            "max_provider_requests": self.max_provider_requests,
        }
