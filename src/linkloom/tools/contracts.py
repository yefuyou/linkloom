"""JSON-safe data contracts for runtime-managed tool calls.

This module intentionally contains data models only. It does not resolve,
authorize, or execute tools; those responsibilities belong to a later
ToolRuntime phase.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from linkloom.runtime.errors import VALID_ERROR_CATEGORIES, ValidationError
from linkloom.runtime.models import _assert_json_safe_primitive, _require_keys


TOOL_RESULT_STATUSES = {"ok", "error"}
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string.")
    return value


def _require_id(value: Any, field_name: str) -> str:
    value = _require_text(value, field_name)
    if _ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a stable identifier.")
    return value


def _validate_optional_id(value: Any, field_name: str) -> None:
    if value is not None:
        _require_id(value, field_name)


def _validate_json_object(value: Any, field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must be a JSON object.")
    _assert_json_safe_primitive(value, field_name)


def _validate_string_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list):
        raise ValidationError(f"{field_name} must be a list of strings.")
    for index, item in enumerate(value):
        _require_text(item, f"{field_name}[{index}]")


@dataclass(frozen=True)
class ToolDefinition:
    """Static metadata for one callable tool.

    The definition contains no executor or permission state. Those are
    intentionally kept outside the serializable contract model.
    """

    tool_id: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolDefinition")
        _require_id(self.tool_id, "tool_id")
        _require_text(self.version, "version")
        _require_text(self.description, "description")
        _validate_json_object(self.input_schema, "input_schema")
        _validate_json_object(self.output_schema, "output_schema")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolDefinition":
        if not isinstance(data, dict):
            raise ValidationError("ToolDefinition must be a JSON object.")
        _require_keys(
            data,
            {"tool_id", "version", "description", "input_schema", "output_schema"},
            "ToolDefinition",
        )
        return cls(
            tool_id=data["tool_id"],
            version=data["version"],
            description=data["description"],
            input_schema=data["input_schema"],
            output_schema=data["output_schema"],
        )


@dataclass(frozen=True)
class ToolCall:
    """One requested tool invocation without execution semantics."""

    call_id: str
    tool_id: str
    arguments: dict[str, Any]
    run_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolCall")
        _require_id(self.call_id, "call_id")
        _require_id(self.tool_id, "tool_id")
        _validate_json_object(self.arguments, "arguments")
        _validate_optional_id(self.run_id, "run_id")
        _validate_optional_id(self.task_id, "task_id")
        _validate_optional_id(self.agent_id, "agent_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValidationError("sequence must be a non-negative integer.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        if not isinstance(data, dict):
            raise ValidationError("ToolCall must be a JSON object.")
        _require_keys(data, {"call_id", "tool_id", "arguments"}, "ToolCall")
        return cls(
            call_id=data["call_id"],
            tool_id=data["tool_id"],
            arguments=data["arguments"],
            run_id=data.get("run_id"),
            task_id=data.get("task_id"),
            agent_id=data.get("agent_id"),
            sequence=data.get("sequence", 0),
        )


@dataclass(frozen=True)
class ToolError:
    """Safe, serializable error details for a failed tool operation."""

    code: str
    category: str
    message: str
    retryable: bool = False
    affected_refs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    safe_to_expose: bool = True

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolError")
        _require_id(self.code, "code")
        _require_text(self.message, "message")
        if self.category not in VALID_ERROR_CATEGORIES:
            raise ValidationError(
                f"ToolError category must be one of {sorted(VALID_ERROR_CATEGORIES)}."
            )
        if not isinstance(self.retryable, bool):
            raise ValidationError("ToolError retryable must be boolean.")
        _validate_string_list(self.affected_refs, "affected_refs")
        _validate_json_object(self.details, "details")
        if not isinstance(self.safe_to_expose, bool):
            raise ValidationError("ToolError safe_to_expose must be boolean.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolError":
        if not isinstance(data, dict):
            raise ValidationError("ToolError must be a JSON object.")
        _require_keys(data, {"code", "category", "message"}, "ToolError")
        return cls(
            code=data["code"],
            category=data["category"],
            message=data["message"],
            retryable=data.get("retryable", False),
            affected_refs=data.get("affected_refs", []),
            details=data.get("details", {}),
            safe_to_expose=data.get("safe_to_expose", True),
        )


@dataclass(frozen=True)
class ToolResult:
    """Normalized result envelope for one ToolCall."""

    call_id: str
    tool_id: str
    status: str
    value: Any = None
    error: ToolError | None = None
    business_status: str | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ToolResult")
        _require_id(self.call_id, "call_id")
        _require_id(self.tool_id, "tool_id")
        if self.status not in TOOL_RESULT_STATUSES:
            raise ValidationError(
                f"ToolResult status must be one of {sorted(TOOL_RESULT_STATUSES)}."
            )
        if self.error is not None and not isinstance(self.error, ToolError):
            raise ValidationError("ToolResult error must be ToolError or None.")
        if self.status == "ok" and self.error is not None:
            raise ValidationError("Successful ToolResult cannot contain an error.")
        if self.status == "error" and self.error is None:
            raise ValidationError("Error ToolResult must contain an error.")
        if self.business_status is not None:
            _require_text(self.business_status, "business_status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResult":
        if not isinstance(data, dict):
            raise ValidationError("ToolResult must be a JSON object.")
        _require_keys(data, {"call_id", "tool_id", "status"}, "ToolResult")
        raw_error = data.get("error")
        error = None if raw_error is None else ToolError.from_dict(raw_error)
        return cls(
            call_id=data["call_id"],
            tool_id=data["tool_id"],
            status=data["status"],
            value=data.get("value"),
            error=error,
            business_status=data.get("business_status"),
        )


__all__ = ["ToolDefinition", "ToolCall", "ToolResult", "ToolError"]
