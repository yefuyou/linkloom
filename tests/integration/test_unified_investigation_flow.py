import json
from pathlib import Path

from run_bounded_multihop import run_case
import pytest
from unified_investigation_fixture import UnifiedInvestigationFixture, RELATIONS, TWO, THREE, CONFLICT, MISSING
from unified_ask_fixture import request_json, poll_product_ask


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = UnifiedInvestigationFixture(tmp_path / "unified.sqlite")
    fixture.configure_agent()
    fixture.select_workspace(RELATIONS)
    yield fixture
    fixture.stop()


def ask(demo, query, **options):
    return request_json(demo.base_url, "/api/product/ask", method="POST", data={
        "query": query, "workspace_id": demo.app._workspace_id(), **options})


@pytest.mark.parametrize("query,outcome,answer", [
    (TWO, "none", "陈宁"), (THREE, "none", "Owen Brook"),
    (CONFLICT, "conflicting_evidence", None), (MISSING, "insufficient_evidence", None),
])
def test_product_ask_executes_real_source_runtime(demo, query, outcome, answer):
    status, body = ask(demo, query)
    assert status == 202, body
    _, body = poll_product_ask(demo, body["result"]["agent_run_id"], demo.app._workspace_id())
    result = body["result"]
    assert result["path"] == "SOURCE_RELATION_INVESTIGATION", result
    assert result["reviewer"]["accepted"]
    assert result["source_investigation"]["answer"] == answer
    assert result["uncertainty"]["status"] == outcome
    assert result["status"] == ("COMPLETED" if answer else "INSUFFICIENT")
    assert result["receipts"] == []
    assert result["source_investigation"]["facts"]
    assert result["evidence"] and all(e["verified"] and e["source_version"] for e in result["evidence"])
    assert all(c["kind"] == "SOURCE_RELATION" for c in result["conclusions"])
    assert result["metrics"]["model_turns"] <= 12
    assert result["metrics"]["read_count"] >= 2


def test_source_permission_uncertain_budget_and_unsupported(demo):
    for query in ("谁负责琥珀线路？其直属领导是谁？",
                  "哪个项目对应 Copper Meadow 这个别名、谁负责该项目，以及负责人的直属领导是谁？",
                  "Who is the direct leader of the owner of " + "A" * 550 + "?"):
        status, body = ask(demo, query)
        assert status == 200 and body["result"]["error"]["code"] == "UNSUPPORTED_QUERY"
        assert demo.relation_models == []
    demo.configure_agent(external_provider=True)
    assert ask(demo, THREE)[0] == 403
    assert demo.relation_models == []
    demo.configure_agent(scenario="uncertain", external_provider=True)
    status, body = ask(demo, THREE, allow_external_provider=True)
    assert status == 202
    _, body = poll_product_ask(demo, body["result"]["agent_run_id"], demo.app._workspace_id())
    assert body["result"]["status"] == "PROVIDER_UNCERTAIN", body
    assert body["result"]["conclusions"] == []
    assert len(demo.relation_models[0].requests) == 1
    demo.configure_agent(relation_max_steps=7)
    status, body = ask(demo, THREE)
    assert status == 202
    _, body = poll_product_ask(demo, body["result"]["agent_run_id"], demo.app._workspace_id())
    assert body["result"]["status"] == "FAILED"
    assert len(demo.relation_models[0].requests) <= 7


def test_exposed_hold_g_conflict_regression(tmp_path):
    """Known defect regression, never a new independent generalization score."""
    cases = json.loads(Path("tests/fixtures/bounded_multihop/heldout.json").read_text(encoding="utf-8"))["cases"]
    case = next(c for c in cases if c["id"] == "HOLD-G")
    result = run_case({key: value for key, value in case.items() if key != "expected"}, tmp_path)
    assert result["outcome"] == "partial"
    assert result["values"] == ["tess avery"]
    assert result["reason"] == "conflicting_evidence"
    assert result["citation_valid"] and result["bindings_verified"]


def test_actual_three_hop_ledger_binds_only_previous_verified_reads(demo):
    from linkloom.runtime.task_plan import assess_task_plan, verified_dependency_entities

    _, body = ask(demo, THREE)
    run_id = body["result"]["agent_run_id"]
    _, body = poll_product_ask(demo, run_id, demo.app._workspace_id())
    assert body["result"]["status"] == "COMPLETED"
    job = demo.service._jobs[run_id]
    state = job.backend.engine.checkpointer.get_latest(body["result"]["source_investigation"]["thread_id"])
    plan = job.task_plan
    assert len(plan.subgoals) == 3
    for record in state.tool_ledger:
        if record.tool_id == "search_notes":
            context = record.arguments["source_context"]
            prior = [r for r in state.tool_ledger if r.sequence < record.sequence]
            assessment = assess_task_plan(plan, prior, run_id=record.run_id, task_id=record.task_id,
                agent_id=record.agent_id, expected_workspace_id=demo.app._workspace_id())
            verified_dependency_entities(plan, assessment, context["subgoal_id"], context["dependency_entities"])
    demo.select_workspace("Other")
    status, body = request_json(demo.base_url, f"/api/product/ask/runs/{run_id}?workspace_id={demo.app._workspace_id()}")
    assert status == 409
    status, body = request_json(demo.base_url, f"/api/agent/decisions/runs/{run_id}")
    assert status == 404 and "result" not in body


