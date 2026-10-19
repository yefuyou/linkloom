import json
from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.context.working import WorkingContext
from linkloom.evaluation.task_semantic_replay import load_recorded_semantic_adapter
from linkloom.runtime.context_engineering import ContextRuntimeConfig
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticCallPolicy
from tests.semantic_integration_support import setup, execute
from tests.integration.test_semantic_recorded_runtime import ARTIFACTS


def load_audits(root, state):
    audits = []
    for record in state.model_executions:
        payload = json.loads((root / "checkpoints/models" / record.request_ref).read_text(encoding="utf8"))
        audits.append(payload["context_audit"]["engineering"])
    return audits


@pytest.mark.parametrize("case", ["revision", "causality", "conflict"])
def test_existing_runtime_selects_and_recovers_complete_originals_with_replay(tmp_path, case):
    adapter = load_recorded_semantic_adapter(ARTIFACTS, case)
    engine, outer, inner, _, context = setup(tmp_path, case, adapter=adapter, policy=SemanticCallPolicy("REPLAY"))
    engine._context_runtime_config = ContextRuntimeConfig()
    status, state, result = execute(engine, tmp_path, context.question)
    assert status.status == "completed", (status.to_dict(), result)
    assert outer.call_count == 4 and len(inner.requests) == 1
    audits = load_audits(tmp_path, state)
    assert all(a["status"] == "COMPLETE" for a in audits)
    assert all(all(c["complete"] for c in a["coverage"]) for a in audits)
    assert all(a["recovery"]["status"] == "complete" for a in audits)
    assert result["working_context_audit"]["status"] == "COMPLETE"
    assert result["review"]["decision"] == "evidence_sufficient"
    semantic = result["result"]["task_relations"]["semantic_analysis"]
    assert all(not p["publishable"] for p in semantic["proposals"])
    if case == "revision": assert semantic["proposals"][1]["comparison"]["delta_calendar_days"] == 4
    if case == "conflict": assert semantic["proposals"][1]["comparison"]["status"] == "BLOCKED"


def test_real_history_pressure_automatically_compacts_and_recovers_pending_goal(tmp_path):
    engine, _, inner, _, context = setup(tmp_path)
    engine._context_runtime_config = ContextRuntimeConfig(history_soft_bytes=8000)
    ids = {}
    def inspect(r):
        return ModelAction.tool(ToolCall("inspect", "inspect_task_context", {"workspace_id": context.workspace_id}))
    def query(r):
        ids["context_call_id"] = r.observation.call_id
        return ModelAction.tool(ToolCall("query", "query_task_relations", {"workspace_id": context.workspace_id}))
    def analyze(r):
        ids["relations_call_id"] = r.observation.call_id
        return ModelAction.tool(ToolCall("semantic", "analyze_task_semantics", {
            "workspace_id": context.workspace_id, "investigation_id": "investigation"}))
    def final(r):
        return ModelAction.final(json.dumps({**ids, "semantic_call_id": r.observation.call_id}))
    outer = FakeModelAdapter([*([inspect] * 6), query, analyze, final])
    engine.model = outer
    status, state, result = execute(engine, tmp_path, context.question, steps=9)
    assert status.status == "completed", (status.to_dict(), result)
    audits = load_audits(tmp_path, state)
    compressed = [a for a in audits if a["compaction"]]
    assert compressed and any(a["compaction"]["dropped_history"] > 0 for a in compressed)
    assert len(state.tool_ledger) == 8 and len(state.model_executions) == 9 and len(inner.requests) == 1
    assert max(a["compaction"]["before_budget"]["input_bytes"] for a in compressed) > 96 * 1024
    for audit in compressed:
        saved = json.loads((tmp_path / "checkpoints/models" / audit["working_ref"]["ref"]).read_text(encoding="utf8"))
        working = WorkingContext.from_json(saved["working_json"])
        assert working.user_goal == context.question and working.unresolved_questions and working.evidence_refs
        assert not working.operations and working.summary_text
        assert audit["recovery"]["status"] == "complete"
