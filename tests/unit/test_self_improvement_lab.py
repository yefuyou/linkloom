import pytest

from scripts.self_improvement.contracts import Candidate, failure_signals, promotion_gate


def test_candidate_cannot_modify_permissions_or_execute_code():
    with pytest.raises(ValueError, match="fields"):
        Candidate.parse({"id": "evil", "change": "disable checks", "permissions": False})
    with pytest.raises(ValueError):
        Candidate.parse({"id": "evil", "change": "unbounded", "limit": 100,
                         "query_mode": "literal", "read_mode": "all"})


def test_attribution_is_derived_from_observed_execution_not_model_text():
    row = {"id": "x", "outcome": "partial", "reason": "evidence_insufficient",
           "calls": [{"tool": "read_verified_note", "evidence_id": "a", "status": "completed"},
                     {"tool": "read_verified_note", "evidence_id": "a", "status": "completed"},
                     {"tool": "search_notes", "status": "failed", "variant": "alternate"}]}
    signal = failure_signals([row])[0]
    assert signal["counts"]["duplicate_read"] == 1
    assert signal["counts"]["illegal_alternate"] == 1
    assert "gold" not in signal and "expected" not in signal


def metric(**changes):
    return {"accuracy": .5, "grounding": 1., "refusal_accuracy": 1., "calls": 10,
            "context_bytes": 2000, "safety_violations": 0, **changes}


def test_success_gain_never_overrides_safety_regression():
    assert not promotion_gate(metric(), metric(accuracy=1., safety_violations=1))[0]
    assert not promotion_gate(metric(), metric(accuracy=1., refusal_accuracy=0.))[0]
    assert not promotion_gate(metric(), metric(accuracy=1., context_bytes=4000))[0]
    assert promotion_gate(metric(), metric(accuracy=1.))[0]
    assert not promotion_gate(metric(), metric())[0]
    assert not promotion_gate(metric(runtime_failures=0), metric(accuracy=1., runtime_failures=1))[0]


def test_gold_fields_and_answer_text_never_enter_failure_feedback():
    row = {"id": "x", "calls": [], "reason": "evidence_insufficient",
           "expected": {"answer": "SECRET GOLD"}, "values": ["SECRET OBSERVED VALUE"]}
    import json
    assert "SECRET" not in json.dumps(failure_signals([row]))


def test_two_strict_uncertain_requests_stop_future_sends(tmp_path, monkeypatch):
    import httpx
    from types import SimpleNamespace
    from scripts.self_improvement import provider
    from linkloom.agents.providers import gemini_api
    sends, options = [], []

    def build(key, **kwargs):
        options.append(kwargs["http_options_kwargs"])

        def send(**payload):
            sends.append(payload)
            raise httpx.ReadTimeout("No confirmed response")

        return SimpleNamespace(models=SimpleNamespace(generate_content=send), close=lambda: None)

    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    monkeypatch.setattr(gemini_api, "build_gemini_sdk_client", build)
    for _ in range(2):
        with pytest.raises(Exception):
            provider.generate("probe", tmp_path)
    assert provider.accounting(tmp_path)["strict_uncertain"] == 2
    with pytest.raises(RuntimeError, match="PROVIDER_STOP_LIMIT"):
        provider.generate("probe", tmp_path)
    assert len(sends) == 2
    assert all(option["retry_options"].attempts == 1 for option in options)


