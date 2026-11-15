"""Read-only call chain of a parallel run. Mock providers and synthetic data only; no timing thresholds."""
import json
from datetime import datetime, timedelta, UTC
from threading import Barrier, Event

from linkloom.observability.events import TraceEvent
from linkloom.observability.reader import TraceManifest, TraceReader
from linkloom.ui.branch_trace import build_branch_trace, compute_overlap
from tests.integration.test_parallel_investigation import QUESTION, MeetingProvider
from tests.integration.test_parallel_resume import LIMITS, OPEN, Counter, env, tree  # noqa: F401  (env is a fixture)

T0 = datetime(2030, 1, 1, tzinfo=UTC)
SECRET_QUESTION = "SECRET-SUBQUESTION-ALPHA"
SECRET_SOURCE = "src_" + "ab" * 16


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf8")


def attempt(sequence, outcome="SUCCEEDED", usage=(100, 20), **extra):
    row = dict(sequence=sequence, stage="runtime", request_sha256="a" * 64, input_reserved=1000, output_reserved=200,
               cost_reserved_usd=0.5, outcome=outcome)
    if usage is not None:
        row.update(usage=dict(input_tokens=usage[0], output_tokens=usage[1]), response_sha256="b" * 64)
    row.update(extra)
    return row


def budget(start, seconds, attempts, failure=None):
    return dict(limits=dict(max_requests=7, input_usd_per_million=1.0, output_usd_per_million=5.0),
                started_at=start.isoformat(), elapsed_seconds=seconds, failure=failure, attempts=attempts,
                requests_reserved=len(attempts), actual_cost_usd=None)


def synthetic(root, run_id, spans, *, statuses=None, attempts=None, planner=None, pool_run_id=None):
    """spans: name -> (start offset seconds, duration seconds) or None (no budget record)."""
    names = list(spans)
    write(root / "parallel.json", dict(schema="parallel-run/v1", query="q", identity="i", workspace_id="w",
          branches=[dict(name=name, question=f"{SECRET_QUESTION}-{name}", plan=None) for name in names],
          planner=planner))
    write(root / "pool.json", dict(run_id=pool_run_id or run_id, stop=None, total={}, branches={
        name: dict(status=(statuses or {}).get(name, "VERIFIED"), slice=None, held=None) for name in names}))
    for name, span in spans.items():
        if span is not None:
            write(root / "branches" / name / "budget.json", budget(T0 + timedelta(seconds=span[0]), span[1],
                  (attempts or {}).get(name, [attempt(1)])))


def snapshot(status="COMPLETE", **extra):
    return dict(parallel_investigation=dict(status=status, branches=[], gaps={}, **extra))


def build(root, run_id="ui-parallel-000000000001", snap=None):
    return build_branch_trace(root, run_id, snap or snapshot(), 12)


def na_is_explained(node):
    """Every key named in an na mapping is None, and every measurement that is None has a reason."""
    if isinstance(node, dict):
        for key, reason in (node.get("na") or {}).items():
            assert isinstance(reason, str) and reason
            assert node.get(key) is None or key not in node, (key, node)
        for key in ("interval", "estimated_cost_usd", "reserved_cost_usd", "requests", "wall_seconds", "overlap_seconds"):
            if key in node and node[key] is None:
                assert key in (node.get("na") or {}), (key, node)
        for value in node.values():
            na_is_explained(value)
    elif isinstance(node, list):
        for value in node:
            na_is_explained(value)


def test_serial_branches_have_zero_overlap_and_overlapping_ones_a_positive_one(tmp_path):
    synthetic(tmp_path / "serial", "ui-parallel-000000000001", dict(a=(0, 2), b=(2, 3)))
    serial = build(tmp_path / "serial")["totals"]["overlap"]
    assert serial["overlap_seconds"] == 0 and serial["pairs"] == [] and serial["max_concurrency"] == 1
    synthetic(tmp_path / "par", "ui-parallel-000000000001", dict(a=(0, 4), b=(1, 4)))
    trace = build(tmp_path / "par")
    overlap = trace["totals"]["overlap"]
    assert overlap["overlap_seconds"] == 3 and overlap["max_concurrency"] == 2
    assert overlap["pairs"] == [dict(branches=["a", "b"], seconds=3)]
    assert trace["totals"]["wall_seconds"] == 5
    assert trace["branches"][1]["interval"]["offset_seconds"] == 1


def test_overlap_ignores_intervals_of_different_pools():
    first = dict(name="a", pool="original", start=T0, end=T0 + timedelta(seconds=5))
    second = dict(name="b", pool="resume", start=T0, end=T0 + timedelta(seconds=5))
    assert compute_overlap([first, second])["overlap_seconds"] is None


