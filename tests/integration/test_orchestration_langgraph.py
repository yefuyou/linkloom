"""LangGraph orchestration with the real RuntimeEngine, Reviewer gate and synthesis (offline fake models)."""
from dataclasses import replace
import json
import threading
from pathlib import Path
from threading import Barrier

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelProviderError, ModelResponse, ModelUsage
from linkloom.orchestration import OrchestrationLimits, Orchestrator, WorkerDeps, open_checkpointer
from linkloom.orchestration.state import ABSTAINED, ACCEPTED, FAILED, PENDING, RUNNING, SKIPPED, merge_first, merge_tasks
from linkloom.orchestration.supervisor import DelegationRejected, accept_delegation
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.ui.projection import WorkspacePresentationContext
from linkloom.ui.task_provider import TaskProviderLimits

NOTES = {
    "succ.md": "Cobalt B replaced Meridian A on 2025-02-14",
    "owner.md": "Mira Chen is the accountable owner of Cobalt B",
    "leader.md": "Mira Chen reports directly to Kai Wu",
    "risk.md": "The Cobalt B rollout risk is tracked by Pat Lee",
}
TOTAL = TaskProviderLimits(80, 20_000_000, 400_000, 30.0, 120, 20, .01)


def delegation(action, tasks=(), reason="test"):
    return dict(action=action, reason=reason, tasks=[
        dict(id=t[0], description=t[1], depends_on=list(t[2]), spawn_reason=t[3] if len(t) > 3 else "initial")
        for t in tasks])


class Crash(BaseException):
    """Simulates a process stop: not an Exception, so no Worker or node swallows it."""


class WorkerModel:
    """search -> read the note holding the fact -> Final. mode: ok | abstain | error | gap | crash."""

    def __init__(self, world, description, fact, query, mode):
        self.world, self.description, self.fact, self.query, self.mode = world, description, fact, query, mode
        self.calls, self.refs, self.requests = 0, [], []

    def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            self.world.started.append(self.description)
            if self.world.barrier and self.description in self.world.barrier_for:
                self.world.barrier.wait(timeout=10)
            if self.mode == "uncertain":
                return ModelResponse(error=ModelProviderError("MODEL_UNKNOWN_FAILURE", "unknown_provider_error",
                    "Synthetic Provider failure.", outcome="unknown_provider_outcome"))
            if self.mode == "auth":
                return ModelResponse(error=ModelProviderError("MODEL_AUTH_REQUIRED", "authentication",
                    "Synthetic credential failure.", outcome="known_failure"))
            if self.mode == "error":
                return ModelResponse(error=ModelProviderError("MODEL_UNAVAILABLE", "unavailable_model",
                    "Synthetic Provider failure.", outcome="known_failure"))
        usage = ModelUsage(input_tokens=10, output_tokens=10)
        if request.observation and isinstance(request.observation.value, dict):
            self.refs.append(request.observation.value["evidence_id"])
        if self.calls == 3:
            gap = self.mode == "gap"
            missing = self.mode == "abstain"
            refs = [] if missing else self.refs
            return ModelResponse(action=ModelAction.final(json.dumps(dict(
                schema_version="team-decision-result/v1",
                decision=dict(value=None if missing else self.fact,
                              status="insufficient_evidence" if missing else "partial" if gap else "approved",
                              evidence_refs=refs),
                rationale=[], rejected_alternatives=[], actions=[], unresolved_items=[],
                uncertainty=dict(status="insufficient_evidence" if missing else "partial" if gap else "none",
                                 statement="Who verified the rollout owner?" if gap else None,
                                 unknown_fields=["answer"] if missing else ["rollout owner"] if gap else [],
                                 evidence_refs=refs if gap else []),
                evidence_refs=refs))), usage=usage)
        if self.calls == 1:
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            action = ModelAction.tool(ToolCall("search-1", "search_notes",
                dict(query=self.query, limit=5, source_context=context)))
        else:
            hit = next(hit for hit in request.observation.value if self.fact in hit["quote"])
            action = ModelAction.tool(ToolCall("read-2", "read_verified_note", dict(note_ref=hit["evidence_id"])))
        return ModelResponse(action=action, usage=usage)


