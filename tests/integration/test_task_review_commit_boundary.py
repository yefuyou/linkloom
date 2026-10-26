"""Real loopback failures at the source-proof / review-commit boundary."""
from __future__ import annotations

from datetime import datetime, timezone

from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent
from tests.integration.test_task_review_recovery import (
    _proof_counts, _request, _review_http, _run_review, _statement,
)
from tests.m3p_support import revision_product


def test_committed_audit_failure_remains_blocked_until_repaired(tmp_path):
    (tmp_path / "work" / "investigations").mkdir(parents=True)
    app, service, _stages, workspace_id, *_ = revision_product(tmp_path, records="none")
    proof_path = tmp_path / "relations.sqlite"
    assert not proof_path.exists()
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, server):
            result, view = _run_review(base, workspace_id)
            payload = _statement(workspace_id, result, view, action="CONTINUE", step_id="dates", note="Audit fault boundary.")
            store = server.task_review._store
            original_audit = store.audit
            failure = {"enabled": True}

            def fail_committed_audit(value, *, phase, status, error_code=None, details=None):
                if failure["enabled"] and phase == "COMMITTED":
                    raise RuntimeError("INJECTED_COMMITTED_AUDIT_FAILURE")
                return original_audit(value, phase=phase, status=status, error_code=error_code, details=details)

            store.audit = fail_committed_audit
            code, first = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 409 and first["status"] == "BLOCKED", first
            assert first["receipt"] is not None
            counts = _proof_counts(proof_path)
            # A durable statement alone is not a completed continuation.
            code, still_failed = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 409 and still_failed["status"] == "BLOCKED", still_failed
            fresh_code, fresh_view = _request(
                base,
                f"/api/product/task-review?workspace_id={workspace_id}&run_id={result['agent_run_id']}",
            )
            assert fresh_code == 200 and fresh_view["revision"] == 1
            generic_code, generic_retry = _request(
                base,
                "/api/product/task-review",
                method="POST",
                data=dict(payload, capability=fresh_view["capability"]),
            )
            assert generic_code != 200, generic_retry
            failure["enabled"] = False
            code, repaired = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 200 and repaired["status"] == "COMMITTED", repaired
            assert repaired["receipt"]["sequence"] == first["receipt"]["sequence"]
            assert _proof_counts(proof_path) == counts
            audits = store.audit_for_request(workspace_id, result["agent_run_id"], payload["request_id"])
            assert sum(row["phase"] == "COMMITTED" for row in audits) == 1
            code, replayed = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 200 and replayed["status"] == "COMMITTED", replayed
            assert replayed["receipt"]["sequence"] == first["receipt"]["sequence"]
            assert _proof_counts(proof_path) == counts

            # A later proof lifecycle mutation invalidates the old capability;
            # the same body must not be replayed as a committed receipt.
            with TaskRelationStore(proof_path) as relation_store:
                assessment_id = server.task_review._proof_snapshot(workspace_id)["assessments"][0][0]
                previous = relation_store.last_event(workspace_id, assessment_id)
                relation_store.append_event(RelationEvent.create(
                    workspace_id=workspace_id,
                    assessment_id=assessment_id,
                    event_type="STALE",
                    reason="POST_COMMIT_HEAD_CHANGE",
                    actor="synthetic-proof-lifecycle",
                    previous_event_id=previous.event_id,
                    recorded_at=datetime.now(timezone.utc),
                ))
            code, changed_head = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code != 200 and changed_head.get("status") != "COMMITTED", changed_head
            code, stale_view = _request(
                base,
                f"/api/product/task-review?workspace_id={workspace_id}&run_id={result['agent_run_id']}",
            )
            assert code == 200 and stale_view["available"] is True
            identity = next(step for step in stale_view["steps"] if step["step_id"] == "identity")
            assert identity["proof"]["status"] == "BLOCKED"
            assert identity["source_proof_status"] == "BLOCKED"
    finally:
        app.close()


def test_external_retraction_during_capture_cannot_commit_review(tmp_path):
    (tmp_path / "work" / "investigations").mkdir(parents=True)
    app, service, _stages, workspace_id, *_ = revision_product(tmp_path, records="none")
    proof_path = tmp_path / "relations.sqlite"
    assert not proof_path.exists()
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, server):
            result, view = _run_review(base, workspace_id)
            payload = _statement(workspace_id, result, view, action="CONTINUE", step_id="dates", note="Actual lifecycle race.")
            review = server.task_review
            original_capture = review._capture_supported_proofs

            def capture_then_external_event(*args, **kwargs):
                captured, comparisons = original_capture(*args, **kwargs)
                assessment_id = captured[0]["assessment_id"]
                with TaskRelationStore(proof_path) as relation_store:
                    previous = relation_store.last_event(workspace_id, assessment_id)
                    relation_store.append_event(RelationEvent.create(
                        workspace_id=workspace_id, assessment_id=assessment_id,
                        event_type="RETRACTED", reason="ACCEPTANCE_CONCURRENT_RETRACTION",
                        actor="synthetic-external-lifecycle",
                        previous_event_id=previous.event_id,
                        recorded_at=datetime.now(timezone.utc),
                    ))
                return captured, comparisons

            review._capture_supported_proofs = capture_then_external_event
            code, rejected = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 409 and rejected["status"] == "BLOCKED", rejected
            assert rejected["receipt"] is None
            assert "STALE_PROOF_HEAD" in rejected["blockers"]
            audits = review._store.audit_for_request(workspace_id, result["agent_run_id"], payload["request_id"])
            assert any(row["phase"] == "PENDING" for row in audits)
            assert any(row["phase"] == "FAILED" for row in audits)
            assert not any(row["phase"] == "COMMITTED" for row in audits)
            # The real append-only external lifecycle event is retained.
            with TaskRelationStore(proof_path) as relation_store:
                snapshot = review._proof_snapshot(workspace_id)
                assessment_id = snapshot["assessments"][0][0]
                # The order is canonical, not necessarily the captured identity order.
                assert any(row[1] in {a[0] for a in snapshot["assessments"]} for row in snapshot["events"])
                assert any(relation_store.last_event(workspace_id, a[0]).event_type == "RETRACTED"
                           for a in snapshot["assessments"])
    finally:
        app.close()