def test_nested_trace_events_are_never_counted_as_parallel(tmp_path):
    root = tmp_path / "run"
    synthetic(root, "ui-parallel-000000000001", dict(only=(0, 10)))
    run = "run_nested"
    events = [dict(event_id=f"e{i}", run_id=run, thread_id="t", seq=i, event_type=kind, actor="agent", status="ok",
                   parent_event_id=None if i == 1 else "e1", started_at=(T0 + timedelta(seconds=i)).isoformat(),
                   finished_at=(T0 + timedelta(seconds=i + 5)).isoformat(), duration_ms=5000)
              for i, kind in enumerate(["tool.called", "tool.completed", "tool.called"], 1)]
    traces = root / "branches" / "only" / "traces"
    (traces / run).mkdir(parents=True)
    (traces / run / "events.jsonl").write_text("\n".join(json.dumps(TraceEvent.from_dict(e).to_dict()) for e in events) + "\n",
                                               encoding="utf8")
    TraceReader(traces).write_manifest(TraceManifest(1, run, 3, 1, 3, "events.jsonl", "trace_summary.md", "c" * 64,
                                                     "trace-redaction-v1", True))
    branch = build(root)["branches"][0]
    assert [event["seq"] for event in branch["events"]] == [1, 2, 3] and branch["events_summary"]["timed"] is True
    overlap = build(root)["totals"]["overlap"]
    assert overlap["overlap_seconds"] is None and overlap["pairs"] == []
    assert overlap["na"]["overlap_seconds"] == "FEWER_THAN_TWO_COMPLETE_SIBLING_INTERVALS"


def test_incomplete_intervals_are_excluded_with_a_reason(tmp_path):
    synthetic(tmp_path, "ui-parallel-000000000001", dict(a=(0, 4), b=(1, 4), c=(0, 9), d=None),
              statuses=dict(c="RUNNING", d="NO_REQUESTS"))
    trace = build(tmp_path)
    assert trace["totals"]["overlap"]["overlap_seconds"] == 3
    assert trace["totals"]["overlap"]["pairs"] == [dict(branches=["a", "b"], seconds=3)]
    assert {row["branch"]: row["reason"] for row in trace["totals"]["overlap"]["excluded"]} == dict(
        c="BRANCH_NOT_SETTLED", d="BUDGET_RECORD_UNAVAILABLE")
    c = trace["branches"][2]
    assert c["interval"] is None and c["na"]["interval"] == "BRANCH_NOT_SETTLED"
    na_is_explained(trace)


def test_pool_of_another_run_gives_no_interval(tmp_path):
    synthetic(tmp_path, "ui-parallel-000000000001", dict(a=(0, 4), b=(1, 4)), pool_run_id="ui-parallel-999999999999")
    trace = build(tmp_path)
    assert trace["totals"]["overlap"]["overlap_seconds"] is None
    assert {b["na"]["interval"] for b in trace["branches"]} == {"POOL_RUN_MISMATCH"}


def test_missing_usage_and_missing_trace_are_na_with_reasons(tmp_path):
    synthetic(tmp_path, "ui-parallel-000000000001", dict(a=(0, 4)),
              attempts=dict(a=[attempt(1, usage=None), attempt(2, "FAILED", usage=(5, 5))]))
    branch = build(tmp_path)["branches"][0]
    first, second = branch["attempts"]
    assert first["input_tokens"] is None and first["na"]["tokens"] == "USAGE_NOT_REPORTED"
    assert first["estimated_cost_usd"] is None and first["na"]["estimated_cost_usd"] == "USAGE_NOT_REPORTED"
    assert second["na"]["tokens"] == "OUTCOME_NOT_SUCCEEDED"
    assert branch["estimated_cost_usd"] is None and branch["na"]["estimated_cost_usd"] == "NO_REQUEST_WITH_USAGE"
    assert branch["events"] is None and branch["na"]["events"] == "TRACE_NOT_RECORDED"
    assert first["interval"] is None and first["na"]["interval"] == "REQUEST_HAS_NO_TIMESTAMP"
    na_is_explained(build(tmp_path))