class World:
    """Scripted Supervisor decisions plus per-task Worker behaviour, shared by every model the factory builds."""

    def __init__(self, decisions, tasks, barrier_for=(), barrier=None):
        self.decisions, self.tasks = list(decisions), tasks
        self.barrier, self.barrier_for = barrier, set(barrier_for)
        self.started, self.supervisor_requests, self.workers = [], [], []
        self.crash_once = set()  # a task whose first Worker start dies like a stopped process
        self.lock = threading.Lock()

    def factory(self, text):
        world = self
        spec = self.tasks.get(text)
        with self.lock:
            if text in self.crash_once:
                self.crash_once.discard(text)
                raise Crash()

        class Router:
            def complete(self, request):
                if request.agent_id == "slot-selector":  # Ask's one intent call for an explicit scope; no Worker
                    from tests.integration.unified_ask_fixture import answer_slot_selector
                    return answer_slot_selector(request)
                if request.agent_id == "orchestration-supervisor":
                    with world.lock:
                        world.supervisor_requests.append(request)
                        payload = world.decisions.pop(0)
                    return ModelResponse(action=ModelAction.tool(
                        ToolCall(f"delegate-{len(world.supervisor_requests)}", "submit_delegation", payload)),
                        usage=ModelUsage(input_tokens=10, output_tokens=10))
                return self.worker().complete(request)

            def worker(self):
                if not hasattr(self, "_worker"):
                    fact, query, mode = spec
                    self._worker = WorkerModel(world, text, fact, query, mode)
                    world.workers.append(self._worker)
                return self._worker
        return Router()


def build(tmp_path, world, *, limits=None, name="run"):
    vault = tmp_path / "vault"
    if not vault.exists():
        vault.mkdir()
        for path, text in NOTES.items():
            (vault / path).write_text(text, encoding="utf-8")
    marker = tmp_path / "index-path.txt"
    if not marker.exists():
        marker.write_text(str(scan_vault(vault, tmp_path / "scan").index_path), encoding="utf-8")
    index = marker.read_text(encoding="utf-8")
    limits = limits or OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=2, max_concurrency=3)
    deps = WorkerDeps(root=tmp_path / name, run_id=name, vault=vault, index=index,
        workspace=WorkspacePresentationContext("Test", len(NOTES)), workspace_id="ws",
        source_names={path: path for path in NOTES},
        source_versions={path: dict(source_id=f"src-{path}", source_version="v1") for path in NOTES},
        model_factory=world.factory, validate=lambda: None, limits=limits)
    saver = open_checkpointer(tmp_path / f"{name}.sqlite")
    return Orchestrator(deps, saver), deps


FACT = {key: text for key, text in zip(("succ", "owner", "leader", "risk"), NOTES.values())}
TASKS = {
    "Which plan replaced Meridian A?": (FACT["succ"], "Meridian A", "ok"),
    "Who owns Cobalt B?": (FACT["owner"], "Cobalt B", "ok"),
    "Who is the direct leader of Mira Chen?": (FACT["leader"], "Mira Chen", "ok"),
    "Who tracks the rollout risk?": (FACT["risk"], "rollout risk", "ok"),
}
Q_SUCC, Q_OWNER, Q_LEADER, Q_RISK = TASKS
GOAL = "Which plan replaced Meridian A, who owns it and who leads that owner?"


def events(result, name):
    return [event for event in result["trace"] if event["event"] == name]


def test_supervisor_declines_delegation_and_one_worker_answers(tmp_path):
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, _ = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    assert list(result["tasks"]) == ["task-1"] and result["tasks"]["task-1"]["status"] == ACCEPTED
    assert result["status"] == "COMPLETE" and len(world.workers) == 1 and len(world.supervisor_requests) == 1
    assert events(result, "wave_dispatched") == [dict(event="wave_dispatched", tasks=["task-1"])]
    answer = result["answer"]
    assert answer["published"] is True and answer["answers"][0]["citations"]


