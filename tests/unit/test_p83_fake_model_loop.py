"""P8.3 Phase B tests for the isolated deterministic fake-model loop."""

from __future__ import annotations

from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolCall, ToolDefinition
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.model_loop import SingleAgentModelLoop


def _state() -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id="run_p83_model",
        thread_id="thread_p83_model",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request_p83_model.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="b" * 64,
            vault_root_fingerprint="root_p83_model",
        ),
    )


def _definitions() -> tuple[ToolDefinition, ToolDefinition]:
    read = ToolDefinition(
        tool_id="read_verified_note",
        version="1",
        description="Read a verified note.",
        input_schema={
            "type": "object",
            "required": ["note_ref"],
            "properties": {"note_ref": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    )
    search = ToolDefinition(
        tool_id="search_notes",
        version="1",
        description="Search verified notes.",
        input_schema={
            "type": "object",
            "required": ["query", "source_context", "limit"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "source_context": {"type": "object"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )
    return read, search


def _runtime() -> tuple[ToolRuntime, ToolPolicyEnforcer, tuple[ToolDefinition, ToolDefinition]]:
    read, search = _definitions()
    registry = ToolRegistry()
    registry.register(read, lambda arguments: {"note_ref": arguments["note_ref"], "verified": True})
    registry.register(search, lambda arguments: [{"evidence_id": "ev_after_model"}])
    runtime = ToolRuntime(registry)
    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["read_verified_note", "search_notes"],
            denied_tool_ids=[],
            max_calls=4,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )
    return runtime, policy, (read, search)


def _proposal(call_id: str, tool_id: str, arguments: dict) -> ToolCall:
    return ToolCall(call_id=call_id, tool_id=tool_id, arguments=arguments)


def test_model_actions_not_python_order_drive_tool_sequence_and_observation_context():
    runtime, policy, definitions = _runtime()
    model = FakeModelAdapter(
        [
            ModelAction.tool(
                _proposal("proposal_read", "read_verified_note", {"note_ref": "note_a"})
            ),
            lambda request: (
                ModelAction.tool(
                    _proposal(
                        "proposal_search",
                        "search_notes",
                        {"query": "after read", "source_context": {}, "limit": 2},
                    )
                )
                if request.observation is not None
                and request.observation.value["verified"] is True
                else ModelAction.final("unexpected observation")
            ),
            lambda request: ModelAction.final(
                f"done:{request.observation.value[0]['evidence_id']}"
            ),
        ]
    )
    snapshots = []
    loop = SingleAgentModelLoop(model, runtime, policy, max_steps=4)

    result = loop.run(
        state=_state(),
        task_id="task_p83_model",
        agent_id="retrieval_agent",
        user_input="find related notes",
        available_tools=list(definitions),
        checkpoint_callback=lambda state: snapshots.append(state.to_dict()),
    )

    assert result.status == "completed"
    assert result.final_answer == "done:ev_after_model"
    assert [request.observation is None for request in model.requests] == [True, False, False]
    assert [record.tool_id for record in result.state.tool_ledger] == [
        "read_verified_note",
        "search_notes",
    ]
    assert len({record.call_id for record in result.state.tool_ledger}) == 2
    assert [turn.status for turn in result.state.turns] == ["completed", "completed", "completed"]
    assert result.state.termination.status == "completed"
    assert any(
        [record["status"] for record in snapshot["tool_ledger"]] == ["pending"]
        for snapshot in snapshots
    )


def test_tool_result_error_is_passed_to_next_model_turn_as_observation():
    runtime, policy, definitions = _runtime()
    model = FakeModelAdapter(
        [
            ModelAction.tool(
                _proposal("proposal_bad", "read_verified_note", {"note_ref": ""})
            ),
            lambda request: ModelAction.final(
                f"handled:{request.observation.error.code}"
            ),
        ]
    )
    result = SingleAgentModelLoop(model, runtime, policy, max_steps=2).run(
        state=_state(),
        task_id="task_p83_model",
        agent_id="retrieval_agent",
        user_input="handle malformed ref",
        available_tools=list(definitions),
    )

    assert result.status == "completed"
    assert result.final_answer == "handled:TOOL_INVALID_ARGUMENTS"
    assert result.state.tool_ledger == []
    assert policy.call_count == 0


def test_runtime_owns_max_steps_termination_when_model_never_finishes():
    runtime, policy, definitions = _runtime()
    model = FakeModelAdapter(
        [
            ModelAction.tool(
                _proposal("proposal_read_1", "read_verified_note", {"note_ref": "note_a"})
            ),
            ModelAction.tool(
                _proposal("proposal_read_2", "read_verified_note", {"note_ref": "note_b"})
            ),
            ModelAction.tool(
                _proposal("proposal_read_3", "read_verified_note", {"note_ref": "note_c"})
            ),
        ]
    )

    result = SingleAgentModelLoop(model, runtime, policy, max_steps=2).run(
        state=_state(),
        task_id="task_p83_model",
        agent_id="retrieval_agent",
        user_input="keep working",
        available_tools=list(definitions),
    )

    assert result.status == "budget_exhausted"
    assert result.error.code == "MODEL_MAX_STEPS_EXCEEDED"
    assert result.state.termination.status == "budget_exhausted"
    assert len(model.requests) == 2
    assert policy.call_count == 2

