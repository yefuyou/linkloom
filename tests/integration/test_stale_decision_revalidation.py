"""Real local HTTP + SQLite acceptance with injected synthetic extraction."""
from __future__ import annotations


import pytest

from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)
from linkloom.ui.product import ProductApplication


OLD = "[2026-10-01T10:00:00Z] Alice: On 2026-10-01 we choose Birchline for Launch."
NEW = "[2026-10-07T10:00:00Z] Alice: On 2026-10-07 we choose Wrenwell for Launch."


def explicit_claim(value, day):
    return {**_claim(value), "temporal_status": "EXPLICIT", "valid_from": f"2026-10-{day}"}


@pytest.fixture
def journey(tmp_path, request):
    new_claim, new_text = getattr(request, "param", (explicit_claim("Wrenwell", "07"), NEW))
    base, server, worker, app = _start_product_server(tmp_path, [explicit_claim("Birchline", "01"), new_claim])
    _, workspace = _client(base, "/api/workspaces", method="POST", data={"name": "Synthetic Revalidation"})
    _, imported = _client(base, "/api/sources/import", method="POST", data={"name": "decision.md", "content": OLD})
    source_id = imported["source"]["source_id"]
    _wait_source(base, source_id)
    _, inbox = _client(base, "/api/review/candidates")
    old_candidate = inbox["candidates"][0]["candidate_id"]
    status, approved = _client(base, f"/api/review/candidates/{old_candidate}/approve", method="POST",
                               data={"reviewer_id": "original-reviewer", "reason": "Original source checked"})
    assert status == 200, approved
    old_id = approved["decision_id"]
    original = app._decision_detail(old_id)
    receipts = app.store.get_decision_receipt_rows(original["workspace_id"], old_id)
    _, _ = _client(base, f"/api/sources/{source_id}", method="PUT", data={"name": "decision.md", "content": new_text})
    _wait_source(base, source_id)
    yield base, app, source_id, old_id, original, receipts, workspace["workspace"]["workspace_id"]
    _stop_product_server(server, worker, app)


def _view(base, old_id):
    status, body = _client(base, f"/api/review/revalidation/{old_id}")
    assert status == 200, body
    return body["revalidation"]


def _approval(view):
    candidate = view["candidates"][0]
    return {"workspace_id": view["workspace_id"], "review_token": view["review_token"],
            "candidate_id": candidate["candidate_id"], "candidate_fingerprint": candidate["candidate_fingerprint"],
            "reviewer_id": "new-reviewer", "reason": "Checked current source against original approval"}


def test_http_revalidation_preserves_history_receipts_and_survives_restart(journey, tmp_path):
    base, app, source_id, old_id, original, receipts, workspace_id = journey
    view = _view(base, old_id)
    assert view["status"] == "UNRESOLVED"
    assert view["original"]["evidence"] == original["evidence"]
    assert view["original_receipts"] == list(receipts)
    assert view["reasons"][0]["code"] == "EVIDENCE_WITHDRAWN_OR_CHANGED"
    assert view["current_sources"][0]["content"] == NEW
    assert len(view["candidates"]) == 1
    assert _view(base, old_id) == view  # Read/reload grants nothing.
    _, attention = _client(base, "/api/review/attention")
    assert attention["decisions"][0]["decision_id"] == old_id
    status, result = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 200, result
    assert result["decision_id"] != old_id
    assert result["receipt_id"] != receipts[0]["receipt_id"]
    _, attention = _client(base, "/api/review/attention")
    assert attention["decisions"] == []
    _, history = _client(base, f"/api/decisions/{result['decision_id']}/history")
    assert len(history["history"]) == 2
    old = next(row for row in history["history"] if row["decision_id"] == old_id)
    assert old["memory_state"] == "STALE"
    for key in ("value", "valid_from", "valid_to", "supersedes_id", "source_episode_id", "evidence"):
        assert old[key] == original[key]
    assert old["revalidated_by"]["receipt_id"] == result["receipt_id"]
    assert app.store.get_decision_receipt_rows(workspace_id, old_id) == receipts
    _, decisions = _client(base, "/api/decisions")
    assert [row["decision_id"] for row in decisions["decisions"]] == [result["decision_id"]]
    reopened = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=None)
    try:
        assert reopened.revalidation.inspect(old_id)["status"] == "RESOLVED"
        assert reopened.revalidation.completed(workspace_id)[old_id]["receipt_id"] == result["receipt_id"]
    finally:
        reopened.close()
    status, _ = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 409  # No duplicate approval.
    # Removal of the replacement still needs attention; old approval is never revived.
    _client(base, f"/api/sources/{source_id}", method="DELETE")
    _, attention = _client(base, "/api/review/attention")
    assert [row["decision_id"] for row in attention["decisions"]] == [result["decision_id"]]


