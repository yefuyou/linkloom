"""Bounded model-visible evidence context for M1.1 Slice B."""

from __future__ import annotations

import pytest

from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.agents.providers.gemini_api import build_gemini_request
from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolResult


def _definition() -> ToolDefinition:
    return ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _verified_result(index: int = 1) -> ToolResult:
    return ToolResult(
        call_id=f"context-call-{index}",
        tool_id="search_notes",
        status="ok",
        value=[{
            "evidence_id": f"ev_context_{index}",
            "relative_path": f"note-{index}.md",
            "content_sha256": f"hash-{index}",
            "line_start": 1,
            "line_end": 1,
            "quote": f"Verified context evidence {index}.",
            "quote_sha256": f"quote-hash-{index}",
            "status": "verified",
        }],
    )


def _request(**changes) -> ModelTurnRequest:
    values = {
        "run_id": "run-context",
        "turn_id": "run-context:turn:2",
        "task_id": "task-context",
        "agent_id": "retrieval_agent",
        "sequence": 2,
        "user_input": "synthesize only verified evidence",
        "observation": _verified_result(),
        "available_tools": [_definition()],
        "previous_tool_call": ToolCall(
            call_id="context-call-1",
            tool_id="search_notes",
            arguments={"query": "verified context"},
        ),
        "model_id": "gemini-test-model",
        "evidence_context": [_verified_result()],
    }
    values.update(changes)
    return ModelTurnRequest(**values)


def test_model_turn_request_roundtrips_a_bounded_verified_evidence_context():
    request = _request()

    restored = ModelTurnRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.evidence_context == [_verified_result()]


def test_model_turn_request_rejects_non_verified_context_values():
    with pytest.raises(ValidationError, match="evidence_context"):
        _request(
            evidence_context=[
                ToolResult(
                    call_id="unverified-context-call",
                    tool_id="search_notes",
                    status="ok",
                    value=[{"status": "unverified"}],
                )
            ]
        )


def test_model_turn_request_rejects_context_larger_than_eight_results():
    with pytest.raises(ValidationError, match="evidence_context"):
        _request(evidence_context=[_verified_result(index) for index in range(1, 10)])


def test_model_turn_request_accepts_historical_requests_without_context():
    historical = _request().to_dict()
    historical.pop("evidence_context", None)

    assert ModelTurnRequest.from_dict(historical).evidence_context == []


def test_gemini_request_serializes_verified_context_into_model_visible_input():
    payload = build_gemini_request(_request())

    model_input = payload["contents"][0]["parts"][0]["text"]
    assert "verified_evidence_context=" in model_input
    assert "ev_context_1" in model_input
    assert "Verified context evidence 1." in model_input
