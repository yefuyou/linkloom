"""Data models and JSON-serializable state contracts for Linkloom Runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path, PureWindowsPath
import re
from typing import Any

from linkloom.runtime.errors import StateTransitionError, ValidationError


# P2 deliberately exposes only the two P1 read workflows. Later phases may
# extend this allow-list when their contracts are approved.
VALID_WORKFLOWS = {"ask", "connect"}
VALID_STATUSES = {
    "accepted",
    "running",
    "paused",
    "completed",
    "failed",
    "rejected",
    "stale",
    "expired",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "accepted": {"running", "failed", "rejected"},
    "running": {"paused", "completed", "failed", "rejected", "stale"},
    "paused": {"running", "expired", "failed", "rejected"},
    "failed": {"running"},  # explicit retry
    "completed": set(),
    "rejected": set(),
    "stale": set(),
    "expired": set(),
}


def validate_state_transition(current_status: str, next_status: str) -> str:
    """Validate that state transition follows the runtime lifecycle contract."""
    if current_status not in VALID_STATUSES:
        raise StateTransitionError(
            f"Unknown current status '{current_status}'.",
            details={"current_status": current_status, "next_status": next_status},
        )
    if next_status not in VALID_STATUSES:
        raise StateTransitionError(
            f"Unknown next status '{next_status}'.",
            details={"current_status": current_status, "next_status": next_status},
        )

    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if next_status not in allowed:
        raise StateTransitionError(
            f"Illegal state transition from '{current_status}' to '{next_status}'.",
            details={"current_status": current_status, "next_status": next_status},
        )
    return next_status


def _assert_json_safe_primitive(value: Any, field_name: str) -> None:
    """Ensure a value is composed only of strict JSON-safe primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError(
                f"Field '{field_name}' must not contain NaN or infinity.",
                details={"field": field_name, "type": type(value).__name__},
            )
        return
    if isinstance(value, (Path, PureWindowsPath)):
        raise ValidationError(
            f"Field '{field_name}' must be a string or JSON-safe primitive, got Path object.",
            details={"field": field_name, "type": type(value).__name__},
        )
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValidationError(
                    f"Field '{field_name}' contains a non-string JSON object key.",
                    details={"field": field_name, "key_type": type(k).__name__},
                )
            _assert_json_safe_primitive(k, f"{field_name}.key({k})")
            _assert_json_safe_primitive(v, f"{field_name}[{k}]")
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _assert_json_safe_primitive(item, f"{field_name}[{idx}]")
        return
    raise ValidationError(
        f"Field '{field_name}' contains a non-JSON-safe value of type {type(value).__name__}.",
        details={"field": field_name, "type": type(value).__name__},
    )


def _assert_relative_artifact_path(value: str, field_name: str) -> None:
    """Reject absolute or traversal paths from persisted runtime metadata."""
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a string path.",
            details={"field": field_name, "type": type(value).__name__},
        )
    if not value:
        raise ValidationError(
            f"Field '{field_name}' must be a non-empty relative artifact path.",
            details={"field": field_name},
        )
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValidationError(
            f"Field '{field_name}' must be a relative artifact path.",
            details={"field": field_name},
        )
    if any(part == ".." for part in value.replace("\\", "/").split("/")):
        raise ValidationError(
            f"Field '{field_name}' must not traverse outside the artifact root.",
            details={"field": field_name},
        )


