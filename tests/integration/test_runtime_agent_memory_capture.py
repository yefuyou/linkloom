from __future__ import annotations

import json
from pathlib import Path

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
from linkloom.tools.contracts import ToolCall
from linkloom.loader import calculate_fingerprint


class SimulatedProcessCrash(BaseException):
    pass


class CrashAfterDurableFinal(SQLiteCheckpointer):
    def save(self, state, checkpoint_id=None):
        saved = super().save(state, checkpoint_id)
        latest = state.model_executions[-1] if state.model_executions else None
        if (
            latest is not None
            and latest.status == "response_durable"
            and latest.normalized_action.get("kind") == "final"
        ):
            raise SimulatedProcessCrash("durable Team Decision final")
        return saved


def _decision_json(evidence_ref: str) -> str:
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": "Acme",
                "status": "approved",
                "evidence_refs": [evidence_ref],
            },
            "rationale": [
                {"point": "The observed note names Acme.", "evidence_refs": [evidence_ref]}
            ],
            "rejected_alternatives": [],
            "actions": [],
            "unresolved_items": [],
            "uncertainty": {
                "status": "none",
                "statement": None,
                "unknown_fields": [],
                "evidence_refs": [],
            },
            "evidence_refs": [evidence_ref],
        }
    )


def _team_decision_model(
    *, fabricated_ref: bool = False, ungrounded_value: bool = False
) -> FakeModelAdapter:
    step_errors: list[str] = []

    def search(request: ModelTurnRequest) -> ModelAction:
        return ModelAction.tool(
            ToolCall(
                call_id=f"memory-capture-search-{request.sequence}",
                tool_id="search_notes",
                arguments={
                    "query": "Acme supplier decision",
                    "source_context": {"intent": "supplier decision"},
                    "limit": 5,
                },
            )
        )

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None and request.observation.status == "ok"
        assert isinstance(request.observation.value, list)
        passage = next(
            item for item in request.observation.value if "Acme" in item["quote"]
        )
        ref = passage["evidence_id"]
        if fabricated_ref:
            ref = "ev_v2_fabricated"
        result = json.loads(_decision_json(ref))
        if ungrounded_value:
            result["decision"]["value"] = "Contoso"
        return ModelAction.final(json.dumps(result))

    def capture_step_error(step):
        def wrapped(request: ModelTurnRequest) -> ModelAction:
            try:
                return step(request)
            except Exception as exc:
                step_errors.append(f"{type(exc).__name__}: {exc}")
                raise

        return wrapped

    model = FakeModelAdapter(
        [capture_step_error(search), capture_step_error(final)]
    )
    model.test_step_errors = step_errors
    return model


def _runtime_fixture(
    tmp_path: Path,
    model,
    *,
    store: TemporalDecisionStore | None = None,
    checkpointer: SQLiteCheckpointer | None = None,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "supplier.md").write_text(
        "We selected Acme as the supplier for the Atlas project.\n",
        encoding="utf-8",
    )
    index_path = scan_vault(vault, tmp_path / "scan").index_path
    engine = RuntimeEngine(
        vault_root=vault,
        index_path=index_path,
        checkpoint_dir=tmp_path / "checkpoints",
        checkpointer=checkpointer,
        trace_dir=tmp_path / "traces",
        model=model,
        decision_memory_store=store,
    )
    request = RunRequest(
        request_id="runtime-agent-memory-capture",
        thread_id="runtime-agent-memory-capture-thread",
        workflow="team_decision",
        query="Which supplier was selected for the Atlas project?",
        max_steps=3,
        max_provider_requests=3,
        dry_run=True,
    )
    return vault, store, engine, request


def _result(tmp_path: Path, run_id: str) -> dict[str, object]:
    path = tmp_path / "checkpoints" / "results" / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_rows(store: TemporalDecisionStore, workspace_id: str):
    return store.list_semantic_candidate_rows(
        workspace_id,
        candidate_source_kind="AGENT_RESULT",
    )


