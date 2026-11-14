import json
from copy import deepcopy
from threading import Barrier, Event

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelProviderError, ModelResponse, ModelUsage
from linkloom.tools.contracts import ToolCall
from linkloom.ui.branch_planner import PlanRejected, PlannerScope, TOOL_ID, validate_branch_plan
from linkloom.ui.branch_budget import HARD_ABORT, SOFT_STOP
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_provider import TaskProviderLimits
from tests.integration.test_parallel_investigation import FACTS, QUESTION, MeetingProvider
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)

VALID = dict(branches=[
    dict(question="What plan replaced Meridian A?", entity_text="Meridian A", time_text="", steps=[
        dict(id="successor", question="What plan replaced Meridian A?", depends_on=[]),
        dict(id="owner", question="Who owns {verified_entity}?", depends_on=["successor"]),
        dict(id="leader", question="Who is the direct leader of {verified_entity}?", depends_on=["owner"])]),
    dict(question="When did the replacement for Meridian A take effect?", entity_text="Meridian A",
         time_text="", steps=[dict(id="when", question="When did the replacement for Meridian A take effect?",
                                   depends_on=[])]),
])


def variant(name):
    plan = deepcopy(VALID)
    first, second = plan["branches"]
    if name == "schema":
        plan["branches"] = plan["branches"][:1]
    elif name == "entity":
        first["entity_text"] = "Zephyr Z"
    elif name == "time":
        second["time_text"] = "next quarter"
    elif name == "dependent":
        second["steps"][0].update(question="Who owns {verified_entity}?", depends_on=["leader"])
    elif name == "subplan":
        first["steps"][1]["question"] = "Send an email to {verified_entity}"
    elif name == "decline":
        plan = dict(decline=True, reason="not splittable")
    return plan


class PlannerProvider:
    def __init__(self, behavior):
        self.behavior, self.calls, self.requests = behavior, 0, []

    def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.behavior == "raise":
            raise RuntimeError("synthetic transport failure")
        if self.behavior == "error":
            return ModelResponse(error=ModelProviderError("MODEL_UNAVAILABLE", "unavailable_model", "Synthetic."))
        payload = variant("valid" if self.behavior in ("valid", "truncate") else self.behavior)
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id="plan-1", tool_id=TOOL_ID, arguments=payload)),
                             usage=ModelUsage(input_tokens=10, output_tokens=10),
                             finish_reason="MAX_TOKENS" if self.behavior == "truncate" else "STOP")


