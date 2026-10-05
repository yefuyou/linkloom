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
    ModelToolResult,
    ModelToolTurn,
    ModelTurnRequest,
    ModelUsage,
)
from linkloom.agents.providers.gemini_api import (
    GeminiProviderAdapter,
    build_gemini_request,
)
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


def test_multiple_function_calls_preserve_order_ids_and_turn_signatures():
    client = FakeGeminiClient(
        response={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "id": "gemini-call-1",
                                    "name": "search_notes",
                                    "args": {"query": "first", "limit": 1},
                                },
                                "thought_signature": b"signature-one",
                            },
                            {
                                "function_call": {
                                    "id": "gemini-call-2",
                                    "name": "search_notes",
                                    "args": {"query": "second", "limit": 1},
                                },
                                "thought_signature": b"signature-two",
                            },
                        ]
                    },
                    "finish_reason": "STOP",
                }
            ]
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.error is None
    assert response.proposal is not None
    assert [call.provider_call_id for call in response.proposal.tool_calls] == [
        "gemini-call-1",
        "gemini-call-2",
    ]
    assert response.provider_turn_continuation is not None
    assert response.provider_turn_continuation.matches_calls(
        response.proposal.proposal_id,
        response.proposal.tool_calls,
    )


def test_missing_gemini_ids_are_deterministic_and_ordinal_unique():
    provider_response = {
        "function_calls": [
            {
                "name": "search_notes",
                "args": {"query": "same", "limit": 1},
            },
            {
                "name": "search_notes",
                "args": {"query": "same", "limit": 1},
            },
        ]
    }

    first = GeminiProviderAdapter(
        FakeGeminiClient(response=provider_response)
    ).complete(_request())
    second = GeminiProviderAdapter(
        FakeGeminiClient(response=provider_response)
    ).complete(_request())

    first_ids = [call.provider_call_id for call in first.proposal.tool_calls]
    second_ids = [call.provider_call_id for call in second.proposal.tool_calls]
    assert first_ids == second_ids
    assert len(set(first_ids)) == 2


def test_present_but_invalid_gemini_id_rejects_instead_of_being_replaced():
    response = GeminiProviderAdapter(
        FakeGeminiClient(
            response={
                "function_calls": [
                    {
                        "id": "invalid id with spaces",
                        "name": "search_notes",
                        "args": {"query": "durability", "limit": 1},
                    }
                ]
            }
        )
    ).complete(_request())

    assert response.proposal is None
    assert response.error is not None
    assert response.error.details["reason"] == "invalid_function_call_id"


@pytest.mark.parametrize(
    ("calls", "reason"),
    [
        (
            [
                {
                    "id": "gemini-call-duplicate",
                    "name": "search_notes",
                    "args": {"query": "first", "limit": 1},
                },
                {
                    "id": "gemini-call-duplicate",
                    "name": "search_notes",
                    "args": {"query": "second", "limit": 1},
                },
            ],
            "duplicate_function_call_id",
        ),
        (
            [
                {
                    "id": "gemini-call-valid",
                    "name": "search_notes",
                    "args": {"query": "first", "limit": 1},
                },
                {
                    "id": "gemini-call-malformed",
                    "name": "search_notes",
                    "args": "not-an-object",
                },
            ],
            "function_arguments_not_an_object",
        ),
    ],
)
def test_duplicate_or_malformed_middle_gemini_call_rejects_whole_response(
    calls,
    reason,
):
    response = GeminiProviderAdapter(
        FakeGeminiClient(response={"function_calls": calls})
    ).complete(_request())

    assert response.proposal is None
    assert response.error is not None
    assert response.error.details["reason"] == reason


