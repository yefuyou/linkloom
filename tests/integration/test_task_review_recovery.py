"""Task-review sidecar recovery and catalog-revocation boundaries.

These cases use the real Product HTTP server and the existing public source
fixture.  The positive case creates no relation proof rows before CONTINUE;
the only model behavior is the already-scripted transport in
``revision_product``.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from linkloom.ui.server import create_http_server
from linkloom.ui.product import ProductApplication
from linkloom.ui.offline_task_demo import build_offline_task_demo
from tests.integration.test_product_application_flow import _UnusedRunBackend, _client
from tests.m3p_support import http_ask, product_case, revision_product


def _request(base: str, path: str, *, method: str = "GET", data: dict | None = None):
    request = Request(
        base + path,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "Origin": base},
    )
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)
    except URLError as error:  # Keep an injected handler failure diagnosable.
        pytest.fail(f"loopback task-review request failed: {error}")


@contextmanager
def _review_http(app, service, *, sidecar_path):
    server = create_http_server(
        _UnusedRunBackend(),
        product_app=app,
        investigation_service=service,
        allow_local_task_review=True,
        task_review_sidecar_path=sidecar_path,
        port=0,
    )
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def _run_review(base: str, workspace_id: str, *, question: str | None = None):
    if question is None:
        result = http_ask(base, workspace_id)
    else:
        code, config = _request(base, "/api/product/ask/config")
        assert code == 200, config
        code, body = _request(
            base,
            "/api/product/ask",
            method="POST",
            data={
                "query": question,
                "workspace_id": workspace_id,
                "investigation_context": {
                    "domain": "tasks",
                    "snapshot_hash": config["investigation_context"]["snapshot_hash"],
                },
            },
        )
        assert code == 202, body
        result = body["result"]
        deadline = monotonic() + 60
        while result["status"] == "INVESTIGATING" and monotonic() < deadline:
            sleep(0.03)
            code, body = _request(
                base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace_id}",
            )
            assert code == 200, body
            result = body["result"]
    assert result["status"] == "PARTIAL", result
    run_id = result["agent_run_id"]
    code, view = _request(
        base,
        f"/api/product/task-review?workspace_id={workspace_id}&run_id={run_id}",
    )
    assert code == 200 and view["available"], view
    return result, view


def _statement(workspace_id: str, result: dict, view: dict, *, action: str, step_id: str, note: str):
    payload = {
        "workspace_id": workspace_id,
        "run_id": result["agent_run_id"],
        "step_id": step_id,
        "action": action,
        "request_id": "req-" + uuid4().hex,
        "capability": view["capability"],
        "expected_revision": view["revision"],
        "actor_label": "local-recovery-test",
        "note": note,
    }
    if action == "REVIEW":
        payload["outcome"] = "CHECKED"
    return payload


def _proof_counts(path):
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "task_relation_assessment",
                "task_revision_comparison",
                "task_relation_event",
            )
        )


def test_empty_product_server_starts_without_review_or_relation_sidecars(tmp_path) -> None:
    app = ProductApplication(database_path=tmp_path / "empty.sqlite", extractor=None)
    try:
        server = create_http_server(_UnusedRunBackend(), product_app=app, port=0)
        try:
            assert server.task_review is not None
            assert not (tmp_path / "empty.sqlite.task-review.sqlite").exists()
            assert not (tmp_path / "empty.sqlite.relations.sqlite").exists()
        finally:
            server.server_close()
    finally:
        app.close()


def test_memory_product_without_explicit_review_sidecar_is_not_writable(tmp_path) -> None:
    app = ProductApplication(database_path=":memory:", extractor=None)
    try:
        server = create_http_server(
            _UnusedRunBackend(), product_app=app, allow_local_task_review=True, port=0
        )
        try:
            assert server.task_review is not None
            assert server.task_review.mode == "DISABLED"
            assert server.task_review.store_path is None
            assert not (tmp_path / "task-review.sqlite").exists()
        finally:
            server.server_close()
    finally:
        app.close()


def test_catalog_revoke_with_unchanged_content_redacts_review(tmp_path) -> None:
    (tmp_path / "work" / "investigations").mkdir(parents=True)
    app, service = build_offline_task_demo(tmp_path / "offline.sqlite")
    workspace_id = app._workspace_id()
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, _server):
            assert service.task_relation_paths[workspace_id] == (tmp_path / "offline.sqlite").with_name(
                "offline.sqlite.relations.sqlite"
            )
            result, view = _run_review(base, workspace_id)
            source = app.snapshot_investigation_sources(workspace_id)[1][0]
            source_before = app.repository.get_source(workspace_id, source["source_id"], include_content=True)
            assert source_before is not None
            quote = view["steps"][0]["subject"]["provenance"]["quote"]
            payload = _statement(
                workspace_id,
                result,
                view,
                action="REVIEW",
                step_id="identity",
                note=quote,
            )
            code, recorded = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 200 and recorded["receipt"]["status"] == "RECORDED", recorded

            evidence_ref = result["evidence"][0]["evidence_ref"]
            with app.repository._lock, app.repository._connection:
                app.repository._connection.execute(
                    "DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?",
                    (workspace_id, evidence_ref),
                )
            source_after = app.repository.get_source(workspace_id, source["source_id"], include_content=True)
            assert source_after is not None
            assert source_after["content"] == source_before["content"]
            assert source_after["content_sha256"] == source_before["content_sha256"]

            path = f"/api/product/task-review?workspace_id={workspace_id}&run_id={result['agent_run_id']}"
            code, stale = _request(base, path)
            encoded = json.dumps(stale, ensure_ascii=False)
            assert code == 200 and stale["available"] is False and stale["steps"] == [], stale
            assert quote not in encoded
            assert all(item.get("note") is None and item.get("actor_label") is None for item in stale["history"])

            code, refused = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 409 and quote not in json.dumps(refused, ensure_ascii=False), refused
    finally:
        app.close()


def test_continue_recovers_after_review_append_failure_without_duplicate_proof(tmp_path) -> None:
    (tmp_path / "work" / "investigations").mkdir(parents=True)
    app, service, _stages, workspace_id, _identity, _revision, _comparison = revision_product(
        tmp_path, records="none"
    )
    # ``revision_product`` supplies this as an explicit trusted Host mapping;
    # the fixture does not create it when records="none".
    proof_path = tmp_path / "relations.sqlite"
    assert not proof_path.exists(), "the positive case must not pre-seed relation proof"
    assert service.task_relation_paths[workspace_id] == proof_path
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, server):
            result, view = _run_review(base, workspace_id)
            dates = next(step for step in view["steps"] if step["step_id"] == "dates")
            assert dates["proof"]["status"] != "CURRENT_CONFIRMED"
            payload = _statement(
                workspace_id,
                result,
                view,
                action="CONTINUE",
                step_id="dates",
                note="Recheck the explicit public source pair only.",
            )

            store = server.task_review._store
            assert store is not None
            original_append = store.append
            injected = {"failed": False}

            def fail_review_receipt_once(journal_payload):
                if not injected["failed"] and journal_payload.get("action") == "CONTINUE":
                    injected["failed"] = True
                    raise RuntimeError("INJECTED_REVIEW_APPEND_FAILURE")
                return original_append(journal_payload)

            store.append = fail_review_receipt_once
            code, first = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert injected["failed"] is True
            assert code == 409, first
            assert first.get("receipt") is None
            assert first.get("status") == "BLOCKED"
            assert any(item.get("phase") == "PENDING" for item in first["review"]["history"])
            assert not any(
                item.get("action") == "CONTINUE" and item.get("status") == "RECORDED"
                for item in first["review"]["history"]
            )
            counts_after_failure = _proof_counts(proof_path)
            assert counts_after_failure[0] >= 2 and counts_after_failure[1] >= 1

            # The original capability and request body are intentionally reused.
            code, recovered = _request(base, "/api/product/task-review", method="POST", data=payload)
            assert code == 200 and recovered["status"] == "COMMITTED", recovered
            assert recovered["receipt"]["revision"] == 1
            assert recovered["receipt"]["request_id"] == payload["request_id"]
            assert _proof_counts(proof_path) == counts_after_failure
            assert any(item.get("phase") == "COMMITTED" for item in recovered["review"]["history"])
    finally:
        app.close()


def test_new_run_projects_only_exact_related_review_history(tmp_path) -> None:
    (tmp_path / "work" / "investigations").mkdir(parents=True)
    app, service, _stages, workspace_id, _identity, _revision, _comparison = revision_product(
        tmp_path, records="none"
    )
    try:
        with _review_http(app, service, sidecar_path=tmp_path / "review.sqlite") as (base, server):
            first_result, first_view = _run_review(base, workspace_id)
            first_step = first_view["steps"][0]
            assert first_step["source_verdict_source"] == "RELATION_ASSESSMENT"
            assert first_step["source_verdict"] in {"SUPPORTED", "UNKNOWN", "CONFLICT", "REFUTED", "NOT_ASSESSABLE"}
            assert first_step["source_proof_status"] == first_step["proof"]["status"]
            first_payload = _statement(
                workspace_id,
                first_result,
                first_view,
                action="REVIEW",
                step_id=first_step["step_id"],
                note="run-one-local-note",
            )
            code, first_receipt = _request(
                base, "/api/product/task-review", method="POST", data=first_payload
            )
            assert code == 200 and first_receipt["receipt"]["run_id"] == first_result["agent_run_id"]

            second_result, second_view = _run_review(base, workspace_id)
            assert second_result["agent_run_id"] != first_result["agent_run_id"]
            related = [
                item
                for item in second_view["history"]
                if item.get("run_id") == first_result["agent_run_id"]
            ]
            assert related and all(item.get("current_run") is False for item in related)
            assert any(
                item.get("run_id") == first_result["agent_run_id"]
                and item.get("note") == "run-one-local-note"
                for item in related
            )

            # A review row with the same source/catalog but a different pair
            # identity is not related history and must stay hidden.
            store = server.task_review._store
            assert store is not None
            store.append({
                "workspace_id": workspace_id,
                "run_id": "ui-0123456789ab",
                "step_id": first_step["step_id"],
                "action": "REVIEW",
                "request_id": "pair-mismatch-history",
                "expected_revision": 0,
                "actor_label": "test-fixture",
                "note": "pair-mismatch-must-stay-hidden",
                "outcome": "CHECKED",
                "source_snapshot_hash": second_view["source_snapshot_hash"],
                "source_catalog_hash": second_view["source_catalog_hash"],
                "pair_hash": "different-pair-identity",
            })
            code, pair_view = _request(
                base,
                f"/api/product/task-review?workspace_id={workspace_id}&run_id={second_result['agent_run_id']}",
            )
            assert code == 200
            assert "pair-mismatch-must-stay-hidden" not in json.dumps(pair_view, ensure_ascii=False)

            foreign = app.repository.create_workspace("Foreign related-history workspace")["workspace_id"]
            app.repository.select_workspace(workspace_id)
            code, foreign_view = _request(
                base,
                f"/api/product/task-review?workspace_id={foreign}&run_id={second_result['agent_run_id']}",
            )
            assert code == 409 and foreign_view["error"]["code"] == "WORKSPACE_MISMATCH"

            source = app.snapshot_investigation_sources(workspace_id)[1][0]
            current = app.repository.get_source(workspace_id, source["source_id"], include_content=True)
            assert current is not None
            changed = app._artifact(workspace_id, source["source_id"], str(current["content"]) + " changed")
            app.repository.update_source(
                workspace_id,
                source["source_id"],
                str(current["name"]),
                changed,
                app._parse_artifact(changed),
            )
            code, stale = _request(
                base,
                f"/api/product/task-review?workspace_id={workspace_id}&run_id={second_result['agent_run_id']}",
            )
            assert code == 200 and stale["available"] is False and stale["history"] == []
            assert "run-one-local-note" not in json.dumps(stale, ensure_ascii=False)
    finally:
        app.close()