def test_runtime_team_decision_is_captured_as_durable_non_authoritative_candidate(tmp_path):
    model = _team_decision_model()
    vault, store, engine, request = _runtime_fixture(tmp_path, model)
    try:
        status = engine.start_multi_agent(request)
        store = engine._decision_memory_store
        assert isinstance(store, TemporalDecisionStore)
        assert store.db_path == str(tmp_path / "checkpoints" / "decision-memory.sqlite")
        result = _result(tmp_path, status.run_id)
        workspace_id = calculate_fingerprint(vault)

        assert status.status == result["status"] == "completed", {
            "result": result,
            "fake_model_errors": model.test_step_errors,
        }
        capture = result["memory_candidate"]
        assert capture["status"] == "CAPTURED"
        assert capture["candidate_id"]
        assert capture["review_required"] is True
        assert set(capture) == {"status", "candidate_id", "review_required", "reason"}

        rows = _candidate_rows(store, workspace_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["candidate_id"] == capture["candidate_id"]
        assert row["workflow_state"] == "PENDING_REVIEW"
        candidate = SemanticDecisionMaterializer(store).get_candidate(
            workspace_id, capture["candidate_id"]
        )
        assert candidate is not None
        assert candidate.authority == "NON_AUTHORITATIVE"
        assert candidate.authorization_status == "NOT_AUTHORIZED"
        assert candidate.evidence[0].evidence_ref.startswith("ev_v2_")
        assert candidate.evidence[0].provenance.semantic_span is None
        assert candidate.effective_time is None
        assert candidate.temporal_resolution.value == "UNRESOLVED"
        assert store._connection.execute("SELECT COUNT(*) FROM decision").fetchone()[0] == 0
        assert store._connection.execute("SELECT COUNT(*) FROM action").fetchone()[0] == 0
    finally:
        store.close()


def test_runtime_store_initialization_failure_does_not_fail_successful_answer(
    tmp_path, monkeypatch
):
    model = _team_decision_model()
    vault, _, engine, request = _runtime_fixture(tmp_path, model)

    def unavailable_store(_self):
        raise OSError("synthetic SQLite open failure")

    monkeypatch.setattr(RuntimeEngine, "_get_decision_memory_store", unavailable_store)
    status = engine.start_multi_agent(request)
    result = _result(tmp_path, status.run_id)

    assert status.status == result["status"] == "completed", result
    assert result["result"]["team_decision"]["decision"]["value"] == "Acme"
    assert result["memory_candidate"] == {
        "status": "CAPTURE_FAILED",
        "candidate_id": None,
        "review_required": False,
        "reason": "PERSISTENT_STORE_UNAVAILABLE",
    }
    assert model.call_count == 2


def test_runtime_capture_failure_does_not_fail_successful_team_decision(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "supplier.md").write_text(
        "We selected Acme as the supplier for the Atlas project.\n",
        encoding="utf-8",
    )
    store = TemporalDecisionStore(tmp_path / "decision-memory.sqlite")

    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic persistence outage")

    monkeypatch.setattr(store, "save_semantic_candidate", unavailable)
    engine = RuntimeEngine(
        vault_root=vault,
        index_path=scan_vault(vault, tmp_path / "scan").index_path,
        checkpoint_dir=tmp_path / "checkpoints",
        trace_dir=tmp_path / "traces",
        model=_team_decision_model(),
        decision_memory_store=store,
    )
    request = RunRequest(
        request_id="runtime-agent-memory-capture-failure",
        thread_id="runtime-agent-memory-capture-failure-thread",
        workflow="team_decision",
        query="Which supplier was selected for the Atlas project?",
        max_steps=3,
        max_provider_requests=3,
        dry_run=True,
    )

    try:
        status = engine.start_multi_agent(request)
        result = _result(tmp_path, status.run_id)

        assert status.status == result["status"] == "completed", result
        assert result["result"]["team_decision"]["decision"]["value"] == "Acme"
        assert result["memory_candidate"] == {
            "status": "CAPTURE_FAILED",
            "candidate_id": None,
            "review_required": False,
            "reason": "CANDIDATE_CAPTURE_FAILED",
        }
        assert _candidate_rows(store, calculate_fingerprint(vault)) == ()
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["grounding", "reviewer"])
def test_runtime_does_not_capture_when_grounding_or_reviewer_fails(
    tmp_path, monkeypatch, failure: str
):
    model = _team_decision_model(ungrounded_value=failure == "grounding")
    vault, store, engine, request = _runtime_fixture(tmp_path, model)
    if failure == "reviewer":
        monkeypatch.setattr(
            RuntimeAgentAdapter,
            "_validate_evidence",
            lambda _self, ref: {"status": "fail", "reason": "synthetic", "ref": ref},
        )
    try:
        status = engine.start_multi_agent(request)
        store = engine._decision_memory_store
        assert isinstance(store, TemporalDecisionStore)
        result = _result(tmp_path, status.run_id)

        expected_status = "completed" if failure == "grounding" else "failed"
        assert status.status == result["status"] == expected_status, result
        if failure == "grounding":
            assert result["result"]["team_decision"]["decision"]["value"] == "Contoso"
        else:
            assert result["result"] is None
        assert result["memory_candidate"]["status"] == "BLOCKED"
        assert _candidate_rows(store, calculate_fingerprint(vault)) == ()
    finally:
        store.close()


