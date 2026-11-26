"""Functional acceptance gaps of the LangGraph orchestration (scripted models, isolated data, no Provider call).

Fills the gaps found by the E1-E12 coverage audit; the scenarios that already have a test are only referenced
in the report. Tests marked xfail(strict=True, reason="KNOWN_LIMITATION ...") describe behaviour the production
code is known not to have (semantic judgement done by exact string or name matching). They are recorded, not
fixed, in the change that adds them: a strict xfail turns into a failure the day the limitation is removed, so it
cannot be forgotten. The two E7 limitations are fixed by the bounded claim comparison and are regular tests now. No test here adds a natural-language rule to production code or loosens an expectation to pass.
"""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import threading
from threading import Barrier

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.orchestration import OrchestrationLimits
from linkloom.orchestration.comparison import compare_pending
from linkloom.orchestration.state import ACCEPTED
from linkloom.orchestration.supervisor import DelegationRejected
from linkloom.tools.contracts import ToolCall
from linkloom.ui import branch_synthesis as bs
from tests.integration import test_orchestration_langgraph as lg
from tests.integration.test_orchestration_langgraph import (
    FACT, GOAL, Q_LEADER, Q_OWNER, Q_RISK, Q_SUCC, TASKS, TOTAL, World, build, delegation, events, run_limits,
)
from tests.integration.test_orchestration_product import (  # noqa: F401 (pytest fixture)
    assert_cited, make_env, started, wait_idle, waves,
)
from tests.integration.test_product_application_flow import _client
from tests.unit.test_branch_synthesis import _item, _row

GAP = "gap"  # WorkerModel mode: the Worker answers but reports an unresolved item, so the Supervisor may review


def gap_tasks(*questions):
    return {question: (FACT["succ"], "Meridian A", GAP) for question in questions}


def decision_log(result):
    return [(event["round"], event["action"], event["reason"], event["tasks"])
            for event in events(result, "supervisor_decision")]


# ---------------------------------------------------------------- E3 dependency order in the trace

def test_e3_dependent_task_is_dispatched_only_after_both_upstream_tasks_are_accepted(tmp_path):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"], "needs both")]),
                   delegation("stop")], TASKS)
    orchestrator, _ = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    order = [(event["event"], event.get("task") or tuple(event.get("tasks", ()))) for event in result["trace"]
             if event["event"] in ("worker_finished", "wave_dispatched")]
    leader_wave = order.index(("wave_dispatched", ("leader",)))
    finished_before = {task for kind, task in order[:leader_wave] if kind == "worker_finished"}
    assert finished_before == {"succ", "owner"}
    accepted = {event["task"] for event in events(result, "worker_finished") if event["status"] == ACCEPTED}
    assert {"succ", "owner"} <= accepted


# ---------------------------------------------------------------- E4 supplementary task identity and journal

def test_e4_supplementary_task_has_a_new_id_reason_round_and_its_own_provider_journal(tmp_path):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", GAP)
    world = World([delegation("delegate", [("owner", Q_OWNER, [])]),
                   delegation("delegate", [("risk", Q_RISK, ["owner"], "owner gap needs the risk tracker")]),
                   delegation("stop")], tasks)
    orchestrator, deps = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    first, spawned = result["tasks"]["owner"], result["tasks"]["risk"]
    assert spawned["id"] != first["id"] and set(result["tasks"]) == {"owner", "risk"}
    assert (first["round"], spawned["round"]) == (0, 1)
    assert spawned["spawn_reason"] == "owner gap needs the risk tracker" and spawned["spawn_reason"] != first["spawn_reason"]
    assert spawned["depth"] == 2 and spawned["attempt"] == 1 and spawned["status"] == ACCEPTED
    assert result["dispatches"] == 2 and decision_log(result)[1][:2] == (1, "delegate")
    # each paid call has its own receipt: one for the review decision, one for the spawned Worker
    receipt = json.loads((deps.root / "tasks" / "risk" / "attempt-0-1" / "provider_journal.json").read_text(encoding="utf-8"))
    assert receipt["requests"] >= 3 and receipt["attempts"]
    review = json.loads((deps.root / "supervisor" / "round-1" / "provider_journal.json").read_text(encoding="utf-8"))
    assert review["requests"] == 1
    # the first Worker's receipt is a different file: the spawn did not reuse or overwrite it
    assert (deps.root / "tasks" / "owner" / "attempt-0-1" / "provider_journal.json").is_file()
    assert deps.limits.dispatch_cap() >= result["dispatches"]


