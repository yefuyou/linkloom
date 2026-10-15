"""Offline DeepSeek multi-turn and cold-resume acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.tools.contracts import ToolDefinition
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


class SequencedDeepSeekClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def create_chat_completion(self, **request):
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


def _tool_response(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": f"response-{call_id}",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


def _final_response(final: str) -> dict:
    return {
        "id": "response-final",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": final},
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
    }


def _final_json() -> str:
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": "Use the verified option.",
                "status": "approved",
                "evidence_refs": ["ev_deepseek_read"],
            },
            "rationale": [],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "none",
                "statement": None,
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": ["ev_deepseek_read"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _definition(tool_id: str) -> ToolDefinition:
    if tool_id == "search_notes":
        schema = {
            "type": "object",
            "required": ["query", "limit"],
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        }
    else:
        schema = {
            "type": "object",
            "required": ["evidence_id"],
            "properties": {"evidence_id": {"type": "string"}},
            "additionalProperties": False,
        }
    return ToolDefinition(
        tool_id=tool_id,
        version="1",
        description=f"Offline {tool_id}.",
        input_schema=schema,
        output_schema={"type": ["array", "object"]},
    )


def _evidence(evidence_id: str, quote: str) -> dict:
    return {
        "status": "verified",
        "evidence_id": evidence_id,
        "relative_path": "notes/deepseek.md",
        "content_sha256": "a" * 64,
        "quote": quote,
        "quote_sha256": "b" * 64,
        "line_start": 1,
        "line_end": 1,
    }


def _runtime(executor_calls: list[tuple[str, dict]]) -> ToolRuntime:
    registry = ToolRegistry()

    def search(arguments: dict):
        executor_calls.append(("search_notes", arguments))
        return [_evidence("ev_deepseek_search", "Search result")]

    def read(arguments: dict):
        executor_calls.append(("read_verified_note", arguments))
        return _evidence("ev_deepseek_read", "Read result")

    registry.register(_definition("search_notes"), search)
    registry.register(_definition("read_verified_note"), read)
    return ToolRuntime(registry)


def _policy() -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_notes", "read_verified_note"],
            denied_tool_ids=[],
            max_calls=4,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _state() -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        run_id="run_deepseek_durable",
        thread_id="thread_deepseek_durable",
        workflow="team_decision",
        status="running",
        step_seq=1,
        request_ref="request_deepseek_durable.json",
        source=SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="c" * 64,
            vault_root_fingerprint="fixture_deepseek_durable",
        ),
    )


def _loop(client: SequencedDeepSeekClient, executor_calls: list[tuple[str, dict]]):
    return SingleAgentModelLoop(
        DeepSeekProviderAdapter(
            client,
            model_id="deepseek-flash",
            json_output=True,
        ),
        _runtime(executor_calls),
        _policy(),
        max_steps=4,
    )


@pytest.fixture
def artifact_root() -> Path:
    root = Path(".artifacts") / "deepseek-offline" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run(loop, store, checkpoint_callback):
    return loop.run(
        state=_state(),
        task_id="task_deepseek_durable",
        agent_id="retrieval_agent",
        user_input="Use tools, then return the TeamDecision JSON contract.",
        available_tools=[_definition("search_notes"), _definition("read_verified_note")],
        artifact_store=store,
        checkpoint_callback=checkpoint_callback,
    )


def test_deepseek_search_read_final_runs_through_production_model_loop(artifact_root):
    client = SequencedDeepSeekClient(
        [
            _tool_response(
                "call_search_1",
                "search_notes",
                {"query": "DeepSeek durable tool loop", "limit": 3},
            ),
            _tool_response(
                "call_read_1",
                "read_verified_note",
                {"evidence_id": "ev_deepseek_search"},
            ),
            _final_response(_final_json()),
        ]
    )
    executor_calls: list[tuple[str, dict]] = []
    snapshots: list[RuntimeState] = []

    result = _run(
        _loop(client, executor_calls),
        ModelArtifactStore(artifact_root / "models"),
        lambda state: snapshots.append(RuntimeState.from_dict(state.to_dict())),
    )

    assert result.status == "completed"
    assert TeamDecisionResult.from_grounded_final(
        result.final_answer,
        ["ev_deepseek_search", "ev_deepseek_read"],
    ).decision["value"] == "Use the verified option."
    assert [name for name, _ in executor_calls] == [
        "search_notes",
        "read_verified_note",
    ]
    assert len(client.requests) == 3
    assert [message["role"] for message in client.requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert [message["role"] for message in client.requests[2]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert client.requests[1]["messages"][3]["tool_call_id"] == "call_search_1"
    assert client.requests[2]["messages"][3]["tool_call_id"] == "call_search_1"
    assert client.requests[2]["messages"][5]["tool_call_id"] == "call_read_1"
    assert all(request["thinking"] == {"type": "disabled"} for request in client.requests)
    assert all(request["response_format"] == {"type": "json_object"} for request in client.requests)
    assert snapshots


def test_deepseek_tool_result_survives_cold_resume_without_tool_reexecution(
    artifact_root,
):
    first_client = SequencedDeepSeekClient(
        [
            _tool_response(
                "call_search_resume",
                "search_notes",
                {"query": "resume DeepSeek", "limit": 2},
            )
        ]
    )
    executor_calls: list[tuple[str, dict]] = []
    store = ModelArtifactStore(artifact_root / "models")

    def stop_before_second_provider_turn(state: RuntimeState) -> None:
        latest = state.model_executions[-1]
        if latest.sequence == 2 and latest.status == "request_durable":
            raise RuntimeError("offline simulated restart")

    interrupted = _run(
        _loop(first_client, executor_calls),
        store,
        stop_before_second_provider_turn,
    )

    assert interrupted.status == "failed"
    assert interrupted.state.model_executions[-1].status == "tool_results_durable"
    assert interrupted.state.model_executions[-1].normalized_proposal is not None
    restored = RuntimeState.from_dict(
        json.loads(json.dumps(interrupted.state.to_dict(), sort_keys=True))
    )
    second_client = SequencedDeepSeekClient(
        [
            _tool_response(
                "call_read_resume",
                "read_verified_note",
                {"evidence_id": "ev_deepseek_search"},
            ),
            _final_response(_final_json()),
        ]
    )

    resumed = _loop(second_client, executor_calls).resume(
        state=restored,
        task_id="task_deepseek_durable",
        agent_id="retrieval_agent",
        user_input="Use tools, then return the TeamDecision JSON contract.",
        available_tools=[_definition("search_notes"), _definition("read_verified_note")],
        artifact_store=store,
        checkpoint_callback=lambda state: None,
    )

    assert resumed.status == "completed"
    assert TeamDecisionResult.from_grounded_final(
        resumed.final_answer,
        ["ev_deepseek_search", "ev_deepseek_read"],
    )
    assert [name for name, _ in executor_calls] == [
        "search_notes",
        "read_verified_note",
    ]
    assert len(first_client.requests) == 1
    assert len(second_client.requests) == 2
    resumed_messages = second_client.requests[0]["messages"]
    assert resumed_messages[2]["tool_calls"][0]["id"] == "call_search_resume"
    assert resumed_messages[3]["tool_call_id"] == "call_search_resume"
    final_messages = second_client.requests[1]["messages"]
    assert [message["role"] for message in final_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert final_messages[2]["tool_calls"][0]["id"] == "call_search_resume"
    assert final_messages[3]["tool_call_id"] == "call_search_resume"
    assert final_messages[4]["tool_calls"][0]["id"] == "call_read_resume"
    assert final_messages[5]["tool_call_id"] == "call_read_resume"
    assert "provider_continuation" not in json.dumps(restored.to_dict())
