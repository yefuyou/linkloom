from dataclasses import replace
import json
from pathlib import Path

import pytest

from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.tools.semantic_analysis import SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from tests.semantic_integration_support import setup, execute, runtime_script
from tests.task_semantic_support import context_for, response_arguments
from tests.unit.test_semantic_invocation import prepared
from tests.unit.test_task_relation_semantic import FakeModel


def variant():
    case = json.loads((Path(__file__).parents[1] / "fixtures/task_semantic/integration-dev-v2.json").read_text(encoding="utf8"))["cases"][0]
    return context_for(case)


def variant_arguments(context, omission=None):
    args = response_arguments(context)
    for row, verdict in zip(args["proposals"], ("SUPPORTED", "REFUTED", "UNKNOWN")):
        row["proposed_verdict"] = verdict
        citations = row["supporting_evidence"]
        row["counter_evidence"] = citations if verdict == "REFUTED" else []
        row["supporting_evidence"] = citations if verdict != "REFUTED" else []
        row["missing_evidence"] = ["No explicit impact bridge within the declared investigation."] if verdict == "UNKNOWN" else []
        row["reason_codes"] = ["FAKE_CONTROL_ONLY_NOT_MODEL_QUALITY"]
        if omission == verdict:
            key = "counter_evidence" if verdict == "REFUTED" else "supporting_evidence"
            exclude = "choice.md:" if verdict == "REFUTED" else "investigation:"
            row[key] = [c for c in row[key] if not c["span_id"].startswith(exclude)]
    return args


@pytest.mark.parametrize("omission", [None, "REFUTED", "UNKNOWN"])
def test_new_public_variant_discloses_required_counter_and_investigation_sources(tmp_path, omission):
    context = variant()
    # Explicit synthetic output control; never a new LLM or benchmark score.
    inner = FakeModel(variant_arguments(context, omission))
    snapshot = SemanticSourceSnapshot.from_context(context)
    engine, _, _, _, _ = setup(tmp_path, "causality", snapshot=snapshot, adapter=inner)
    status, _, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed", result
    rows = result["result"]["task_relations"]["semantic_analysis"]["proposals"]
    assert [r["proposed_verdict"] for r in rows] == ["SUPPORTED", "REFUTED", "UNKNOWN"]
    assert all(r["semantic_status"] == "SEMANTIC_NOT_EVALUATED" and not r["publishable"] for r in rows)
    for row in rows:
        missing = row["proposed_verdict"] == omission
        assert row["coverage"]["status"] == ("INSUFFICIENT" if missing else "COMPLETE")
        assert row["evidence_status"] == ("EVIDENCE_INVALID" if missing else "EVIDENCE_VALID")
        assert bool(row["coverage"]["missing_source_versions"]) == missing


@pytest.mark.parametrize("kind", ["workspace", "stale", "revoked_source", "revoked_endpoint", "missing_source", "missing_competitor", "truncated", "chars", "tokens", "cost", "provider", "request_budget"])
def test_semantic_admission_blocks_before_inner_request(tmp_path, kind):
    bound, call, model, snapshot = prepared(tmp_path)
    policy = bound.owner.policy
    if kind == "workspace": snapshot = replace(snapshot, workspace_id="other")
    if kind == "stale":
        span = snapshot.observations[0].provenance
        snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref, content_hash=span.content_hash, source_version="changed")
    if kind == "revoked_source": snapshot = replace(snapshot, revoked_evidence_refs=frozenset([snapshot.observations[0].provenance.evidence_ref]))
    if kind == "revoked_endpoint": snapshot = replace(snapshot, revoked_observation_ids=frozenset([snapshot.observations[0].observation_id]))
    if kind == "missing_source": snapshot = replace(snapshot, artifacts=snapshot.artifacts[:1])
    if kind == "missing_competitor": snapshot = replace(snapshot, observations=snapshot.observations[:1])
    if kind == "truncated": snapshot = replace(snapshot, truncated=True)
    if kind == "chars": snapshot = replace(snapshot, limits=replace(snapshot.limits, max_allowed_chars=1))
    if kind == "tokens": policy = replace(policy, max_input_tokens=1)
    if kind == "cost": policy = replace(policy, request_cost_upper_bound_usd=0.01)
    if kind == "provider": bound.owner.adapter = None
    if kind == "request_budget": policy = replace(policy, max_requests=0)
    bound.owner.policy = policy
    bound.owner.snapshot_provider = lambda: snapshot
    value = bound.execute(call)
    assert value["analysis_status"] == "BLOCKED" and value["errors"], value
    assert not model.requests
    assert not list(tmp_path.rglob("reservation.json"))
    assert value["semantic_status"] == "SEMANTIC_NOT_EVALUATED" and not value["publishable"]


def test_response_saved_before_result_can_resume_without_another_adapter_request(tmp_path, monkeypatch):
    bound, call, model, _ = prepared(tmp_path)
    write = bound.store.write
    def crash(ref, payload, **kwargs):
        if ref == bound.result_ref:
            raise KeyboardInterrupt("crash after response durability")
        return write(ref, payload, **kwargs)
    monkeypatch.setattr(bound.store, "write", crash)
    with pytest.raises(KeyboardInterrupt): bound.execute(call)
    assert len(model.requests) == 1
    monkeypatch.setattr(bound.store, "write", write)
    restored = bound.execute(call)
    assert restored["cache_hit"] and restored["analysis_status"] == "EXECUTED"
    assert len(model.requests) == 1 and restored["execution"]["reserved_requests"] == 1


