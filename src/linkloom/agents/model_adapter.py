"""Minimal local model seam for the first LinkLoom model-loop slice.

The adapter is intentionally provider-neutral.  Phase B only supplies a
deterministic fake implementation; no network client or SDK schema belongs in
this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any, Callable, Protocol, Sequence

from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    _assert_json_safe_primitive,
    _assert_no_forbidden_persisted_keys,
    _require_keys,
)
from linkloom.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolResult,
    _require_id,
    _require_text,
)


MODEL_ACTION_KINDS = frozenset({"tool_call", "final"})


MODEL_PROVIDER_ERROR_SPECS = {
    "MODEL_AUTH_REQUIRED": ("authentication", False),
    "MODEL_INVALID_REQUEST": ("invalid_request", False),
    "MODEL_RATE_LIMITED": ("rate_limit", True),
    "MODEL_TIMEOUT": ("timeout", False),
    "MODEL_TRANSIENT_FAILURE": ("transient", True),
    "MODEL_UNAVAILABLE": ("unavailable_model", False),
    "MODEL_RESPONSE_MALFORMED": ("malformed_response", False),
    "MODEL_TOOL_CALL_PARSE_FAILED": ("tool_call_parse", False),
    "MODEL_RESPONSE_UNSUPPORTED": ("unsupported_shape", False),
    "MODEL_TOOL_SCHEMA_UNSUPPORTED": ("tool_schema", False),
    "MODEL_CANCELLED": ("cancelled", False),
}
MODEL_PROVIDER_ERROR_OUTCOMES = frozenset({"known_failure", "unknown_provider_outcome"})

_UNSAFE_PROVIDER_TEXT_MARKERS = (
    "api_key",
    "api-token",
    "access_token",
    "authorization:",
    "bearer ",
    "chain_of_thought",
    "hidden_reasoning",
    "password=",
    "raw_traceback",
    "secret",
    "stack trace",
    "token=",
    "traceback",
)
_MAX_PROVIDER_TEXT_LENGTH = 512
_MAX_PROVIDER_METADATA_BYTES = 16 * 1024


def _validate_provider_text(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string.")
    if len(value) > _MAX_PROVIDER_TEXT_LENGTH:
        raise ValidationError(f"{field_name} exceeds the safe length limit.")
    if any(ord(character) < 32 for character in value):
        raise ValidationError(f"{field_name} must not contain control characters.")
    lowered = value.lower()
    if any(marker in lowered for marker in _UNSAFE_PROVIDER_TEXT_MARKERS):
        raise ValidationError(f"{field_name} contains unsafe provider text.")


def _validate_optional_provider_text(value: Any, field_name: str) -> None:
    if value is not None:
        _validate_provider_text(value, field_name)


def _validate_provider_metadata(value: Any, field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValidationError(f"{field_name} must be a JSON object.")
    _assert_json_safe_primitive(value, field_name)
    _assert_no_forbidden_persisted_keys(value, field_name)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_PROVIDER_METADATA_BYTES:
        raise ValidationError(f"{field_name} exceeds the safe size limit.")


def _validate_optional_non_negative_integer(value: Any, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{field_name} must be a non-negative integer or null.")


def _reject_unsupported_fields(
    data: dict[str, Any],
    allowed: set[str],
    model_name: str,
) -> None:
    unsupported = sorted(set(data) - allowed)
    if unsupported:
        raise ValidationError(
            f"{model_name} contains unsupported fields.",
            details={"fields": unsupported},
        )


@dataclass(frozen=True)
class ModelGenerationOptions:
    """Small provider-neutral generation option set."""

    max_output_tokens: int | None = None
    temperature: float | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ModelGenerationOptions")
        if self.max_output_tokens is not None and (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or self.max_output_tokens < 1
        ):
            raise ValidationError(
                "ModelGenerationOptions.max_output_tokens must be a positive integer or null."
            )
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or self.temperature < 0
        ):
            raise ValidationError(
                "ModelGenerationOptions.temperature must be a finite non-negative number or null."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelGenerationOptions":
        if not isinstance(data, dict):
            raise ValidationError("ModelGenerationOptions must be a JSON object.")
        unsupported = sorted(set(data) - {"max_output_tokens", "temperature"})
        if unsupported:
            raise ValidationError(
                "ModelGenerationOptions contains unsupported fields.",
                details={"fields": unsupported},
            )
        return cls(
            max_output_tokens=data.get("max_output_tokens"),
            temperature=data.get("temperature"),
        )


@dataclass(frozen=True)
class ModelUsage:
    """Normalized provider usage; unknown counts remain null."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: float | None = None

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ModelUsage")
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("total_tokens", self.total_tokens),
        ):
            _validate_optional_non_negative_integer(value, f"ModelUsage.{field_name}")
        if self.duration_ms is not None and (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, (int, float))
            or not math.isfinite(self.duration_ms)
            or self.duration_ms < 0
        ):
            raise ValidationError(
                "ModelUsage.duration_ms must be a finite non-negative number or null."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelUsage":
        if not isinstance(data, dict):
            raise ValidationError("ModelUsage must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {"input_tokens", "output_tokens", "total_tokens", "duration_ms"},
            "ModelUsage",
        )
        return cls(
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            total_tokens=data.get("total_tokens"),
            duration_ms=data.get("duration_ms"),
        )


@dataclass(frozen=True)
class ProviderCapability:
    """Provider-neutral declaration of capabilities the runtime may rely on."""

    supports_tool_calls: bool = True
    supports_final_answers: bool = True
    supports_multiple_tool_calls: bool = False
    supports_streaming: bool = False
    provider_executes_tools: bool = False

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ProviderCapability")
        if not all(
            isinstance(value, bool)
            for value in (
                self.supports_tool_calls,
                self.supports_final_answers,
                self.supports_multiple_tool_calls,
                self.supports_streaming,
                self.provider_executes_tools,
            )
        ):
            raise ValidationError("ProviderCapability fields must be boolean.")
        if self.provider_executes_tools:
            raise ValidationError(
                "ProviderCapability cannot grant provider-side tool execution."
            )

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderCapability":
        if not isinstance(data, dict):
            raise ValidationError("ProviderCapability must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {
                "supports_tool_calls",
                "supports_final_answers",
                "supports_multiple_tool_calls",
                "supports_streaming",
                "provider_executes_tools",
            },
            "ProviderCapability",
        )
        return cls(
            supports_tool_calls=data.get("supports_tool_calls", True),
            supports_final_answers=data.get("supports_final_answers", True),
            supports_multiple_tool_calls=data.get("supports_multiple_tool_calls", False),
            supports_streaming=data.get("supports_streaming", False),
            provider_executes_tools=data.get("provider_executes_tools", False),
        )


