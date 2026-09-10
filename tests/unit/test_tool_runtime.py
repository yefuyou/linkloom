import json

import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


class _RecordingSink:
    def __init__(self):
        self.events = []

    def emit(self, event_type, actor, status, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "status": status,
                "attributes": kwargs.get("attributes") or {},
            }
        )


def _definition(
    tool_id: str = "search_notes",
    output_schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version="1",
        description="Search verified notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        output_schema=output_schema or {"type": "array"},
    )


def _call(call_id: str = "call_1", arguments: dict | None = None, tool_id: str = "search_notes") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id=tool_id,
        arguments=arguments if arguments is not None else {"query": "architecture"},
    )


def _enforcer(*, allowed: list[str] | None = None, max_calls: int = 3) -> ToolPolicyEnforcer:
    policy = ToolCallPolicy(
        policy_version="p4-readonly-tools-v1",
        agent_id="retrieval_agent",
        allowed_tool_ids=allowed if allowed is not None else ["search_notes"],
        denied_tool_ids=["write_file", "rename_file", "read_gold", "raw_filesystem"],
        max_calls=max_calls,
        network="deny",
        vault_write="deny",
        gold_access="deny",
    )
    return ToolPolicyEnforcer(policy)


def _runtime(executor, definition: ToolDefinition | None = None) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(definition or _definition(), executor)
    return ToolRuntime(registry)


def test_valid_execution_returns_success_and_consumes_one_authorized_call():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments) or [{"evidence_id": "ev_1"}])
    enforcer = _enforcer()

    result = runtime.execute(_call(), enforcer)

    assert result.status == "ok"
    assert result.value == [{"evidence_id": "ev_1"}]
    assert result.error is None
    assert calls == [{"query": "architecture"}]
    assert enforcer.call_count == 1


def test_unknown_tool_returns_tool_error_without_authorization_or_execution():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments))
    enforcer = _enforcer()

    result = runtime.execute(_call(tool_id="unknown_tool"), enforcer)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_UNKNOWN"
    assert calls == []
    assert enforcer.call_count == 0


def test_malformed_arguments_are_rejected_before_policy_and_do_not_consume_budget():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments))
    enforcer = _enforcer(max_calls=1)

    result = runtime.execute(_call(arguments={"limit": 5}), enforcer)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_INVALID_ARGUMENTS"
    assert calls == []
    assert enforcer.call_count == 0
    assert enforcer.remaining_calls == 1


def test_permission_denied_returns_tool_error_before_executor():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments))
    enforcer = _enforcer(allowed=[])

    result = runtime.execute(_call(), enforcer)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_PERMISSION_DENIED"
    assert calls == []
    assert enforcer.call_count == 0


def test_max_calls_exhausted_returns_budget_error_without_second_execution():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments) or [])
    enforcer = _enforcer(max_calls=1)

    first = runtime.execute(_call("call_1"), enforcer)
    second = runtime.execute(_call("call_2"), enforcer)

    assert first.status == "ok"
    assert second.status == "error"
    assert second.error is not None
    assert second.error.code == "TOOL_BUDGET_EXCEEDED"
    assert calls == [{"query": "architecture"}]
    assert enforcer.call_count == 1


def test_executor_exception_is_safe_and_normalized():
    raw_message = "internal failure at C:/vault/secret.txt token=secret-value"

    def executor(arguments):
        raise RuntimeError(raw_message)

    runtime = _runtime(executor)
    enforcer = _enforcer()

    result = runtime.execute(_call(), enforcer)
    serialized = json.dumps(result.to_dict(), sort_keys=True)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_EXECUTION_FAILED"
    assert result.error.message == "Tool execution failed."
    assert raw_message not in serialized
    assert "secret-value" not in serialized
    assert enforcer.call_count == 1


def test_invalid_executor_output_is_normalized_after_authorization():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments) or {"not": "an array"})
    enforcer = _enforcer()

    result = runtime.execute(_call(), enforcer)

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_INVALID_OUTPUT"
    assert calls == [{"query": "architecture"}]
    assert enforcer.call_count == 1


def test_business_not_found_is_success_with_business_status():
    definition = _definition(output_schema={"type": "array"})
    call = _call()

    def executor(arguments):
        return ToolResult(
            call_id=call.call_id,
            tool_id=call.tool_id,
            status="ok",
            value=[],
            business_status="NOT_FOUND",
        )

    runtime = _runtime(executor, definition)
    result = runtime.execute(call, _enforcer())

    assert result.status == "ok"
    assert result.business_status == "NOT_FOUND"
    assert result.value == []
    assert result.error is None


def test_executor_runs_only_after_resolve_input_and_policy_prechecks_pass():
    calls = []
    runtime = _runtime(lambda arguments: calls.append(arguments) or [])

    denied = runtime.execute(_call("denied"), _enforcer(allowed=[]))
    malformed = runtime.execute(_call("malformed", {"limit": 1}), _enforcer())
    valid = runtime.execute(_call("valid"), _enforcer())

    assert denied.error is not None and denied.error.code == "TOOL_PERMISSION_DENIED"
    assert malformed.error is not None and malformed.error.code == "TOOL_INVALID_ARGUMENTS"
    assert valid.status == "ok"
    assert calls == [{"query": "architecture"}]


def test_json_schema_enum_does_not_treat_integer_as_boolean():
    calls = []
    definition = ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Enum boundary test.",
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "integer", "enum": [True]}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )
    runtime = _runtime(lambda arguments: calls.append(arguments) or [], definition)
    policy = _enforcer(allowed=["search_notes"])

    result = runtime.execute(
        ToolCall("enum_call", "search_notes", {"value": 1}),
        policy,
    )

    assert result.error is not None
    assert result.error.code == "TOOL_INVALID_ARGUMENTS"
    assert calls == []
    assert policy.call_count == 0


def test_trace_lifecycle_correlates_called_with_one_terminal_event():
    sink = _RecordingSink()
    runtime = _runtime(lambda arguments: [])

    success = runtime.execute(_call("trace_success"), _enforcer(), event_sink=sink)
    malformed = runtime.execute(
        _call("trace_malformed", {"limit": 1}),
        _enforcer(),
        event_sink=sink,
    )

    assert success.status == "ok"
    assert malformed.error is not None
    assert malformed.error.code == "TOOL_INVALID_ARGUMENTS"
    grouped = {}
    for event in sink.events:
        call_id = event["attributes"]["call_id"]
        grouped.setdefault(call_id, []).append(event)
    assert set(grouped) == {"trace_success", "trace_malformed"}
    for call_id, events in grouped.items():
        assert [event["event_type"] for event in events].count("tool.called") == 1
        terminal = [event for event in events if event["event_type"] in {"tool.completed", "tool.failed"}]
        assert len(terminal) == 1
        assert terminal[0]["attributes"]["call_id"] == call_id
