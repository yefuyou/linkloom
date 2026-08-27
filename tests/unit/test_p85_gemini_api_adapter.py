"""P8.5 WP-2 offline Gemini provider-adapter contract tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import (
    ModelAction,
    ModelGenerationOptions,
    ModelProviderError,
    ModelResponse,
    ModelTurnRequest,
    ModelUsage,
)
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


@dataclass
class FakeGeminiClient:
    response: object | None = None
    exception: BaseException | None = None

    def __post_init__(self) -> None:
        self.requests: list[dict] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict],
        config: dict,
    ) -> object:
        self.requests.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        if self.exception is not None:
            raise self.exception
        return self.response


class FakeGeminiError(Exception):
    def __init__(self, message: str = "provider failure", *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SdkSurfaceGeminiClient:
    """Fake for the official SDK's keyword-based generate_content seam."""

    response: object | None = None
    exception: BaseException | None = None

    def __post_init__(self) -> None:
        self.calls: list[dict] = []

    def generate_content(self, *, model: str, contents: list[dict], config: dict) -> object:
        self.calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )
        if self.exception is not None:
            raise self.exception
        return self.response


class OfficialApiErrorShape(Exception):
    """Minimal offline stand-in for google.genai.errors.APIError.code."""

    def __init__(self, code: int):
        super().__init__("provider detail must not be exposed")
        self.code = code