# ---------------------------------------------------------------- E5 limits stop the run

def test_e5_rounds_stop_the_review_loop_even_when_the_supervisor_keeps_proposing_work(tmp_path):
    questions = [Q_SUCC, Q_OWNER, Q_LEADER, Q_RISK, "Who reviewed the rollout?", "Who funds the rollout?"]
    plans = [delegation("delegate", [(f"t{i}", question, [], f"gap {i}")]) for i, question in enumerate(questions)]
    world = World(plans, {**TASKS, **gap_tasks(*questions)})
    limits = run_limits(max_tasks=6, max_rounds=2, max_concurrency=3)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert len(world.supervisor_requests) == limits.max_rounds + 1 == 3
    assert len(world.decisions) == len(questions) - 3  # proposals were available; the Host did not ask for them
    assert sorted(result["tasks"]) == ["t0", "t1", "t2"] and result["dispatches"] == 3
    assert result["round"] == 3 and result["status"] == "PARTIAL"


def test_e5_a_review_round_still_runs_when_the_first_plan_fills_max_tasks(tmp_path):
    questions = [Q_SUCC, Q_OWNER]
    plan = delegation("delegate", [(f"t{i}", question, []) for i, question in enumerate(questions)])
    world = World([plan, delegation("delegate", [("extra", Q_LEADER, [])]), delegation("stop")],
                  {**TASKS, **gap_tasks(*questions)})
    limits = run_limits(max_tasks=2, max_followup_tasks=1, max_rounds=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert sorted(result["tasks"]) == ["extra", "t0", "t1"] and len(world.supervisor_requests) >= 2
    assert result["tasks"]["extra"]["round"] == 1 and result["tasks"]["extra"]["status"] == ACCEPTED


def test_e5_no_review_is_requested_once_the_followup_room_is_used(tmp_path):
    questions = [Q_SUCC, Q_OWNER]
    plan = delegation("delegate", [(f"t{i}", question, []) for i, question in enumerate(questions)])
    world = World([plan, delegation("delegate", [("extra", Q_LEADER, [])])], {**TASKS, **gap_tasks(*questions)})
    limits = run_limits(max_tasks=2, max_followup_tasks=0, max_rounds=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert sorted(result["tasks"]) == ["t0", "t1"] and len(world.supervisor_requests) == 1
    assert result["status"] == "PARTIAL" and "extra" not in result["tasks"]


def test_e5_a_review_that_exceeds_the_task_limit_is_refused_and_adds_nothing(tmp_path):
    questions = [Q_SUCC, Q_OWNER]
    plan = delegation("delegate", [(f"t{i}", question, []) for i, question in enumerate(questions)])
    over = delegation("delegate", [("x1", Q_LEADER, []), ("x2", Q_RISK, [])])  # room is 1
    world = World([plan, over], {**TASKS, **gap_tasks(*questions)})
    limits = run_limits(max_tasks=3, max_followup_tasks=0, max_rounds=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert sorted(result["tasks"]) == ["t0", "t1"] and result["dispatches"] == 2
    assert decision_log(result)[1][1:3] == ("stop", "SUPERVISOR_TASK_LIMIT")
    assert len(world.supervisor_requests) == 2


def test_e5_a_first_plan_over_the_task_limit_becomes_one_task_for_the_goal(tmp_path):
    plan = delegation("delegate", [(f"t{i}", question, []) for i, question in enumerate(TASKS)])
    world = World([plan], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    limits = run_limits(max_tasks=3, max_rounds=0)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert list(result["tasks"]) == ["task-1"] and len(world.workers) == 1
    assert result["tasks"]["task-1"]["spawn_reason"] == "HOST_FALLBACK_SUPERVISOR_TASK_LIMIT"


def test_e5_depth_limit_stops_a_chain_that_a_review_tries_to_extend(tmp_path):
    questions = [Q_SUCC, Q_OWNER, Q_LEADER]
    plans = [delegation("delegate", [("a", Q_SUCC, [])]),
             delegation("delegate", [("b", Q_OWNER, ["a"], "deeper")]),
             delegation("delegate", [("c", Q_LEADER, ["b"], "deeper still")])]  # depth 3 > max_depth 2
    world = World(plans, gap_tasks(*questions))
    limits = run_limits(max_tasks=5, max_rounds=3, max_depth=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert sorted(result["tasks"]) == ["a", "b"] and result["tasks"]["b"]["depth"] == 2
    assert decision_log(result)[-1][1:3] == ("stop", "SUPERVISOR_DEPTH_LIMIT")


def test_e5_a_first_plan_deeper_than_the_depth_limit_falls_back_to_one_task(tmp_path):
    chain = delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, ["a"]), ("c", Q_LEADER, ["b"])])
    world = World([chain], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    limits = run_limits(max_tasks=4, max_rounds=0, max_depth=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert result["tasks"]["task-1"]["spawn_reason"] == "HOST_FALLBACK_SUPERVISOR_DEPTH_LIMIT" and len(result["tasks"]) == 1


def test_e5_running_workers_never_exceed_the_concurrency_limit(tmp_path, monkeypatch):
    active, peak, lock = [0], [0], threading.Lock()
    real = lg.WorkerModel.complete

    def watched(self, request):
        first = self.calls == 0
        if first:
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
        try:
            return real(self, request)
        finally:
            if self.calls >= 3:
                with lock:
                    active[0] -= 1
    monkeypatch.setattr(lg.WorkerModel, "complete", watched)
    plan = delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, []), ("c", Q_LEADER, []), ("d", Q_RISK, [])])
    world = World([plan, delegation("stop")], TASKS, barrier_for=(Q_SUCC, Q_OWNER), barrier=Barrier(2))
    limits = run_limits(max_tasks=4, max_rounds=1, max_concurrency=2)
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert [event["tasks"] for event in events(result, "wave_dispatched")] == [["a", "b"], ["c", "d"]]
    assert peak[0] == 2 and active[0] == 0  # two ran together (the barrier proves it), never three


def test_e5_retries_stop_at_the_dispatch_cap_of_the_run_limits(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, [])]), delegation("stop")],
                  {Q_SUCC: (FACT["succ"], "Meridian A", "error"), Q_OWNER: (FACT["owner"], "Cobalt B", "error")})
    limits = run_limits(max_tasks=2, max_followup_tasks=0, max_rounds=1, max_attempts=2)
    assert limits.max_tasks * limits.max_attempts == 4 and limits.dispatch_cap() == 4
    orchestrator, _ = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert result["dispatches"] == limits.dispatch_cap() == 4
    assert sorted(task["attempt"] for task in result["tasks"].values()) == [2, 2] and result["status"] == "UNKNOWN"
    assert len(world.workers) == 4  # no fifth Worker


