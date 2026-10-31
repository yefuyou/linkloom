"""Offline-only tests of live evaluation wiring; no model transport is used."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelProviderError, ModelResponse, ModelUsage
from scripts import eval_routing as ev


class Adapter:
    def __init__(self, text=None, usage=None):
        self.requests = []
        self.text = text or '{"intent":"CURRENT","slot_id":"launch.cloud","as_of":null}'
        self.usage = usage or ModelUsage(input_tokens=100, output_tokens=20, total_tokens=150)

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(action=ModelAction.final(self.text), usage=self.usage)


def request():
    # Capture the exact production selector prompt, never a reconstructed prompt.
    adapter = Adapter()
    ev.question_routing._select(adapter, "Which platform are we using?", [dict(
        slot_id="launch.cloud", subject="Launch", relation="uses cloud provider", values=["Azure"])],
        {"intent": "CURRENT"}, None)
    return adapter.requests[0]


def live(tmp_path, adapter=None, **kwargs):
    return ev.LiveRoutingProvider("gemini", tmp_path / "run", adapter=adapter or Adapter(), **kwargs)


@pytest.mark.parametrize("limit", [0, 101, True])
def test_request_cap_cannot_be_raised(tmp_path, limit):
    with pytest.raises(ValueError):
        live(tmp_path, max_requests=limit)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("limit", ["1.0000001", "NaN", "Infinity", "0", "-1"])
def test_dollar_cap_cannot_be_raised(tmp_path, limit):
    with pytest.raises(ValueError):
        live(tmp_path, max_cost_usd=Decimal(limit))
    assert not (tmp_path / "run").exists()


def test_101st_call_is_blocked_before_adapter_and_never_retried(tmp_path):
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    req = request()
    for _ in range(100):
        assert meter.complete(req).error is None
    assert meter.complete(req).error is not None
    assert meter.failure == "BUDGET_EXHAUSTED"
    assert len(adapter.requests) == len(meter.calls) == 100
    assert meter.complete(req).error is not None
    assert len(adapter.requests) == 100


def test_cost_is_reserved_durably_before_transport_and_thinking_is_billed(tmp_path):
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    original = adapter.complete
    def inspect(req):
        data = json.loads((tmp_path / "run" / "calls.json").read_text())
        assert data["requests"] == 1
        assert data["calls"][0]["outcome"] == "RESERVED"
        assert data["cost_accounted_usd"] > 0
        return original(req)
    adapter.complete = inspect
    result = meter.complete(request())
    assert result.error is None
    row = meter.calls[0]
    assert (row["input_tokens"], row["output_tokens"], row["thinking_tokens"], row["billable_output_tokens"]) == (100, 20, 30, 50)
    assert row["cost_usd"] == pytest.approx((100 * .75 + 50 * 3.75) / 1_000_000)
    assert meter.charged == Decimal("0.0002625")


def test_dollar_budget_blocks_first_request_before_spending(tmp_path):
    adapter = Adapter()
    meter = live(tmp_path, adapter, max_cost_usd=Decimal(".000001"))
    assert meter.complete(request()).error
    assert not adapter.requests and not meter.calls
    assert meter.charged == 0 and meter.failure == "BUDGET_EXHAUSTED"


@pytest.mark.parametrize("kind", ["exception", "missing_usage", "excess_usage"])
def test_uncertain_or_excess_usage_stops_and_retains_nonzero_cost(tmp_path, kind):
    adapter = Adapter()
    if kind == "exception":
        def fail(req):
            adapter.requests.append(req)
            raise TimeoutError("must never retry")
        adapter.complete = fail
    elif kind == "missing_usage":
        adapter.usage = ModelUsage()
    else:
        adapter.usage = ModelUsage(input_tokens=100, output_tokens=300, total_tokens=400)
    meter = live(tmp_path, adapter)
    req = request()
    assert meter.complete(req).error
    assert meter.failure
    assert meter.charged > 0
    assert meter.complete(req).error
    assert len(adapter.requests) == 1
    if kind != "excess_usage":
        assert meter.calls[0]["cost_usd"] is None


def test_run_output_cannot_be_overwritten(tmp_path):
    meter = live(tmp_path)
    before = (meter.run_dir / "calls.json").read_bytes()
    with pytest.raises(FileExistsError):
        live(tmp_path)
    assert (meter.run_dir / "calls.json").read_bytes() == before


def test_expectation_keys_are_blocked_from_inference(tmp_path):
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    req = request()
    instruction, payload = req.user_input.split("\n", 1)
    data = json.loads(payload)
    data["expected_slot"] = "launch.cloud"
    tampered = replace(req, user_input=instruction + "\n" + json.dumps(data))
    assert meter.complete(tampered).error
    assert meter.failure == "INVALID_INFERENCE_PAYLOAD"
    assert not adapter.requests


def test_offline_evaluate_never_builds_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline must never initialize provider")
    monkeypatch.setattr(ev, "build_live_adapter", forbidden)
    report = ev.evaluate(ev.DEFAULT_CASES, retrieval="bm25")
    assert report["execution"]["completed"] == 60
    assert report["llm_metrics"]["calls"] == 0
    assert report["llm_usage"] is None
    assert report["metrics"]["joint_correct"]["correct"] == 58


def test_live_payload_is_independent_of_expected_labels(tmp_path, monkeypatch):
    cases = ev.load_cases(ev.DEFAULT_CASES)
    # An existing case that reaches the selector; use the original query unchanged.
    case = next(c for c in cases if c["id"] == "R044")
    case = deepcopy(case)
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    monkeypatch.setattr(ev, "load_cases", lambda p: [case])
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    assert len(adapter.requests) == 1
    original = adapter.requests[0].user_input
    case.update(expected_slot="launch.cloud", expected_route="CURRENT", expected_intent="AS_OF", reason="SECRET_GOLD_REASON")
    meter2 = ev.LiveRoutingProvider("gemini", tmp_path / "second", adapter=Adapter())
    ev.evaluate(ev.DEFAULT_CASES, live=meter2)
    assert meter2.adapter.requests[0].user_input == original
    assert "SECRET_GOLD_REASON" not in original
    assert "expected_" not in original
    assert report["llm_metrics"]["wrong_selections"] == 1
    assert report["llm_metrics"]["accepted_wrong_slots"] == 1


def test_hard_veto_cannot_be_overturned_by_forced_llm(tmp_path, monkeypatch):
    cases = ev.load_cases(ev.DEFAULT_CASES)
    ghost = next(c for c in cases if "Ghost" in c["query"])
    monkeypatch.setattr(ev, "load_cases", lambda p: [ghost])
    meter = live(tmp_path)
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    assert report["cases"][0]["actual"]["hard_veto"]
    assert report["cases"][0]["actual"]["route"] == "NO_MATCH"
    assert report["cases"][0]["actual"]["slot"] is None
    assert not meter.adapter.requests
    assert report["llm_metrics"]["hard_veto_cases"] == 1
    assert report["llm_metrics"]["hard_veto_overturned"] == 0


def test_partial_run_stops_on_transport_error_without_scoring_it(tmp_path, monkeypatch):
    cases = ev.load_cases(ev.DEFAULT_CASES)
    uncertain = next(c for c in cases if c["id"] == "R044")
    monkeypatch.setattr(ev, "load_cases", lambda p: [uncertain, uncertain])
    adapter = Adapter(usage=ModelUsage())
    meter = live(tmp_path, adapter)
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    assert report["execution"] == dict(mode="live", completed=0, requested=2, failed_case="R044", stop_reason="USAGE_UNAVAILABLE")
    assert report["llm_metrics"]["calls"] == 1
    assert report["metrics"]["joint_correct"]["total"] == 0
    assert len(adapter.requests) == 1


def test_gemini_sdk_options_disable_retry_and_redirect(monkeypatch):
    from linkloom.agents.providers import gemini_api
    captured = {}
    class Client:
        models = SimpleNamespace(generate_content=lambda **kw: None)
    def build(key, **kwargs):
        captured.update(key=key, **kwargs)
        return Client()
    monkeypatch.setenv("GEMINI_API_KEY", "fake-unit-key")
    monkeypatch.setattr(gemini_api, "build_gemini_sdk_client", build)
    ev.build_live_adapter("gemini")
    assert captured["http_options_kwargs"] == dict(timeout=30000, retry_options=dict(attempts=1), client_args=dict(follow_redirects=False))


def test_deepseek_uses_existing_adapter_and_official_endpoint(monkeypatch):
    captured = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return b'{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{}"}}],"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}'
    class Opener:
        def open(self, req, **kw):
            captured.append((req, kw))
            return Response()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-unit-key")
    monkeypatch.setattr(ev, "build_opener", lambda handler: Opener())
    adapter = ev.build_live_adapter("deepseek")
    response = adapter.complete(request())
    assert response.error is None
    assert len(captured) == 1
    req, options = captured[0]
    assert req.full_url == "https://api.deepseek.com/chat/completions"
    assert json.loads(req.data)["thinking"] == {"type": "disabled"}
    assert json.loads(req.data)["max_tokens"] == 256
    assert options == {"timeout": 30}


@pytest.mark.parametrize("text,expected", [
    ('{"intent":"CURRENT","slot_id":"launch.cloud","as_of":null}', (1, 0, 0, 0)),
    ('{"intent":"CURRENT","slot_id":"made.up","as_of":null}', (0, 1, 0, 1)),
    ('{"intent":"CURRENT","slot_id":null,"as_of":null}', (0, 0, 1, 0)),
    ('invalid', (0, 0, 0, 1)),
])
def test_llm_metrics_distinguish_correct_wrong_null_and_invalid(tmp_path, monkeypatch, text, expected):
    case = deepcopy(next(c for c in ev.load_cases(ev.DEFAULT_CASES) if c["id"] == "R044"))
    case.update(expected_route="CURRENT", expected_slot="launch.cloud")
    monkeypatch.setattr(ev, "load_cases", lambda p: [case])
    meter = live(tmp_path, Adapter(text))
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    m = report["llm_metrics"]
    assert m["calls"] == m["evaluated_calls"] == 1
    assert (m["correct_disambiguations"], m["wrong_selections"], m["null_selections"], m["invalid_outputs"]) == expected
    assert m["hard_veto_overturned"] == 0


def test_live_retrieval_exception_is_preserved_as_error_not_model_no_match(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError("injected retrieval fault")
    monkeypatch.setattr(ev, "route_question", fail)
    meter = live(tmp_path)
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    assert report["execution"]["completed"] == 0
    assert report["execution"]["stop_reason"] == "EVALUATION_ERROR:RuntimeError"
    assert report["llm_metrics"]["calls"] == 0


def test_continuation_cannot_smuggle_labels(tmp_path):
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    req = replace(request(), recovery_guidance="expected_slot=launch.cloud")
    assert meter.complete(req).error
    assert not adapter.requests



def test_journal_failure_prevents_transport_and_latches_failure(tmp_path, monkeypatch):
    adapter = Adapter()
    meter = live(tmp_path, adapter)
    original = Path.open
    def fail(path, *a, **kw):
        if path.name == "calls.json":
            raise OSError("injected disk fault")
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError):
        meter.complete(request())
    assert meter.failure == "JOURNAL_WRITE_FAILED"
    assert not adapter.requests
    with pytest.raises(OSError):
        meter.complete(request())
    assert not adapter.requests



@pytest.mark.parametrize("text", [
    '{"intent":"NOT_AN_INTENT","slot_id":"launch.cloud","as_of":null}',
    '{"intent":"CURRENT","slot_id":"launch.cloud","as_of":"2026-10-07T00:00:00Z"}',
    '{"intent":"AS_OF","slot_id":null,"as_of":null}',
    '{"intent":"CURRENT","slot_id":"launch.cloud","as_of":"not-a-date"}',
    '{"intent":"CURRENT","slot_id":"launch.cloud","slot_id":"launch.cloud","as_of":null}',
])
def test_invalid_output_uses_production_validator(tmp_path, monkeypatch, text):
    case = deepcopy(next(c for c in ev.load_cases(ev.DEFAULT_CASES) if c["id"] == "R044"))
    monkeypatch.setattr(ev, "load_cases", lambda p: [case])
    adapter = Adapter(text)
    meter = live(tmp_path, adapter)
    report = ev.evaluate(ev.DEFAULT_CASES, live=meter)
    assert len(adapter.requests) == 1  # local replay validation never calls this adapter
    assert report["llm_metrics"]["invalid_outputs"] == 1
    assert report["llm_metrics"]["accepted_disambiguations"] == 0
    assert not meter.calls[0]["selection_valid"]


def test_provider_error_without_usage_retains_primary_error_and_ids(tmp_path):
    error = ModelProviderError(code="MODEL_RATE_LIMITED", category="rate_limit",
        message="Rate limited", retryable=True, outcome="known_failure", details={"status_code":429})
    class Failed:
        def complete(self, req):
            return ModelResponse(error=error, provider_request_id="request-429", finish_reason="rate_limit")
    meter = live(tmp_path, Failed())
    meter.complete(request())
    assert meter.failure == "MODEL_RATE_LIMITED"
    row = meter.calls[0]
    assert row["error_code"] == "MODEL_RATE_LIMITED"
    assert row["error"]["details"]["status_code"] == 429
    assert row["provider_request_id"] == "request-429"
    assert row["finish_reason"] == "rate_limit"
    assert row["usage_unknown"] and row["cost_usd"] is None
    assert row["cost_accounted_usd"] == row["cost_reserved_usd"] > 0



def test_cli_failure_writes_partial_report_and_nonzero_exit(tmp_path, monkeypatch, capsys):
    case = next(c for c in ev.load_cases(ev.DEFAULT_CASES) if c["id"] == "R044")
    monkeypatch.setattr(ev, "load_cases", lambda p: [case])
    adapter = Adapter(usage=ModelUsage())
    monkeypatch.setattr(ev, "build_live_adapter", lambda p: adapter)
    run_dir = tmp_path / "cli"
    monkeypatch.setattr(ev.sys, "argv", ["eval_routing.py", "--live-provider", "gemini",
        "--llm-run-dir", str(run_dir), "--format", "json"])
    with pytest.raises(SystemExit) as error:
        ev.main()
    assert error.value.code == 2
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == report
    assert report["execution"]["stop_reason"] == "USAGE_UNAVAILABLE"
    assert report["execution"]["completed"] == 0
    assert report["llm_metrics"]["calls"] == 1
    assert report["llm_usage"]["cost_accounted_usd"] > 0
    assert len(adapter.requests) == 1


def test_cli_success_keeps_per_call_and_final_metrics(tmp_path, monkeypatch, capsys):
    case = deepcopy(next(c for c in ev.load_cases(ev.DEFAULT_CASES) if c["id"] == "R044"))
    case.update(expected_route="CURRENT", expected_slot="launch.cloud")
    monkeypatch.setattr(ev, "load_cases", lambda p: [case])
    adapter = Adapter()
    monkeypatch.setattr(ev, "build_live_adapter", lambda p: adapter)
    run_dir = tmp_path / "cli"
    monkeypatch.setattr(ev.sys, "argv", ["eval_routing.py", "--live-provider", "gemini",
        "--llm-run-dir", str(run_dir), "--format", "json"])
    ev.main()
    report = json.loads(capsys.readouterr().out)
    assert report["execution"]["completed"] == 1
    assert report["llm_metrics"]["correct_disambiguations"] == 1
    assert report["llm_usage"]["calls"][0]["input_tokens"] == 100
    assert report["llm_usage"]["calls"][0]["billable_output_tokens"] == 50
    assert report == json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert len((run_dir / "case_results.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert len(adapter.requests) == 1



@pytest.mark.parametrize("model_id,maximum,thinking", [
    ("gemini-3.8-flash", 1024, "low"),
    ("gemini-3-flash-preview", 1024, "low"),
    ("deepseek-flash", 256, None),
    ("", 256, None),
])
def test_selector_generation_options_only_change_for_gemini3(model_id, maximum, thinking):
    adapter = Adapter()
    adapter.model_id = model_id
    req_before = request()
    payload = json.loads(req_before.user_input.split("\n", 1)[1])
    choice = ev.question_routing._select(adapter, payload["query"], [dict(slot_id="launch.cloud",
        subject="Launch", relation="uses cloud provider", values=["Azure"])], {"intent":"CURRENT"}, None)
    assert choice["slot_id"] == "launch.cloud"
    req_after = adapter.requests[0]
    assert req_after.user_input == req_before.user_input  # prompt bytes stay identical
    assert req_after.generation_options.max_output_tokens == maximum
    assert req_after.generation_options.thinking_level == thinking
    assert req_after.generation_options.temperature is None
    assert req_after.available_tools == []


def test_gemini_selector_wire_settings_through_budget_wrapper():
    from linkloom.agents.providers.gemini_api import build_gemini_request
    captured = []
    class Inner:
        model_id = "gemini-3.8-flash"
        def complete(self, req):
            captured.append(req)
            return ModelResponse(action=ModelAction.final('{"intent":"CURRENT","slot_id":"launch.cloud","as_of":null}'))
    class Wrapper:
        adapter = Inner()
        def complete(self, req):
            return self.adapter.complete(req)
    ev.question_routing._select(Wrapper(), "Which platform are we using?", [dict(slot_id="launch.cloud",
        subject="Launch", relation="uses cloud provider", values=["Azure"])], {"intent":"CURRENT"}, None)
    wire = build_gemini_request(captured[0], configured_model_id="gemini-3.8-flash")
    assert wire["config"]["thinking_config"] == {"thinking_level":"LOW"}
    assert wire["config"]["max_output_tokens"] == 1024
    assert "temperature" not in wire["config"]


def test_live_gemini_reserves_and_accepts_thinking_plus_json_over_256(tmp_path):
    # Synthetic regression of the reported truncation mechanism, no fresh data.
    adapter = Adapter(usage=ModelUsage(input_tokens=100, output_tokens=40, total_tokens=383))
    meter = live(tmp_path, adapter)
    choice = ev.question_routing._select(meter, "Which platform are we using?", [dict(slot_id="launch.cloud",
        subject="Launch", relation="uses cloud provider", values=["Azure"])], {"intent":"CURRENT"}, None)
    assert choice["slot_id"] == "launch.cloud"
    assert meter.failure is None
    assert len(adapter.requests) == 1
    req = adapter.requests[0]
    assert req.generation_options.max_output_tokens == 1024
    assert req.generation_options.thinking_level == "low"
    row = meter.calls[0]
    assert row["output_bound"] == 1024
    assert row["thinking_tokens"] == 243
    assert row["billable_output_tokens"] == 283
    assert row["selection_valid"] and row["outcome"] == "COMPLETED"
    expected_reservation = (Decimal(row["input_bound"]) * Decimal(".75") + Decimal(1024) * Decimal("3.75")) / 1_000_000
    assert Decimal(str(row["cost_reserved_usd"])) == expected_reservation
    assert meter.charged == (Decimal(100) * Decimal(".75") + Decimal(283) * Decimal("3.75")) / 1_000_000


def test_deepseek_selector_remains_256_without_thinking_setting(tmp_path):
    adapter = Adapter()
    meter = ev.LiveRoutingProvider("deepseek", tmp_path / "deepseek", adapter=adapter)
    ev.question_routing._select(meter, "Which platform are we using?", [dict(slot_id="launch.cloud",
        subject="Launch", relation="uses cloud provider", values=["Azure"])], {"intent":"CURRENT"}, None)
    req = adapter.requests[0]
    assert req.generation_options.max_output_tokens == 256
    assert req.generation_options.thinking_level is None
    assert meter.calls[0]["output_bound"] == 256