def test_grouped_gemini_turn_preserves_call_parts_signatures_and_results():
    response = GeminiProviderAdapter(
        FakeGeminiClient(
            response={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "function_call": {
                                        "id": "gemini-call-1",
                                        "name": "search_notes",
                                        "args": {"query": "first", "limit": 1},
                                    },
                                    "thought_signature": b"signature-one",
                                },
                                {
                                    "function_call": {
                                        "id": "gemini-call-2",
                                        "name": "search_notes",
                                        "args": {"query": "second", "limit": 1},
                                    },
                                    "thought_signature": b"signature-two",
                                },
                            ]
                        }
                    }
                ]
            }
        )
    ).complete(_request())
    proposal = response.proposal
    model_results = [
        ModelToolResult.for_call(
            run_id="run_p85_1",
            proposal_id=proposal.proposal_id,
            ordinal=ordinal,
            model_call=model_call,
            result=ToolResult(
                call_id=model_call.runtime_call.call_id,
                tool_id=model_call.runtime_call.tool_id,
                status="ok",
                value={"ordinal": ordinal},
            ),
        )
        for ordinal, model_call in enumerate(proposal.tool_calls)
    ]
    tool_turn = ModelToolTurn(
        proposal_id=proposal.proposal_id,
        source_turn_id="run_p85_1:turn:1",
        source_sequence=1,
        tool_calls=proposal.tool_calls,
        tool_results=model_results,
        provider_continuation=response.provider_turn_continuation,
    )

    payload = build_gemini_request(
        _request(
            turn_id="run_p85_1:turn:2",
            sequence=2,
            tool_turns=[tool_turn],
        )
    )

    contents = payload["contents"]
    assert [content["role"] for content in contents] == [
        "user",
        "model",
        "user",
    ]
    assert [
        part["function_call"]["id"] for part in contents[1]["parts"]
    ] == ["gemini-call-1", "gemini-call-2"]
    assert all("thought_signature" in part for part in contents[1]["parts"])
    assert [
        part["function_response"]["id"] for part in contents[2]["parts"]
    ] == ["gemini-call-1", "gemini-call-2"]


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


def test_multiple_function_calls_use_the_runtime_v2_proposal_contract():
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
    assert response.error is None
    assert response.proposal is not None
    assert [call.provider_call_id for call in response.proposal.tool_calls] == [
        "gemini-call-1",
        "gemini-call-2",
    ]


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
    assert adapter.capability.supports_multiple_tool_calls is True


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


def test_non_stop_function_call_error_preserves_safe_finish_reason_diagnostic():
    client = SdkSurfaceGeminiClient(
        response={
            "function_calls": [
                {
                    "id": "gemini-call-max-tokens-1",
                    "name": "search_notes",
                    "args": {"query": "durability", "limit": 3},
                }
            ],
            "finish_reason": "MAX_TOKENS",
        }
    )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_MALFORMED"
    assert response.error.details["reason"] == "tool_call_finish_reason_mismatch"
    assert response.error.details["finish_reason"] == "MAX_TOKENS"
    assert response.error.details["low_level_failure_class"] == "PROVIDER_INCOMPLETE"


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


def test_gemini_exception_diagnostics_preserve_nested_failure_and_request_shape():
    class TransportError(Exception):
        pass

    cause = ConnectionRefusedError(10061, "connection refused")
    exception = TransportError("SDK transport failed")
    exception.__cause__ = cause
    client = FakeGeminiClient(exception=exception)

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.error is not None
    diagnostics = response.error.details
    assert diagnostics["provider"] == "gemini"
    assert diagnostics["model"] == "gemini-test-model"
    assert diagnostics["high_level_outcome"] == "MODEL_TRANSIENT_FAILURE"
    assert diagnostics["low_level_failure_class"] == "TRANSPORT_CONNECT"
    assert {
        "failure_layer",
        "exception_type",
        "exception_message_safe",
        "exception_repr_safe",
        "nested_cause_chain",
        "http_status",
        "provider_error_code",
        "provider_error_message_safe",
        "request_id",
        "response_status",
        "finish_reason",
        "timeout_type",
        "target_hostname",
        "target_port",
        "configured_proxy_hostname",
        "configured_proxy_port",
        "connection_phase",
        "elapsed_ms",
        "request_shape_summary",
    } <= diagnostics.keys()
    assert diagnostics["exception_type"] == "TransportError"
    assert diagnostics["nested_cause_chain"][0]["exception_type"] == "ConnectionRefusedError"
    assert (
        diagnostics["nested_cause_chain"][0]["winerror"] == 10061
        or diagnostics["nested_cause_chain"][0]["errno"] == 10061
    )
    assert diagnostics["request_shape_summary"]["tool_declaration_count"] == 1
    assert "PRIVATE" not in json.dumps(diagnostics)
    assert diagnostics["elapsed_ms"] >= 0
    assert response.error.to_dict()["details"] == diagnostics
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("exception", "low_level", "phase"),
    [
        pytest.param(
            __import__("socket").gaierror(__import__("socket").EAI_NONAME, "DNS resolution failed"),
            "TRANSPORT_DNS",
            "DNS",
            id="dns",
        ),
        pytest.param(
            ConnectionRefusedError(10061, "connection refused"),
            "TRANSPORT_CONNECT",
            "TCP_CONNECT",
            id="tcp-connect",
        ),
        pytest.param(
            __import__("httpx").ConnectTimeout("connect timed out"),
            "TRANSPORT_TIMEOUT",
            "TCP_CONNECT",
            id="connect-timeout",
        ),
        pytest.param(
            __import__("httpx").ReadTimeout("read timed out"),
            "TRANSPORT_TIMEOUT",
            "RESPONSE_READ",
            id="read-timeout",
        ),
        pytest.param(
            __import__("ssl").SSLError("certificate verify failed"),
            "TRANSPORT_TLS",
            "TLS",
            id="tls",
        ),
        pytest.param(
            __import__("httpx").ProxyError("proxy connection failed"),
            "TRANSPORT_PROXY_CONNECT",
            "PROXY_CONNECT",
            id="proxy-connect",
        ),
    ],
)
def test_gemini_transport_exceptions_get_narrow_safe_classification(
    exception, low_level, phase
):
    response = GeminiProviderAdapter(FakeGeminiClient(exception=exception)).complete(
        _request()
    )

    assert response.error is not None
    assert response.error.code in {"MODEL_TRANSIENT_FAILURE", "MODEL_TIMEOUT"}
    assert response.error.details["low_level_failure_class"] == low_level
    assert response.error.details["connection_phase"] == phase