# ---------------------------------------------------------------- E7 conflicts

class Sequenced(dict):
    """Worker scripts that differ per start of the same task text (two Workers given one question)."""

    def __init__(self, base, repeated):
        super().__init__(base)
        self.repeated, self.guard = {key: list(value) for key, value in repeated.items()}, threading.Lock()

    def get(self, key, default=None):
        with self.guard:
            if self.repeated.get(key):
                return self.repeated[key].pop(0)
        return super().get(key, default)


def test_e7_same_question_with_different_answers_keeps_both_sides_and_is_not_complete(tmp_path):
    scripts = Sequenced({}, {Q_SUCC: [(FACT["succ"], "Cobalt B", "ok"), (FACT["owner"], "Cobalt B", "ok")]})
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_SUCC, [])]), delegation("stop")], scripts)
    world.verdict = lambda a, b, claims: "CONFLICT"
    orchestrator, _ = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    answer = result["answer"]
    (conflict,) = answer["conflicts"]
    assert conflict["subject"] is None and conflict["basis"] == "semantic_comparison"  # no task text as identity
    assert [side["branch"] for side in conflict["sides"]] == ["a", "b"]
    assert {side["value"] for side in conflict["sides"]} == {FACT["succ"], FACT["owner"]}
    assert all(side["citations"] for side in conflict["sides"])
    assert len({tuple(side["citations"]) for side in conflict["sides"]}) == 2  # each side keeps its own source
    assert not any(key in conflict for key in ("winner", "chosen"))
    assert answer["published"] is True and result["status"] == "PARTIAL" != "COMPLETE"
    assert len(answer["answers"]) == 2  # neither side is dropped
    assert answer["conflict_check"]["cross_branch_compared"] is True and answer["conflict_check"]["status"] == "CONFLICT"
    assert len(world.comparison_requests) == 1  # one call for the whole run


