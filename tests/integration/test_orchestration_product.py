"""Product Ask / inspect / continue / cancel on the LangGraph orchestration.

Real HTTP server, ProductApplication, RuntimeEngine, Reviewer gate and synthesis. Only the model is an offline
script (no Provider call). Every run is asserted through the product endpoints, and its orchestration state is
read back from the LangGraph SQLite checkpoint.
"""
import hashlib
import json
import sqlite3
from threading import Barrier
from time import monotonic, sleep

import pytest

from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_provider import TaskProviderLimits
from linkloom.ui.unified_ask import UnifiedAskService
from tests.integration.test_orchestration_langgraph import (
    FACT, GOAL, Q_LEADER, Q_OWNER, Q_RISK, Q_SUCC, TASKS, World, delegation,
)
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)

TOTAL = TaskProviderLimits(80, 20_000_000, 400_000, 30.0, 120, 20, .01)
SCOPE = dict(subject="Launch", relation="uses supplier", as_of="2025-02-20")
SOURCE_REASON = ("Launch replaced Meridian A with Cobalt B because Meridian A could not meet "
                 "the agreed delivery schedule.")
WHY = "Why did Launch switch suppliers?"
COUNTER = "Why did Meridian A replace Launch with Cobalt B?"
SOURCES = (FACT["succ"], FACT["owner"], FACT["leader"], FACT["risk"], SOURCE_REASON)


class Env:
    def __init__(self, tmp_path, world, limits=TOTAL, others=(), **options):
        self.tmp_path, self.world = tmp_path, world
        self.base, self.server, self.worker, self.app = _start_product_server(tmp_path, [
            _claim("Cobalt B"), _claim("Cobalt B", claim_type="PROPOSAL"),
            _claim("Cobalt B", claim_type="PROPOSAL"), _claim("Cobalt B", claim_type="PROPOSAL")])
        for name in others:  # earlier workspaces; the last one created is the current one
            _client(self.base, "/api/workspaces", method="POST", data={"name": name})
        _, workspace = _client(self.base, "/api/workspaces", method="POST", data={"name": "Meetings"})
        self.workspace_id = workspace["workspace"]["workspace_id"]
        _, scope = _client(self.base, "/api/sources/import", method="POST", data=dict(
            name="approval.md",
            content="[2025-02-10T10:00:00Z] Alice: We decided to use Cobalt B as the supplier for Launch."))
        _wait_source(self.base, scope["source"]["source_id"])
        _, inbox = _client(self.base, "/api/review/candidates")
        status, approval = _client(self.base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
            method="POST", data=dict(reviewer_id="local", reason="Synthetic reviewed scope"))
        assert status == 200, approval
        self.source_ids = {}
        for index, text in enumerate(SOURCES, 1):
            _, imported = _client(self.base, "/api/sources/import", method="POST", data=dict(
                name=f"meeting-{index}.md", content=f"[2025-02-1{index}T10:00:00Z] Alice: {text}."))
            self.source_ids[text] = imported["source"]["source_id"]
            _wait_source(self.base, imported["source"]["source_id"])
        self.service = self.use(ProductInvestigationService(
            self.app, model_factory=world.factory, external_provider=False, parallel_limits=limits, **options))

    def use(self, service, *, restart=False):
        self.service = service
        self.server.investigation_service = service
        if restart:  # a new process: neither the service nor the Ask service remembers any run
            self.server.unified_ask = UnifiedAskService(self.app, lambda: self.server.investigation_service,
                                                        graph_service=False)
        return service

    def ask(self, query, **extra):
        return _client(self.base, "/api/product/ask", method="POST",
                       data=dict(query=query, workspace_id=self.workspace_id, **SCOPE, **extra))

    def run(self, run_id):
        status, payload = _client(self.base, f"/api/product/ask/runs/{run_id}?workspace_id={self.workspace_id}")
        assert status in (200, 500), payload
        return payload["result"]

    def run_trace(self, run_id):
        return _client(self.base, f"/api/product/ask/runs/{run_id}/trace?workspace_id={self.workspace_id}")

    def finish(self, run_id, timeout=180):
        # The run takes about 1s locally. Shared Windows runners stall for minutes at a time, so the wait is longer
        # than the run's own max_seconds (120): a genuine hang ends as a failed run, not as a poll timeout.
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            result = self.run(run_id)
            if result["status"] != "INVESTIGATING":
                return result
            sleep(.05)
        pytest.fail(f"orchestrated run did not finish: {json.dumps(result.get('parallel_investigation', {}).get('trace', [])[-5:], default=str)}")

    def root(self, run_id):
        return self.tmp_path / "work" / "investigations" / run_id.removeprefix("ui-parallel-")

    def stop(self):
        self.service.close(timeout=25)
        _stop_product_server(self.server, self.worker, self.app)


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    made = []

    def make(world, **kwargs):
        made.append(Env(tmp_path, world, **kwargs))
        return made[-1]
    yield make
    for env in made:
        env.stop()