def test_independent_tasks_share_a_wave_and_dependent_task_waits_for_verified_results(tmp_path):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"], "needs both")]),
                   delegation("stop")], TASKS, barrier_for=(Q_SUCC, Q_OWNER), barrier=Barrier(2))
    orchestrator, _ = build(tmp_path, world)
    scope = dict(workspace_id="ws", as_of="2025-02-20")
    result = orchestrator.start(GOAL, confirmed_scope=scope, entity_leads=["Meridian A"])
    waves = [event["tasks"] for event in events(result, "wave_dispatched")]
    assert waves == [["owner", "succ"], ["leader"]]  # Barrier(2) passed: both ran at the same time
    assert world.started.index(Q_LEADER) > max(world.started.index(Q_SUCC), world.started.index(Q_OWNER))
    leader = next(worker for worker in world.workers if worker.description == Q_LEADER)
    text = json.loads(leader.requests[0].user_input.rsplit("team_decision_request=", 1)[1])["query"]
    structured = json.loads(text.split("investigation_context=", 1)[1])
    assert structured["entity_leads_unverified"] == ["Meridian A"]
    assert structured["confirmed_scope"] == scope
    assert "verified_upstream_results" in text and FACT["succ"] in text and FACT["owner"] in text
    # the Host-confirmed scope and the unconfirmed entity lead reach Workers as separate fields
    assert result["status"] == "COMPLETE"
    assert {item["task_id"] for item in result["verified_evidence"].values()} == {"succ", "owner", "leader"}
    assert result["approved_decisions"] == []  # inferences never become approved decisions
    assert set(result["inferences"]) == {"succ", "owner", "leader"}
    assert len(result["answer"]["answers"]) == 3 and result["answer"]["conflicts"] == []


def test_worker_gap_lets_supervisor_spawn_a_supplementary_task(tmp_path):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", "gap")
    world = World([delegation("delegate", [("owner", Q_OWNER, [])]),
                   delegation("delegate", [("risk", Q_RISK, ["owner"], "owner gap needs the risk tracker")]),
                   delegation("stop")], tasks)
    orchestrator, _ = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    review = world.supervisor_requests[1].user_input
    assert "Who verified the rollout owner?" in review  # the structured gap reached the Supervisor
    assert [event["tasks"] for event in events(result, "wave_dispatched")] == [["owner"], ["risk"]]
    assert result["tasks"]["risk"]["spawn_reason"] == "owner gap needs the risk tracker"
    assert result["tasks"]["risk"]["round"] == 1 and result["tasks"]["risk"]["depends_on"] == ["owner"]
    assert result["status"] == "PARTIAL"  # the unresolved item stays visible
    assert {"owner", "risk"} <= set(result["verified_evidence"][k]["task_id"] for k in result["verified_evidence"])


def test_failed_and_abstained_tasks_keep_other_results_as_partial(tmp_path):
    tasks = {Q_SUCC: (FACT["succ"], "Meridian A", "ok"), Q_OWNER: (FACT["owner"], "Cobalt B", "error"),
             Q_LEADER: (FACT["leader"], "Mira Chen", "abstain"),
             Q_RISK: (FACT["risk"], "rollout risk", "ok")}
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, []), ("risk", Q_RISK, ["owner"])])], tasks)
    limits = OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=0, max_concurrency=4)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    status = {task_id: task["status"] for task_id, task in result["tasks"].items()}
    assert status == dict(succ=ACCEPTED, owner=FAILED, leader=ABSTAINED, risk=SKIPPED)
    assert result["tasks"]["risk"]["failure"] == "UPSTREAM_UNAVAILABLE"
    assert result["status"] == "PARTIAL"
    answer = result["answer"]
    assert [a["question"] for a in answer["answers"]] == [Q_SUCC]
    reasons = {(u["branch"], u["reason"]) for u in answer["unknowns"]}
    assert reasons == {("owner", "BRANCH_FAILED"), ("leader", "BRANCH_NO_DECISION"), ("risk", "BRANCH_SKIPPED")}
    owners = {item["task_id"] for item in result["verified_evidence"].values()}
    assert "succ" in owners and not owners & {"owner", "risk"}


