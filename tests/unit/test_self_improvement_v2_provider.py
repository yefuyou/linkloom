from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts.self_improvement import v2_provider


class FakeResponse:
    model_version = "gemini-3.8-flash-001"
    text = '{"ok": true}'

    def __init__(self, *, usage=True):
        self.usage_metadata = (
            SimpleNamespace(
                prompt_token_count=11,
                candidates_token_count=7,
                total_token_count=18,
            )
            if usage
            else None
        )

    def model_dump(self, mode="json"):
        return {
            "model_version": self.model_version,
            "text": self.text,
            "usage_metadata": None if self.usage_metadata is None else {
                "prompt_token_count": self.usage_metadata.prompt_token_count,
                "candidates_token_count": self.usage_metadata.candidates_token_count,
                "total_token_count": self.usage_metadata.total_token_count,
            },
        }


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def generate_content(self, **payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.response


class FakeSDK:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response=response, error=error)
        self.closed = False

    def close(self):
        self.closed = True


def provider(tmp_path, sdk):
    return v2_provider.V2Provider(
        tmp_path,
        experiment_id="exp-offline",
        freeze_sha256="freeze-sha",
        permission=True,
        sdk=sdk,
    )


def test_offline_usage_and_budget_are_accounted_without_credentials(tmp_path):
    sdk = FakeSDK()
    client = provider(tmp_path, sdk)
    try:
        for _ in range(v2_provider.CANARY_REQUESTS):
            client.generate_content(model=v2_provider.MODEL, contents="offline", config={})

        with pytest.raises(RuntimeError, match="V2_CANARY_REQUEST_CAP"):
            client.generate_content(model=v2_provider.MODEL, contents="offline", config={})

        summary = client.summary()
        assert summary["generation_requests"] == v2_provider.CANARY_REQUESTS
        assert summary["known_usage_estimated_usd"] > 0
        assert summary["missing_usage_reserves_usd"] == 0
        assert summary["cost_upper_bound_usd"] < v2_provider.MAX_USD
        assert len(sdk.models.calls) == v2_provider.CANARY_REQUESTS
        assert all(call["config"]["max_output_tokens"] == v2_provider.OUTPUT_BOUND
                   for call in sdk.models.calls)
    finally:
        client.close()
    assert sdk.closed


def _write_canary_marker(experiment_root, client, *, tamper_request_count=False):
    candidate = {
        "id": "candidate",
        "change": "bounded retrieval adjustment",
        "query_mode": "relation_terms",
        "read_mode": "all",
        "limit": 4,
    }
    raw_path = experiment_root / "real" / "canary-case-candidate.envelope.json"
    score_path = experiment_root / "real" / "canary-case-candidate.scored.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps({
        "partition": "dev",
        "candidate": candidate,
        "cases": [{"id": "canary-case", "candidate_id": "candidate"}],
    }), encoding="utf-8")
    score_path.write_text(json.dumps({
        "partition": "dev",
        "candidate": candidate,
        "metrics": {"safety_violations": 0},
        "cases": [{"id": "canary-case", "passed": True, "safety_violation": False}],
    }), encoding="utf-8")
    actual_request_ids = client.summary()["request_ids"][:v2_provider.CANARY_REQUESTS]
    marker = {
        "experiment_id": client.experiment_id,
        "freeze_sha256": client.freeze_sha256,
        "batch_id": client.batch["batch_id"],
        "raw_path": str(raw_path.relative_to(experiment_root)),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "score_path": str(score_path.relative_to(experiment_root)),
        "score_sha256": hashlib.sha256(score_path.read_bytes()).hexdigest(),
        "request_count": len(actual_request_ids),
        "request_ids": actual_request_ids,
    }
    if tamper_request_count:
        marker["request_count"] -= 1
    (client.root / "CANARY_PASS.json").write_text(json.dumps(marker), encoding="utf-8")


def test_tampered_canary_marker_cannot_unlock_after_cap(tmp_path):
    experiment_root = tmp_path / "experiment"
    client = provider(experiment_root / "provider", FakeSDK())
    try:
        for _ in range(v2_provider.CANARY_REQUESTS):
            client.generate_content(model=v2_provider.MODEL, contents="canary", config={})

        _write_canary_marker(experiment_root, client)
        # A valid marker unlocks the next request after the offline canary.
        client.generate_content(model=v2_provider.MODEL, contents="unlocked", config={})
        assert client.summary()["generation_requests"] == v2_provider.CANARY_REQUESTS + 1

        _write_canary_marker(experiment_root, client, tamper_request_count=True)
        with pytest.raises(RuntimeError, match="V2_CANARY_MARKER_INVALID"):
            client.generate_content(model=v2_provider.MODEL, contents="tampered", config={})
        assert len(client.sdk.models.calls) == v2_provider.CANARY_REQUESTS + 1
    finally:
        client.close()


def test_existing_invalid_canary_marker_stops_before_request_cap(tmp_path):
    client = provider(tmp_path / "experiment" / "provider", FakeSDK())
    try:
        (client.root / "CANARY_PASS.json").write_text("{}", encoding="utf-8")
        with pytest.raises(RuntimeError, match="V2_CANARY_MARKER_INVALID"):
            client.generate_content(model=v2_provider.MODEL, contents="blocked", config={})
        assert len(client.sdk.models.calls) == 0
    finally:
        client.close()