def checkpoints(env, run_id):
    """Rows the LangGraph SqliteSaver wrote for this run: the orchestration state is stored there."""
    with sqlite3.connect(env.root(run_id) / "langgraph.sqlite") as connection:
        return connection.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (run_id,)).fetchone()[0]


def waves(result):
    return [event["tasks"] for event in result["parallel_investigation"]["trace"] if event["event"] == "wave_dispatched"]


def started(env, query=GOAL, **extra):
    status, payload = env.ask(query, **extra)
    assert status == 202, payload
    result = payload["result"]
    assert result["parallel_investigation"]["status"] == "RUNNING" and result["status"] == "INVESTIGATING"
    return result["agent_run_id"], env.finish(result["agent_run_id"])


def assert_cited(result):
    """Natural-language answer, every line with citations that resolve to original, version-pinned sources."""
    answer = result["parallel_investigation"]["answer"]
    known = {item["citation_id"]: item for item in result["evidence"]}
    assert answer["published"] is True and answer["text"] and answer["answers"]
    for row in answer["answers"]:
        assert row["citations"] and set(row["citations"]) <= set(known)
    for item in known.values():
        assert item["verified"] is True and item["quote"] and item["source_name"].endswith(".md")
        assert item["source_id"].startswith("src_") and item["source_version"]
        assert len(item["quote_sha256"]) == 64 and len(item["content_sha256"]) == 64
    return answer, known


def assert_pinned(env, result):
    """Every citation still matches the current source version and the hash of its own quote."""
    for item in result["evidence"]:
        assert hashlib.sha256(item["quote"].encode("utf-8")).hexdigest() == item["quote_sha256"]
        _, source = _client(env.base, f"/api/sources/{item['source_id']}")
        assert str(source["source"]["source_version"]) == item["source_version"]
        assert item["quote"] in source["source"]["content"]


def assert_within_budget(env, run_id):
    _, traced = env.run_trace(run_id)
    totals = traced["result"]["totals"]
    for key, limit in (("requests_reserved", TOTAL.max_requests), ("input_tokens_reserved", TOTAL.max_input_tokens),
                       ("output_tokens_reserved", TOTAL.max_output_tokens), ("cost_reserved_usd", TOTAL.max_cost_usd)):
        assert 0 < totals[key] <= limit, (key, totals)


def test_single_question_needs_no_extra_worker(make_env):
    world = World([delegation("answer_single")], {WHY: (SOURCE_REASON, "Meridian A", "ok")})
    env = make_env(world)
    run_id, result = started(env, WHY)
    assert run_id.startswith("ui-parallel-") and checkpoints(env, run_id) > 0
    parallel = result["parallel_investigation"]
    assert [task["id"] for task in parallel["tasks"]] == ["task-1"] and parallel["status"] == "COMPLETE"
    assert len(world.workers) == 1 and len(world.supervisor_requests) == 1
    assert result["status"] == "COMPLETED"
    answer, evidence = assert_cited(result)
    assert SOURCE_REASON in json.dumps(answer) and {item["source_id"] for item in evidence.values()} == {
        env.source_ids[SOURCE_REASON]}


