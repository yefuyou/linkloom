from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from scripts.adaptive_provider import (
    AdaptiveProvider,
    ConcurrentRequestError,
    DeadlineExceeded,
    ProviderOutcomeUnknownError,
    ProviderStopped,
    TrialLimitExceeded,
    LedgerError,
)


class FakeSDK:
    def __init__(self, responses=None, errors=None):
        self.calls = []
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if self.responses:
            return self.responses.pop(0)
        return SimpleNamespace(response_id="response", usage_metadata={})


def response(*, input_tokens=100, output_tokens=20, thinking_tokens=10, response_id="r1"):
    return SimpleNamespace(
        response_id=response_id,
        usage_metadata={
            "prompt_token_count": input_tokens,
            "candidates_token_count": output_tokens,
            "thoughts_token_count": thinking_tokens,
            "total_token_count": input_tokens + output_tokens + thinking_tokens,
        },
    )


def call(provider: AdaptiveProvider, *, case_id="case-1", trial=1, phase="A"):
    with provider.task(case_id, trial, phase):
        return provider.generate_content(
            model="gemini-3.8-flash",
            contents=[{"role": "user", "parts": [{"text": "synthetic"}]}],
            config={},
        )


def test_generate_content_pins_model_and_logs_only_safe_usage(tmp_path: Path):
    sdk = FakeSDK(responses=[response()])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=3, max_usd=1)

    actual = call(provider)

    assert actual.response_id == "r1"
    assert len(sdk.calls) == 1
    request = sdk.calls[0]
    assert request["model"] == "gemini-3.8-flash"
    assert request["config"]["max_output_tokens"] == 8192
    assert request["config"]["automatic_function_calling"] == {"disable": True}
    assert request["config"]["tools"] == []
    summary = provider.summary()
    assert summary["request_count"] == 1
    assert summary["usage_known_count"] == 1
    assert summary["strict_uncertain_count"] == 0
    assert summary["committed_usd"] == pytest.approx((100 * 0.75 + 30 * 3.75) / 1_000_000)

    rows = [json.loads(line) for line in (tmp_path / "adaptive.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "experiment_created",
        "request_reserved",
        "request_finished",
    ]
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "synthetic" not in serialized
    assert '"contents"' not in serialized
    assert '"config"' not in serialized
    assert rows[1]["case_id"] == "case-1"
    assert rows[1]["trial"] == 1
    assert rows[1]["phase"] == "A"
    assert rows[2]["response_id"] == "r1"


def test_runtime_function_declarations_are_allowed_but_hosted_tools_are_not(tmp_path: Path):
    sdk = FakeSDK(responses=[response(response_id="tool-response")])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=3, max_usd=1)
    config = {
        "tools": [{"function_declarations": [
            {"name": "search_notes", "description": "search", "parameters": {"type": "object"}},
            {"name": "read_verified_note", "description": "read", "parameters": {"type": "object"}},
        ]}],
    }
    with provider.task("case-1", 1, "A"):
        provider.generate_content("gemini-3.8-flash", [{"text": "synthetic"}], config)
    assert sdk.calls[0]["config"]["tools"] == config["tools"]

    other = AdaptiveProvider(FakeSDK(), tmp_path / "hosted.jsonl", max_requests=3, max_usd=1)
    with pytest.raises(ValueError, match="TEXT_ONLY"):
        with other.task("case-1", 1, "A"):
            other.generate_content("gemini-3.8-flash", [], {"tools": [{"google_search": {}}]})


def test_unknown_usage_keeps_full_reservation_and_enforces_hard_cost_cap(tmp_path: Path):
    sdk = FakeSDK(responses=[SimpleNamespace(response_id="r1"), SimpleNamespace(response_id="r2")])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=400, max_usd=0.1)

    call(provider)
    assert provider.summary()["committed_usd"] == pytest.approx(provider.reservation_usd)
    with pytest.raises(RuntimeError, match="COST_LIMIT"):
        call(provider, case_id="case-2")
    assert len(sdk.calls) == 1


def test_case_allows_multiple_requests_per_trial_but_rejects_fourth_trial(tmp_path: Path):
    sdk = FakeSDK(responses=[response(response_id=f"r{i}") for i in range(1, 5)])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)

    call(provider, trial=1)
    call(provider, trial=1, phase="B")
    call(provider, trial=2)
    call(provider, trial=3)
    with pytest.raises(TrialLimitExceeded, match="CASE_TRIAL_LIMIT"):
        call(provider, trial=4)
    assert len(sdk.calls) == 4


def test_completed_phase_cannot_be_reopened_but_other_phase_can(tmp_path: Path):
    sdk = FakeSDK(responses=[response(response_id=f"r{i}") for i in range(1, 4)])
    path = tmp_path / "adaptive.jsonl"
    provider = AdaptiveProvider(sdk, path, max_requests=10, max_usd=1)

    call(provider, case_id="case-1", trial=1, phase="A")
    with pytest.raises(TrialLimitExceeded, match="TASK_PHASE_ALREADY_COMPLETED"):
        with provider.task("case-1", 1, "A"):
            pass
    call(provider, case_id="case-1", trial=1, phase="B")
    restarted = AdaptiveProvider(FakeSDK(), path, max_requests=10, max_usd=1)
    with pytest.raises(TrialLimitExceeded, match="TASK_PHASE_ALREADY_COMPLETED"):
        with restarted.task("case-1", 1, "B"):
            pass


