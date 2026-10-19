"""Focused offline acceptance tests for the durable canary ledger."""

from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path
from threading import Thread
import time

import pytest

from linkloom.agents.providers.canary_ledger import (
    BudgetExceededError,
    CampaignStoppedError,
    CanaryLedger,
    DuplicateRequestError,
    LedgerCorruptError,
    LedgerNotInitializedError,
    ManifestMismatchError,
    PendingReservationError,
    RESERVATION_USD,
)


def _usage(input_tokens: int = 100, output_tokens: int = 20, thinking_tokens: int = 3) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "total_tokens": input_tokens + output_tokens + thinking_tokens,
    }


def _process_reserve_and_complete(root: str, ordinal: int, barrier: object) -> None:
    ledger = CanaryLedger(root, "proc", "manifest")
    barrier.wait()
    try:
        reservation_id = ledger.reserve(
            f"C{ordinal % 3 + 1}",
            "runtime",
            {"request": ordinal},
            {"source": f"snapshot-{ordinal}"},
        )
    except PendingReservationError:
        return
    # Keep the durable reservation live long enough for sibling processes to
    # contend with it; a fresh process must fail closed until it completes.
    time.sleep(3.0)
    ledger.complete(reservation_id, _usage())


def test_initialization_is_once_only_and_campaign_identity_is_bound(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    assert ledger.ledger_path == tmp_path / "campaign" / "ledger.jsonl"
    assert ledger.marker_path.exists()
    assert ledger.snapshot()["status"] == "READY"

    with pytest.raises(ManifestMismatchError):
        CanaryLedger(tmp_path, "campaign", "different-manifest")

    # Re-opening validates the existing campaign; it never resets the ledger
    # or makes a second path for the same campaign.
    reopened = CanaryLedger(tmp_path, "campaign", "manifest")
    assert reopened.ledger_path == ledger.ledger_path


def test_marker_and_ledger_partial_or_corrupt_state_fails_closed(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    ledger.marker_path.unlink()
    with pytest.raises(LedgerNotInitializedError):
        CanaryLedger(tmp_path, "campaign", "manifest")

    restored = CanaryLedger(tmp_path / "other", "campaign", "manifest")
    with restored.ledger_path.open("ab") as stream:
        stream.write(b"not-json\n")
    with pytest.raises(LedgerCorruptError):
        CanaryLedger(restored.root, "campaign", "manifest")


def test_reservations_are_durable_and_identity_cannot_be_replayed(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    reservation_id = ledger.reserve(
        "C1",
        "discovery",
        {"action": "one"},
        {"source_hash": "source-1"},
        metadata={"request_payload_hash": "payload-1", "artifact_hash": "artifact-1"},
    )
    snapshot = ledger.snapshot()
    assert snapshot["pending_request_count"] == 1
    assert snapshot["reserved_cost_usd"] == RESERVATION_USD

    ledger.complete(reservation_id, _usage())
    with pytest.raises(DuplicateRequestError):
        ledger.reserve("C1", "discovery", {"action": "one"}, {"source_hash": "source-1"})
    reopened = CanaryLedger(tmp_path, "campaign", "manifest")
    assert reopened.snapshot()["request_count"] == 1
    assert reopened.snapshot()["pending_request_count"] == 0


def test_stage_and_case_caps_are_shared_across_instances(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    for ordinal in range(2):
        rid = ledger.reserve("C1", "discovery", f"d-{ordinal}", {"n": ordinal})
        ledger.complete(rid, _usage())
    with pytest.raises(BudgetExceededError):
        ledger.reserve("C1", "discovery", "d-2", {"n": 2})

    # A different case still shares the stage total, while another stage has
    # its own counter and can admit work.
    rid = ledger.reserve("C2", "planning", "p-1", {"n": 1})
    ledger.complete(rid, _usage())
    reopened = CanaryLedger(tmp_path, "campaign", "manifest")
    assert reopened.snapshot()["stages"]["discovery"]["count"] == 2
    assert reopened.snapshot()["stages"]["planning"]["count"] == 1


def test_frozen_caps_admit_exact_partition_and_allow_final_completion(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    caps = {
        "discovery": {"C1": 2, "C2": 2, "C3": 3},
        "planning": {"C1": 2, "C2": 2, "C3": 2},
        "semantic": {"C1": 1, "C2": 1, "C3": 1},
        "runtime": {"C1": 6, "C2": 6, "C3": 6},
    }
    ordinal = 0
    for stage, by_case in caps.items():
        for case, limit in by_case.items():
            for _ in range(limit):
                rid = ledger.reserve(case, stage, f"{stage}-{case}-{ordinal}", {"n": ordinal})
                ledger.complete(rid, _usage())
                ordinal += 1
    snapshot = ledger.snapshot()
    assert snapshot["request_count"] == 34
    assert snapshot["cases"]["C1"]["count"] == 11
    assert snapshot["cases"]["C2"]["count"] == 11
    assert snapshot["cases"]["C3"]["count"] == 12
    assert ledger.check_open() is True
    with pytest.raises(BudgetExceededError):
        ledger.reserve("C1", "runtime", "after-cap", {"n": 99})


def test_missing_usage_charges_full_reservation_and_stops_globally(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    reservation_id = ledger.reserve("C1", "semantic", "unknown-1", {"source": 1})
    completed = ledger.complete(reservation_id, None)
    assert completed["usage_status"] == "unknown"
    assert completed["charged_cost_usd"] == RESERVATION_USD
    snapshot = ledger.snapshot()
    assert snapshot["stopped"] is True
    assert snapshot["pending_request_count"] == 0
    assert snapshot["committed_cost_usd"] == RESERVATION_USD
    with pytest.raises(CampaignStoppedError):
        ledger.check_open()


def test_missing_thinking_or_unknown_outcome_preserves_raw_usage_and_stops(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    reservation_id = ledger.reserve("C1", "semantic", "unknown-thinking", {"source": 1})
    raw = {
        "prompt_token_count": 10,
        "candidates_token_count": 2,
        "total_token_count": 12,
        "provider_extra": "retained",
    }
    completed = ledger.complete(reservation_id, raw)
    assert completed["usage_status"] == "unknown"
    assert completed["usage_reason"] == "USAGE_INCOMPLETE"
    assert completed["usage"] == raw
    assert completed["charged_cost_usd"] == RESERVATION_USD

    outcome_ledger = CanaryLedger(tmp_path / "outcome", "campaign", "manifest")
    outcome_id = outcome_ledger.reserve("C1", "semantic", "unknown-outcome", {"source": 2})
    outcome_raw = dict(_usage(), provider_extra="retained")
    outcome = outcome_ledger.complete(outcome_id, outcome_raw, outcome="timeout")
    assert outcome["usage_status"] == "unknown"
    assert outcome["usage_reason"] == "OUTCOME_UNKNOWN"
    assert outcome["usage"] == outcome_raw


def test_pending_reservation_survives_restart_and_recovery_stops_replay(tmp_path: Path) -> None:
    ledger = CanaryLedger(tmp_path, "campaign", "manifest")
    reservation_id = ledger.reserve("C1", "runtime", "pending-1", {"source": 1})
    assert reservation_id in ledger.snapshot()["pending"]

    # A new object represents a crash/restart.  It keeps the reservation in
    # the ledger, marks the campaign stopped, and cannot replay the action.
    restarted = CanaryLedger(tmp_path, "campaign", "manifest")
    snapshot = restarted.snapshot()
    assert snapshot["pending_request_count"] == 1
    assert snapshot["stop_reason"] == "PENDING_RESERVATION"
    with pytest.raises(PendingReservationError):
        restarted.check_open()
    with pytest.raises(CampaignStoppedError):
        restarted.reserve("C1", "runtime", "pending-2", {"source": 2})


def test_concurrent_threads_and_processes_serialize_admission(tmp_path: Path) -> None:
    threaded = CanaryLedger(tmp_path, "threads", "manifest")
    errors: list[BaseException] = []

    def run_thread(ordinal: int) -> None:
        try:
            rid = threaded.reserve(f"C{ordinal % 3 + 1}", "discovery", f"thread-{ordinal}", {"n": ordinal})
            threaded.complete(rid, _usage())
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    workers = [Thread(target=run_thread, args=(ordinal,)) for ordinal in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    assert threaded.snapshot()["request_count"] == 4

    processed = CanaryLedger(tmp_path, "proc", "manifest")
    del processed
    context = get_context("spawn")
    barrier = context.Barrier(4)
    processes = [
        context.Process(target=_process_reserve_and_complete, args=(str(tmp_path), ordinal, barrier))
        for ordinal in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
    assert all(process.exitcode == 0 for process in processes)
    snapshot = CanaryLedger(tmp_path, "proc", "manifest").snapshot()
    assert snapshot["request_count"] == 1
    assert snapshot["stop_reason"] == "PENDING_RESERVATION"


def test_concurrent_contention_cannot_oversubscribe_last_stage_slots(tmp_path):
    ledger = CanaryLedger(tmp_path, 'last-slots', 'manifest')
    admitted, rejected = [], []
    def contend(index):
        try:
            rid = ledger.reserve('C1', 'discovery', f'competing-{index}', {'version': 1})
            ledger.complete(rid, _usage())
            admitted.append(rid)
        except BudgetExceededError as exc:
            rejected.append(exc)
    workers = [Thread(target=contend, args=(i,)) for i in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(admitted) == 2 and len(rejected) == 6
    assert ledger.snapshot()['request_count'] == 2