def test_local_worker_budget_failure_keeps_receipt_and_does_not_retry_or_review(tmp_path):
    world = World([delegation("answer_single"), delegation("delegate", [("again", GOAL, [])])],
                  {GOAL: (FACT["succ"], "Meridian A", "ok")})
    limits = OrchestrationLimits.split(TOTAL, max_tasks=3, max_rounds=1, max_attempts=2)
    limits = replace(limits, per_task=replace(limits.per_task, max_cost_usd=.001))
    orchestrator, _ = build(tmp_path, world, limits=limits)

    result = orchestrator.start(GOAL)

    assert result["status"] == "UNKNOWN"
    task, = result["tasks"].values()
    assert task["failure"] == "TASK_PROVIDER_BUDGET_EXHAUSTED"
    assert task["attempt"] == result["dispatches"] == 1
    assert len(world.supervisor_requests) == 1
    assert world.workers == []


def test_every_task_failing_gives_unknown(tmp_path):
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "error")})
    limits = OrchestrationLimits.split(TOTAL, max_tasks=2, max_rounds=0)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert result["status"] == "UNKNOWN" and result["tasks"]["task-1"]["status"] == FAILED
    assert not result["verified_evidence"]


def test_resume_after_a_crash_does_not_rerun_accepted_tasks(tmp_path):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    world.crash_once.add(Q_OWNER)
    orchestrator, deps = build(tmp_path, world)
    with pytest.raises(Crash):
        orchestrator.start(GOAL)
    succ_models = [worker for worker in world.workers if worker.description == Q_SUCC]
    assert len(succ_models) == 1 and succ_models[0].calls == 3
    # a new process: new Orchestrator and new SqliteSaver over the same file
    restarted, _ = build(tmp_path, world)
    result = restarted.resume()
    assert result["status"] == "COMPLETE"
    assert [w.calls for w in world.workers if w.description == Q_SUCC] == [3]  # no second paid run
    assert len(world.supervisor_requests) == 1 and len(events(result, "wave_dispatched")) == 1
    assert {task["status"] for task in result["tasks"].values()} == {ACCEPTED}
    # resuming a finished run changes nothing and pays nothing
    calls = sum(w.calls for w in world.workers)
    assert restarted.resume()["status"] == "COMPLETE" and sum(w.calls for w in world.workers) == calls


def test_replayed_worker_with_a_receipt_makes_no_provider_call(tmp_path):
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, deps = build(tmp_path, world)
    orchestrator.start(GOAL)
    from linkloom.orchestration.worker import build_worker_graph
    task = dict(id="task-1", description=GOAL, depends_on=[], status=RUNNING, attempt=1, epoch=0,
                depth=1, round=0, spawn_reason="x", result_ref=None, failure=None)
    before = sum(w.calls for w in world.workers)
    out = build_worker_graph(deps).invoke(dict(task=task, upstream=[], context={}))
    assert sum(w.calls for w in world.workers) == before
    assert out["tasks"]["task-1"]["status"] == ACCEPTED


def test_plan_above_threshold_waits_for_human_confirmation(tmp_path):
    plan = delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])])
    limits = OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=0, confirm_over_tasks=1)
    world = World([plan], TASKS)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    paused = orchestrator.start(GOAL)
    assert paused["__interrupt__"][0].value["kind"] == "delegation_confirmation"
    assert not world.workers and orchestrator.state()["next"] == ["confirm"]
    result = orchestrator.resume(dict(approved=True, reviewer_id="local", reason="ok"))
    assert result["status"] == "COMPLETE" and len(world.workers) == 2
    assert result["approved_decisions"][0]["reviewer_id"] == "local"

    world2 = World([plan], TASKS)
    rejected, _ = build(tmp_path, world2, limits=limits, name="run2")
    rejected.start(GOAL)
    result = rejected.resume(dict(approved=False))
    assert result["status"] == "UNKNOWN" and not world2.workers
    assert {task["failure"] for task in result["tasks"].values()} == {"PLAN_REJECTED"}


