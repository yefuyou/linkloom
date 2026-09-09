"""M0.3 production-path RED tests for model-driven retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pytest

from linkloom.agents.base import AgentTask
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import (
    PolicySnapshot,
    RunRequest,
    RuntimeState,
    SourceContext,
    TerminationState,
)
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


SEARCH_ARGS = {
    "query": "model-driven",
    "source_context": {"model_owned": "m03-source-context"},
    "limit": 2,
}


@pytest.fixture
def m03_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"m03-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def _synthetic_fixture(test_root: Path) -> tuple[Path, Path]:
    test_root.mkdir(parents=True, exist_ok=True)
    vault_root = test_root / "vault"
    vault_root.mkdir()
    (vault_root / "alpha.md").write_text(
        "# Alpha decision\n\nThe model-driven retrieval fixture has shared evidence.\n",
        encoding="utf-8",
    )
    (vault_root / "beta.md").write_text(
        "# Beta decision\n\nThe model-driven retrieval fixture has a second evidence item.\n",
        encoding="utf-8",
    )
    scan = scan_vault(vault_root, test_root / "scan")
    return vault_root, scan.index_path


def _run_engine(
    test_root: Path,
    model: FakeModelAdapter,
    *,
    request_id: str,
    query: str = "model-driven",
    max_steps: int = 12,
    max_provider_requests: int = 8,
):
    vault_root, index_path = _synthetic_fixture(test_root)
    checkpoint_root = test_root / "checkpoints"
    trace_root = test_root / "traces"
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_root,
        trace_dir=trace_root,
        checkpointer=InMemoryCheckpointer(),
        model=model,
    )
    status = engine.start_multi_agent(
        RunRequest(
            request_id=request_id,
            workflow="ask",
            query=query,
            max_steps=max_steps,
            max_provider_requests=max_provider_requests,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)
    result = None
    if status.result_ref:
        result = json.loads((checkpoint_root / status.result_ref).read_text(encoding="utf-8"))
    return status, state, result, trace_root, model


def _search_action(arguments: dict[str, Any] = SEARCH_ARGS) -> ModelAction:
    return ModelAction.tool(
        ToolCall(
            call_id="proposal_search",
            tool_id="search_notes",
            arguments=dict(arguments),
        )
    )


def _read_action(note_ref: str) -> ModelAction:
    return ModelAction.tool(
        ToolCall(
            call_id="proposal_read",
            tool_id="read_verified_note",
            arguments={"note_ref": note_ref},
        )
    )


def _policy_a() -> FakeModelAdapter:
    def search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _search_action()

    def final_after_search(request: ModelTurnRequest) -> ModelAction:
        assert request.previous_tool_call is not None
        assert request.previous_tool_call.tool_id == "search_notes"
        assert request.previous_tool_call.arguments == SEARCH_ARGS
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert request.observation.value
        return ModelAction.final("final after search")

    return FakeModelAdapter([search, final_after_search])


def _policy_b() -> FakeModelAdapter:
    def search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _search_action()

    def read_second_result(request: ModelTurnRequest) -> ModelAction:
        assert request.previous_tool_call is not None
        assert request.previous_tool_call.tool_id == "search_notes"
        assert request.previous_tool_call.arguments == SEARCH_ARGS
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert isinstance(request.observation.value, list)
        assert len(request.observation.value) >= 2
        second_ref = request.observation.value[1]["evidence_id"]
        return _read_action(second_ref)

    def final_after_read(request: ModelTurnRequest) -> ModelAction:
        assert request.previous_tool_call is not None
        assert request.previous_tool_call.tool_id == "read_verified_note"
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert request.observation.value["evidence_id"] == request.previous_tool_call.arguments["note_ref"]
        return ModelAction.final("final after selected read")

    return FakeModelAdapter([search, read_second_result, final_after_read])


def _trajectory(state: Any) -> list[str]:
    return [record.tool_id for record in state.tool_ledger]


def test_same_production_path_allows_policy_selected_trajectories(m03_root: Path):
    status_a, state_a, result_a, trace_a, model_a = _run_engine(
        m03_root / "policy_a",
        _policy_a(),
        request_id="m03-policy-a",
    )
    status_b, state_b, result_b, trace_b, model_b = _run_engine(
        m03_root / "policy_b",
        _policy_b(),
        request_id="m03-policy-b",
    )

    assert status_a.status == "completed"
    assert status_b.status == "completed"
    assert state_a is not None and state_b is not None
    assert result_a is not None and result_b is not None
    assert model_a.call_count == 2
    assert model_b.call_count == 3
    assert _trajectory(state_a) == ["search_notes"]
    assert _trajectory(state_b) == ["search_notes", "read_verified_note"]
    assert result_a["tool_ledger"] == [record.to_dict() for record in state_a.tool_ledger]
    assert result_b["tool_ledger"] == [record.to_dict() for record in state_b.tool_ledger]
    assert len(state_a.model_executions) == 2
    assert len(state_b.model_executions) == 3
    assert [record.status for record in state_a.model_executions] == [
        "tool_result_durable",
        "completed",
    ]
    assert [record.status for record in state_b.model_executions] == [
        "tool_result_durable",
        "tool_result_durable",
        "completed",
    ]
    assert trace_a.exists() and trace_b.exists()


def test_model_arguments_and_observations_remain_exact_on_production_path(m03_root: Path):
    model = _policy_b()
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-argument-integrity",
    )

    assert status.status == "completed"
    assert state is not None and result is not None
    search_record, read_record = state.tool_ledger
    assert search_record.arguments == SEARCH_ARGS
    assert read_record.arguments["note_ref"] == model.requests[1].observation.value[1]["evidence_id"]
    assert model.requests[1].observation.to_dict() == search_record.result
    assert model.requests[2].observation.to_dict() == read_record.result
    assert result["evidence"] == [read_record.result["value"]["evidence_id"]]


def test_successful_empty_search_is_observed_before_exact_m01_completion(m03_root: Path):
    observations = []

    def search(request: ModelTurnRequest) -> ModelAction:
        return _search_action({"query": "no-match", "source_context": {}, "limit": 1})

    def final_after_empty(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        observations.append(request.observation)
        assert request.observation.status == "ok"
        assert request.observation.value == []
        return ModelAction.final("no matching notes")

    model = FakeModelAdapter([search, final_after_empty])
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-empty-search",
        query="no-match",
    )

    assert status.status == "completed"
    assert state is not None and result is not None
    assert len(observations) == 1
    assert state.tool_ledger[0].status == "completed"
    assert state.tool_ledger[0].result["value"] == []
    assert result["status"] == "completed"
    assert result["evidence"] == []
    assert result["fallback_used"] is False
    assert result["errors"] == []
    assert result["agent_tasks"][0]["status"] == "completed"
    assert result["tool_ledger"][0]["status"] == "completed"


def test_prior_search_failure_remains_authoritative_after_later_empty_search(
    m03_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    search_attempts = 0

    def search_failure_then_empty(
        self: RuntimeAgentAdapter,
        query: str,
        source_context: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        nonlocal search_attempts
        search_attempts += 1
        if search_attempts == 1:
            raise RuntimeError("internal search failure at /private/vault.md")
        return []

    monkeypatch.setattr(RuntimeAgentAdapter, "_search_notes", search_failure_then_empty)

    def initial_search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _search_action()

    def retry_after_failure(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.error is not None
        assert request.observation.error.code == "TOOL_EXECUTION_FAILED"
        return _search_action({"query": "no-match", "source_context": {}, "limit": 1})

    def final_after_empty(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert request.observation.value == []
        return ModelAction.final("final after failed search and empty retry")

    model = FakeModelAdapter([initial_search, retry_after_failure, final_after_empty])
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-search-failure-then-empty",
    )

    assert status.status == "completed"
    assert state is not None and result is not None
    assert search_attempts == 2
    assert [record.tool_id for record in state.tool_ledger] == [
        "search_notes",
        "search_notes",
    ]
    assert [record.status for record in state.tool_ledger] == ["failed", "completed"]
    assert state.tool_ledger[0].error["code"] == "TOOL_EXECUTION_FAILED"
    assert state.tool_ledger[1].result["value"] == []
    assert result["agent_tasks"][0]["status"] == "failed"
    assert result["fallback_used"] is True
    assert result["evidence"] == []
    assert "fallback_used" in result["errors"]
    assert "NO_VERIFIED_EVIDENCE" not in json.dumps(result, ensure_ascii=False)


def test_prior_read_failure_remains_authoritative_after_later_empty_search(
    m03_root: Path,
):
    run_id = "m03-read-failure-then-empty"
    search_attempts = 0
    read_attempts = 0

    def search_then_empty(
        query: str,
        source_context: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        nonlocal search_attempts
        search_attempts += 1
        if search_attempts == 1:
            return [{"evidence_id": "note_ref_0"}]
        return []

    def read_failure(ref: str) -> dict[str, Any]:
        nonlocal read_attempts
        read_attempts += 1
        raise RuntimeError("internal read failure at /private/vault.md")

    def initial_search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is None
        return _search_action()

    def read_after_search(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert request.observation.value
        return _read_action(request.observation.value[0]["evidence_id"])

    def search_after_read_failure(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.error is not None
        assert request.observation.error.code == "TOOL_EXECUTION_FAILED"
        return _search_action({"query": "no-match", "source_context": {}, "limit": 1})

    def final_after_empty(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.status == "ok"
        assert request.observation.value == []
        return ModelAction.final("final after failed read and empty search")

    model = FakeModelAdapter(
        [
            initial_search,
            read_after_search,
            search_after_read_failure,
            final_after_empty,
        ]
    )
    state = RuntimeState(
        schema_version=1,
        run_id=run_id,
        thread_id="thread_m03_read_failure_then_empty",
        workflow="ask",
        status="running",
        step_seq=1,
        request_ref="request.json",
        source=SourceContext(
            index_path="vault_index.json",
            index_sha256="0" * 64,
            vault_root_fingerprint="fixture-root",
        ),
        policy=PolicySnapshot(max_steps=8, max_provider_requests=8),
        termination=TerminationState(status="running", sequence=1),
        created_at="2026-08-31T00:00:00Z",
        updated_at="2026-08-31T00:00:00Z",
    )
    snapshots: list[RuntimeState] = []
    task = AgentTask(
        task_id="task_m03_read_failure_then_empty",
        run_id=run_id,
        parent_task_id=None,
        parent_agent_id="coordinator",
        agent_id="retrieval_agent",
        workflow="ask",
        input_refs=["request.md"],
        allowed_tool_ids=["search_notes", "read_verified_note"],
        max_steps=4,
        deadline_ms=5000,
        status="queued",
        attempt=0,
        created_at="2026-08-31T00:00:00Z",
    )
    runtime = create_retrieval_tool_runtime(search_then_empty, read_failure)
    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_notes", "read_verified_note"],
            denied_tool_ids=[
                "write_file",
                "rename_file",
                "read_gold",
                "raw_filesystem",
            ],
            max_calls=4,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )
    agent = RetrievalAgent(
        model=model,
        initial_state=state,
        artifact_store=ModelArtifactStore(m03_root / "models"),
        state_checkpoint_callback=snapshots.append,
    )
    result = agent.execute(
        task=task,
        query="model-driven",
        source_context={},
        policy_enforcer=policy,
        tool_runtime=runtime,
    )

    assert snapshots
    final_state = snapshots[-1]
    assert search_attempts == 2
    assert read_attempts == 1
    assert [record.tool_id for record in final_state.tool_ledger] == [
        "search_notes",
        "read_verified_note",
        "search_notes",
    ]
    assert [record.status for record in final_state.tool_ledger] == [
        "completed",
        "failed",
        "completed",
    ]
    assert final_state.tool_ledger[1].error["code"] == "TOOL_EXECUTION_FAILED"
    assert final_state.tool_ledger[2].result["value"] == []
    assert result.status == "failed"
    assert result.output_refs == []
    assert result.error is not None
    assert result.error["code"] == "TOOL_EXECUTION_FAILED"
    assert "read_verified_note:TOOL_EXECUTION_FAILED" in result.warnings
    assert "NO_VERIFIED_EVIDENCE" not in json.dumps(result.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize(
    "policy_factory",
    [_policy_a, _policy_b],
)
def test_model_policy_is_the_only_trajectory_switch(
    m03_root: Path,
    policy_factory: Callable[[], FakeModelAdapter],
):
    model = policy_factory()
    _, state, _, _, _ = _run_engine(
        m03_root,
        model,
        request_id=f"m03-policy-{model.call_count}",
    )
    assert state is not None
    assert state.termination is not None
    assert state.model_executions[-1].normalized_action["kind"] == "final"


def test_missing_model_fails_closed_without_hidden_deterministic_fallback(m03_root: Path):
    vault_root, index_path = _synthetic_fixture(m03_root)
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=m03_root / "checkpoints",
        checkpointer=InMemoryCheckpointer(),
    )

    status = engine.start_multi_agent(
        RunRequest(
            request_id="m03-no-model",
            workflow="ask",
            query="model-driven",
            max_provider_requests=8,
            dry_run=True,
        )
    )

    assert status.status == "failed"
    assert status.error is not None
    assert status.error["code"] == "MODEL_NOT_CONFIGURED"
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is None or state.tool_ledger == []


def test_runtime_refusal_returns_to_model_without_fabricating_ledger(m03_root: Path):
    def unknown_tool(request: ModelTurnRequest) -> ModelAction:
        return ModelAction.tool(
            ToolCall(
                call_id="proposal_unknown",
                tool_id="unknown_tool",
                arguments={},
            )
        )

    def final_after_refusal(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        assert request.observation.error is not None
        assert request.observation.error.code == "TOOL_UNKNOWN"
        return ModelAction.final("final after runtime refusal")

    model = FakeModelAdapter([unknown_tool, final_after_refusal])
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-runtime-refusal",
    )

    assert status.status == "completed"
    assert state is not None and result is not None
    assert model.call_count == 2
    assert state.tool_ledger == []
    assert result["tool_ledger"] == []
    assert result["fallback_used"] is True
    assert result["agent_tasks"][0]["status"] == "failed"


def test_model_request_budget_stops_without_an_extra_model_or_tool_call(m03_root: Path):
    def always_search(request: ModelTurnRequest) -> ModelAction:
        return _search_action()

    model = FakeModelAdapter([always_search, always_search, always_search])
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-model-budget",
        max_steps=4,
        max_provider_requests=2,
    )

    assert status.status == "completed"
    assert state is not None and result is not None
    assert model.call_count == 2
    assert len(state.tool_ledger) == 2
    assert result["fallback_used"] is True
    assert result["agent_tasks"][0]["status"] == "failed"


def test_zero_provider_budget_fails_before_model_invocation(m03_root: Path):
    model = _policy_a()
    status, state, result, _, _ = _run_engine(
        m03_root,
        model,
        request_id="m03-zero-budget",
        max_provider_requests=0,
    )

    assert status.status == "failed"
    assert status.error is not None
    assert status.error["code"] == "MODEL_PROVIDER_BUDGET_EXHAUSTED"
    assert model.call_count == 0
    assert state is None
    assert result is None
