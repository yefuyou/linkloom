"""Usage is an observation of what Providers reported; it is never an admission condition for the next step.

Offline Provider doubles only. Every run here reports usage far above any historical budget (a trillion tokens
per request, many requests per session) and must still complete, aggregate exactly and publish its answer.
"""
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.observability.ask_telemetry import build_ask_telemetry
from linkloom.observability.pricing import estimate_cost_usd
from linkloom.orchestration import OrchestrationLimits
from linkloom.ui.task_provider import (
    TaskProviderLimits, TaskProviderSession, read_provider_journal, validate_task_provider_receipt,
)
from tests.integration import test_orchestration_langgraph as lg
from tests.integration.test_orchestration_langgraph import (
    GOAL, Q_LEADER, Q_OWNER, Q_SUCC, TASKS, TOTAL, World, build, delegation, run_limits,
)
from tests.integration.test_orchestration_product import make_env, started  # noqa: F401 (pytest fixture)
from tests.unit.test_task_provider_session import request

HUGE = 10 ** 12  # tokens per request, above any budget the product ever had
THREE = [("succ", Q_SUCC, []), ("owner", Q_OWNER, []), ("leader", Q_LEADER, ["succ", "owner"])]


def report(monkeypatch, **usage):
    """Make every scripted Provider reply carry this usage."""
    monkeypatch.setattr(lg, "ModelUsage", lambda **_ignored: ModelUsage(**usage))


def journals(root, result):
    found = {"supervisor/round-0": root / "supervisor" / "round-0"}
    for task in result["tasks"]:
        found[f"tasks/{task}/attempt-0-1"] = root / "tasks" / task / "attempt-0-1"
    if (root / "comparison").is_dir():
        found["comparison"] = root / "comparison"
    return {scope: read_provider_journal(path) for scope, path in found.items()}


def run_three_workers(tmp_path, **options):
    world = World([delegation("delegate", THREE), delegation("stop")], TASKS)
    orchestrator, deps = build(tmp_path, world, limits=run_limits(max_tasks=4, max_rounds=2, **options))
    return world, deps, orchestrator.start(GOAL)


def test_cumulative_tokens_and_cost_far_above_any_old_budget_do_not_stop_the_run(tmp_path, monkeypatch):
    """Acceptance 1, 2, 3: the run keeps going past every historical token, cost and request ceiling."""
    report(monkeypatch, input_tokens=HUGE, output_tokens=HUGE)
    world, deps, result = run_three_workers(tmp_path)
    assert result["status"] == "COMPLETE", result["tasks"]
    assert all(task["status"] == lg.ACCEPTED for task in result["tasks"].values())
    found = journals(deps.root, result)
    rows = [row for journal in found.values() for row in journal["attempts"]]
    assert len(rows) > 10 and all(row["outcome"] == "SUCCEEDED" for row in rows)
    assert all(journal["failure"] is None for journal in found.values())
    assert sum(row["usage"]["input_tokens"] for row in rows) == HUGE * len(rows) > 10 ** 13
    assert sum(row["estimated_cost_usd"] for row in rows) > 10 ** 5  # dollars, far above any old cost cap
    assert json.dumps(result).count("BUDGET") == 0 and "COST_LIMIT" not in json.dumps(result)