def test_concurrency_limit_splits_ready_tasks_into_waves(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, []), ("c", Q_LEADER, [])]),
                   delegation("stop")], TASKS)
    limits = OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=1, max_concurrency=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert [event["tasks"] for event in events(result, "wave_dispatched")] == [["a", "b"], ["c"]]
    assert result["status"] == "COMPLETE"


def test_invalid_first_plan_falls_back_to_one_task_for_the_goal(tmp_path):
    cycle = delegation("delegate", [("a", Q_SUCC, ["b"]), ("b", Q_OWNER, ["a"])])
    world = World([cycle], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    limits = OrchestrationLimits.split(TOTAL, max_tasks=3, max_rounds=0)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert list(result["tasks"]) == ["task-1"]
    assert result["tasks"]["task-1"]["spawn_reason"] == "HOST_FALLBACK_SUPERVISOR_DEPENDENCY_CYCLE"
    assert result["status"] == "COMPLETE"


def _limits(**options):
    return OrchestrationLimits.split(TOTAL, **options)


def _propose(tasks, existing=None, limits=None, blocked=()):
    return accept_delegation(delegation("delegate", tasks), query=GOAL, existing=existing or {},
                             limits=limits or _limits(max_tasks=4, max_depth=2), blocked=blocked, round_number=0)


@pytest.mark.parametrize("tasks,code", [
    ([("a", "x", ["b"]), ("b", "y", ["a"])], "SUPERVISOR_DEPENDENCY_CYCLE"),
    ([("a", "x", ["a"])], "SUPERVISOR_DEPENDENCY_UNKNOWN"),
    ([("a", "x", ["zzz"])], "SUPERVISOR_DEPENDENCY_UNKNOWN"),
    ([("a", "x", []), ("a", "y", [])], "SUPERVISOR_TASK_ID_DUPLICATE"),
    ([("a", "x", []), ("b", "y", ["a"]), ("c", "z", ["b"])], "SUPERVISOR_DEPTH_LIMIT"),
    ([(str(n), "x", []) for n in range(5)], "SUPERVISOR_TASK_LIMIT"),
    ([("bad id", "x", [])], "SUPERVISOR_TASK_ID_INVALID"),
    ([], "SUPERVISOR_NO_TASKS"),
])
def test_host_rejects_unsafe_delegation(tasks, code):
    with pytest.raises(DelegationRejected, match=code):
        _propose(tasks)


def test_host_blocks_entities_outside_the_authorized_scope_and_unverified_dependencies():
    with pytest.raises(DelegationRejected, match="SUPERVISOR_ENTITY_BLOCKED"):
        _propose([("a", "Who owns Project Zephyr?", [])], blocked=[["Project Zephyr", "OUT_OF_SCOPE"]])
    existing = {"old": dict(id="old", description="d", depends_on=[], status=FAILED, depth=1)}
    with pytest.raises(DelegationRejected, match="SUPERVISOR_DEPENDENCY_NOT_VERIFIED"):
        _propose([("a", "x", ["old"])], existing=existing)
    with pytest.raises(DelegationRejected, match="SUPERVISOR_TASK_ID_DUPLICATE"):
        _propose([("old", "x", [])], existing=existing)


def test_limits_come_from_the_host_budget_not_the_model():
    limits = _limits(max_tasks=4, max_rounds=2)
    assert limits.task_cap() == 4 and limits.dispatch_cap() == 4
    tight = OrchestrationLimits(provider=TOTAL, per_task=TaskProviderLimits(20, 5_000_000, 100_000, 8.0, 60, 10, .01),
                                supervisor=TaskProviderLimits(1, 100_000, 4096, .1, 30, 10, .01), max_tasks=9)
    assert tight.task_cap() < 9  # four Workers' slices plus the Supervisor reserve exhaust the total


def test_reducers_are_monotonic_and_never_overwrite_accepted_results():
    base = dict(id="a", status=PENDING, epoch=0, attempt=0)
    merged = merge_tasks({"a": base}, {"a": dict(base, status=RUNNING, attempt=1)})
    merged = merge_tasks(merged, {"a": dict(base, status=ACCEPTED, attempt=1)})
    assert merged["a"]["status"] == ACCEPTED
    assert merge_tasks(merged, {"a": dict(base, status=FAILED, attempt=2, epoch=1)})["a"]["status"] == ACCEPTED
    assert merge_tasks({"a": dict(base, status=RUNNING, attempt=1)}, {"a": base})["a"]["status"] == RUNNING
    failed = dict(base, status=FAILED, attempt=1)
    assert merge_tasks({"a": failed}, {"a": dict(base, epoch=1)})["a"]["status"] == PENDING  # a retry epoch
    assert merge_first({"k": 1}, {"k": 2, "j": 3}) == {"k": 1, "j": 3}


def test_retryable_failure_is_retried_within_the_attempt_limit_only(tmp_path):
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "error")})
    limits = OrchestrationLimits.split(TOTAL, max_tasks=2, max_rounds=0, max_attempts=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert [event["tasks"] for event in events(result, "wave_dispatched")] == [["task-1"], ["task-1"]]
    assert result["tasks"]["task-1"]["attempt"] == 2 and result["dispatches"] == 2
    assert len(world.workers) == 2 and result["status"] == "UNKNOWN"


def _kill_before_receipt(monkeypatch, module, name):
    """Die exactly when the receipt `name` would be written: after the paid provider call returned."""
    real, state = module.write_json, dict(fired=False)

    def write(path, data):
        if Path(path).name == name and not state["fired"]:
            state["fired"] = True
            raise Crash()
        return real(path, data)
    monkeypatch.setattr(module, "write_json", write)
    return state


def test_kill_after_a_worker_request_before_its_receipt_is_never_paid_again(tmp_path, monkeypatch):
    from linkloom.orchestration import worker as worker_module
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    orchestrator, deps = build(tmp_path, world, limits=OrchestrationLimits.split(
        TOTAL, max_tasks=4, max_rounds=2, max_concurrency=1))
    state = _kill_before_receipt(monkeypatch, worker_module, "status.json")
    with pytest.raises(Crash):
        orchestrator.start(GOAL)
    assert state["fired"]
    paid = {w.description: w.calls for w in world.workers}
    assert paid and all(calls == 3 for calls in paid.values())  # a request was sent, the receipt was not written
    monkeypatch.undo()
    restarted, _ = build(tmp_path, world)
    result = restarted.resume()
    # the killed task was not sent again; its outcome is unknown, so it is a non-retryable gap
    assert {w.description: w.calls for w in world.workers if w.description in paid} == paid
    uncertain = [t for t in result["tasks"].values() if t["failure"] == "PROVIDER_UNCERTAIN"]
    assert len(uncertain) == 1 and uncertain[0]["status"] == FAILED and uncertain[0]["retryable"] is False
    assert result["status"] in {"PARTIAL", "UNKNOWN"}
    assert len(world.supervisor_requests) == 2  # the first plan once, plus the later review of the gap


def test_kill_after_the_supervisor_request_before_its_proposal_is_never_paid_again(tmp_path, monkeypatch):
    from linkloom.orchestration import graph as graph_module
    world = World([delegation("answer_single"), delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, _ = build(tmp_path, world)
    state = _kill_before_receipt(monkeypatch, graph_module, "proposal.json")
    with pytest.raises(Crash):
        orchestrator.start(GOAL)
    assert state["fired"] and len(world.supervisor_requests) == 1
    monkeypatch.undo()
    restarted, _ = build(tmp_path, world)
    result = restarted.resume()
    assert len(world.supervisor_requests) == 1  # the unknown outcome is not re-sent
    assert "HOST_FALLBACK" in result["tasks"]["task-1"]["spawn_reason"]  # fail closed: one task for the goal


def test_a_kill_before_any_request_is_still_replayed(tmp_path):
    world = World([delegation("delegate", [("succ", Q_SUCC, [])]), delegation("stop")], TASKS)
    world.crash_once.add(Q_SUCC)  # dies in the model factory, before a request: nothing was reserved
    orchestrator, _ = build(tmp_path, world)
    with pytest.raises(Crash):
        orchestrator.start(GOAL)
    result = build(tmp_path, world)[0].resume()
    assert result["tasks"]["succ"]["status"] == ACCEPTED


def test_approved_plan_records_the_reviewer_as_self_asserted(tmp_path):
    plan = delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])])
    limits = OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=0, confirm_over_tasks=1)
    orchestrator, _ = build(tmp_path, World([plan], TASKS), limits=limits)
    orchestrator.start(GOAL)
    result = orchestrator.resume(dict(approved=True, reviewer_id="anyone", reason="ok"))
    assert result["approved_decisions"][0]["reviewer_identity"] == "self_asserted"


