"""B1-7 offline acceptance: three meetings through the real product HTTP API.

Import → extracted Draft candidates → human confirmation → cross-meeting task
updates as TaskEvents → timeline → Ask current / historical / causal → click
source → execution summary. The scripted Provider replaces only the model; every
Host check, store, review receipt and evidence verification is the real code.
"""
from __future__ import annotations

import json
from copy import deepcopy
from threading import Lock, Thread

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend, _client, _wait_source
from tests.unit.test_product_task_fields import proposal
from tests.unit.test_task_observation_discovery import claim as task_claim

DECIDE_A = "决定银杏项目使用 Halden 作为支付网关供应商。"
TASK_A = "联调报告由李明负责，10/12 前完成。"
DECIDE_B = "决定银杏项目改用 Wrenwell 作为支付网关供应商。"
TASK_B = "联调报告改由王芳负责，延期到 10/16。"
TASK_C = "联调报告仍由王芳负责，延期到 10/20，原因是 Wrenwell 接口文档未就绪。"
MEETINGS = {
    "meeting-a.md": f"[2026-10-01T10:00:00Z] 张伟: {DECIDE_A}\n[2026-10-01T10:05:00Z] 张伟: {TASK_A}\n",
    "meeting-b.md": f"[2026-10-08T10:00:00Z] 张伟: {DECIDE_B}\n[2026-10-08T10:05:00Z] 张伟: {TASK_B}\n",
    "meeting-c.md": f"[2026-10-15T10:00:00Z] 王芳: {TASK_C}\n",
}
FIELDS = {TASK_A: ("李明", "10/12"), TASK_B: ("王芳", "10/16"), TASK_C: ("王芳", "10/20")}
ORDINAL = {TASK_A: 1, TASK_B: 1, TASK_C: 0}  # message position inside its meeting
DECISIONS = {DECIDE_A: "Halden", DECIDE_B: "Wrenwell"}
REVIEWER = {"reviewer": "人工审核人", "reason": "逐字核对原文"}


class MeetingModel:
    """Answers by the exact segment text it receives; never sees other meetings."""

    def __init__(self):
        self.requests = []
        self._lock = Lock()

    def complete(self, request):
        data = json.loads(request.user_input.split("Input:\n", 1)[1]) if "Input:\n" in request.user_input else None
        tool = request.available_tools[0].tool_id
        if tool == "emit_task_field_annotations":
            text = data["task_text"]
            owner, deadline = FIELDS[text]
            task = proposal(text, owner=owner, deadline=deadline, description="负责", title="负责",
                            ordinal=ORDINAL[text])["task"]
            arguments = {key: task[key] for key in ("field_bindings", "title_binding")}
        else:
            text = (data or {}).get("source_segment", {}).get("text") or _segment_text(request.user_input)
            if text in DECISIONS:
                arguments = {"claims": [{
                    "claim_type": "DECISION", "subject": "银杏项目", "relation": "uses supplier",
                    "value": DECISIONS[text], "temporal_status": "SOURCE_EVENT_TIME",
                    "valid_from": None, "valid_to": None,
                    "confidence": dict.fromkeys(("claim_type", "entity", "relation", "temporal", "overall"), .95)}]}
            else:
                arguments = {"claims": [task_claim(text, "负责")]}
        with self._lock:
            self.requests.append(tool)
            number = len(self.requests)
        return ModelResponse(
            action=ModelAction.tool(ToolCall(call_id=f"e2e-{number}", tool_id=tool, arguments=deepcopy(arguments))),
            usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            provider_request_id=f"e2e-request-{number}", provider_response_id=f"e2e-response-{number}")


def _segment_text(prompt: str) -> str:
    for text in (*DECISIONS, *FIELDS):
        if json.dumps(text, ensure_ascii=False)[1:-1] in prompt:
            return text
    raise AssertionError("unexpected extraction prompt")


