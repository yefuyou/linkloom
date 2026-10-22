from dataclasses import replace
import json

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.context.policy import ContextPolicy
from linkloom.context.working import WorkingContext
from linkloom.runtime.context_engineering import ContextRuntimeConfig
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.tools.contracts import ToolCall
from tests.semantic_integration_support import setup, execute, runtime_script
from tests.integration.test_context_runtime_integration import load_audits
from tests.integration.test_task_relation_runtime import prepared, engine_for, script
from tests.unit.test_task_relation_query import retract


def test_cold_resume_after_inner_response_keeps_models_tools_and_original_refs(tmp_path, monkeypatch):
    engine, outer, inner, _, context = setup(tmp_path)
    engine._context_runtime_config = ContextRuntimeConfig(history_soft_bytes=8000)
    save = engine.checkpointer.save
    stopped = {}
    def crash(state, *args, **kwargs):
        ref = save(state, *args, **kwargs)
        if len(state.tool_ledger) == 3 and state.model_executions[-1].status == "tool_result_durable":
            stopped["state"] = state
            raise KeyboardInterrupt("synthetic crash after durable semantic observation")
        return ref
    monkeypatch.setattr(engine.checkpointer, "save", crash)
    with pytest.raises(KeyboardInterrupt): execute(engine, tmp_path, context.question)
    state = stopped["state"]
    ids = dict(zip(("context_call_id", "relations_call_id", "semantic_call_id"), (r.call_id for r in state.tool_ledger)))
    model = FakeModelAdapter([ModelAction.final(json.dumps(ids))])
    cold = RuntimeEngine(engine.vault_root, engine.index_path, engine.checkpoint_dir, trace_dir=engine.trace_dir,
        model=model, task_relation_tools=engine._task_relation_tools, context_runtime_config=engine._context_runtime_config,
        memory_enabled=False, decision_memory_enabled=False)
    before = {p: p.read_bytes() for p in (tmp_path / "checkpoints/models").rglob("*.json")}
    status = cold.resume_multi_agent(state.thread_id)
    assert status.status == "completed", status.to_dict()
    assert outer.call_count == 3 and model.call_count == 1 and len(inner.requests) == 1
    assert all(p.read_bytes() == data for p, data in before.items())
    latest = cold.checkpointer.get_latest(state.thread_id)
    assert [r.to_dict() for r in latest.tool_ledger] == [r.to_dict() for r in state.tool_ledger]
    assert len(latest.model_executions) == 4


@pytest.mark.parametrize("kind", ["changed", "forbidden", "revoked", "stale", "incomplete"])
def test_source_changes_block_before_next_model_and_inner_call(tmp_path, kind):
    engine, _, inner, live, context = setup(tmp_path)
    engine._context_runtime_config = ContextRuntimeConfig()
    def inspect(r):
        return ModelAction.tool(ToolCall("inspect", "inspect_task_context", {"workspace_id": context.workspace_id}))
    def mutate(r):
        snapshot = live["snapshot"]
        if kind == "changed":
            live["snapshot"] = replace(snapshot, artifacts=(replace(snapshot.artifacts[0], content=snapshot.artifacts[0].content + " changed"), *snapshot.artifacts[1:]))
        if kind == "forbidden": live["snapshot"] = replace(snapshot, authorized_artifact_versions=frozenset())
        if kind == "revoked": live["snapshot"] = replace(snapshot, revoked_evidence_refs=frozenset([context.allowed_spans[0][1].evidence_ref]))
        if kind == "incomplete": live["snapshot"] = replace(snapshot, truncated=True)
        if kind == "stale":
            s = context.allowed_spans[0][1]
            snapshot.source_registry.register_evidence(snapshot.workspace_id, s.evidence_ref, content_hash=s.content_hash, source_version="changed")
        return ModelAction.tool(ToolCall("query", "query_task_relations", {"workspace_id": context.workspace_id}))
    outer = FakeModelAdapter([inspect, mutate, ModelAction.final("must not execute")])
    engine.model = outer
    status, _, result = execute(engine, tmp_path, context.question)
    assert status.status == "failed" and outer.call_count == 2 and not inner.requests
    assert result["error"]["code"] == "CONTEXT_INCOMPLETE"
    audit = result["working_context_audit"]
    assert audit["status"] == "BLOCKED" and audit["errors"]
    working = WorkingContext.from_json(json.loads((tmp_path / "checkpoints/models" / audit["working_ref"]["ref"]).read_text(encoding="utf8"))["working_json"])
    assert working.evidence_refs and "sources_current" not in working.completed_subgoals


def test_relation_revoked_after_observed_query_cannot_revive_through_compaction(tmp_path):
    tools, path, snapshot, rows = prepared(tmp_path)
    def revoke(): retract(path, snapshot.workspace_id, rows[0]["same_task"]["assessment_id"])
    model = script(snapshot.workspace_id, before_final=revoke)
    engine = engine_for(tmp_path, tools, model)
    engine._context_runtime_config = ContextRuntimeConfig(history_soft_bytes=1)
    from linkloom.runtime.models import RunRequest
    status = engine.start_multi_agent(RunRequest(request_id="revoked-context", workflow="task_relations",
        query="Which plan is current?", max_steps=3, max_provider_requests=3))
    assert status.status == "failed" and model.call_count == 3
    state = engine.checkpointer.get_latest(status.thread_id)
    result = json.loads((tmp_path / "checkpoints" / state.result_ref).read_text(encoding="utf8"))
    assert result["review"]["decision"] == "schema_invalid"
    assert not result.get("result")