def test_e7_same_question_with_the_same_answer_is_not_a_conflict_and_costs_no_comparison_call(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_SUCC, [])]), delegation("stop")], TASKS)
    orchestrator, _ = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    assert result["answer"]["conflicts"] == [] and result["status"] == "COMPLETE"
    assert result["answer"]["conflict_check"]["cross_branch_compared"] is True
    assert result["answer"]["conflict_check"]["status"] == "AGREE" and world.comparison_requests == []


def test_e7_different_questions_are_reported_as_not_compared_instead_of_claiming_agreement(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, [])]), delegation("stop")], TASKS)
    orchestrator, _ = build(tmp_path, world)  # the scripted comparator says UNRELATED
    result = orchestrator.start(GOAL)
    check = result["answer"]["conflict_check"]
    # COMPLETE only means "every task has an answer"; the check says nothing was compared
    assert result["status"] == "COMPLETE" and check["cross_branch_compared"] is False
    assert check["uncompared"] == ["a", "b"] and (check["status"], check["verified"]) == ("UNRELATED", True)
    assert len(world.comparison_requests) == 1


def _orchestration_row(name, question, text, value, source):
    item = _item(source, f"{source} quote")
    return _row(name, question, text, [item], None, value)


def _deps(tmp_path, answers, **options):
    """Host dependencies of the comparison step with a scripted comparator model; no Worker, no Runtime."""
    world = World([], {})
    for key, value in options.items():
        setattr(world, key, value)
    limits = run_limits(max_tasks=4, max_rounds=2)
    deps = SimpleNamespace(root=tmp_path / "run", run_id="run", limits=limits, validate=lambda: None,
                           model_factory=world.factory)
    return deps, world


def _compare_and_synthesize(tmp_path, rows, dependencies=None, **options):
    deps, world = _deps(tmp_path, rows, **options)
    plan = bs.plan_comparison(rows, dependencies)
    verdicts, reason = compare_pending(deps, plan)
    pairs = bs.resolve_pairs(plan, verdicts, reason or "COMPARISON_FAILED")
    merged = bs.merge_evidence(rows)
    questions = {row["run"]["branch"]: row["conclusion"]["question"] for row in rows}
    return bs.synthesize_reviewed(rows, {}, 0, merged, questions, comparison=pairs), world, deps


def test_e7_contradiction_between_reworded_questions_is_detected(tmp_path):
    rows = [_orchestration_row("a", "Which supplier replaced Meridian A?", "Cobalt B", "Cobalt B", "s1"),
            _orchestration_row("b", "What did Meridian A get swapped out for?", "Meridian A stayed; Cobalt B was rejected",
                               "Meridian A stayed; Cobalt B was rejected", "s2")]
    result, world, _ = _compare_and_synthesize(tmp_path, rows, verdict=lambda a, b, claims: "CONFLICT")
    assert len(world.comparison_requests) == 1
    assert result["conflicts"] and result["status"] == "PARTIAL"
    (conflict,) = result["conflicts"]
    # the sides carry the answers and citations the Reviewer already passed, nothing the comparison wrote
    assert [(side["branch"], side["value"], side["citations"]) for side in conflict["sides"]] == [
        ("a", "Cobalt B", ["citation-1"]), ("b", "Meridian A stayed; Cobalt B was rejected", ["citation-2"])]
    assert result["conflict_check"]["status"] == "CONFLICT"


@pytest.mark.parametrize("second", ["cobalt b", "Cobalt B.", "Cobalt-B", "the Cobalt B supplier"])
def test_e7_same_meaning_in_other_words_is_not_reported_as_a_conflict(tmp_path, second):
    question = "Which supplier replaced Meridian A?"
    rows = [_orchestration_row("a", question, "Cobalt B", "Cobalt B", "s1"),
            _orchestration_row("b", question, second, second, "s2")]
    result, world, _ = _compare_and_synthesize(tmp_path, rows, verdict=lambda a, b, claims: "AGREE")
    assert result["conflicts"] == [] and result["status"] == "COMPLETE"
    assert result["conflict_check"]["status"] == "AGREE" and len(world.comparison_requests) == 1


