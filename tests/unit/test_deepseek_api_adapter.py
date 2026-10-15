"""Offline contract tests for the DeepSeek Chat Completions adapter."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from linkloom.agents.model_adapter import (
    ModelAction,
    ModelGenerationOptions,
    ModelResponse,
    ModelToolResult,
    ModelToolTurn,
    ModelTurnRequest,
    ModelUsage,
)
from linkloom.agents.providers.deepseek_api import (
    DeepSeekProviderAdapter,
    build_deepseek_request,
    map_tool_definition_to_deepseek_tool,
)
from linkloom.agents.providers.failure_diagnostics import (
    exception_failure_diagnostics,
)
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


@dataclass
class FakeDeepSeekClient:
    response: object | None = None
    exception: BaseException | None = None

    def __post_init__(self) -> None:
        self.requests: list[dict] = []

    def create_chat_completion(self, **request):
        self.requests.append(request)
        if self.exception is not None:
            raise self.exception
        return self.response


class FakeDeepSeekError(Exception):
    def __init__(self, *, status_code: int | None = None) -> None:
        super().__init__("raw provider error must not persist")
        self.status_code = status_code


def _definition(tool_id: str = "search_notes") -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1",
        description=f"Use {tool_id} on verified notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _call() -> ToolCall:
    return ToolCall(
        call_id="call_deepseek_1",
        tool_id="search_notes",
        arguments={"query": "durable DeepSeek tools"},
        run_id="run_deepseek_1",
        task_id="task_deepseek_1",
        agent_id="retrieval_agent",
        sequence=1,
    )


def _observation(call: ToolCall | None = None) -> ToolResult:
    call = call or _call()
    return ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_deepseek_1", "quote": "verified"}],
        business_status="FOUND",
    )


def _request(**changes) -> ModelTurnRequest:
    values = {
        "run_id": "run_deepseek_1",
        "turn_id": "run_deepseek_1:turn:1",
        "task_id": "task_deepseek_1",
        "agent_id": "retrieval_agent",
        "sequence": 1,
        "user_input": "Return JSON after inspecting verified evidence.",
        "observation": None,
        "available_tools": [_definition()],
        "model_id": "deepseek-flash",
        "generation_options": ModelGenerationOptions(
            max_output_tokens=512,
            temperature=0.1,
        ),
    }
    values.update(changes)
    return ModelTurnRequest(**values)


def _response_message(
    *,
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-deepseek-1",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": message,
            }
        ],
        "usage": {
            "prompt_tokens": 17,
            "completion_tokens": 9,
            "total_tokens": 26,
            "prompt_cache_hit_tokens": 3,
            "prompt_cache_miss_tokens": 14,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _tool_call(*, arguments: str = '{"query":"durable DeepSeek tools"}') -> dict:
    return {
        "id": "call_deepseek_1",
        "type": "function",
        "function": {
            "name": "search_notes",
            "arguments": arguments,
        },
    }


def test_initial_request_uses_current_model_non_thinking_json_and_tools():
    payload = build_deepseek_request(
        _request(),
        system_instruction="Use tools and return one JSON object.",
        json_output=True,
    )

    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.1
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
    ]
    assert payload["messages"][0]["content"] == "Use tools and return one JSON object."
    assert payload["messages"][1]["content"] == _request().user_input
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [
        map_tool_definition_to_deepseek_tool(_definition())
    ]
    assert payload["tools"][0]["function"]["parameters"] == _definition().input_schema
    assert "strict" not in payload["tools"][0]["function"]


def test_tool_call_response_maps_id_name_and_json_arguments_without_execution():
    client = FakeDeepSeekClient(
        response=_response_message(
            tool_calls=[_tool_call()],
            finish_reason="tool_calls",
        )
    )

    response = DeepSeekProviderAdapter(client).complete(_request())

    assert response.action == ModelAction.tool(_call())
    assert response.provider_continuation is None
    assert response.finish_reason == "tool_calls"
    assert response.usage == ModelUsage(
        input_tokens=17,
        output_tokens=9,
        total_tokens=26,
        duration_ms=response.usage.duration_ms,
    )
    assert response.provider_metadata["prompt_cache_hit_tokens"] == 3
    assert response.provider_metadata["prompt_cache_miss_tokens"] == 14
    assert response.provider_metadata["reasoning_tokens"] == 0
    assert len(client.requests) == 1


def test_multiple_tool_calls_map_to_one_ordered_proposal_with_runtime_ids():
    calls = [
        _tool_call(arguments='{"query":"first"}'),
        {
            "id": "call_deepseek_2",
            "type": "function",
            "function": {
                "name": "read_verified_note",
                "arguments": '{"query":"second"}',
            },
        },
    ]
    client = FakeDeepSeekClient(
        response=_response_message(tool_calls=calls, finish_reason="tool_calls")
    )

    response = DeepSeekProviderAdapter(client).complete(
        _request(available_tools=[_definition(), _definition("read_verified_note")])
    )

    assert response.error is None
    assert response.proposal is not None
    assert response.proposal.kind == "tool_calls"
    assert [call.provider_call_id for call in response.proposal.tool_calls] == [
        "call_deepseek_1",
        "call_deepseek_2",
    ]
    assert len(
        {call.runtime_call.call_id for call in response.proposal.tool_calls}
    ) == 2
    assert DeepSeekProviderAdapter(client).capability.supports_multiple_tool_calls


def test_duplicate_or_malformed_middle_call_rejects_the_whole_response():
    duplicate = [_tool_call(), _tool_call(arguments='{"query":"other"}')]
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response=_response_message(
                tool_calls=duplicate,
                finish_reason="tool_calls",
            )
        )
    ).complete(_request())

    assert response.proposal is None
    assert response.error is not None
    assert response.error.details["reason"] == "duplicate_function_call_id"


def test_grouped_v2_turn_serializes_one_assistant_array_then_ordered_tools():
    adapter = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response=_response_message(
                tool_calls=[
                    _tool_call(arguments='{"query":"first"}'),
                    {
                        "id": "call_deepseek_2",
                        "type": "function",
                        "function": {
                            "name": "search_notes",
                            "arguments": '{"query":"second"}',
                        },
                    },
                ],
                finish_reason="tool_calls",
            )
        )
    )
    proposal = adapter.complete(_request()).proposal
    model_results = [
        ModelToolResult.for_call(
            run_id="run_deepseek_1",
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
        source_turn_id="run_deepseek_1:turn:1",
        source_sequence=1,
        tool_calls=proposal.tool_calls,
        tool_results=model_results,
    )

    payload = build_deepseek_request(
        _request(
            turn_id="run_deepseek_1:turn:2",
            sequence=2,
            tool_turns=[tool_turn],
        )
    )

    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert [
        item["id"] for item in payload["messages"][2]["tool_calls"]
    ] == ["call_deepseek_1", "call_deepseek_2"]
    assert [message["tool_call_id"] for message in payload["messages"][3:]] == [
        "call_deepseek_1",
        "call_deepseek_2",
    ]


def test_tool_result_reconstructs_official_assistant_then_tool_message_pair():
    call = _call()
    payload = build_deepseek_request(
        _request(
            sequence=2,
            turn_id="run_deepseek_1:turn:2",
            previous_tool_call=call,
            observation=_observation(call),
        ),
        system_instruction="Use tools and return JSON.",
        json_output=True,
    )

    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assistant = payload["messages"][2]
    assert assistant == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.tool_id,
                    "arguments": json.dumps(
                        call.arguments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }
        ],
    }
    tool_message = payload["messages"][3]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == call.call_id
    assert json.loads(tool_message["content"]) == {
        "business_status": "FOUND",
        "status": "ok",
        "value": [{"evidence_id": "ev_deepseek_1", "quote": "verified"}],
    }


def test_final_json_maps_to_final_action_and_never_persists_reasoning_content():
    final = '{"schema_version":"team-decision-result/v1"}'
    raw = _response_message(content=final)
    raw["choices"][0]["message"]["reasoning_content"] = None
    client = FakeDeepSeekClient(response=raw)

    response = DeepSeekProviderAdapter(client, json_output=True).complete(_request())

    assert response.action == ModelAction.final(final)
    assert "reasoning_content" not in json.dumps(response.to_dict())


@pytest.mark.parametrize(
    ("raw", "code", "reason"),
    [
        (
            _response_message(
                tool_calls=[_tool_call(arguments="not-json")],
                finish_reason="tool_calls",
            ),
            "MODEL_TOOL_CALL_PARSE_FAILED",
            "function_arguments_not_json",
        ),
        (
            _response_message(
                tool_calls=[
                    {
                        **_tool_call(),
                        "function": {
                            "name": "undeclared_tool",
                            "arguments": "{}",
                        },
                    }
                ],
                finish_reason="tool_calls",
            ),
            "MODEL_RESPONSE_UNSUPPORTED",
            "undeclared_tool",
        ),
        (
            _response_message(content=None),
            "MODEL_RESPONSE_MALFORMED",
            "empty_action",
        ),
    ],
)
def test_malformed_or_undeclared_provider_actions_fail_closed(raw, code, reason):
    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.details["reason"] == reason


def test_nonempty_reasoning_content_is_rejected_in_non_thinking_adapter():
    raw = _response_message(content="{}")
    raw["choices"][0]["message"]["reasoning_content"] = "hidden"

    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "unexpected_reasoning_content"
    assert "hidden" not in json.dumps(response.to_dict())


def test_nonzero_reasoning_usage_is_rejected_in_non_thinking_adapter():
    raw = _response_message(content="{}")
    raw["usage"]["completion_tokens_details"]["reasoning_tokens"] = 1

    response = DeepSeekProviderAdapter(FakeDeepSeekClient(response=raw)).complete(
        _request()
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_RESPONSE_UNSUPPORTED"
    assert response.error.details["reason"] == "unexpected_reasoning_usage"


@pytest.mark.parametrize(
    ("finish_reason", "code", "reason"),
    [
        ("aborted", "MODEL_CANCELLED", "provider_aborted"),
        (
            "insufficient_system_resource",
            "MODEL_TRANSIENT_FAILURE",
            "insufficient_system_resource",
        ),
    ],
)
def test_official_terminal_failure_reasons_map_to_provider_errors(
    finish_reason, code, reason
):
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response=_response_message(content=None, finish_reason=finish_reason)
        )
    ).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.details["reason"] == reason


@pytest.mark.parametrize(
    ("exception", "code", "outcome"),
    [
        (FakeDeepSeekError(status_code=400), "MODEL_INVALID_REQUEST", "known_failure"),
        (FakeDeepSeekError(status_code=401), "MODEL_AUTH_REQUIRED", "known_failure"),
        (FakeDeepSeekError(status_code=402), "MODEL_UNAVAILABLE", "known_failure"),
        (FakeDeepSeekError(status_code=422), "MODEL_INVALID_REQUEST", "known_failure"),
        (FakeDeepSeekError(status_code=429), "MODEL_RATE_LIMITED", "known_failure"),
        (TimeoutError("secret timeout detail"), "MODEL_TRANSIENT_FAILURE", "unknown_provider_outcome"),
        (FakeDeepSeekError(status_code=503), "MODEL_TRANSIENT_FAILURE", "known_failure"),
    ],
)
def test_provider_errors_are_normalized_once_without_raw_error_text(
    exception, code, outcome
):
    client = FakeDeepSeekClient(exception=exception)

    response = DeepSeekProviderAdapter(client).complete(_request())

    assert response.action is None
    assert response.error is not None
    assert response.error.code == code
    assert response.error.outcome == outcome
    assert response.error.message != str(exception)
    assert len(client.requests) == 1


def test_transport_timeout_keeps_provider_diagnostics_and_transient_contract():
    client = FakeDeepSeekClient(exception=TimeoutError("read timed out"))

    response = DeepSeekProviderAdapter(client).complete(_request())

    assert response.error is not None
    assert response.error.code == "MODEL_TRANSIENT_FAILURE"
    details = response.error.details
    assert details["failure_layer"] == "transport"
    assert details["low_level_failure"] == "TRANSPORT_TIMEOUT"
    assert details["high_level_outcome"] == "MODEL_TRANSIENT_FAILURE"
    assert details["exception_type"] == "TimeoutError"
    assert details["timeout_type"] == "TimeoutError"
    assert details["elapsed_ms"] is not None


def test_http_503_and_429_keep_status_and_low_level_classification():
    for status, expected_low_level in (
        (400, "HTTP_4XX"),
        (503, "HTTP_5XX"),
        (429, "HTTP_429"),
    ):
        response = DeepSeekProviderAdapter(
            FakeDeepSeekClient(exception=FakeDeepSeekError(status_code=status))
        ).complete(_request())

        assert response.error is not None
        details = response.error.details
        assert details["http_status"] == status
        assert details["low_level_failure"] == expected_low_level
        assert details["high_level_outcome"] == response.error.code
        assert {
            "failure_layer",
            "exception_type",
            "exception_repr",
            "nested_cause",
            "http_status",
            "provider_error_code",
            "provider_error_message",
            "request_id",
            "finish_reason",
            "response_status",
            "timeout_type",
            "errno",
            "winerror",
            "elapsed_ms",
            "high_level_outcome",
            "low_level_failure",
        } <= set(details)


def test_urllib_http_error_code_is_preserved_as_http_status():
    exception = HTTPError(
        "https://api.deepseek.com/chat/completions",
        503,
        "Service Unavailable",
        {"x-request-id": "req-urllib-503"},
        BytesIO(b'{"error":{"message":"offline"}}'),
    )
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=exception)
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert response.error.code == "MODEL_TRANSIENT_FAILURE"
    assert details["http_status"] == 503
    assert details["low_level_failure"] == "HTTP_5XX"
    assert details["failure_layer"] == "http"
    assert details["request_id"] == "req-urllib-503"
    assert details["provider_error_code"] is None


@pytest.mark.parametrize("status_key", ["status", "http_status", "code"])
def test_provider_response_status_aliases_are_classified_as_http(status_key):
    response_payload = {
        "error": {status_key: 503, "message": "service unavailable"}
    }
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(response=response_payload)
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["http_status"] == 503
    assert details["low_level_failure"] == "HTTP_5XX"
    assert details["failure_layer"] == "http"
    assert details["provider_error_code"] is None


def test_nested_exception_diagnostics_are_bounded():
    class CountedError(RuntimeError):
        cause_reads = 0

        def __getattribute__(self, name):
            if name == "__cause__":
                type(self).cause_reads += 1
            return super().__getattribute__(name)

    cause: BaseException = CountedError("cause 0")
    for index in range(1, 20):
        try:
            raise CountedError(f"cause {index}") from cause
        except CountedError as wrapped:
            cause = wrapped

    CountedError.cause_reads = 0
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=cause)
    ).complete(_request())

    assert response.error is not None
    assert len(response.error.details["nested_cause"]) <= 8
    assert CountedError.cause_reads <= 8


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (URLError(socket.gaierror(11001, "name resolution failed")), "TRANSPORT_DNS"),
        (URLError("timed out while resolving host"), "TRANSPORT_TIMEOUT"),
        (URLError("certificate verification failed"), "TRANSPORT_TLS"),
        (ConnectionRefusedError(10061, "connection refused"), "TRANSPORT_CONNECT"),
        (ssl.SSLError("certificate verify failed"), "TRANSPORT_TLS"),
    ],
)
def test_transport_failure_layers_are_distinguished(exception, expected):
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=exception)
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure"] == expected


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("aborted", "PROVIDER_ABORTED"),
        ("insufficient_system_resource", "PROVIDER_OVERLOADED"),
    ],
)
def test_chat_completion_native_terminal_failure_is_preserved(finish_reason, expected):
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response=_response_message(content=None, finish_reason=finish_reason)
        )
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["low_level_failure"] == expected
    assert details["finish_reason"] == finish_reason
    assert details["provider_error_code"] == (
        finish_reason
        if finish_reason == "insufficient_system_resource"
        else None
    )
    assert details["http_status"] is None


def test_provider_failed_chat_completion_response_keeps_native_error_fields():
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(
            response={
                "id": "chatcmpl-failed",
                "request_id": "req-safe-123",
                "status": "failed",
                "error": {
                    "code": "insufficient_system_resource",
                    "message": "provider overloaded",
                },
            }
        )
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["low_level_failure"] == "PROVIDER_OVERLOADED"
    assert details["provider_error_code"] == "insufficient_system_resource"
    assert details["provider_error_message"] == "provider overloaded"
    assert details["request_id"] == "req-safe-123"
    assert details["response_status"] == "failed"


def test_generic_failed_chat_completion_response_is_not_silently_normalized():
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(response={"error": {"message": "response failed"}})
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure"] == "PROVIDER_FAILED_RESPONSE"
    assert response.error.details["provider_error_message"] == "response failed"


def test_unknown_exception_retains_safe_type_and_nested_cause():
    try:
        try:
            raise ValueError("inner transport detail")
        except ValueError as cause:
            raise RuntimeError("outer failure") from cause
    except RuntimeError as error:
        exception = error

    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=exception)
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["exception_type"] == "RuntimeError"
    assert details["low_level_failure"] == "UNKNOWN_PROVIDER_FAILURE"
    assert details["nested_cause"][0]["exception_type"] == "ValueError"
    assert "inner transport detail" in details["nested_cause"][0]["exception_repr"]
    round_trip = ModelResponse.from_dict(response.to_dict())
    assert round_trip.error is not None
    assert round_trip.error.details == details


def test_urllib_reason_chain_retains_dns_errno_without_raw_request():
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=URLError(socket.gaierror(11001, "DNS failed")))
    ).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["low_level_failure"] == "TRANSPORT_DNS"
    assert details["nested_cause"][0]["exception_type"] == "gaierror"
    assert details["nested_cause"][0]["errno"] == 11001


def test_unknown_httpx_protocol_error_is_classified_as_sdk_error():
    import httpx

    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=httpx.ProtocolError("invalid protocol frame"))
    ).complete(_request())

    assert response.error is not None
    assert response.error.details["low_level_failure"] == "SDK_ERROR"


def test_provider_diagnostics_redact_credentials_and_full_prompt():
    prompt = "customer-sensitive-prompt-7e129"
    exception = RuntimeError(
        f"api_key=sk-secret-value Authorization: Bearer bearer-secret {prompt}"
    )
    response = DeepSeekProviderAdapter(
        FakeDeepSeekClient(exception=exception)
    ).complete(_request(user_input=prompt))

    assert response.error is not None
    encoded = json.dumps(response.error.to_dict(), ensure_ascii=False)
    assert "sk-secret-value" not in encoded
    assert "bearer-secret" not in encoded
    assert prompt not in encoded
    assert "[REDACTED]" in encoded


def test_short_request_content_is_redacted_from_exception_repr():
    diagnostics = exception_failure_diagnostics(
        RuntimeError("echo secret7"),
        high_level_outcome="MODEL_TRANSIENT_FAILURE",
        request_payload={"messages": [{"content": "secret7"}]},
        elapsed_ms=1.0,
    )

    assert "secret7" not in diagnostics["exception_repr"]
    assert diagnostics["exception_repr"] == "RuntimeError('echo [REDACTED]')"


@pytest.mark.parametrize(
    ("status", "expected_low_level"),
    [(429, "HTTP_429"), (503, "HTTP_5XX")],
)
def test_installed_openai_sdk_http_errors_keep_status_and_request_id(
    status, expected_low_level
):
    import httpx
    from openai import OpenAI

    def handler(request):
        return httpx.Response(
            status,
            request=request,
            headers={"x-request-id": "req-offline-123"},
            json={
                "error": {
                    "code": "rate_limit" if status == 429 else "server_error",
                    "message": "offline synthetic failure",
                    "type": "provider_error",
                }
            },
        )

    sdk = OpenAI(
        api_key="offline-test-key",
        base_url="https://api.deepseek.com",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    class SDKBackedClient:
        def create_chat_completion(self, **payload):
            thinking = payload.pop("thinking")
            return sdk.chat.completions.create(
                **payload,
                extra_body={"thinking": thinking},
            )

    response = DeepSeekProviderAdapter(SDKBackedClient()).complete(_request())

    assert response.error is not None
    details = response.error.details
    assert details["http_status"] == status
    assert details["low_level_failure"] == expected_low_level
    assert details["request_id"] == "req-offline-123"
    assert details["provider_error_code"] in {"rate_limit", "server_error"}
    sdk.close()


def test_observation_without_previous_call_fails_before_provider_invocation():
    client = FakeDeepSeekClient(response=_response_message(content="{}"))

    response = DeepSeekProviderAdapter(client).complete(
        _request(observation=_observation())
    )

    assert response.action is None
    assert response.error is not None
    assert response.error.code == "MODEL_INVALID_REQUEST"
    assert client.requests == []


def test_request_and_durable_response_contain_no_credential_or_deepseek_state_blob():
    client = FakeDeepSeekClient(response=_response_message(content="{}"))

    response = DeepSeekProviderAdapter(client, json_output=True).complete(_request())

    encoded_request = json.dumps(client.requests[0], ensure_ascii=False).lower()
    encoded_response = json.dumps(response.to_dict(), ensure_ascii=False).lower()
    assert "api_key" not in encoded_request + encoded_response
    assert "authorization" not in encoded_request + encoded_response
    assert "reasoning_content" not in encoded_request + encoded_response
    assert "provider_continuation" not in encoded_response
    assert response.provider_continuation is None
