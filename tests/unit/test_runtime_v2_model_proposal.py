from __future__ import annotations

import json

import pytest

from linkloom.agents.model_adapter import (
    ModelResponse,
    ModelToolCall,
    ModelToolResult,
    ModelToolTurn,
    ModelTurnProposal,
    ModelTurnRequest,
    ProviderTurnContinuation,
    runtime_call_id_for,
    select_bounded_model_tool_turns,
    tool_result_id_for,
)
from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolCall, ToolResult


RUN_ID = "run_runtime_v2_contract"
TASK_ID = "task_runtime_v2_contract"
AGENT_ID = "agent_runtime_v2_contract"


def _model_call(
    *,
    turn_id: str = f"{RUN_ID}:turn:1",
    sequence: int = 1,
    ordinal: int = 0,
    provider_call_id: str = "provider-call-1",
    tool_id: str = "search_notes",
) -> ModelToolCall:
    proposal_id = f"{turn_id}:proposal"
    runtime_call_id = runtime_call_id_for(
        run_id=RUN_ID,
        proposal_id=proposal_id,
        ordinal=ordinal,
        provider_call_id=provider_call_id,
    )
    return ModelToolCall(
        provider_call_id=provider_call_id,
        runtime_call=ToolCall(
            call_id=runtime_call_id,
            tool_id=tool_id,
            arguments={"query": f"query-{ordinal}"},
            run_id=RUN_ID,
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            sequence=sequence,
        ),
    )


def _model_result(
    model_call: ModelToolCall,
    *,
    proposal_id: str,
    ordinal: int,
) -> ModelToolResult:
    return ModelToolResult.for_call(
        run_id=RUN_ID,
        proposal_id=proposal_id,
        ordinal=ordinal,
        model_call=model_call,
        result=ToolResult(
            call_id=model_call.runtime_call.call_id,
            tool_id=model_call.runtime_call.tool_id,
            status="ok",
            value={"ordinal": ordinal},
        ),
    )


def test_tool_proposal_roundtrips_order_and_runtime_identities():
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    calls = [
        _model_call(ordinal=index, provider_call_id=f"provider-call-{index}")
        for index in range(3)
    ]

    proposal = ModelTurnProposal.tools(proposal_id, calls)
    restored = ModelTurnProposal.from_dict(
        json.loads(json.dumps(proposal.to_dict(), sort_keys=True))
    )

    assert restored == proposal
    assert [call.provider_call_id for call in restored.tool_calls] == [
        "provider-call-0",
        "provider-call-1",
        "provider-call-2",
    ]
    assert len({call.runtime_call.call_id for call in restored.tool_calls}) == 3


def test_v2_model_response_roundtrips_only_the_canonical_proposal():
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    proposal = ModelTurnProposal.tools(
        proposal_id,
        [
            _model_call(ordinal=index, provider_call_id=f"provider-call-{index}")
            for index in range(2)
        ],
    )
    response = ModelResponse(proposal=proposal)

    payload = response.to_dict()
    restored = ModelResponse.from_dict(
        json.loads(json.dumps(payload, sort_keys=True))
    )

    assert payload["action"] is None
    assert restored == response
    assert restored.proposal == proposal


def test_provider_id_reuse_across_turns_gets_distinct_run_wide_runtime_ids():
    provider_call_id = "provider-reused-call"

    first = _model_call(provider_call_id=provider_call_id)
    second = _model_call(
        turn_id=f"{RUN_ID}:turn:2",
        sequence=2,
        provider_call_id=provider_call_id,
    )

    assert first.provider_call_id == second.provider_call_id
    assert first.runtime_call.call_id != second.runtime_call.call_id
    assert first.runtime_call.call_id == runtime_call_id_for(
        run_id=RUN_ID,
        proposal_id=f"{RUN_ID}:turn:1:proposal",
        ordinal=0,
        provider_call_id=provider_call_id,
    )


def test_proposal_rejects_duplicate_provider_or_runtime_call_identity():
    first = _model_call(ordinal=0, provider_call_id="provider-duplicate")
    duplicate_provider = _model_call(
        ordinal=1,
        provider_call_id="provider-duplicate",
    )
    with pytest.raises(ValidationError, match="Provider call IDs"):
        ModelTurnProposal.tools(
            f"{RUN_ID}:turn:1:proposal",
            [first, duplicate_provider],
        )

    duplicate_runtime = ModelToolCall(
        provider_call_id="provider-distinct",
        runtime_call=ToolCall(
            **{
                **first.runtime_call.to_dict(),
                "arguments": {"query": "other"},
            }
        ),
    )
    with pytest.raises(ValidationError, match="Runtime call IDs"):
        ModelTurnProposal.tools(
            f"{RUN_ID}:turn:1:proposal",
            [first, duplicate_runtime],
        )


def test_proposal_rejects_empty_or_more_than_sixteen_calls():
    with pytest.raises(ValidationError, match="1..16"):
        ModelTurnProposal.tools(f"{RUN_ID}:turn:1:proposal", [])

    with pytest.raises(ValidationError, match="1..16"):
        ModelTurnProposal.tools(
            f"{RUN_ID}:turn:1:proposal",
            [
                _model_call(ordinal=index, provider_call_id=f"provider-{index}")
                for index in range(17)
            ],
        )


