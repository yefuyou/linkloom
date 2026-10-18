"""Real durable runtime with scripted model actions; no live provider or Gold."""
import json

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.agents.registry import create_default_registry
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.integration.test_retrieval_tool_runtime import _policy
from tests.unit.test_task_relation_query import seed, retract


def prepared(root, case_id="D04"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "relations.sqlite"
    case, observations, artifacts, registry, rows, _ = seed(path, case_id)
    snapshot = TaskContextSnapshot(case["workspace_id"], tuple(observations.values()),
                                   tuple(artifacts.values()), registry)
    tools = TaskRelationTools(snapshot.workspace_id, lambda: snapshot, relation_path=path)
    return tools, path, snapshot, rows


def script(workspace, *, before_final=None):
    def inspect(request):
        assert {t.tool_id for t in request.available_tools} == {"inspect_task_context", "query_task_relations"}
        return ModelAction.tool(ToolCall(call_id="inspect", tool_id="inspect_task_context",
            arguments={"workspace_id": workspace, "max_candidates": 5}))

    def query(request):
        assert request.observation.status == "ok"
        assert request.observation.value["observations"]
        assert request.observation.value["observations"][0]["observation"]["provenance"]
        observation_id = request.observation.value["observations"][0]["ref"]["observation_id"]
        return ModelAction.tool(ToolCall(call_id="query", tool_id="query_task_relations",
            arguments={"workspace_id": workspace, "observation_id": observation_id,
                       "include_history": True, "as_of": "2026-10-18"}))

    def final(request):
        assert request.observation.status == "ok"
        # Read the actual prior receipt IDs, not the model's proposed IDs.
        context = next(h.tool_result for h in request.tool_history
                       if h.tool_result.tool_id == "inspect_task_context")
        if before_final:
            before_final()
        return ModelAction.final(json.dumps({"context_call_id": context.call_id,
                                            "relations_call_id": request.observation.call_id}))
    return FakeModelAdapter([inspect, query, final])


def engine_for(root, tools, model):
    vault = root / "vault"
    vault.mkdir()
    (vault / "synthetic.md").write_text("# Synthetic runtime anchor\n", encoding="utf8")
    scan = scan_vault(vault, root / "scan")
    return RuntimeEngine(vault, scan.index_path, root / "checkpoints", trace_dir=root / "traces",
                         model=model, memory_enabled=False, decision_memory_enabled=False,
                         task_relation_tools=tools)


def execute(root, tools, model=None, *, steps=3):
    model = model or script(tools.workspace_id)
    engine = engine_for(root, tools, model)
    status = engine.start_multi_agent(RunRequest(request_id="m3b", workflow="task_relations",
        query="这些任务是否同一任务，计划截止日期如何修订？", max_steps=steps, max_provider_requests=steps))
    state = engine.checkpointer.get_latest(status.thread_id)
    result = json.loads((root / "checkpoints" / state.result_ref).read_text(encoding="utf8")) if state and state.result_ref else None
    return status, state, result, model


def test_durable_runtime_inspect_query_final_and_reviewer(tmp_path):
    tools, path, _, _ = prepared(tmp_path)
    before = path.read_bytes()
    status, state, result, model = execute(tmp_path, tools)
    assert status.status == "completed", (status.to_dict(), result)
    assert result["review"]["decision"] == "evidence_sufficient"
    payload = result["result"]["task_relations"]
    assert payload["context"]["observations"]
    assert payload["relation_query"]["status"] == "VERIFIED_RELATIONS_AVAILABLE"
    assert len(state.model_executions) == 3
    assert [r.tool_id for r in state.tool_ledger] == ["inspect_task_context", "query_task_relations"]
    assert all(r.status == "completed" for r in state.tool_ledger)
    assert model.call_count == 3
    assert path.read_bytes() == before


@pytest.mark.parametrize("variant", ["stale", "revoked", "insufficient"])
def test_p4_diagnostics_preserved_through_real_runtime(tmp_path, variant):
    tools, path, snapshot, rows = prepared(tmp_path, "D07" if variant == "insufficient" else "D04")
    if variant == "stale":
        span = snapshot.observations[0].provenance
        snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref,
            content_hash=span.content_hash, source_version="stale-version")
    if variant == "revoked":
        retract(path, snapshot.workspace_id, rows[0]["same_task"]["assessment_id"])
    before = path.read_bytes()
    status, _, result, _ = execute(tmp_path, tools)
    assert status.status == "completed", (status.to_dict(), result)
    query = result["result"]["task_relations"]["relation_query"]
    assert all(not r["current_supported"] for r in query["relations"])
    assert any(r["blocking_reasons"] or r["assessment"]["verdict"] == "UNKNOWN" for r in query["relations"])
    assert path.read_bytes() == before


def test_reviewer_rejects_source_change_after_model_read(tmp_path):
    tools, _, snapshot, _ = prepared(tmp_path)
    def invalidate():
        span = snapshot.observations[0].provenance
        snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref,
            content_hash=span.content_hash, source_version="replaced-after-query")
    status, _, result, _ = execute(tmp_path, tools, script(snapshot.workspace_id, before_final=invalidate))
    assert status.status == "failed"
    assert result["review"]["decision"] == "schema_invalid"
    assert status.error["code"] == "TASK_RELATIONS_EVIDENCE_REVIEW_FAILED"


@pytest.mark.parametrize("arguments", [{"workspace_id": "wrong"},
    {"workspace_id": None}, {"workspace_id": "replace", "path": "C:/secret.sqlite"},
    {"workspace_id": "replace", "as_of": "2026-02-30"}])
def test_runtime_rejects_illegal_business_arguments_before_executor(tmp_path, arguments):
    tools, _, _, _ = prepared(tmp_path)
    args = {k: tools.workspace_id if v == "replace" else v for k, v in arguments.items()}
    runtime = create_retrieval_tool_runtime(lambda *_: [], lambda _: {}, task_relation_tools=tools)
    result = runtime.execute(ToolCall(call_id="bad", tool_id="query_task_relations", arguments=args),
                             _policy(["query_task_relations"]))
    assert result.error.code == "TOOL_INVALID_ARGUMENTS"
    assert runtime.ledger.to_list() == []


def test_tool_budget_and_permission_are_enforced(tmp_path):
    tools, _, _, _ = prepared(tmp_path)
    runtime = create_retrieval_tool_runtime(lambda *_: [], lambda _: {}, task_relation_tools=tools)
    call = ToolCall(call_id="budget", tool_id="inspect_task_context", arguments={"workspace_id": tools.workspace_id})
    assert runtime.execute(call, _policy(["inspect_task_context"], max_calls=0)).error.code == "TOOL_BUDGET_EXCEEDED"
    assert runtime.execute(call, _policy(["search_notes"])).error.code == "TOOL_PERMISSION_DENIED"


def test_missing_provider_and_model_step_budget(tmp_path):
    tools, _, _, _ = prepared(tmp_path)
    engine = engine_for(tmp_path, tools, None)
    status = engine.start_multi_agent(RunRequest(request_id="missing", workflow="task_relations", query="任务关系"))
    assert status.error["code"] == "MODEL_NOT_CONFIGURED"
    other = tmp_path / "step"
    other.mkdir()
    status, _, _, _ = execute(other, tools, steps=1)
    assert status.status == "failed" and status.error["category"] == "budget"


def test_default_workflows_do_not_gain_task_capabilities():
    for enabled in (False, True):
        registry = create_default_registry(include_decision_memory=enabled)
        assert not {"inspect_task_context", "query_task_relations"} & set(registry.get_agent("retrieval_agent").capabilities)