def test_independent_tasks_run_in_parallel_and_share_verified_evidence(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")],
                  TASKS, barrier_for=(Q_SUCC, Q_OWNER), barrier=Barrier(2))
    env = make_env(world)
    _, result = started(env)
    assert waves(result) == [["owner", "succ"]]  # the Barrier(2) passed: the Workers ran at the same time
    parallel = result["parallel_investigation"]
    assert parallel["status"] == "COMPLETE" and result["status"] == "COMPLETED"
    board = parallel["blackboard"]
    assert {item["task_id"] for item in board["verified_evidence"]} == {"succ", "owner"}
    assert {item["verification"] for item in board["verified_evidence"]} == {"reviewer_passed"}
    assert board["inferences"] == 2 and board["approved_decisions"] == []  # nothing enters Decision Memory
    assert board["raw_retrievals"] >= 2
    answer, evidence = assert_cited(result)
    assert len(answer["answers"]) == 2 and len(evidence) == 2
    assert_pinned(env, result)
    assert_within_budget(env, result["agent_run_id"])


def test_dependent_task_receives_only_the_verified_upstream_results(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"], "needs both")]),
                   delegation("stop")], TASKS)
    env = make_env(world)
    _, result = started(env)
    assert waves(result) == [["owner", "succ"], ["leader"]]
    assert world.started.index(Q_LEADER) > max(world.started.index(Q_SUCC), world.started.index(Q_OWNER))
    leader = next(worker for worker in world.workers if worker.description == Q_LEADER)
    query = json.loads(leader.requests[0].user_input.rsplit("team_decision_request=", 1)[1])["query"]
    upstream = json.loads(query.split("investigation_context=", 1)[1])["verified_upstream_results"]
    assert {item["task_id"] for item in upstream} == {"succ", "owner"}
    assert FACT["succ"] in query and FACT["owner"] in query
    tasks = {task["id"]: task for task in result["parallel_investigation"]["tasks"]}
    assert tasks["leader"]["depends_on"] == ["succ", "owner"] and tasks["leader"]["status"] == "accepted"
    assert_cited(result)


def test_worker_gap_spawns_a_supplementary_task(make_env):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", "gap")
    world = World([delegation("delegate", [("owner", Q_OWNER, [])]),
                   delegation("delegate", [("risk", Q_RISK, ["owner"], "owner gap needs the risk tracker")]),
                   delegation("stop")], tasks)
    env = make_env(world)
    _, result = started(env)
    assert "Who verified the rollout owner?" in world.supervisor_requests[1].user_input
    assert waves(result) == [["owner"], ["risk"]]
    spawned = {task["id"]: task for task in result["parallel_investigation"]["tasks"]}["risk"]
    assert spawned["spawn_reason"] == "owner gap needs the risk tracker" and spawned["round"] == 1
    assert result["parallel_investigation"]["rounds"] == 3
    assert_cited(result)


@pytest.mark.parametrize("failing,expected,status", [("error", "PARTIAL", "PARTIAL"), ("abstain", "PARTIAL", "PARTIAL"),
                                                     ("uncertain", "PARTIAL", "PARTIAL")])
def test_failure_or_abstention_keeps_valid_results_and_reports_partial(make_env, failing, expected, status):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", failing)
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], tasks)
    env = make_env(world)
    _, result = started(env)
    parallel = result["parallel_investigation"]
    assert parallel["status"] == expected and result["status"] == status
    assert result["uncertainty"]["unknown_fields"]
    assert {task["id"]: task["status"] for task in parallel["tasks"]}["succ"] == "accepted"
    answer, evidence = assert_cited(result)
    assert [row["branch"] for row in answer["answers"]] == ["succ"] and answer["unknowns"]
    assert FACT["succ"] in json.dumps(answer) and len(evidence) == 1
    if failing in ("error", "uncertain"):
        assert parallel["gaps"] == {"owner": "FAILED" if failing == "error" else "UNCERTAIN"}
    if failing == "uncertain":  # an outcome the Provider may have billed is never sent again, even on continue
        assert next(w for w in world.workers if w.description == Q_OWNER).calls == 1
        status, again = _client(env.base, f"/api/product/ask/runs/{result['agent_run_id']}/continue", method="POST",
                                data=dict(workspace_id=env.workspace_id))
        assert status == 409 and next(w for w in world.workers if w.description == Q_OWNER).calls == 1