@dataclass(frozen=True)
class ModelProviderError:
    """Safe, provider-neutral failure data returned to the runtime."""

    code: str
    category: str
    message: str
    retryable: bool | None = None
    outcome: str = "known_failure"
    provider_request_id: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _assert_json_safe_primitive(asdict(self), "ModelProviderError")
        if self.code not in MODEL_PROVIDER_ERROR_SPECS:
            raise ValidationError(
                "ModelProviderError.code is not a supported normalized code.",
                details={"code": self.code},
            )
        expected_category, default_retryable = MODEL_PROVIDER_ERROR_SPECS[self.code]
        if self.category != expected_category:
            raise ValidationError(
                "ModelProviderError.code and category do not match.",
                details={"code": self.code, "expected_category": expected_category},
            )
        _validate_provider_text(self.message, "ModelProviderError.message")
        if self.retryable is None:
            object.__setattr__(self, "retryable", default_retryable)
        elif not isinstance(self.retryable, bool):
            raise ValidationError("ModelProviderError.retryable must be boolean.")
        if self.outcome not in MODEL_PROVIDER_ERROR_OUTCOMES:
            raise ValidationError(
                "ModelProviderError.outcome must be a supported outcome classification."
            )
        _validate_optional_provider_text(
            self.provider_request_id,
            "ModelProviderError.provider_request_id",
        )
        _validate_provider_metadata(self.provider_metadata, "ModelProviderError.provider_metadata")
        _validate_provider_metadata(self.details, "ModelProviderError.details")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelProviderError":
        if not isinstance(data, dict):
            raise ValidationError("ModelProviderError must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {
                "code",
                "category",
                "message",
                "retryable",
                "outcome",
                "provider_request_id",
                "provider_metadata",
                "details",
            },
            "ModelProviderError",
        )
        _require_keys(data, {"code", "category", "message"}, "ModelProviderError")
        return cls(
            code=data["code"],
            category=data["category"],
            message=data["message"],
            retryable=data.get("retryable"),
            outcome=data.get("outcome", "known_failure"),
            provider_request_id=data.get("provider_request_id"),
            provider_metadata=data.get("provider_metadata", {}),
            details=data.get("details", {}),
        )