def test_estimated_cost_and_reserved_cost_are_separate(tmp_path):
    synthetic(tmp_path, "ui-parallel-000000000001", dict(a=(0, 4)),
              attempts=dict(a=[attempt(1, usage=(1_000_000, 100_000)), attempt(2, "UNCERTAIN", usage=None)]))
    trace = build(tmp_path)
    branch = trace["branches"][0]
    assert branch["estimated_cost_usd"] == 1.5  # 1M input * $1 + 100k output * $5, per million
    assert branch["reserved_cost_usd"] == 1.0 and branch["unestimated_requests"] == 1
    assert trace["totals"]["estimated_cost_usd"] == 1.5 and trace["totals"]["reserved_cost_usd"] == 1.0
    assert "NOT_A_BILL" in trace["cost_basis"]["estimated"] and "NOT_A_BILL" in trace["cost_basis"]["reserved"]
    assert trace["cost_basis"]["input_usd_per_million"] == 1.0


def test_uncertain_and_pending_attempts_are_marked_not_verified(tmp_path):
    synthetic(tmp_path, "ui-parallel-000000000001", dict(a=(0, 4)), statuses=dict(a="UNCERTAIN"),
              attempts=dict(a=[attempt(1), attempt(2, "UNCERTAIN", usage=None), attempt(3, "PENDING", usage=None)]))
    rows = build(tmp_path, snap=snapshot("PARTIAL"))["branches"][0]["attempts"]
    assert [(row["outcome"], row["verified"], row["note"]) for row in rows] == [
        ("SUCCEEDED", True, None), ("UNCERTAIN", False, "OUTCOME_UNCERTAIN_MAY_BE_BILLED"),
        ("PENDING", False, "OUTCOME_PENDING_NOT_VERIFIED")]


def test_withheld_runs_show_no_subquestion_evidence_or_answer(tmp_path):
    for status in ("ABORTED", "CANCELLED"):
        root = tmp_path / status
        synthetic(root, "ui-parallel-000000000001", dict(a=(0, 4), b=(1, 4)),
                  attempts=dict(a=[attempt(1, provider_request_id="prov-secret")]), planner=dict(source="LLM", reason=None))
        snap = snapshot(status, reason="TASK_SOURCE_AUTHORIZATION_CHANGED")
        snap["parallel_investigation"].pop("branches")
        trace = build(root, snap=snap)
        text = json.dumps(trace)
        assert trace["withheld"] is True and trace["synthesizer"]["status"] == "WITHHELD"
        for leaked in (SECRET_QUESTION, "prov-secret", "a" * 64, "evidence", "citation", "run_p4"):
            assert leaked not in text, leaked
        assert all(b["question"] is None and b["events"] is None and b["reviewer"] is None for b in trace["branches"])
        assert trace["branches"][0]["requests"] == 1 and trace["totals"]["reserved_cost_usd"] == 1.0
        assert trace["branches"][0]["interval"] is not None
        na_is_explained(trace)


def test_unreadable_record_raises(tmp_path):
    (tmp_path / "parallel.json").write_text("{}", encoding="utf8")
    try:
        build(tmp_path)
    except Exception:
        return
    raise AssertionError("an invalid parallel.json must not produce a call chain")


def test_branch_budget_symlink_is_not_followed(tmp_path):
    root, outside = tmp_path / "run", tmp_path / "outside.json"
    synthetic(root, "ui-parallel-000000000001", dict(a=(0, 4), b=(1, 4)))
    write(outside, budget(T0, 1, [attempt(1)]))
    (root / "branches" / "a" / "budget.json").unlink()
    (root / "branches" / "a" / "budget.json").symlink_to(outside)
    branch = build(root)["branches"][0]
    assert branch["interval"] is None and branch["na"]["interval"] == "BUDGET_RECORD_UNAVAILABLE"


# ---- real runs through the product server ----------------------------------------------------------

def get_trace(env, run_id, workspace_id=None):
    from tests.integration.test_product_application_flow import _client
    return _client(env.base, f"/api/product/ask/runs/{run_id}/trace?workspace_id={env.workspace_id if workspace_id is None else workspace_id}")


def overlapping_run(env):
    barrier, models = Barrier(2), {}

    def factory(question):
        models[question] = MeetingProvider(question, barrier, "complete", OPEN)
        return models[question]

    service = env.service(factory)
    from tests.integration.test_product_application_flow import _client
    status, payload = _client(env.base, "/api/product/ask", method="POST", data=dict(
        query=QUESTION, workspace_id=env.workspace_id, subject="Launch", relation="uses supplier", as_of="2025-02-20"))
    assert status == 202, payload
    run_id = payload["result"]["agent_run_id"]
    service.close(timeout=25)
    return run_id, env.work / run_id.removeprefix("ui-parallel-"), service