def _assert_sha256(value: str, field_name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValidationError(
            f"Field '{field_name}' must be a 64-character lowercase SHA-256 hex string.",
            details={"field": field_name},
        )


def _require_keys(data: dict[str, Any], required: set[str], model_name: str) -> None:
    missing = sorted(required - data.keys())
    if missing:
        raise ValidationError(
            f"{model_name} is missing required fields: {', '.join(missing)}.",
            details={"model": model_name, "missing": missing},
        )


@dataclass(frozen=True)
class SourceContext:
    index_path: str = ""
    index_sha256: str = ""
    index_schema_version: int = 1
    vault_root_fingerprint: str = ""
    fixture_id: str | None = None
    document_count: int = 0

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "SourceContext")
        _assert_relative_artifact_path(self.index_path, "SourceContext.index_path")
        _assert_sha256(self.index_sha256, "SourceContext.index_sha256")
        if not self.vault_root_fingerprint:
            raise ValidationError("SourceContext.vault_root_fingerprint cannot be empty.")
        if self.document_count < 0:
            raise ValidationError("SourceContext.document_count must be non-negative.")
        if self.index_schema_version != 1:
            raise ValidationError(
                f"Unsupported index schema version {self.index_schema_version}.",
                details={"index_schema_version": self.index_schema_version},
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceContext:
        if not isinstance(data, dict):
            raise ValidationError("SourceContext must be a JSON object.")
        _require_keys(
            data,
            {"index_sha256", "index_schema_version", "vault_root_fingerprint"},
            "SourceContext",
        )
        return cls(
            index_path=str(data.get("index_path", "")),
            index_sha256=str(data.get("index_sha256", "")),
            index_schema_version=int(data.get("index_schema_version", 1)),
            vault_root_fingerprint=str(data.get("vault_root_fingerprint", "")),
            fixture_id=data.get("fixture_id"),
            document_count=int(data.get("document_count", 0)),
        )


@dataclass(frozen=True)
class UsageEnvelope:
    step_count: int = 0
    provider_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "UsageEnvelope")
        if min(
            self.step_count,
            self.provider_requests,
            self.input_tokens,
            self.output_tokens,
            self.estimated_cost_usd,
        ) < 0:
            raise ValidationError("UsageEnvelope counters and cost must be non-negative.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageEnvelope:
        if not isinstance(data, dict):
            raise ValidationError("UsageEnvelope must be a JSON object.")
        return cls(
            step_count=int(data.get("step_count", 0)),
            provider_requests=int(data.get("provider_requests", 0)),
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            estimated_cost_usd=float(data.get("estimated_cost_usd", 0.0)),
        )


@dataclass(frozen=True)
class PolicySnapshot:
    policy_version: str = "p2-readonly-v1"
    write_capability: bool = False
    network_capability: bool = False
    max_steps: int = 12
    max_provider_requests: int = 0

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "PolicySnapshot")
        if self.policy_version != "p2-readonly-v1":
            raise ValidationError(
                f"Unsupported policy version '{self.policy_version}'.",
                details={"policy_version": self.policy_version},
            )
        if self.write_capability or self.network_capability:
            raise ValidationError(
                "P2 policy cannot enable write or network capability.",
                details={
                    "write_capability": self.write_capability,
                    "network_capability": self.network_capability,
                },
            )
        if self.max_steps < 1 or self.max_provider_requests < 0:
            raise ValidationError("Policy limits must be max_steps >= 1 and provider requests >= 0.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicySnapshot:
        if not isinstance(data, dict):
            raise ValidationError("PolicySnapshot must be a JSON object.")
        _require_keys(
            data,
            {"policy_version", "write_capability", "network_capability"},
            "PolicySnapshot",
        )
        return cls(
            policy_version=str(data.get("policy_version", "p2-readonly-v1")),
            write_capability=bool(data.get("write_capability", False)),
            network_capability=bool(data.get("network_capability", False)),
            max_steps=int(data.get("max_steps", 12)),
            max_provider_requests=int(data.get("max_provider_requests", 0)),
        )


@dataclass(frozen=True)
class ErrorEnvelope:
    code: str
    category: str
    message: str
    retryable: bool = False
    affected_refs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    safe_to_expose: bool = True

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ErrorEnvelope")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorEnvelope:
        if not isinstance(data, dict):
            raise ValidationError("ErrorEnvelope must be a JSON object.")
        _require_keys(data, {"code", "category", "message"}, "ErrorEnvelope")
        return cls(
            code=str(data.get("code", "UNKNOWN_ERROR")),
            category=str(data.get("category", "runtime")),
            message=str(data.get("message", "")),
            retryable=bool(data.get("retryable", False)),
            affected_refs=list(data.get("affected_refs", [])),
            details=dict(data.get("details", {})),
            safe_to_expose=bool(data.get("safe_to_expose", True)),
        )


@dataclass(frozen=True)
class InterruptEnvelope:
    interrupt_id: str
    kind: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    allowed_responses: list[str] = field(default_factory=lambda: ["resume", "reject"])
    checkpoint_id: str = ""
    created_at: str = ""
    expires_at: str | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "InterruptEnvelope")
        if not self.interrupt_id or not self.message:
            raise ValidationError("InterruptEnvelope requires interrupt_id and message.")
        if not self.allowed_responses or set(self.allowed_responses) - {"resume", "reject"}:
            raise ValidationError(
                "P2 interrupts only allow non-empty resume/reject responses.",
                details={"allowed_responses": self.allowed_responses},
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InterruptEnvelope:
        if not isinstance(data, dict):
            raise ValidationError("InterruptEnvelope must be a JSON object.")
        _require_keys(data, {"interrupt_id", "kind", "message"}, "InterruptEnvelope")
        return cls(
            interrupt_id=str(data.get("interrupt_id", "")),
            kind=str(data.get("kind", "clarification_required")),
            message=str(data.get("message", "")),
            payload=dict(data.get("payload", {})),
            allowed_responses=list(data.get("allowed_responses", ["resume", "reject"])),
            checkpoint_id=str(data.get("checkpoint_id", "")),
            created_at=str(data.get("created_at", "")),
            expires_at=data.get("expires_at"),
        )


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    node_name: str
    step_seq: int
    input_sha256: str
    idempotency_key: str
    status: str = "started"
    retry_index: int = 0
    output_ref: str | None = None
    error_code: str | None = None
    started_at: str = ""
    finished_at: str | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "AttemptRecord")
        if self.step_seq < 0 or self.retry_index < 0:
            raise ValidationError("AttemptRecord step_seq and retry_index must be non-negative.")
        if self.status not in {"started", "completed", "failed", "skipped"}:
            raise ValidationError(f"Unsupported attempt status '{self.status}'.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttemptRecord:
        if not isinstance(data, dict):
            raise ValidationError("AttemptRecord must be a JSON object.")
        _require_keys(
            data,
            {"attempt_id", "node_name", "step_seq", "input_sha256", "idempotency_key"},
            "AttemptRecord",
        )
        return cls(
            attempt_id=str(data.get("attempt_id", "")),
            node_name=str(data.get("node_name", "")),
            step_seq=int(data.get("step_seq", 0)),
            input_sha256=str(data.get("input_sha256", "")),
            idempotency_key=str(data.get("idempotency_key", "")),
            status=str(data.get("status", "started")),
            retry_index=int(data.get("retry_index", 0)),
            output_ref=data.get("output_ref"),
            error_code=data.get("error_code"),
            started_at=str(data.get("started_at", "")),
            finished_at=data.get("finished_at"),
        )


@dataclass(frozen=True)
class RunRequest:
    request_id: str
    workflow: str
    query: str
    schema_version: int = 1
    thread_id: str | None = None
    vault_root: str | None = None
    index_path: str | None = None
    max_steps: int = 12
    max_provider_requests: int = 0
    interrupt_policy: str = "clarify_only"
    dry_run: bool = True
    caller: str = "cli"
    received_at: str = ""

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "RunRequest")
        if not self.request_id:
            raise ValidationError("RunRequest.request_id cannot be empty.")
        if not isinstance(self.thread_id, (str, type(None))):
            raise ValidationError("RunRequest.thread_id must be a string or null.")
        if self.vault_root is not None:
            if not isinstance(self.vault_root, str):
                raise ValidationError("RunRequest.vault_root must be a process-only string.")
        if self.index_path is not None:
            _assert_relative_artifact_path(self.index_path, "RunRequest.index_path")

        if self.schema_version != 1:
            raise ValidationError(
                f"Unsupported RunRequest schema_version {self.schema_version}.",
                details={"schema_version": self.schema_version},
            )
        if self.workflow not in VALID_WORKFLOWS:
            raise ValidationError(
                f"Unsupported workflow '{self.workflow}'. Expected one of {sorted(VALID_WORKFLOWS)}.",
                details={"workflow": self.workflow},
            )
        if not self.query.strip():
            raise ValidationError("query cannot be empty.", details={"request_id": self.request_id})
        if self.max_steps < 1 or self.max_provider_requests < 0:
            raise ValidationError("RunRequest limits must be max_steps >= 1 and provider requests >= 0.")
        if self.interrupt_policy != "clarify_only":
            raise ValidationError("P2 interrupt_policy must be 'clarify_only'.")

    def to_dict(self, *, include_process_fields: bool = False) -> dict[str, Any]:
        """Return a safe request envelope; vault_root is process-only by default."""
        data = asdict(self)
        if not include_process_fields:
            data.pop("vault_root", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRequest:
        if not isinstance(data, dict):
            raise ValidationError("RunRequest must be a JSON object.")
        _require_keys(data, {"request_id", "workflow", "query"}, "RunRequest")
        return cls(
            request_id=str(data.get("request_id", "")),
            workflow=str(data.get("workflow", "ask")),
            query=str(data.get("query", "")),
            schema_version=int(data.get("schema_version", 1)),
            thread_id=data.get("thread_id"),
            vault_root=data.get("vault_root"),
            index_path=data.get("index_path"),
            max_steps=int(data.get("max_steps", 12)),
            max_provider_requests=int(data.get("max_provider_requests", 0)),
            interrupt_policy=str(data.get("interrupt_policy", "clarify_only")),
            dry_run=bool(data.get("dry_run", True)),
            caller=str(data.get("caller", "cli")),
            received_at=str(data.get("received_at", "")),
        )


AGENT_TURN_STATUSES = {"pending", "running", "completed", "failed", "terminated"}
TERMINATION_STATUSES = {
    "running",
    "completed",
    "failed",
    "budget_exhausted",
    "terminated",
}
TOOL_EXECUTION_STATUSES = {"pending", "completed", "failed"}
_STATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _require_state_id(value: Any, field_name: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or _STATE_ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a stable identifier.")


def _require_optional_artifact_ref(value: Any, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a relative artifact reference or null.")
    _assert_relative_artifact_path(value, field_name)


_FORBIDDEN_PERSISTED_KEYS = {
    "api_key",
    "api_token",
    "access_token",
    "authorization",
    "chain_of_thought",
    "credential",
    "credentials",
    "hidden_reasoning",
    "password",
    "provider_reasoning",
    "raw_request",
    "raw_response",
    "raw_secret",
    "raw_token",
    "reasoning",
    "secret",
    "secret_value",
    "stack_trace",
    "token",
    "token_value",
    "traceback",
    "raw_traceback",
}


def _assert_no_forbidden_persisted_keys(value: Any, field_name: str) -> None:
    """Reject fields that would persist secrets or hidden model reasoning."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in _FORBIDDEN_PERSISTED_KEYS:
                raise ValidationError(
                    f"Field '{field_name}' contains a forbidden persisted key.",
                    details={"field": field_name, "key": key},
                )
            _assert_no_forbidden_persisted_keys(nested, f"{field_name}[{key}]")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_forbidden_persisted_keys(nested, f"{field_name}[{index}]")


MAX_INLINE_TOOL_RESULT_BYTES = 128 * 1024


def _assert_inline_checkpoint_size(value: Any, field_name: str) -> None:
    """Keep checkpoint-inline tool outcomes bounded; large values need an artifact ref."""
    try:
        size = len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{field_name} cannot be serialized for checkpoint storage.") from error
    if size > MAX_INLINE_TOOL_RESULT_BYTES:
        raise ValidationError(
            f"{field_name} exceeds the inline checkpoint size limit.",
            details={
                "field": field_name,
                "max_bytes": MAX_INLINE_TOOL_RESULT_BYTES,
                "actual_bytes": size,
                "reason": "inline_result_too_large",
            },
        )


@dataclass(frozen=True)
class AgentTurn:
    """Runtime-owned cursor for one agent decision turn.

    This is intentionally not a message or observation model.  It records
    identity and lifecycle only, plus safe references to future model
    exchange artifacts and the tool calls associated with this turn.
    """

    turn_id: str
    run_id: str
    task_id: str
    agent_id: str
    sequence: int
    status: str = "running"
    tool_call_ids: list[str] = field(default_factory=list)
    model_request_ref: str | None = None
    model_response_ref: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "AgentTurn")
        for field_name, value in (
            ("turn_id", self.turn_id),
            ("run_id", self.run_id),
            ("task_id", self.task_id),
            ("agent_id", self.agent_id),
        ):
            _require_state_id(value, f"AgentTurn.{field_name}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValidationError("AgentTurn.sequence must be a non-negative integer.")
        if self.status not in AGENT_TURN_STATUSES:
            raise ValidationError(
                f"AgentTurn.status must be one of {sorted(AGENT_TURN_STATUSES)}."
            )
        if not isinstance(self.tool_call_ids, list):
            raise ValidationError("AgentTurn.tool_call_ids must be a list.")
        for index, call_id in enumerate(self.tool_call_ids):
            _require_state_id(call_id, f"AgentTurn.tool_call_ids[{index}]")
        _require_optional_artifact_ref(self.model_request_ref, "AgentTurn.model_request_ref")
        _require_optional_artifact_ref(self.model_response_ref, "AgentTurn.model_response_ref")
        if not isinstance(self.usage, dict):
            raise ValidationError("AgentTurn.usage must be a JSON object.")
        _assert_json_safe_primitive(self.usage, "AgentTurn.usage")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentTurn":
        if not isinstance(data, dict):
            raise ValidationError("AgentTurn must be a JSON object.")
        _require_keys(
            data,
            {"turn_id", "run_id", "task_id", "agent_id", "sequence", "status"},
            "AgentTurn",
        )
        return cls(
            turn_id=data["turn_id"],
            run_id=data["run_id"],
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            sequence=data["sequence"],
            status=data.get("status", "running"),
            tool_call_ids=list(data.get("tool_call_ids", [])),
            model_request_ref=data.get("model_request_ref"),
            model_response_ref=data.get("model_response_ref"),
            usage=dict(data.get("usage", {})),
        )


@dataclass(frozen=True)
class TerminationState:
    """Runtime authority for agent-loop termination, separate from model output."""

    status: str = "running"
    reason_code: str | None = None
    reason: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "TerminationState")
        if self.status not in TERMINATION_STATUSES:
            raise ValidationError(
                f"TerminationState.status must be one of {sorted(TERMINATION_STATUSES)}."
            )
        if self.reason_code is not None:
            _require_state_id(self.reason_code, "TerminationState.reason_code")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValidationError("TerminationState.reason must be a non-empty string or null.")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValidationError("TerminationState.sequence must be a non-negative integer.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminationState":
        if not isinstance(data, dict):
            raise ValidationError("TerminationState must be a JSON object.")
        return cls(
            status=str(data.get("status", "running")),
            reason_code=data.get("reason_code"),
            reason=data.get("reason"),
            sequence=int(data.get("sequence", 0)),
        )


@dataclass(frozen=True)
class ToolExecutionRecord:
    """Durable, safe summary of one authorized tool execution."""

    call_id: str
    run_id: str | None
    task_id: str | None
    agent_id: str | None
    tool_id: str
    sequence: int
    status: str
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    idempotency_key: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolExecutionRecord")
        for field_name, value in (
            ("call_id", self.call_id),
            ("tool_id", self.tool_id),
        ):
            _require_state_id(value, f"ToolExecutionRecord.{field_name}")
        for field_name, value in (
            ("run_id", self.run_id),
            ("task_id", self.task_id),
            ("agent_id", self.agent_id),
            ("idempotency_key", self.idempotency_key),
        ):
            _require_state_id(value, f"ToolExecutionRecord.{field_name}", allow_none=True)
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValidationError("ToolExecutionRecord.sequence must be a non-negative integer.")
        if self.status not in TOOL_EXECUTION_STATUSES:
            raise ValidationError(
                f"ToolExecutionRecord.status must be one of {sorted(TOOL_EXECUTION_STATUSES)}."
            )
        if not isinstance(self.arguments, dict):
            raise ValidationError("ToolExecutionRecord.arguments must be a JSON object.")
        _assert_json_safe_primitive(self.arguments, "ToolExecutionRecord.arguments")
        _assert_no_forbidden_persisted_keys(self.arguments, "ToolExecutionRecord.arguments")
        for field_name, value in (("result", self.result), ("error", self.error)):
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"ToolExecutionRecord.{field_name} must be an object or null.")
            if value is not None:
                _assert_json_safe_primitive(value, f"ToolExecutionRecord.{field_name}")
                _assert_no_forbidden_persisted_keys(value, f"ToolExecutionRecord.{field_name}")
        if self.result is not None:
            _assert_inline_checkpoint_size(self.result, "ToolExecutionRecord.result")
        if self.status == "pending" and (self.result is not None or self.error is not None):
            raise ValidationError("Pending ToolExecutionRecord cannot contain result or error.")
        if self.status == "completed" and self.result is None:
            raise ValidationError("Completed ToolExecutionRecord requires a result.")
        if self.status == "failed" and self.error is None:
            raise ValidationError("Failed ToolExecutionRecord requires an error.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolExecutionRecord":
        if not isinstance(data, dict):
            raise ValidationError("ToolExecutionRecord must be a JSON object.")
        _require_keys(
            data,
            {"call_id", "tool_id", "sequence", "status"},
            "ToolExecutionRecord",
        )
        return cls(
            call_id=data["call_id"],
            run_id=data.get("run_id"),
            task_id=data.get("task_id"),
            agent_id=data.get("agent_id"),
            tool_id=data["tool_id"],
            sequence=data["sequence"],
            status=data["status"],
            arguments=dict(data.get("arguments", {})),
            result=data.get("result"),
            error=data.get("error"),
            idempotency_key=data.get("idempotency_key"),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


MODEL_EXECUTION_STATUSES = {
    "request_durable",
    "request_sent",
    "response_obtained",
    "response_durable",
    "tool_result_durable",
    "completed",
    "failed",
    "reinvoke_allowed",
}


@dataclass(frozen=True)
class ModelExecutionRecord:
    """Durable lifecycle record for one provider-neutral model turn."""

    run_id: str
    turn_id: str
    task_id: str
    agent_id: str
    sequence: int
    status: str
    request_ref: str | None = None
    tool_definition_snapshot_ref: str | None = None
    observation_ref: str | None = None
    response_ref: str | None = None
    normalized_action: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    request_sha256: str | None = None
    response_sha256: str | None = None
    observation_sha256: str | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ModelExecutionRecord")
        for field_name, value in (
            ("run_id", self.run_id),
            ("turn_id", self.turn_id),
            ("task_id", self.task_id),
            ("agent_id", self.agent_id),
        ):
            _require_state_id(value, f"ModelExecutionRecord.{field_name}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValidationError("ModelExecutionRecord.sequence must be a non-negative integer.")
        if self.status not in MODEL_EXECUTION_STATUSES:
            raise ValidationError(
                "ModelExecutionRecord.status must be one of "
                f"{sorted(MODEL_EXECUTION_STATUSES)}."
            )
        for field_name, value in (
            ("request_ref", self.request_ref),
            ("tool_definition_snapshot_ref", self.tool_definition_snapshot_ref),
            ("observation_ref", self.observation_ref),
            ("response_ref", self.response_ref),
        ):
            _require_optional_artifact_ref(value, f"ModelExecutionRecord.{field_name}")
        if self.normalized_action is not None:
            if not isinstance(self.normalized_action, dict):
                raise ValidationError("ModelExecutionRecord.normalized_action must be an object or null.")
            _assert_no_forbidden_persisted_keys(self.normalized_action, "ModelExecutionRecord.normalized_action")
        for field_name, value in (("usage", self.usage), ("provider_metadata", self.provider_metadata)):
            if not isinstance(value, dict):
                raise ValidationError(f"ModelExecutionRecord.{field_name} must be a JSON object.")
            _assert_no_forbidden_persisted_keys(value, f"ModelExecutionRecord.{field_name}")
        for field_name, value in (
            ("request_sha256", self.request_sha256),
            ("response_sha256", self.response_sha256),
            ("observation_sha256", self.observation_sha256),
        ):
            if value is not None:
                _assert_sha256(value, f"ModelExecutionRecord.{field_name}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelExecutionRecord":
        if not isinstance(data, dict):
            raise ValidationError("ModelExecutionRecord must be a JSON object.")
        _require_keys(
            data,
            {"run_id", "turn_id", "task_id", "agent_id", "sequence", "status"},
            "ModelExecutionRecord",
        )
        return cls(
            run_id=data["run_id"],
            turn_id=data["turn_id"],
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            sequence=data["sequence"],
            status=data["status"],
            request_ref=data.get("request_ref"),
            tool_definition_snapshot_ref=data.get("tool_definition_snapshot_ref"),
            observation_ref=data.get("observation_ref"),
            response_ref=data.get("response_ref"),
            normalized_action=data.get("normalized_action"),
            usage=dict(data.get("usage", {})),
            provider_metadata=dict(data.get("provider_metadata", {})),
            request_sha256=data.get("request_sha256"),
            response_sha256=data.get("response_sha256"),
            observation_sha256=data.get("observation_sha256"),
        )


@dataclass(frozen=True)
class RuntimeState:
    schema_version: int
    run_id: str
    thread_id: str
    workflow: str
    status: str
    step_seq: int
    request_ref: str
    source: SourceContext
    parent_run_id: str | None = None
    current_step: str | None = None
    intent: dict[str, Any] | None = None
    evidence_refs: list[str | dict[str, Any]] = field(default_factory=list)
    agent_tasks: list[dict[str, Any]] = field(default_factory=list)
    pending_interrupt: InterruptEnvelope | None = None
    result_ref: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)
    error: ErrorEnvelope | None = None
    usage: UsageEnvelope = field(default_factory=UsageEnvelope)
    policy: PolicySnapshot = field(default_factory=PolicySnapshot)
    memory_refs: list[dict[str, Any]] = field(default_factory=list)
    turns: list[AgentTurn] = field(default_factory=list)
    tool_ledger: list[ToolExecutionRecord] = field(default_factory=list)
    termination: TerminationState | None = None
    model_executions: list[ModelExecutionRecord] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.run_id or not self.thread_id or not self.request_ref:
            raise ValidationError("RuntimeState requires non-empty run_id, thread_id and request_ref.")
        if not isinstance(self.source, SourceContext):
            raise ValidationError("RuntimeState.source must be a SourceContext.")
        if not isinstance(self.policy, PolicySnapshot):
            raise ValidationError("RuntimeState.policy must be a PolicySnapshot.")
        if not isinstance(self.usage, UsageEnvelope):
            raise ValidationError("RuntimeState.usage must be a UsageEnvelope.")
        if not isinstance(self.memory_refs, list):
            raise ValidationError("RuntimeState.memory_refs must be a list.")
        for index, ref in enumerate(self.memory_refs):
            if not isinstance(ref, dict):
                raise ValidationError(f"RuntimeState.memory_refs[{index}] must be an object.")
            allowed = {"memory_id", "scope", "key", "value_sha256"}
            if set(ref) - allowed or not all(isinstance(ref.get(key), str) for key in allowed):
                raise ValidationError(
                    "RuntimeState.memory_refs must contain only memory_id, scope, key and value_sha256 strings."
                )
            _assert_sha256(ref["memory_id"], f"RuntimeState.memory_refs[{index}].memory_id")
            _assert_sha256(ref["value_sha256"], f"RuntimeState.memory_refs[{index}].value_sha256")
            if ref["scope"] not in {"global", "project", "workflow", "thread"}:
                raise ValidationError(f"Unsupported memory scope: {ref['scope']}")
            if not ref["key"] or len(ref["key"]) > 128:
                raise ValidationError("RuntimeState.memory_refs key must be a short identifier.")

        if not isinstance(self.turns, list):
            raise ValidationError("RuntimeState.turns must be a list.")
        for index, turn in enumerate(self.turns):
            if not isinstance(turn, AgentTurn):
                raise ValidationError(f"RuntimeState.turns[{index}] must be an AgentTurn.")

        if not isinstance(self.tool_ledger, list):
            raise ValidationError("RuntimeState.tool_ledger must be a list.")
        seen_call_ids: set[str] = set()
        for index, record in enumerate(self.tool_ledger):
            if not isinstance(record, ToolExecutionRecord):
                raise ValidationError(
                    f"RuntimeState.tool_ledger[{index}] must be a ToolExecutionRecord."
                )
            if record.call_id in seen_call_ids:
                raise ValidationError(
                    "RuntimeState.tool_ledger cannot contain duplicate call_id values.",
                    details={"call_id": record.call_id, "reason": "duplicate_call_id"},
                )
            seen_call_ids.add(record.call_id)
            if record.run_id is not None and record.run_id != self.run_id:
                raise ValidationError(
                    "RuntimeState.tool_ledger contains a record from another run.",
                    details={"run_id": record.run_id, "state_run_id": self.run_id},
                )

        if self.termination is not None and not isinstance(self.termination, TerminationState):
            raise ValidationError("RuntimeState.termination must be a TerminationState or None.")

        if not isinstance(self.model_executions, list):
            raise ValidationError("RuntimeState.model_executions must be a list.")
        seen_turn_ids: set[str] = set()
        for index, record in enumerate(self.model_executions):
            if not isinstance(record, ModelExecutionRecord):
                raise ValidationError(
                    f"RuntimeState.model_executions[{index}] must be a ModelExecutionRecord."
                )
            if record.turn_id in seen_turn_ids:
                raise ValidationError(
                    "RuntimeState.model_executions cannot contain duplicate turn_id values.",
                    details={"turn_id": record.turn_id, "reason": "duplicate_turn_id"},
                )
            seen_turn_ids.add(record.turn_id)
            if record.run_id != self.run_id:
                raise ValidationError(
                    "RuntimeState.model_executions contains a record from another run.",
                    details={"run_id": record.run_id, "state_run_id": self.run_id},
                )

        if self.schema_version != 1:
            raise ValidationError(
                f"Unsupported RuntimeState schema_version {self.schema_version}.",
                details={"schema_version": self.schema_version},
            )
        if self.workflow not in VALID_WORKFLOWS:
            raise ValidationError(
                f"Unsupported workflow '{self.workflow}'.",
                details={"workflow": self.workflow},
            )
        if self.status not in VALID_STATUSES:
            raise ValidationError(
                f"Unsupported status '{self.status}'.",
                details={"status": self.status},
            )
        if self.step_seq < 0:
            raise ValidationError(
                f"step_seq must be non-negative, got {self.step_seq}.",
                details={"step_seq": self.step_seq},
            )

        # Invariant checks
        if self.status == "paused" and self.pending_interrupt is None:
            raise ValidationError(
                "RuntimeState with status='paused' requires a non-null pending_interrupt.",
                details={"status": self.status, "run_id": self.run_id},
            )
        if self.status != "paused" and self.pending_interrupt is not None:
            raise ValidationError(
                f"RuntimeState with status='{self.status}' must have pending_interrupt=None. (pending_interrupt must be None when not paused)",
                details={"status": self.status, "run_id": self.run_id},
            )
        if self.status == "completed" and not self.result_ref:
            raise ValidationError(
                "RuntimeState with status='completed' requires a non-empty result_ref.",
                details={"status": self.status, "run_id": self.run_id},
            )
        if self.status in ("failed", "rejected", "stale", "expired") and self.error is None:
            raise ValidationError(
                f"RuntimeState with status='{self.status}' requires a non-null error envelope.",
                details={"status": self.status, "run_id": self.run_id},
            )

        # Validate every serialized field, including nested evidence and
        # intent, instead of trusting only a few scalar fields above.
        _assert_json_safe_primitive(self.to_dict(), "RuntimeState")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "parent_run_id": self.parent_run_id,
            "workflow": self.workflow,
            "status": self.status,
            "current_step": self.current_step,
            "step_seq": self.step_seq,
            "request_ref": self.request_ref,
            "intent": self.intent,
            "source": self.source.to_dict(),
            "evidence_refs": self.evidence_refs,
            "agent_tasks": self.agent_tasks,
            "pending_interrupt": (
                self.pending_interrupt.to_dict() if self.pending_interrupt else None
            ),
            "result_ref": self.result_ref,
            "attempts": [a.to_dict() for a in self.attempts],
            "error": self.error.to_dict() if self.error else None,
            "usage": self.usage.to_dict(),
            "policy": self.policy.to_dict(),
            "memory_refs": self.memory_refs,
            "turns": [turn.to_dict() for turn in self.turns],
            "tool_ledger": [record.to_dict() for record in self.tool_ledger],
            "termination": self.termination.to_dict() if self.termination else None,
            "model_executions": [record.to_dict() for record in self.model_executions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeState:
        if not isinstance(data, dict):
            raise ValidationError("RuntimeState must be a JSON object.")
        _require_keys(
            data,
            {
                "schema_version",
                "run_id",
                "thread_id",
                "workflow",
                "status",
                "step_seq",
                "request_ref",
                "source",
                "evidence_refs",
                "pending_interrupt",
                "result_ref",
                "attempts",
                "error",
                "usage",
                "policy",
                "created_at",
                "updated_at",
            },
            "RuntimeState",
        )
        source_data = data.get("source", {})
        source = (
            source_data
            if isinstance(source_data, SourceContext)
            else SourceContext.from_dict(source_data)
        )

        pending_interrupt_data = data.get("pending_interrupt")
        pending_interrupt = (
            InterruptEnvelope.from_dict(pending_interrupt_data)
            if pending_interrupt_data
            else None
        )

        error_data = data.get("error")
        error = ErrorEnvelope.from_dict(error_data) if error_data else None

        usage_data = data.get("usage", {})
        usage = usage_data if isinstance(usage_data, UsageEnvelope) else UsageEnvelope.from_dict(usage_data)

        policy_data = data.get("policy", {})
        policy = policy_data if isinstance(policy_data, PolicySnapshot) else PolicySnapshot.from_dict(policy_data)

        attempts = [
            a if isinstance(a, AttemptRecord) else AttemptRecord.from_dict(a)
            for a in data.get("attempts", [])
        ]

        turns = [
            turn if isinstance(turn, AgentTurn) else AgentTurn.from_dict(turn)
            for turn in data.get("turns", [])
        ]
        tool_ledger = [
            record
            if isinstance(record, ToolExecutionRecord)
            else ToolExecutionRecord.from_dict(record)
            for record in data.get("tool_ledger", [])
        ]
        model_executions = [
            record
            if isinstance(record, ModelExecutionRecord)
            else ModelExecutionRecord.from_dict(record)
            for record in data.get("model_executions", [])
        ]
        termination_data = data.get("termination")
        termination = (
            termination_data
            if isinstance(termination_data, TerminationState)
            else TerminationState.from_dict(termination_data)
            if isinstance(termination_data, dict)
            else None
        )

        if not isinstance(source_data, (dict, SourceContext)):
            raise ValidationError("RuntimeState.source must be a JSON object.")
        if not isinstance(data.get("request_ref"), str):
            raise ValidationError("RuntimeState.request_ref must be a string artifact id.")

        return cls(
            schema_version=int(data.get("schema_version", 1)),
            run_id=str(data.get("run_id", "")),
            thread_id=str(data.get("thread_id", "")),
            parent_run_id=data.get("parent_run_id"),
            workflow=str(data.get("workflow", "ask")),
            status=str(data.get("status", "running")),
            current_step=data.get("current_step"),
            step_seq=int(data.get("step_seq", 0)),
            request_ref=data["request_ref"],
            intent=data.get("intent"),
            source=source,
            evidence_refs=list(data.get("evidence_refs", [])),
            agent_tasks=list(data.get("agent_tasks", [])),
            pending_interrupt=pending_interrupt,
            result_ref=data.get("result_ref"),
            attempts=attempts,
            error=error,
            usage=usage,
            policy=policy,
            memory_refs=list(data.get("memory_refs", [])),
            turns=turns,
            tool_ledger=tool_ledger,
            termination=termination,
            model_executions=model_executions,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    thread_id: str
    status: str
    current_step: str | None = None
    checkpoint_id: str | None = None
    interrupt: dict[str, Any] | None = None
    result_ref: str | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStatus:
        return cls(
            run_id=str(data.get("run_id", "")),
            thread_id=str(data.get("thread_id", "")),
            status=str(data.get("status", "")),
            current_step=data.get("current_step"),
            checkpoint_id=data.get("checkpoint_id"),
            interrupt=data.get("interrupt"),
            result_ref=data.get("result_ref"),
            error=data.get("error"),
        )
