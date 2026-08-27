"""Offline-testable Gemini provider boundary for P8.5.

The adapter maps bounded LinkLoom model requests to the official Google Gen AI
SDK's ``generate_content(model=..., contents=..., config=...)`` call shape and
maps one provider proposal back to the neutral model contracts.  It
deliberately receives an injected client rather than creating credentials,
importing an SDK, executing tools, or owning runtime decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
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


class GeminiClient(Protocol):
    """The smallest injected client surface needed by the adapter."""

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> Any:
        """Return a Gemini-shaped response or raise a provider exception."""


_MAX_PROVIDER_INPUT_BYTES = 32 * 1024
_MAX_PROVIDER_OBSERVATION_BYTES = 32 * 1024
_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "type",
        "description",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "minItems",
        "maxItems",
    }
)
_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FINISH_REASON_MAP = {
    "STOP": "stop",
    "END": "stop",
    "FINISHED": "stop",
    "MAX_TOKENS": "max_tokens",
    "LENGTH": "max_tokens",
    "SAFETY": "safety",
    "BLOCKED": "safety",
    "RECITATION": "recitation",
}
_ERROR_MESSAGES = {
    "MODEL_AUTH_REQUIRED": "Gemini authentication was not available.",
    "MODEL_INVALID_REQUEST": "The Gemini request was invalid.",
    "MODEL_RATE_LIMITED": "The Gemini provider rate limit was reached.",
    "MODEL_TIMEOUT": "The Gemini provider request timed out.",
    "MODEL_TRANSIENT_FAILURE": "The Gemini provider failed transiently.",
    "MODEL_UNAVAILABLE": "The requested Gemini model was unavailable.",
    "MODEL_RESPONSE_MALFORMED": "The Gemini provider response was malformed.",
    "MODEL_TOOL_CALL_PARSE_FAILED": "The Gemini function call could not be parsed safely.",
    "MODEL_RESPONSE_UNSUPPORTED": "The Gemini provider response shape is unsupported.",
    "MODEL_TOOL_SCHEMA_UNSUPPORTED": "A tool schema cannot be represented safely for Gemini.",
    "MODEL_CANCELLED": "The Gemini provider request was cancelled.",
}


class _AdapterFailure(Exception):
    """Internal safe failure used before a ModelResponse is constructed."""

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
    except (AttributeError, IndexError, KeyError, ValueError):
        # Some SDK convenience properties raise when a response contains a
        # different part kind, e.g. ``text`` on a function-call response.
        # Treat that property as absent and inspect the explicit parts below.
        return default


def _first_present(value: Any, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        candidate = _read(value, key, None)
        if candidate is not None:
            return candidate
    return default


def _safe_provider_id(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    _validate_optional_provider_text(value, field_name)
    return value


def _bounded_json(value: Any, field_name: str, maximum_bytes: int) -> Any:
    _assert_json_safe_primitive(value, field_name)
    _assert_no_forbidden_persisted_keys(value, field_name)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > maximum_bytes:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": f"{field_name}_too_large"},
        )
    return deepcopy(value)


def _schema_failure(path: str, keywords: list[str] | None = None) -> None:
    details: dict[str, Any] = {"path": path}
    if keywords:
        details["unsupported_keywords"] = keywords
    raise _AdapterFailure("MODEL_TOOL_SCHEMA_UNSUPPORTED", details=details)


def _validate_schema_for_gemini(schema: Any, path: str = "input_schema") -> None:
    if not isinstance(schema, dict):
        _schema_failure(path)

    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        _schema_failure(path, unsupported)

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SCHEMA_TYPES:
        _schema_failure(path)

    if "enum" in schema:
        if not isinstance(schema["enum"], list):
            _schema_failure(path)
        try:
            _assert_json_safe_primitive(schema["enum"], f"{path}.enum")
        except ValidationError as exc:
            raise _AdapterFailure(
                "MODEL_TOOL_SCHEMA_UNSUPPORTED",
                details={"path": f"{path}.enum", "reason": "enum_not_json_safe"},
            ) from exc

    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], int)
            or schema[key] < 0
        ):
            _schema_failure(path)
    for key in ("minimum", "maximum"):
        if key in schema and (
            isinstance(schema[key], bool)
            or not isinstance(schema[key], (int, float))
        ):
            _schema_failure(path)
    if "pattern" in schema and not isinstance(schema["pattern"], str):
        _schema_failure(path)
    if "description" in schema and not isinstance(schema["description"], str):
        _schema_failure(path)

    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            _schema_failure(path)
        for name, child in properties.items():
            if not isinstance(name, str) or not name.strip():
                _schema_failure(path)
            _validate_schema_for_gemini(child, f"{path}.properties.{name}")

    required = schema.get("required")
    if required is not None and (
        not isinstance(required, list)
        or any(not isinstance(item, str) or not item.strip() for item in required)
    ):
        _schema_failure(path)

    additional_properties = schema.get("additionalProperties")
    if additional_properties is not None and not isinstance(additional_properties, bool):
        _schema_failure(path)

    if "items" in schema:
        _validate_schema_for_gemini(schema["items"], f"{path}.items")


def map_tool_definition_to_gemini_function(
    definition: ToolDefinition,
) -> dict[str, Any]:
    """Map one LinkLoom tool definition to an SDK-compatible declaration."""

    if not isinstance(definition, ToolDefinition):
        raise ValidationError("Gemini function mapping requires ToolDefinition.")
    _validate_schema_for_gemini(definition.input_schema)
    return {
        "name": definition.tool_id,
        "description": definition.description,
        "parameters_json_schema": deepcopy(definition.input_schema),
    }


def _function_response_payload(observation: ToolResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": observation.status,
        "value": observation.value,
    }
    if observation.business_status is not None:
        payload["business_status"] = observation.business_status
    if observation.error is not None:
        payload["error"] = {
            "code": observation.error.code,
            "category": observation.error.category,
            "message": observation.error.message,
        }
    return payload


def _build_contents(request: ModelTurnRequest) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = [
        {"role": "user", "parts": [{"text": request.user_input}]}
    ]
    if request.observation is None:
        return contents
    if request.previous_tool_call is None:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "observation_requires_previous_tool_call"},
        )
    previous = request.previous_tool_call
    observation = _bounded_json(
        _function_response_payload(request.observation),
        "ModelTurnRequest.observation",
        _MAX_PROVIDER_OBSERVATION_BYTES,
    )
    contents.extend(
        [
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": previous.call_id,
                            "name": previous.tool_id,
                            "args": deepcopy(previous.arguments),
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "function_response": {
                            "id": request.observation.call_id,
                            "name": request.observation.tool_id,
                            "response": observation,
                        }
                    }
                ],
            },
        ]
    )
    return contents


def build_gemini_request(
    request: ModelTurnRequest,
    *,
    configured_model_id: str | None = None,
) -> dict[str, Any]:
    """Build a bounded, JSON-safe Gemini request without provider SDK types."""

    if not isinstance(request, ModelTurnRequest):
        raise _AdapterFailure("MODEL_INVALID_REQUEST", details={"reason": "invalid_request_type"})
    model_id = request.model_id or configured_model_id
    if model_id is None:
        raise _AdapterFailure("MODEL_INVALID_REQUEST", details={"reason": "missing_model_id"})
    _validate_provider_text(model_id, "GeminiProviderAdapter.model_id")
    bounded_input = _bounded_json(
        request.user_input,
        "ModelTurnRequest.user_input",
        _MAX_PROVIDER_INPUT_BYTES,
    )
    if not isinstance(bounded_input, str):
        raise _AdapterFailure("MODEL_INVALID_REQUEST", details={"reason": "input_not_text"})

    declarations = [
        map_tool_definition_to_gemini_function(tool)
        for tool in request.available_tools
    ]
    config: dict[str, Any] = {
        "tools": [{"function_declarations": declarations}] if declarations else [],
        "automatic_function_calling": {"disable": True},
    }
    options = request.generation_options
    if options.max_output_tokens is not None:
        config["max_output_tokens"] = options.max_output_tokens
    if options.temperature is not None:
        config["temperature"] = options.temperature

    payload = {
        "model": model_id,
        "contents": _build_contents(request),
        "config": config,
    }
    _assert_json_safe_primitive(payload, "GeminiRequest")
    _assert_no_forbidden_persisted_keys(payload, "GeminiRequest")
    return payload


def _normalize_finish_reason(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        value = enum_value
    if not isinstance(value, str) or not value.strip():
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "invalid_finish_reason"},
        )
    normalized = _FINISH_REASON_MAP.get(value.strip().upper())
    if normalized is None:
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "unsupported_finish_reason"},
        )
    return normalized


def _extract_candidates(response: Any) -> list[Any]:
    raw_candidates = _read(response, "candidates", None)
    if raw_candidates is None:
        return []
    if not isinstance(raw_candidates, (list, tuple)):
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "candidates_not_a_list"},
        )
    return list(raw_candidates)


def _extract_parts(response: Any) -> list[Any]:
    candidates = _extract_candidates(response)
    if len(candidates) > 1:
        raise _AdapterFailure(
            "MODEL_RESPONSE_UNSUPPORTED",
            details={"reason": "multiple_candidates"},
        )
    if not candidates:
        return []
    content = _read(candidates[0], "content", None)
    parts = _read(content, "parts", None)
    if parts is None:
        return []
    if not isinstance(parts, (list, tuple)):
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "parts_not_a_list"},
        )
    return list(parts)


def _extract_function_calls(response: Any) -> list[Any]:
    raw_calls = _read(response, "function_calls", None)
    if raw_calls is not None:
        if not isinstance(raw_calls, (list, tuple)):
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "function_calls_not_a_list"},
            )
        return list(raw_calls)
    calls = []
    for part in _extract_parts(response):
        function_call = _read(part, "function_call", None)
        if function_call is not None:
            calls.append(function_call)
    return calls


def _extract_text(response: Any) -> str | None:
    text = _read(response, "text", None)
    if text is not None:
        return text
    texts: list[str] = []
    for part in _extract_parts(response):
        part_text = _read(part, "text", None)
        if part_text is not None:
            texts.append(part_text)
    if not texts:
        return None
    return "".join(texts)


def _extract_finish_reason(response: Any) -> Any:
    top_level = _read(response, "finish_reason", None)
    if top_level is not None:
        return top_level
    candidates = _extract_candidates(response)
    if candidates:
        return _read(candidates[0], "finish_reason", None)
    return None


def _extract_usage(response: Any, duration_ms: float) -> ModelUsage:
    usage = _first_present(response, ("usage_metadata", "usage"), {})
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping) and not hasattr(usage, "__dict__"):
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "usage_not_an_object"},
        )
    input_tokens = _first_present(
        usage,
        ("prompt_token_count", "input_tokens", "promptTokenCount"),
    )
    output_tokens = _first_present(
        usage,
        ("candidates_token_count", "output_tokens", "candidatesTokenCount"),
    )
    total_tokens = _first_present(
        usage,
        ("total_token_count", "total_tokens", "totalTokenCount"),
    )
    try:
        return ModelUsage(
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


def _provider_metadata(response: Any, model_id: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"provider": "gemini", "model": model_id}
    model_version = _read(response, "model_version", None)
    if model_version is not None:
        _validate_provider_text(model_version, "GeminiResponse.model_version")
        metadata["model_version"] = model_version
    _validate_provider_metadata(metadata, "GeminiResponse.provider_metadata")
    return metadata


def _extract_response_id(response: Any, names: tuple[str, ...], field_name: str) -> str | None:
    value = _first_present(response, names)
    return _safe_provider_id(value, field_name)


def _generated_call_id(request: ModelTurnRequest, tool_name: str) -> str:
    digest = hashlib.sha256(
        f"{request.run_id}:{request.turn_id}:{request.sequence}:{tool_name}".encode("utf-8")
    ).hexdigest()[:20]
    return f"gemini-call-{digest}"


def _parse_function_call(
    raw_call: Any,
    request: ModelTurnRequest,
) -> ToolCall:
    name = _first_present(raw_call, ("name", "tool_name"))
    arguments = _first_present(raw_call, ("args", "arguments"))
    provider_call_id = _first_present(raw_call, ("id", "call_id"))
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
    if not isinstance(arguments, dict):
        raise _AdapterFailure(
            "MODEL_TOOL_CALL_PARSE_FAILED",
            details={"reason": "function_arguments_not_an_object"},
        )
    call_id = provider_call_id or _generated_call_id(request, name)
    if not isinstance(call_id, str) or not _ID_PATTERN.fullmatch(call_id):
        call_id = _generated_call_id(request, name)
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


def _parse_provider_error(response: Any, model_id: str) -> ModelProviderError | None:
    raw_error = _read(response, "error", None)
    if raw_error is None:
        return None
    status_code = _first_present(raw_error, ("status_code", "status", "http_status"))
    if status_code is None:
        status_code = _read(response, "status_code", None)
    code, outcome = _code_for_status(status_code, "MODEL_INVALID_REQUEST")
    request_id = _extract_response_id(
        response,
        ("request_id", "provider_request_id"),
        "GeminiResponse.provider_request_id",
    )
    metadata = {"provider": "gemini", "model": model_id}
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        metadata["http_status"] = status_code
    return _make_error(
        code,
        outcome=outcome,
        provider_request_id=request_id,
        provider_metadata=metadata,
        details={"reason": "provider_error_response"},
    )


def _code_for_status(status_code: Any, default: str) -> tuple[str, str]:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        return default, "known_failure"
    if status_code in {401, 403}:
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if status_code in {400, 404, 422}:
        return "MODEL_INVALID_REQUEST", "known_failure"
    if status_code == 429:
        return "MODEL_RATE_LIMITED", "known_failure"
    if status_code in {408, 504}:
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    if status_code in {500, 502, 503}:
        return "MODEL_UNAVAILABLE", "known_failure"
    return default, "known_failure"


def _exception_code(exception: BaseException) -> tuple[str, str]:
    if isinstance(exception, TimeoutError):
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    # google-genai's APIError exposes the HTTP status as ``code``.  Keep the
    # older ``status_code`` fallback for injected clients and generic SDK
    # wrappers without depending on provider exception classes here.
    status_code = _first_present(exception, ("code", "status_code"))
    if status_code is not None:
        return _code_for_status(status_code, "MODEL_TRANSIENT_FAILURE")
    name = type(exception).__name__.lower()
    if any(marker in name for marker in ("auth", "unauthor", "permission", "credential")):
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if any(marker in name for marker in ("rate", "quota", "throttle")):
        return "MODEL_RATE_LIMITED", "known_failure"
    if any(marker in name for marker in ("timeout", "deadline")):
        return "MODEL_TIMEOUT", "unknown_provider_outcome"
    if any(marker in name for marker in ("cancel", "abort")):
        return "MODEL_CANCELLED", "known_failure"
    if any(marker in name for marker in ("invalid", "badrequest", "argument")):
        return "MODEL_INVALID_REQUEST", "known_failure"
    if any(marker in name for marker in ("unavailable", "notfound")):
        return "MODEL_UNAVAILABLE", "known_failure"
    return "MODEL_TRANSIENT_FAILURE", "unknown_provider_outcome"


def _make_error(
    code: str,
    *,
    outcome: str | None = None,
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
        outcome=outcome or "known_failure",
        provider_request_id=provider_request_id,
        provider_metadata=provider_metadata or {},
        details=details or {},
    )


class GeminiProviderAdapter:
    """Normalize one Gemini provider attempt without executing any tool."""

    def __init__(self, client: GeminiClient, *, model_id: str | None = None) -> None:
        if client is None or not callable(getattr(client, "generate_content", None)):
            raise ValidationError("GeminiProviderAdapter client must implement generate_content().")
        if model_id is not None:
            _validate_provider_text(model_id, "GeminiProviderAdapter.model_id")
        self.client = client
        self.model_id = model_id
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
            payload = build_gemini_request(
                request,
                configured_model_id=self.model_id,
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
            raw_response = self.client.generate_content(
                model=payload["model"],
                contents=payload["contents"],
                config=payload["config"],
            )
        except Exception as exception:
            code, outcome = _exception_code(exception)
            try:
                request_id = _safe_provider_id(
                    _first_present(exception, ("request_id", "provider_request_id")),
                    "GeminiProviderError.provider_request_id",
                )
            except ValidationError:
                request_id = None
            metadata = {"provider": "gemini", "model": payload["model"]}
            status_code = _first_present(exception, ("code", "status_code"))
            if isinstance(status_code, int) and not isinstance(status_code, bool):
                metadata["http_status"] = status_code
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
            return self._parse_response(raw_response, request, payload["model"], duration_ms)
        except _AdapterFailure as failure:
            return self._failure_response(failure)
        except (ValidationError, TypeError, ValueError):
            return ModelResponse(
                error=_make_error(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "response_normalization_failed"},
                )
            )

    def _failure_response(self, failure: _AdapterFailure) -> ModelResponse:
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
        if response is None:
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "empty_response"},
            )
        if isinstance(response, (str, bytes, bytearray, list, tuple)):
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "response_root_not_object"},
            )

        provider_error = _parse_provider_error(response, model_id)
        if provider_error is not None:
            return ModelResponse(
                error=provider_error,
                provider_response_id=_extract_response_id(
                    response,
                    ("response_id", "id"),
                    "GeminiResponse.provider_response_id",
                ),
                finish_reason=_normalize_finish_reason(_extract_finish_reason(response)),
                provider_metadata=_provider_metadata(response, model_id),
                usage=_extract_usage(response, duration_ms),
            )

        calls = _extract_function_calls(response)
        text = _extract_text(response)
        if len(calls) > 1:
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "multiple_tool_calls"},
            )
        if calls and text is not None and text.strip():
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "text_and_tool_call"},
            )
        finish_reason = _normalize_finish_reason(_extract_finish_reason(response))
        response_id = _extract_response_id(
            response,
            ("response_id", "id"),
            "GeminiResponse.provider_response_id",
        )
        request_id = _extract_response_id(
            response,
            ("request_id", "provider_request_id"),
            "GeminiResponse.provider_request_id",
        )
        usage = _extract_usage(response, duration_ms)
        metadata = _provider_metadata(response, model_id)

        if calls:
            action = ModelAction.tool(_parse_function_call(calls[0], request))
            if finish_reason not in {None, "stop"}:
                raise _AdapterFailure(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "tool_call_finish_reason_mismatch"},
                )
            return ModelResponse(
                action=action,
                usage=usage,
                provider_request_id=request_id,
                provider_response_id=response_id,
                finish_reason=finish_reason or "stop",
                provider_metadata=metadata,
            )

        if not isinstance(text, str) or not text.strip():
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "empty_action"},
            )
        return ModelResponse(
            action=ModelAction.final(text),
            usage=usage,
            provider_request_id=request_id,
            provider_response_id=response_id,
            finish_reason=finish_reason or "stop",
            provider_metadata=metadata,
        )
__all__ = [
    "GeminiClient",
    "GeminiProviderAdapter",
    "build_gemini_request",
    "map_tool_definition_to_gemini_function",
]
