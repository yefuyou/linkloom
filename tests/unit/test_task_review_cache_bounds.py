"""Review memory is bounded and eviction cannot recreate authorization."""
from types import SimpleNamespace

from linkloom.ui import task_review
from linkloom.ui.task_review import TaskReviewService, _CapabilityIssuer


def test_capability_capacity_and_expiry_discard_old_tokens(monkeypatch):
    import time

    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    monkeypatch.setattr(task_review, "_MAX_REVIEW_CAPABILITIES", 2)
    issuer = _CapabilityIssuer(ttl_seconds=5)
    first = issuer.issue({"workspace_id": "ws_a"})
    second = issuer.issue({"workspace_id": "ws_b"})
    third = issuer.issue({"workspace_id": "ws_c"})
    assert issuer.binding(first) is None
    assert issuer.binding(second) == {"workspace_id": "ws_b"}
    assert issuer.binding(third) == {"workspace_id": "ws_c"}
    clock["now"] = 1006.0
    live = issuer.issue({"workspace_id": "ws_live"})
    assert len(issuer._issued) == 1
    assert issuer.binding(second) is None
    assert issuer.binding(live) == {"workspace_id": "ws_live"}


def test_run_snapshot_capacity_is_bounded_and_close_releases_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(task_review, "_MAX_REVIEW_RUN_SNAPSHOTS", 2)
    app = SimpleNamespace(repository=SimpleNamespace(database_path=str(tmp_path / "product.sqlite")))
    service = TaskReviewService(app, lambda *_: ({}, 200), enabled=True)
    try:
        for index in range(3):
            service._remember_run("ws_a", f"ui-{index:012x}", {"quote": str(index)}, 200)
        assert len(service._run_snapshots) == 2
        assert ("ws_a", "ui-000000000000") not in service._run_snapshots
        assert service._run_snapshots[("ws_a", "ui-000000000002")][0]["quote"] == "2"
        token = service._issuer.issue({"workspace_id": "ws_a"})
        assert service._issuer.binding(token)
        service.close()
        assert not service._run_snapshots
        assert service._issuer.binding(token) is None
    finally:
        service.close()