def test_reported_usage_with_missing_thinking_tokens_uses_total_without_undercount(tmp_path):
    response = FakeResponse()
    # The SDK omitted thinking_tokens, but total_tokens includes seven extra
    # provider tokens beyond prompt + visible output.
    response.usage_metadata.total_token_count = 25
    client = provider(tmp_path, FakeSDK(response=response))
    try:
        client.generate_content(model=v2_provider.MODEL, contents="hidden", config={})
        summary = client.summary()
    finally:
        client.close()

    expected = (11 * v2_provider.INPUT_RATE
                + max(25 - 11, 7) * v2_provider.OUTPUT_RATE)
    receipt_only = 11 * v2_provider.INPUT_RATE + 7 * v2_provider.OUTPUT_RATE
    assert summary["known_usage_estimated_usd"] == pytest.approx(expected)
    assert summary["known_usage_estimated_usd"] > receipt_only
    assert summary["missing_usage_reserves_usd"] == 0


def test_missing_usage_is_reserved_at_upper_bound_and_case_cannot_replay(tmp_path):
    sdk = FakeSDK(response=FakeResponse(usage=False))
    client = provider(tmp_path, sdk)
    try:
        client.generate_content(model=v2_provider.MODEL, contents="missing", config={})

        summary = client.summary()
        assert summary["generation_requests"] == 1
        assert summary["known_usage_estimated_usd"] == 0
        assert summary["missing_usage_reserves_usd"] > 0
        assert summary["cost_upper_bound_usd"] == summary["missing_usage_reserves_usd"]

        client.sdk = FakeSDK()
        with pytest.raises(RuntimeError, match="V2_UNKNOWN_OUTCOME_NO_REPLAY"):
            client.generate_content(model=v2_provider.MODEL, contents="missing", config={})
        assert len(sdk.models.calls) == 1
        assert len(client.sdk.models.calls) == 0
    finally:
        client.close()


def test_strict_unknown_outcome_stops_replay_even_after_provider_is_replaced(tmp_path):
    failing = FakeSDK(error=httpx.ReadTimeout("synthetic timeout"))
    client = provider(tmp_path, failing)
    try:
        with pytest.raises(Exception):
            client.generate_content(model=v2_provider.MODEL, contents="unknown", config={})
        assert client.summary()["strict_uncertain"] == 1

        client.sdk = FakeSDK()
        with pytest.raises(RuntimeError, match="V2_UNKNOWN_OUTCOME_NO_REPLAY"):
            client.generate_content(model=v2_provider.MODEL, contents="unknown", config={})
        assert len(failing.models.calls) == 1
        assert len(client.sdk.models.calls) == 0
        assert client.summary()["response_integrity_failures"] == 0
    finally:
        client.close()


def test_response_received_but_rejected_stops_all_later_cases(tmp_path):
    rejected = FakeResponse()
    rejected.model_version = "different-model"
    first = FakeSDK(response=rejected)
    client = provider(tmp_path, first)
    try:
        with pytest.raises(Exception, match="MODEL_IDENTITY_MISMATCH"):
            client.generate_content(model=v2_provider.MODEL, contents="identity", config={})

        summary = client.summary()
        assert summary["response_integrity_failures"] == 1
        assert summary["strict_uncertain"] == 0

        client.case_id = "different-case"
        later = FakeSDK()
        client.sdk = later
        with pytest.raises(RuntimeError, match="V2_PROVIDER_RESPONSE_INTEGRITY_STOP"):
            client.generate_content(model=v2_provider.MODEL, contents="later", config={})
        assert len(first.models.calls) == 1
        assert len(later.models.calls) == 0
    finally:
        client.close()


def test_old_ledger_identity_is_rejected(tmp_path):
    client = provider(tmp_path, FakeSDK())
    try:
        folder = tmp_path / "calls" / "old"
        folder.mkdir(parents=True)
        (folder / "provider_ledger.jsonl").write_text(
            json.dumps({"event": "batch_reserved", "batch_id": "v1-old"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="V2_OLD_LEDGER_IDENTITY"):
            client.summary()
    finally:
        client.close()


def test_batch_limits_and_freeze_identity_are_immutable(tmp_path):
    client = provider(tmp_path, FakeSDK())
    client.close()
    batch_path = tmp_path / "BATCH.json"
    batch = json.loads(batch_path.read_text(encoding="utf-8"))

    batch["max_usd"] = 60.0
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(ValueError, match="V2_BATCH_LIMITS_IMMUTABLE"):
        v2_provider.V2Provider(
            tmp_path,
            experiment_id="exp-offline",
            freeze_sha256="freeze-sha",
            permission=True,
            sdk=FakeSDK(),
        )

    batch["max_usd"] = v2_provider.MAX_USD
    batch["freeze_sha256"] = "old-freeze"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(ValueError, match="V2_BATCH_OR_FREEZE_IDENTITY"):
        v2_provider.V2Provider(
            tmp_path,
            experiment_id="exp-offline",
            freeze_sha256="freeze-sha",
            permission=True,
            sdk=FakeSDK(),
        )

    batch["freeze_sha256"] = "freeze-sha"
    batch["synthetic_only"] = 1
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(ValueError, match="V2_BATCH_LIMITS_IMMUTABLE"):
        v2_provider.V2Provider(
            tmp_path,
            experiment_id="exp-offline",
            freeze_sha256="freeze-sha",
            permission=True,
            sdk=FakeSDK(),
        )
