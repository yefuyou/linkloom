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

    monkeypatch.setattr(app, "_validated_source_content", deny)
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


def test_jump_validation_does_not_change_internal_snapshot_or_historical_review_reads(product):
    app, request, workspace, source = product
    path = f"/api/sources/{source['source_id']}"
    _, initial = request(path)
    reference = initial["source"]["evidence"][0]["evidence_ref"]
    app.store.source_registry.unregister_evidence(workspace, reference)
    # Task investigation owns a separate source registry; an internal snapshot
    # remains governed by that service, not the UI's jump-output gate.
    _, snapshots = app.snapshot_investigation_sources(workspace)
    assert snapshots[0]["content"] == TEXT
    assert "evidence_jump" not in snapshots[0]
    status, failure = request(path)
    assert status == 409 and "source" not in failure
    app.repository.delete_source(workspace, source["source_id"])
    historical = app.repository.get_source(workspace, source["source_id"], include_deleted=True, include_content=True)
    assert historical["active"] is False
    assert historical["content"] == TEXT
    assert "evidence_jump" not in historical


@pytest.fixture(params=['real_import', 'demo_seed'])
def graph_jump(request, tmp_path, monkeypatch):
    from graph_evidence_jump_fixture import build_graph_demo, GRAPH_MEETING
    from unified_ask_fixture import request_json, wait_source

    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE', 'bm25')
    database = tmp_path / 'graph.sqlite'
    demo = build_graph_demo(database, seed_graph=request.param == 'demo_seed')
    try:
        if request.param == 'real_import':
            status, body = request_json(demo.base_url, '/api/workspaces', method='POST', data={'name': 'Real import'})
            assert status == 201
            status, body = request_json(demo.base_url, '/api/sources/import', method='POST',
                data={'name': 'meeting.txt', 'content': GRAPH_MEETING})
            assert status == 202
            source = wait_source(demo.base_url, body['source']['source_id'])
            assert source['ingestion_status'] == 'COMPLETE' and source['candidate_count'] == 2
            # Import alone grants no graph authority.
            graph = demo.server.unified_ask.graph_service.store_factory(demo.app._workspace_id())
            assert not graph.load_edges(demo.app._workspace_id()).edges
            status, body = request_json(demo.base_url, '/api/review/candidates')
            assert status == 200 and len(body['candidates']) == 2
            for candidate in body['candidates']:
                status, detail = request_json(demo.base_url, f"/api/review/candidates/{candidate['candidate_id']}")
                assert status == 200
                review = {'reviewer_id': 'Human reviewer', 'reason': 'Checked original relationship and effective date.'}
                if 'valid_from' in detail['candidate']['current_unresolved_fields']:
                    review['valid_from'] = '2026-04-01'
                status, approved = request_json(demo.base_url,
                    f"/api/review/candidates/{candidate['candidate_id']}/approve", method='POST',
                    data=review)
                assert status == 200 and approved['workflow_state'] == 'MATERIALIZED', approved
        else:
            # Seeds must remain readable after an ordinary application restart,
            # without private registry reconstruction or re-approval.
            demo.stop()
            demo = build_graph_demo(database, seed_graph=False)
        yield demo
    finally:
        demo.stop()


def graph_answer(demo):
    from graph_evidence_jump_fixture import GRAPH_QUERY, GRAPH_AS_OF
    from unified_ask_fixture import request_json

    status, body = request_json(demo.base_url, '/api/product/ask', method='POST', data={
        'query': GRAPH_QUERY, 'workspace_id': demo.app._workspace_id(), 'as_of': GRAPH_AS_OF})
    result = body['result']
    assert status == 200 and result['status'] == 'COMPLETED', body
    assert result['decided_by'] == 'graph' and result['reviewer']['accepted'] is True
    assert result['source_investigation']['answer'] == 'Bo'
    assert [fact['predicate'] for fact in result['source_investigation']['facts']] == ['owner_of', 'reports_to']
    assert len(result['evidence']) == 2
    return result


def test_graph_result_quotes_resolve_to_exact_current_source_spans(graph_jump):
    from unified_ask_fixture import request_json

    demo = graph_jump
    result = graph_answer(demo)
    for evidence in result['evidence']:
        status, body = request_json(demo.base_url, f"/api/sources/{evidence['source_id']}")
        assert status == 200, body
        source = body['source']
        view = source['evidence_jump']
        assert view['validated'] is True
        assert view['workspace_id'] == result['workspace_id']
        assert view['source_version'] == source['source_version'] == evidence['source_version']
        assert view['content_sha256'] == evidence['content_sha256']
        matches = [s for s in view['spans'] if s['evidence_ref'] == evidence['evidence_ref']]
        assert len(matches) == 1
        span = matches[0]
        assert span['verification_kind'] == 'SEMANTIC_SPAN'
        assert span['quote'] == evidence['quote']
        assert [span['line_start'], span['line_end']] == [evidence['line_start'], evidence['line_end']]
        assert source['content'][span['char_start']:span['char_end']] == evidence['quote']


@pytest.mark.parametrize('mutation', ['registry_revoked', 'content_hash', 'version', 'removed', 'workspace'])
def test_graph_result_jump_still_fails_closed(graph_jump, mutation):
    from graph_evidence_jump_fixture import GRAPH_MEETING
    from unified_ask_fixture import request_json, wait_source

    demo = graph_jump
    result = graph_answer(demo)
    evidence = result['evidence'][0]
    workspace = result['workspace_id']
    path = f"/api/sources/{evidence['source_id']}"
    assert request_json(demo.base_url, path)[0] == 200
    if mutation == 'registry_revoked':
        demo.app.store.source_registry.unregister_evidence(workspace, evidence['evidence_ref'])
    elif mutation == 'content_hash':
        with demo.app.repository._lock, demo.app.repository._connection:
            demo.app.repository._connection.execute(
                "UPDATE ll_source_version SET content=content || 'tampered' WHERE workspace_id=? AND source_id=?",
                (workspace, evidence['source_id']))
    elif mutation == 'version':
        # The new text still parses and is current, but cannot validate the old graph answer.
        status, body = request_json(demo.base_url, path, method='PUT', data={
            'name': 'new.txt', 'content': GRAPH_MEETING.replace('Bo', 'Cora')})
        assert status == 202
        wait_source(demo.base_url, evidence['source_id'])
    elif mutation == 'removed':
        assert request_json(demo.base_url, path, method='DELETE')[0] == 200
    else:
        assert request_json(demo.base_url, '/api/workspaces', method='POST', data={'name': 'Other'})[0] == 201

    status, body = request_json(demo.base_url, path)
    if mutation == 'version':
        assert status == 200 and body['source']['source_version'] != evidence['source_version']
        assert evidence['evidence_ref'] not in {s['evidence_ref'] for s in body['source']['evidence_jump']['spans']}
    else:
        assert status in (404, 409) and 'source' not in body
        assert evidence['quote'] not in json.dumps(body)
