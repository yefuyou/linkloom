import json

import pytest

from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult


def test_tool_result_success_preserves_call_and_value():
    result = ToolResult(
        call_id="call_1",
        tool_id="search_notes",
        status="ok",
        value={"matches": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "ok"
    assert result.error is None
    assert result.to_dict()["call_id"] == "call_1"
    assert result.to_dict()["value"] == {"matches": [{"evidence_id": "ev_1"}]}


def test_tool_result_error_contains_structured_tool_error():
    error = ToolError(
        code="TOOL_EXECUTION_FAILED",
        category="runtime",
        message="The verified reader failed.",
        retryable=True,
        affected_refs=["note_ref_0"],
        details={"source": "loader"},
        safe_to_expose=True,
    )
    result = ToolResult(
        call_id="call_2",
        tool_id="read_verified_note",
        status="error",
        error=error,
    )

    assert result.status == "error"
    assert result.error == error
    assert result.to_dict()["error"]["code"] == "TOOL_EXECUTION_FAILED"


def test_tool_contracts_round_trip_through_json_serialization():
    definition = ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema={"type": "object", "required": ["query"]},
        output_schema={"type": "array"},
    )
    call = ToolCall(
        call_id="call_3",
        tool_id="search_notes",
        arguments={"query": "architecture", "limit": 5},
        run_id="run_1",
        task_id="task_1",
        agent_id="retrieval_agent",
        sequence=1,
    )
    result = ToolResult(
        call_id=call.call_id,
        tool_id=call.tool_id,
        status="ok",
        value=[{"evidence_id": "ev_1"}],
        business_status="FOUND",
    )

    serialized = json.dumps(
        {
            "definition": definition.to_dict(),
            "call": call.to_dict(),
            "result": result.to_dict(),
        },
        sort_keys=True,
    )
    decoded = json.loads(serialized)

    assert ToolDefinition.from_dict(decoded["definition"]) == definition
    assert ToolCall.from_dict(decoded["call"]) == call
    assert ToolResult.from_dict(decoded["result"]) == result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code", ""),
        ("category", "tool"),
        ("message", ""),
        ("retryable", "yes"),
        ("affected_refs", "note_ref_0"),
        ("details", []),
        ("safe_to_expose", 1),
    ],
)
def test_tool_error_rejects_invalid_fields(field, value):
    kwargs = {
        "code": "TOOL_EXECUTION_FAILED",
        "category": "runtime",
        "message": "failed",
        "retryable": False,
        "affected_refs": [],
        "details": {},
        "safe_to_expose": True,
    }
    kwargs[field] = value

    with pytest.raises(ValidationError):
        ToolError(**kwargs)