@pytest.mark.parametrize("field,value,code", [
    ("review_token", "old-token", "REVALIDATION_CHANGED"),
    ("candidate_fingerprint", "old-fingerprint", "APPROVAL_STALE"),
    ("candidate_id", "foreign-candidate", "REVALIDATION_CANDIDATE_UNAVAILABLE"),
    ("workspace_id", "foreign-workspace", "WORKSPACE_MISMATCH"),
    ("reviewer_id", "", "INVALID_REQUEST"),
])
def test_bad_approval_does_not_close_attention(journey, field, value, code):
    base, app, _, old_id, _, _, workspace_id = journey
    view = _view(base, old_id)
    payload = _approval(view)
    payload[field] = value
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=payload)
    assert status in (400, 409), body
    if field != "reviewer_id":
        assert body["error"]["code"] == code
    assert _view(base, old_id)["status"] == "UNRESOLVED"
    assert len(app.store.list_decisions(workspace_id)) == 1


def test_removed_source_invalidates_open_review_and_keeps_audit(journey):
    base, _, source_id, old_id, original, _, _ = journey
    view = _view(base, old_id)
    _client(base, f"/api/sources/{source_id}", method="DELETE")
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 409 and body["error"]["code"] == "REVALIDATION_CHANGED"
    removed = _view(base, old_id)
    assert removed["status"] == "UNRESOLVED"
    assert removed["candidates"] == []
    assert removed["reasons"][0]["code"] == "SOURCE_REMOVED"
    assert removed["original"]["evidence"] == original["evidence"]


def test_cross_workspace_reads_and_approval_are_scoped(journey):
    base, _, _, old_id, _, _, _ = journey
    view = _view(base, old_id)
    _client(base, "/api/workspaces", method="POST", data={"name": "Other workspace"})
    status, body = _client(base, f"/api/review/revalidation/{old_id}")
    assert status == 404 and body["error"]["code"] == "DECISION_NOT_FOUND"
    status, _ = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 404


def test_withdrawn_evidence_and_rejected_candidate_stay_unresolved(journey):
    base, _, _, old_id, _, _, _ = journey
    view = _view(base, old_id)
    candidate_id = view["candidates"][0]["candidate_id"]
    _client(base, f"/api/review/candidates/{candidate_id}/reject", method="POST",
            data={"reviewer_id": "reviewer", "reason": "Not sufficient"})
    current = _view(base, old_id)
    assert current["status"] == "UNRESOLVED" and current["candidates"] == []
    status, _ = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 409


def test_missing_source_is_an_actionable_unresolved_projection(journey, monkeypatch):
    base, app, source_id, old_id, original, _, workspace_id = journey
    # Simulate an unavailable catalog record; do not disable SQLite foreign keys.
    get_source = app.repository.get_source
    monkeypatch.setattr(app.repository, "get_source", lambda workspace, source, **kwargs:
                        None if (workspace, source) == (workspace_id, source_id) else get_source(workspace, source, **kwargs))
    view = _view(base, old_id)
    assert view["reasons"][0]["code"] == "SOURCE_MISSING"
    assert view["candidates"] == []
    assert view["original"]["evidence"] == original["evidence"]


