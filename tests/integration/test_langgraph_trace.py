"""Delivery evidence: one complete dynamic multi-agent trace and one checkpoint/resume run, through the product.

The trace is read from the product trace endpoint (GET /api/product/ask/runs/<id>/trace), so what is asserted is
what an operator sees. Run with `-s` to print the captured output (each block is bracketed by `=== name ===`).
Only the model is an offline script. Runtime, Reviewer, Tool Ledger, synthesis and the LangGraph checkpoint are real.
"""
import json
from time import monotonic, sleep

from tests.integration.test_orchestration_langgraph import (
    FACT, GOAL, Q_LEADER, Q_OWNER, Q_RISK, Q_SUCC, TASKS, World, delegation,
)
from tests.integration.test_orchestration_product import (
    COUNTER, SOURCE_REASON, WHY, assert_cited, checkpoints, make_env, started, waves,  # noqa: F401 (fixture)
)
from tests.integration.test_orchestration_router import LEAD, Q3, ScriptedRouter, ask_service, drive, trace_of
from tests.integration.test_product_application_flow import _client
from linkloom.ui.investigation import ProductInvestigationService
from tests.integration.test_orchestration_product import TOTAL


def emit(name, payload):
    print(f"\n=== {name} ===\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}\n=== end {name} ===")


def brief(trace):
    """The trace without bulky evidence bodies, for a readable printout."""
    return dict(source=trace["source"], status=trace["status"], totals=trace["totals"],
                confirmed_scope=trace["confirmed_scope"], entity_leads=trace["entity_leads"],
                supervisor=[dict(round=item["round"], decision=item.get("decision")) for item in trace["supervisor"]],
                tasks=[dict(id=task["id"], description=task["description"], depends_on=task["depends_on"],
                            status=task["status"], spawn_reason=task.get("spawn_reason"), round=task.get("round"),
                            attempts=[dict(requests_reserved=a.get("requests_reserved")) for a in task["attempts"]])
                       for task in trace["tasks"]],
                events=[{key: value for key, value in event.items() if key != "at"} for event in trace["events"]],
                blackboard=trace["blackboard"], synthesizer=trace["synthesizer"])


def test_one_dynamic_trace_with_parallel_dependency_spawn_shared_evidence_and_synthesis(make_env):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", "gap")  # the owner worker reports a gap -> supplementary task
    world = World([
        delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                ("leader", Q_LEADER, ["succ", "owner"], "needs both upstream results")]),
        delegation("delegate", [("risk", Q_RISK, ["owner"], "owner gap needs the risk tracker")]),
        delegation("stop")], tasks)
    env = make_env(world)
    run_id, result = started(env)
    status, traced = env.run_trace(run_id)
    trace = traced["result"]
    assert status == 200 and trace["source"] == "LIVE"
    # the owner gap is reported by its own worker and stays open, so the run is honestly PARTIAL, not COMPLETE
    assert trace["status"] == "PARTIAL" and result["status"] == "PARTIAL"
    # parallel wave, then the dependent task, then the supplementary task
    assert waves(result) == [["owner", "succ"], ["leader"], ["risk"]]
    by_id = {task["id"]: task for task in trace["tasks"]}
    assert by_id["leader"]["depends_on"] == ["succ", "owner"] and by_id["risk"]["spawn_reason"]
    assert world.started.index(Q_LEADER) > max(world.started.index(Q_SUCC), world.started.index(Q_OWNER))
    assert world.started.index(Q_RISK) > world.started.index(Q_LEADER)
    # shared evidence: verified items from several tasks sit on one blackboard; the answer cites them
    board = trace["blackboard"]
    assert {item["task_id"] for item in board["verified_evidence"]} == {"succ", "owner", "leader", "risk"}
    assert board["approved_decisions"] == []
    # synthesis: one published natural-language answer with citations that resolve to original sources
    assert trace["synthesizer"]["published"] is True
    answer, evidence = assert_cited(result)
    assert len(answer["answers"]) == 4 and checkpoints(env, run_id) > 0
    assert {event["event"] for event in trace["events"]} >= {"supervisor_decision", "wave_dispatched",
                                                             "worker_finished", "synthesized"}
    emit("dynamic-trace", brief(trace))
    emit("dynamic-answer", dict(status=result["status"], text=answer["text"], citations=sorted(evidence)))


