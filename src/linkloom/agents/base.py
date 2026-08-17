"""Typed, JSON-safe contracts for the P4 Linkloom agent workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import _assert_json_safe_primitive, _require_keys


VALID_WORKFLOWS = {"ask", "connect"}
AGENT_TASK_STATUSES = {"queued", "running", "completed", "failed", "rejected", "timed_out"}
AGENT_RESULT_STATUSES = {"completed", "failed", "rejected", "timed_out"}
HANDOFF_STATUSES = {"requested", "accepted", "rejected", "completed"}
REVIEW_DECISIONS = {
    "evidence_sufficient",
    "evidence_insufficient",
    "schema_invalid",
    "needs_human",
}
FORBIDDEN_REF_TERMS = {
    "gold",
    "writer",
    "secret",
    "credential",
    "raw_filesystem",
    "raw filesystem",
    "filesystem",
    "vault_root",
    "vault root",
    "shell",
}
FORBIDDEN_TOOL_IDS = {"write_file", "rename_file", "read_gold", "raw_filesystem"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string.")
    return value


def _require_id(value: Any, field_name: str) -> str:
    value = _require_text(value, field_name)
    if ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a stable identifier.")
    return value


def _validate_ref_list(refs: Any, field_name: str) -> None:
    if not isinstance(refs, list):
        raise ValidationError(f"{field_name} must be a list of opaque references.")
    for ref in refs:
        ref = _require_text(ref, f"{field_name} item")
        normalized = ref.casefold()
        if (
            Path(ref).is_absolute()
            or PureWindowsPath(ref).is_absolute()
            or "/" in ref
            or "\\" in ref
            or ".." in ref
            or any(term in normalized for term in FORBIDDEN_REF_TERMS)
        ):
            raise ValidationError(
                f"{field_name} contains an absolute path, relative reference, or forbidden term."
            )


def _validate_string_list(values: Any, field_name: str) -> None:
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValidationError(f"{field_name} must be a list of strings.")


def _validate_tool_list(values: Any, field_name: str) -> None:
    _validate_string_list(values, field_name)
    if any(tool_id in FORBIDDEN_TOOL_IDS for tool_id in values):
        raise ValidationError(f"{field_name} contains a forbidden tool.")


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    role: str
    version: str
    capabilities: list[str]
    allowed_workflows: list[str]
    can_read_gold: bool
    can_write_vault: bool
    max_steps: int

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "AgentIdentity")
        _require_id(self.agent_id, "agent_id")
        _require_text(self.role, "role")
        _require_text(self.version, "version")
        _validate_string_list(self.capabilities, "capabilities")
        _validate_string_list(self.allowed_workflows, "allowed_workflows")
        if not set(self.allowed_workflows) <= VALID_WORKFLOWS:
            raise ValidationError("allowed_workflows can only contain ask or connect.")
        if not isinstance(self.can_read_gold, bool) or not isinstance(self.can_write_vault, bool):
            raise ValidationError("Agent capability flags must be boolean.")
        if self.can_read_gold:
            raise ValidationError("P4 AgentIdentity cannot read gold.")
        if self.can_write_vault:
            raise ValidationError("P4 AgentIdentity cannot write vault.")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValidationError("max_steps must be a positive integer.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentIdentity":
        if not isinstance(data, dict):
            raise ValidationError("AgentIdentity must be a JSON object.")
        _require_keys(
            data,
            {
                "agent_id", "role", "version", "capabilities", "allowed_workflows",
                "can_read_gold", "can_write_vault", "max_steps"
            },
            "AgentIdentity",
        )
        return cls(**{key: data[key] for key in (
            "agent_id", "role", "version", "capabilities", "allowed_workflows",
            "can_read_gold", "can_write_vault", "max_steps"
        )})


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    run_id: str
    parent_task_id: str | None
    parent_agent_id: str
    agent_id: str
    workflow: str
    input_refs: list[str]
    allowed_tool_ids: list[str]
    max_steps: int
    deadline_ms: int
    status: str
    attempt: int
    created_at: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "AgentTask")
        for value, field_name in (
            (self.task_id, "task_id"), (self.run_id, "run_id"),
            (self.parent_agent_id, "parent_agent_id"), (self.agent_id, "agent_id")
        ):
            _require_id(value, field_name)
        if self.parent_task_id is not None:
            _require_id(self.parent_task_id, "parent_task_id")
        if self.workflow not in VALID_WORKFLOWS:
            raise ValidationError("AgentTask workflow must be ask or connect.")
        if self.status not in AGENT_TASK_STATUSES:
            raise ValidationError(f"Invalid AgentTask status: {self.status}")
        _validate_ref_list(self.input_refs, "AgentTask input_refs")
        _validate_tool_list(self.allowed_tool_ids, "AgentTask allowed_tool_ids")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValidationError("AgentTask max_steps must be a positive integer.")
        if isinstance(self.deadline_ms, bool) or not isinstance(self.deadline_ms, int) or self.deadline_ms <= 0:
            raise ValidationError("AgentTask deadline_ms must be a positive integer.")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 0:
            raise ValidationError("AgentTask attempt must be a non-negative integer.")
        _require_text(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTask":
        if not isinstance(data, dict):
            raise ValidationError("AgentTask must be a JSON object.")
        _require_keys(
            data,
            {
                "task_id", "run_id", "parent_agent_id", "agent_id", "workflow",
                "input_refs", "allowed_tool_ids", "max_steps", "deadline_ms",
                "status", "attempt", "created_at"
            },
            "AgentTask",
        )
        return cls(
            task_id=data["task_id"], run_id=data["run_id"],
            parent_task_id=data.get("parent_task_id"), parent_agent_id=data["parent_agent_id"],
            agent_id=data["agent_id"], workflow=data["workflow"], input_refs=data["input_refs"],
            allowed_tool_ids=data["allowed_tool_ids"], max_steps=data["max_steps"],
            deadline_ms=data["deadline_ms"], status=data["status"], attempt=data["attempt"],
            created_at=data["created_at"]
        )


@dataclass(frozen=True)
class AgentResult:
    task_id: str
    agent_id: str
    status: str
    output_type: str
    output_refs: list[str]
    summary: str
    confidence: float | None
    handoff: dict[str, Any] | None
    warnings: list[str]
    usage: dict[str, int]
    error: dict[str, Any] | None
    completed_at: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "AgentResult")
        _require_id(self.task_id, "task_id")
        _require_id(self.agent_id, "agent_id")
        if self.status not in AGENT_RESULT_STATUSES:
            raise ValidationError(f"Invalid AgentResult status: {self.status}")
        _require_text(self.output_type, "output_type")
        _validate_ref_list(self.output_refs, "AgentResult output_refs")
        _require_text(self.summary, "summary")
        _validate_string_list(self.warnings, "warnings")
        if self.status == "completed" and not self.output_refs and not self.handoff:
            raise ValidationError("Completed AgentResult must have output_refs or handoff.")
        if self.status in {"failed", "rejected", "timed_out"} and not self.error:
            raise ValidationError("Failed/rejected AgentResult must have error; timed_out is also terminal.")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
        ):
            raise ValidationError("AgentResult confidence must be None or between 0 and 1.")
        if not isinstance(self.usage, dict):
            raise ValidationError("AgentResult usage must be an object.")
        required_usage = {"steps", "tool_calls", "provider_requests"}
        if not required_usage <= self.usage.keys():
            raise ValidationError("AgentResult usage must include steps, tool_calls, provider_requests.")
        for key, value in self.usage.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValidationError(f"AgentResult usage {key} must be a non-negative integer.")
        if self.handoff is not None and not isinstance(self.handoff, dict):
            raise ValidationError("AgentResult handoff must be an object or null.")
        _require_text(self.completed_at, "completed_at")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResult":
        if not isinstance(data, dict):
            raise ValidationError("AgentResult must be a JSON object.")
        _require_keys(
            data,
            {"task_id", "agent_id", "status", "output_type", "output_refs", "summary", "usage", "completed_at"},
            "AgentResult",
        )
        return cls(
            task_id=data["task_id"], agent_id=data["agent_id"], status=data["status"],
            output_type=data["output_type"], output_refs=data["output_refs"], summary=data["summary"],
            confidence=data.get("confidence"), handoff=data.get("handoff"),
            warnings=data.get("warnings", []), usage=data["usage"], error=data.get("error"),
            completed_at=data["completed_at"]
        )


@dataclass(frozen=True)
class HandoffRequest:
    handoff_id: str
    from_agent_id: str
    to_agent_id: str
    reason_code: str
    task_id: str
    input_refs: list[str]
    requested_output_type: str
    allowed_tool_ids: list[str]
    max_steps: int
    status: str
    created_at: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "HandoffRequest")
        for value, field_name in (
            (self.handoff_id, "handoff_id"), (self.from_agent_id, "from_agent_id"),
            (self.to_agent_id, "to_agent_id"), (self.task_id, "task_id")
        ):
            _require_id(value, field_name)
        if self.from_agent_id == self.to_agent_id:
            raise ValidationError("HandoffRequest cannot target the same agent.")
        _require_text(self.reason_code, "reason_code")
        _validate_ref_list(self.input_refs, "HandoffRequest input_refs")
        _require_text(self.requested_output_type, "requested_output_type")
        _validate_tool_list(self.allowed_tool_ids, "HandoffRequest allowed_tool_ids")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValidationError("HandoffRequest max_steps must be a positive integer.")
        if self.status not in HANDOFF_STATUSES:
            raise ValidationError(f"Invalid HandoffRequest status: {self.status}")
        _require_text(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HandoffRequest":
        if not isinstance(data, dict):
            raise ValidationError("HandoffRequest must be a JSON object.")
        _require_keys(
            data,
            {
                "handoff_id", "from_agent_id", "to_agent_id", "reason_code", "task_id",
                "input_refs", "requested_output_type", "allowed_tool_ids", "max_steps",
                "status", "created_at"
            },
            "HandoffRequest",
        )
        return cls(**{key: data[key] for key in (
            "handoff_id", "from_agent_id", "to_agent_id", "reason_code", "task_id",
            "input_refs", "requested_output_type", "allowed_tool_ids", "max_steps",
            "status", "created_at"
        )})


@dataclass(frozen=True)
class ReviewDecision:
    review_id: str
    reviewer_agent_id: str
    subject_refs: list[str]
    decision: str
    checks: list[dict[str, Any]]
    human_approval_required: bool
    reason: str
    created_at: str

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ReviewDecision")
        _require_id(self.review_id, "review_id")
        _require_id(self.reviewer_agent_id, "reviewer_agent_id")
        _validate_ref_list(self.subject_refs, "ReviewDecision subject_refs")
        if self.decision not in REVIEW_DECISIONS:
            raise ValidationError(f"Invalid ReviewDecision decision: {self.decision}")
        if not isinstance(self.checks, list) or any(not isinstance(check, dict) for check in self.checks):
            raise ValidationError("ReviewDecision checks must be a list of objects.")
        if not isinstance(self.human_approval_required, bool):
            raise ValidationError("human_approval_required must be boolean.")
        _require_text(self.reason, "reason")
        _require_text(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        """Serialize review output; this contract has no mutation approval field."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewDecision":
        if not isinstance(data, dict):
            raise ValidationError("ReviewDecision must be a JSON object.")
        _require_keys(
            data,
            {"review_id", "reviewer_agent_id", "subject_refs", "decision", "checks", "human_approval_required", "reason", "created_at"},
            "ReviewDecision",
        )
        return cls(**{key: data[key] for key in (
            "review_id", "reviewer_agent_id", "subject_refs", "decision", "checks",
            "human_approval_required", "reason", "created_at"
        )})
