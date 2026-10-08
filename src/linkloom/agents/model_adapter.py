"""Minimal local model seam for the first LinkLoom model-loop slice.

The adapter is intentionally provider-neutral.  Phase B only supplies a
deterministic fake implementation; no network client or SDK schema belongs in
this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
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
MODEL_PROPOSAL_KINDS = frozenset({"tool_calls", "final"})
MAX_MODEL_TOOL_CALLS_PER_PROPOSAL = 16
MODEL_VISIBLE_EVIDENCE_TOOL_IDS = frozenset({"search_notes", "read_verified_note"})
MAX_VERIFIED_EVIDENCE_CONTEXT_RESULTS = 8
MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES = 24 * 1024
MAX_MODEL_TOOL_HISTORY_RESULTS = 16
MAX_MODEL_TOOL_HISTORY_BYTES = 128 * 1024


MODEL_PROVIDER_ERROR_SPECS = {
    "MODEL_AUTH_REQUIRED": ("authentication", False),
    "MODEL_BILLING_BLOCKED": ("billing_blocked", False),
    "MODEL_INVALID_REQUEST": ("invalid_request", False),
    "MODEL_RATE_LIMITED": ("rate_limit", True),
    "MODEL_TIMEOUT": ("timeout", False),
    "MODEL_TRANSIENT_FAILURE": ("transient", True),
    "MODEL_UNKNOWN_FAILURE": ("unknown_provider_error", False),
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
MAX_PROVIDER_CONTINUATION_BYTES = 64 * 1024


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


def _tool_arguments_sha256(arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity_digest(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}{hashlib.sha256(encoded).hexdigest()[:32]}"


def runtime_call_id_for(
    *,
    run_id: str,
    proposal_id: str,
    ordinal: int,
    provider_call_id: str,
) -> str:
    """Derive one stable run-wide execution ID from Provider turn identity."""
    _require_id(run_id, "run_id")
    _require_id(proposal_id, "proposal_id")
    _require_id(provider_call_id, "provider_call_id")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValidationError("ordinal must be a non-negative integer.")
    return _identity_digest(
        "rtc_",
        {
            "schema_version": 2,
            "run_id": run_id,
            "proposal_id": proposal_id,
            "ordinal": ordinal,
            "provider_call_id": provider_call_id,
        },
    )


def tool_result_id_for(
    *,
    run_id: str,
    proposal_id: str,
    ordinal: int,
    runtime_call_id: str,
) -> str:
    """Derive the stable identity of one model-visible tool result."""
    _require_id(run_id, "run_id")
    _require_id(proposal_id, "proposal_id")
    _require_id(runtime_call_id, "runtime_call_id")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValidationError("ordinal must be a non-negative integer.")
    return _identity_digest(
        "tr_",
        {
            "schema_version": 2,
            "run_id": run_id,
            "proposal_id": proposal_id,
            "ordinal": ordinal,
            "runtime_call_id": runtime_call_id,
        },
    )


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


def _is_verified_evidence_value(value: Any) -> bool:
    """Recognize the source-grounded evidence shape safe for a model context."""
    if not isinstance(value, dict) or value.get("status") != "verified":
        return False
    for field_name in (
        "evidence_id",
        "relative_path",
        "content_sha256",
        "quote",
        "quote_sha256",
    ):
        if not isinstance(value.get(field_name), str) or not value[field_name].strip():
            return False
    line_start = value.get("line_start")
    line_end = value.get("line_end")
    return (
        not isinstance(line_start, bool)
        and not isinstance(line_end, bool)
        and isinstance(line_start, int)
        and isinstance(line_end, int)
        and line_start >= 1
        and line_end >= line_start
    )


def is_verified_evidence_result(result: ToolResult) -> bool:
    """Return whether a successful tool result is safe to expose as evidence."""
    if (
        not isinstance(result, ToolResult)
        or result.status != "ok"
        or result.tool_id not in MODEL_VISIBLE_EVIDENCE_TOOL_IDS
        or result.business_status not in {None, "FOUND"}
    ):
        return False
    values = result.value if isinstance(result.value, list) else [result.value]
    return bool(values) and all(_is_verified_evidence_value(value) for value in values)


def _validate_verified_evidence_context(context: Any) -> None:
    """Keep model-visible history bounded to successful, cited source evidence."""
    if not isinstance(context, list):
        raise ValidationError("ModelTurnRequest.evidence_context must be a list.")
    if len(context) > MAX_VERIFIED_EVIDENCE_CONTEXT_RESULTS:
        raise ValidationError(
            "ModelTurnRequest.evidence_context exceeds the bounded result limit."
        )
    for result in context:
        if not isinstance(result, ToolResult):
            raise ValidationError(
                "ModelTurnRequest.evidence_context must contain ToolResult values."
            )
        if not is_verified_evidence_result(result):
            raise ValidationError(
                "ModelTurnRequest.evidence_context must contain only verified evidence values."
            )
    encoded = json.dumps(
        [result.to_dict() for result in context],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES:
        raise ValidationError(
            "ModelTurnRequest.evidence_context exceeds the bounded byte limit."
        )


@dataclass(frozen=True)
class ModelGenerationOptions:
    """Small provider-neutral generation option set."""

    max_output_tokens: int | None = None
    temperature: float | None = None
    require_tool_call: bool = False
    thinking_level: str | None = None

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
        if not isinstance(self.require_tool_call, bool):
            raise ValidationError("ModelGenerationOptions.require_tool_call must be boolean.")
        if self.thinking_level is not None and self.thinking_level not in {
            "low",
            "medium",
            "high",
        }:
            raise ValidationError(
                "ModelGenerationOptions.thinking_level must be low, medium, high, or null."
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.require_tool_call:
            payload.pop("require_tool_call")
        if self.thinking_level is None:
            payload.pop("thinking_level")
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelGenerationOptions":
        if not isinstance(data, dict):
            raise ValidationError("ModelGenerationOptions must be a JSON object.")
        unsupported = sorted(
            set(data)
            - {
                "max_output_tokens",
                "temperature",
                "require_tool_call",
                "thinking_level",
            }
        )
        if unsupported:
            raise ValidationError(
                "ModelGenerationOptions contains unsupported fields.",
                details={"fields": unsupported},
            )
        return cls(
            max_output_tokens=data.get("max_output_tokens"),
            temperature=data.get("temperature"),
            require_tool_call=data.get("require_tool_call", False),
            thinking_level=data.get("thinking_level"),
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
class ProviderContinuation:
    """Bounded opaque provider state associated with one model tool call."""

    provider_id: str
    source_turn_id: str
    source_sequence: int
    tool_call_id: str
    tool_id: str
    arguments_sha256: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_provider_text(
            self.provider_id,
            "ProviderContinuation.provider_id",
        )
        _require_id(self.source_turn_id, "ProviderContinuation.source_turn_id")
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 1
        ):
            raise ValidationError(
                "ProviderContinuation.source_sequence must be a positive integer."
            )
        _require_id(self.tool_call_id, "ProviderContinuation.tool_call_id")
        _require_id(self.tool_id, "ProviderContinuation.tool_id")
        if (
            not isinstance(self.arguments_sha256, str)
            or len(self.arguments_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.arguments_sha256)
        ):
            raise ValidationError(
                "ProviderContinuation.arguments_sha256 must be a lowercase SHA-256 digest."
            )
        if not isinstance(self.payload, dict):
            raise ValidationError("ProviderContinuation.payload must be a JSON object.")
        _assert_json_safe_primitive(self.payload, "ProviderContinuation.payload")
        _assert_no_forbidden_persisted_keys(
            self.payload,
            "ProviderContinuation.payload",
        )
        encoded = json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_PROVIDER_CONTINUATION_BYTES:
            raise ValidationError(
                "ProviderContinuation.payload exceeds the safe size limit."
            )

    @classmethod
    def for_tool_call(
        cls,
        *,
        provider_id: str,
        source_turn_id: str,
        call: ToolCall,
        payload: dict[str, Any],
    ) -> "ProviderContinuation":
        return cls(
            provider_id=provider_id,
            source_turn_id=source_turn_id,
            source_sequence=call.sequence,
            tool_call_id=call.call_id,
            tool_id=call.tool_id,
            arguments_sha256=_tool_arguments_sha256(call.arguments),
            payload=deepcopy(payload),
        )

    def matches_tool_call(self, call: ToolCall) -> bool:
        return (
            self.source_sequence == call.sequence
            and self.tool_call_id == call.call_id
            and self.tool_id == call.tool_id
            and self.arguments_sha256 == _tool_arguments_sha256(call.arguments)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "source_turn_id": self.source_turn_id,
            "source_sequence": self.source_sequence,
            "tool_call_id": self.tool_call_id,
            "tool_id": self.tool_id,
            "arguments_sha256": self.arguments_sha256,
            "payload": deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderContinuation":
        if not isinstance(data, dict):
            raise ValidationError("ProviderContinuation must be a JSON object.")
        fields = {
            "provider_id",
            "source_turn_id",
            "source_sequence",
            "tool_call_id",
            "tool_id",
            "arguments_sha256",
            "payload",
        }
        _reject_unsupported_fields(data, fields, "ProviderContinuation")
        _require_keys(data, fields, "ProviderContinuation")
        return cls(
            provider_id=data["provider_id"],
            source_turn_id=data["source_turn_id"],
            source_sequence=data["source_sequence"],
            tool_call_id=data["tool_call_id"],
            tool_id=data["tool_id"],
            arguments_sha256=data["arguments_sha256"],
            payload=deepcopy(data["payload"]),
        )


@dataclass(frozen=True)
class ModelToolCall:
    """One Provider-correlated call bound to a run-wide Runtime ToolCall."""

    provider_call_id: str
    runtime_call: ToolCall

    def __post_init__(self) -> None:
        _require_id(self.provider_call_id, "ModelToolCall.provider_call_id")
        if not isinstance(self.runtime_call, ToolCall):
            raise ValidationError("ModelToolCall.runtime_call must be ToolCall.")
        _assert_json_safe_primitive(self.to_dict(), "ModelToolCall")

    @classmethod
    def bind(
        cls,
        *,
        provider_call_id: str,
        proposal_id: str,
        ordinal: int,
        tool_id: str,
        arguments: dict[str, Any],
        run_id: str,
        task_id: str,
        agent_id: str,
        sequence: int,
    ) -> "ModelToolCall":
        return cls(
            provider_call_id=provider_call_id,
            runtime_call=ToolCall(
                call_id=runtime_call_id_for(
                    run_id=run_id,
                    proposal_id=proposal_id,
                    ordinal=ordinal,
                    provider_call_id=provider_call_id,
                ),
                tool_id=tool_id,
                arguments=deepcopy(arguments),
                run_id=run_id,
                task_id=task_id,
                agent_id=agent_id,
                sequence=sequence,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_call_id": self.provider_call_id,
            "runtime_call": self.runtime_call.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelToolCall":
        if not isinstance(data, dict):
            raise ValidationError("ModelToolCall must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {"provider_call_id", "runtime_call"},
            "ModelToolCall",
        )
        _require_keys(
            data,
            {"provider_call_id", "runtime_call"},
            "ModelToolCall",
        )
        return cls(
            provider_call_id=data["provider_call_id"],
            runtime_call=ToolCall.from_dict(data["runtime_call"]),
        )


@dataclass(frozen=True)
class ModelTurnProposal:
    """One durable model turn: either a final answer or ordered tool calls."""

    proposal_id: str
    kind: str
    final_answer: str | None = None
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    schema_version: int = 2

    def __post_init__(self) -> None:
        _require_id(self.proposal_id, "ModelTurnProposal.proposal_id")
        if self.schema_version != 2:
            raise ValidationError("ModelTurnProposal.schema_version must be 2.")
        if self.kind not in MODEL_PROPOSAL_KINDS:
            raise ValidationError(
                f"ModelTurnProposal.kind must be one of {sorted(MODEL_PROPOSAL_KINDS)}."
            )
        if not isinstance(self.tool_calls, list):
            raise ValidationError("ModelTurnProposal.tool_calls must be a list.")
        if self.kind == "final":
            if (
                not isinstance(self.final_answer, str)
                or not self.final_answer.strip()
                or self.tool_calls
            ):
                raise ValidationError(
                    "A final ModelTurnProposal requires only a non-empty final_answer."
                )
        elif (
            self.final_answer is not None
            or not 1 <= len(self.tool_calls) <= MAX_MODEL_TOOL_CALLS_PER_PROPOSAL
        ):
            raise ValidationError(
                "A tool_calls ModelTurnProposal requires 1..16 calls and no final answer."
            )

        provider_ids: set[str] = set()
        runtime_ids: set[str] = set()
        for call in self.tool_calls:
            if not isinstance(call, ModelToolCall):
                raise ValidationError(
                    "ModelTurnProposal.tool_calls must contain ModelToolCall values."
                )
            if call.provider_call_id in provider_ids:
                raise ValidationError(
                    "ModelTurnProposal Provider call IDs must be unique."
                )
            runtime_call_id = call.runtime_call.call_id
            if runtime_call_id in runtime_ids:
                raise ValidationError(
                    "ModelTurnProposal Runtime call IDs must be unique."
                )
            provider_ids.add(call.provider_call_id)
            runtime_ids.add(runtime_call_id)
        _assert_json_safe_primitive(self.to_dict(), "ModelTurnProposal")

    @classmethod
    def final(cls, proposal_id: str, answer: str) -> "ModelTurnProposal":
        return cls(proposal_id=proposal_id, kind="final", final_answer=answer)

    @classmethod
    def tools(
        cls,
        proposal_id: str,
        calls: Sequence[ModelToolCall],
    ) -> "ModelTurnProposal":
        return cls(
            proposal_id=proposal_id,
            kind="tool_calls",
            tool_calls=list(calls),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "final_answer": self.final_answer,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelTurnProposal":
        if not isinstance(data, dict):
            raise ValidationError("ModelTurnProposal must be a JSON object.")
        fields = {
            "schema_version",
            "proposal_id",
            "kind",
            "final_answer",
            "tool_calls",
        }
        _reject_unsupported_fields(data, fields, "ModelTurnProposal")
        _require_keys(data, fields, "ModelTurnProposal")
        raw_calls = data["tool_calls"]
        if not isinstance(raw_calls, list):
            raise ValidationError("ModelTurnProposal.tool_calls must be a list.")
        return cls(
            schema_version=data["schema_version"],
            proposal_id=data["proposal_id"],
            kind=data["kind"],
            final_answer=data["final_answer"],
            tool_calls=[ModelToolCall.from_dict(item) for item in raw_calls],
        )


@dataclass(frozen=True)
class ModelToolResult:
    """Stable model-bound identity around one normalized ToolResult."""

    result_id: str
    run_id: str
    proposal_id: str
    ordinal: int
    provider_call_id: str
    runtime_call_id: str
    result: ToolResult

    def __post_init__(self) -> None:
        for field_name, value in (
            ("result_id", self.result_id),
            ("run_id", self.run_id),
            ("proposal_id", self.proposal_id),
            ("provider_call_id", self.provider_call_id),
            ("runtime_call_id", self.runtime_call_id),
        ):
            _require_id(value, f"ModelToolResult.{field_name}")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValidationError("ModelToolResult.ordinal must be non-negative.")
        if not isinstance(self.result, ToolResult):
            raise ValidationError("ModelToolResult.result must be ToolResult.")
        if self.result.call_id != self.runtime_call_id:
            raise ValidationError(
                "ModelToolResult Runtime call identity must match its ToolResult."
            )
        expected_id = tool_result_id_for(
            run_id=self.run_id,
            proposal_id=self.proposal_id,
            ordinal=self.ordinal,
            runtime_call_id=self.runtime_call_id,
        )
        if self.result_id != expected_id:
            raise ValidationError("ModelToolResult.result_id is not canonical.")
        _assert_json_safe_primitive(self.to_dict(), "ModelToolResult")

    @classmethod
    def for_call(
        cls,
        *,
        run_id: str,
        proposal_id: str,
        ordinal: int,
        model_call: ModelToolCall,
        result: ToolResult,
    ) -> "ModelToolResult":
        return cls(
            result_id=tool_result_id_for(
                run_id=run_id,
                proposal_id=proposal_id,
                ordinal=ordinal,
                runtime_call_id=model_call.runtime_call.call_id,
            ),
            run_id=run_id,
            proposal_id=proposal_id,
            ordinal=ordinal,
            provider_call_id=model_call.provider_call_id,
            runtime_call_id=model_call.runtime_call.call_id,
            result=result,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "run_id": self.run_id,
            "proposal_id": self.proposal_id,
            "ordinal": self.ordinal,
            "provider_call_id": self.provider_call_id,
            "runtime_call_id": self.runtime_call_id,
            "result": self.result.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelToolResult":
        if not isinstance(data, dict):
            raise ValidationError("ModelToolResult must be a JSON object.")
        fields = {
            "result_id",
            "run_id",
            "proposal_id",
            "ordinal",
            "provider_call_id",
            "runtime_call_id",
            "result",
        }
        _reject_unsupported_fields(data, fields, "ModelToolResult")
        _require_keys(data, fields, "ModelToolResult")
        return cls(
            result_id=data["result_id"],
            run_id=data["run_id"],
            proposal_id=data["proposal_id"],
            ordinal=data["ordinal"],
            provider_call_id=data["provider_call_id"],
            runtime_call_id=data["runtime_call_id"],
            result=ToolResult.from_dict(data["result"]),
        )


@dataclass(frozen=True)
class ProviderTurnContinuation:
    """Bounded opaque Provider state for one complete model tool-call turn."""

    provider_id: str
    source_turn_id: str
    source_sequence: int
    proposal_id: str
    ordered_call_fingerprints: list[dict[str, str]]
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_provider_text(
            self.provider_id,
            "ProviderTurnContinuation.provider_id",
        )
        _require_id(
            self.source_turn_id,
            "ProviderTurnContinuation.source_turn_id",
        )
        _require_id(self.proposal_id, "ProviderTurnContinuation.proposal_id")
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 1
        ):
            raise ValidationError(
                "ProviderTurnContinuation.source_sequence must be positive."
            )
        if not isinstance(self.ordered_call_fingerprints, list):
            raise ValidationError(
                "ProviderTurnContinuation.ordered_call_fingerprints must be a list."
            )
        for fingerprint in self.ordered_call_fingerprints:
            if not isinstance(fingerprint, dict) or set(fingerprint) != {
                "provider_call_id",
                "runtime_call_id",
                "tool_id",
                "arguments_sha256",
            }:
                raise ValidationError(
                    "ProviderTurnContinuation call fingerprint is invalid."
                )
            for field_name in (
                "provider_call_id",
                "runtime_call_id",
                "tool_id",
            ):
                _require_id(
                    fingerprint[field_name],
                    f"ProviderTurnContinuation.{field_name}",
                )
            arguments_sha256 = fingerprint["arguments_sha256"]
            if (
                not isinstance(arguments_sha256, str)
                or len(arguments_sha256) != 64
                or any(character not in "0123456789abcdef" for character in arguments_sha256)
            ):
                raise ValidationError(
                    "ProviderTurnContinuation.arguments_sha256 must be a lowercase SHA-256 digest."
                )
        if not isinstance(self.payload, dict):
            raise ValidationError(
                "ProviderTurnContinuation.payload must be a JSON object."
            )
        _assert_json_safe_primitive(
            self.payload,
            "ProviderTurnContinuation.payload",
        )
        _assert_no_forbidden_persisted_keys(
            self.payload,
            "ProviderTurnContinuation.payload",
        )
        encoded = json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > MAX_PROVIDER_CONTINUATION_BYTES:
            raise ValidationError(
                "ProviderTurnContinuation.payload exceeds the safe size limit."
            )

    @staticmethod
    def _fingerprint(call: ModelToolCall) -> dict[str, str]:
        return {
            "provider_call_id": call.provider_call_id,
            "runtime_call_id": call.runtime_call.call_id,
            "tool_id": call.runtime_call.tool_id,
            "arguments_sha256": _tool_arguments_sha256(
                call.runtime_call.arguments
            ),
        }

    @classmethod
    def for_calls(
        cls,
        *,
        provider_id: str,
        source_turn_id: str,
        source_sequence: int,
        proposal_id: str,
        calls: Sequence[ModelToolCall],
        payload: dict[str, Any],
    ) -> "ProviderTurnContinuation":
        return cls(
            provider_id=provider_id,
            source_turn_id=source_turn_id,
            source_sequence=source_sequence,
            proposal_id=proposal_id,
            ordered_call_fingerprints=[cls._fingerprint(call) for call in calls],
            payload=deepcopy(payload),
        )

    def matches_calls(
        self,
        proposal_id: str,
        calls: Sequence[ModelToolCall],
    ) -> bool:
        return (
            self.proposal_id == proposal_id
            and self.ordered_call_fingerprints
            == [self._fingerprint(call) for call in calls]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "source_turn_id": self.source_turn_id,
            "source_sequence": self.source_sequence,
            "proposal_id": self.proposal_id,
            "ordered_call_fingerprints": deepcopy(
                self.ordered_call_fingerprints
            ),
            "payload": deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProviderTurnContinuation":
        if not isinstance(data, dict):
            raise ValidationError("ProviderTurnContinuation must be a JSON object.")
        fields = {
            "provider_id",
            "source_turn_id",
            "source_sequence",
            "proposal_id",
            "ordered_call_fingerprints",
            "payload",
        }
        _reject_unsupported_fields(data, fields, "ProviderTurnContinuation")
        _require_keys(data, fields, "ProviderTurnContinuation")
        return cls(
            provider_id=data["provider_id"],
            source_turn_id=data["source_turn_id"],
            source_sequence=data["source_sequence"],
            proposal_id=data["proposal_id"],
            ordered_call_fingerprints=deepcopy(
                data["ordered_call_fingerprints"]
            ),
            payload=deepcopy(data["payload"]),
        )


@dataclass(frozen=True)
class ModelToolTurn:
    """One complete ordered tool-call/result exchange from a model turn."""

    proposal_id: str
    source_turn_id: str
    source_sequence: int
    tool_calls: list[ModelToolCall]
    tool_results: list[ModelToolResult]
    provider_continuation: ProviderTurnContinuation | None = None

    def __post_init__(self) -> None:
        _require_id(self.proposal_id, "ModelToolTurn.proposal_id")
        _require_id(self.source_turn_id, "ModelToolTurn.source_turn_id")
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 1
        ):
            raise ValidationError("ModelToolTurn.source_sequence must be positive.")
        if (
            not isinstance(self.tool_calls, list)
            or not isinstance(self.tool_results, list)
            or not self.tool_calls
            or len(self.tool_calls) != len(self.tool_results)
        ):
            raise ValidationError(
                "ModelToolTurn calls and results must be non-empty equal-length lists."
            )
        for ordinal, (call, model_result) in enumerate(
            zip(self.tool_calls, self.tool_results, strict=True)
        ):
            if not isinstance(call, ModelToolCall) or not isinstance(
                model_result,
                ModelToolResult,
            ):
                raise ValidationError(
                    "ModelToolTurn must contain model call/result values."
                )
            runtime_call = call.runtime_call
            if (
                model_result.proposal_id != self.proposal_id
                or model_result.ordinal != ordinal
                or model_result.provider_call_id != call.provider_call_id
                or model_result.runtime_call_id != runtime_call.call_id
                or model_result.result.tool_id != runtime_call.tool_id
                or runtime_call.sequence != self.source_sequence
            ):
                raise ValidationError(
                    "ModelToolTurn call and result identities must match."
                )
        if self.provider_continuation is not None:
            if not isinstance(
                self.provider_continuation,
                ProviderTurnContinuation,
            ) or not self.provider_continuation.matches_calls(
                self.proposal_id,
                self.tool_calls,
            ):
                raise ValidationError(
                    "ModelToolTurn Provider continuation identity must match."
                )
        _assert_json_safe_primitive(self.to_dict(), "ModelToolTurn")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "proposal_id": self.proposal_id,
            "source_turn_id": self.source_turn_id,
            "source_sequence": self.source_sequence,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "tool_results": [result.to_dict() for result in self.tool_results],
        }
        if self.provider_continuation is not None:
            payload["provider_continuation"] = self.provider_continuation.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelToolTurn":
        if not isinstance(data, dict):
            raise ValidationError("ModelToolTurn must be a JSON object.")
        fields = {
            "proposal_id",
            "source_turn_id",
            "source_sequence",
            "tool_calls",
            "tool_results",
            "provider_continuation",
        }
        _reject_unsupported_fields(data, fields, "ModelToolTurn")
        _require_keys(
            data,
            {
                "proposal_id",
                "source_turn_id",
                "source_sequence",
                "tool_calls",
                "tool_results",
            },
            "ModelToolTurn",
        )
        raw_continuation = data.get("provider_continuation")
        return cls(
            proposal_id=data["proposal_id"],
            source_turn_id=data["source_turn_id"],
            source_sequence=data["source_sequence"],
            tool_calls=[ModelToolCall.from_dict(item) for item in data["tool_calls"]],
            tool_results=[
                ModelToolResult.from_dict(item) for item in data["tool_results"]
            ],
            provider_continuation=(
                ProviderTurnContinuation.from_dict(raw_continuation)
                if raw_continuation is not None
                else None
            ),
        )


def select_bounded_model_tool_turns(
    tool_turns: Sequence[ModelToolTurn],
) -> list[ModelToolTurn]:
    """Keep the newest chronological suffix without splitting a model turn."""
    if not isinstance(tool_turns, (list, tuple)):
        raise ValidationError("Model tool-turn history must be a sequence.")
    turns = list(tool_turns)
    if any(not isinstance(turn, ModelToolTurn) for turn in turns):
        raise ValidationError(
            "Model tool-turn history must contain ModelToolTurn values."
        )
    if any(
        later.source_sequence <= earlier.source_sequence
        for earlier, later in zip(turns, turns[1:])
    ):
        raise ValidationError("Model tool-turn history must be chronological.")

    selected: list[ModelToolTurn] = []
    result_count = 0
    for turn in reversed(turns):
        trial_count = result_count + len(turn.tool_results)
        trial = [turn, *selected]
        encoded = json.dumps(
            [item.to_dict() for item in trial],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if (
            trial_count > MAX_MODEL_TOOL_HISTORY_RESULTS
            or len(encoded) > MAX_MODEL_TOOL_HISTORY_BYTES
        ):
            if not selected:
                raise ValidationError(
                    "The newest tool turn exceeds the bounded model history limit."
                )
            break
        selected.insert(0, turn)
        result_count = trial_count
    return selected


@dataclass(frozen=True)
class ModelResponse:
    """Complete Provider response with a V2 proposal and V1 action view."""

    action: ModelAction | None = None
    proposal: ModelTurnProposal | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    finish_reason: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    provider_continuation: ProviderContinuation | None = None
    provider_turn_continuation: ProviderTurnContinuation | None = None
    error: ModelProviderError | None = None

    def __post_init__(self) -> None:
        success_sources = int(self.action is not None) + int(self.proposal is not None)
        if (success_sources == 0) == (self.error is None) or success_sources > 1:
            raise ValidationError(
                "ModelResponse must contain exactly one proposal/action or provider error."
            )
        if self.action is not None and not isinstance(self.action, ModelAction):
            raise ValidationError("ModelResponse.action must be ModelAction or None.")
        if self.proposal is not None and not isinstance(
            self.proposal,
            ModelTurnProposal,
        ):
            raise ValidationError(
                "ModelResponse.proposal must be ModelTurnProposal or None."
            )
        if self.error is not None and not isinstance(self.error, ModelProviderError):
            raise ValidationError(
                "ModelResponse.error must be ModelProviderError or None."
            )
        if not isinstance(self.usage, ModelUsage):
            raise ValidationError("ModelResponse.usage must be ModelUsage.")
        if self.provider_continuation is not None and not isinstance(
            self.provider_continuation,
            ProviderContinuation,
        ):
            raise ValidationError(
                "ModelResponse.provider_continuation must be ProviderContinuation or None."
            )
        if self.provider_continuation is not None and (
            self.action is None
            or self.action.kind != "tool_call"
            or self.action.tool_call is None
            or not self.provider_continuation.matches_tool_call(
                self.action.tool_call
            )
        ):
            raise ValidationError(
                "ModelResponse.provider_continuation identity must match its tool action."
            )
        if self.provider_turn_continuation is not None:
            if not isinstance(
                self.provider_turn_continuation,
                ProviderTurnContinuation,
            ):
                raise ValidationError(
                    "ModelResponse.provider_turn_continuation must be ProviderTurnContinuation or None."
                )
            if (
                self.proposal is None
                or self.proposal.kind != "tool_calls"
                or not self.provider_turn_continuation.matches_calls(
                    self.proposal.proposal_id,
                    self.proposal.tool_calls,
                )
            ):
                raise ValidationError(
                    "ModelResponse Provider turn continuation identity must match its proposal."
                )
        if (
            self.provider_continuation is not None
            and self.provider_turn_continuation is not None
        ):
            raise ValidationError(
                "ModelResponse cannot contain both legacy and V2 continuation values."
            )
        if self.proposal is not None:
            compatibility_action: ModelAction | None
            if self.proposal.kind == "final":
                compatibility_action = ModelAction.final(
                    self.proposal.final_answer
                )
            elif len(self.proposal.tool_calls) == 1:
                model_call = self.proposal.tool_calls[0]
                runtime_call = model_call.runtime_call
                compatibility_action = ModelAction.tool(
                    ToolCall(
                        call_id=model_call.provider_call_id,
                        tool_id=runtime_call.tool_id,
                        arguments=deepcopy(runtime_call.arguments),
                        run_id=runtime_call.run_id,
                        task_id=runtime_call.task_id,
                        agent_id=runtime_call.agent_id,
                        sequence=runtime_call.sequence,
                    )
                )
            else:
                compatibility_action = None
            object.__setattr__(self, "action", compatibility_action)
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
        payload = {
            "action": (
                self.action.to_dict()
                if self.proposal is None and self.action is not None
                else None
            ),
            "proposal": (
                self.proposal.to_dict() if self.proposal is not None else None
            ),
            "usage": self.usage.to_dict(),
            "provider_request_id": self.provider_request_id,
            "provider_response_id": self.provider_response_id,
            "finish_reason": self.finish_reason,
            "provider_metadata": self.provider_metadata,
            "error": self.error.to_dict() if self.error is not None else None,
        }
        if self.provider_continuation is not None:
            payload["provider_continuation"] = self.provider_continuation.to_dict()
        if self.provider_turn_continuation is not None:
            payload["provider_turn_continuation"] = (
                self.provider_turn_continuation.to_dict()
            )
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelResponse":
        if not isinstance(data, dict):
            raise ValidationError("ModelResponse must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {
                "action",
                "proposal",
                "usage",
                "provider_request_id",
                "provider_response_id",
                "finish_reason",
                "provider_metadata",
                "provider_continuation",
                "provider_turn_continuation",
                "error",
            },
            "ModelResponse",
        )
        raw_action = data.get("action")
        raw_proposal = data.get("proposal")
        raw_error = data.get("error")
        raw_continuation = data.get("provider_continuation")
        raw_turn_continuation = data.get("provider_turn_continuation")
        action = ModelAction.from_dict(raw_action) if raw_action is not None else None
        proposal = (
            ModelTurnProposal.from_dict(raw_proposal)
            if raw_proposal is not None
            else None
        )
        error = ModelProviderError.from_dict(raw_error) if raw_error is not None else None
        return cls(
            action=action,
            proposal=proposal,
            usage=ModelUsage.from_dict(data.get("usage", {})),
            provider_request_id=data.get("provider_request_id"),
            provider_response_id=data.get("provider_response_id"),
            finish_reason=data.get("finish_reason"),
            provider_metadata=data.get("provider_metadata", {}),
            provider_continuation=(
                ProviderContinuation.from_dict(raw_continuation)
                if raw_continuation is not None
                else None
            ),
            provider_turn_continuation=(
                ProviderTurnContinuation.from_dict(raw_turn_continuation)
                if raw_turn_continuation is not None
                else None
            ),
            error=error,
        )


@dataclass(frozen=True)
class ModelToolInteraction:
    """One provider-neutral, completed tool exchange retained for chat history."""

    tool_call: ToolCall
    tool_result: ToolResult

    def __post_init__(self) -> None:
        if not isinstance(self.tool_call, ToolCall):
            raise ValidationError("ModelToolInteraction.tool_call must be ToolCall.")
        if not isinstance(self.tool_result, ToolResult):
            raise ValidationError("ModelToolInteraction.tool_result must be ToolResult.")
        if (
            self.tool_call.call_id != self.tool_result.call_id
            or self.tool_call.tool_id != self.tool_result.tool_id
        ):
            raise ValidationError(
                "ModelToolInteraction call and result identities must match."
            )
        _assert_json_safe_primitive(self.to_dict(), "ModelToolInteraction")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call": self.tool_call.to_dict(),
            "tool_result": self.tool_result.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelToolInteraction":
        if not isinstance(data, dict):
            raise ValidationError("ModelToolInteraction must be a JSON object.")
        _reject_unsupported_fields(
            data,
            {"tool_call", "tool_result"},
            "ModelToolInteraction",
        )
        _require_keys(
            data,
            {"tool_call", "tool_result"},
            "ModelToolInteraction",
        )
        return cls(
            tool_call=ToolCall.from_dict(data["tool_call"]),
            tool_result=ToolResult.from_dict(data["tool_result"]),
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
    provider_continuation: ProviderContinuation | None = None
    model_id: str | None = None
    generation_options: ModelGenerationOptions = field(default_factory=ModelGenerationOptions)
    evidence_context: list[ToolResult] = field(default_factory=list)
    tool_history: list[ModelToolInteraction] = field(default_factory=list)
    tool_turns: list[ModelToolTurn] = field(default_factory=list)

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
        if self.provider_continuation is not None:
            if not isinstance(self.provider_continuation, ProviderContinuation):
                raise ValidationError(
                    "ModelTurnRequest.provider_continuation must be ProviderContinuation or None."
                )
            if self.previous_tool_call is None or self.observation is None:
                raise ValidationError(
                    "ModelTurnRequest.provider_continuation requires a previous tool observation."
                )
            if not self.provider_continuation.matches_tool_call(
                self.previous_tool_call
            ):
                raise ValidationError(
                    "ModelTurnRequest.provider_continuation identity must match the previous tool call."
                )
            if self.provider_continuation.source_sequence >= self.sequence:
                raise ValidationError(
                    "ModelTurnRequest.provider_continuation must originate from an earlier turn."
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
        _validate_verified_evidence_context(self.evidence_context)
        if not isinstance(self.tool_history, list):
            raise ValidationError("ModelTurnRequest.tool_history must be a list.")
        if len(self.tool_history) > MAX_MODEL_TOOL_HISTORY_RESULTS:
            raise ValidationError(
                "ModelTurnRequest.tool_history exceeds the bounded result limit."
            )
        seen_history_call_ids: set[str] = set()
        previous_sequence = 0
        for interaction in self.tool_history:
            if not isinstance(interaction, ModelToolInteraction):
                raise ValidationError(
                    "ModelTurnRequest.tool_history must contain ModelToolInteraction values."
                )
            call = interaction.tool_call
            if call.call_id in seen_history_call_ids:
                raise ValidationError(
                    "ModelTurnRequest.tool_history cannot repeat a tool call."
                )
            if call.sequence <= previous_sequence or call.sequence >= self.sequence:
                raise ValidationError(
                    "ModelTurnRequest.tool_history must be chronological and precede the current turn."
                )
            for expected, actual in (
                (self.run_id, call.run_id),
                (self.task_id, call.task_id),
                (self.agent_id, call.agent_id),
            ):
                if actual is not None and actual != expected:
                    raise ValidationError(
                        "ModelTurnRequest.tool_history identity does not match the request."
                    )
            if (
                self.previous_tool_call is not None
                and call.call_id == self.previous_tool_call.call_id
            ):
                raise ValidationError(
                    "ModelTurnRequest.tool_history must exclude the current observation pair."
                )
            seen_history_call_ids.add(call.call_id)
            previous_sequence = call.sequence
        encoded_history = json.dumps(
            [interaction.to_dict() for interaction in self.tool_history],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded_history.encode("utf-8")) > MAX_MODEL_TOOL_HISTORY_BYTES:
            raise ValidationError(
                "ModelTurnRequest.tool_history exceeds the bounded byte limit."
            )
        if not isinstance(self.tool_turns, list):
            raise ValidationError("ModelTurnRequest.tool_turns must be a list.")
        if self.tool_turns and (
            self.tool_history
            or self.observation is not None
            or self.previous_tool_call is not None
            or self.provider_continuation is not None
        ):
            raise ValidationError(
                "ModelTurnRequest cannot mix V2 tool turns with legacy tool history."
            )
        total_results = 0
        seen_runtime_call_ids: set[str] = set()
        previous_turn_sequence = 0
        for tool_turn in self.tool_turns:
            if not isinstance(tool_turn, ModelToolTurn):
                raise ValidationError(
                    "ModelTurnRequest.tool_turns must contain ModelToolTurn values."
                )
            if (
                tool_turn.source_sequence <= previous_turn_sequence
                or tool_turn.source_sequence >= self.sequence
            ):
                raise ValidationError(
                    "ModelTurnRequest.tool_turns must be chronological and precede the current turn."
                )
            for call in tool_turn.tool_calls:
                runtime_call = call.runtime_call
                if runtime_call.call_id in seen_runtime_call_ids:
                    raise ValidationError(
                        "ModelTurnRequest.tool_turns cannot repeat a Runtime call ID."
                    )
                for expected, actual in (
                    (self.run_id, runtime_call.run_id),
                    (self.task_id, runtime_call.task_id),
                    (self.agent_id, runtime_call.agent_id),
                ):
                    if actual is not None and actual != expected:
                        raise ValidationError(
                            "ModelTurnRequest.tool_turns identity does not match the request."
                        )
                seen_runtime_call_ids.add(runtime_call.call_id)
            total_results += len(tool_turn.tool_results)
            previous_turn_sequence = tool_turn.source_sequence
        if total_results > MAX_MODEL_TOOL_HISTORY_RESULTS:
            raise ValidationError(
                "ModelTurnRequest.tool_turns exceeds the bounded result limit."
            )
        encoded_tool_turns = json.dumps(
            [turn.to_dict() for turn in self.tool_turns],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded_tool_turns.encode("utf-8")) > MAX_MODEL_TOOL_HISTORY_BYTES:
            raise ValidationError(
                "ModelTurnRequest.tool_turns exceeds the bounded byte limit."
            )
        _assert_json_safe_primitive(self.to_dict(), "ModelTurnRequest")

    def to_dict(self) -> dict:
        payload = {
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
            "evidence_context": [result.to_dict() for result in self.evidence_context],
        }
        if self.tool_history:
            payload["tool_history"] = [
                interaction.to_dict() for interaction in self.tool_history
            ]
        if self.tool_turns:
            payload["tool_turns"] = [turn.to_dict() for turn in self.tool_turns]
        if self.provider_continuation is not None:
            payload["provider_continuation"] = self.provider_continuation.to_dict()
        return payload

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
                "provider_continuation",
                "model_id",
                "generation_options",
                "evidence_context",
                "tool_history",
                "tool_turns",
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
        raw_continuation = data.get("provider_continuation")
        raw_tools = data["available_tools"]
        raw_evidence_context = data.get("evidence_context", [])
        raw_tool_history = data.get("tool_history", [])
        raw_tool_turns = data.get("tool_turns", [])
        if not isinstance(raw_tools, list):
            raise ValidationError("ModelTurnRequest.available_tools must be a list.")
        if not isinstance(raw_evidence_context, list):
            raise ValidationError("ModelTurnRequest.evidence_context must be a list.")
        if not isinstance(raw_tool_history, list):
            raise ValidationError("ModelTurnRequest.tool_history must be a list.")
        if not isinstance(raw_tool_turns, list):
            raise ValidationError("ModelTurnRequest.tool_turns must be a list.")
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
            provider_continuation=(
                ProviderContinuation.from_dict(raw_continuation)
                if raw_continuation is not None
                else None
            ),
            model_id=data.get("model_id"),
            generation_options=ModelGenerationOptions.from_dict(
                data.get("generation_options", {})
            ),
            evidence_context=[
                ToolResult.from_dict(item) for item in raw_evidence_context
            ],
            tool_history=[
                ModelToolInteraction.from_dict(item) for item in raw_tool_history
            ],
            tool_turns=[ModelToolTurn.from_dict(item) for item in raw_tool_turns],
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
    "MAX_PROVIDER_CONTINUATION_BYTES",
    "MAX_MODEL_TOOL_CALLS_PER_PROPOSAL",
    "MAX_MODEL_TOOL_HISTORY_BYTES",
    "MAX_MODEL_TOOL_HISTORY_RESULTS",
    "MAX_VERIFIED_EVIDENCE_CONTEXT_BYTES",
    "MAX_VERIFIED_EVIDENCE_CONTEXT_RESULTS",
    "MODEL_ACTION_KINDS",
    "MODEL_PROPOSAL_KINDS",
    "MODEL_VISIBLE_EVIDENCE_TOOL_IDS",
    "MODEL_PROVIDER_ERROR_OUTCOMES",
    "MODEL_PROVIDER_ERROR_SPECS",
    "ModelAction",
    "ModelAdapter",
    "ModelGenerationOptions",
    "ModelProviderAdapter",
    "ModelProviderError",
    "ModelResponse",
    "ModelToolCall",
    "ModelToolInteraction",
    "ModelToolResult",
    "ModelToolTurn",
    "ModelTurnProposal",
    "ModelTurnRequest",
    "ModelUsage",
    "ProviderCapability",
    "ProviderContinuation",
    "ProviderTurnContinuation",
    "ScriptedModelAdapter",
    "is_verified_evidence_result",
    "runtime_call_id_for",
    "select_bounded_model_tool_turns",
    "tool_result_id_for",
]