@dataclass(frozen=True)
class ModelResponse:
    """Complete provider response envelope around one normalized ModelAction."""

    action: ModelAction | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    finish_reason: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    error: ModelProviderError | None = None

    def __post_init__(self) -> None:
        if (self.action is None) == (self.error is None):
            raise ValidationError(
                "ModelResponse must contain exactly one action or provider error."
            )
        if self.action is not None and not isinstance(self.action, ModelAction):
            raise ValidationError("ModelResponse.action must be ModelAction or None.")
        if self.error is not None and not isinstance(self.error, ModelProviderError):
            raise ValidationError(
                "ModelResponse.error must be ModelProviderError or None."
            )
        if not isinstance(self.usage, ModelUsage):
            raise ValidationError("ModelResponse.usage must be ModelUsage.")
        _validate_optional_provider_text(
            self.provider_request_id,
            "ModelResponse.provider_request_id",
        )
        _validate_optional_provider_text(
            self.provider_response_id,
            "ModelResponse.provider_response_id",
        )
        _validate_optional_provider_text(self.finish_reason, "ModelResponse.finish_reason")
        _validate_provider_metadata(self.provider_metadata, "ModelResponse.provider_metadata")
        payload = self.to_dict()
        _assert_json_safe_primitive(payload, "ModelResponse")
        _assert_no_forbidden_persisted_keys(payload, "ModelResponse")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.to_dict() if self.action is not None else None,
            "usage": self.usage.to_dict(),
            "provider_request_id": self.provider_request_id,
            "provider_response_id": self.provider_response_id,
            "finish_reason": self.finish_reason,
            "provider_metadata": self.provider_metadata,
            "error": self.error.to_dict() if self.error is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelResponse":
        if not isinstance(data, dict):
            raise ValidationError("ModelResponse must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {
                "action",
                "usage",
                "provider_request_id",
                "provider_response_id",
                "finish_reason",
                "provider_metadata",
                "error",
            },
            "ModelResponse",
        )
        raw_action = data.get("action")
        raw_error = data.get("error")
        action = ModelAction.from_dict(raw_action) if raw_action is not None else None
        error = ModelProviderError.from_dict(raw_error) if raw_error is not None else None
        return cls(
            action=action,
            usage=ModelUsage.from_dict(data.get("usage", {})),
            provider_request_id=data.get("provider_request_id"),
            provider_response_id=data.get("provider_response_id"),
            finish_reason=data.get("finish_reason"),
            provider_metadata=data.get("provider_metadata", {}),
            error=error,
        )