def test_reviewer_rejects_changed_response_receipt(tmp_path):
    bound, call, _, _ = prepared(tmp_path)
    result = bound.execute(call)
    path = bound.store.root / bound.response_ref
    data = json.loads(path.read_text(encoding="utf8"))
    data["response"]["provider_request_id"] = "wrong-receipt"
    path.write_text(json.dumps(data), encoding="utf8")
    assert bound.review(result, call_id=call.call_id)["status"] == "fail"


@pytest.mark.parametrize("boundary", ["tool_result_durable", "ledger_terminal"])
def test_outer_checkpoint_resume_keeps_inner_count_and_uses_only_remaining_final(tmp_path, monkeypatch, boundary):
    from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
    engine, outer, inner, _, context = setup(tmp_path)
    saved = engine.checkpointer.save
    interrupted = {}
    def save(state, *args, **kwargs):
        ref = saved(state, *args, **kwargs)
        if (len(state.tool_ledger) == 3 and all(r.status == "completed" for r in state.tool_ledger)
                and (boundary == "ledger_terminal" or state.model_executions[-1].status == "tool_result_durable")):
            interrupted["state"] = state
            raise KeyboardInterrupt("crash after durable third tool receipt")
        return ref
    monkeypatch.setattr(engine.checkpointer, "save", save)
    with pytest.raises(KeyboardInterrupt): execute(engine, tmp_path, context.question)
    monkeypatch.setattr(engine.checkpointer, "save", saved)
    state = interrupted["state"]
    ids = dict(zip(("context_call_id", "relations_call_id", "semantic_call_id"), (r.call_id for r in state.tool_ledger)))
    engine.model = FakeModelAdapter([ModelAction.final(json.dumps(ids))])
    status = engine.resume_multi_agent(state.thread_id)
    if boundary == "ledger_terminal":
        # Existing loop fails closed in the ledger/observation commit gap. No
        # semantic retry or extra charge; do not claim universal recovery.
        assert status.status == "failed" and status.error["code"] == "MODEL_LOOP_RUNTIME_FAILED"
        assert outer.call_count == 3 and engine.model.call_count == 0 and len(inner.requests) == 1
        return
    assert status.status == "completed", status.to_dict()
    latest = engine.checkpointer.get_latest(state.thread_id)
    assert len(latest.model_executions) == 4
    assert outer.call_count == 3 and engine.model.call_count == 1 and len(inner.requests) == 1


def test_outer_step_budget_does_not_trigger_inner_request(tmp_path):
    engine, outer, inner, _, context = setup(tmp_path)
    status, _, _ = execute(engine, tmp_path, context.question, steps=2)
    assert status.status == "failed" and outer.call_count == 2 and not inner.requests


@pytest.mark.parametrize("arguments", [dict(workspace_id="wrong", investigation_id="investigate"),
    dict(workspace_id="replace", investigation_id="wrong"),
    dict(workspace_id="replace", investigation_id="investigate", path="arbitrary.sqlite"),
    dict(workspace_id="replace", investigation_id="investigate", api_key="forbidden")])
def test_semantic_schema_rejects_workspace_job_path_and_secrets_before_execution(tmp_path, arguments):
    from linkloom.tools.task_relations import TaskRelationTools, TaskContextSnapshot
    from linkloom.tools.runtime import create_retrieval_tool_runtime
    from tests.integration.test_retrieval_tool_runtime import _policy
    bound, call, model, snapshot = prepared(tmp_path)
    raw = TaskContextSnapshot(snapshot.workspace_id, snapshot.observations, snapshot.artifacts, snapshot.source_registry)
    tools = TaskRelationTools(snapshot.workspace_id, lambda: raw, semantic_analysis=bound)
    runtime = create_retrieval_tool_runtime(lambda *_: [], lambda _: {}, task_relation_tools=tools)
    args = {k: snapshot.workspace_id if v == "replace" else v for k, v in arguments.items()}
    value = runtime.execute(replace(call, arguments=args), _policy(["analyze_task_semantics"]))
    assert value.error.code == "TOOL_INVALID_ARGUMENTS"
    assert not runtime.ledger.to_list() and not model.requests


@pytest.mark.parametrize("change", ["stale", "withdrawn"])
def test_reviewer_rejects_source_change_after_semantic_response(tmp_path, change):
    engine, _, inner, live, context = setup(tmp_path)
    def change_source():
        snapshot = live["snapshot"]
        span = snapshot.observations[0].provenance
        if change == "stale":
            snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref, content_hash=span.content_hash, source_version="new-version")
        else:
            live["snapshot"] = replace(snapshot, revoked_evidence_refs=frozenset([span.evidence_ref]))
    engine.model = runtime_script(context.workspace_id, before_final=change_source)
    status, _, result = execute(engine, tmp_path, context.question)
    assert len(inner.requests) == 1 and status.status == "failed"
    assert result["review"]["decision"] == "schema_invalid"
