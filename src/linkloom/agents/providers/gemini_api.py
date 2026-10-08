"""Offline-testable Gemini provider boundary for P8.5.

The adapter maps bounded LinkLoom model requests to the official Google Gen AI
SDK's ``generate_content(model=..., contents=..., config=...)`` call shape and
maps one provider proposal back to the neutral model contracts.  It
deliberately receives an injected client rather than creating credentials,
importing an SDK, executing tools, or owning runtime decisions.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
import time
from typing import Any, Callable, Protocol

from linkloom.agents.model_adapter import (
    MODEL_PROVIDER_ERROR_SPECS,
    ModelAction,
    ModelProviderError,
    ModelResponse,
    ModelToolCall,
    ModelTurnProposal,
    ModelTurnRequest,
    ModelUsage,
    ProviderCapability,
    ProviderContinuation,
    ProviderTurnContinuation,
    _validate_optional_provider_text,
    _validate_provider_metadata,
    _validate_provider_text,
)
from linkloom.agents.providers.gemini_diagnostics import (
    gemini_exception_diagnostics,
    gemini_response_diagnostics,
)
from linkloom.agents.providers.retry_policy import classify_retryable_failure
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
_GEMINI_SCHEMA_CONSTRAINTS_NOT_PROJECTED = frozenset({"maxItems", "maxLength"})
_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_GEMINI_CONTINUATION_FORMAT = "gemini-function-call-v1"
_GEMINI_TURN_CONTINUATION_FORMAT = "gemini-function-call-turn-v2"
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
    "MODEL_BILLING_BLOCKED": "Gemini billing or prepayment is unavailable.",
    "MODEL_INVALID_REQUEST": "The Gemini request was invalid.",
    "MODEL_RATE_LIMITED": "The Gemini provider rate limit was reached.",
    "MODEL_TIMEOUT": "The Gemini provider request timed out.",
    "MODEL_TRANSIENT_FAILURE": "The Gemini provider failed transiently.",
    "MODEL_UNKNOWN_FAILURE": "The Gemini provider failed with an unclassified error.",
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


def _request_fingerprint(request: ModelTurnRequest) -> str:
    return hashlib.sha256(
        json.dumps(
            request.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


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


def _gemini_json_schema_wire_compatibility(request: Any) -> None:
    """Write the SDK's JSON Schema parameter under Gemini's wire field name.

    google-genai 2.22.0 serializes ``parameters_json_schema`` as a snake-case
    JSON key, although the Developer API expects ``parametersJsonSchema``.
    Keep the workaround at the HTTP boundary so the canonical JSON Schema is
    passed through unchanged.
    """

    url = getattr(request, "url", None)
    if (
        getattr(url, "host", "").casefold() != "generativelanguage.googleapis.com"
        or not getattr(url, "path", "").endswith(":generateContent")
    ):
        return
    try:
        body = json.loads(request.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(body, dict):
        return

    changed = False
    tools = body.get("tools")
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        declarations = tool.get("functionDeclarations")
        if not isinstance(declarations, list):
            continue
        for declaration in declarations:
            if not isinstance(declaration, dict):
                continue
            if "parameters_json_schema" not in declaration:
                continue
            if "parametersJsonSchema" in declaration:
                raise ValueError("conflicting Gemini JSON Schema wire fields")
            declaration["parametersJsonSchema"] = declaration.pop(
                "parameters_json_schema"
            )
            changed = True

    if not changed:
        return
    import httpx

    content = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    request._content = content
    request.stream = httpx.ByteStream(content)
    request.headers["content-length"] = str(len(content))


def _wire_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _gemini_wire_payload_fingerprints(request: Any) -> dict[str, str | None] | None:
    """Fingerprint the exact GenerateContent bytes after all request hooks."""

    url = getattr(request, "url", None)
    if (
        getattr(url, "host", "").casefold() != "generativelanguage.googleapis.com"
        or not getattr(url, "path", "").endswith(":generateContent")
    ):
        return None
    try:
        wire_bytes = request.content
        body = json.loads(wire_bytes.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None

    schemas: list[Any] = []
    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            declarations = tool.get("functionDeclarations")
            if not isinstance(declarations, list):
                continue
            for declaration in declarations:
                if isinstance(declaration, Mapping):
                    schema = declaration.get("parametersJsonSchema")
                    if schema is not None:
                        schemas.append(schema)
    schema_subtree: Any = schemas[0] if len(schemas) == 1 else schemas
    tool_config = body.get("toolConfig")
    return {
        "wire_payload_sha256": hashlib.sha256(wire_bytes).hexdigest(),
        "wire_schema_subtree_sha256": (
            _wire_json_sha256(schema_subtree) if schemas else None
        ),
        "wire_tool_config_sha256": (
            _wire_json_sha256(tool_config) if tool_config is not None else None
        ),
    }


def _gemini_wire_observer_hook(
    observer: Callable[[Mapping[str, str | None]], Any],
) -> Callable[[Any], None]:
    def observe(request: Any) -> None:
        fingerprints = _gemini_wire_payload_fingerprints(request)
        if fingerprints is None:
            return
        request.extensions["linkloom_gemini_wire_fingerprints"] = fingerprints
        # The callback receives hashes only; it never receives request bytes.
        observer(dict(fingerprints))

    return observe


def build_gemini_sdk_client(
    api_key: str,
    *,
    genai_module: Any | None = None,
    types_module: Any | None = None,
    http_options_kwargs: Mapping[str, Any] | None = None,
    wire_payload_observer: Callable[[Mapping[str, str | None]], Any] | None = None,
) -> Any:
    """Construct an official Developer API client with the wire fix installed.

    Importing the SDK stays lazy so the provider adapter remains usable without
    the optional ``google-genai`` dependency. Existing HTTP client arguments
    and hooks are preserved.
    """

    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Gemini API key must be a non-empty string")
    if genai_module is None or types_module is None:
        from google import genai as sdk_genai
        from google.genai import types as sdk_types

        genai_module = genai_module or sdk_genai
        types_module = types_module or sdk_types

    options = dict(http_options_kwargs or {})
    client_args = options.get("client_args") or {}
    if not isinstance(client_args, Mapping):
        raise TypeError("Gemini client_args must be a mapping")
    client_args = dict(client_args)
    event_hooks = client_args.get("event_hooks") or {}
    if not isinstance(event_hooks, Mapping):
        raise TypeError("Gemini event_hooks must be a mapping")
    event_hooks = dict(event_hooks)
    request_hooks = event_hooks.get("request") or []
    if not isinstance(request_hooks, (list, tuple)):
        raise TypeError("Gemini request hooks must be a list or tuple")
    request_hooks = list(request_hooks)
    if _gemini_json_schema_wire_compatibility not in request_hooks:
        request_hooks.append(_gemini_json_schema_wire_compatibility)
    if wire_payload_observer is not None:
        observer_hook = _gemini_wire_observer_hook(wire_payload_observer)
        if not any(
            getattr(hook, "_linkloom_wire_observer", False)
            for hook in request_hooks
        ):
            setattr(observer_hook, "_linkloom_wire_observer", True)
            request_hooks.append(observer_hook)
    event_hooks["request"] = request_hooks
    client_args["event_hooks"] = event_hooks
    options["client_args"] = client_args

    http_options = types_module.HttpOptions(**options)
    return genai_module.Client(
        api_key=api_key,
        vertexai=False,
        http_options=http_options,
    )


def _validate_schema_for_gemini(schema: Any, path: str = "input_schema") -> None:
    if not isinstance(schema, dict):
        _schema_failure(path)

    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        _schema_failure(path, unsupported)

    schema_type = schema.get("type")
    if schema_type is not None:
        if isinstance(schema_type, list):
            if (
                len(schema_type) != 2
                or schema_type.count("null") != 1
                or sum(item != "null" for item in schema_type) != 1
                or any(
                    not isinstance(item, str) or item not in _SCHEMA_TYPES
                    for item in schema_type
                )
            ):
                _schema_failure(path)
        elif not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
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


def _project_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Project only the live-confirmed unsupported constraints from Gemini schema.

    This projection is transport-specific. The canonical schema stays intact,
    and Semantic Ingestion still enforces these limits on accepted output.
    """

    projected: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GEMINI_SCHEMA_CONSTRAINTS_NOT_PROJECTED:
            continue
        if key == "properties":
            projected[key] = {
                name: _project_schema_for_gemini(child)
                for name, child in value.items()
            }
        elif key == "items":
            projected[key] = _project_schema_for_gemini(value)
        else:
            projected[key] = deepcopy(value)
    return projected


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
        "parameters_json_schema": _project_schema_for_gemini(definition.input_schema),
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