def test_checkpoint_resume_run_pays_accepted_tasks_once(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"])]), delegation("stop")], TASKS)
    world.crash_once.add(Q_LEADER)  # the second wave dies like a stopped process
    env = make_env(world)
    status, payload = env.ask(GOAL)
    run_id = payload["result"]["agent_run_id"]
    deadline = monotonic() + 30
    while run_id in env.service._active and monotonic() < deadline:
        sleep(.05)
    interrupted = env.run(run_id)
    rows_before = checkpoints(env, run_id)
    before = {w.description: w.calls for w in world.workers}
    assert interrupted["parallel_investigation"]["continuable"] == ["leader"]
    emit("resume-1-interrupted", dict(status=interrupted["status"], run=interrupted["parallel_investigation"]["status"],
         branch_states=interrupted["parallel_investigation"]["branch_states"], checkpoint_rows=rows_before,
         worker_calls=before, supervisor_calls=len(world.supervisor_requests)))
    env.service.close(timeout=25)  # a new process: no live object, resume from the LangGraph checkpoint only
    env.use(ProductInvestigationService(env.app, model_factory=world.factory, external_provider=False,
                                        parallel_limits=TOTAL), restart=True)
    cold = env.run(run_id)
    assert cold["parallel_investigation"]["continuable"] == ["leader"]
    status, resumed = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id))
    assert status == 202, resumed
    final = env.finish(run_id)
    counts = {}
    for worker in world.workers:
        counts[worker.description] = counts.get(worker.description, 0) + worker.calls
    assert counts == {Q_SUCC: 3, Q_OWNER: 3, Q_LEADER: 3} and len(world.supervisor_requests) == 1
    assert final["status"] == "COMPLETED"
    # E2E #12: the trace read path projects the run's own Provider journals (#135). Resumed branches
    # are REUSED, never re-billed; reservations, reported usage, estimate and bill stay separate.
    status, traced = env.run_trace(run_id)
    assert status == 200
    telemetry = traced["result"]["telemetry"]
    assert telemetry["schema_version"] == "ask-telemetry/v2" and telemetry["run_id"] == run_id
    calls = telemetry["provider_calls"]
    paid = sum(counts.values()) + len(world.supervisor_requests) + 1  # + the synthesis comparison
    assert calls["journal_coverage"] == "COMPLETE" and calls["attempted"] == calls["completed"] == paid
    assert calls["uncertain"] == calls["failed"] == 0 and calls["pending"] == 0
    assert traced["result"]["totals"]["requests"] == paid
    recovery = {node["id"]: node["recovery_state"] for node in telemetry["nodes"] if node.get("event") == "checkpoint_state"}
    assert recovery == {"succ": "REUSED", "owner": "REUSED", "leader": "REUSED"}
    usage, cost = telemetry["usage"], telemetry["cost"]
    assert usage["input_tokens"]["value"] and usage["input_tokens"]["value"] == traced["result"]["totals"]["input_tokens"]
    assert cost["estimated_usd"]["source"] == "estimated" and cost["estimated_usd"]["value"] > 0
    assert cost["provider_reported_usd"]["value"] is None
    assert usage["cached_input_tokens"]["value"] is None and telemetry["total_duration_ms"] is None
    emit("resume-2-completed", dict(status=final["status"], run=final["parallel_investigation"]["status"],
         waves=waves(final), worker_calls_total=counts, supervisor_calls=len(world.supervisor_requests),
         checkpoint_rows=checkpoints(env, run_id), answer=final["parallel_investigation"]["answer"]["text"]))


def test_original_q3_and_counterexample_end_to_end_report(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    env = make_env(world)
    started_run, final = drive(env, ask_service(env, ScriptedRouter(), "llm_first"), Q3)
    state = trace_of(env, final["agent_run_id"])
    assert final["status"] == "COMPLETED" and state["entity_leads"] == [LEAD]
    # The Router's journal is not linked to this run: totals are never presented as complete.
    telemetry = state["telemetry"]
    assert [node["kind"] for node in telemetry["nodes"]][0] == "router"
    assert telemetry["provider_calls"]["journal_coverage"] == "PARTIAL"
    assert telemetry["provider_calls"]["attempted"] is None and telemetry["provider_calls"]["observed_attempted"] > 0
    assert telemetry["cost"]["estimated_usd"]["value"] is None and telemetry["cost"]["estimated_usd"]["known_value"] > 0
    answer, _ = assert_cited(final)
    emit("q3", dict(query=Q3, router=started_run["router"], status=final["status"], confirmed_scope=state["confirmed_scope"],
                    entity_leads=state["entity_leads"], answer=answer["text"]))
    counter_world = World([delegation("answer_single")], {COUNTER: (SOURCE_REASON, LEAD, "ok")})
    counter_env = make_env(counter_world)
    started_run, final = drive(counter_env, ask_service(counter_env, ScriptedRouter(bind=True), "llm_first"), COUNTER)
    state = trace_of(counter_env, final["agent_run_id"])
    answer, evidence = assert_cited(final)
    assert final["status"] == "COMPLETED" and "Meridian A replaced Launch" not in json.dumps(answer)
    emit("counterexample", dict(query=COUNTER, router=started_run["router"], status=final["status"],
                                confirmed_scope=state["confirmed_scope"], entity_leads=state["entity_leads"],
                                answer=answer["text"], quotes=[item["quote"] for item in evidence.values()]))