def test_model_tool_result_identity_survives_roundtrip_and_binds_content():
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    call = _model_call()
    result = _model_result(call, proposal_id=proposal_id, ordinal=0)

    restored = ModelToolResult.from_dict(
        json.loads(json.dumps(result.to_dict(), sort_keys=True))
    )

    assert restored == result
    assert result.result_id == tool_result_id_for(
        run_id=RUN_ID,
        proposal_id=proposal_id,
        ordinal=0,
        runtime_call_id=call.runtime_call.call_id,
    )

    with pytest.raises(ValidationError, match="result_id"):
        ModelToolResult.from_dict({**result.to_dict(), "result_id": "tr_invalid"})


def test_completed_tool_turn_roundtrips_as_one_group():
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    calls = [
        _model_call(ordinal=index, provider_call_id=f"provider-call-{index}")
        for index in range(2)
    ]
    continuation = ProviderTurnContinuation.for_calls(
        provider_id="gemini",
        source_turn_id=f"{RUN_ID}:turn:1",
        source_sequence=1,
        proposal_id=proposal_id,
        calls=calls,
        payload={"format": "gemini-function-call-turn-v2", "parts": []},
    )
    turn = ModelToolTurn(
        proposal_id=proposal_id,
        source_turn_id=f"{RUN_ID}:turn:1",
        source_sequence=1,
        tool_calls=calls,
        tool_results=[
            _model_result(call, proposal_id=proposal_id, ordinal=index)
            for index, call in enumerate(calls)
        ],
        provider_continuation=continuation,
    )

    restored = ModelToolTurn.from_dict(
        json.loads(json.dumps(turn.to_dict(), sort_keys=True))
    )

    assert restored == turn
    assert len(restored.tool_calls) == len(restored.tool_results) == 2


def test_request_history_limit_counts_results_not_grouped_turns():
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    calls = [
        _model_call(ordinal=index, provider_call_id=f"provider-call-{index}")
        for index in range(16)
    ]
    full_turn = ModelToolTurn(
        proposal_id=proposal_id,
        source_turn_id=f"{RUN_ID}:turn:1",
        source_sequence=1,
        tool_calls=calls,
        tool_results=[
            _model_result(call, proposal_id=proposal_id, ordinal=index)
            for index, call in enumerate(calls)
        ],
    )
    request = ModelTurnRequest(
        run_id=RUN_ID,
        turn_id=f"{RUN_ID}:turn:3",
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        sequence=3,
        user_input="test grouped history",
        observation=None,
        available_tools=[],
        tool_turns=[full_turn],
    )
    assert sum(len(turn.tool_results) for turn in request.tool_turns) == 16

    extra_call = _model_call(
        turn_id=f"{RUN_ID}:turn:2",
        sequence=2,
        provider_call_id="provider-extra",
    )
    extra_turn = ModelToolTurn(
        proposal_id=f"{RUN_ID}:turn:2:proposal",
        source_turn_id=f"{RUN_ID}:turn:2",
        source_sequence=2,
        tool_calls=[extra_call],
        tool_results=[
            _model_result(
                extra_call,
                proposal_id=f"{RUN_ID}:turn:2:proposal",
                ordinal=0,
            )
        ],
    )
    with pytest.raises(ValidationError, match="bounded result limit"):
        ModelTurnRequest(
            run_id=RUN_ID,
            turn_id=f"{RUN_ID}:turn:3",
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            sequence=3,
            user_input="too much grouped history",
            observation=None,
            available_tools=[],
            tool_turns=[full_turn, extra_turn],
        )


def _tool_turn(*, sequence: int, call_count: int) -> ModelToolTurn:
    turn_id = f"{RUN_ID}:turn:{sequence}"
    proposal_id = f"{turn_id}:proposal"
    calls = [
        _model_call(
            turn_id=turn_id,
            sequence=sequence,
            ordinal=ordinal,
            provider_call_id=f"provider-{sequence}-{ordinal}",
        )
        for ordinal in range(call_count)
    ]
    return ModelToolTurn(
        proposal_id=proposal_id,
        source_turn_id=turn_id,
        source_sequence=sequence,
        tool_calls=calls,
        tool_results=[
            _model_result(call, proposal_id=proposal_id, ordinal=ordinal)
            for ordinal, call in enumerate(calls)
        ],
    )


def test_bounded_history_keeps_newest_whole_turns_in_chronological_order():
    older = _tool_turn(sequence=1, call_count=8)
    middle = _tool_turn(sequence=2, call_count=8)
    newest = _tool_turn(sequence=3, call_count=8)

    selected = select_bounded_model_tool_turns([older, middle, newest])

    assert [turn.source_sequence for turn in selected] == [2, 3]
    assert sum(len(turn.tool_results) for turn in selected) == 16


def test_bounded_history_rejects_an_oversized_newest_turn():
    call = _model_call()
    proposal_id = f"{RUN_ID}:turn:1:proposal"
    oversized_result = ModelToolResult.for_call(
        run_id=RUN_ID,
        proposal_id=proposal_id,
        ordinal=0,
        model_call=call,
        result=ToolResult(
            call_id=call.runtime_call.call_id,
            tool_id=call.runtime_call.tool_id,
            status="ok",
            value={"payload": "x" * (128 * 1024)},
        ),
    )
    oversized = ModelToolTurn(
        proposal_id=proposal_id,
        source_turn_id=f"{RUN_ID}:turn:1",
        source_sequence=1,
        tool_calls=[call],
        tool_results=[oversized_result],
    )

    with pytest.raises(ValidationError, match="newest tool turn"):
        select_bounded_model_tool_turns([oversized])