@dataclass(frozen=True)
class ModelTurnRequest:
    """The bounded input exposed to one model decision."""

    run_id: str
    turn_id: str
    task_id: str
    agent_id: str
    sequence: int
    user_input: str
    observation: ToolResult | None
    available_tools: list[ToolDefinition]
    previous_tool_call: ToolCall | None = None
    model_id: str | None = None
    generation_options: ModelGenerationOptions = field(default_factory=ModelGenerationOptions)

    def __post_init__(self) -> None:
        for field_name, value in (
            ("run_id", self.run_id),
            ("turn_id", self.turn_id),
            ("task_id", self.task_id),
            ("agent_id", self.agent_id),
        ):
            _require_id(value, f"ModelTurnRequest.{field_name}")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValidationError("ModelTurnRequest.sequence must be a positive integer.")
        _require_text(self.user_input, "ModelTurnRequest.user_input")
        if self.observation is not None and not isinstance(self.observation, ToolResult):
            raise ValidationError("ModelTurnRequest.observation must be ToolResult or None.")
        if self.previous_tool_call is not None and not isinstance(self.previous_tool_call, ToolCall):
            raise ValidationError(
                "ModelTurnRequest.previous_tool_call must be ToolCall or None."
            )
        if self.previous_tool_call is not None and self.observation is None:
            raise ValidationError(
                "ModelTurnRequest.previous_tool_call requires an observation."
            )
        if self.observation is not None and self.previous_tool_call is not None and (
            self.observation.call_id != self.previous_tool_call.call_id
            or self.observation.tool_id != self.previous_tool_call.tool_id
        ):
            raise ValidationError(
                "ModelTurnRequest.previous_tool_call identity must match observation."
            )
        if not isinstance(self.available_tools, list):
            raise ValidationError("ModelTurnRequest.available_tools must be a list.")
        if any(not isinstance(tool, ToolDefinition) for tool in self.available_tools):
            raise ValidationError("ModelTurnRequest.available_tools must contain ToolDefinition values.")
        if self.model_id is not None:
            _validate_provider_text(self.model_id, "ModelTurnRequest.model_id")
        if isinstance(self.generation_options, dict):
            object.__setattr__(
                self,
                "generation_options",
                ModelGenerationOptions.from_dict(self.generation_options),
            )
        if not isinstance(self.generation_options, ModelGenerationOptions):
            raise ValidationError(
                "ModelTurnRequest.generation_options must be ModelGenerationOptions."
            )
        _assert_json_safe_primitive(self.to_dict(), "ModelTurnRequest")

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "sequence": self.sequence,
            "user_input": self.user_input,
            "observation": self.observation.to_dict() if self.observation else None,
            "available_tools": [tool.to_dict() for tool in self.available_tools],
            "previous_tool_call": (
                self.previous_tool_call.to_dict() if self.previous_tool_call else None
            ),
            "model_id": self.model_id,
            "generation_options": self.generation_options.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelTurnRequest":
        if not isinstance(data, dict):
            raise ValidationError("ModelTurnRequest must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {
                "run_id",
                "turn_id",
                "task_id",
                "agent_id",
                "sequence",
                "user_input",
                "observation",
                "available_tools",
                "previous_tool_call",
                "model_id",
                "generation_options",
            },
            "ModelTurnRequest",
        )
        _require_keys(
            data,
            {
                "run_id",
                "turn_id",
                "task_id",
                "agent_id",
                "sequence",
                "user_input",
                "available_tools",
            },
            "ModelTurnRequest",
        )
        raw_observation = data.get("observation")
        raw_previous_call = data.get("previous_tool_call")
        raw_tools = data["available_tools"]
        if not isinstance(raw_tools, list):
            raise ValidationError("ModelTurnRequest.available_tools must be a list.")
        return cls(
            run_id=data["run_id"],
            turn_id=data["turn_id"],
            task_id=data["task_id"],
            agent_id=data["agent_id"],
            sequence=data["sequence"],
            user_input=data["user_input"],
            observation=(
                ToolResult.from_dict(raw_observation)
                if raw_observation is not None
                else None
            ),
            available_tools=[ToolDefinition.from_dict(item) for item in raw_tools],
            previous_tool_call=(
                ToolCall.from_dict(raw_previous_call)
                if raw_previous_call is not None
                else None
            ),
            model_id=data.get("model_id"),
            generation_options=ModelGenerationOptions.from_dict(
                data.get("generation_options", {})
            ),
        )