def test_real_overlap_comes_from_the_logs_and_cold_read_matches_live(env):
    run_id, root, _ = overlapping_run(env)
    status, live = get_trace(env, run_id)
    assert status == 200, live
    trace = live["result"]
    assert trace["source"] == "LIVE" and trace["status"] == "COMPLETE" and trace["synthesizer"]["published"] is True
    receipts = {name: json.loads((root / "branches" / name / "budget.json").read_text(encoding="utf8"))
                for name in ("branch-1", "branch-2")}
    spans = {}
    for row in trace["branches"]:
        receipt = receipts[row["name"]]
        start = datetime.fromisoformat(receipt["started_at"])
        assert row["interval"]["start"] == start.isoformat()
        assert row["interval"]["end"] == (start + timedelta(seconds=receipt["elapsed_seconds"])).isoformat()
        assert row["requests"] == receipt["requests_reserved"] == len(row["attempts"])
        assert row["events"] and row["events_summary"]["timed"] is False
        assert row["na"]["event_timing"] == "TRACE_EVENTS_HAVE_NO_TIMESTAMPS"
        assert row["reviewer"]["decision"] == "evidence_sufficient"
        spans[row["name"]] = (start, start + timedelta(seconds=receipt["elapsed_seconds"]))
    shared = (min(e for _, e in spans.values()) - max(s for s, _ in spans.values())).total_seconds()
    assert shared > 0  # the Barrier made both first requests wait for each other
    assert trace["totals"]["overlap"]["overlap_seconds"] == round(shared, 3) > 0
    assert trace["totals"]["requests"] == sum(r["requests_reserved"] for r in receipts.values())
    assert trace["planner"]["source"] == "NOT_RUN"
    assert {row["resume_state"]["state"] for row in trace["branches"]} == {"REUSED"}
    na_is_explained(trace)

    factory = Counter()
    env.service(factory)  # restart: nothing in memory
    before = tree(env.work)
    status, cold = get_trace(env, run_id)
    assert status == 200 and cold["result"]["source"] == "SAVED"
    assert {k: v for k, v in cold["result"].items() if k != "source"} == {k: v for k, v in trace.items() if k != "source"}
    assert factory.calls == 0 and tree(env.work) == before


def test_trace_endpoint_checks_workspace_run_id_and_flag(env):
    run_id, root, service = overlapping_run(env)
    assert get_trace(env, run_id, "ws_other")[0] == 409
    assert get_trace(env, run_id, "")[0] == 409
    for bad in ("ui-parallel-../../x", "ui-parallel-000000000000", "..", "ui-parallel-" + "0" * 12 + "%2F.."):
        assert get_trace(env, bad)[0] == 404, bad
    assert service.parallel_trace(run_id, "ws_other") is None  # the service refuses on its own, live
    cold = env.service(Counter())
    assert cold.parallel_trace(run_id, "ws_other") is None and cold.parallel_trace(run_id, env.workspace_id) is not None
    service = cold
    service.parallel_limits = None
    status, payload = get_trace(env, run_id)
    assert status == 404 and payload["error"]["code"] == "TRACE_NOT_FOUND"


def test_cold_trace_of_a_run_whose_authorization_changed_shows_only_metadata(env):
    run_id, root, _ = overlapping_run(env)
    env.service(Counter())
    env.edit_json(root / "parallel.json", lambda data: data.update(identity="changed"))
    status, payload = get_trace(env, run_id)
    trace = payload["result"]
    assert status == 200 and trace["status"] == "ABORTED" and trace["withheld"] is True
    text = json.dumps(trace)
    for row in json.loads((root / "parallel.json").read_text(encoding="utf8"))["branches"]:
        assert row["question"] not in text
    assert "src_" not in text and "citation" not in text
    assert trace["synthesizer"]["status"] == "WITHHELD"
    assert trace["totals"]["requests"] > 0 and trace["totals"]["reserved_cost_usd"] > 0


def test_pending_branch_is_shown_as_uncertain_in_a_cold_trace(env):
    run_id, root, _ = overlapping_run(env)
    env.make_pending(root)
    env.service(Counter())
    status, payload = get_trace(env, run_id)
    assert status == 200, payload
    branch = next(row for row in payload["result"]["branches"] if row["name"] == "branch-2")
    assert branch["attempts"][-1]["outcome"] == "PENDING" and branch["attempts"][-1]["verified"] is False
    assert branch["attempts"][-1]["note"] == "OUTCOME_PENDING_NOT_VERIFIED"
    assert branch["verified"] is False and branch["reviewer"] is None and branch["na"]["reviewer"] == "BRANCH_NOT_PUBLISHED"
    assert branch["resume_state"]["state"] == "UNCERTAIN"
