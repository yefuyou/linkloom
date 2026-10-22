"""Regression probes for the independent M3-E review findings."""
from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.context.assembler import ContextAssembler
from linkloom.context.policy import ContextPolicy
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef
from linkloom.context.working import WorkingContext
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.context_engineering import ContextRuntimeConfig, RuntimeWorkingContext
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from tests.integration.test_runtime_v2_multi_action_resume import _state
from tests.unit.test_runtime_context_sources import bridge_for


def service_for(tmp_path, name="revision", config=None):
    bridge, context, live = bridge_for(name)
    config = config or ContextRuntimeConfig()
    assembler = ContextAssembler(policy=config.policy, budget=config.local_budget)
    store = ModelArtifactStore(tmp_path)
    service = RuntimeWorkingContext(config, bridge.tools, store, context.question,
        lambda **kw: assembler.assemble(workspace_id=context.workspace_id, **kw))
    return service, store, context, live


def request_for(state, question):
    return ModelTurnRequest(state.run_id, "turn1", "task", "retrieval_agent", 1, question, None, [])


def test_receipt_polarities_and_atomic_groups_preserve_actual_opposing_sources(tmp_path):
    service, store, context, _ = service_for(tmp_path, "causality")
    mapping = service.bridge.catalog()
    assert len(mapping) >= 3
    span_refs = {"positive": {"evidence_ref": mapping[0]["origin_ref"]},
                 "negative": {"evidence_ref": mapping[1]["origin_ref"]}}
    value = {"evidence_spans": span_refs, "proposals": [{"predicate": "REVISES_PLAN",
        "proposed_verdict": "CONFLICT", "supporting_evidence": [{"span_ref": "positive"}],
        "counter_evidence": [{"span_ref": "negative"}]}]}
    state = replace(_state(), workflow="task_relations")
    record = ToolExecutionRecord(call_id="observed", run_id=state.run_id, task_id="task",
        agent_id="retrieval_agent", sequence=1, tool_id="inspect_task_context", arguments={},
        status="completed", result={"status": "ok", "value": value})
    state = replace(state, tool_ledger=[record])
    _, audit = service.prepare(request_for(state, context.question), state)
    working = WorkingContext.from_json(store.read(audit["working_ref"]["ref"])["working_json"])
    positive, negative = (row["context_ref"]["evidence_id"] for row in mapping[:2])
    assert working.negative_evidence_ids == (negative,)
    assert working.conflicts[0].support_ids == (positive,)
    assert working.conflicts[0].counter_ids == (negative,)
    groups = {group["group_id"]: set(group["evidence_ids"]) for group in audit["groups"]}
    assert groups["observed_support_only"] == {positive}
    assert groups["observed_counter_only"] == {negative}
    assert groups["unclassified_investigation"] == {r["context_ref"]["evidence_id"] for r in mapping[2:]}
    ids = [e for group in groups.values() for e in group]
    assert len(ids) == len(set(ids)) == len(mapping)


def test_source_batch_uses_one_snapshot_and_checks_fresh_boundary(monkeypatch):
    bridge, context, _ = bridge_for()
    original = bridge.tools.context_sources
    calls = []
    def read():
        calls.append(True)
        return original()
    monkeypatch.setattr(bridge.tools, "context_sources", read)
    with bridge.session():
        rows = bridge.catalog()
        refs = tuple(EvidenceRef.from_dict(r["context_ref"]) for r in rows)
        result = EvidenceRecovery(bridge.read_source).restore(workspace_id=context.workspace_id,
            refs=refs, required_ids=tuple(r.evidence_id for r in refs))
        assert result.status == "complete"
        assert len(calls) == 1
    assert len(calls) == 2