def test_every_task_failing_is_unknown_without_evidence(make_env):
    tasks = {Q_SUCC: (FACT["succ"], "Meridian A", "error"), Q_OWNER: (FACT["owner"], "Cobalt B", "error")}
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], tasks)
    env = make_env(world)
    _, result = started(env)
    assert result["parallel_investigation"]["status"] == "UNKNOWN" and result["status"] == "UNKNOWN"
    assert not result["evidence"] and result["parallel_investigation"]["gaps"] == {"owner": "FAILED", "succ": "FAILED"}


def test_inspect_trace_and_cold_read_show_dag_evidence_and_answer(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"])]), delegation("stop")], TASKS)
    env = make_env(world)
    run_id, live = started(env)
    status, traced = _client(env.base, f"/api/product/ask/runs/{run_id}/trace?workspace_id={env.workspace_id}")
    trace = traced["result"]
    assert status == 200 and trace["source"] == "LIVE" and trace["status"] == "COMPLETE"
    assert [task["id"] for task in trace["tasks"]] == ["succ", "owner", "leader"]
    assert all(task["attempts"] and task["attempts"][0]["requests_reserved"] >= 3 for task in trace["tasks"])
    assert [item["round"] for item in trace["supervisor"]] == ["round-0"] and trace["totals"]["requests_reserved"] > 9
    assert {event["event"] for event in trace["events"]} >= {"supervisor_decision", "wave_dispatched",
                                                             "worker_finished", "synthesized"}
    assert trace["synthesizer"]["published"] is True and "blocked_entities" not in json.dumps(trace)
    # a restarted server has no live object: the same run is read from the LangGraph checkpoint alone
    before = len(world.workers)
    env.service.close(timeout=25)
    fresh = env.use(ProductInvestigationService(env.app, model_factory=world.factory, external_provider=False,
                                                parallel_limits=TOTAL), restart=True)
    cold = env.run(run_id)
    assert cold["parallel_investigation"]["answer"] == live["parallel_investigation"]["answer"]
    assert cold["evidence"] == live["evidence"] and cold["status"] == "COMPLETED"
    status, saved = _client(env.base, f"/api/product/ask/runs/{run_id}/trace?workspace_id={env.workspace_id}")
    assert status == 200 and saved["result"]["source"] == "SAVED" and len(world.workers) == before
    assert fresh is env.service and fresh.inspect(run_id) is None  # no live object: only the saved read serves it


def test_resume_after_interruption_reuses_accepted_tasks_and_pays_nothing_twice(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, []),
                                           ("leader", Q_LEADER, ["succ", "owner"])]), delegation("stop")], TASKS)
    world.crash_once.add(Q_LEADER)  # the second wave dies like a stopped process, after the first wave was paid
    env = make_env(world)
    status, payload = env.ask(GOAL)
    run_id = payload["result"]["agent_run_id"]
    deadline = monotonic() + 30
    while run_id in env.service._active and monotonic() < deadline:
        sleep(.05)
    interrupted = env.run(run_id)
    parallel = interrupted["parallel_investigation"]
    assert status == 202 and parallel["status"] == "RUNNING" and interrupted["status"] == "INVESTIGATING"
    states = {item["branch"]: item["state"] for item in parallel["branch_states"]}
    assert states == {"succ": "REUSED", "owner": "REUSED", "leader": "CONTINUABLE"} and parallel["continuable"] == ["leader"]
    calls = {worker.description: worker.calls for worker in world.workers}
    assert calls == {Q_SUCC: 3, Q_OWNER: 3} and len(world.supervisor_requests) == 1
    # a new process: nothing in memory; the run resumes from the LangGraph checkpoint
    env.service.close(timeout=25)
    env.use(ProductInvestigationService(env.app, model_factory=world.factory, external_provider=False,
                                        parallel_limits=TOTAL), restart=True)
    cold = env.run(run_id)
    assert cold["parallel_investigation"]["continuable"] == ["leader"]
    status, resumed = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id))
    assert status == 202, resumed
    final = env.finish(run_id)
    assert final["parallel_investigation"]["status"] == "COMPLETE" and final["status"] == "COMPLETED"
    counts = {}
    for worker in world.workers:
        counts[worker.description] = counts.get(worker.description, 0) + worker.calls
    assert counts == {Q_SUCC: 3, Q_OWNER: 3, Q_LEADER: 3}, counts  # accepted tasks were not paid again
    assert len(world.supervisor_requests) == 1  # the paid Supervisor proposal is a receipt, not a new call
    assert waves(final) == [["owner", "succ"], ["leader"]]
    assert_cited(final)
    status, again = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                            data=dict(workspace_id=env.workspace_id))
    assert status == 409 and again["error"]["code"] == "NO_CONTINUABLE_BRANCH"


