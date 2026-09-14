"""Offline-testable DeepSeek Chat Completions provider boundary.

The adapter implements the non-thinking OpenAI-compatible DeepSeek tool loop.
It receives an injected client, maps only provider-neutral LinkLoom contracts,
and never executes tools or owns credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import re
import time
from typing import Any, Protocol

from linkloom.agents.model_adapter import (
    MODEL_PROVIDER_ERROR_SPECS,
    ModelAction,
    ModelProviderError,
    ModelResponse,
    ModelTurnRequest,
    ModelUsage,
    ProviderCapability,
    _validate_optional_provider_text,
    _validate_provider_metadata,
    _validate_provider_text,
)
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import (
    _assert_json_safe_primitive,
    _assert_no_forbidden_persisted_keys,
)
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


class DeepSeekClient(Protocol):
    """Small injected seam matching one non-streaming chat completion."""

    def create_chat_completion(self, **request: Any) -> Any:
        """Return a DeepSeek Chat Completions response or raise an API error."""


DEFAULT_SYSTEM_INSTRUCTION = (
    "Use the available tools as needed and follow the user request exactly. "
    "When enough evidence is available, return the requested final response."
)
_JSON_SYSTEM_SUFFIX = " Return the final response as exactly one valid JSON object."
_MAX_PROVIDER_INPUT_BYTES = 32 * 1024
_MAX_PROVIDER_OBSERVATION_BYTES = 32 * 1024
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FINISH_REASONS = frozenset(
    {
        "stop",
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
        "aborted",
    }
)
_ERROR_MESSAGES = {
    "MODEL_AUTH_REQUIRED": "DeepSeek authentication was not available.",
    "MODEL_INVALID_REQUEST": "The DeepSeek request was invalid.",
    "MODEL_RATE_LIMITED": "The DeepSeek provider rate limit was reached.",
    "MODEL_TIMEOUT": "The DeepSeek provider request timed out.",
    "MODEL_TRANSIENT_FAILURE": "The DeepSeek provider failed transiently.",
    "MODEL_UNAVAILABLE": "The requested DeepSeek model was unavailable.",
    "MODEL_RESPONSE_MALFORMED": "The DeepSeek provider response was malformed.",
    "MODEL_TOOL_CALL_PARSE_FAILED": "The DeepSeek function call could not be parsed safely.",
    "MODEL_RESPONSE_UNSUPPORTED": "The DeepSeek provider response shape is unsupported.",
    "MODEL_TOOL_SCHEMA_UNSUPPORTED": "A tool schema cannot be represented safely for DeepSeek.",
    "MODEL_CANCELLED": "The DeepSeek provider request was cancelled.",
}


class _AdapterFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, Any] | None = None,
        outcome: str = "known_failure",
        provider_request_id: str | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = details or {}
        self.outcome = outcome
        self.provider_request_id = provider_request_id
        self.provider_metadata = provider_metadata or {}


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    try:
        return getattr(value, key, default)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return default


def _first_present(value: Any, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        candidate = _read(value, key, None)
        if candidate is not None:
            return candidate
    return default


def _bounded_json(value: Any, field_name: str, maximum_bytes: int) -> Any:
    _assert_json_safe_primitive(value, field_name)
    _assert_no_forbidden_persisted_keys(value, field_name)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": f"{field_name}_too_large"},
        )
    return deepcopy(value)


def _canonical_json(value: Any, field_name: str, maximum_bytes: int) -> str:
    bounded = _bounded_json(value, field_name, maximum_bytes)
    return json.dumps(
        bounded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def map_tool_definition_to_deepseek_tool(
    definition: ToolDefinition,
) -> dict[str, Any]:
    """Map one neutral definition to the official Chat Completions shape."""

    if not isinstance(definition, ToolDefinition):
        raise ValidationError("DeepSeek function mapping requires ToolDefinition.")
    parameters = _bounded_json(
        definition.input_schema,
        "ToolDefinition.input_schema",
        _MAX_PROVIDER_INPUT_BYTES,
    )
    return {
        "type": "function",
        "function": {
            "name": definition.tool_id,
            "description": definition.description,
            "parameters": parameters,
        },
    }


def _function_response_payload(observation: ToolResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": observation.status,
        "value": observation.value,
    }
    if observation.business_status is not None:
        payload["business_status"] = observation.business_status
    if observation.error is not None:
        payload["error"] = observation.error.to_dict()
    return payload


def _model_visible_evidence_context(request: ModelTurnRequest) -> list[dict[str, Any]]:
    return [
        {
            "call_id": result.call_id,
            "tool_id": result.tool_id,
            "value": deepcopy(result.value),
        }
        for result in request.evidence_context
    ]


def _tool_exchange_messages(
    call: ToolCall,
    observation: ToolResult,
) -> list[dict[str, Any]]:
    if call.call_id != observation.call_id or call.tool_id != observation.tool_id:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "tool_result_identity_mismatch"},
        )
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_id,
                        "arguments": _canonical_json(
                            call.arguments,
                            "ModelTurnRequest.tool_call.arguments",
                            _MAX_PROVIDER_INPUT_BYTES,
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": observation.call_id,
            "content": _canonical_json(
                _function_response_payload(observation),
                "ModelTurnRequest.tool_result",
                _MAX_PROVIDER_OBSERVATION_BYTES,
            ),
        },
    ]


def _build_messages(
    request: ModelTurnRequest,
    *,
    system_instruction: str,
) -> list[dict[str, Any]]:
    evidence_context = _model_visible_evidence_context(request)
    if evidence_context:
        bounded_context = _bounded_json(
            evidence_context,
            "ModelTurnRequest.evidence_context",
            _MAX_PROVIDER_INPUT_BYTES,
        )
        user_input = (
            f"{request.user_input}\nverified_evidence_context="
            f"{json.dumps(bounded_context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )
    else:
        user_input = request.user_input
    bounded_input = _bounded_json(
        user_input,
        "ModelTurnRequest.model_visible_input",
        _MAX_PROVIDER_INPUT_BYTES,
    )
    if not isinstance(bounded_input, str):
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "input_not_text"},
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": bounded_input},
    ]
    for interaction in request.tool_history:
        messages.extend(
            _tool_exchange_messages(
                interaction.tool_call,
                interaction.tool_result,
            )
        )
    if request.observation is None:
        if request.previous_tool_call is not None:
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "previous_tool_call_requires_observation"},
            )
        return messages
    if request.previous_tool_call is None:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "observation_requires_previous_tool_call"},
        )
    previous = request.previous_tool_call
    observation = request.observation
    messages.extend(_tool_exchange_messages(previous, observation))
    return messages


def build_deepseek_request(
    request: ModelTurnRequest,
    *,
    configured_model_id: str | None = None,
    system_instruction: str | None = None,
    json_output: bool = False,
) -> dict[str, Any]:
    """Build one bounded, non-thinking DeepSeek Chat Completions request."""

    if not isinstance(request, ModelTurnRequest):
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "invalid_request_type"},
        )
    model_id = request.model_id or configured_model_id
    if model_id is None:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "missing_model_id"},
        )
    _validate_provider_text(model_id, "DeepSeekProviderAdapter.model_id")
    instruction = system_instruction or DEFAULT_SYSTEM_INSTRUCTION
    _validate_provider_text(instruction, "DeepSeekProviderAdapter.system_instruction")
    if json_output and "json" not in instruction.lower():
        instruction += _JSON_SYSTEM_SUFFIX

    tools = [
        map_tool_definition_to_deepseek_tool(definition)
        for definition in request.available_tools
    ]
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": _build_messages(
            request,
            system_instruction=instruction,
        ),
        "thinking": {"type": "disabled"},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_output:
        payload["response_format"] = {"type": "json_object"}
    options = request.generation_options
    if options.max_output_tokens is not None:
        payload["max_tokens"] = options.max_output_tokens
    if options.temperature is not None:
        payload["temperature"] = options.temperature
    _assert_json_safe_primitive(payload, "DeepSeekRequest")
    _assert_no_forbidden_persisted_keys(payload, "DeepSeekRequest")
    return payload


def _safe_provider_id(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    _validate_optional_provider_text(value, field_name)
    return value


def _normalize_finish_reason(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _FINISH_REASONS:
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "invalid_finish_reason"},
        )
    return value


def _extract_usage(response: Any, duration_ms: float) -> tuple[ModelUsage, dict[str, int]]:
    raw = _read(response, "usage", None)
    if raw is None:
        return ModelUsage(duration_ms=duration_ms), {}
    if not isinstance(raw, Mapping) and not hasattr(raw, "__dict__"):
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "usage_not_an_object"},
        )
    input_tokens = _read(raw, "prompt_tokens", None)
    output_tokens = _read(raw, "completion_tokens", None)
    total_tokens = _read(raw, "total_tokens", None)
    try:
        usage = ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
        )
    except ValidationError as exc:
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "invalid_usage"},
        ) from exc

    breakdown: dict[str, int] = {}
    for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        value = _read(raw, key, None)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _AdapterFailure(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "invalid_usage"},
                )
            breakdown[key] = value
    details = _read(raw, "completion_tokens_details", None)
    reasoning_tokens = _read(details, "reasoning_tokens", None)
    if reasoning_tokens is not None:
        if (
            isinstance(reasoning_tokens, bool)
            or not isinstance(reasoning_tokens, int)
            or reasoning_tokens < 0
        ):
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "invalid_usage"},
            )
        breakdown["reasoning_tokens"] = reasoning_tokens
    return usage, breakdown


def _status_code(value: Any) -> int | None:
    status = _first_present(value, ("status_code", "status", "http_status", "code"))
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _code_for_status(status: int | None, default: str) -> tuple[str, str]:
    if status == 401:
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if status in {400, 422}:
        return "MODEL_INVALID_REQUEST", "known_failure"
    if status in {402, 403, 404}:
        return "MODEL_UNAVAILABLE", "known_failure"
    if status == 429:
        return "MODEL_RATE_LIMITED", "known_failure"
    if status in {408, 504}:
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    if status in {500, 502, 503}:
        return "MODEL_TRANSIENT_FAILURE", "known_failure"
    return default, "known_failure"


def _exception_code(exception: BaseException) -> tuple[str, str]:
    status = _status_code(exception)
    if status is not None:
        return _code_for_status(status, "MODEL_TRANSIENT_FAILURE")
    if isinstance(exception, TimeoutError):
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    name = type(exception).__name__.lower()
    if any(marker in name for marker in ("auth", "unauthor", "permission", "credential")):
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if any(marker in name for marker in ("rate", "quota", "throttle")):
        return "MODEL_RATE_LIMITED", "known_failure"
    if any(marker in name for marker in ("timeout", "deadline")):
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    if any(marker in name for marker in ("cancel", "abort")):
        return "MODEL_CANCELLED", "known_failure"
    if any(marker in name for marker in ("invalid", "badrequest", "unprocessable")):
        return "MODEL_INVALID_REQUEST", "known_failure"
    return "MODEL_TRANSIENT_FAILURE", "unknown_provider_outcome"


def _make_error(
    code: str,
    *,
    outcome: str = "known_failure",
    provider_request_id: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> ModelProviderError:
    if code not in MODEL_PROVIDER_ERROR_SPECS:
        code = "MODEL_TRANSIENT_FAILURE"
    category, retryable = MODEL_PROVIDER_ERROR_SPECS[code]
    return ModelProviderError(
        code=code,
        category=category,
        message=_ERROR_MESSAGES[code],
        retryable=retryable,
        outcome=outcome,
        provider_request_id=provider_request_id,
        provider_metadata=provider_metadata or {},
        details=details or {},
    )


def _provider_error_response(response: Any, model_id: str) -> ModelProviderError | None:
    raw_error = _read(response, "error", None)
    if raw_error is None:
        return None
    status = _status_code(raw_error)
    if status is None:
        status = _status_code(response)
    code, outcome = _code_for_status(status, "MODEL_INVALID_REQUEST")
    metadata: dict[str, Any] = {"provider": "deepseek", "model": model_id}
    if status is not None:
        metadata["http_status"] = status
    request_id = _safe_provider_id(
        _first_present(response, ("request_id", "provider_request_id")),
        "DeepSeekResponse.provider_request_id",
    )
    return _make_error(
        code,
        outcome=outcome,
        provider_request_id=request_id,
        provider_metadata=metadata,
        details={"reason": "provider_error_response"},
    )


class DeepSeekProviderAdapter:
    """Normalize one non-thinking DeepSeek attempt without executing tools."""

    def __init__(
        self,
        client: DeepSeekClient,
        *,
        model_id: str | None = None,
        system_instruction: str | None = None,
        json_output: bool = False,
    ) -> None:
        if client is None or not callable(
            getattr(client, "create_chat_completion", None)
        ):
            raise ValidationError(
                "DeepSeekProviderAdapter client must implement create_chat_completion()."
            )
        if model_id is not None:
            _validate_provider_text(model_id, "DeepSeekProviderAdapter.model_id")
        if system_instruction is not None:
            _validate_provider_text(
                system_instruction,
                "DeepSeekProviderAdapter.system_instruction",
            )
        if not isinstance(json_output, bool):
            raise ValidationError("DeepSeekProviderAdapter.json_output must be boolean.")
        self.client = client
        self.model_id = model_id
        self.system_instruction = system_instruction
        self.json_output = json_output
        self.capability = ProviderCapability(
            supports_tool_calls=True,
            supports_final_answers=True,
            supports_multiple_tool_calls=False,
            supports_streaming=False,
            provider_executes_tools=False,
        )

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
        started = time.perf_counter()
        try:
            payload = build_deepseek_request(
                request,
                configured_model_id=self.model_id,
                system_instruction=self.system_instruction,
                json_output=self.json_output,
            )
        except _AdapterFailure as failure:
            return self._failure_response(failure)
        except (ValidationError, TypeError, ValueError):
            return ModelResponse(
                error=_make_error(
                    "MODEL_INVALID_REQUEST",
                    details={"reason": "request_mapping_failed"},
                )
            )

        try:
            raw_response = self.client.create_chat_completion(**payload)
        except Exception as exception:
            code, outcome = _exception_code(exception)
            try:
                request_id = _safe_provider_id(
                    _first_present(exception, ("request_id", "provider_request_id")),
                    "DeepSeekProviderError.provider_request_id",
                )
            except ValidationError:
                request_id = None
            metadata: dict[str, Any] = {
                "provider": "deepseek",
                "model": payload["model"],
            }
            status = _status_code(exception)
            if status is not None:
                metadata["http_status"] = status
            return ModelResponse(
                error=_make_error(
                    code,
                    outcome=outcome,
                    provider_request_id=request_id,
                    provider_metadata=metadata,
                    details={"reason": "provider_exception"},
                )
            )

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        try:
            return self._parse_response(
                raw_response,
                request,
                payload["model"],
                duration_ms,
            )
        except _AdapterFailure as failure:
            return self._failure_response(failure)
        except (ValidationError, TypeError, ValueError):
            return ModelResponse(
                error=_make_error(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "response_normalization_failed"},
                )
            )

    @staticmethod
    def _failure_response(failure: _AdapterFailure) -> ModelResponse:
        return ModelResponse(
            error=_make_error(
                failure.code,
                outcome=failure.outcome,
                provider_request_id=failure.provider_request_id,
                provider_metadata=failure.provider_metadata,
                details=failure.details,
            )
        )

    def _parse_response(
        self,
        response: Any,
        request: ModelTurnRequest,
        model_id: str,
        duration_ms: float,
    ) -> ModelResponse:
        if response is None or isinstance(
            response,
            (str, bytes, bytearray, list, tuple),
        ):
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "response_root_not_object"},
            )

        provider_error = _provider_error_response(response, model_id)
        if provider_error is not None:
            return ModelResponse(error=provider_error)

        choices = _read(response, "choices", None)
        if not isinstance(choices, (list, tuple)) or len(choices) != 1:
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "choices_must_contain_one_item"},
            )
        choice = choices[0]
        message = _read(choice, "message", None)
        if message is None:
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "missing_message"},
            )
        finish_reason = _normalize_finish_reason(
            _read(choice, "finish_reason", None)
        )
        if finish_reason == "aborted":
            raise _AdapterFailure(
                "MODEL_CANCELLED",
                details={"reason": "provider_aborted"},
            )
        if finish_reason == "insufficient_system_resource":
            raise _AdapterFailure(
                "MODEL_TRANSIENT_FAILURE",
                details={"reason": "insufficient_system_resource"},
            )
        reasoning_content = _read(message, "reasoning_content", None)
        if reasoning_content not in (None, ""):
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "unexpected_reasoning_content"},
            )
        raw_calls = _read(message, "tool_calls", None)
        if raw_calls is None:
            calls: list[Any] = []
        elif isinstance(raw_calls, (list, tuple)):
            calls = list(raw_calls)
        else:
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "tool_calls_not_a_list"},
            )
        content = _read(message, "content", None)
        if len(calls) > 1:
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "multiple_tool_calls"},
            )
        if calls and isinstance(content, str) and content.strip():
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "text_and_tool_call"},
            )

        usage, usage_breakdown = _extract_usage(response, duration_ms)
        if usage_breakdown.get("reasoning_tokens", 0) != 0:
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "unexpected_reasoning_usage"},
            )
        response_id = _safe_provider_id(
            _read(response, "id", None),
            "DeepSeekResponse.provider_response_id",
        )
        request_id = _safe_provider_id(
            _first_present(response, ("request_id", "provider_request_id")),
            "DeepSeekResponse.provider_request_id",
        )
        response_model = _read(response, "model", None)
        if response_model is not None:
            _validate_provider_text(response_model, "DeepSeekResponse.model")
        metadata: dict[str, Any] = {
            "provider": "deepseek",
            "model": response_model or model_id,
            **usage_breakdown,
        }
        fingerprint = _read(response, "system_fingerprint", None)
        if fingerprint is not None:
            _validate_provider_text(
                fingerprint,
                "DeepSeekResponse.system_fingerprint",
            )
            metadata["system_fingerprint"] = fingerprint
        _validate_provider_metadata(metadata, "DeepSeekResponse.provider_metadata")

        if calls:
            if finish_reason != "tool_calls":
                raise _AdapterFailure(
                    "MODEL_RESPONSE_MALFORMED",
                    details={
                        "reason": "tool_call_finish_reason_mismatch",
                        "finish_reason": finish_reason,
                    },
                )
            call = self._parse_tool_call(calls[0], request)
            return ModelResponse(
                action=ModelAction.tool(call),
                usage=usage,
                provider_request_id=request_id,
                provider_response_id=response_id,
                finish_reason=finish_reason,
                provider_metadata=metadata,
            )

        if not isinstance(content, str) or not content.strip():
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "empty_action"},
            )
        if finish_reason != "stop":
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={
                    "reason": "final_finish_reason_mismatch",
                    "finish_reason": finish_reason,
                },
            )
        return ModelResponse(
            action=ModelAction.final(content),
            usage=usage,
            provider_request_id=request_id,
            provider_response_id=response_id,
            finish_reason=finish_reason,
            provider_metadata=metadata,
        )

    @staticmethod
    def _parse_tool_call(raw_call: Any, request: ModelTurnRequest) -> ToolCall:
        if _read(raw_call, "type", None) != "function":
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "unsupported_tool_call_type"},
            )
        call_id = _read(raw_call, "id", None)
        function = _read(raw_call, "function", None)
        name = _read(function, "name", None)
        arguments_text = _read(function, "arguments", None)
        if not isinstance(call_id, str) or _ID_PATTERN.fullmatch(call_id) is None:
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "invalid_function_call_id"},
            )
        if not isinstance(name, str) or not name.strip():
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "missing_function_name"},
            )
        if name not in {tool.tool_id for tool in request.available_tools}:
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "undeclared_tool"},
            )
        if not isinstance(arguments_text, str):
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "function_arguments_not_text"},
            )
        try:
            arguments = json.loads(arguments_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "function_arguments_not_json"},
            ) from exc
        if not isinstance(arguments, dict):
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "function_arguments_not_an_object"},
            )
        _bounded_json(
            arguments,
            "DeepSeekFunctionCall.arguments",
            _MAX_PROVIDER_INPUT_BYTES,
        )
        try:
            return ToolCall(
                call_id=call_id,
                tool_id=name,
                arguments=deepcopy(arguments),
                run_id=request.run_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                sequence=request.sequence,
            )
        except ValidationError as exc:
            raise _AdapterFailure(
                "MODEL_TOOL_CALL_PARSE_FAILED",
                details={"reason": "invalid_normalized_tool_call"},
            ) from exc


__all__ = [
    "DEFAULT_SYSTEM_INSTRUCTION",
    "DeepSeekClient",
    "DeepSeekProviderAdapter",
    "build_deepseek_request",
    "map_tool_definition_to_deepseek_tool",
]