@dataclass(frozen=True)
class ModelAction:
    """A model proposal: one structured tool call or a terminal answer."""

    kind: str
    tool_call: ToolCall | None = None
    final_answer: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in MODEL_ACTION_KINDS:
            raise ValidationError(
                f"ModelAction.kind must be one of {sorted(MODEL_ACTION_KINDS)}."
            )
        if self.kind == "tool_call":
            if not isinstance(self.tool_call, ToolCall) or self.final_answer is not None:
                raise ValidationError("A tool_call ModelAction requires only a ToolCall.")
        elif not isinstance(self.final_answer, str) or not self.final_answer.strip() or self.tool_call is not None:
            raise ValidationError("A final ModelAction requires only a non-empty final_answer.")
        _assert_json_safe_primitive(self.to_dict(), "ModelAction")

    @classmethod
    def tool(cls, call: ToolCall) -> "ModelAction":
        return cls(kind="tool_call", tool_call=call)

    @classmethod
    def final(cls, answer: str) -> "ModelAction":
        return cls(kind="final", final_answer=answer)

    @classmethod
    def from_dict(cls, data: dict) -> "ModelAction":
        if not isinstance(data, dict):
            raise ValidationError("ModelAction must be a JSON object.")
        kind = data.get("kind")
        if kind == "tool_call":
            raw_call = data.get("tool_call")
            if not isinstance(raw_call, dict):
                raise ValidationError("Tool-call ModelAction requires a ToolCall object.")
            return cls.tool(ToolCall.from_dict(raw_call))
        if kind == "final":
            return cls.final(data.get("final_answer"))
        raise ValidationError("ModelAction.kind is not supported.")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "tool_call": self.tool_call.to_dict() if self.tool_call else None,
            "final_answer": self.final_answer,
        }


class ModelAdapter(Protocol):
    """Provider-neutral interface for one model decision."""

    def decide(self, request: ModelTurnRequest) -> ModelAction:
        ...


class ModelProviderAdapter(Protocol):
    """Normalized provider seam; the runtime still owns the model loop."""

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
        ...


class FakeModelAdapter:
    """Deterministic scripted adapter used only for local loop tests/demos."""

    def __init__(
        self,
        script: Sequence[ModelAction | Callable[[ModelTurnRequest], ModelAction]],
    ) -> None:
        if not isinstance(script, (list, tuple)) or not script:
            raise ValidationError("FakeModelAdapter script must be a non-empty sequence.")
        self._script = tuple(script)
        self._index = 0
        self.requests: list[ModelTurnRequest] = []

    @property
    def call_count(self) -> int:
        return self._index

    def decide(self, request: ModelTurnRequest) -> ModelAction:
        if not isinstance(request, ModelTurnRequest):
            raise ValidationError("ModelAdapter request must be a ModelTurnRequest.")
        if self._index >= len(self._script):
            raise ValidationError(
                "FakeModelAdapter script is exhausted.",
                details={"reason": "script_exhausted", "turn_id": request.turn_id},
            )
        step = self._script[self._index]
        self._index += 1
        self.requests.append(request)
        action = step(request) if callable(step) else step
        if not isinstance(action, ModelAction):
            raise ValidationError("FakeModelAdapter script must return ModelAction values.")
        return action


ScriptedModelAdapter = FakeModelAdapter


__all__ = [
    "FakeModelAdapter",
    "MODEL_ACTION_KINDS",
    "MODEL_PROVIDER_ERROR_OUTCOMES",
    "MODEL_PROVIDER_ERROR_SPECS",
    "ModelAction",
    "ModelAdapter",
    "ModelGenerationOptions",
    "ModelProviderAdapter",
    "ModelProviderError",
    "ModelResponse",
    "ModelTurnRequest",
    "ModelUsage",
    "ProviderCapability",
    "ScriptedModelAdapter",
]
