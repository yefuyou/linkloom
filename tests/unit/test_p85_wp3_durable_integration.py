"""P8.5 WP-3 durable request and tool-budget regression tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.model_loop import SingleAgentModelLoop
from linkloom.runtime.models import RuntimeState, SourceContext, ToolExecutionRecord
from linkloom.tools.contracts import ToolCall, ToolDefinition, ToolError, ToolResult
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


@pytest.fixture
def artifact_root() -> Path:
    """Use the repository artifact area because this host protects pytest temp ACLs."""
    root = Path(".artifacts") / "p85-wp3-test-runs" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state(**changes) -> RuntimeState:
    values = {
        "schema_version": 1,
        "run_id": "run_p85_wp3",
        "thread_id": "thread_p85_wp3",
        "workflow": "ask",
        "status": "running",
        "step_seq": 1,
        "request_ref": "request_p85_wp3.json",
        "source": SourceContext(
            index_path=".artifacts/scan/vault_index.json",
            index_sha256="e" * 64,
            vault_root_fingerprint="fixture_p85_wp3",
        ),
    }
    values.update(changes)
    return RuntimeState(**values)


def _definition(
    *,
    version: str = "1",
    description: str = "Search notes.",
    tool_id: str = "search_notes",
) -> ToolDefinition:
    return ToolDefinition(
        tool_id=tool_id,
        version=version,
        description=description,
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
    )


def _runtime(executor, definition: ToolDefinition | None = None) -> ToolRuntime:
    registry = ToolRegistry()
    registry.register(definition or _definition(), executor)
    return ToolRuntime(registry)


def _policy(max_calls: int = 5) -> ToolPolicyEnforcer:
    return ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_notes"],
            denied_tool_ids=[],
            max_calls=max_calls,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )


def _proposal(
    call_id: str = "proposal_search",
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    agent_id: str | None = None,
    sequence: int = 1,
) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_id="search_notes",
        arguments={"query": "durable"},
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        sequence=sequence,
    )


def _interrupt_at_request_durable(
    artifact_root: Path,
    *,
    user_input: str = "input A",
    definition: ToolDefinition | None = None,
):
    snapshots = []
    store = ModelArtifactStore(artifact_root / "models")
    model = FakeModelAdapter([ModelAction.final("must not finish")])

    def checkpoint(snapshot: RuntimeState) -> None:
        snapshots.append(snapshot)
        if (
            snapshot.model_executions
            and snapshot.model_executions[-1].status == "request_durable"
        ):
            raise RuntimeError("simulated crash after request durability")

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: [], definition),
        _policy(),
        max_steps=1,
    ).run(
        state=_state(),
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input=user_input,
        available_tools=[definition or _definition()],
        artifact_store=store,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "failed"
    durable = next(
        snapshot
        for snapshot in snapshots
        if snapshot.model_executions[-1].status == "request_durable"
    )
    record = durable.model_executions[-1]
    assert record.request_ref is not None
    assert record.request_sha256 is not None
    return durable, record, store, model


def _request_path(store: ModelArtifactStore, reference: str) -> Path:
    return store.root.joinpath(*reference.split("/"))


def _ledger_with_statuses(
    statuses: list[str],
    *,
    run_id: str = "run_p85_wp3",
) -> ToolExecutionLedger:
    ledger = ToolExecutionLedger()
    for index, status in enumerate(statuses, start=1):
        call = ToolCall(
            call_id=f"historical-{index}",
            tool_id="search_notes",
            arguments={"query": f"historical-{index}"},
            run_id=run_id,
            task_id="task_p85_wp3",
            agent_id="retrieval_agent",
            sequence=index,
        )
        ledger.record_pending(call)
        if status == "completed":
            ledger.record_completed(
                call,
                ToolResult(
                    call_id=call.call_id,
                    tool_id=call.tool_id,
                    status="ok",
                    value=[{"evidence_id": f"ev-{index}"}],
                ),
            )
        elif status == "failed":
            ledger.record_failed(
                call,
                ToolError(
                    code="TOOL_EXECUTION_FAILED",
                    category="runtime",
                    message="Historical tool execution failed safely.",
                    safe_to_expose=True,
                ),
            )
        elif status != "pending":
            raise AssertionError(f"Unsupported test status: {status}")
    return ledger


def test_resume_rehydrates_persisted_request_instead_of_caller_values(artifact_root):
    durable, record, store, _ = _interrupt_at_request_durable(artifact_root)
    definition_v2 = _definition(version="2", description="Caller replacement.")
    requests = []

    def final_from_request(request):
        requests.append(request)
        return ModelAction.final(request.user_input)

    model = FakeModelAdapter([final_from_request])
    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: [], _definition()),
        _policy(),
        max_steps=1,
    ).resume(
        state=durable,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="input B",
        available_tools=[definition_v2],
        artifact_store=store,
    )

    assert result.status == "completed"
    assert result.final_answer == "input A"
    assert len(requests) == 1
    assert requests[0].user_input == "input A"
    assert [tool.to_dict() for tool in requests[0].available_tools] == [
        _definition().to_dict()
    ]
    assert record.request_sha256 is not None


def test_resume_rejects_tampered_request_artifact_without_model_call(artifact_root):
    durable, record, store, _ = _interrupt_at_request_durable(artifact_root)
    _request_path(store, record.request_ref).write_text(
        '{"tampered": true}\n',
        encoding="utf-8",
    )
    model = FakeModelAdapter([ModelAction.final("must not run")])

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: [], _definition()),
        _policy(),
        max_steps=1,
    ).resume(
        state=durable,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="input B",
        available_tools=[_definition(version="2")],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0


def test_resume_rejects_missing_request_artifact_without_model_call(artifact_root):
    durable, record, store, _ = _interrupt_at_request_durable(artifact_root)
    _request_path(store, record.request_ref).unlink()
    model = FakeModelAdapter([ModelAction.final("must not run")])

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: [], _definition()),
        _policy(),
        max_steps=1,
    ).resume(
        state=durable,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="input B",
        available_tools=[_definition(version="2")],
        artifact_store=store,
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "MODEL_LOOP_RUNTIME_FAILED"
    assert model.call_count == 0


def test_durable_request_hash_remains_bound_to_record(artifact_root):
    _, record, store, _ = _interrupt_at_request_durable(artifact_root)
    payload = store.read(record.request_ref, expected_sha256=record.request_sha256)

    assert payload["runtime_identity"]["run_id"] == "run_p85_wp3"
    assert payload["user_input"] == "input A"


def test_durable_resume_rehydrates_consumed_budget_before_new_tool_call(artifact_root):
    ledger = _ledger_with_statuses(["completed"] * 4)
    state = _state(tool_ledger=ledger.to_list())
    executed = []
    model = FakeModelAdapter(
        [
            ModelAction.tool(_proposal("new-proposal")),
            ModelAction.final("done"),
        ]
    )

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executed.append(arguments) or []),
        _policy(max_calls=5),
        max_steps=2,
    ).resume(
        state=state,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="find durable evidence",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
    )

    assert result.status == "completed"
    assert executed == [{"query": "durable"}]


def test_durable_resume_at_limit_rejects_without_executor(artifact_root):
    ledger = _ledger_with_statuses(["completed"] * 5)
    state = _state(tool_ledger=ledger.to_list())
    executed = []
    model = FakeModelAdapter([ModelAction.tool(_proposal("blocked-proposal"))])

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executed.append(arguments) or []),
        _policy(max_calls=5),
        max_steps=1,
    ).resume(
        state=state,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="find durable evidence",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
    )

    assert result.status == "budget_exhausted"
    assert executed == []
    assert result.state.tool_ledger == ledger.to_list()


def test_durable_budget_rehydration_counts_pending_completed_and_failed_once(artifact_root):
    ledger = _ledger_with_statuses(["pending", "completed", "failed"])
    state = _state(tool_ledger=ledger.to_list())
    model = FakeModelAdapter([ModelAction.final("done")])

    loop = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: []),
        _policy(max_calls=5),
        max_steps=1,
    )
    result = loop.resume(
        state=state,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="find durable evidence",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
    )

    assert result.status == "completed"
    assert loop.policy_enforcer.call_count == 3
    assert result.state.tool_ledger == ledger.to_list()


def test_durable_ledger_duplicate_records_fail_before_model(artifact_root):
    record = _ledger_with_statuses(["completed"]).to_list()[0]

    with pytest.raises(ValidationError):
        _state(
            tool_ledger=[
                ToolExecutionRecord.from_dict(record.to_dict()),
                ToolExecutionRecord.from_dict(record.to_dict()),
            ]
        )


def test_pending_checkpoint_failure_does_not_commit_budget_or_run_executor_after_restart():
    executed = []
    durable_records = []
    call = _proposal(
        "pending-checkpoint-failure",
        run_id="run_p85_wp3",
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
    )
    policy = _policy()

    def checkpoint(_ledger):
        raise RuntimeError("simulated checkpoint failure")

    result = _runtime(lambda arguments: executed.append(arguments) or []).execute(
        call,
        policy,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.code == "TOOL_PENDING_CHECKPOINT_FAILED"
    assert policy.call_count == 0
    assert executed == []

    # The failed callback did not establish a durable pending snapshot.  A
    # fresh policy reconstructed from the last durable checkpoint must not
    # count the in-memory pending record.
    restarted_policy = _policy()
    restarted_policy.rehydrate_from_ledger(
        durable_records,
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
    )
    assert restarted_policy.call_count == 0


def test_durable_pending_commits_before_executor_and_rehydrates_one_call():
    events = []
    durable_pending = []
    call = _proposal(
        "durable-pending-before-executor",
        run_id="run_p85_wp3",
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
    )
    policy = _policy()

    def checkpoint(ledger):
        events.append("checkpoint")
        if not durable_pending:
            assert policy.call_count == 0
            durable_pending.extend(ledger.to_list())

    def executor(arguments):
        events.append("executor")
        assert policy.call_count == 1
        return []

    result = _runtime(executor).execute(
        call,
        policy,
        checkpoint_callback=checkpoint,
    )

    assert result.status == "ok"
    assert events[:2] == ["checkpoint", "executor"]
    assert durable_pending and durable_pending[0].status == "pending"
    assert policy.call_count == 1

    restarted_policy = _policy()
    restarted_policy.rehydrate_from_ledger(
        durable_pending,
        run_id=call.run_id,
        task_id=call.task_id,
        agent_id=call.agent_id,
    )
    assert restarted_policy.call_count == 1


@pytest.mark.parametrize("executor_mode", ["completed", "failed"])
def test_completed_or_failed_call_id_consumes_exactly_one_budget_unit(executor_mode):
    executions = []
    policy = _policy(max_calls=2)
    call = _proposal(
        f"terminal-{executor_mode}",
        run_id="run_p85_wp3",
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
    )

    def executor(arguments):
        executions.append(arguments)
        if executor_mode == "failed":
            raise RuntimeError("expected executor failure")
        return []

    runtime = _runtime(executor)
    first = runtime.execute(call, policy)
    second = runtime.execute(call, policy)

    assert first.status == ("ok" if executor_mode == "completed" else "error")
    assert second.status == "error"
    assert second.error is not None
    assert second.error.code == "TOOL_LEDGER_CONFLICT"
    assert policy.call_count == 1
    assert len(executions) == 1


def test_rehydrate_counts_only_current_run_task_and_agent_scope():
    ledger = ToolExecutionLedger()
    scoped_calls = [
        ("same-scope", "run_p85_wp3", "task_p85_wp3", "retrieval_agent"),
        ("other-task", "run_p85_wp3", "other-task", "retrieval_agent"),
        ("other-agent", "run_p85_wp3", "task_p85_wp3", "curator_agent"),
        ("other-run", "other-run", "task_p85_wp3", "retrieval_agent"),
    ]
    for sequence, (call_id, run_id, task_id, agent_id) in enumerate(scoped_calls, start=1):
        ledger.record_pending(
            _proposal(
                call_id,
                run_id=run_id,
                task_id=task_id,
                agent_id=agent_id,
                sequence=sequence,
            )
        )

    policy = _policy(max_calls=1)
    policy.rehydrate_from_ledger(
        ledger.to_list(),
        run_id="run_p85_wp3",
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
    )

    assert policy.call_count == 1
    assert policy.remaining_calls == 0


def test_durable_model_loop_ignores_other_task_and_agent_budget_records(artifact_root):
    ledger = ToolExecutionLedger()
    for sequence, (call_id, task_id, agent_id) in enumerate(
        [
            ("other-task-record", "other-task", "retrieval_agent"),
            ("other-agent-record", "task_p85_wp3", "curator_agent"),
        ],
        start=1,
    ):
        ledger.record_pending(
            _proposal(
                call_id,
                run_id="run_p85_wp3",
                task_id=task_id,
                agent_id=agent_id,
                sequence=sequence,
            )
        )

    executed = []
    state = _state(tool_ledger=ledger.to_list())
    model = FakeModelAdapter(
        [
            ModelAction.tool(_proposal("current-scope-proposal")),
            ModelAction.final("done"),
        ]
    )

    result = SingleAgentModelLoop(
        model,
        _runtime(lambda arguments: executed.append(arguments) or []),
        _policy(max_calls=1),
        max_steps=2,
    ).resume(
        state=state,
        task_id="task_p85_wp3",
        agent_id="retrieval_agent",
        user_input="find durable evidence",
        available_tools=[_definition()],
        artifact_store=ModelArtifactStore(artifact_root / "models"),
    )

    assert result.status == "completed"
    assert executed == [{"query": "durable"}]


def test_rehydrate_rejects_duplicate_call_ids_without_silent_deduplication():
    record = _ledger_with_statuses(["pending"]).to_list()[0]
    policy = _policy()

    with pytest.raises(ValidationError) as exc_info:
        policy.rehydrate_from_ledger(
            [record, ToolExecutionRecord.from_dict(record.to_dict())],
            run_id="run_p85_wp3",
            task_id="task_p85_wp3",
            agent_id="retrieval_agent",
        )

    assert exc_info.value.details["reason"] == "duplicate_call_id"
    assert policy.call_count == 0


def test_runtime_state_rejects_wrong_run_ledger_record():
    record = _ledger_with_statuses(["pending"], run_id="other-run").to_list()[0]

    with pytest.raises(ValidationError) as exc_info:
        _state(tool_ledger=[record])

    assert "another run" in str(exc_info.value)
