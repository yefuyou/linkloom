"""Offline guards for the one-budget, two-stage development acceptance run."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from scripts import eval_task_field_bindings_v2 as ev


@pytest.fixture
def budget(tmp_path, monkeypatch):
    fixed = {"samples": [{"sample_id": "sample-1"}, {"sample_id": "sample-2"}]}
    monkeypatch.setattr(ev, "verify_fixed", lambda out: (fixed, {}, {}))
    monkeypatch.setattr(ev, "freeze_run", lambda out: {"frozen": "offline-test"})
    with ev.SharedBudget(tmp_path) as value:
        yield value


def request(task="core"):
    return SimpleNamespace(run_id="run", task_id=task, turn_id="turn",
        to_dict=lambda: {"run_id": "run", "task_id": task, "turn_id": "turn"})


def metadata(sample="A", stage="stage1", namespace="ab"):
    return dict(namespace=namespace, sample_id=sample, stage=stage,
        stage1_prompt_sha256="prompt", source_text_sha256="source", original_claim_index=None)


def reserve(budget, sample="A", stage="stage1", task="core", body=None):
    output = ev.STAGE1_MAX_OUTPUT if stage == "stage1" else ev.STAGE2_MAX_OUTPUT
    return budget.reserve(metadata(sample, stage), request(task), body or {"contents": "x"}, output)


def wire(budget, *, host="generativelanguage.googleapis.com"):
    budget.wire(SimpleNamespace(method="POST", url=SimpleNamespace(host=host,
        path="/v1/models/test:generateContent")))


def complete(budget):
    wire(budget)
    budget.response({"usage_metadata": dict(prompt_token_count=10, candidates_token_count=2,
        thoughts_token_count=3, cached_content_token_count=4)})
    budget.finish(SimpleNamespace(to_dict=lambda: {"result": "known"}))


def test_cli_requires_explicit_live_before_loading_any_data(capsys):
    assert ev.main([])["network"] == "not used"
    with pytest.raises(SystemExit) as error:
        ev.main(["run", "--out", "absent"])
    assert error.value.code == 2


def test_shared_budget_counts_both_stages_and_both_namespaces(budget):
    reserve(budget)
    complete(budget)
    reserve(budget, stage="stage2", task="task_c0")
    complete(budget)
    budget.reserve(metadata("sample-1", namespace="es2002"), request(), {"contents": "x"}, ev.STAGE1_MAX_OUTPUT)
    complete(budget)
    expected = ((10 - 4) * .75 + 4 * .075 + 5 * 3.75) / 1e6
    assert budget.state["requests"] == budget.state["actual_wire_requests"] == 3
    assert budget.state["known_usage_cost_usd"] == pytest.approx(expected * 3)
    assert budget.state["charged_upper_bound_usd"] == pytest.approx(expected * 3)
    assert budget.state["active"] is None
    budget.verify()


def test_second_process_cannot_open_same_ledger(budget):
    with pytest.raises(RuntimeError, match="owns the shared ledger"):
        ev.ExclusiveRunLock(budget.out / "budget.lock")


def test_duplicate_request_cannot_be_replayed_even_after_completion(budget):
    reserve(budget)
    complete(budget)
    with pytest.raises(RuntimeError, match="Duplicate"):
        reserve(budget)
    assert budget.state["actual_wire_requests"] == 1


def test_unapproved_source_stage_and_changed_output_cap_fail_before_wire(budget):
    with pytest.raises(ValueError, match="outside approved"):
        reserve(budget, sample="C")
    with pytest.raises(ValueError, match="output cap"):
        budget.reserve(metadata(), request(), {}, 10)
    assert budget.state["requests"] == budget.state["actual_wire_requests"] == 0


def test_cost_ceiling_is_checked_before_wire_and_field_failure_is_typed(budget):
    from linkloom.semantic_ingestion.product_task_stages import AnnotationBudgetUnavailable
    with pytest.raises(AnnotationBudgetUnavailable, match="ceiling"):
        reserve(budget, stage="stage2", task="task_c0", body={"contents": "x" * 900000})
    assert budget.state["blocked"] == "REQUEST_OR_COST_CEILING"
    assert budget.state["actual_wire_requests"] == budget.state["requests"] == 0


def test_wire_redirect_or_unreserved_second_wire_is_blocked(budget):
    reserve(budget)
    with pytest.raises(RuntimeError, match="wire blocked"):
        wire(budget, host="unapproved.example")
    assert budget.state["actual_wire_requests"] == 0
    wire(budget)
    with pytest.raises(RuntimeError, match="wire blocked"):
        wire(budget)
    assert budget.state["actual_wire_requests"] == 1


def test_unknown_usage_keeps_full_reservation_and_stops_replay(budget):
    reserve(budget)
    wire(budget)
    reserved = budget.state["charged_upper_bound_usd"]
    with pytest.raises(ValueError, match="usage missing"):
        budget.response({"usage_metadata": {}})
    budget.fail_active(ValueError("unknown"))
    assert budget.state["charged_upper_bound_usd"] == reserved
    assert budget.state["known_usage_cost_usd"] == 0
    assert budget.state["blocked"] == "UNRESOLVED_PROVIDER_OUTCOME"
    with pytest.raises(PermissionError, match="stopped"):
        reserve(budget, sample="B")
    assert budget.state["actual_wire_requests"] == 1
    budget.verify()


def test_unresolved_restart_does_not_reset_or_reissue(budget):
    reserve(budget)
    wire(budget)
    path = budget.out
    budget.close()
    with ev.SharedBudget(path) as resumed:
        assert resumed.state["blocked"] == "UNRESOLVED_PRIOR_REQUEST"
        assert resumed.state["requests"] == resumed.state["actual_wire_requests"] == 1
        with pytest.raises(PermissionError, match="stopped"):
            reserve(resumed, sample="B")


def test_response_and_result_pairing_are_immutable(budget):
    reserve(budget)
    complete(budget)
    budget.save_result("ab", "A", {"extraction": "known"})
    assert budget.prior_result("ab", "A")["result"] == {"extraction": "known"}
    result = budget.out / "results/ab/A.json"
    result.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Saved result changed"):
        budget.prior_result("ab", "A")
    (budget.out / "responses/0001.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="pairing changed"):
        budget.verify()


def test_scopes_require_exact_original_ab_sentences():
    assert ev.exact_ab_sample("联调报告由李明负责，10/12 前完成。") == "A"
    assert ev.exact_ab_sample("联调报告改由王芳负责，延期到 10/16。") == "B"
    with pytest.raises(ValueError, match="sentence changed"):
        ev.exact_ab_sample("联调报告改由王芳负责，延期至 10/16。")


def test_usage_cost_is_recomputed_during_ledger_verification(budget):
    reserve(budget)
    complete(budget)
    budget.state["entries"][0]["usage"]["thoughts_token_count"] += 1
    with pytest.raises(ValueError, match="recorded cost"):
        budget.verify()


@pytest.fixture
def report_inputs(tmp_path, monkeypatch):
    gold = [{"gold_id": f"g{i}", "deadline": "未说明" if i == 10 else "next meeting"} for i in range(11)]
    positive = [dict(sample_id="positive", sample_kind="reference_positive")]
    negative = [dict(sample_id=f"negative-{i}", sample_kind="reference_negative") for i in range(200)]
    fixed = dict(gold_tasks=gold, gold_to_samples={row["gold_id"]: ["positive"] for row in gold},
        samples=positive + negative, seed=20261101)
    results = {row["sample_id"]: dict(candidates=[]) for row in negative}
    results["positive"] = dict(candidates=[dict(prediction_id=f"positive:{i}") for i in range(11)])
    entries = [dict(namespace="ab", stage="stage1") for _ in range(2)]
    entries += [dict(namespace="es2002", stage="stage1") for _ in range(210)]
    entries += [dict(namespace="es2002", stage="stage2") for _ in range(11)]
    budget = dict(entries=entries, requests=len(entries), actual_wire_requests=len(entries),
        known_usage_cost_usd=.31, charged_upper_bound_usd=.31, pricing=ev.PRICE)
    monkeypatch.setattr(ev, "completed_results", lambda out: (fixed, budget, results))
    rows = [dict(gold_id=f"g{i}", prediction_id=f"positive:{i}" if i < 8 else "",
        extracted="true" if i < 8 else "false", owner_correct="true" if i < 3 else "false",
        deadline_correct="false", reason="Exact work evidence, full owner only") for i in range(11)]
    def write(rows):
        import csv
        path = tmp_path / "review.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path
    return tmp_path, rows, results, write


def test_report_uses_exact_11_and_200_denominators_and_both_false_positive_gates(report_inputs):
    root, rows, results, write = report_inputs
    results["negative-0"]["candidates"] = [{}, {}, {}]
    result = ev.report(root, write(rows))
    assert result["counts"]["extracted"] == 8
    assert result["counts"]["owner_correct"] == 3
    assert result["counts"]["gold_tasks"] == 11
    assert result["counts"]["negative_utterances"] == 200
    assert result["counts"]["false_positive_utterances"] == 1
    assert result["counts"]["false_positive_candidates"] == 3
    assert result["targets"]["negative_utterances"] is True
    assert result["targets"]["negative_candidates"] is False
    assert result["target_passed"] is False


def test_report_never_allows_one_prediction_to_match_multiple_gold(report_inputs):
    root, rows, _, write = report_inputs
    rows[1]["prediction_id"] = rows[0]["prediction_id"]
    with pytest.raises(ValueError, match="already matched"):
        ev.report(root, write(rows))


def test_report_rejects_missing_gold_and_unstated_deadline_credit(report_inputs):
    root, rows, _, write = report_inputs
    with pytest.raises(ValueError, match="all eleven"):
        ev.report(root, write(rows[:-1]))
    rows[-1].update(extracted="true", prediction_id="positive:10", deadline_correct="true")
    with pytest.raises(ValueError, match="Unstated"):
        ev.report(root, write(rows))