def _provider_threads():
    return [t for t in threading.enumerate() if t.name.startswith("task-provider-") and t.is_alive()]


def test_worker_session_is_closed_after_a_normal_run_and_leaves_no_provider_thread(tmp_path):
    from linkloom.orchestration import worker as worker_module
    world = World([delegation("answer_single")], TASKS)
    _, deps = build(tmp_path, world)
    closed = []
    real = worker_module.close_session
    worker_module.close_session = lambda session, *a, **k: closed.append(real(session, *a, **k)) or closed[-1]
    try:
        receipt = worker_module._run(deps, dict(id="t", description=Q_SUCC, epoch=0, attempt=1), Q_SUCC)
    finally:
        worker_module.close_session = real
    assert receipt["failure"] is None and receipt["run_status"]["status"] == "completed"
    assert closed == [True] and _provider_threads() == []


def test_a_timed_out_provider_call_is_uncertain_and_its_session_stops_accepting_work(tmp_path):
    from dataclasses import replace
    from linkloom.orchestration import worker as worker_module
    release = threading.Event()

    class Hang:
        def complete(self, request):
            release.wait(timeout=20)
            return ModelResponse(error=ModelProviderError("MODEL_UNAVAILABLE", "unavailable_model", "late",
                                                          outcome="known_failure"))
    world = World([], {})
    limits = OrchestrationLimits.split(TOTAL, max_tasks=4, max_rounds=2, max_concurrency=1)
    limits = replace(limits, per_task=replace(limits.per_task, request_timeout_seconds=0.2))
    _, deps = build(tmp_path, world, limits=limits)
    deps.model_factory = lambda text: Hang()
    try:
        receipt = worker_module._run(deps, dict(id="t", description=Q_SUCC, epoch=0, attempt=1), Q_SUCC)
        assert receipt["failure"] == "PROVIDER_TIMEOUT_UNCERTAIN"  # the in-flight outcome is never guessed
        budget = json.loads((worker_module.attempt_dir(deps, dict(id="t", epoch=0, attempt=1)) / "budget.json")
                            .read_text(encoding="utf-8"))
        assert budget["attempts"][0]["outcome"] == "UNCERTAIN" and budget["failure"] == "PROVIDER_TIMEOUT_UNCERTAIN"
        assert len(_provider_threads()) == 1  # still blocked inside the model, but owned and not accepting more
    finally:
        release.set()
    for thread in _provider_threads():
        thread.join(5)
    assert _provider_threads() == []
    # a replay of this attempt sends nothing again
    assert worker_module.paid_without_receipt(worker_module.attempt_dir(deps, dict(id="t", epoch=0, attempt=1)))


def test_session_is_closed_when_the_run_is_cancelled_by_a_base_exception(tmp_path):
    from linkloom.orchestration import worker as worker_module
    world = World([], TASKS)
    world.crash_once.add(Q_SUCC)
    _, deps = build(tmp_path, world)
    seen = []
    real = worker_module.TaskProviderSession

    class Spy(real):
        def __init__(self, *args):
            super().__init__(*args)
            seen.append(self)
    worker_module.TaskProviderSession = Spy
    try:
        with pytest.raises(Crash):
            worker_module._run(deps, dict(id="t", description=Q_SUCC, epoch=0, attempt=1), Q_SUCC)
    finally:
        worker_module.TaskProviderSession = real
    from linkloom.ui.lifecycle import BackgroundWorkStopped
    assert len(seen) == 1 and seen[0].pending_task_ids() == ()
    with pytest.raises(BackgroundWorkStopped):  # closed: no call can start afterwards
        seen[0]._calls.operation("late").__enter__()