def test_multi_worker_cost_and_tokens_aggregate_exactly(tmp_path, monkeypatch):
    """Acceptance 5: Supervisor, every Worker attempt and the comparison sum exactly, nothing counted twice."""
    report(monkeypatch, input_tokens=1000, output_tokens=400, cached_input_tokens=300, reasoning_tokens=50)
    world, deps, result = run_three_workers(tmp_path)
    found = journals(deps.root, result)
    count = sum(len(journal["attempts"]) for journal in found.values())
    assert set(found) >= {"supervisor/round-0", "tasks/succ/attempt-0-1", "tasks/owner/attempt-0-1",
                          "tasks/leader/attempt-0-1"} and count >= 10
    telemetry = build_ask_telemetry(run_id="r", workspace_id="w", status="COMPLETE", provider_journals=found,
                                    provider_journals_complete=True)
    assert telemetry["provider_calls"]["attempted"] == count
    assert telemetry["usage"]["input_tokens"]["value"] == 1000 * count
    assert telemetry["usage"]["output_tokens"]["value"] == 400 * count
    assert telemetry["usage"]["total_tokens"]["value"] == 1400 * count  # sub-counts are not added again
    assert telemetry["usage"]["cached_input_tokens"]["value"] == 300 * count
    assert telemetry["usage"]["reasoning_tokens"]["value"] == 50 * count
    assert telemetry["cost"]["estimated_usd"]["value"] == pytest.approx(estimate_cost_usd(1000, 400) * count)
    per_worker = {scope: sum(row["usage"]["input_tokens"] for row in journal["attempts"])
                  for scope, journal in found.items() if scope.startswith("tasks/")}
    assert all(total == 1000 * len(found[scope]["attempts"]) for scope, total in per_worker.items())


def test_router_and_security_usage_is_counted_but_never_reduces_worker_limits(tmp_path, monkeypatch):
    """Acceptance 4: their sessions appear in telemetry; the Worker session starts with the unchanged limits."""
    report(monkeypatch, input_tokens=1000, output_tokens=400)
    router_limits = TaskProviderLimits(15, 15)
    spent = {}
    for stage in ("ask-security", "ask-router"):
        session = TaskProviderSession(tmp_path / stage, router_limits, lambda: None)
        bound = session.bind(SimpleNamespace(complete=lambda _r: ModelResponse(
            action=ModelAction.final("x"), usage=ModelUsage(input_tokens=HUGE, output_tokens=HUGE))), stage)
        for _ in range(3):
            assert bound.complete(request()).action.final_answer == "x"
        spent[stage] = read_provider_journal(tmp_path / stage)
        session.close()
    (tmp_path / "run").mkdir()
    world, deps, result = run_three_workers(tmp_path / "run")
    assert deps.limits.provider == TOTAL
    worker = read_provider_journal(deps.root / "tasks" / "succ" / "attempt-0-1")
    assert worker["limits"] == asdict(TOTAL) and worker["failure"] is None
    found = journals(deps.root, result)
    everything = {"router/security": spent["ask-security"], "router/router": spent["ask-router"], **found}
    telemetry = build_ask_telemetry(run_id="r", workspace_id="w", status="COMPLETE", provider_journals=everything,
                                    provider_journals_complete=True)
    run_count = sum(len(journal["attempts"]) for journal in found.values())
    assert telemetry["provider_calls"]["attempted"] == run_count + 6
    assert telemetry["usage"]["input_tokens"]["value"] == 6 * HUGE + 1000 * run_count


def test_valid_receipts_above_every_historical_budget_are_accepted_and_the_answer_is_published(tmp_path, monkeypatch):
    """Acceptance 6: validate_task_provider_receipt takes the journals of the huge-usage run."""
    from datetime import UTC, datetime
    report(monkeypatch, input_tokens=HUGE, output_tokens=HUGE, provider_reported_cost_usd=10 ** 9)
    world, deps, result = run_three_workers(tmp_path)
    assert result["status"] == "COMPLETE" and result["answer"]["published"] is True
    completed = datetime.now(UTC).isoformat()
    for path in (deps.root / "supervisor" / "round-0", *(deps.root / "tasks" / t / "attempt-0-1" for t in result["tasks"])):
        validate_task_provider_receipt(path / "provider_journal.json", completed)


