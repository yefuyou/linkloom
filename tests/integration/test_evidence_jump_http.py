"""Read the existing source endpoint through HTTP; no Provider or real-vault access."""
import json
from threading import Thread
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.server import create_http_server


TEXT = ("[2026-10-01T09:00:00Z] 甲: 😀 前面的发言\n"
        "[2026-10-01T10:00:00Z] 乙: 目标原话 <script>x</script>\n"
        "[2026-10-01T11:00:00Z] 乙: 目标原话 <script>x</script>")


@pytest.fixture
def product(tmp_path):
    app = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=None)
    server = create_http_server(None, product_app=app, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, method="GET", data=None, headers=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = Request(base + path, data=body, method=method,
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    try:
        _, payload = request("/api/workspaces", "POST", {"name": "Synthetic"})
        workspace = payload["workspace"]["workspace_id"]
        _, payload = request("/api/sources/import", "POST", {"name": "meeting.txt", "content": TEXT})
        source = payload["source"]
        app._ingestion_workers.wait(monotonic() + 5)
        yield app, request, workspace, source
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        app.close()


def test_source_http_read_returns_exact_current_ids_and_does_not_mutate_review(product):
    app, request, workspace, source = product
    with app.repository._lock:
        before = app.repository._connection.total_changes
    status, payload = request(f"/api/sources/{source['source_id']}")
    assert status == 200
    current = payload["source"]
    view = current["evidence_jump"]
    assert view["validated"] and view["workspace_id"] == workspace
    assert view["source_version"] == source["source_version"]
    assert {span["evidence_ref"] for span in view["spans"] if span["verification_kind"] == "SEMANTIC_SPAN"} == {row["evidence_ref"] for row in current["evidence"]}
    assert len(view["spans"]) == 6
    for span in view["spans"]:
        assert current["content"][span["char_start"]:span["char_end"]] == span["quote"]
    with app.repository._lock:
        assert app.repository._connection.total_changes == before


@pytest.mark.parametrize("mutation", ["registry_revoked", "catalog_revoked", "registry_hash", "registry_version", "content_hash"])
def test_source_http_fails_closed_after_evidence_or_hash_change(product, mutation):
    app, request, workspace, source = product
    path = f"/api/sources/{source['source_id']}"
    _, initial = request(path)
    reference = initial["source"]["evidence"][0]["evidence_ref"]
    if mutation == "registry_revoked":
        app.store.source_registry.unregister_evidence(workspace, reference)
    elif mutation.startswith("registry_"):
        app.store.source_registry.register_evidence(workspace, reference,
            content_hash="0" * 64 if mutation == "registry_hash" else source["content_sha256"],
            source_version="old" if mutation == "registry_version" else source["source_version"])
    else:
        with app.repository._lock, app.repository._connection:
            if mutation == "catalog_revoked":
                app.repository._connection.execute("DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?", (workspace, reference))
            else:
                app.repository._connection.execute("UPDATE ll_source_version SET content=content || 'tampered' WHERE workspace_id=? AND source_id=?", (workspace, source["source_id"]))
    status, failure = request(path)
    assert status == 409
    assert failure["error"]["code"] == "SOURCE_EVIDENCE_UNAVAILABLE"
    assert "source" not in failure
    assert "目标原话" not in json.dumps(failure, ensure_ascii=False)


def test_source_http_only_serves_current_version_and_rejects_other_workspace(product):
    app, request, workspace, source = product
    path = f"/api/sources/{source['source_id']}"
    _, old = request(path)
    status, _ = request(path, "PUT", {"name": "meeting.txt", "content": TEXT.replace("目标原话", "新版原话")})
    assert status == 202
    app._ingestion_workers.wait(monotonic() + 5)
    status, changed = request(path)
    assert status == 200
    assert changed["source"]["evidence_jump"]["source_version"] != old["source"]["evidence_jump"]["source_version"]
    assert "目标原话" not in changed["source"]["content"]
    request("/api/workspaces", "POST", {"name": "Other workspace"})
    status, failure = request(path)
    assert status == 404 and "source" not in failure
    request("/api/workspaces/current", "POST", {"workspace_id": workspace})
    request(path, "DELETE")
    status, failure = request(path)
    assert status == 404 and "source" not in failure


def test_source_http_permission_failure_contains_no_cached_quote(product, monkeypatch):
    app, request, _, source = product
    path = f"/api/sources/{source['source_id']}"
    assert request(path)[0] == 200

    def deny(_source):
        raise ProductApplicationError("SOURCE_ACCESS_DENIED", "Source access denied.", status=403)

    monkeypatch.setattr(app.repository, "validate_source_content", deny)
    status, failure = request(path)
    assert status == 403
    assert failure["error"]["code"] == "SOURCE_ACCESS_DENIED"
    assert "目标原话" not in json.dumps(failure, ensure_ascii=False)
    assert "source" not in failure


def test_real_offline_investigation_existing_runtime_refs_resolve_through_source_http(tmp_path, monkeypatch):
    from unified_investigation_fixture import UnifiedInvestigationFixture, RELATIONS, TWO
    from unified_ask_fixture import request_json, poll_product_ask

    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = UnifiedInvestigationFixture(tmp_path / "runtime.sqlite")
    try:
        fixture.configure_agent()
        fixture.select_workspace(RELATIONS)
        workspace = fixture.app._workspace_id()
        status, pending = request_json(fixture.base_url, "/api/product/ask", method="POST",
            data={"query": TWO, "workspace_id": workspace})
        assert status == 202, pending
        _, completed = poll_product_ask(fixture, pending["result"]["agent_run_id"], workspace)
        result = completed["result"]
        assert result["path"] == "SOURCE_RELATION_INVESTIGATION"
        assert result["reviewer"]["accepted"] is True
        assert result["source_investigation"]["answer"] == "陈宁"
        assert len(result["evidence"]) >= 2
        for evidence in result["evidence"]:
            assert evidence["evidence_ref"].startswith("ev_v2_")
            status, payload = request_json(fixture.base_url, f"/api/sources/{evidence['source_id']}")
            assert status == 200
            source = payload["source"]
            assert source["source_version"] == evidence["source_version"]
            matches = [span for span in source["evidence_jump"]["spans"] if span["evidence_ref"] == evidence["evidence_ref"]]
            assert len(matches) == 1
            span = matches[0]
            assert span["verification_kind"] == "RUNTIME_PASSAGE"
            assert span["quote"] == evidence["quote"]
            assert source["content"][span["char_start"]:span["char_end"]] == evidence["quote"]
    finally:
        fixture.stop()