def _model_visible_evidence_context(request: ModelTurnRequest) -> list[dict[str, Any]]:
    """Project only already-validated source evidence into a provider prompt."""
    return [
        {
            "call_id": result.call_id,
            "tool_id": result.tool_id,
            "value": deepcopy(result.value),
        }
        for result in request.evidence_context
    ]


def _model_tool_turn_contents(tool_turn) -> list[dict[str, Any]]:
    signature_by_provider_id: dict[str, str] = {}
    continuation = tool_turn.provider_continuation
    if continuation is not None:
        if continuation.provider_id != "gemini":
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_identity_mismatch"},
            )
        payload = continuation.payload
        if set(payload) != {"format", "parts"} or payload.get("format") != (
            _GEMINI_TURN_CONTINUATION_FORMAT
        ):
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_payload_invalid"},
            )
        raw_parts = payload.get("parts")
        if not isinstance(raw_parts, list) or len(raw_parts) != len(
            tool_turn.tool_calls
        ):
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_payload_invalid"},
            )
        for model_call, raw_part in zip(
            tool_turn.tool_calls,
            raw_parts,
            strict=True,
        ):
            if (
                not isinstance(raw_part, dict)
                or set(raw_part) != {"provider_call_id", "encoding", "data"}
                or raw_part.get("provider_call_id") != model_call.provider_call_id
                or raw_part.get("encoding") != "base64"
            ):
                raise _AdapterFailure(
                    "MODEL_INVALID_REQUEST",
                    details={"reason": "provider_continuation_payload_invalid"},
                )
            encoded_signature = raw_part.get("data")
            if encoded_signature is None:
                continue
            if (
                not isinstance(encoded_signature, str)
                or not encoded_signature
                or len(encoded_signature) % 4 != 0
                or _BASE64_PATTERN.fullmatch(encoded_signature) is None
            ):
                raise _AdapterFailure(
                    "MODEL_INVALID_REQUEST",
                    details={"reason": "provider_continuation_payload_invalid"},
                )
            signature_by_provider_id[model_call.provider_call_id] = encoded_signature

    call_parts = []
    result_parts = []
    for model_call, model_result in zip(
        tool_turn.tool_calls,
        tool_turn.tool_results,
        strict=True,
    ):
        runtime_call = model_call.runtime_call
        call_part: dict[str, Any] = {
            "function_call": {
                "id": model_call.provider_call_id,
                "name": runtime_call.tool_id,
                "args": deepcopy(runtime_call.arguments),
            }
        }
        signature = signature_by_provider_id.get(model_call.provider_call_id)
        if signature is not None:
            call_part["thought_signature"] = signature
        call_parts.append(call_part)
        result_parts.append(
            {
                "function_response": {
                    "id": model_call.provider_call_id,
                    "name": runtime_call.tool_id,
                    "response": _bounded_json(
                        _function_response_payload(model_result.result),
                        "ModelTurnRequest.tool_result",
                        _MAX_PROVIDER_OBSERVATION_BYTES,
                    ),
                }
            }
        )
    return [
        {"role": "model", "parts": call_parts},
        {"role": "user", "parts": result_parts},
    ]