class CodeErrorGeminiClient:
    def __init__(self, code: int):
        self.code = code
        self.calls: list[dict] = []

    def generate_content(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        raise OfficialApiErrorShape(self.code)


def _definition(*, schema: dict | None = None) -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema=schema
        or {
            "type": "object",
            "required": ["query", "limit"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _call(call_id: str = "call_p85_1") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments={"query": "durability", "limit": 3},
        run_id="run_p85_1",
        task_id="task_p85_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _observation(call: ToolCall | None = None) -> ToolResult:
    call = call or _call()
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value={"matches": [{"evidence_id": "ev_p85_1"}]},
        business_status="FOUND",
    )


def _request(**changes) -> ModelTurnRequest:
    values = {
        "run_id": "run_p85_1",
        "turn_id": "run_p85_1:turn:1",
        "task_id": "task_p85_1",
        "agent_id": "retrieval_agent",
        "sequence": 1,
        "user_input": "find durable runtime evidence",
        "observation": None,
        "available_tools": [_definition()],
        "model_id": "gemini-test-model",
        "generation_options": ModelGenerationOptions(
            max_output_tokens=128,
            temperature=0.2,
        ),
    }
    values.update(changes)
    return ModelTurnRequest(**values)


def test_final_text_response_maps_to_normalized_model_response_without_sdk_objects():
    client = FakeGeminiClient(
        response={
            "text": "The answer is grounded in retrieved evidence.",
            "request_id": "gemini-request-1",
            "response_id": "gemini-response-1",
            "finish_reason": "STOP",
            "usage_metadata": {
                "prompt_token_count": 17,
                "candidates_token_count": 9,
                "total_token_count": 26,
            },
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert isinstance(response, ModelResponse)
    assert response.action == ModelAction.final(
        "The answer is grounded in retrieved evidence."
    )
    assert response.error is None
    assert response.usage == ModelUsage(
        input_tokens=17,
        output_tokens=9,
        total_tokens=26,
        duration_ms=response.usage.duration_ms,
    )
    assert response.provider_request_id == "gemini-request-1"
    assert response.provider_response_id == "gemini-response-1"
    assert response.finish_reason == "stop"
    assert json.dumps(response.to_dict(), ensure_ascii=False)


def test_single_function_call_maps_to_runtime_tool_call_and_is_not_executed():
    client = FakeGeminiClient(
        response={
            "function_calls": [
                {
                    "id": "gemini-call-1",
                    "name": "search_notes",
                    "args": {"query": "durability", "limit": 3},
                }
            ],
            "finish_reason": "STOP",
            "response_id": "gemini-response-tool-1",
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is not None
    assert response.action.kind == "tool_call"
    assert response.action.tool_call == ToolCall(
        call_id="gemini-call-1",
        tool_id="search_notes",
        arguments={"query": "durability", "limit": 3},
        run_id="run_p85_1",
        task_id="task_p85_1",
        agent_id="retrieval_agent",
        sequence=1,
    )
    assert response.finish_reason == "stop"
    assert client.requests
    assert not hasattr(client, "executed_tools")


def test_sdk_like_function_call_response_is_parsed_without_importing_sdk_types():
    class SdkLikeResponse:
        function_calls = [
            SimpleNamespace(
                id="sdk-call-1",
                name="search_notes",
                args={"query": "durability", "limit": 3},
            )
        ]
        response_id = "sdk-response-1"
        usage_metadata = SimpleNamespace(
            prompt_token_count=3,
            candidates_token_count=4,
            total_token_count=7,
        )

        @property
        def text(self):
            raise ValueError("function-call response has no text")

    client = FakeGeminiClient(response=SdkLikeResponse())

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is not None
    assert response.action.tool_call is not None
    assert response.action.tool_call.call_id == "sdk-call-1"
    assert response.usage.input_tokens == 3
    assert response.provider_response_id == "sdk-response-1"


def test_observation_request_maps_previous_call_and_result_without_full_runtime_state():
    call = _call()
    client = FakeGeminiClient(response={"text": "The result is sufficient."})

    GeminiProviderAdapter(client).complete(
        _request(observation=_observation(call), previous_tool_call=call)
    )

    request = client.requests[0]
    assert request["contents"][0] == {
        "role": "user",
        "parts": [{"text": "find durable runtime evidence"}],
    }
    assert request["contents"][1]["parts"][0]["function_call"] == {
        "id": "call_p85_1",
        "name": "search_notes",
        "args": {"query": "durability", "limit": 3},
    }
    assert request["contents"][2]["parts"][0]["function_response"] == {
        "id": "call_p85_1",
        "name": "search_notes",
        "response": {
            "status": "ok",
            "value": {"matches": [{"evidence_id": "ev_p85_1"}]},
            "business_status": "FOUND",
        },
    }
    assert "runtime_state" not in request
    assert "tool_ledger" not in request


def test_observation_without_previous_call_fails_before_provider_invocation():
    client = FakeGeminiClient(response={"text": "must not be used"})

    response = GeminiProviderAdapter(client).complete(_request(observation=_observation()))

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_INVALID_REQUEST"
    assert client.requests == []


@pytest.mark.parametrize(
    ("exception", "code", "outcome"),
    [
        (FakeGeminiError(status_code=401), "MODEL_AUTH_REQUIRED", "known_failure"),
        (FakeGeminiError(status_code=400), "MODEL_INVALID_REQUEST", "known_failure"),
        (FakeGeminiError(status_code=429), "MODEL_RATE_LIMITED", "known_failure"),
        (TimeoutError("provider call timed out"), "MODEL_TIMEOUT", "unknown_provider_outcome"),
        (FakeGeminiError(status_code=503), "MODEL_UNAVAILABLE", "known_failure"),
    ],
)
def test_provider_exceptions_are_normalized_without_adapter_retry(
    exception, code, outcome
):
    client = FakeGeminiClient(exception=exception)

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.outcome == outcome
    assert response.error.message != str(exception)
    assert len(client.requests) == 1


def test_provider_error_response_is_normalized_without_raw_provider_payload():
    client = FakeGeminiClient(
        response={
            "error": {
                "status_code": 429,
                "message": "api_key=should-not-be-persisted",
            }
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RATE_LIMITED"
    assert "api_key" not in json.dumps(response.to_dict())


def test_malformed_provider_response_does_not_become_an_empty_success():
    client = FakeGeminiClient(response={"candidates": []})

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_MALFORMED"


def test_malformed_tool_call_arguments_are_normalized_as_parse_failure():
    client = FakeGeminiClient(
        response={
            "function_calls": [
                {"id": "gemini-call-invalid", "name": "search_notes", "args": []}
            ]
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_TOOL_CALL_PARSE_FAILED"


def test_multiple_function_calls_fail_closed_instead_of_inventing_multi_action():
    client = FakeGeminiClient(
        response={
            "function_calls": [
                {"id": "gemini-call-1", "name": "search_notes", "args": {}},
                {"id": "gemini-call-2", "name": "search_notes", "args": {}},
            ]
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "multiple_tool_calls"


def test_invalid_finish_reason_is_rejected():
    client = FakeGeminiClient(response={"text": "answer", "finish_reason": "UNKNOWN"})

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_MALFORMED"


def test_missing_model_id_is_a_safe_request_error_without_provider_call():
    client = FakeGeminiClient(response={"text": "must not be used"})

    response = GeminiProviderAdapter(client).complete(
        _request(model_id=None)
    )

    assert response.error is not None
    assert response.error.code == "MODEL_INVALID_REQUEST"
    assert client.requests == []


def test_adapter_configuration_can_supply_model_id_for_legacy_request():
    client = FakeGeminiClient(response={"text": "configured model answer"})

    response = GeminiProviderAdapter(
        client,
        model_id="configured-gemini-model",
    ).complete(_request(model_id=None))

    assert response.action == ModelAction.final("configured model answer")
    assert client.requests[0]["model"] == "configured-gemini-model"


def test_unsafe_provider_exception_request_id_is_dropped_not_raised_or_persisted():
    exception = FakeGeminiError(status_code=401)
    exception.request_id = "api_key=raw-secret"
    client = FakeGeminiClient(exception=exception)

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.error is not None
    assert response.error.code == "MODEL_AUTH_REQUIRED"
    assert response.error.provider_request_id is None
    assert "raw-secret" not in json.dumps(response.to_dict())


def test_provider_capability_disables_provider_side_tool_execution():
    adapter = GeminiProviderAdapter(FakeGeminiClient(response={"text": "answer"}))

    assert adapter.capability.provider_executes_tools is False
    assert adapter.capability.supports_multiple_tool_calls is False


def test_unsupported_schema_fails_before_provider_invocation():
    client = FakeGeminiClient(response={"text": "must not be used"})
    unsupported = _definition(
        schema={
            "type": "object",
            "properties": {},
            "oneOf": [{"type": "string"}, {"type": "number"}],
        }
    )

    response = GeminiProviderAdapter(client).complete(
        _request(available_tools=[unsupported])
    )

    assert response.error is not None
    assert response.error.code == "MODEL_TOOL_SCHEMA_UNSUPPORTED"
    assert client.requests == []


def test_client_request_is_json_safe_and_does_not_include_credentials_or_raw_state():
    client = FakeGeminiClient(response={"text": "answer"})

    GeminiProviderAdapter(client).complete(_request())

    request = client.requests[0]
    encoded = json.dumps(request, ensure_ascii=False)
    assert "api_key" not in encoded
    assert "authorization" not in encoded
    assert "checkpoint" not in encoded
    assert "runtime_state" not in encoded


def test_client_seam_matches_sdk_generate_content_and_does_not_send_root_store_field():
    client = SdkSurfaceGeminiClient(response={"text": "sdk answer"})

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action == ModelAction.final("sdk answer")
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gemini-test-model"
    assert isinstance(call["contents"], list)
    assert isinstance(call["config"], dict)
    assert "store" not in call


def test_single_function_call_with_stop_maps_to_one_tool_action():
    client = SdkSurfaceGeminiClient(
        response={
            "function_calls": [
                {
                    "id": "gemini-call-stop-1",
                    "name": "search_notes",
                    "args": {"query": "durability", "limit": 3},
                }
            ],
            "finish_reason": "STOP",
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is not None
    assert response.action.kind == "tool_call"
    assert response.action.tool_call is not None
    assert response.action.tool_call.tool_id == "search_notes"
    assert response.finish_reason == "stop"


def test_undeclared_function_call_fails_closed_before_model_action_creation():
    client = SdkSurfaceGeminiClient(
        response={
            "function_calls": [
                {
                    "id": "gemini-call-undeclared-1",
                    "name": "read_verified_note",
                    "args": {"path": "notes/runtime.md"},
                }
            ],
            "finish_reason": "STOP",
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "undeclared_tool"


@pytest.mark.parametrize(
    ("provider_code", "normalized_code"),
    [
        (401, "MODEL_AUTH_REQUIRED"),
        (400, "MODEL_INVALID_REQUEST"),
        (429, "MODEL_RATE_LIMITED"),
        (503, "MODEL_UNAVAILABLE"),
    ],
)
def test_official_api_error_code_is_normalized_without_retry(
    provider_code, normalized_code
):
    client = CodeErrorGeminiClient(provider_code)

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == normalized_code
    assert len(client.calls) == 1
