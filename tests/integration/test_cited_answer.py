import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.ui import parallel_investigation
from linkloom.ui.parallel_investigation import ParallelInvestigation

SOURCE = ("Launch replaced Meridian A with Cobalt B because Meridian A could not meet "
          "the agreed delivery schedule.")
REASON = "Meridian A was replaced because it could not meet the agreed delivery schedule."


def _final(value, refs, *, status="approved", extra=None):
    abstain = value is None
    return json.dumps(dict(schema_version="team-decision-result/v1",
        decision=dict(value=value, status=status, evidence_refs=refs),
        rationale=[], rejected_alternatives=[], actions=[], unresolved_items=[],
        uncertainty=dict(status="insufficient_evidence" if abstain else "none", statement=None,
                         unknown_fields=["reason"] if abstain else [], evidence_refs=[]),
        evidence_refs=refs, **(extra or {})))


def _run(tmp_path, monkeypatch, build, *, cited=True, search=True):
    """Run one branch: search, read decision.md, then the Final that build(read_ref, other_ref) returns."""
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "decision.md").write_text(SOURCE, encoding="utf-8")
    (vault / "other.md").write_text("Meridian A was also discussed in the budget review.", encoding="utf-8")
    scan = scan_vault(vault, tmp_path / "scan")
    seen = {}

    class Scripted:
        calls = 0

        def complete(self, request):
            self.calls += 1
            if search and self.calls == 1:
                context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
                return ModelResponse(action=ModelAction.tool(ToolCall("s", "search_notes",
                    dict(query="Meridian A", source_context=context, limit=5))))
            if search and self.calls == 2:
                seen["hits"] = {hit["relative_path"]: hit["evidence_id"] for hit in request.observation.value}
                return ModelResponse(action=ModelAction.tool(ToolCall("r", "read_verified_note",
                    dict(note_ref=seen["hits"]["decision.md"]))))
            read = seen.get("hits", {}).get("decision.md")
            return ModelResponse(action=ModelAction.final(build(read, seen.get("hits", {}).get("other.md"))))

    engine = RuntimeEngine(vault, scan.index_path, tmp_path / "rt", trace_dir=tmp_path / "tr",
                           model=Scripted(), memory_enabled=False, decision_memory_enabled=False)
    result = engine.start_multi_agent(RunRequest(request_id="cited", thread_id="cited",
        workflow="team_decision", query="Why was Meridian A replaced?", max_steps=3,
        max_provider_requests=3, reserve_final_turn=True, cited_answer=cited))
    if result.status == "completed":
        artifact = json.loads((engine.checkpoint_dir / result.result_ref).read_text(encoding="utf-8"))
        return result, artifact["result"]["team_decision"]
    return result, None


@pytest.mark.parametrize("value", [REASON, "Meridian A was replaced by Cobalt B"])
def test_cited_answer_publishes_natural_language_with_read_evidence(tmp_path, monkeypatch, value):
    result, decision = _run(tmp_path, monkeypatch, lambda read, other: _final(value, [read]))
    assert result.status == "completed", result.error
    assert decision["decision"]["value"] == value and decision["evidence_refs"]


def test_cited_answer_off_keeps_semantic_claim_validation(tmp_path, monkeypatch):
    result, _ = _run(tmp_path, monkeypatch, lambda read, other: _final(REASON, [read]), cited=False)
    assert result.status == "failed" and result.error["code"] == "TEAM_DECISION_CONTRACT_ERROR"


def test_cited_answer_allows_abstention_after_reading_but_not_without_search(tmp_path, monkeypatch):
    result, decision = _run(tmp_path, monkeypatch, lambda read, other: _final(None, [read], status="insufficient_evidence"))
    assert result.status == "completed", result.error
    assert decision["decision"]["value"] is None and decision["uncertainty"]["status"] == "insufficient_evidence"
    result, _ = _run(tmp_path / "off", monkeypatch, lambda read, other: _final(None, [read], status="insufficient_evidence"),
                     cited=False)
    assert result.status == "failed" and result.error["code"] == "UNVERIFIED_ABSTENTION"
    result, _ = _run(tmp_path / "nosearch", monkeypatch, lambda read, other: _final(None, [], status="insufficient_evidence"),
                     search=False)
    assert result.status == "failed" and result.error["code"] == "UNVERIFIED_ABSTENTION"


