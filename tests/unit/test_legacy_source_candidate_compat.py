"""Historical SOURCE_INGESTION candidates (pre authorization_id receipts) stay readable."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json

import pytest

from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion import materialization as materialization_module
from linkloom.semantic_ingestion.candidate_models import ExtractionAttempt
from linkloom.semantic_ingestion.candidate_persistence import (
    SourceCandidateIntegrityError,
    load_source_candidate,
    serialize_candidate,
)
from linkloom.semantic_ingestion.materialization import (
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
    _candidate_from_row,
)
from tests.unit.test_semantic_ingestion_materialization import (
    _build_candidates,
    _registry_for_results,
)

NOW = datetime(2026, 10, 8, tzinfo=UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _legacy_serialize(candidate) -> tuple[str, str]:
    """Byte-exact shape written before authorization_id replaced the cost bound."""
    payload = json.loads(serialize_candidate(candidate)[0])
    for attempt in payload["candidate"]["extraction_receipt"]["attempts"]:
        attempt["budget_reservation_id"] = attempt.pop("authorization_id")
        attempt["estimated_cost_upper_bound_usd"] = 0.0125
    encoded = _canonical(payload)
    return encoded, _sha(encoded)


@pytest.fixture
def legacy_store(tmp_path, monkeypatch):
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    registry = _registry_for_results(batches)
    with TemporalDecisionStore(tmp_path / "legacy.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        with monkeypatch.context() as patch:
            patch.setattr(materialization_module, "serialize_source_candidate", _legacy_serialize)
            service.capture(result, expected_workspace_id="ws-alpha", now=NOW)
        yield store, service, result.candidate


def test_fixture_reproduces_legacy_shape(legacy_store) -> None:
    store, _, candidate = legacy_store
    row = store.get_semantic_candidate_row("ws-alpha", candidate.candidate_id)
    attempts = json.loads(row["payload_json"])["candidate"]["extraction_receipt"]["attempts"]
    assert attempts and all(
        "budget_reservation_id" in a and "estimated_cost_upper_bound_usd" in a and "authorization_id" not in a
        for a in attempts
    )
    assert row["candidate_fingerprint"] == _sha(row["payload_json"])
    assert row["payload_json"] != serialize_candidate(candidate)[0]


def test_legacy_row_is_readable_by_every_reader_and_unchanged(legacy_store) -> None:
    store, service, candidate = legacy_store
    before = dict(store.get_semantic_candidate_row("ws-alpha", candidate.candidate_id))
    loaded = service.get_candidate("ws-alpha", candidate.candidate_id)
    assert loaded.candidate_id == candidate.candidate_id
    assert [a.authorization_id for a in loaded.extraction_receipt.attempts] == [
        a.authorization_id for a in candidate.extraction_receipt.attempts
    ]
    assert not hasattr(loaded.extraction_receipt.attempts[0], "estimated_cost_upper_bound_usd")
    assert service.list_pending("ws-alpha")
    view, fingerprint = service._load_candidate("ws-alpha", candidate.candidate_id)
    assert fingerprint == before["candidate_fingerprint"]
    assert view.candidate_fingerprint == before["candidate_fingerprint"]

    service.approve(
        "ws-alpha",
        candidate.candidate_id,
        reviewer_id="reviewer-1",
        expected_candidate_fingerprint=fingerprint,
        reason="Legacy fixture reviewed",
        now=NOW,
    )
    service.materialize("ws-alpha", candidate.candidate_id, now=NOW)

    after = dict(store.get_semantic_candidate_row("ws-alpha", candidate.candidate_id))
    assert after["payload_json"] == before["payload_json"]
    assert after["candidate_fingerprint"] == before["candidate_fingerprint"]
    events = store.get_semantic_candidate_events("ws-alpha", candidate.candidate_id)
    assert events and {e["candidate_fingerprint"] for e in events} == {before["candidate_fingerprint"]}
    receipt = store._connection.execute(
        "SELECT candidate_fingerprint FROM semantic_materialization_receipt WHERE candidate_id = ?",
        (candidate.candidate_id,),
    ).fetchone()
    assert receipt["candidate_fingerprint"] == before["candidate_fingerprint"]


def test_current_format_row_still_reads(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    with TemporalDecisionStore(tmp_path / "current.sqlite", source_registry=_registry_for_results(batches)) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha", now=NOW)
        row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        assert "authorization_id" in row["payload_json"]
        assert service.get_candidate("ws-alpha", result.candidate.candidate_id) == result.candidate
        _, fingerprint = service._load_candidate("ws-alpha", result.candidate.candidate_id)
        assert fingerprint == row["candidate_fingerprint"]


def _legacy_args():
    _, batches, _ = _build_candidates()
    candidate = batches[0][0].candidate
    encoded, fingerprint = _legacy_serialize(candidate)
    return candidate, encoded, fingerprint


def _load(encoded, fingerprint, candidate, **overrides):
    args = dict(
        expected_fingerprint=fingerprint,
        expected_candidate_id=candidate.candidate_id,
        expected_workspace_id=candidate.workspace_id,
        expected_payload_version=json.loads(encoded)["record_version"],
    )
    args.update(overrides)
    return load_source_candidate(encoded, **args)


def test_loader_returns_original_bytes_and_fingerprint_for_legacy() -> None:
    candidate, encoded, fingerprint = _legacy_args()
    loaded, stored, stored_fp = _load(encoded, fingerprint, candidate)
    assert (stored, stored_fp) == (encoded, fingerprint)
    assert loaded.extraction_receipt.attempts[0].authorization_id == "fake-reservation-1"


def test_loader_rejects_tampered_payload_with_original_fingerprint() -> None:
    candidate, encoded, fingerprint = _legacy_args()
    tampered = encoded.replace("Vendor A", "Vendor Z")
    assert tampered != encoded
    with pytest.raises(SourceCandidateIntegrityError):
        _load(tampered, fingerprint, candidate)


def test_loader_rejects_business_change_even_with_recomputed_fingerprint() -> None:
    candidate, encoded, _ = _legacy_args()
    tampered = encoded.replace("Vendor A", "Vendor Z")
    with pytest.raises(SourceCandidateIntegrityError):
        _load(tampered, _sha(tampered), candidate)


def test_loader_rejects_fingerprint_mismatch() -> None:
    candidate, encoded, _ = _legacy_args()
    with pytest.raises(SourceCandidateIntegrityError):
        _load(encoded, "0" * 64, candidate)


def test_loader_rejects_candidate_and_workspace_mismatch() -> None:
    candidate, encoded, fingerprint = _legacy_args()
    with pytest.raises(SourceCandidateIntegrityError):
        _load(encoded, fingerprint, candidate, expected_candidate_id="other")
    with pytest.raises(SourceCandidateIntegrityError):
        _load(encoded, fingerprint, candidate, expected_workspace_id="ws-other")


def test_loader_rejects_unknown_and_mismatched_versions() -> None:
    candidate, encoded, _ = _legacy_args()
    payload = json.loads(encoded)
    payload["record_version"] = "candidate-decision-fact/v99"
    unknown = _canonical(payload)
    with pytest.raises(SourceCandidateIntegrityError):
        _load(unknown, _sha(unknown), candidate, expected_payload_version="candidate-decision-fact/v99")
    with pytest.raises(SourceCandidateIntegrityError):
        _load(encoded, _sha(encoded), candidate, expected_payload_version="candidate-decision-fact/v4")


def _rewrite_attempt(encoded, mutate) -> str:
    payload = json.loads(encoded)
    mutate(payload["candidate"]["extraction_receipt"]["attempts"][0])
    return _canonical(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a.update(authorization_id="x"),  # legacy and current keys together
        lambda a: a.pop("estimated_cost_upper_bound_usd"),  # partial legacy shape
        lambda a: a.update(estimated_cost_upper_bound_usd=-1.0),
    ],
)
def test_loader_rejects_mixed_or_partial_attempt_shapes(mutate) -> None:
    candidate, encoded, _ = _legacy_args()
    bad = _rewrite_attempt(encoded, mutate)
    with pytest.raises(SourceCandidateIntegrityError):
        _load(bad, _sha(bad), candidate)


def test_attempt_from_dict_accepts_only_exact_shapes() -> None:
    candidate, encoded, _ = _legacy_args()
    attempt = json.loads(encoded)["candidate"]["extraction_receipt"]["attempts"][0]
    assert ExtractionAttempt.from_dict(attempt).authorization_id == attempt["budget_reservation_id"]
    partial = deepcopy(attempt)
    partial.pop("estimated_cost_upper_bound_usd")
    with pytest.raises(ValueError):
        ExtractionAttempt.from_dict(partial)


def test_row_reader_rejects_identity_mismatch_for_legacy_row(legacy_store) -> None:
    store, _, candidate = legacy_store
    row = dict(store.get_semantic_candidate_row("ws-alpha", candidate.candidate_id))
    assert _candidate_from_row(row).candidate_id == candidate.candidate_id
    for key, value in (("candidate_id", "other"), ("workspace_id", "ws-other"), ("candidate_fingerprint", "0" * 64)):
        with pytest.raises(MaterializationBlockedError, match="CANDIDATE_INVALID|integrity|does not match"):
            _candidate_from_row({**row, key: value})
