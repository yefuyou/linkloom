"""A pending proof capture never authorizes a different review request."""
import pytest

from tests.integration.test_task_review_recovery import (
    _proof_counts, _request, _review_http, _run_review, _statement,
)
from tests.m3p_support import revision_product


@pytest.mark.parametrize("tamper", ["foreign_capability", "actor", "note", "step", "fresh_actor", "fresh_note", "fresh_step", "fresh_run"])
def test_pending_recovery_preserves_capability_scope_and_exact_request(tmp_path, tamper):
    app, service, _stages, workspace_id, *_ = revision_product(tmp_path, records="none")
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, server):
            result, view = _run_review(base, workspace_id)
            payload = _statement(workspace_id, result, view, action="CONTINUE", step_id="dates", note="Exact pending request.")
            review = server.task_review
            original_append = review._store.append
            failed = {"once": False}

            def fail_once(value):
                if not failed["once"]:
                    failed["once"] = True
                    raise RuntimeError("INTEGRATION_RECEIPT_FAILURE")
                return original_append(value)

            review._store.append = fail_once
            code, pending = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 409 and pending["receipt"] is None
            counts = _proof_counts(tmp_path / "relations.sqlite")
            altered = dict(payload)
            if tamper == "fresh_run":
                another, another_view = _run_review(base, workspace_id, question="请再次核对同一来源中的计划修订与截止日期证据。")
                assert another["agent_run_id"] != result["agent_run_id"]
                altered = _statement(workspace_id, another, another_view, action="CONTINUE", step_id="dates", note=payload["note"])
                altered["request_id"] = payload["request_id"]
            elif tamper.startswith("fresh_"):
                code, refreshed = _request(base, f"/api/product/task-review?workspace_id={workspace_id}&run_id={result['agent_run_id']}")
                assert code == 200 and refreshed["available"]
                altered["capability"] = refreshed["capability"]
                tamper = tamper.removeprefix("fresh_")
            if tamper == "foreign_capability":
                binding = review._issuer.binding(payload["capability"])
                altered["capability"] = review._issuer.issue(dict(binding, workspace_id="ws_foreign"))
            elif tamper == "actor":
                altered["actor_label"] = "another-local-actor"
            elif tamper == "note":
                altered["note"] = "A different statement cannot reuse this audit identity."
            elif tamper != "fresh_run":
                altered["step_id"] = "identity"
            code, refused = _request(base, "/api/product/task-review", method="POST", data=altered)
            assert code in {403, 409} and refused.get("status") != "COMMITTED", refused
            assert review._store.find_request(workspace_id, result["agent_run_id"], payload["request_id"]) is None
            code, recovered = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 200 and recovered["status"] == "COMMITTED", recovered
            assert recovered["receipt"]["actor_label"] == payload["actor_label"]
            assert _proof_counts(tmp_path / "relations.sqlite") == counts
    finally:
        app.close()
