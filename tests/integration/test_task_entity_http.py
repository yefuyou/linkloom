"""The ten formal-task HTTP routes use persisted, offline evidence and no models."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
import json
from threading import Thread
from time import perf_counter
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor
from linkloom.semantic_ingestion.meeting_tasks_v2 import MeetingTaskExtractor
from linkloom.tasks.candidate_source import ProductTaskCandidateSource
from linkloom.tasks.candidate_store import StoredTaskCandidateSource
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.models import ConfidenceMetadata
from linkloom.ui.product import ProductApplication
from linkloom.ui.server import create_http_server
from tests.unit.test_task_legacy_adapter import close, seed, RecordedProvider, TEXT, write_candidate
from tests.unit.test_task_merge import B_CONTENT, B_TEXT, C_CONTENT, C_TEXT
from tests.unit.test_task_rebind import revise


PREFIX = "/api/product"
AUDIT = {"reviewer": "人工审核人", "reason": "人工逐字核对"}


class Backend:
    def context(self):
        return {"schema_version": "offline-task-http"}

    def start(self, query):
        raise AssertionError("formal-task requests must not start Ask")

    def inspect(self, run_id):
        raise AssertionError("formal-task requests must not inspect Ask")


class Client:
    def __init__(self, base, workspace, probes):
        self.base, self.workspace, self.probes = base, workspace, probes
        self.timings = []

    def request(self, path, *, method="GET", data=None, workspace=None, params=None, headers=None):
        query = {"workspace_id": self.workspace if workspace is None else workspace} | (params or {})
        if workspace is False:
            query.pop("workspace_id")
        target = PREFIX + path + ("?" + urlencode(query) if query else "")
        return self.raw(target, method=method, data=data, headers=headers)

    def raw(self, target, *, method="GET", data=None, headers=None):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        request = Request(self.base + target, method=method, data=body,
            headers=({"Content-Type": "application/json"} if body is not None else {}) | (headers or {}))
        self.probes.clear()
        started = perf_counter()
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            status, result = response.status, json.loads(response.read().decode("utf-8"))
        elapsed = perf_counter() - started
        self.timings.append({"method": method, "path": target.split("?", 1)[0], "seconds": elapsed})
        assert elapsed <= 2, (target, elapsed)
        duplicates = {repr(key): count for key, count in Counter(self.probes).items() if count > 1}
        assert not duplicates, (target, "repeated complete verification", duplicates)
        return status, result

    def queued(self):
        status, value = self.request("/task-candidates")
        assert status == 200, value
        return {item["candidate_id"]: item for item in value["items"]}

    def create(self):
        candidate = self.queued()[self.ids["a"]]
        status, value = self.request(f"/task-candidates/{self.ids['a']}/review", method="POST",
            data={"action": "create", "expected_candidate_fingerprint": candidate["candidate_fingerprint"], **AUDIT})
        assert status == 201, value
        return value

    def merge(self, task_id, which="b"):
        candidate = self.queued()[self.ids[which]]
        fields = {"task_id": task_id, "expected_candidate_fingerprint": candidate["candidate_fingerprint"],
                  "apply_fields": ["owner", "deadline"]}
        status, preview = self.request(f"/task-candidates/{self.ids[which]}/merge-preview", method="POST", data=fields)
        assert status == 200, preview
        status, value = self.request(f"/task-candidates/{self.ids[which]}/review", method="POST", data={
            "action": "merge", **fields, "expected_review_revision": preview["review_revision"],
            "expected_preview_token": preview["preview_token"], **AUDIT})
        assert status == 200, value
        return value

    def merged(self):
        return self.merge(self.create()["task"]["id"])


def _instrument(monkeypatch):
    probes = []
    for kind in (ProductTaskCandidateSource, LegacyTaskCandidateSource, StoredTaskCandidateSource):
        original = kind.list_candidates
        def wrapped(instance, connection, workspace, *, _original=original, _kind=kind):
            probes.append((_kind.__name__, workspace))
            return _original(instance, connection, workspace)
        monkeypatch.setattr(kind, "list_candidates", wrapped)
    original = LegacyTaskCandidateSource.version_proof
    def version_proof(instance, connection, workspace, source, version, **options):
        probes.append(("version_proof", workspace, source, version, tuple(options.get("evidence", ())),
                       options.get("require_current", False)))
        return original(instance, connection, workspace, source, version, **options)
    monkeypatch.setattr(LegacyTaskCandidateSource, "version_proof", version_proof)
    import linkloom.tasks.candidate_store as candidate_store
    read_batch = candidate_store._read_batch
    def checked_batch(connection, row, adapter):
        probes.append(("generated_batch", row["workspace_id"], row["id"]))
        return read_batch(connection, row, adapter)
    monkeypatch.setattr(candidate_store, "_read_batch", checked_batch)
    return probes


def _block_models(monkeypatch):
    models = []
    for kind, method in ((GeminiProviderAdapter, "complete"), (DeepSeekProviderAdapter, "complete"),
                         (RecordedProvider, "complete"), (ProviderNeutralSemanticExtractor, "extract"),
                         (MeetingTaskExtractor, "extract")):
        spy = Mock(side_effect=AssertionError("a formal-task HTTP request called a model or extractor"))
        monkeypatch.setattr(kind, method, spy)
        models.append(spy)
    return models

@contextmanager
def running(database_path, probes):
    product = ProductApplication(database_path=database_path, extractor=None)
    server = create_http_server(Backend(), product_app=product, port=0)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield product, server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
        product.close()


@pytest.fixture
def http(tmp_path, monkeypatch):
    imported = seed(tmp_path / "task-http.sqlite")
    _, b = revise(imported, content=B_CONTENT, text=B_TEXT, parts=("联调验收报告",), source_id="meeting-b.txt")
    _, c = revise(imported, content=C_CONTENT, text=C_TEXT, owner="陈宁", deadline="2026年10月20日",
                  parts=("联调终版报告",), source_id="meeting-c.txt")
    probes = _instrument(monkeypatch)
    models = _block_models(monkeypatch)
    try:
        with running(imported.path, probes) as (product, server, base):
            client = Client(base, imported.workspace, probes)
            client.ids = {"a": imported.observation.observation_id, "b": b.task_observation.observation_id,
                          "c": c.task_observation.observation_id}
            client.imported, client.product, client.server = imported, product, server
            yield client
        assert all(model.call_count == 0 for model in models)
    finally:
        close(imported)


def _paths(http, task="missing-task", merge="missing-merge"):
    return [
        ("GET", "/task-candidates", None),
        ("GET", f"/task-candidates/{http.ids['a']}/evidence", None),
        ("GET", "/tasks", None),
        ("GET", f"/tasks/{task}", None),
        ("GET", f"/tasks/{task}/events", None),
        ("POST", f"/task-candidates/{http.ids['b']}/merge-preview", {
            "task_id": task, "expected_candidate_fingerprint": "0" * 64, "apply_fields": ["owner"]}),
        ("POST", f"/task-candidates/{http.ids['a']}/review", {
            "action": "create", "expected_candidate_fingerprint": "0" * 64, **AUDIT}),
        ("POST", f"/tasks/{task}/merges/{merge}/undo-preview", {}),
        ("POST", f"/tasks/{task}/merges/{merge}/undo", {
            "expected_review_revision": "1", "expected_preview_token": "0" * 64, **AUDIT}),
        ("POST", f"/tasks/{task}/status", {"status": "done", "actor": "负责人", "reason": "人工完成"}),
    ]


def test_all_ten_routes_cross_meeting_history_and_undo_under_two_seconds_without_models(http, record_property):
    queued = http.queued()
    a = queued[http.ids["a"]]
    status, evidence = http.request(f"/task-candidates/{http.ids['a']}/evidence",
        params={"candidate_fingerprint": a["candidate_fingerprint"]})
    assert status == 200 and evidence["items"][0]["quote"] == TEXT
    assert evidence["items"][0]["line_start"] == 1
    first = http.create()
    task_id = first["task"]["id"]
    status, tasks = http.request("/tasks", params={"owner": "李明", "status": "open"})
    assert status == 200 and [item["task"]["id"] for item in tasks["items"]] == [task_id]
    status, detail = http.request(f"/tasks/{task_id}")
    assert status == 200 and detail["review_status"] == "approved"
    assert not {"events", "review_history", "merge_history", "original_candidate"} & detail.keys()
    merged = http.merge(task_id)
    assert merged["task"]["owner"] == "王芳" and merged["task"]["deadline"] == "2026-10-16"
    status, history = http.request(f"/tasks/{task_id}/events")
    assert status == 200 and [event["event_type"] for event in history["items"]] == [
        "created", "owner_changed", "deadline_changed"]
    for event in history["items"][1:]:
        assert event["before_evidence"][0]["quote"] == TEXT
        assert event["after_evidence"][0]["quote"] == B_TEXT
        assert event["after_evidence"][0]["line_start"] == 1
        assert event["after_evidence"][0]["meeting_time"] == "2026-10-10T02:00:00+00:00"
        assert len(event["before_evidence"]) + len(event["after_evidence"]) <= 5
        assert event["actor"] == AUDIT["reviewer"]
    status, final_page = http.request(f"/tasks/{task_id}/events", params={"limit": 2, "offset": 2})
    assert status == 200 and [event["id"] for event in final_page["items"]] == [history["items"][2]["id"]]
    assert final_page["page"]["has_more"] is False
    undo_path = f"/tasks/{task_id}/merges/{merged['merge_id']}"
    status, preview = http.request(undo_path + "/undo-preview", method="POST", data={})
    assert status == 200 and preview["restored_values"]["owner"] == "李明"
    status, restored = http.request(undo_path + "/undo", method="POST", data={
        "expected_review_revision": preview["review_revision"], "expected_preview_token": preview["preview_token"], **AUDIT})
    assert status == 200 and restored["task"]["owner"] == "李明"
    assert restored["task"]["deadline"] == "2026-10-12"
    assert http.ids["b"] in http.queued()
    status, changed = http.request(f"/tasks/{task_id}/status", method="POST",
        data={"status": "in_progress", "actor": "李明", "reason": "人工开始执行"})
    assert status == 200 and changed["task"]["status"] == "in_progress"
    c = http.queued()[http.ids["c"]]
    status, rejected = http.request(f"/task-candidates/{http.ids['c']}/review", method="POST", data={
        "action": "reject", "expected_candidate_fingerprint": c["candidate_fingerprint"], **AUDIT})
    assert status == 200 and rejected["review_status"] == "rejected"
    status, processed = http.request("/task-candidates", params={"state": "processed"})
    assert status == 200
    rejection = next(item for item in processed["items"] if item["candidate_id"] == http.ids["c"])
    assert rejection["reviewer"] == AUDIT["reviewer"] and rejection["reason"] == AUDIT["reason"]
    assert http.server.task_api.service.store.replay_task(http.workspace, task_id).to_dict() == changed["task"]
    record_property("task_http_elapsed_seconds", json.dumps(http.timings))


@pytest.mark.parametrize("index", range(10))
def test_each_route_requires_explicit_workspace(http, index):
    method, path, body = _paths(http)[index]
    status, result = http.request(path, method=method, data=body, workspace=False)
    assert status == 400 and result["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("index", range(10))
def test_each_route_rejects_missing_workspace_without_leaking_resources(http, index):
    method, path, body = _paths(http)[index]
    status, result = http.request(path, method=method, data=body, workspace="does-not-exist")
    assert status == 404 and result["error"]["code"] == "WORKSPACE_NOT_FOUND"


@pytest.mark.parametrize("index", range(10))
def test_each_route_isolates_existing_foreign_workspace(http, index):
    merged = http.merged()
    foreign = http.imported.repository.create_workspace("另一个工作区")["workspace_id"]
    method, path, body = _paths(http, merged["task"]["id"], merged["merge_id"])[index]
    params = {"candidate_fingerprint": "0" * 64} if index == 1 else None
    status, result = http.request(path, method=method, data=body, params=params, workspace=foreign)
    if index in (0, 2):
        assert status == 200 and result["items"] == []
    else:
        assert status == 404 and result["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert "李明" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("index", range(10))
def test_each_route_rejects_invalid_parameters(http, index):
    method, path, body = _paths(http)[index]
    params = None
    if index in (0, 2, 4): params = {"limit": 0}
    elif index == 1: params = {"candidate_fingerprint": "invalid"}
    elif index == 3: params = {"unexpected": "x"}
    elif index == 5: body = body | {"apply_fields": [["owner"]]}
    elif index == 6: body = body | {"action": "automatic"}
    elif index == 7: body = {"unexpected": True}
    elif index == 8: body = body | {"expected_review_revision": 1}
    else: body = body | {"status": "automatic"}
    status, result = http.request(path, method=method, data=body, params=params)
    assert status == 400 and result["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("resource", ["evidence", "task", "events", "merge-preview", "review", "undo-preview", "undo", "status"])
def test_resource_routes_reject_nonexistent_ids(http, resource):
    first = http.create()
    task_id = first["task"]["id"]
    paths = {
        "evidence": ("GET", "/task-candidates/missing/evidence", None),
        "task": ("GET", "/tasks/missing", None),
        "events": ("GET", "/tasks/missing/events", None),
        "merge-preview": ("POST", "/task-candidates/missing/merge-preview", {
            "task_id": task_id, "expected_candidate_fingerprint": "0" * 64, "apply_fields": []}),
        "review": ("POST", "/task-candidates/missing/review", {
            "action": "create", "expected_candidate_fingerprint": "0" * 64, **AUDIT}),
        "undo-preview": ("POST", f"/tasks/{task_id}/merges/missing/undo-preview", {}),
        "undo": ("POST", f"/tasks/{task_id}/merges/missing/undo", {
            "expected_review_revision": first["review_revision"], "expected_preview_token": "0" * 64, **AUDIT}),
        "status": ("POST", "/tasks/missing/status", {"status": "done", "actor": "人", "reason": "人工"}),
    }
    method, path, body = paths[resource]
    params = {"candidate_fingerprint": "0" * 64} if resource == "evidence" else None
    status, result = http.request(path, method=method, data=body, params=params)
    assert status == 404 and result["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.parametrize("query", [
    {"limit": 0}, {"limit": 101}, {"limit": -1}, {"limit": "1.1"}, {"limit": "true"},
    {"offset": -1}, {"offset": "1.5"}, {"offset": "false"}, {"offset": 2**63},
])
@pytest.mark.parametrize("endpoint", ["/task-candidates", "/tasks", "/tasks/missing/events"])
def test_list_pagination_rejects_invalid_boundaries(http, endpoint, query):
    status, result = http.request(endpoint, params=query)
    assert status == 400 and result["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("change", ["fields", "status", "merge"])
def test_later_operations_block_both_undo_routes_without_partial_changes(http, change):
    merged = http.merged()
    task_id, merge_id = merged["task"]["id"], merged["merge_id"]
    path = f"/tasks/{task_id}/merges/{merge_id}"
    status, before = http.request(path + "/undo-preview", method="POST", data={})
    assert status == 200
    service = http.server.task_api.service
    if change == "fields":
        service.edit_fields(http.workspace, task_id, patch={"title": "人工修改任务描述"},
            expected_candidate_fingerprint=merged["candidate_fingerprint"],
            expected_review_revision=merged["review_revision"], **AUDIT)
    elif change == "status":
        status, value = http.request(f"/tasks/{task_id}/status", method="POST",
            data={"status": "done", "actor": "负责人", "reason": "人工完成"})
        assert status == 200
    else:
        http.merge(task_id, "c")
    snapshot = service.store.get_task(http.workspace, task_id).to_dict()
    events = service.store.get_events(http.workspace, task_id)
    for suffix, data in (("undo-preview", {}), ("undo", {
        "expected_review_revision": before["review_revision"], "expected_preview_token": before["preview_token"], **AUDIT})):
        status, value = http.request(path + "/" + suffix, method="POST", data=data)
        assert status == 409 and value["error"]["code"] == "UNDO_BLOCKED"
        assert value["error"]["details"]["blocking_count"] > 0
        assert service.store.get_task(http.workspace, task_id).to_dict() == snapshot
        assert service.store.get_events(http.workspace, task_id) == events


def test_merge_chain_can_only_be_undone_in_reverse_order_with_history_retained(http):
    first = http.merged()
    second = http.merge(first["task"]["id"], "c")
    for merged in (second, first):
        path = f"/tasks/{first['task']['id']}/merges/{merged['merge_id']}"
        status, preview = http.request(path + "/undo-preview", method="POST", data={})
        assert status == 200
        status, restored = http.request(path + "/undo", method="POST", data={
            "expected_review_revision": preview["review_revision"], "expected_preview_token": preview["preview_token"], **AUDIT})
        assert status == 200
    assert restored["task"]["owner"] == "李明" and restored["task"]["deadline"] == "2026-10-12"
    status, events = http.request(f"/tasks/{first['task']['id']}/events")
    assert status == 200 and len(events["items"]) == 9
    assert {item["operation"] for item in events["items"]} >= {"merge", "undo_merge"}


def test_stale_merge_preview_and_duplicate_review_do_not_apply_candidate_twice(http):
    first = http.create()
    candidate = http.queued()[http.ids["b"]]
    fields = {"task_id": first["task"]["id"], "expected_candidate_fingerprint": candidate["candidate_fingerprint"],
              "apply_fields": ["owner", "deadline"]}
    status, preview = http.request(f"/task-candidates/{http.ids['b']}/merge-preview", method="POST", data=fields)
    assert status == 200
    http.request(f"/tasks/{first['task']['id']}/status", method="POST",
        data={"status": "in_progress", "actor": "人", "reason": "人工开始"})
    status, value = http.request(f"/task-candidates/{http.ids['b']}/review", method="POST", data={
        "action": "merge", **fields, "expected_review_revision": preview["review_revision"],
        "expected_preview_token": preview["preview_token"], **AUDIT})
    assert status == 409 and value["error"]["code"] in {"PREVIEW_STALE", "REVIEW_CONFLICT"}
    assert http.ids["b"] in http.queued()
    status, duplicate = http.request(f"/task-candidates/{http.ids['a']}/review", method="POST", data={
        "action": "create", "expected_candidate_fingerprint": first["candidate_fingerprint"], **AUDIT})
    assert status == 409 and duplicate["error"]["code"] == "ALREADY_REVIEWED"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_new_routes_reject_wrong_methods_over_http(http, method):
    status, value = http.request("/tasks", method=method, data={})
    assert status == 405 and value["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.parametrize("headers,expected", [
    ({"Origin": "https://untrusted.invalid"}, 403), ({"Host": "untrusted.invalid"}, 421),
    ({"Origin": "http://["}, 403),
])
def test_new_routes_keep_local_host_and_origin_guards(http, headers, expected):
    status, value = http.request("/tasks", headers=headers)
    assert status == expected and value["error"]["code"] in {"INVALID_ORIGIN", "INVALID_HOST"}


def test_new_routes_use_json_body_guard_and_reject_duplicate_query_keys(http):
    status, value = http.request(f"/tasks/missing/status", method="POST", data={}, headers={"Content-Type": "text/plain"})
    assert status == 415 and value["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    status, value = http.raw(PREFIX + "/tasks?workspace_id=" + http.workspace + "&workspace_id=" + http.workspace)
    assert status == 400 and value["error"]["code"] == "INVALID_REQUEST"


def test_task_api_is_unavailable_for_an_unshared_memory_database(tmp_path):
    with running(":memory:", []) as (_product, _server, base):
        client = Client(base, "memory-workspace", [])
        status, result = client.request("/tasks")
        assert status == 503 and result["error"]["code"] == "TASK_API_UNAVAILABLE"
        status, result = client.raw("/api/status")
        assert status == 200

def test_v2_low_multispan_evidence_and_historical_rejection_without_model_calls(tmp_path, monkeypatch):
    from tests.unit.test_task_generated_candidates import seed as generated_seed, close as generated_close
    imported = generated_seed(tmp_path / "generated-http.sqlite")
    identifier = imported.batch.candidates[0].candidate_id
    imported.intake.save(imported.workspace, imported.artifact.artifact_id, imported.artifact.artifact_version_id,
        imported.batch, confidence_by_candidate={identifier: ConfidenceMetadata("LOW", "负责人仍需人工核对")})
    models = _block_models(monkeypatch)
    probes = _instrument(monkeypatch)
    try:
        with running(imported.path, probes) as (_product, _server, base):
            client = Client(base, imported.workspace, probes)
            candidate = client.queued()[identifier]
            assert candidate["candidate"]["confidence"] == "LOW" and candidate["low_confidence"] is True
            assert candidate["candidate"]["confidence_reason"] == "负责人仍需人工核对"
            assert candidate["candidate"]["deadline"] is None
            assert candidate["candidate"]["deadline_text"] == "before the next workshop"
            assert candidate["evidence_count"] == 2
            quotes = []
            for offset in (0, 1):
                status, page = client.request(f"/task-candidates/{identifier}/evidence", params={
                    "candidate_fingerprint": candidate["candidate_fingerprint"], "limit": 1, "offset": offset})
                assert status == 200 and len(page["items"]) == 1
                quotes.extend(page["items"])
                assert page["page"]["has_more"] is (offset == 0)
            assert [quote["line_start"] for quote in quotes] == [1, 3]
            assert [quote["meeting_time"] for quote in quotes] == ["2034-01-02T09:00:00+00:00", "2034-01-02T09:02:00+00:00"]
            assert all(quote["quote"] in imported.artifact.content and quote["quote_sha256"] for quote in quotes)
            status, result = client.request(f"/task-candidates/{identifier}/review", method="POST", data={
                "action": "reject", "expected_candidate_fingerprint": candidate["candidate_fingerprint"], **AUDIT})
            assert status == 200 and result["review_status"] == "rejected"
            imported.repository.delete_source(imported.workspace, imported.artifact.artifact_id)
            status, processed = client.request("/task-candidates", params={"state": "processed"})
            assert status == 200 and len(processed["items"]) == 1
            historical = processed["items"][0]
            assert historical["is_current"] is False and historical["low_confidence"] is True
            assert historical["reviewer"] == AUDIT["reviewer"] and historical["reason"] == AUDIT["reason"]
            status, evidence = client.request(f"/task-candidates/{identifier}/evidence", params={
                "candidate_fingerprint": candidate["candidate_fingerprint"]})
            assert status == 200 and evidence["is_current"] is False and evidence["items"] == quotes
        assert all(model.call_count == 0 for model in models)
    finally:
        generated_close(imported)


def test_confirmation_with_patch_and_reconfirmation_preserves_manual_fields(http):
    raw = http.queued()[http.ids["a"]]
    body = {"action": "create", "expected_candidate_fingerprint": raw["candidate_fingerprint"],
            "patch": {"title": "人工补充的联调任务", "owner": None}, **AUDIT}
    status, first = http.request(f"/task-candidates/{http.ids['a']}/review", method="POST", data=body)
    assert status == 201 and first["task"]["owner"] is None
    assert first["field_sources"]["owner"]["origin"] == "human"
    changed = replace(http.imported.candidate, confidence=replace(http.imported.candidate.confidence, overall=.8))
    write_candidate(http.imported, changed)
    task_id = first["task"]["id"]
    status, pending = http.request(f"/tasks/{task_id}")
    assert status == 200 and pending["review_status"] == "needs_review"
    candidate = http.queued()[http.ids["a"]]
    status, reviewed = http.request(f"/task-candidates/{http.ids['a']}/review", method="POST", data={
        "action": "create", "expected_candidate_fingerprint": candidate["candidate_fingerprint"],
        "expected_review_revision": pending["review_revision"], **AUDIT})
    assert status == 200 and reviewed["operation"] == "reconfirm"
    assert reviewed["task"]["id"] == task_id and reviewed["task"]["owner"] is None
    assert reviewed["task"]["title"] == "人工补充的联调任务"
    assert reviewed["review_status"] == "approved" and reviewed["field_sources"]["owner"]["origin"] == "human"
    status, unassigned = http.request("/tasks", params={"owner": ""})
    assert status == 200 and [item["task"]["id"] for item in unassigned["items"]] == [task_id]
    status, history = http.request(f"/tasks/{task_id}/events")
    assert status == 200 and len(history["items"]) == 1
    assert history["items"][0]["evidence_role"] == "context"
    assert history["items"][0]["after_evidence"][0]["quote"] == TEXT


@pytest.mark.parametrize("path", [
    "/tasks/missing/reviews", "/tasks/missing/merges", "/tasks/missing/events/missing/evidence",
    "/tasks/missing/fields", "/tasks/missing/rebind-preview", "/tasks/missing/rebind",
    "/tasks/missing/invalidation-preview", "/tasks/missing/invalidate", "/tasks/missing/undo-invalidation",
])
def test_deferred_routes_are_not_implemented(http, path):
    method = "GET" if path.endswith(("reviews", "merges", "evidence")) else "POST"
    status, result = http.request(path, method=method, data={} if method == "POST" else None)
    assert status == 404 and result["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_wrong_post_method_is_rejected_before_reading_a_json_body(http):
    status, result = http.request("/tasks", method="POST")
    assert status == 405 and result["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_create_database_failure_rolls_back_without_leaking_sql_errors(http):
    service = http.server.task_api.service
    service.store._connection.execute("""CREATE TRIGGER inject_task_http_failure BEFORE INSERT ON task_event
        BEGIN SELECT RAISE(ABORT,'sensitive database details'); END""")
    candidate = http.queued()[http.ids["a"]]
    status, result = http.request(f"/task-candidates/{http.ids['a']}/review", method="POST", data={
        "action": "create", "expected_candidate_fingerprint": candidate["candidate_fingerprint"], **AUDIT})
    assert status == 500 and result["error"]["code"] == "INTERNAL_ERROR"
    assert "sensitive" not in json.dumps(result)
    assert service.store.list_tasks(http.workspace) == ()
    assert service.store.get_link(http.workspace, http.ids["a"]) is None
    assert service.store._connection.execute("SELECT COUNT(*) FROM task_event").fetchone()[0] == 0
    assert service.store._connection.execute("SELECT COUNT(*) FROM task_review_event").fetchone()[0] == 0
    assert http.ids["a"] in http.queued()

def test_head_rejects_new_route_with_json_headers_and_no_body(http):
    request = Request(http.base + PREFIX + "/tasks?" + urlencode({"workspace_id": http.workspace}), method="HEAD")
    started = perf_counter()
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=3)
    assert caught.value.code == 405
    assert caught.value.headers.get_content_type() == "application/json"
    assert caught.value.read() == b""
    assert perf_counter() - started <= 2


def test_preexisting_unsupported_methods_keep_their_legacy_response(http):
    request = Request(http.base + "/api/product/ask", method="OPTIONS")
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=3)
    assert caught.value.code == 501