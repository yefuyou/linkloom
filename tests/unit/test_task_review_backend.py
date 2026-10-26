from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from threading import RLock
from types import SimpleNamespace

import pytest

from linkloom.ui.task_review import TaskReviewConflict, TaskReviewStore


def _statement(*, request_id: str, revision: int = 0, note: str = "checked") -> dict:
    return {
        "workspace_id": "ws_test",
        "run_id": "ui-0123456789ab",
        "step_id": "identity",
        "action": "REVIEW",
        "request_id": request_id,
        "capability": "host-capability",
        "expected_revision": revision,
        "actor_label": "local-reviewer",
        "note": note,
        "outcome": "CHECKED",
        "source_snapshot_hash": "source-hash",
        "source_catalog_hash": "catalog-hash",
        "pair_hash": "pair-hash",
    }


def test_review_sidecar_is_durable_cas_and_idempotent(tmp_path) -> None:
    path = tmp_path / "review.sqlite"
    with TaskReviewStore(path) as store:
        first = store.append(_statement(request_id="req-1"))
        assert first["revision"] == 1
        assert first["status"] == "RECORDED"

        retry = store.append(_statement(request_id="req-1"))
        assert retry == first

        with pytest.raises(TaskReviewConflict, match="REQUEST_ID_COLLISION"):
            store.append(_statement(request_id="req-1", note="changed"))

        with pytest.raises(TaskReviewConflict, match="STALE_REVIEW_REVISION"):
            store.append(_statement(request_id="req-2", revision=0))

    with TaskReviewStore(path) as cold_store:
        history = cold_store.history("ws_test", "ui-0123456789ab")
        assert len(history) == 1
        assert history[0]["request_id"] == "req-1"


def test_review_sidecar_keeps_blocked_continuation_as_audit(tmp_path) -> None:
    with TaskReviewStore(tmp_path / "review.sqlite") as store:
        row = store.append({
            **_statement(request_id="req-blocked"),
            "action": "CONTINUE",
            "outcome": None,
            "status": "BLOCKED",
            "error_code": "RELATION_SIDECAR_UNAVAILABLE",
        })
        assert row["status"] == "BLOCKED"
        assert row["error_code"] == "RELATION_SIDECAR_UNAVAILABLE"
        assert store.history("ws_test", "ui-0123456789ab")[0]["status"] == "BLOCKED"


def test_review_sidecar_serializes_concurrent_revision_cas(tmp_path) -> None:
    path = tmp_path / "review.sqlite"
    with TaskReviewStore(path):
        pass
    barrier = Barrier(2)

    def append(request_id: str):
        with TaskReviewStore(path) as store:
            barrier.wait(timeout=5)
            try:
                return ("ok", store.append(_statement(request_id=request_id)))
            except TaskReviewConflict as error:
                return ("conflict", error.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(append, ("req-a", "req-b")))
    assert [kind for kind, _value in results] == ["conflict", "ok"]
    assert results[0][1] == "STALE_REVIEW_REVISION"
    with TaskReviewStore(path) as store:
        assert store.revision("ws_test", "ui-0123456789ab") == 1


def test_capability_preview_rejects_external_proof_head_change(tmp_path) -> None:
    from linkloom.ui.product import ProductApplicationError
    from linkloom.ui.task_review import TaskReviewService, _safe_hash

    state = {"head": "event-head-before"}
    app = SimpleNamespace(_workspace_id=lambda: "ws_test", _store_lock=RLock())
    service = TaskReviewService.__new__(TaskReviewService)
    service.product_app = app
    service.result_loader = lambda _run, _workspace: ({}, 200)
    service.enabled = True
    service.mode = "LOCAL_HOST_SELF_ASSERTED"
    service.proof_path_provider = lambda _workspace: None
    service._lock = RLock()
    from linkloom.ui.task_review import _CapabilityIssuer
    service._issuer = _CapabilityIssuer()
    service._store = TaskReviewStore(tmp_path / "review.sqlite")
    service._proof_head = lambda _workspace: state["head"]
    step = {"step_id": "identity", "pair_hash": "pair-hash"}
    view = {
        "available": True,
        "workspace_id": "ws_test",
        "run_id": "ui-0123456789ab",
        "source_snapshot_hash": "source-hash",
        "source_catalog_hash": "catalog-hash",
        "revision": 0,
        "steps": [step],
    }
    pair_hash = _safe_hash([{"step_id": "identity", "pair_hash": "pair-hash"}])
    token = service._issuer.issue({
        "workspace_id": "ws_test", "run_id": "ui-0123456789ab",
        "source_snapshot_hash": "source-hash", "source_catalog_hash": "catalog-hash",
        "pair_hash": pair_hash, "revision": 0, "proof_head": state["head"],
    })
    service.get = lambda _workspace, _run: view
    state["head"] = "event-head-after"
    payload = {
        "workspace_id": "ws_test", "run_id": "ui-0123456789ab", "step_id": "identity",
        "action": "REVIEW", "request_id": "req-event-race", "capability": token,
        "expected_revision": 0, "actor_label": "local", "note": "checked", "outcome": "CHECKED",
    }
    try:
        with pytest.raises(ProductApplicationError) as error:
            service.post(payload)
        assert error.value.code == "STALE_PROOF_HEAD"
        assert error.value.status == 409
    finally:
        service._store.close()