def test_cost_cap_survives_a_new_process_and_never_treats_missing_usage_as_free(tmp_path):
    import json
    from scripts.self_improvement import provider
    folder = tmp_path / "provider" / "prior"
    folder.mkdir(parents=True)
    events = []
    for i in range(25):
        events.extend([{"event": "request_reserved", "request_id": str(i)},
                       {"event": "request_finished", "request_id": str(i), "usage": None}])
    events.append({"event": "run_finished"})
    (folder / "provider_ledger.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    (folder / "outcome.json").write_text('{"strict_uncertain": 0}', encoding="utf-8")
    assert provider.accounting(tmp_path)["reserved_usd"] == 5
    with pytest.raises(RuntimeError, match="PROVIDER_COST_LIMIT"):
        provider.generate("probe", tmp_path)


def test_failed_case_cannot_hide_a_new_grounding_regression_in_aggregate():
    from scripts.self_improvement.run import compare
    before = {"metrics": metric(), "cases": [{"id": "x", "pass": False, "citation_valid": True}]}
    after = {"metrics": metric(accuracy=1.), "cases": [{"id": "x", "pass": False, "citation_valid": False}]}
    assert compare(before, after) == (False, "per_case_citation_valid_regression")


def test_batch_reconciliation_preserves_reservations_and_rejects_inflight(tmp_path):
    import json
    from scripts.self_improvement import provider
    folder = tmp_path / "provider" / "prior"
    folder.mkdir(parents=True)
    ledger = folder / "provider_ledger.jsonl"
    original = json.dumps({"event": "request_reserved", "request_id": "one"}) + "\n"
    ledger.write_text(original, encoding="utf-8")
    (folder / "outcome.json").write_text('{"strict_uncertain": 0}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="UNCLOSED_PROVIDER_RESERVATION"):
        provider.reconcile_completed_batches(tmp_path)
    assert ledger.read_text(encoding="utf-8") == original
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "request_finished", "request_id": "one"}) + "\n")
    before = ledger.read_bytes()
    provider.reconcile_completed_batches(tmp_path)
    assert ledger.read_bytes().startswith(before)
    assert provider.accounting(tmp_path)["requests"] == 1
    assert provider.accounting(tmp_path)["incomplete_batches"] == 0


def test_targeted_signal_must_improve_not_only_an_unrelated_case():
    from scripts.self_improvement.contracts import targeted_reductions
    candidate = Candidate("query-test", "Replace dependent question words with relation terms", query_mode="relation_terms")
    before = [{"case_id": "missing", "counts": {"unbound_next_hop": 1}}]
    unchanged = [{"id": "missing", "calls": [], "reason": "evidence_insufficient"}]
    assert targeted_reductions(before, unchanged, candidate) == []
    improved = [{"id": "missing", "calls": [], "reason": None}]
    assert targeted_reductions(before, improved, candidate)[0]["after"] == 0


def test_already_incorrect_refusal_cannot_silently_change_category():
    from scripts.self_improvement.run import compare
    before = {"metrics": metric(), "cases": [{"id": "x", "pass": False, "reason": "wrong_a"}]}
    after = {"metrics": metric(accuracy=1.), "cases": [{"id": "x", "pass": False,
             "reason": "wrong_b", "reason_match": False}]}
    assert compare(before, after)[0] is False


def test_final_execution_failure_still_runs_regression_and_rejects(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from scripts.self_improvement import run
    monkeypatch.setattr(run, "ROOT", tmp_path)
    executed = []

    def child(module, arguments, log, **kwargs):
        executed.append(module)
        if module.endswith("generator"):
            output = arguments[arguments.index("--output") + 1]
            run.write(output, {"candidates": []})
        elif module == "pytest":
            from pathlib import Path
            output = next(str(arg).split("=", 1)[1] for arg in arguments if str(arg).startswith("--junitxml="))
            Path(output).write_text('<testsuites/>', encoding="utf-8")

    def evaluate(candidate, split, out):
        if split == "final":
            raise RuntimeError("worker exited")
        return {"metrics": metric(), "cases": []}, {"cases": []}

    monkeypatch.setattr(run, "child", child)
    monkeypatch.setattr(run, "evaluate", evaluate)
    out = run.experiment(SimpleNamespace(budget=1, rounds=1, live_reflection=False, development_only=False))
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert "pytest" in executed
    assert report["final"]["gate"] == "execution_failed"
    assert report["regression"]["passed"] is True and report["conclusion"] == "B"
