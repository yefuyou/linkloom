from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.contract_v3_provider import (
    ContractV3Provider,
    ProviderPermissionRequired,
    V3_MAX_REQUESTS,
    V3_MAX_USD,
    call_provider,
    connect,
)
from scripts.adaptive_provider import (
    BudgetExceeded,
    ProviderOutcomeUnknownError,
    ProviderStopped,
    RESERVATION_USD,
)


class FakeSDK:
    def __init__(self, *, responses=None, errors=None, model_version=None):
        self.calls = []
        self.responses = list(responses or [])
        self.errors = list(errors or [])
        self.models = self
        self.model_version = model_version

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        if self.responses:
            return self.responses.pop(0)
        return SimpleNamespace(response_id="unknown", usage_metadata={})


def response(response_id="ok"):
    return SimpleNamespace(
        response_id=response_id,
        usage_metadata={
            "prompt_token_count": 100,
            "candidates_token_count": 20,
            "thoughts_token_count": 10,
            "total_token_count": 130,
        },
    )


def call(provider: ContractV3Provider, *, case_id: str):
    return call_provider(provider, case_id=case_id)


def test_v3_fixed_policy_and_preflight_are_recorded_without_raw_payload(tmp_path: Path):
    sdk = FakeSDK(responses=[response()], model_version="gemini-3.8-flash")
    provider = ContractV3Provider(sdk, tmp_path / "provider.jsonl", provider_permission=True)

    call(provider, case_id="policy")

    assert provider.summary()["model"] == "gemini-3.8-flash"
    assert provider.summary()["max_requests"] == V3_MAX_REQUESTS == 80
    assert provider.summary()["max_usd"] == V3_MAX_USD == 4.0
    assert provider.summary()["max_seconds"] == 7200
    assert provider.summary()["serial"] is True
    assert provider.summary()["actual_model_version"] == "gemini-3.8-flash"
    assert sdk.calls[0]["model"] == "gemini-3.8-flash"
    assert sdk.calls[0]["config"]["max_output_tokens"] == 8192

    raw = (tmp_path / "provider.jsonl").read_text(encoding="utf-8")
    assert "synthetic" not in raw
    assert "contract_v3_preflight" in raw


def test_two_strict_outcomes_with_success_between_stop_cumulatively(tmp_path: Path):
    sdk = FakeSDK(
        errors=[TimeoutError("one"), None, TimeoutError("two")],
        responses=[response("middle")],
    )
    path = tmp_path / "provider.jsonl"
    provider = ContractV3Provider(sdk, path, provider_permission=True)

    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="uncertain-1")
    call(provider, case_id="success")
    with pytest.raises(ProviderOutcomeUnknownError):
        call(provider, case_id="uncertain-2")

    summary = provider.summary()
    assert summary["strict_uncertain_count"] == 2
    assert summary["strict_uncertain_streak"] == 1
    assert summary["stopped"] is True
    assert summary["stop_reason"] == "TWO_STRICT_UNCERTAIN_STOP"
    assert len(sdk.calls) == 3

    with pytest.raises(ProviderStopped, match="TWO_STRICT_UNCERTAIN_STOP"):
        call(provider, case_id="blocked")
    assert len(sdk.calls) == 3

    with pytest.raises(ProviderStopped, match="TWO_STRICT_UNCERTAIN_STOP"):
        ContractV3Provider(FakeSDK(), path, provider_permission=True)


def test_uncertain_case_trial_is_terminal_after_reopen_and_never_resends(tmp_path: Path):
    path = tmp_path / "provider.jsonl"
    first_sdk = FakeSDK(errors=[TimeoutError("unknown")])
    first = ContractV3Provider(first_sdk, path, provider_permission=True)
    with pytest.raises(ProviderOutcomeUnknownError):
        call(first, case_id="same-task")

    second_sdk = FakeSDK(responses=[response()])
    reopened = ContractV3Provider(second_sdk, path, provider_permission=True)
    with pytest.raises(ProviderOutcomeUnknownError) as rejected:
        with reopened.task("same-task", 1, "B"):
            pass
    assert rejected.value.code == "TASK_PROVIDER_OUTCOME_UNCERTAIN"
    assert second_sdk.calls == []


def test_hard_request_limit_blocks_before_sdk_call(tmp_path: Path):
    sdk = FakeSDK(responses=[response(str(index)) for index in range(V3_MAX_REQUESTS)])
    provider = ContractV3Provider(sdk, tmp_path / "provider.jsonl", provider_permission=True)

    for index in range(V3_MAX_REQUESTS):
        call(provider, case_id=f"request-{index}")
    with pytest.raises(BudgetExceeded, match="REQUEST_LIMIT"):
        call(provider, case_id="request-overflow")

    assert provider.summary()["request_count"] == V3_MAX_REQUESTS
    assert len(sdk.calls) == V3_MAX_REQUESTS


def test_hard_cost_limit_reserves_unknown_usage_and_blocks_without_resend(tmp_path: Path):
    sdk = FakeSDK()
    provider = ContractV3Provider(sdk, tmp_path / "provider.jsonl", provider_permission=True)
    capacity = int(V3_MAX_USD // RESERVATION_USD)

    for index in range(capacity):
        call(provider, case_id=f"unknown-{index}")
    with pytest.raises(BudgetExceeded, match="COST_LIMIT"):
        call(provider, case_id="unknown-overflow")

    summary = provider.summary()
    assert summary["usage_known_count"] == 0
    assert summary["usage_unknown_count"] == capacity
    assert summary["unknown_usage_reserved_usd"] == pytest.approx(capacity * RESERVATION_USD)
    assert len(sdk.calls) == capacity


def test_permission_false_stops_before_sdk_or_ledger_work(tmp_path: Path):
    sdk = FakeSDK()
    with pytest.raises(ProviderPermissionRequired, match="PROVIDER_PERMISSION_REQUIRED"):
        ContractV3Provider(sdk, tmp_path / "provider.jsonl", provider_permission=False)
    assert sdk.calls == []

    built = []

    def forbidden_builder(*args, **kwargs):
        built.append((args, kwargs))
        return sdk

    with pytest.raises(ProviderPermissionRequired):
        connect(provider_permission=False, sdk_factory=forbidden_builder, ledger_path=tmp_path / "connect.jsonl")
    assert built == []