def test_task_allows_worker_thread_but_rejects_concurrent_generations(tmp_path: Path):
    started = threading.Event()
    release = threading.Event()

    class BlockingSDK(FakeSDK):
        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            started.set()
            release.wait(timeout=5)
            return response(response_id=f"r{len(self.calls)}")

    sdk = BlockingSDK()
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)
    with provider.task("case-1", 1, "A"):
        first = threading.Thread(target=lambda: provider.generate_content(
            "gemini-3.8-flash", [{"text": "worker"}], {}
        ))
        first.start()
        assert started.wait(timeout=2)
        with pytest.raises(ConcurrentRequestError):
            provider.generate_content("gemini-3.8-flash", [{"text": "second"}], {})
        release.set()
        first.join(timeout=5)
        assert not first.is_alive()
    assert len(sdk.calls) == 1


def test_strict_uncertainty_ends_task_and_stops_after_two_independent_trials(tmp_path: Path):
    sdk = FakeSDK(errors=[TimeoutError("synthetic timeout"), TimeoutError("synthetic timeout")])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)

    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="case-1")
    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="case-1", phase="B")
    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="case-2")
    with pytest.raises(ProviderStopped, match="TWO_STRICT_UNCERTAIN_STOP"):
        call(provider, case_id="case-3")

    summary = provider.summary()
    assert summary["strict_uncertain_count"] == 2
    assert summary["stopped"] is True
    assert len(sdk.calls) == 2


def test_connection_establishment_failure_is_known_and_is_never_retried(tmp_path: Path):
    sdk = FakeSDK(errors=[ConnectionError("synthetic connect failure")])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)

    with pytest.raises(ConnectionError):
        call(provider)

    summary = provider.summary()
    assert summary["strict_uncertain_count"] == 0
    assert summary["last_outcome"] == "known_failure"
    assert len(sdk.calls) == 1


def test_success_resets_strict_streak_but_preserves_uncertain_count(tmp_path: Path):
    sdk = FakeSDK(
        errors=[TimeoutError("synthetic timeout"), None, TimeoutError("synthetic timeout")],
        responses=[response(response_id="success")],
    )
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)

    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="case-1")
    call(provider, case_id="case-2")
    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="case-3")

    summary = provider.summary()
    assert summary["strict_uncertain_count"] == 2
    assert summary["strict_uncertain_streak"] == 1
    assert summary["stopped"] is False


def test_pending_reservation_on_restart_fails_closed_without_rewriting_ledger(tmp_path: Path):
    path = tmp_path / "adaptive.jsonl"
    first = AdaptiveProvider(FakeSDK(), path, max_requests=10, max_usd=1)
    fingerprint = "a" * 64
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "event": "request_reserved",
                    "request_id": "adaptive-provider-000001",
                    "sequence": 1,
                    "case_id": "case-1",
                    "trial": 1,
                    "phase": "A",
                    "request_fingerprint": fingerprint,
                    "reservation_usd": first.reservation_usd,
                    "utc": "2026-10-16T00:00:00+00:00",
                }
            )
            + "\n"
        )
    before = path.read_bytes()

    with pytest.raises(ProviderStopped, match="UNCLOSED_REQUEST_STOP"):
        AdaptiveProvider(FakeSDK(), path, max_requests=10, max_usd=1)

    assert path.read_bytes() == before


def test_finish_ledger_error_stops_instance_before_next_sdk_call(tmp_path: Path, monkeypatch):
    sdk = FakeSDK(responses=[response(response_id="should-not-repeat")])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)
    append = provider._append

    def fail_finish(event, **fields):
        if event == "request_finished":
            raise LedgerError("LEDGER_APPEND_FAILED")
        return append(event, **fields)

    monkeypatch.setattr(provider, "_append", fail_finish)
    with pytest.raises(LedgerError, match="LEDGER_APPEND_FAILED"):
        call(provider)
    assert provider.summary()["stop_reason"] == "LEDGER_ERROR"
    assert len(sdk.calls) == 1
    with pytest.raises(ProviderStopped, match="LEDGER_ERROR"):
        call(provider, case_id="case-2")
    assert len(sdk.calls) == 1


def test_payload_policy_and_deadline_fail_before_sdk_call(tmp_path: Path):
    sdk = FakeSDK(responses=[response()])
    provider = AdaptiveProvider(sdk, tmp_path / "adaptive.jsonl", max_requests=10, max_usd=1)

    with pytest.raises(ValueError, match="MODEL_PINNED"):
        with provider.task("case-1", 1, "A"):
            provider.generate_content(model="other-model", contents=[], config={})
    with pytest.raises(ValueError, match="TEXT_ONLY"):
        with provider.task("case-1", 1, "A"):
            provider.generate_content(
                model="gemini-3.8-flash",
                contents=[{"role": "user", "parts": [{"inline_data": {"data": "x"}}]}],
                config={},
            )
    assert len(sdk.calls) == 0

    deadline_provider = AdaptiveProvider(
        sdk, tmp_path / "deadline.jsonl", max_requests=10, max_usd=1, max_seconds=129
    )
    with pytest.raises(DeadlineExceeded, match="DEADLINE"):
        call(deadline_provider)
    assert len(sdk.calls) == 0


def test_os_lease_rejects_second_provider_while_first_task_is_active(tmp_path: Path):
    path = tmp_path / "adaptive.jsonl"
    first = AdaptiveProvider(FakeSDK(), path, max_requests=10, max_usd=1)
    second = AdaptiveProvider(FakeSDK(), path, max_requests=10, max_usd=1)

    with first.task("case-1", 1, "A"):
        with pytest.raises(ConcurrentRequestError, match="CONCURRENT"):
            with second.task("case-2", 1, "A"):
                pass