def test_responses_without_any_usage_are_published(tmp_path, monkeypatch):
    """Acceptance 7: usage all None is unknown, not an error, and the answer is still released."""
    report(monkeypatch)
    world, deps, result = run_three_workers(tmp_path)
    assert result["status"] == "COMPLETE" and result["answer"]["published"] is True
    found = journals(deps.root, result)
    rows = [row for journal in found.values() for row in journal["attempts"]]
    assert all(row["outcome"] == "SUCCEEDED" and row["estimated_cost_usd"] is None for row in rows)
    telemetry = build_ask_telemetry(run_id="r", workspace_id="w", status="COMPLETE", provider_journals=found,
                                    provider_journals_complete=True)
    assert telemetry["usage"]["input_tokens"]["value"] is None
    assert telemetry["usage"]["input_tokens"]["unreported_requests"] == len(rows)
    assert telemetry["cost"]["estimated_usd"]["source"] == "unknown"


def legacy_limits(**changes):
    """A limits dict as an old run stored it: tiny, zero and removed budget fields included."""
    provider = dict(max_requests=1, max_input_tokens=1, max_output_tokens=1, max_cost_usd=1e-9,
                    estimated_cost_per_request_usd=99, max_seconds=120, request_timeout_seconds=20)
    return dict(provider=provider, max_run_cost_usd=1e-9, max_tasks=4, max_rounds=2, **changes)


def test_legacy_limits_dict_is_readable_and_its_budget_fields_never_decide(tmp_path, monkeypatch):
    """Acceptance 9 (orchestration layer): a stored record with tiny budgets still runs to completion."""
    report(monkeypatch, input_tokens=HUGE, output_tokens=HUGE)
    limits = OrchestrationLimits.from_dict(legacy_limits())
    assert limits.provider == TaskProviderLimits(120, 20)
    stored = limits.to_dict()
    assert not {"max_run_cost_usd", "max_requests", "max_cost_usd"} & (set(stored) | set(stored["provider"]))
    world = World([delegation("delegate", THREE), delegation("stop")], TASKS)
    orchestrator, deps = build(tmp_path, world, limits=limits)
    result = orchestrator.start(GOAL)
    assert result["status"] == "COMPLETE" and result["answer"]["published"] is True


def test_legacy_budget_journal_is_read_for_history_and_replay_guard_only(tmp_path):
    from linkloom.orchestration.worker import paid_without_receipt
    legacy = dict(requests_reserved=1, input_tokens_reserved=10 ** 9, cost_reserved_usd=5.0, failure=None,
                  limits=dict(max_requests=1, max_cost_usd=.0001, max_seconds=5, request_timeout_seconds=1),
                  attempts=[dict(sequence=1, stage="runtime", request_sha256="a" * 64, outcome="PENDING")])
    (tmp_path / "budget.json").write_text(json.dumps(legacy), encoding="utf8")
    assert read_provider_journal(tmp_path)["requests_reserved"] == 1
    assert paid_without_receipt(tmp_path)  # the request may have been sent: never sent twice
    telemetry = build_ask_telemetry(run_id="r", workspace_id="w", status="COMPLETE",
                                    provider_journals={"tasks/a/attempt-0-1": legacy})
    assert telemetry["usage"]["input_tokens"]["value"] is None  # a reservation is not usage
    assert telemetry["cost"]["estimated_usd"]["known_value"] is None


def test_default_product_composition_with_legacy_tiny_limits_and_huge_usage_publishes(make_env, monkeypatch):
    """Acceptance 9 (product call chain): Ask -> Supervisor -> Workers -> Comparison with fake adapters."""
    report(monkeypatch, input_tokens=HUGE, output_tokens=HUGE)
    limits = TaskProviderLimits.from_dict(legacy_limits()["provider"])
    world = World([delegation("delegate", THREE), delegation("stop")], TASKS)
    env = make_env(world, limits=limits)
    run_id, result = started(env)
    assert result["status"] == "COMPLETED" and result["parallel_investigation"]["status"] == "COMPLETE"
    _, traced = env.run_trace(run_id)
    totals = traced["result"]["totals"]
    assert totals["requests"] > 9 and totals["input_tokens"] == HUGE * totals["requests"]
    assert json.dumps(result).count("BUDGET") == 0