def _server(tmp_path):
    model = MeetingModel()
    extractor = ProviderNeutralSemanticExtractor(
        model, provider_id="e2e-offline", model_id="e2e-model",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="e2e-guard/v1", reservation_id=f"e2e-{len(model.requests) + 1}",
            estimated_cost_upper_bound_usd=0.0),
        request_guard_id="e2e-guard/v1", max_attempts=1)
    app = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=extractor,
                             relation_resolver=FrozenRelationResolver(("uses supplier",),
                                                                      schema_version="e2e-relations/v1"))
    server = create_http_server(_UnusedRunBackend(), product_app=app, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return f"http://127.0.0.1:{server.server_port}", server, worker, app, model


def test_three_meetings_import_review_evolution_ask_and_source(tmp_path):
    base, server, worker, app, model = _server(tmp_path)
    try:
        status, payload = _client(base, "/api/workspaces", method="POST", data={"name": "银杏"})
        assert status == 201
        workspace = payload["workspace"]["workspace_id"]
        sources = {}
        for name, content in MEETINGS.items():
            status, payload = _client(base, "/api/sources/import", method="POST", data={
                "name": name, "content": content, "request_id": f"e2e-import-{name}", "input_kind": "file"})
            assert status == 202, payload
            sources[name] = _wait_source(base, payload["source"]["source_id"])
            assert sources[name]["ingestion_status"] == "COMPLETE", sources[name]
        calls_after_import = len(model.requests)
        assert calls_after_import == 8  # 5 segments + one field-annotation call per task

        # 1. Extraction only produced Drafts: no formal decision or task exists yet.
        assert _client(base, "/api/decisions")[1]["decisions"] == []
        pending = _client(base, "/api/review/candidates")[1]["candidates"]
        assert sorted(item["proposed_value"] for item in pending if item["claim_type"] == "DECISION") == [
            "Halden", "Wrenwell"]

        # 2. Human confirmation of both decisions. The Chinese wording has no deterministic
        #    effective-time cue, so the reviewer chooses each effective date explicitly.
        for value, effective in (("Halden", "2026-10-01"), ("Wrenwell", "2026-10-08")):
            candidate = next(item for item in pending if item.get("proposed_value") == value)
            path = f"/api/review/candidates/{candidate['candidate_id']}/approve"
            status, blocked = _client(base, path, method="POST",
                                      data={"reviewer_id": "人工审核人", "reason": "逐字核对原文"})
            assert status == 409 and blocked["error"]["code"] == "TEMPORAL_UNRESOLVED"
            status, approved = _client(base, path, method="POST", data={
                "reviewer_id": "人工审核人", "reason": "逐字核对原文", "valid_from": effective})
            assert status == 200 and approved["workflow_state"] == "MATERIALIZED", approved

        # 3. Tasks: create from meeting A, then attach B and C to the same task by human choice.
        query = f"?workspace_id={workspace}"
        tasks = _client(base, f"/api/product/task-candidates{query}&state=pending")[1]["items"]
        by_owner_deadline = {(item["candidate"]["owner"], item["candidate"]["deadline_text"]): item for item in tasks}
        assert set(by_owner_deadline) == {("李明", "10/12"), ("王芳", "10/16"), ("王芳", "10/20")}
        first = by_owner_deadline[("李明", "10/12")]
        status, created = _client(base, f"/api/product/task-candidates/{first['candidate_id']}/review{query}",
                                  method="POST", data={"action": "create",
                                                       "expected_candidate_fingerprint": first["candidate_fingerprint"],
                                                       **REVIEWER})
        assert status == 201, created
        task_id = created["task"]["id"]
        for key, fields in ((("王芳", "10/16"), ["owner", "deadline"]), (("王芳", "10/20"), ["deadline"])):
            item = by_owner_deadline[key]
            status, preview = _client(base, f"/api/product/task-candidates/{item['candidate_id']}/merge-preview{query}",
                                      method="POST", data={"task_id": task_id, "apply_fields": fields,
                                                           "expected_candidate_fingerprint": item["candidate_fingerprint"]})
            assert status == 200 and preview["same_task"] == "human_decision_required", preview
            status, merged = _client(base, f"/api/product/task-candidates/{item['candidate_id']}/review{query}",
                                     method="POST", data={
                                         "action": "merge", "task_id": task_id, "apply_fields": fields,
                                         "expected_candidate_fingerprint": item["candidate_fingerprint"],
                                         "expected_review_revision": preview["review_revision"],
                                         "expected_preview_token": preview["preview_token"], **REVIEWER})
            assert status == 200, merged
        status, task = _client(base, f"/api/product/tasks/{task_id}{query}")
        assert status == 200 and task["task"]["owner"] == "王芳" and task["task"]["deadline_text"] == "10/20"
        assert task["task"]["deadline"] is None, "no year is invented for 10/20"

        # 4. Timeline: each change is its own event with that meeting's quote.
        events = _client(base, f"/api/product/tasks/{task_id}/events{query}")[1]["items"]
        assert [event["event_type"] for event in events] == [
            "created", "owner_changed", "deadline_changed", "deadline_changed"]
        quotes = [event["after_evidence"][0]["quote"] for event in events]
        assert quotes[0] == TASK_A and quotes[1] == quotes[2] == TASK_B and quotes[3] == TASK_C
        status, history = _client(base, f"/api/decisions/{approved['decision_id']}/history")
        assert status == 200 and [item["value"] for item in history["history"]] == ["Halden", "Wrenwell"]

        # 5. Ask: exact current and historical questions are deterministic and cost nothing.
        def ask(text, **extra):
            status, body = _client(base, "/api/product/ask", method="POST", data={
                "workspace_id": workspace, "query": text, "subject": "银杏项目", "relation": "uses supplier", **extra})
            return status, body["result"]
        status, current = ask("银杏项目现在用哪个支付网关？")
        assert status == 200 and current["status"] == "COMPLETED" and current["conclusions"][0]["value"] == "Wrenwell"
        assert current["execution"]["answer_provider_calls"] == 0
        assert current["receipts"][0]["authorization_type"] == "HUMAN_APPROVAL"
        status, earlier = ask("2026-10-05 银杏项目用哪个支付网关？", as_of="2026-10-05")
        assert status == 200 and earlier["conclusions"][0]["value"] == "Halden"
        # A causal question is not routed by wording: without a selector model the deterministic reviewed
        # lookup answers it and no Agent or provider is started.
        status, causal = ask("联调报告延期是不是因为银杏项目换了支付网关供应商？为什么？")
        assert status == 200 and causal["path"] == "DETERMINISTIC" and causal["status"] == "COMPLETED"
        assert causal["conclusions"][0]["value"] == "Wrenwell"
        assert len(model.requests) == calls_after_import

        # 6. Click source: the cited quote is an exact range of meeting B's immutable version.
        (evidence,) = current["evidence"]
        assert evidence["verified"] is True and evidence["quote"] == DECIDE_B
        source = _client(base, f"/api/sources/{evidence['source_id']}")[1]["source"]
        assert source["source_version"] == evidence["source_version"]
        (span,) = [item for item in source["evidence_jump"]["spans"] if item["evidence_ref"] == evidence["evidence_ref"]]
        assert source["content"][span["char_start"]:span["char_end"]] == DECIDE_B

        # 7. A network replay of an import is idempotent; no extraction is repeated.
        status, replay = _client(base, "/api/sources/import", method="POST", data={
            "name": "meeting-a.md", "content": MEETINGS["meeting-a.md"], "request_id": "e2e-import-meeting-a.md",
            "input_kind": "file"})
        assert status == 200 and replay["replayed"] is True
        assert replay["source"]["source_id"] == sources["meeting-a.md"]["source_id"]
        assert len(model.requests) == calls_after_import
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        app.close()