@pytest.mark.parametrize(
    ("status", "expected_class"),
    [
        (400, "HTTP_400"),
        (401, "HTTP_401"),
        (403, "HTTP_403"),
        (404, "HTTP_404"),
        (429, "HTTP_429"),
        (503, "HTTP_5XX"),
    ],
)
def test_gemini_http_failures_preserve_exact_status_class(status, expected_class):
    response = GeminiProviderAdapter(
        FakeGeminiClient(exception=FakeGeminiError(status_code=status))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["http_status"] == status
    assert response.error.details["low_level_failure_class"] == expected_class
    assert response.error.details["failure_layer"] == "HTTP"


def test_gemini_structured_quota_error_is_not_guessed_from_message_text():
    class QuotaError(Exception):
        code = 429
        status = "RESOURCE_EXHAUSTED"

    response = GeminiProviderAdapter(
        FakeGeminiClient(exception=QuotaError("provider failure"))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == "PROVIDER_QUOTA"
    assert response.error.details["provider_error_code"] == "RESOURCE_EXHAUSTED"
    assert response.error.details["http_status"] == 429


@pytest.mark.parametrize("source", ["exception", "response"])
@pytest.mark.parametrize("field", ["status", "reason", "type", "code"])
def test_gemini_structured_provider_code_is_secret_redacted(source, field):
    secret_value = "api_key=GEMINI_DIAGNOSTIC_SECRET_123"
    if source == "exception":
        class UnsafeProviderError(Exception):
            pass

        error = UnsafeProviderError("provider failure")
        setattr(error, field, secret_value)
        client = FakeGeminiClient(exception=error)
    else:
        client = FakeGeminiClient(
            response={"error": {field: secret_value, "message": "provider failure"}}
        )

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.error is not None
    serialized = json.dumps(response.to_dict())
    assert secret_value not in serialized
    assert "GEMINI_DIAGNOSTIC_SECRET_123" not in serialized
    assert response.error.details["provider_error_code"] == "[REDACTED]"


def test_gemini_provider_error_response_and_safety_block_are_observable():
    provider_error = GeminiProviderAdapter(
        FakeGeminiClient(
            response={
                "error": {
                    "status_code": 503,
                    "status": "UNAVAILABLE",
                    "message": "provider overloaded",
                    "code": 503,
                },
                "request_id": "gemini-request-503",
            }
        )
    ).complete(_request())

    assert provider_error.error is not None
    assert provider_error.error.details["low_level_failure_class"] == "PROVIDER_OVERLOADED"
    assert provider_error.error.details["request_id"] == "gemini-request-503"
    assert provider_error.error.details["provider_error_message_safe"] == "provider overloaded"

    safety = GeminiProviderAdapter(
        FakeGeminiClient(response={"finish_reason": "SAFETY", "response_status": "blocked"})
    ).complete(_request())

    assert safety.error is not None
    assert safety.error.details["low_level_failure_class"] == "PROVIDER_SAFETY_BLOCK"
    assert safety.error.details["finish_reason"] == "SAFETY"
    assert safety.error.details["response_status"] == "blocked"


def test_gemini_malformed_and_incomplete_responses_keep_provider_classification():
    malformed = GeminiProviderAdapter(FakeGeminiClient(response={"candidates": []})).complete(
        _request()
    )
    assert malformed.error is not None
    assert malformed.error.details["low_level_failure_class"] == "RESPONSE_PARSE_ERROR"
    assert malformed.error.details["connection_phase"] == "SDK_PARSE"

    incomplete = GeminiProviderAdapter(
        FakeGeminiClient(response={"finish_reason": "MAX_TOKENS"})
    ).complete(_request())
    assert incomplete.error is not None
    assert incomplete.error.details["low_level_failure_class"] == "PROVIDER_INCOMPLETE"


def test_gemini_diagnostics_redact_prompt_and_proxy_credentials():
    prompt_fragment = "PRIVATE_PROMPT_FRAGMENT_2026"
    exception = FakeGeminiError(
        f"proxy https://proxy-user:proxy-secret@127.0.0.1:7897 failed for {prompt_fragment}; "
        "api_key=AIzaSyDUMMYKeyIsNotReal1234567890 "
        "Authorization: Bearer oauth-token-secret-123456789"
    )
    response = GeminiProviderAdapter(FakeGeminiClient(exception=exception)).complete(
        _request(user_input=f"request contains {prompt_fragment}")
    )

    assert response.error is not None
    serialized = json.dumps(response.to_dict())
    assert prompt_fragment not in serialized
    assert "proxy-user" not in serialized
    assert "proxy-secret" not in serialized
    assert "AIzaSyDUMMYKeyIsNotReal1234567890" not in serialized
    assert "oauth-token-secret-123456789" not in serialized
    assert "127.0.0.1" in serialized


def test_gemini_unknown_sdk_exception_is_preserved_without_provider_payload():
    SdkFailure = type(
        "SdkFailure",
        (Exception,),
        {"__module__": "google.genai.errors"},
    )
    response = GeminiProviderAdapter(
        FakeGeminiClient(exception=SdkFailure("unclassified SDK failure"))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == "SDK_ERROR"
    assert response.error.details["exception_type"] == "SdkFailure"
    assert response.error.details["provider_error_code"] is None
    assert response.error.details["http_status"] is None


@pytest.mark.parametrize(
    ("native_status", "expected_class"),
    [
        ("RATE_LIMIT_EXCEEDED", "PROVIDER_RATE_LIMIT"),
        ("ABORTED", "PROVIDER_ABORTED"),
    ],
)
def test_gemini_provider_status_codes_use_structured_classification(
    native_status, expected_class
):
    class NativeStatusError(Exception):
        code = 429
        status = native_status

    response = GeminiProviderAdapter(
        FakeGeminiClient(exception=NativeStatusError("provider error"))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == expected_class
    assert response.error.details["provider_error_code"] == native_status


def test_gemini_unclassified_provider_error_does_not_guess_overload_from_message():
    response = GeminiProviderAdapter(
        FakeGeminiClient(response={"error": {"message": "possibly overloaded"}})
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == "PROVIDER_FAILED_RESPONSE"


def test_gemini_aborted_provider_response_is_classified_from_finish_reason():
    response = GeminiProviderAdapter(
        FakeGeminiClient(response={"finish_reason": "ABORTED"})
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == "PROVIDER_ABORTED"
    assert response.error.details["finish_reason"] == "ABORTED"


def test_gemini_unclassified_exception_remains_unknown_not_guessed():
    class MysteryFailure(Exception):
        pass

    response = GeminiProviderAdapter(
        FakeGeminiClient(exception=MysteryFailure("opaque error"))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure_class"] == "UNKNOWN_PROVIDER_FAILURE"
    assert response.error.details["failure_layer"] == "UNKNOWN"


def test_gemini_route_diagnostics_keep_only_host_and_port():
    class ProxyUrl:
        host = "proxy.example.test"
        port = 7897
        username = "must-not-persist"
        password = "proxy-password"

    class Pool:
        _proxy_url = ProxyUrl()

    class Transport:
        _pool = Pool()

    exception = __import__("httpx").ConnectError(
        "refused",
        request=__import__("httpx").Request(
            "POST", "https://generativelanguage.googleapis.com"
        ),
    )
    client = FakeGeminiClient(exception=exception)
    client._transport_for_url = lambda _url: Transport()

    response = GeminiProviderAdapter(client).complete(_request())

    assert response.error is not None
    diagnostics = response.error.details
    assert diagnostics["target_hostname"] == "generativelanguage.googleapis.com"
    assert diagnostics["target_port"] == 443
    assert diagnostics["configured_proxy_hostname"] == "proxy.example.test"
    assert diagnostics["configured_proxy_port"] == 7897
    serialized = json.dumps(diagnostics)
    assert "must-not-persist" not in serialized
    assert "proxy-password" not in serialized