def test_relation_date_scope_and_explicit_decision_do_not_silently_change_path(demo):
    _, body = ask(demo, TWO, subject="Launch", relation="uses supplier")
    assert body["result"]["error"]["code"] == "TASK_SCOPE_CONFLICT"
    _, body = ask(demo, TWO, as_of="2026-10-06T12:00:00Z")
    assert body["result"]["error"]["code"] == "DATE_SCOPE_REQUIRED"
    assert demo.relation_models == []
    _, body = ask(demo, "Who is the direct leader of the owner of Mariner Field?", as_of="2026-10-06")
    _, body = poll_product_ask(demo, body["result"]["agent_run_id"], demo.app._workspace_id())
    assert body["result"]["status"] == "INSUFFICIENT"
    assert body["result"]["uncertainty"]["status"] == "conflicting_evidence"


def test_three_hop_conflict_preserves_alias_and_owner_and_source_change_invalidates_final(demo):
    demo._import_and_review(name="opposing-report.md", value="", approve=False,
        content="[2026-10-06T10:00:00Z] Synthetic: Ada Vale reports directly to Evan North.")
    _, body = ask(demo, THREE)
    run_id = body["result"]["agent_run_id"]
    _, body = poll_product_ask(demo, run_id, demo.app._workspace_id())
    result = body["result"]
    assert result["status"] == "INSUFFICIENT"
    assert result["uncertainty"]["status"] == "conflicting_evidence"
    assert {f["predicate"] for f in result["source_investigation"]["facts"]} == {"alias_of_project", "owner_of_project"}
    assert result["source_investigation"]["answer"] is None
    assert len(result["evidence"]) >= 4
    evidence = result["evidence"][0]
    status, _ = request_json(demo.base_url, "/api/sources/" + evidence["source_id"], method="PUT", data={
        "name": evidence["source_name"], "content": "[2026-10-06T10:00:00Z] Synthetic: This source has changed."})
    assert status == 202
    _, body = poll_product_ask(demo, run_id, demo.app._workspace_id())
    assert body["result"]["status"] == "FAILED" and body["result"]["conclusions"] == []


def test_source_policy_excludes_decision_memory_reads_and_candidate_capture(demo, monkeypatch):
    from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
    from linkloom.runtime.errors import ValidationError

    demo.select_workspace("Atlas")  # This workspace already has authorized decisions.
    workspace = demo.app._workspace_id()
    demo._import_and_review(name="same-passage-chain.md", value="", approve=False,
        content="[2026-10-06T10:00:00Z] Synthetic: Lin Zhou is the accountable owner of Orion. Lin Zhou reports directly to Chen Ning.")
    decisions = demo.app.store.list_decisions(workspace)
    assert decisions
    candidates = demo.app.store.list_semantic_candidate_rows(workspace)
    events = {row["candidate_id"]: demo.app.store.get_semantic_candidate_events(workspace, row["candidate_id"])
              for row in candidates}
    def forbidden(*args, **kwargs):
        pytest.fail("Source investigation accessed Decision Memory")
    monkeypatch.setattr(RuntimeAgentAdapter, "_capture_agent_memory_candidate", forbidden)
    monkeypatch.setattr(demo.app.store, "search", forbidden)
    _, body = ask(demo, TWO)
    run_id = body["result"]["agent_run_id"]
    _, body = poll_product_ask(demo, run_id, workspace)
    assert body["result"]["status"] == "COMPLETED", body
    assert all("search_decision_memory" not in {tool.tool_id for tool in request.available_tools}
               for request in demo.relation_models[0].requests)
    engine = demo.service._jobs[run_id].backend.engine
    adapter = engine._build_runtime_agent_adapter()
    assert engine._decision_memory_store is None and adapter._decision_memory_store is None
    with pytest.raises(ValidationError, match="disabled"):
        adapter._search_decision_memory("supplier", None, 3)
    assert not (engine.checkpoint_dir / "decision-memory.sqlite").exists()
    assert demo.app.store.list_decisions(workspace) == decisions
    assert demo.app.store.list_semantic_candidate_rows(workspace) == candidates
    assert all(demo.app.store.get_semantic_candidate_events(workspace, key) == value for key, value in events.items())