def test_e7_the_comparison_request_carries_only_questions_and_answers_not_evidence_or_gold(tmp_path):
    rows = [_orchestration_row("a", "Q one?", "A", "A", "s1"), _orchestration_row("b", "Q two?", "B", "B", "s2")]
    _, world, _ = _compare_and_synthesize(tmp_path, rows)
    (request,) = world.comparison_requests
    context = json.loads(request.user_input.split("context=", 1)[1])
    assert context == dict(claims=[dict(id="a", question="Q one?", answer="A"), dict(id="b", question="Q two?", answer="B")],
                           pairs=[dict(a="a", b="b")])
    assert "s1 quote" not in request.user_input and request.generation_options.require_tool_call is True


@pytest.mark.parametrize("reply", [
    ("raw", dict(pairs=[dict(a="a", b="zzz", verdict="CONFLICT")])),                       # task id does not exist
    ("raw", dict(pairs=[dict(a="a", b="b", verdict="CONFLICT", note="Cobalt B is cited at s9")])),  # a new field
    ("raw", dict(pairs=[dict(a="a", b="b", verdict="WINNER_A")])),                         # verdict outside the set
    ("raw", dict(pairs=[dict(a="a", b="b", verdict="AGREE"), dict(a="b", b="a", verdict="CONFLICT")])),  # twice
    ("raw", dict(pairs=[])),                                                               # pair missing
    ("raw", dict(pairs=[dict(a="a", b="b", verdict="AGREE")], citations=["citation-9"])),  # a new citation
    ("error", None),
])
def test_e7_a_comparison_reply_that_fails_the_host_schema_is_unavailable_and_changes_nothing(tmp_path, reply):
    rows = [_orchestration_row("a", "Q one?", "A", "A", "s1"), _orchestration_row("b", "Q two?", "B", "B", "s2")]
    result, _, _ = _compare_and_synthesize(tmp_path, rows, verdict=lambda a, b, claims: reply)
    check = result["conflict_check"]
    assert (check["status"], check["verified"]) == ("UNAVAILABLE", False) and check["reasons"]
    assert result["conflicts"] == [] and result["status"] == "COMPLETE" and result["published"] is True
    assert [a["citations"] for a in result["answers"]] == [["citation-1"], ["citation-2"]]


def test_e7_the_model_may_name_the_pair_in_either_order(tmp_path):
    rows = [_orchestration_row("a", "Q one?", "A", "A", "s1"), _orchestration_row("b", "Q two?", "B", "B", "s2")]
    deps, world = _deps(tmp_path, rows, verdict=lambda a, b, claims: "CONFLICT")
    world.compare = lambda request: ModelResponse(action=ModelAction.tool(ToolCall("c", "submit_comparison",
        dict(pairs=[dict(a="b", b="a", verdict="CONFLICT")]))), usage=ModelUsage(input_tokens=1, output_tokens=1))
    verdicts, reason = compare_pending(deps, bs.plan_comparison(rows))
    assert reason is None and verdicts == {("a", "b"): ("CONFLICT", "semantic_comparison")}


def test_e7_an_unavailable_comparison_in_a_real_run_keeps_the_answer_status_and_discloses_it(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, [])]), delegation("stop")], TASKS)
    world.verdict = lambda a, b, claims: ("error", None)
    orchestrator, deps = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    check = result["answer"]["conflict_check"]
    assert result["status"] == "COMPLETE"  # the real completeness of the answers
    assert (check["status"], check["verified"]) == ("UNAVAILABLE", False)
    assert result["answer"]["published"] is True and len(result["answer"]["answers"]) == 2
    from linkloom.orchestration.snapshot import render_snapshot
    outcome = render_snapshot("run", GOAL, deps.workspace, result, running=False)["parallel_investigation"]
    assert outcome["status"] == "COMPLETE" and outcome["conflict_check_status"] == "UNAVAILABLE"
    assert outcome["conflict_check_verified"] is False


def test_e7_no_comparison_call_when_there_is_nothing_structure_cannot_decide(tmp_path):
    (tmp_path / "single").mkdir()
    (tmp_path / "chained").mkdir()
    single = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, _ = build(tmp_path / "single", single)
    assert orchestrator.start(GOAL)["answer"]["conflict_check"]["status"] == "NOT_APPLICABLE"
    chained = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, ["a"], "needs a")]),
                     delegation("stop")], TASKS)
    orchestrator, _ = build(tmp_path / "chained", chained)
    result = orchestrator.start(GOAL)
    assert result["answer"]["conflict_check"]["status"] == "UNRELATED"
    assert result["answer"]["conflict_check"]["pairs"][0]["basis"] == "dependent_task"
    assert single.comparison_requests == [] and chained.comparison_requests == []