def _build_contents(
    request: ModelTurnRequest,
    initial_user_text: str,
) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = [
        {"role": "user", "parts": [{"text": initial_user_text}]}
    ]
    for tool_turn in request.tool_turns:
        contents.extend(_model_tool_turn_contents(tool_turn))
    if request.observation is None:
        return contents
    if request.previous_tool_call is None:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "observation_requires_previous_tool_call"},
        )
    previous = request.previous_tool_call
    function_call_part: dict[str, Any] = {
        "function_call": {
            "id": previous.call_id,
            "name": previous.tool_id,
            "args": deepcopy(previous.arguments),
        }
    }
    if request.provider_continuation is not None:
        continuation = request.provider_continuation
        if continuation.provider_id != "gemini" or not continuation.matches_tool_call(
            previous
        ):
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_identity_mismatch"},
            )
        continuation_payload = continuation.payload
        if set(continuation_payload) != {"format", "encoding", "data"} or (
            continuation_payload.get("format") != _GEMINI_CONTINUATION_FORMAT
            or continuation_payload.get("encoding") != "base64"
        ):
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_payload_invalid"},
            )
        encoded_signature = continuation_payload.get("data")
        if (
            not isinstance(encoded_signature, str)
            or not encoded_signature
            or len(encoded_signature) % 4 != 0
            or _BASE64_PATTERN.fullmatch(encoded_signature) is None
        ):
            raise _AdapterFailure(
                "MODEL_INVALID_REQUEST",
                details={"reason": "provider_continuation_payload_invalid"},
            )
        function_call_part["thought_signature"] = encoded_signature
    observation = _bounded_json(
        _function_response_payload(request.observation),
        "ModelTurnRequest.observation",
        _MAX_PROVIDER_OBSERVATION_BYTES,
    )
    contents.extend(
        [
            {
                "role": "model",
                "parts": [function_call_part],
            },
            {
                "role": "user",
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
    evidence_context = _model_visible_evidence_context(request)
    if evidence_context:
        bounded_context = _bounded_json(
            evidence_context,
            "ModelTurnRequest.evidence_context",
            _MAX_PROVIDER_INPUT_BYTES,
        )
        initial_user_input = (
            f"{request.user_input}\nverified_evidence_context="
            f"{json.dumps(bounded_context, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
        )
    else:
        initial_user_input = request.user_input
    bounded_input = _bounded_json(
        initial_user_input,
        "ModelTurnRequest.model_visible_input",
        _MAX_PROVIDER_INPUT_BYTES,
    )
    if not isinstance(bounded_input, str):
        raise _AdapterFailure("MODEL_INVALID_REQUEST", details={"reason": "input_not_text"})

    declarations = [
        map_tool_definition_to_gemini_function(tool)
        for tool in request.available_tools
    ]
    options = request.generation_options
    if options.require_tool_call and not declarations:
        raise _AdapterFailure(
            "MODEL_INVALID_REQUEST",
            details={"reason": "required_tool_call_without_tools"},
        )
    config: dict[str, Any] = {
        "tools": [{"function_declarations": declarations}] if declarations else [],
        "automatic_function_calling": {"disable": True},
    }
    if options.require_tool_call:
        config["tool_config"] = {
            "function_calling_config": {"mode": "ANY"}
        }
    if options.max_output_tokens is not None:
        config["max_output_tokens"] = options.max_output_tokens
    if options.temperature is not None:
        config["temperature"] = options.temperature
    if options.thinking_level is not None:
        config["thinking_config"] = {
            "thinking_level": options.thinking_level.upper()
        }

    payload = {
        "model": model_id,
        "contents": _build_contents(request, bounded_input),
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


def _extract_function_calls(response: Any) -> list[tuple[Any, Any | None]]:
    calls_with_parts = []
    for part in _extract_parts(response):
        function_call = _read(part, "function_call", None)
        if function_call is not None:
            calls_with_parts.append((function_call, part))
    if calls_with_parts:
        return calls_with_parts
    raw_calls = _read(response, "function_calls", None)
    if raw_calls is not None:
        if not isinstance(raw_calls, (list, tuple)):
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "function_calls_not_a_list"},
            )
        return [(raw_call, None) for raw_call in raw_calls]
    return []


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


def _generated_call_id(
    request: ModelTurnRequest,
    tool_name: str,
    arguments: dict[str, Any],
    ordinal: int,
) -> str:
    canonical_arguments = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(
        (
            f"{request.run_id}:{request.turn_id}:{request.sequence}:"
            f"{ordinal}:{tool_name}:{canonical_arguments}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"gemini-call-{digest}"


def _parse_function_call(
    raw_call: Any,
    request: ModelTurnRequest,
    *,
    proposal_id: str,
    ordinal: int,
) -> ModelToolCall:
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
    if provider_call_id is None:
        call_id = _generated_call_id(request, name, arguments, ordinal)
    elif not isinstance(provider_call_id, str) or not _ID_PATTERN.fullmatch(
        provider_call_id
    ):
        raise _AdapterFailure(
            "MODEL_TOOL_CALL_PARSE_FAILED",
            details={"reason": "invalid_function_call_id"},
        )
    else:
        call_id = provider_call_id
    try:
        return ModelToolCall.bind(
            provider_call_id=call_id,
            proposal_id=proposal_id,
            ordinal=ordinal,
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


def _parse_provider_continuation(
    raw_part: Any | None,
    request: ModelTurnRequest,
    call: ToolCall,
) -> ProviderContinuation | None:
    if raw_part is None:
        return None
    signature = _first_present(
        raw_part,
        ("thought_signature", "thoughtSignature"),
    )
    if signature is None:
        return None
    if not isinstance(signature, (bytes, bytearray)) or not signature:
        raise _AdapterFailure(
            "MODEL_RESPONSE_MALFORMED",
            details={"reason": "invalid_provider_continuation"},
        )
    return ProviderContinuation.for_tool_call(
        provider_id="gemini",
        source_turn_id=request.turn_id,
        call=call,
        payload={
            "format": _GEMINI_CONTINUATION_FORMAT,
            "encoding": "base64",
            "data": base64.b64encode(bytes(signature)).decode("ascii"),
        },
    )


def _parse_provider_turn_continuation(
    calls_with_parts: list[tuple[Any, Any | None]],
    request: ModelTurnRequest,
    proposal: ModelTurnProposal,
) -> ProviderTurnContinuation | None:
    parts_payload: list[dict[str, Any]] = []
    has_signature = False
    for model_call, (_, raw_part) in zip(
        proposal.tool_calls,
        calls_with_parts,
        strict=True,
    ):
        signature = (
            None
            if raw_part is None
            else _first_present(
                raw_part,
                ("thought_signature", "thoughtSignature"),
            )
        )
        encoded_signature = None
        if signature is not None:
            if not isinstance(signature, (bytes, bytearray)) or not signature:
                raise _AdapterFailure(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "invalid_provider_continuation"},
                )
            encoded_signature = base64.b64encode(bytes(signature)).decode("ascii")
            has_signature = True
        parts_payload.append(
            {
                "provider_call_id": model_call.provider_call_id,
                "encoding": "base64",
                "data": encoded_signature,
            }
        )
    if not has_signature:
        return None
    return ProviderTurnContinuation.for_calls(
        provider_id="gemini",
        source_turn_id=request.turn_id,
        source_sequence=request.sequence,
        proposal_id=proposal.proposal_id,
        calls=proposal.tool_calls,
        payload={
            "format": _GEMINI_TURN_CONTINUATION_FORMAT,
            "parts": parts_payload,
        },
    )


def _parse_provider_error(
    response: Any,
    request: ModelTurnRequest,
    model_id: str,
    *,
    client: Any,
    payload: dict[str, Any],
    elapsed_ms: float,
) -> ModelProviderError | None:
    raw_error = _read(response, "error", None)
    if raw_error is None:
        return None
    status_code = _first_present(raw_error, ("status_code", "http_status"))
    if status_code is None:
        status_code = _read(response, "status_code", None)
    diagnostics = gemini_response_diagnostics(
        response,
        high_level_outcome="MODEL_UNKNOWN_FAILURE",
        model=model_id,
        client=client,
        payload=payload,
        elapsed_ms=elapsed_ms,
        provider_error=raw_error,
        finish_reason=_extract_finish_reason(response),
    )
    code, outcome = _code_for_provider_diagnostics(diagnostics)
    retry_classification = classify_retryable_failure(None, diagnostics)
    retryable = retry_classification is not None
    if retryable:
        outcome = "known_failure"
    diagnostics = _annotate_generation_failure(
        code,
        diagnostics,
        request,
        retry_classification=retry_classification,
        response_accepted=True,
    )
    raw_request_id = _first_present(response, ("request_id", "provider_request_id"))
    if raw_request_id is None:
        raw_request_id = diagnostics.get("request_id")
    try:
        request_id = _safe_provider_id(
            raw_request_id,
            "GeminiResponse.provider_request_id",
        )
    except ValidationError:
        request_id = None
    metadata = {"provider": "gemini", "model": model_id}
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        metadata["http_status"] = status_code
    return _make_error(
        code,
        outcome=outcome,
        provider_request_id=request_id,
        provider_metadata=metadata,
        details={"reason": "provider_error_response", **diagnostics},
        retryable=retryable,
    )


def _code_for_status(status_code: Any, default: str) -> tuple[str, str]:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        return default, "known_failure"
    if status_code in {401, 403}:
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if status_code == 402:
        return "MODEL_BILLING_BLOCKED", "known_failure"
    if status_code in {400, 422}:
        return "MODEL_INVALID_REQUEST", "known_failure"
    if status_code == 404:
        return "MODEL_UNAVAILABLE", "known_failure"
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
        return _code_for_status(status_code, "MODEL_UNKNOWN_FAILURE")
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
    return "MODEL_UNKNOWN_FAILURE", "unknown_provider_outcome"


def _generation_failure_category(
    code: str,
    diagnostics: dict[str, Any],
) -> str:
    status = diagnostics.get("http_status")
    provider_code = diagnostics.get("provider_error_code")
    normalized_provider_code = (
        provider_code.strip().upper()
        if isinstance(provider_code, str)
        else None
    )
    low_level = diagnostics.get("low_level_failure_class")
    exception_type = diagnostics.get("exception_type")
    exception_name = exception_type.casefold() if isinstance(exception_type, str) else ""

    if (
        status == 404
        or normalized_provider_code in {"NOT_FOUND", "MODEL_NOT_FOUND"}
        or "notfound" in exception_name
    ):
        return "MODEL_NOT_FOUND"
    if code == "MODEL_AUTH_REQUIRED" or status in {401, 403} or normalized_provider_code in {
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
    }:
        return "AUTHENTICATION_ERROR"
    if code == "MODEL_BILLING_BLOCKED" or status == 402:
        return "BILLING_BLOCKED"
    if code == "MODEL_INVALID_REQUEST" or status in {400, 422} or normalized_provider_code in {
        "INVALID_ARGUMENT",
        "FAILED_PRECONDITION",
    }:
        return "INVALID_REQUEST"
    if code == "MODEL_RATE_LIMITED" or status == 429 or normalized_provider_code in {
        "RESOURCE_EXHAUSTED",
        "RATE_LIMIT_EXCEEDED",
    }:
        return "RATE_LIMITED"
    if code == "MODEL_TIMEOUT" or status in {408, 504} or low_level == "TRANSPORT_TIMEOUT":
        return "TIMEOUT"
    if (
        (isinstance(status, int) and 500 <= status <= 599)
        or low_level in {"HTTP_5XX", "PROVIDER_OVERLOADED", "PROVIDER_ABORTED"}
        or normalized_provider_code in {"UNAVAILABLE", "OVERLOADED", "INTERNAL", "ABORTED"}
    ):
        return "PROVIDER_SERVER_ERROR"
    if isinstance(low_level, str) and low_level.startswith("TRANSPORT_"):
        return "TRANSPORT_ERROR"
    return "UNKNOWN_PROVIDER_ERROR"


def _annotate_generation_failure(
    code: str,
    diagnostics: dict[str, Any],
    request: ModelTurnRequest,
    *,
    retry_classification: str | None,
    response_accepted: bool,
    failure_stage: str = "GENERATION",
    failure_category: str | None = None,
) -> dict[str, Any]:
    return {
        **diagnostics,
        "failure_stage": failure_stage,
        "failure_category": failure_category or _generation_failure_category(code, diagnostics),
        "retry_classification": retry_classification,
        "retryable": retry_classification is not None,
        "attempt_number": request.sequence,
        "request_fingerprint": _request_fingerprint(request),
        "response_accepted": response_accepted,
        "api_endpoint_hostname": diagnostics.get("target_hostname"),
        "api_endpoint_port": diagnostics.get("target_port"),
    }


def _code_for_provider_diagnostics(diagnostics: dict[str, Any]) -> tuple[str, str]:
    status = diagnostics.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool):
        return _code_for_status(status, "MODEL_UNKNOWN_FAILURE")
    provider_code = diagnostics.get("provider_error_code")
    provider_code = provider_code.strip().upper() if isinstance(provider_code, str) else None
    if provider_code in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return "MODEL_AUTH_REQUIRED", "known_failure"
    if provider_code in {"INVALID_ARGUMENT", "FAILED_PRECONDITION"}:
        return "MODEL_INVALID_REQUEST", "known_failure"
    if provider_code in {"RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED"}:
        return "MODEL_RATE_LIMITED", "known_failure"
    if provider_code == "DEADLINE_EXCEEDED":
        return "MODEL_TIMEOUT", "known_failure"
    if provider_code in {
        "UNAVAILABLE",
        "OVERLOADED",
        "INTERNAL",
        "ABORTED",
        "NOT_FOUND",
        "MODEL_NOT_FOUND",
    }:
        return "MODEL_UNAVAILABLE", "known_failure"
    return "MODEL_UNKNOWN_FAILURE", "unknown_provider_outcome"


def _make_error(
    code: str,
    *,
    outcome: str | None = None,
    provider_request_id: str | None = None,
    provider_metadata: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> ModelProviderError:
    if code not in MODEL_PROVIDER_ERROR_SPECS:
        code = "MODEL_UNKNOWN_FAILURE"
    category, default_retryable = MODEL_PROVIDER_ERROR_SPECS[code]
    return ModelProviderError(
        code=code,
        category=category,
        message=_ERROR_MESSAGES[code],
        retryable=default_retryable if retryable is None else retryable,
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
            supports_multiple_tool_calls=True,
            supports_streaming=False,
            provider_executes_tools=False,
        )

    def complete(self, request: ModelTurnRequest) -> ModelResponse:
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

        provider_started = time.perf_counter()
        try:
            raw_response = self.client.generate_content(
                model=payload["model"],
                contents=payload["contents"],
                config=payload["config"],
            )
        except Exception as exception:
            code, outcome = _exception_code(exception)
            elapsed_ms = (time.perf_counter() - provider_started) * 1000
            diagnostics = gemini_exception_diagnostics(
                exception,
                high_level_outcome=code,
                model=payload["model"],
                client=self.client,
                payload=payload,
                elapsed_ms=elapsed_ms,
            )
            retry_classification = classify_retryable_failure(None, diagnostics)
            retryable = retry_classification is not None
            if retryable:
                # A narrowly classified transport/status failure is eligible for
                # the caller's bounded retry policy. Unknown SDK failures remain
                # terminal even when their message sounds transient.
                outcome = "known_failure"
                if code == "MODEL_UNKNOWN_FAILURE":
                    code = (
                        "MODEL_TIMEOUT"
                        if retry_classification == "TRANSPORT_TIMEOUT"
                        else "MODEL_TRANSIENT_FAILURE"
                    )
                    diagnostics["high_level_outcome"] = code
            diagnostics = _annotate_generation_failure(
                code,
                diagnostics,
                request,
                retry_classification=retry_classification,
                response_accepted=False,
            )
            raw_request_id = _first_present(exception, ("request_id", "provider_request_id"))
            if raw_request_id is None:
                raw_request_id = diagnostics.get("request_id")
            try:
                request_id = _safe_provider_id(
                    raw_request_id,
                    "GeminiProviderError.provider_request_id",
                )
            except ValidationError:
                request_id = None
            metadata = {"provider": "gemini", "model": payload["model"]}
            status_code = diagnostics.get("http_status")
            if isinstance(status_code, int) and not isinstance(status_code, bool):
                metadata["http_status"] = status_code
            return ModelResponse(
                error=_make_error(
                    code,
                    outcome=outcome,
                    provider_request_id=request_id,
                    provider_metadata=metadata,
                    details={"reason": "provider_exception", **diagnostics},
                    retryable=retryable,
                )
            )

        duration_ms = round((time.perf_counter() - provider_started) * 1000, 3)
        try:
            return self._parse_response(
                raw_response,
                request,
                payload["model"],
                duration_ms,
                payload=payload,
            )
        except _AdapterFailure as failure:
            diagnostics = gemini_response_diagnostics(
                raw_response,
                high_level_outcome=failure.code,
                model=payload["model"],
                client=self.client,
                payload=payload,
                elapsed_ms=duration_ms,
                finish_reason=_extract_finish_reason(raw_response),
                parse_error=True,
            )
            failure_details = _annotate_generation_failure(
                failure.code,
                {**failure.details, **diagnostics},
                request,
                retry_classification=None,
                response_accepted=True,
                failure_stage="RESPONSE_VALIDATION",
                failure_category="RESPONSE_VALIDATION_ERROR",
            )
            return self._failure_response(
                _AdapterFailure(
                    failure.code,
                    outcome=failure.outcome,
                    provider_request_id=failure.provider_request_id,
                    provider_metadata=failure.provider_metadata,
                    details=failure_details,
                )
            )
        except (ValidationError, TypeError, ValueError) as exception:
            diagnostics = gemini_response_diagnostics(
                raw_response,
                high_level_outcome="MODEL_RESPONSE_MALFORMED",
                model=payload["model"],
                client=self.client,
                payload=payload,
                elapsed_ms=duration_ms,
                finish_reason=_extract_finish_reason(raw_response),
                parse_error=True,
                exception=exception,
            )
            failure_details = _annotate_generation_failure(
                "MODEL_RESPONSE_MALFORMED",
                diagnostics,
                request,
                retry_classification=None,
                response_accepted=True,
                failure_stage="RESPONSE_VALIDATION",
                failure_category="RESPONSE_VALIDATION_ERROR",
            )
            return ModelResponse(
                error=_make_error(
                    "MODEL_RESPONSE_MALFORMED",
                    details={"reason": "response_normalization_failed", **failure_details},
                    retryable=False,
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
                retryable=(
                    failure.details.get("retryable")
                    if isinstance(failure.details.get("retryable"), bool)
                    else None
                ),
            )
        )

    def _parse_response(
        self,
        response: Any,
        request: ModelTurnRequest,
        model_id: str,
        duration_ms: float,
        *,
        payload: dict[str, Any],
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

        provider_error = _parse_provider_error(
            response,
            request,
            model_id,
            client=self.client,
            payload=payload,
            elapsed_ms=duration_ms,
        )
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
        if len(calls) > 16:
            raise _AdapterFailure(
                "MODEL_RESPONSE_UNSUPPORTED",
                details={"reason": "too_many_tool_calls"},
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
            proposal_id = f"{request.turn_id}:proposal"
            parsed_calls = [
                _parse_function_call(
                    raw_call,
                    request,
                    proposal_id=proposal_id,
                    ordinal=ordinal,
                )
                for ordinal, (raw_call, _) in enumerate(calls)
            ]
            provider_call_ids = [call.provider_call_id for call in parsed_calls]
            if len(set(provider_call_ids)) != len(provider_call_ids):
                raise _AdapterFailure(
                    "MODEL_TOOL_CALL_PARSE_FAILED",
                    details={"reason": "duplicate_function_call_id"},
                )
            proposal = ModelTurnProposal.tools(proposal_id, parsed_calls)
            continuation = _parse_provider_turn_continuation(
                calls,
                request,
                proposal,
            )
            if finish_reason not in {None, "stop"}:
                raise _AdapterFailure(
                    "MODEL_RESPONSE_MALFORMED",
                    details={
                        "reason": "tool_call_finish_reason_mismatch",
                        "finish_reason": finish_reason,
                    },
                )
            return ModelResponse(
                proposal=proposal,
                usage=usage,
                provider_request_id=request_id,
                provider_response_id=response_id,
                finish_reason=finish_reason or "stop",
                provider_metadata=metadata,
                provider_turn_continuation=continuation,
            )

        if not isinstance(text, str) or not text.strip():
            raise _AdapterFailure(
                "MODEL_RESPONSE_MALFORMED",
                details={"reason": "empty_action"},
            )
        return ModelResponse(
            proposal=ModelTurnProposal.final(
                f"{request.turn_id}:proposal",
                text,
            ),
            usage=usage,
            provider_request_id=request_id,
            provider_response_id=response_id,
            finish_reason=finish_reason or "stop",
            provider_metadata=metadata,
        )
__all__ = [
    "GeminiClient",
    "GeminiProviderAdapter",
    "build_gemini_sdk_client",
    "build_gemini_request",
    "map_tool_definition_to_gemini_function",
]