def test_cancel_and_source_change_publish_nothing(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    env = make_env(world)
    _, finished = started(env)
    run_id = finished["agent_run_id"]
    assert finished["parallel_investigation"]["status"] == "COMPLETE"
    assert env.service.cancel_parallel(run_id) is True
    cancelled = env.run(run_id)
    assert cancelled["status"] == "CANCELLED" and not cancelled["evidence"]
    assert "answer" not in cancelled["parallel_investigation"]
    assert env.service.cancel_parallel("ui-parallel-000000000000") is False

    other = make_env_world_run(env)
    _, imported = _client(env.base, "/api/sources/import", method="POST", data=dict(
        name="late.md", content="[2025-03-01T10:00:00Z] Alice: A source added after the run."))
    _wait_source(env.base, imported["source"]["source_id"])
    changed = env.service.inspect(other)  # the authorized source identity changed after publication
    assert changed["parallel_investigation"]["status"] == "ABORTED" and not changed["evidence"]
    assert "answer" not in changed["parallel_investigation"]
    with env.app._store_lock:
        env.app.repository.create_workspace("Different workspace")
    status, moved = _client(env.base, f"/api/product/ask/runs/{other}?workspace_id={env.workspace_id}")
    assert status == 409 and moved["error"]["code"] == "WORKSPACE_MISMATCH"


def make_env_world_run(env):
    env.world.decisions.extend([delegation("answer_single")])
    env.world.tasks[GOAL] = (FACT["succ"], "Meridian A", "ok")
    status, payload = env.ask(GOAL)
    assert status == 202
    run_id = payload["result"]["agent_run_id"]
    env.finish(run_id)
    return run_id


def test_hard_provider_failure_aborts_and_publishes_nothing(make_env):
    tasks = dict(TASKS)
    tasks[Q_OWNER] = (FACT["owner"], "Cobalt B", "auth")
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], tasks)
    env = make_env(world)
    _, result = started(env)
    assert result["parallel_investigation"]["status"] == "ABORTED" and result["status"] == "FAILED"
    assert not result["evidence"] and "answer" not in result["parallel_investigation"]


def test_pending_source_is_never_a_default_for_flag_off_requests(make_env):
    """Flag off: the same Ask never reaches the orchestrator (one path, chosen by Host configuration)."""
    world = World([], {})
    env = make_env(world, limits=None)
    env.service.parallel_limits = None
    status, payload = env.ask(GOAL)
    assert "parallel_investigation" not in payload["result"] and not world.supervisor_requests
    assert not list((env.tmp_path / "work" / "investigations").glob("*/langgraph.sqlite"))


def wait_idle(env, run_id, timeout=30):
    deadline = monotonic() + timeout
    while run_id in env.service._active and monotonic() < deadline:
        sleep(.05)
    assert run_id not in env.service._active