def test_e7_the_comparison_is_one_recorded_request_under_the_run_provider_limits(tmp_path):
    world = World([delegation("delegate", [("a", Q_SUCC, []), ("b", Q_OWNER, [])]), delegation("stop")], TASKS)
    orchestrator, deps = build(tmp_path, world)
    orchestrator.start(GOAL)
    receipt = json.loads((deps.root / "comparison" / "provider_journal.json").read_text(encoding="utf-8"))
    assert receipt["requests"] == 1 and receipt["attempts"]
    assert receipt["limits"] == asdict(deps.limits.provider)  # no per-role slice
    assert OrchestrationLimits.from_dict(deps.limits.to_dict()) == deps.limits


def test_e7_a_resumed_synthesis_never_sends_an_accepted_comparison_again(tmp_path):
    rows = [_orchestration_row("a", "Q one?", "A", "A", "s1"), _orchestration_row("b", "Q two?", "B", "B", "s2")]
    first, world, deps = _compare_and_synthesize(tmp_path, rows, verdict=lambda a, b, claims: "CONFLICT")
    assert len(world.comparison_requests) == 1 and first["conflict_check"]["status"] == "CONFLICT"
    for _ in range(2):  # the synthesize node replays after a restart
        verdicts, reason = compare_pending(deps, bs.plan_comparison(rows))
        assert reason is None and verdicts[("a", "b")][0] == "CONFLICT"
    assert len(world.comparison_requests) == 1


def test_e7_a_comparison_killed_after_the_request_left_is_unavailable_and_not_resent(tmp_path):
    rows = [_orchestration_row("a", "Q one?", "A", "A", "s1"), _orchestration_row("b", "Q two?", "B", "B", "s2")]
    _, world, deps = _compare_and_synthesize(tmp_path, rows)
    (deps.root / "comparison" / "result.json").unlink()  # the process died before the receipt was written
    verdicts, reason = compare_pending(deps, bs.plan_comparison(rows))
    assert verdicts == {} and reason == "PROVIDER_UNCERTAIN" and len(world.comparison_requests) == 1


# ---------------------------------------------------------------- E8 cancel in the middle of a run

