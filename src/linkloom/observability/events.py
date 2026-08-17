"""Versioned, structured trace events for LinkLoom."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import re
from typing import Any, Mapping, Iterable
from uuid import uuid4

from linkloom.observability.redaction import (
    DEFAULT_REDACTION_POLICY_VERSION,
    RedactionPolicy,
    safe_ref,
)
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ErrorEnvelope


EVENT_TYPES = frozenset(
    {
        "run.accepted",
        "run.started",
        "run.completed",
        "run.failed",
        "run.stale",
        "step.started",
        "step.completed",
        "step.failed",
        "tool.called",
        "tool.completed",
        "tool.failed",
        "provider.requested",
        "provider.completed",
        "provider.failed",
        "checkpoint.saved",
        "checkpoint.failed",
        "interrupt.raised",
        "interrupt.resumed",
        "retry.scheduled",
        "retry.exhausted",
        "policy.rejected",
        "artifact.written",
        "agent.task.created",
        "agent.task.started",
        "agent.task.completed",
        "agent.task.failed",
        "handoff.requested",
        "handoff.accepted",
        "handoff.rejected",
        "agent.fallback.used",
        "memory.injected",
        "writeback.proposed",
        "writeback.previewed",
        "writeback.approval.requested",
        "writeback.approval.accepted",
        "writeback.approval.rejected",
        "writeback.checkpointed",
        "writeback.resumed",
        "writeback.hash_validated",
        "writeback.started",
        "writeback.completed",
        "writeback.failed",
        "writeback.verified",
        "writeback.rollback",
    }
)
ACTORS = frozenset({"runtime", "service", "provider", "agent", "human"})
STATUSES = frozenset({"started", "ok", "paused", "failed", "rejected", "stale"})
_RFC3339_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

# Compatibility aliases for callers of the first A1 draft.
VALID_EVENT_TYPES = EVENT_TYPES
VALID_ACTORS = ACTORS
VALID_STATUSES = STATUSES


def _validate_json(value: Any, field_name: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValidationError(f"{field_name} must contain finite JSON numbers")
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _validate_json(item, f"{field_name}[{idx}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError(f"{field_name} has a non-string object key")
            _validate_json(item, f"{field_name}.{key}")
        return
    raise ValidationError(f"{field_name} contains non-JSON value {type(value).__name__}")


def _validate_timestamp(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ValidationError(f"Invalid {field_name} format: {value}")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Invalid {field_name} value: {value}") from exc


def _error_to_dict(error: Any) -> dict[str, Any] | None:
    if error is None:
        return None
    if isinstance(error, ErrorEnvelope):
        return error.to_dict()
    if isinstance(error, Mapping):
        result = dict(error)
    elif hasattr(error, "to_envelope"):
        result = error.to_envelope()
    elif hasattr(error, "to_dict"):
        result = error.to_dict()
    else:
        raise ValidationError("TraceEvent.error must be an ErrorEnvelope or JSON object")
    _validate_json(result, "error")
    return result


def _validate_ref(ref: Any, field_name: str) -> None:
    if ref is None:
        return
    if not isinstance(ref, dict):
        raise ValidationError(f"{field_name} must be a JSON object or null")
    _validate_json(ref, field_name)
    for key, value in ref.items():
        lowered = key.lower()
        if lowered in {"prompt", "quote", "content", "password", "token", "secret"}:
            raise ValidationError(f"{field_name} contains prohibited raw field '{key}'")
        if key in {"path", "relative_path"} and isinstance(value, str):
            if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
                raise ValidationError(f"{field_name}.{key} must not be absolute")


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    run_id: str
    thread_id: str
    seq: int
    event_type: str
    actor: str
    status: str
    redaction: dict[str, Any] = field(default_factory=lambda: {"policy_version": DEFAULT_REDACTION_POLICY_VERSION})
    schema_version: int = 1
    parent_event_id: str | None = None
    node_name: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    input_ref: dict[str, Any] | None = None
    output_ref: dict[str, Any] | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: Any = None

    def __post_init__(self) -> None:
        for name in ("event_id", "run_id", "thread_id", "event_type", "actor", "status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValidationError(f"TraceEvent.{name} must be a non-empty string")
        if self.schema_version != 1:
            raise ValidationError(f"Unsupported trace schema_version: {self.schema_version}")
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 1:
            raise ValidationError(f"TraceEvent.seq must be a positive integer, got {self.seq}")
        if self.event_type not in EVENT_TYPES:
            raise ValidationError(f"Unknown event_type: {self.event_type}")
        if self.actor not in ACTORS:
            raise ValidationError(f"Unknown actor: {self.actor}")
        if self.status not in STATUSES:
            raise ValidationError(f"Unknown status: {self.status}")
        if self.duration_ms is not None and (
            isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0
        ):
            raise ValidationError(f"duration_ms must be a non-negative integer, got {self.duration_ms}")
        _validate_timestamp(self.started_at, "started_at")
        _validate_timestamp(self.finished_at, "finished_at")
        if self.started_at and self.finished_at:
            start = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            finish = datetime.fromisoformat(self.finished_at.replace("Z", "+00:00"))
            if finish < start:
                raise ValidationError("finished_at cannot precede started_at")
        if self.status in {"failed", "rejected", "stale"} and self.error is None:
            raise ValidationError("Error envelope is required for failed, rejected, or stale events")
        if not isinstance(self.redaction, dict):
            raise ValidationError("TraceEvent.redaction must be a JSON object")
        _validate_json(self.redaction, "redaction")
        if self.redaction.get("policy_version", DEFAULT_REDACTION_POLICY_VERSION) != DEFAULT_REDACTION_POLICY_VERSION:
            raise ValidationError("TraceEvent uses unsupported redaction policy")
        if not isinstance(self.attributes, dict):
            raise ValidationError("TraceEvent.attributes must be a JSON object")
        _validate_json(self.attributes, "attributes")
        _validate_ref(self.input_ref, "input_ref")
        _validate_ref(self.output_ref, "output_ref")
        self.__dict__['error'] = _error_to_dict(self.error)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["error"] = _error_to_dict(self.error)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceEvent":
        if not isinstance(data, Mapping):
            raise ValidationError("TraceEvent must be a JSON object")
        required = {"event_id", "run_id", "thread_id", "seq", "event_type", "actor", "status"}
        missing = sorted(required - data.keys())
        if missing:
            raise ValidationError(f"TraceEvent is missing required fields: {', '.join(missing)}")
        return cls(
            event_id=data["event_id"],
            run_id=data["run_id"],
            thread_id=data["thread_id"],
            seq=data["seq"],
            event_type=data["event_type"],
            actor=data["actor"],
            status=data["status"],
            redaction=dict(data.get("redaction", {"policy_version": DEFAULT_REDACTION_POLICY_VERSION})),
            schema_version=data.get("schema_version", 1),
            parent_event_id=data.get("parent_event_id"),
            node_name=data.get("node_name"),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            duration_ms=data.get("duration_ms"),
            input_ref=data.get("input_ref"),
            output_ref=data.get("output_ref"),
            attributes=dict(data.get("attributes", {})),
            error=data.get("error"),
        )


class EventEmitter:
    def __init__(self, run_id: str, thread_id: str, sink: Any, policy: RedactionPolicy | None = None) -> None:
        if not run_id or not thread_id:
            raise ValueError("EventEmitter requires run_id and thread_id")
        self.run_id = run_id
        self.thread_id = thread_id
        self.sink = sink
        self.policy = policy or RedactionPolicy()
        self.seq = 0
        self._event_ids: set[str] = set()

    def emit(
        self,
        event_type: str,
        actor: str,
        status: str,
        redaction: Mapping[str, Any] | None = None,
        parent_event_id: str | None = None,
        node_name: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: int | None = None,
        input_ref: Mapping[str, Any] | None = None,
        output_ref: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
        error: Any = None,
    ) -> TraceEvent:
        result = self.policy.apply(attributes or {})
        metadata = {
            "policy_version": result.policy_version,
            "secrets_detected": result.secrets_detected,
            "raw_content_included": result.raw_content_included,
            "truncated_fields": result.truncated_fields,
        }
        if redaction:
            for key in ("policy_version", "secrets_detected", "raw_content_included", "truncated_fields"):
                if key in redaction:
                    metadata[key] = redaction[key]
        self.seq += 1
        event_id = str(uuid4())
        while event_id in self._event_ids:
            event_id = str(uuid4())
        self._event_ids.add(event_id)
        safe_error = error
        if error is not None:
            error_data = _error_to_dict(error) or {}
            safe_error = ErrorEnvelope(
                code=str(error_data.get("code", "TRACE_ERROR")),
                category=str(error_data.get("category", "runtime")),
                message=f"message_sha256:{safe_ref(str(error_data.get('message', 'trace error')))['sha256']}",
                retryable=bool(error_data.get("retryable", False)),
                affected_refs=[
                    safe_ref(ref)["sha256"]
                    for ref in error_data.get("affected_refs", [])
                    if isinstance(ref, str)
                ],
                details=self.policy.apply(error_data.get("details", {})).redacted_data,
                safe_to_expose=bool(error_data.get("safe_to_expose", True)),
            )
        event = TraceEvent(
            event_id=event_id,
            run_id=self.run_id,
            thread_id=self.thread_id,
            seq=self.seq,
            event_type=event_type,
            actor=actor,
            status=status,
            redaction=metadata,
            parent_event_id=parent_event_id,
            node_name=node_name,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            input_ref=self.policy.redact_ref(input_ref),
            output_ref=self.policy.redact_ref(output_ref),
            attributes=result.redacted_data,
            error=safe_error,
        )
        self.sink.append(event)
        return event

    def load_existing_events(self, events: Iterable[TraceEvent]) -> None:
        """Load existing events to restore sequence and prevent duplicate event IDs."""
        last_seq = 0
        for event in events:
            if event.run_id != self.run_id or event.thread_id != self.thread_id:
                raise ValidationError("Existing event belongs to a different run or thread")
            if event.seq != last_seq + 1:
                raise ValidationError(f"Discontinuous event log: expected seq {last_seq + 1}, got {event.seq}")
            last_seq = event.seq
            self._event_ids.add(event.event_id)
        self.seq = last_seq