def test_large_plan_waits_for_a_reviewer_and_runs_only_after_approval(make_env):
    plan = [("succ", Q_SUCC, []), ("owner", Q_OWNER, []), ("leader", Q_LEADER, ["succ", "owner"])]
    world = World([delegation("delegate", plan), delegation("stop")], TASKS)
    env = make_env(world, confirm_over_tasks=2)
    status, payload = env.ask(GOAL)
    run_id = payload["result"]["agent_run_id"]
    wait_idle(env, run_id)
    waiting = env.run(run_id)["parallel_investigation"]
    assert status == 202 and waiting["status"] == "RUNNING" and not world.workers
    assert sorted(item["id"] for item in waiting["awaiting_confirmation"][0]["tasks"]) == ["leader", "owner", "succ"]
    status, refused = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id))
    assert status == 409 and refused["error"]["code"] == "CONFIRMATION_REQUIRED" and not world.workers
    approval = dict(approved=True, reviewer_id="local", reason="Reviewed the three tasks")
    status, resumed = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id, approval=approval))
    assert status == 202, resumed
    final = env.finish(run_id)
    assert final["parallel_investigation"]["status"] == "COMPLETE"
    decisions = final["parallel_investigation"]["blackboard"]["approved_decisions"]
    assert [item["kind"] for item in decisions] == ["delegation_plan"] and decisions[0]["reviewer_id"] == "local"
    assert len(world.supervisor_requests) == 1  # the plan proposal is a receipt: approval did not pay again


def test_rejected_plan_runs_nothing(make_env):
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])])], TASKS)
    env = make_env(world, confirm_over_tasks=1)
    status, payload = env.ask(GOAL)
    run_id = payload["result"]["agent_run_id"]
    wait_idle(env, run_id)
    status, resumed = _client(env.base, f"/api/product/ask/runs/{run_id}/continue", method="POST",
                              data=dict(workspace_id=env.workspace_id, approval=dict(approved=False)))
    assert status == 202, resumed
    final = env.finish(run_id)
    assert final["parallel_investigation"]["status"] == "UNKNOWN" and not world.workers and not final["evidence"]
    assert {task["failure"] for task in final["parallel_investigation"]["tasks"]} == {"PLAN_REJECTED"}


def test_a_result_the_reviewer_does_not_accept_is_a_gap_and_other_results_stay(make_env, monkeypatch):
    from linkloom.orchestration import worker
    real = worker.project_reviewer

    def reject_owner(artifact):
        review = real(artifact)
        value = artifact["result"]["team_decision"]["decision"]["value"]
        return dict(review, decision="evidence_insufficient") if value == FACT["owner"] else review

    monkeypatch.setattr(worker, "project_reviewer", reject_owner)
    world = World([delegation("delegate", [("succ", Q_SUCC, []), ("owner", Q_OWNER, [])]), delegation("stop")], TASKS)
    env = make_env(world)
    _, result = started(env)
    assert result["parallel_investigation"]["status"] == "PARTIAL" and result["status"] == "PARTIAL"
    tasks = {task["id"]: task for task in result["parallel_investigation"]["tasks"]}
    assert tasks["owner"]["status"] == "failed" and tasks["owner"]["failure"] == "WORKER_REVIEW_FAILED"
    assert tasks["succ"]["status"] == "accepted" and FACT["owner"] not in json.dumps(result["evidence"])
    assert_cited(result)


def test_product_run_carries_the_user_cost_budget_as_the_whole_run_fuse(make_env):
    world = World([delegation("answer_single")], {WHY: (SOURCE_REASON, "Meridian A", "ok")})
    env = make_env(world)
    run_id, _ = started(env, WHY)
    from linkloom.orchestration.runs import load_record
    record = load_record(env.root(run_id))
    assert record["limits"]["max_run_cost_usd"] == TOTAL.max_cost_usd


def test_unmatched_question_is_budgeted_like_any_orchestrated_run(make_env):
    """NO_MATCH falls through to a source investigation: same Provider limits, same totals, no extra pool."""
    question = "Who attended the budget review?"
    world = World([delegation("answer_single")], {question: (SOURCE_REASON, "Meridian A", "ok")})
    env = make_env(world)
    status, payload = _client(env.base, "/api/product/ask", method="POST",
                              data=dict(query=question, workspace_id=env.workspace_id))
    assert status == 202, payload
    run_id = payload["result"]["agent_run_id"]
    env.finish(run_id)
    assert_within_budget(env, run_id)