def run_ask(tmp_path, monkeypatch, behavior, *, flag=True, query=QUESTION, expect_parallel=True):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    base, server, worker, app = _start_product_server(tmp_path, [
        _claim("Cobalt B"), _claim("Cobalt B", claim_type="PROPOSAL"),
        _claim("Cobalt B", claim_type="PROPOSAL"), _claim("Cobalt B", claim_type="PROPOSAL")])
    try:
        _, workspace = _client(base, "/api/workspaces", method="POST", data={"name": "Meetings"})
        workspace_id = workspace["workspace"]["workspace_id"]
        _, scope = _client(base, "/api/sources/import", method="POST", data=dict(
            name="approval.md", content="[2025-02-10T10:00:00Z] Alice: We decided to use Cobalt B as the supplier for Launch."))
        _wait_source(base, scope["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        status, approval = _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
            method="POST", data=dict(reviewer_id="local", reason="Synthetic reviewed scope"))
        assert status == 200, approval
        for index, fact in enumerate(FACTS, 1):
            _, imported = _client(base, "/api/sources/import", method="POST", data=dict(
                name=f"meeting-{index}.md", content=f"[2025-02-1{index}T10:00:00Z] Alice: {fact}."))
            _wait_source(base, imported["source"]["source_id"])
        overlapped, release = Event(), Event()
        barrier = Barrier(2, action=overlapped.set)
        planner, branches, asked = PlannerProvider(behavior), [], []

        def factory(question):
            if question == query and not planner.calls and not branches and not asked:
                asked.append(question)  # the first factory call for the full question is the Planner
                return planner
            branches.append(question)
            return MeetingProvider(question, barrier, "complete", release)

        limits = TaskProviderLimits(21, 2_000_000, 60_000, 3.0, 20, 10, .01)
        service = ProductInvestigationService(app, model_factory=factory, external_provider=False,
                                              parallel_limits=limits, branch_planner=flag)
        server.investigation_service = service
        status, payload = _client(base, "/api/product/ask", method="POST", data=dict(
            query=query, workspace_id=workspace_id, subject="Launch", relation="uses supplier", as_of="2025-02-20"))
        started = payload["result"]
        out = dict(status=status, started=started, planner=planner, branches=branches, overlapped=overlapped)
        if expect_parallel:
            assert status == 202, started
            run_id = started["agent_run_id"]
            service.close(timeout=25)
            out.update(final=service.inspect(run_id)["parallel_investigation"],
                       root=tmp_path / "work" / "investigations" / run_id.removeprefix("ui-parallel-"))
        else:
            service.close(timeout=25)
        out["service"] = service
        return out
    finally:
        _stop_product_server(server, worker, app)


def receipts(out):
    return {path.parent.name: json.loads(path.read_text()) for path in out["root"].glob("branches/*/budget.json")}


def test_valid_llm_plan_runs_two_branches_in_parallel_and_bills_the_pool(tmp_path, monkeypatch):
    out = run_ask(tmp_path, monkeypatch, "valid")
    assert out["overlapped"].is_set(), (out["final"], out["root"].joinpath("pool.json").read_text(), out["branches"], out["planner"].calls)  # both branch Providers met inside Barrier(2): truly concurrent
    assert out["planner"].calls == 1  # routing and _start both consult, the model is called once
    request = out["planner"].requests[0]
    assert request.generation_options.require_tool_call and request.available_tools[0].tool_id == TOOL_ID
    final = out["final"]
    assert final["status"] == "COMPLETE" and final["planner"] == dict(source="LLM", reason=None)
    assert [row["branch"] for row in final["branches"]] == ["branch-1", "branch-2"] and not final["gaps"]
    rows = receipts(out)
    assert set(rows) == {"planner", "branch-1", "branch-2"}
    assert rows["planner"]["requests_reserved"] == 1 and rows["planner"]["attempts"][0]["stage"] == "plan"
    assert rows["planner"]["attempts"][0]["outcome"] == "SUCCEEDED"
    assert sum(row["requests_reserved"] for row in rows.values()) <= 21


@pytest.mark.parametrize("behavior", ["schema", "entity", "time", "dependent", "subplan", "decline",
                                      "truncate", "error", "raise"])
def test_rejected_plan_degrades_to_template_split(tmp_path, monkeypatch, behavior):
    out = run_ask(tmp_path, monkeypatch, behavior)
    assert out["planner"].calls == 1  # no retry
    final = out["final"]
    assert final["planner"]["source"] == "TEMPLATE" and final["planner"]["reason"].startswith("PLANNER_")
    assert final["status"] == "COMPLETE" and len(final["branches"]) == 2
    assert out["branches"] and all(q != QUESTION for q in out["branches"])


def test_planner_and_template_empty_falls_back_to_single_path(tmp_path, monkeypatch):
    query = "Who owns Cobalt B?"
    out = run_ask(tmp_path, monkeypatch, "decline", query=query, expect_parallel=False)
    assert out["planner"].calls == 1
    assert "parallel_investigation" not in json.dumps(out["started"])


def test_flag_off_matches_baseline(tmp_path, monkeypatch):
    out = run_ask(tmp_path, monkeypatch, "valid", flag=False)
    assert out["planner"].calls == 0
    assert "planner" not in out["final"] and out["final"]["status"] == "COMPLETE"
    assert set(receipts(out)) == {"branch-1", "branch-2"}


def test_planner_is_called_at_most_once_per_job(tmp_path):
    from linkloom.ui.parallel_investigation import ParallelInvestigation
    job = ParallelInvestigation(tmp_path, "run-1", QUESTION, None, TaskProviderLimits(21, 2_000_000, 60_000, 3.0, 20, 10, .01),
                                lambda: None, (), lambda q: PlannerProvider("decline"), tmp_path, tmp_path, {}, {}, "ws", True)
    job.plan("2025-02-20", 12, None)
    with pytest.raises(RuntimeError):
        job.plan("2025-02-20", 12, None)
    job.pool.close()


def _job(tmp_path, provider, *, seconds=20, validate=lambda: None):
    from linkloom.ui.parallel_investigation import ParallelInvestigation
    return ParallelInvestigation(tmp_path, "run-1", QUESTION, None, TaskProviderLimits(21, 2_000_000, 60_000, 3.0, seconds, min(10, seconds), .01),
                                 validate, (), lambda q: provider, tmp_path, tmp_path, {}, {}, "ws", True)


def test_missing_blocked_entity_provider_fails_closed(tmp_path):
    provider = PlannerProvider("valid")
    job = _job(tmp_path, provider)
    job.plan("2025-02-20", 12, None)
    assert provider.calls == 0 and job.planner["reason"] == "PLANNER_CONTEXT_UNAVAILABLE"
    job.pool.close()


def test_planner_timeout_stops_pool_and_ignores_late_plan(tmp_path):
    entered, release = Event(), Event()

    class Blocking(PlannerProvider):
        def complete(self, request):
            entered.set()
            release.wait(10)
            return super().complete(request)

    job = _job(tmp_path, Blocking("valid"), seconds=0.3)
    job.plan("2025-02-20", 12, lambda: ())
    try:
        assert entered.is_set() and job.pool.stop_state == (SOFT_STOP, "PLANNER_TIMEOUT")
        assert job.planner["source"] != "LLM" and job.planner["reason"] == "PLANNER_TIMEOUT"
    finally:
        release.set()
        job.pool.close()


def test_authorization_change_during_planning_aborts(tmp_path):
    calls = []

    def validate():
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("SOURCE_CHANGED")

    provider = PlannerProvider("valid")
    job = _job(tmp_path, provider, validate=validate)
    job.plan("2025-02-20", 12, lambda: ())
    assert job.pool.stop_state == (HARD_ABORT, "TASK_SOURCE_AUTHORIZATION_CHANGED")
    assert job.planner["source"] != "LLM"
    job.pool.close()


SCOPE = PlannerScope(QUESTION, "2025-02-20", 12, (), 7, 20_000)


def test_validator_accepts_valid_and_enforces_each_rule():
    assert len(validate_branch_plan(deepcopy(VALID), SCOPE)) == 2
    for name, code in (("schema", "SCHEMA"), ("entity", "ENTITY_NOT_LITERAL"), ("time", "TIME_NOT_LITERAL"),
                       ("dependent", "BRANCHES_DEPENDENT"), ("subplan", "PLAN_INVALID"), ("decline", "DECLINED")):
        with pytest.raises(PlanRejected, match=code):
            validate_branch_plan(variant(name), SCOPE)
    with pytest.raises(PlanRejected, match="PLAN_INVALID"):  # the shared task-plan validator bounds each branch
        validate_branch_plan(deepcopy(VALID), PlannerScope(QUESTION, "2025-02-20", 6, (), 7, 20_000))
    with pytest.raises(PlanRejected, match="BUDGET_INSUFFICIENT"):
        validate_branch_plan(deepcopy(VALID), PlannerScope(QUESTION, "2025-02-20", 12, (), 4, 20_000))
    with pytest.raises(PlanRejected, match="ENTITY_BLOCKED"):
        validate_branch_plan(deepcopy(VALID), PlannerScope(QUESTION, "2025-02-20", 12, (("Meridian A", "WORKSPACE_DENIED"),), 7, 20_000))
    dated = variant("valid")
    dated["branches"][1]["time_text"] = ""
    dated["branches"][1]["question"] = "When did the replacement for Meridian A take effect on 2024-01-01?"
    with pytest.raises(PlanRejected):
        validate_branch_plan(dated, SCOPE)