@pytest.mark.parametrize("budget", ["tokens", "bytes", "evidence"])
def test_budget_failure_sends_no_request_and_discloses_missing_coverage(tmp_path, budget):
    engine, outer, inner, _, context = setup(tmp_path)
    config = ContextRuntimeConfig()
    if budget == "tokens": config = replace(config, policy=ContextPolicy(context_window_tokens=500, output_reserve_tokens=100))
    if budget == "bytes": config = replace(config, policy=ContextPolicy(max_request_bytes=1024))
    if budget == "evidence": config = replace(config, local_budget=replace(config.local_budget, max_evidence=1))
    engine._context_runtime_config = config
    status, _, result = execute(engine, tmp_path, context.question)
    assert status.status == "failed" and outer.call_count == 0 and not inner.requests
    assert result["error"]["code"] == "CONTEXT_INCOMPLETE"
    audit = result["working_context_audit"]
    assert audit["status"] == "BLOCKED"
    if budget == "evidence": assert any(not row["complete"] and row["missing_evidence_ids"] for row in audit["coverage"])


@pytest.mark.parametrize("change", ["source", "working", "mapping", "checkpoint", "policy", "tokenizer"])
def test_cold_restore_rejects_changed_sources_or_context_artifacts_before_model(tmp_path, monkeypatch, change):
    engine, _, inner, live, context = setup(tmp_path)
    engine._context_runtime_config = ContextRuntimeConfig()
    save = engine.checkpointer.save
    stopped = {}
    def crash(state, *args, **kwargs):
        ref = save(state, *args, **kwargs)
        if len(state.tool_ledger) == 1 and state.model_executions[-1].status == "tool_result_durable":
            stopped["state"] = state
            raise KeyboardInterrupt("source recovery checkpoint")
        return ref
    monkeypatch.setattr(engine.checkpointer, "save", crash)
    with pytest.raises(KeyboardInterrupt): execute(engine, tmp_path, context.question)
    state = stopped["state"]
    audit = load_audits(tmp_path, state)[-1]
    config = engine._context_runtime_config
    if change == "source":
        live["snapshot"] = replace(live["snapshot"], revoked_observation_ids=frozenset([context.observations[0].observation_id]))
    elif change == "policy": config = replace(config, history_soft_bytes=500)
    elif change == "tokenizer": config = replace(config, tokenizer_id="DIFFERENT_TOKENIZER_IMPLEMENTATION_V2")
    else:
        if change == "checkpoint":
            wc = WorkingContext.from_json(json.loads((tmp_path / "checkpoints/models" / audit["working_ref"]["ref"]).read_text(encoding="utf8"))["working_json"])
            path = tmp_path / "checkpoints/models" / wc.checkpoint_ref.locator
        else:
            path = tmp_path / "checkpoints/models" / audit[change + "_ref"]["ref"]
        path.write_text('{"forged":true}', encoding="utf8")
    model = FakeModelAdapter([ModelAction.final("must not run")])
    cold = RuntimeEngine(engine.vault_root, engine.index_path, engine.checkpoint_dir, trace_dir=engine.trace_dir,
        model=model, task_relation_tools=engine._task_relation_tools, context_runtime_config=config,
        memory_enabled=False, decision_memory_enabled=False)
    with pytest.raises(ValidationError): cold.resume_multi_agent(state.thread_id)
    assert model.call_count == 0 and not inner.requests


def test_durable_final_cold_resume_uses_no_additional_outer_or_inner_request(tmp_path, monkeypatch):
    engine, outer, inner, _, context = setup(tmp_path)
    engine._context_runtime_config = ContextRuntimeConfig()
    save = engine.checkpointer.save
    stopped = {}
    def crash(state, *args, **kwargs):
        ref = save(state, *args, **kwargs)
        if len(state.model_executions) == 4 and state.model_executions[-1].status == "response_durable":
            stopped["state"] = state
            raise KeyboardInterrupt("durable Final")
        return ref
    monkeypatch.setattr(engine.checkpointer, "save", crash)
    with pytest.raises(KeyboardInterrupt): execute(engine, tmp_path, context.question)
    state = stopped["state"]
    model = FakeModelAdapter([ModelAction.final("must not run")])
    cold = RuntimeEngine(engine.vault_root, engine.index_path, engine.checkpoint_dir, trace_dir=engine.trace_dir,
        model=model, task_relation_tools=engine._task_relation_tools, context_runtime_config=engine._context_runtime_config,
        memory_enabled=False, decision_memory_enabled=False)
    status = cold.resume_multi_agent(state.thread_id)
    assert status.status == "completed", status.to_dict()
    assert outer.call_count == 4 and model.call_count == 0 and len(inner.requests) == 1