def test_cold_recovery_reuses_persistent_store_and_replay_is_idempotent(tmp_path):
    checkpoint_dir = tmp_path / "checkpoints"
    checkpointer = CrashAfterDurableFinal(checkpoint_dir)
    vault, store, engine, request = _runtime_fixture(
        tmp_path,
        _team_decision_model(),
        checkpointer=checkpointer,
    )
    thread_id = request.thread_id
    with pytest.raises(SimulatedProcessCrash):
        engine.start_multi_agent(request)
    store = engine._decision_memory_store
    assert isinstance(store, TemporalDecisionStore)
    workspace_id = calculate_fingerprint(vault)
    assert store.db_path == str(checkpoint_dir / "decision-memory.sqlite")
    assert _candidate_rows(store, workspace_id) == ()
    durable_state = engine.checkpointer.get_latest(thread_id)
    assert durable_state is not None
    assert durable_state.status == "running"
    assert durable_state.current_step == "model_loop"
    assert durable_state.agent_tasks == []
    assert durable_state.termination is not None
    assert durable_state.termination.status == "running"
    store_path = Path(store.db_path)
    store.close()

    replay_model = FakeModelAdapter([ModelAction.final("must not invoke model on recovery")])
    restarted = RuntimeEngine(
        vault_root=vault,
        index_path=scan_vault(vault, tmp_path / "scan-recovery").index_path,
        checkpoint_dir=checkpoint_dir,
        trace_dir=tmp_path / "traces-recovery",
        model=replay_model,
    )
    recovery_store: TemporalDecisionStore | None = None
    try:
        status = restarted.resume_multi_agent(thread_id)
        result = _result(tmp_path, status.run_id)
        recovery_store = restarted._decision_memory_store
        assert isinstance(recovery_store, TemporalDecisionStore)
        assert recovery_store.db_path == str(store_path)

        assert status.status == result["status"] == "completed"
        capture = result["memory_candidate"]
        assert capture["status"] == "CAPTURED"
        candidate_id = capture["candidate_id"]
        assert candidate_id
        assert replay_model.call_count == 0
        before_rows = _candidate_rows(recovery_store, workspace_id)
        before_events = recovery_store.get_semantic_candidate_events(
            workspace_id, candidate_id
        )
        assert len(before_rows) == 1

        recovered_state = restarted.checkpointer.get_latest(thread_id)
        assert recovered_state is not None
        recovery_adapter = RuntimeAgentAdapter(
            vault,
            restarted.index_path,
            decision_memory_store=recovery_store,
        )
        recovery_adapter._restore_evidence(
            recovered_state,
            durable_state.model_executions[0].task_id,
            recovery_adapter.reader.read_notes(),
        )
        replay_capture = recovery_adapter._capture_agent_memory_candidate(
            result,
            run_id=status.run_id,
        )
        assert replay_capture["candidate_id"] == candidate_id
        assert _candidate_rows(recovery_store, workspace_id) == before_rows
        assert recovery_store.get_semantic_candidate_events(
            workspace_id, candidate_id
        ) == before_events
    finally:
        if isinstance(recovery_store, TemporalDecisionStore):
            recovery_store.close()