def test_generic_approval_cannot_inherit_revalidation_context(journey):
    base, _, _, old_id, _, _, _ = journey
    view = _view(base, old_id)
    candidate_id = view["candidates"][0]["candidate_id"]
    status, _ = _client(base, f"/api/review/candidates/{candidate_id}/approve", method="POST",
                        data={"reviewer_id": "other", "reason": "Ordinary approval"})
    assert status == 200
    assert _view(base, old_id)["status"] == "UNRESOLVED"
    assert _view(base, old_id)["completion"] is None


@pytest.mark.parametrize("journey", [
    ({**explicit_claim("Wrenwell", "07"), "subject": "Other"}, NEW.replace("Launch", "Other")),
    ({**explicit_claim("Wrenwell", "07"), "claim_type": "PROPOSAL"}, NEW),
    ({**explicit_claim("Wrenwell", "07"), "valid_from": "2099-10-07"}, NEW.replace("On 2026-10-07", "On 2099-10-07")),
], indirect=True)
def test_no_eligible_candidate_remains_unresolved(journey):
    base, _, _, old_id, _, _, _ = journey
    view = _view(base, old_id)
    assert view["status"] == "UNRESOLVED" and view["candidates"] == []


def test_restoring_original_source_never_revives_completed_old_approval(journey, tmp_path):
    base, app, source_id, old_id, _, receipts, workspace_id = journey
    view = _view(base, old_id)
    status, approved = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 200
    status, _ = _client(base, f"/api/sources/{source_id}", method="PUT", data={"name": "decision.md", "content": OLD})
    assert status == 202
    _wait_source(base, source_id)
    assert app.store.get_decision(workspace_id, old_id).memory_state.value == "STALE"
    assert app.store.get_decision_receipt_rows(workspace_id, old_id) == receipts
    assert app.store.lookup(workspace_id, "Launch", "uses supplier").records == ()
    reopened = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=None)
    try:
        assert reopened.store.get_decision(workspace_id, old_id).memory_state.value == "STALE"
        assert reopened.revalidation.inspect(old_id)["status"] == "RESOLVED"
    finally:
        reopened.close()

@pytest.mark.parametrize("journey", [(_claim("Wrenwell"), NEW)], indirect=True)
def test_unresolved_effective_time_requires_explicit_source_supported_resolution(journey):
    base, _, _, old_id, _, _, _ = journey
    view = _view(base, old_id)
    assert view["candidates"][0]["current_unresolved_fields"] == ["valid_from"]
    payload = _approval(view)
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=payload)
    assert status == 409 and body["error"]["code"] == "TEMPORAL_UNRESOLVED"
    payload["valid_from"] = "2026-10-07T10:00:00Z"
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=payload)
    assert status == 200, body


def test_failed_materialization_can_resume_only_its_target_bound_approval(journey, monkeypatch):
    from linkloom.semantic_ingestion.materialization import MaterializationBlockedError
    base, app, _, old_id, _, _, workspace_id = journey
    view = _view(base, old_id)
    materialize = app.materializer.materialize
    def fail(*args, **kwargs):
        raise MaterializationBlockedError("TEST_TRANSIENT", "Synthetic outage after approval")
    monkeypatch.setattr(app.materializer, "materialize", fail)
    status, _ = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=_approval(view))
    assert status == 409
    resume = _view(base, old_id)
    assert resume["status"] == "UNRESOLVED"
    assert resume["candidates"][0]["status"] == "APPROVED"
    assert app.revalidation.completed(workspace_id) == {}
    monkeypatch.setattr(app.materializer, "materialize", materialize)
    payload = _approval(resume)
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=payload)
    assert status == 409 and body["error"]["code"] == "REVALIDATION_ALREADY_AUTHORIZED"
    assert resume["candidates"][0]["recorded_approval"]["reviewer_id"] == "new-reviewer"
    payload.pop("reviewer_id")
    payload.pop("reason")
    status, body = _client(base, f"/api/review/revalidation/{old_id}/approve", method="POST", data=payload)
    assert status == 200, body
    approvals = [event for event in app.store.get_semantic_candidate_events(workspace_id, payload["candidate_id"])
                 if event["event_type"] == "HUMAN_APPROVAL"]
    assert len(approvals) == 1 and approvals[0]["actor_id"] == "new-reviewer"