def test_source_contract_change_during_batch_is_rejected(monkeypatch):
    bridge, _, live = bridge_for()
    original = bridge.tools.context_sources
    count = 0
    def flip():
        nonlocal count
        count += 1
        raw, snapshot = original()
        if count == 2:
            snapshot = replace(snapshot, question=snapshot.question + " Changed authorized question.")
        return raw, snapshot
    monkeypatch.setattr(bridge.tools, "context_sources", flip)
    with pytest.raises(ValidationError) as error:
        bridge.catalog()
    assert error.value.details["code"] == "CONTEXT_SNAPSHOT_CHANGED"
    assert count == 2


def test_changed_contract_at_preflight_end_blocks_with_audit_and_no_usable_request(tmp_path, monkeypatch):
    service, store, context, _ = service_for(tmp_path)
    original = service.bridge.tools.context_sources
    count = 0
    def flip():
        nonlocal count
        count += 1
        raw, snapshot = original()
        return raw, replace(snapshot, question=snapshot.question + " changed") if count == 2 else snapshot
    monkeypatch.setattr(service.bridge.tools, "context_sources", flip)
    state = replace(_state(), workflow="task_relations")
    with pytest.raises(ValidationError) as error: service.prepare(request_for(state, context.question), state)
    assert error.value.details["code"] == "CONTEXT_INCOMPLETE"
    assert service.latest["status"] == "BLOCKED"
    assert service.latest["errors"] == ["CONTEXT_SNAPSHOT_CHANGED"]
    assert all(not c["complete"] and c["missing_evidence_ids"] for c in service.latest["coverage"])
    working = WorkingContext.from_json(store.read(service.latest["working_ref"]["ref"])["working_json"])
    assert "sources_current" not in working.completed_subgoals


@pytest.mark.parametrize("change", ["workspace", "competitor", "endpoint", "query", "source_handle"])
def test_direct_recovery_validates_full_host_contract(change):
    bridge, context, live = bridge_for()
    rows = bridge.catalog()
    snapshot = live["snapshot"]
    if change == "workspace": snapshot = replace(snapshot, workspace_id="other")
    if change == "competitor": snapshot = replace(snapshot, observations=snapshot.observations[:-1])
    if change == "endpoint": snapshot = replace(snapshot, endpoints=())
    if change == "query": snapshot = replace(snapshot, queries=(replace(snapshot.queries[0], subject="missing-handle"),))
    if change == "source_handle": snapshot = replace(snapshot, source_handles=())
    live["snapshot"] = snapshot
    ref = EvidenceRef.from_dict(rows[0]["context_ref"])
    assert bridge.read_source(context.workspace_id, ref.resource_id) is None
    result = EvidenceRecovery(bridge.read_source).restore(workspace_id=context.workspace_id,
        refs=(ref,), required_ids=(ref.evidence_id,))
    assert result.status != "complete" and not result.evidence


def test_unallocated_request_reports_missing_protected_ids(tmp_path):
    config = ContextRuntimeConfig(policy=ContextPolicy(context_window_tokens=500, output_reserve_tokens=100))
    service, _, context, _ = service_for(tmp_path, config=config)
    state = replace(_state(), workflow="task_relations")
    with pytest.raises(ValidationError): service.prepare(request_for(state, context.question), state)
    for field in ("coverage_before_compaction", "coverage"):
        assert service.latest[field]
        assert all(not c["complete"] and len(c["missing_evidence_ids"]) == len(service.mapping)
                   for c in service.latest[field])


def test_injected_tokenizer_identity_is_explicit_and_counter_changes_detected():
    policy = ContextPolicy(token_counter=lambda text: len(text), token_count_kind="OFFLINE_REFERENCE")
    with pytest.raises(ValueError, match="tokenizers require"):
        ContextRuntimeConfig(policy=policy)
    before = ContextRuntimeConfig(policy=policy, tokenizer_id="COUNTER_V1")
    changed = replace(before, policy=replace(policy, token_counter=lambda text: len(text.encode("utf8"))))
    assert changed.identity() != before.identity()
    assert replace(before, tokenizer_id="COUNTER_V2").identity() != before.identity()