@pytest.mark.parametrize("pick", ["unknown", "searched_not_read"])
def test_cited_answer_rejects_references_the_run_did_not_read(tmp_path, monkeypatch, pick):
    result, _ = _run(tmp_path, monkeypatch, lambda read, other: _final(
        REASON, ["evidence-never-seen"] if pick == "unknown" else [other]))
    assert result.status == "failed", pick


def test_cited_answer_rejects_evidence_from_another_workspace():
    ref = "evidence-1"
    evidence = {ref: dict(evidence_id=ref, status="verified", workspace_id="other-workspace")}
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(_final(REASON, [ref]), [ref], evidence_by_ref=evidence,
            expected_workspace_id="current-workspace", read_evidence_ids=[ref], require_read_evidence=True)


@pytest.mark.parametrize("field,item", [
    ("actions", lambda r: dict(description="Notify", owner=None, deadline=None, status="pending", evidence_refs=[r])),
    ("unresolved_items", lambda r: dict(description="Open", owner=None, deadline=None, status="pending", evidence_refs=[r])),
    ("rejected_alternatives", lambda r: dict(alternative="Keep Meridian A", reason="Late", evidence_refs=[r])),
])
def test_cited_answer_keeps_structured_sections_closed(tmp_path, monkeypatch, field, item):
    result, _ = _run(tmp_path, monkeypatch, lambda read, other: json.dumps(
        dict(json.loads(_final(REASON, [read])), **{field: [item(read)]})))
    assert result.status == "failed" and result.error["code"] == "TEAM_DECISION_CONTRACT_ERROR"


def test_cited_answer_is_requested_only_for_single_step_branches(tmp_path, monkeypatch):
    sent = []

    class Engine:
        checkpoint_dir = checkpointer = None

        def __init__(self, **kwargs):
            pass

        def start_multi_agent(self, request):
            sent.append(request)
            return None

    monkeypatch.setattr(parallel_investigation, "RuntimeEngine", Engine)
    monkeypatch.setattr(parallel_investigation, "RunRequest", lambda **kwargs: kwargs)
    monkeypatch.setattr(parallel_investigation, "build_branch_row", lambda *args: None)
    session = SimpleNamespace(limits=SimpleNamespace(max_requests=99, max_output_tokens=99999),
                              bind=lambda model, role: model, root=tmp_path)
    run = SimpleNamespace(model_factory=lambda question: None, vault=tmp_path, index=tmp_path,
        workspace_id="w", run_id="run", workspace=None, source_versions={}, source_names={})
    plan = SimpleNamespace(subgoals=[1, 2], to_dict=lambda: {"goal": "q"})
    ParallelInvestigation._execute_branch(run, "branch-1", "q", plan, session)
    ParallelInvestigation._execute_branch(run, "branch-2", "q", None, session)
    assert [request["cited_answer"] for request in sent] == [False, True]


def test_cited_answer_request_round_trips_and_needs_default_protocol():
    request = RunRequest(request_id="r", workflow="team_decision", query="q", cited_answer=True)
    assert RunRequest.from_dict(request.to_dict()).cited_answer is True
    assert "cited_answer" not in RunRequest(request_id="r", workflow="team_decision", query="q").to_dict()
    for kwargs in (dict(workflow="ask"), dict(workflow="team_decision", final_protocol="claim-proposal/v1")):
        with pytest.raises(ValidationError):
            RunRequest(request_id="r", query="q", cited_answer=True, **kwargs)


def test_cited_answer_is_rejected_with_a_task_plan_or_model_planning():
    plan = {"goal": "q"}
    with pytest.raises(ValidationError, match="without a TaskPlan"):
        RunRequest(request_id="r", workflow="team_decision", query="q", cited_answer=True, task_plan=plan)
    with pytest.raises(ValidationError, match="without a TaskPlan"):
        RunRequest(request_id="r", workflow="team_decision", query="q", cited_answer=True, planning_mode="model")
    # graph.py adds the model-proposed TaskPlan with dataclasses.replace, which re-runs this validation.
    request = RunRequest(request_id="r", workflow="team_decision", query="q", cited_answer=True)
    with pytest.raises(ValidationError, match="without a TaskPlan"):
        replace(request, task_plan=plan)