class CancelWorld(World):
    """Cancels the run the moment the second wave's Worker model is requested."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel = None

    def factory(self, text):
        if text == Q_LEADER and self.cancel:
            self.cancel()
        return super().factory(text)


def test_e8_cancel_between_waves_stops_new_provider_calls_and_publishes_nothing(make_env):
    world = CancelWorld([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                                 ("leader", Q_LEADER, ["succ", "owner"])]), delegation("stop")], TASKS)
    env = make_env(world)
    world.cancel = lambda: [run.cancel() for run in list(env.service._orch_runs.values())]
    status, payload = env.ask(GOAL)
    assert status == 202
    run_id = payload["result"]["agent_run_id"]
    wait_idle(env, run_id)
    result = env.run(run_id)
    assert result["status"] == "CANCELLED" and not result["evidence"]
    assert "answer" not in result["parallel_investigation"]
    calls = {worker.description: worker.calls for worker in world.workers}
    assert calls == {Q_SUCC: 3, Q_OWNER: 3}  # the dependent Worker never reached the Provider
    assert len(world.supervisor_requests) == 1
    status, refused = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id))
    assert status == 409 and refused["error"]["code"] == "PARALLEL_RUN_STOPPED"
    assert {worker.description: worker.calls for worker in world.workers} == calls


# ---------------------------------------------------------------- E10 authorization boundary

def test_e10_a_delegation_naming_a_blocked_workspace_is_replaced_by_the_original_goal(tmp_path):
    blocked = [["Orchid Labs", "WORKSPACE_DENIED"]]
    plan = delegation("delegate", [("a", "Who owns Orchid Labs?", []), ("b", Q_OWNER, [])])
    world = World([plan], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, _ = build(tmp_path, world, limits=run_limits(max_tasks=3, max_rounds=0))
    scope = dict(workspace_id="ws", blocked_entities=blocked)
    result = orchestrator.start(GOAL, confirmed_scope=scope, entity_leads=["Meridian A"])
    assert list(result["tasks"]) == ["task-1"]
    assert result["tasks"]["task-1"]["spawn_reason"] == "HOST_FALLBACK_SUPERVISOR_ENTITY_BLOCKED"
    seen = world.supervisor_requests[0].user_input + "".join(r.user_input for w in world.workers for r in w.requests)
    assert "Orchid" not in seen and "blocked_entities" not in seen
    assert result["entity_leads"] == ["Meridian A"]
    assert "Meridian A" not in json.dumps(result["confirmed_scope"])  # a lead never becomes confirmed scope


@pytest.mark.xfail(strict=True, reason="KNOWN_LIMITATION: the Supervisor-side check for a blocked name is a "
                   "normalized substring match, so a hyphenated, truncated or descriptive reference passes it. "
                   "This is an early convenience check, not the authorization boundary. The boundary is the "
                   "Workspace-scoped vault the Worker tools read, the Reviewer source identity check and the "
                   "accepted-rows-only publication; tests/integration/test_orchestration_workspace_auth.py "
                   "shows these variants reach no data of another Workspace.")
@pytest.mark.parametrize("text", ["Who owns Orchid-Labs?", "Who owns Orchid Lab?", "Who owns the Orchid group?"])
def test_e10_known_limitation_a_reworded_blocked_name_is_not_caught_by_the_supervisor_check(text):
    with pytest.raises(DelegationRejected, match="SUPERVISOR_ENTITY_BLOCKED"):
        lg._propose([("a", text, [])], blocked=[["Orchid Labs", "WORKSPACE_DENIED"]])


# ---------------------------------------------------------------- E12 Worker step limit and Final-only turn

class LingeringModel(lg.WorkerModel):
    """Keeps asking for tools on the turn where it could answer, so the reserved Final turn must be used."""

    def complete(self, request):
        self.seen = getattr(self, "seen", [])
        self.seen.append(request)
        if request.final_only:
            self.calls = 2  # the base script answers on its third call
            return super().complete(request)
        if self.calls == 2:  # would be the base script's Final: ask for one more search instead
            self.calls += 1
            self.requests.append(request)
            if request.observation and isinstance(request.observation.value, dict):
                self.refs.append(request.observation.value["evidence_id"])
            call = lg.ToolCall("search-3", "search_notes", dict(query="Cobalt B", limit=5, source_context=self.context))
            return lg.ModelResponse(action=lg.ModelAction.tool(call), usage=lg.ModelUsage(input_tokens=10, output_tokens=10))
        response = super().complete(request)
        if self.calls == 1:
            self.context = response.action.tool_call.arguments["source_context"]
        return response


class EndlessSearchModel(lg.WorkerModel):
    """Never decides to answer: every turn that still offers tools asks for more evidence."""
    QUERIES = ("Cobalt B", "Meridian A", "rollout", "owner", "tracker", "risk", "leader")

    def complete(self, request):
        self.seen = getattr(self, "seen", [])
        self.seen.append(request)
        if request.final_only:
            self.calls = 2  # the base script answers on its third call
            return super().complete(request)
        response = super().complete(request) if self.calls < 2 else None
        if response is not None:
            if self.calls == 1:
                self.context = response.action.tool_call.arguments["source_context"]
            return response
        self.calls += 1
        self.requests.append(request)
        if request.observation and isinstance(request.observation.value, dict):
            self.refs.append(request.observation.value["evidence_id"])
        turn = len(self.seen)
        call = lg.ToolCall(f"search-{turn}", "search_notes",
                           dict(query=self.QUERIES[turn % len(self.QUERIES)], limit=5, source_context=self.context))
        return lg.ModelResponse(action=lg.ModelAction.tool(call), usage=lg.ModelUsage(input_tokens=10, output_tokens=10))


def _requests(worker):
    return worker.seen


def test_scenario_e_long_investigation_keeps_tools_until_the_last_step_then_publishes_partial(tmp_path, monkeypatch):
    """Needs more than four evidence turns. No dollar slice ends the tools early; max_steps reserves the Final."""
    monkeypatch.setattr(lg, "WorkerModel", EndlessSearchModel)
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", GAP)})
    steps = 7
    orchestrator, deps = build(tmp_path, world, limits=run_limits(max_tasks=2, max_rounds=0, max_steps=steps))
    result = orchestrator.start(GOAL)
    (worker,) = world.workers
    turns = _requests(worker)
    assert [request.final_only for request in turns] == [False] * (steps - 1) + [True]
    assert all(request.available_tools for request in turns[:-1]) and turns[-1].available_tools == []
    assert result["status"] == "PARTIAL" and result["dispatches"] == 1  # no retry, no review loop
    receipt = json.loads((deps.root / "tasks" / "task-1" / "attempt-0-1" / "provider_journal.json").read_text(encoding="utf-8"))
    hashes = [row["request_sha256"] for row in receipt["attempts"]]
    assert receipt["requests"] == steps == len(hashes) == len(set(hashes))  # one journal row per request
    assert all(row["outcome"] == "SUCCEEDED" and row["usage"] for row in receipt["attempts"])


def test_e12_the_reserved_final_turn_has_no_tools_and_earlier_turns_do(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "WorkerModel", LingeringModel)
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, deps = build(tmp_path, world, limits=run_limits(max_tasks=4, max_rounds=2, max_steps=4))
    result = orchestrator.start(GOAL)
    (worker,) = world.workers
    turns = _requests(worker)
    assert [request.final_only for request in turns] == [False, False, False, True]
    assert [bool(request.available_tools) for request in turns] == [True, True, True, False]
    assert turns[3].available_tools == []  # the Final turn declares no function at all
    from linkloom.agents.providers.gemini_api import build_gemini_request

    configs = [build_gemini_request(request, configured_model_id="gemini-3.8-flash")["config"] for request in turns]
    assert [config["max_output_tokens"] for config in configs] == [4096, 4096, 4096, 8192]
    assert all("thinking_config" not in config for config in configs)
    assert configs[-1]["tool_config"]["function_calling_config"]["mode"] == "NONE"

    assert result["status"] == "COMPLETE" and result["tasks"]["task-1"]["status"] == ACCEPTED
    receipt = json.loads((deps.root / "tasks" / "task-1" / "attempt-0-1" / "provider_journal.json").read_text(encoding="utf-8"))
    assert receipt["requests"] == 4


def test_e12_supervisor_turn_requires_a_tool_call_and_is_not_a_final_only_turn(tmp_path):
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, deps = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    supervisor = world.supervisor_requests[0]
    assert supervisor.final_only is False and supervisor.available_tools
    assert supervisor.generation_options.require_tool_call is True
    (worker,) = world.workers
    assert worker.calls == 3 and result["status"] == "COMPLETE"  # an on-time Final needs no reserved turn
    receipt = json.loads((deps.root / "tasks" / "task-1" / "attempt-0-1" / "provider_journal.json").read_text(encoding="utf-8"))
    assert receipt["requests"] == 3


def test_scenario_f_worker_path_runs_without_coordinator_reviewer_agent_or_role_slices(tmp_path, monkeypatch):
    """Call chain: Worker -> RuntimeEngine -> SingleAgentModelLoop + one deterministic verification."""
    from linkloom.agents import coordinator, runtime_adapter

    def refuse(*args, **kwargs):
        raise AssertionError("the team_decision Worker path must not use this")

    monkeypatch.setattr(runtime_adapter, "Coordinator", refuse)
    monkeypatch.setattr(coordinator.Coordinator, "run", refuse)
    monkeypatch.setattr(runtime_adapter, "ReviewerAgent", refuse)  # the Synthesizer's aggregate check stays
    world = World([delegation("answer_single")], {GOAL: (FACT["succ"], "Meridian A", "ok")})
    orchestrator, deps = build(tmp_path, world)
    result = orchestrator.start(GOAL)
    assert result["status"] == "COMPLETE" and result["tasks"]["task-1"]["status"] == ACCEPTED
    row = json.loads((deps.root / "tasks" / "task-1" / "attempt-0-1" / "row.json").read_text(encoding="utf-8"))
    assert row["run"]["review_id"].startswith("review_")
    artifact = json.loads(next((deps.root / "tasks" / "task-1" / "attempt-0-1" / "runtime" / "results").rglob(
        "result.json")).read_text(encoding="utf-8"))
    assert artifact["review"]["reviewer_agent_id"] == "evidence_verifier" and artifact["handoffs"] == []
