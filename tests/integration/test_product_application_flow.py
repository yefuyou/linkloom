from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from linkloom.runtime.errors import ValidationError

from linkloom.agents.model_adapter import (
    FakeModelAdapter, ModelAction, ModelProviderError, ModelResponse, ModelUsage,
)
from linkloom.decision_memory.models import DecisionMemoryState
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import timestamped_message_body
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.server import create_http_server


def _claim(value: str, *, claim_type: str = "DECISION") -> dict[str, object]:
    return {
        "claim_type": claim_type,
        "subject": "Launch",
        "relation": "uses supplier",
        "value": value,
        "temporal_status": "SOURCE_EVENT_TIME" if claim_type == "DECISION" else "NOT_STATED",
        "valid_from": None,
        "valid_to": None,
        "confidence": {
            "claim_type": 0.98,
            "entity": 0.95,
            "relation": 0.94,
            "temporal": 0.91,
            "overall": 0.94,
        },
    }


class _ScriptedProvider:
    def __init__(self, claims: list[dict[str, object]]) -> None:
        self.claims = list(claims)
        self.requests = 0
        self.lock = Lock()

    def complete(self, request):
        with self.lock:
            claim = deepcopy(self.claims.pop(0))
            self.requests += 1
            request_number = self.requests
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall(
                    call_id=f"product-fake-{request_number}",
                    tool_id=request.available_tools[0].tool_id,
                    arguments={"claims": [claim]},
                )
            ),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            provider_request_id=f"product-request-{request_number}",
            provider_response_id=f"product-response-{request_number}",
        )


class _UnusedRunBackend:
    def context(self):
        return {"schema_version": "test", "workspace": {"display_name": "Test"}}

    def start(self, query: str):
        raise AssertionError("the product flow uses /api/ask")

    def inspect(self, run_id: str):
        return None


def _client(base: str, path: str, *, method: str = "GET", data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    request = Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        response = urlopen(request, timeout=3)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _wait_source(base: str, source_id: str) -> dict[str, object]:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        status, payload = _client(base, f"/api/sources/{source_id}")
        assert status == 200
        state = payload["source"]["ingestion_status"]
        if state != "INGESTING":
            return payload["source"]
        sleep(0.01)
    raise AssertionError("source ingestion did not reach a terminal state")


def _start_product_server(tmp_path, claims, *, provider_enabled=True):
    provider = _ScriptedProvider(claims)
    extractor = None
    if provider_enabled:
        extractor = ProviderNeutralSemanticExtractor(
            provider,
            provider_id="product-test-provider",
            model_id="product-test-model",
            request_guard=lambda request: ProviderRequestAuthorization.approve(
                request,
                guard_id="product-test-guard/v1",
                reservation_id=f"product-test-reservation-{provider.requests + 1}",
                estimated_cost_upper_bound_usd=0.001,
            ),
            request_guard_id="product-test-guard/v1",
            max_attempts=1,
        )
    app = ProductApplication(
        database_path=tmp_path / "product.sqlite",
        extractor=extractor,
        relation_resolver=FrozenRelationResolver(
            ("uses supplier",), schema_version="product-test-relations/v1"
        ),
    )
    server = create_http_server(_UnusedRunBackend(), product_app=app, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    return base, server, worker, app


def _stop_product_server(server, worker, app):
    server.shutdown()
    server.server_close()
    worker.join(timeout=2)
    app.close()


@pytest.mark.parametrize(("classification", "expected_status"), [
    ("provider_outcome_uncertain", 503),
    ("provider_failure", 502),
    ("tool_policy_denied", 403),
    ("runtime_failure", 500),
])
def test_agent_run_failure_http_status_is_not_success(
    tmp_path, classification, expected_status,
):
    base, server, worker, app = _start_product_server(tmp_path, [], provider_enabled=False)
    try:
        server.investigation_service = SimpleNamespace(inspect=lambda _run_id: {
            "kind": "error", "run": {"status": "failed"},
            "decision": None, "error": {"classification": classification,
                                        "code": "MODEL_UNKNOWN_FAILURE"},
        })
        status, snapshot = _client(base, "/api/agent/decisions/runs/synthetic-run")
        assert status == expected_status
        assert snapshot["kind"] == "error"
        assert snapshot["decision"] is None
    finally:
        _stop_product_server(server, worker, app)


@pytest.mark.parametrize("fail_at", [1, 3, 4])
def test_temporal_http_provider_unknown_keeps_read_source_without_verified_final(
    tmp_path, monkeypatch, fail_at,
):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    base, server, worker, app = _start_product_server(tmp_path, [_claim("Birchline")])
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
        workspace_id = created["workspace"]["workspace_id"]
        _, imported = _client(base, "/api/sources/import", method="POST", data={
            "name": "decision.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for Launch.",
        })
        _wait_source(base, imported["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
                method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})

        class InterruptedProvider:
            def __init__(self):
                self.calls = 0

            def complete(self, request):
                self.calls += 1
                if self.calls == fail_at:
                    return ModelResponse(error=ModelProviderError(
                        code="MODEL_UNKNOWN_FAILURE", category="unknown_provider_error",
                        message="Synthetic provider outcome unknown.",
                        outcome="unknown_provider_outcome",
                    ))
                if self.calls == 1:
                    source_context = json.loads(request.user_input.rsplit(
                        "team_decision_request=", 1,
                    )[1])["source_context"]
                    return ModelResponse(action=ModelAction.tool(ToolCall(
                        call_id="find-source", tool_id="search_notes",
                        arguments={"query": "Launch Birchline", "limit": 5,
                                   "source_context": source_context},
                    )))
                if self.calls == 2:
                    return ModelResponse(action=ModelAction.tool(ToolCall(
                        call_id="read-source", tool_id="read_verified_note",
                        arguments={"note_ref": request.observation.value[0]["evidence_id"]},
                    )))
                return ModelResponse(action=ModelAction.tool(ToolCall(
                    call_id="check-reviewed-memory", tool_id="search_decision_memory",
                    arguments={"query": "Launch supplier", "limit": 5},
                )))

        formal_before = app.store.list_decisions(workspace_id)
        model = InterruptedProvider()
        server.investigation_service = ProductInvestigationService(
            app, model_factory=lambda _query: model, max_steps=6,
        )
        status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "Which supplier is effective?", "workspace_id": workspace_id,
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
        })
        assert status == 202
        deadline = monotonic() + 30
        snapshot = started
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            status, snapshot = _client(base, f"/api/agent/decisions/runs/{started['run']['id']}")
        assert status == 503, snapshot
        assert snapshot["kind"] == "error"
        assert snapshot["error"]["classification"] == "provider_outcome_uncertain"
        assert snapshot["decision"] is None
        assert "temporal_decision" not in snapshot
        assert len(snapshot["evidence"]) == (0 if fail_at == 1 else 1)
        if fail_at > 1:
            assert snapshot["evidence"][0]["supports"] == ["Read during incomplete investigation"]
        assert model.calls == fail_at  # No automatic resend of the uncertain request.
        # Repeated browser polls inspect the same terminal state; they cannot resend.
        for _ in range(2):
            repeated_status, repeated = _client(base, f"/api/agent/decisions/runs/{started['run']['id']}")
            assert repeated_status == 503
            assert repeated["decision"] is None
        assert model.calls == fail_at
        _, recovered = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
        })
        assert recovered["recovery"]["effective"]["value"] == "Birchline"
        assert app.store.list_decisions(workspace_id) == formal_before
    finally:
        _stop_product_server(server, worker, app)


@pytest.fixture
def product_server(tmp_path):
    base, server, worker, app = _start_product_server(
        tmp_path,
        [
            _claim("Birchline"),
            _claim("Wrenwell", claim_type="PROPOSAL"),
            _claim("Wrenwell"),
            _claim("Wrenwell"),
            _claim("Wrenwell"),
        ],
    )
    try:
        yield base
    finally:
        _stop_product_server(server, worker, app)


@pytest.fixture
def undated_product_server(tmp_path):
    claim = _claim("Birchline")
    claim["temporal_status"] = "NOT_STATED"
    base, server, worker, app = _start_product_server(tmp_path, [claim])
    try:
        yield base
    finally:
        _stop_product_server(server, worker, app)


@pytest.fixture
def unconfigured_product_server(tmp_path):
    base, server, worker, app = _start_product_server(tmp_path, [], provider_enabled=False)
    try:
        yield base
    finally:
        _stop_product_server(server, worker, app)


def test_status_keeps_source_save_available_when_semantic_provider_is_missing(
    unconfigured_product_server,
):
    status, provider = _client(unconfigured_product_server, "/api/status")

    assert status == 200
    assert provider["ready"] is False
    assert "--allow-external-provider" in provider["message"]
    _client(unconfigured_product_server, "/api/workspaces", method="POST", data={"name": "Saved only"})
    content = "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline."
    status, imported = _client(unconfigured_product_server, "/api/sources/import", method="POST",
                               data={"name": "saved.md", "content": content})
    assert status == 202
    source = _wait_source(unconfigured_product_server, imported["source"]["source_id"])
    assert source["ingestion_status"] == "FAILED"
    assert source["error"].startswith("PROVIDER_UNAVAILABLE:")
    assert source["content"] == content
    assert source["candidate_count"] == 0
    assert len(source["evidence"]) == 1
    assert _client(unconfigured_product_server, "/api/review/candidates")[1]["candidates"] == []


def test_review_cannot_map_unknown_relation_but_can_reject_it(tmp_path):
    claim = _claim("Birchline")
    claim.update(relation="unmapped supplier phrase", temporal_status="NOT_STATED")
    base, server, worker, app = _start_product_server(tmp_path, [claim])
    try:
        _client(base, "/api/workspaces", method="POST", data={"name": "Relation review"})
        _, imported = _client(base, "/api/sources/import", method="POST", data={
            "name": "unresolved.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline for Launch.",
        })
        _wait_source(base, imported["source"]["source_id"])
        candidate = _client(base, "/api/review/candidates")[1]["candidates"][0]
        path = f"/api/review/candidates/{candidate['candidate_id']}"
        detail = _client(base, path)[1]["candidate"]
        assert detail["relation"] is None
        assert "relation" in detail["current_unresolved_fields"]
        assert "subject" not in detail["current_unresolved_fields"]
        review = {"reviewer_id": "local-user", "reason": "The relation is not confirmed."}
        status, result = _client(base, path + "/approve", method="POST", data={
            **review, "relation": "uses supplier", "valid_from": "2026-10-01",
        })
        assert status == 409
        assert result["error"]["code"] == "RELATION_UNRESOLVED"
        assert _client(base, path)[1]["candidate"]["status"] == "PENDING_REVIEW"
        assert _client(base, "/api/decisions")[1]["decisions"] == []
        assert _client(base, path + "/reject", method="POST", data=review)[0] == 200
        assert _client(base, path)[1]["candidate"]["status"] == "REJECTED"
        assert _client(base, "/api/decisions")[1]["decisions"] == []
    finally:
        _stop_product_server(server, worker, app)


def test_product_http_flow_imports_reviews_materializes_and_asks_with_history(product_server):
    base = product_server
    status, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
    assert status == 201
    workspace_id = created["workspace"]["workspace_id"]
    assert created["workspace"]["selected"] is True
    status, workspaces = _client(base, "/api/workspaces")
    assert status == 200 and workspaces["workspaces"][0]["workspace_id"] == workspace_id
    assert _client(base, "/api/workspaces/current")[1]["workspace"]["workspace_id"] == workspace_id

    first_text = "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for launch."
    status, imported = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={"name": "meeting-2026-10-01.md", "content": first_text},
    )
    assert status == 202
    assert imported["source"]["ingestion_status"] == "INGESTING"
    first_source = _wait_source(base, imported["source"]["source_id"])
    assert first_source["ingestion_status"] == "COMPLETE"
    assert first_source["source_id"] == imported["source"]["source_id"]
    assert first_source["candidate_count"] == 1
    assert first_source["review_required_count"] == 1

    status, inbox = _client(base, "/api/review/candidates")
    assert status == 200 and len(inbox["candidates"]) == 1
    first_candidate_id = inbox["candidates"][0]["candidate_id"]
    status, detail = _client(base, f"/api/review/candidates/{first_candidate_id}")
    assert status == 200
    assert detail["candidate"]["source"]["name"] == "meeting-2026-10-01.md"
    assert detail["candidate"]["source"]["source_id"] == first_source["source_id"]
    assert detail["candidate"]["evidence"]["quote"] == first_text.split(": ", 1)[1]
    assert detail["candidate"]["evidence"]["line_start"] == 1
    assert detail["candidate"]["valid_from"] == "2026-10-01T10:00:00+00:00"

    status, approved = _client(
        base,
        f"/api/review/candidates/{first_candidate_id}/approve",
        method="POST",
        data={"reviewer_id": "local-user", "reason": "Confirmed by the team"},
    )
    assert status == 200 and approved["workflow_state"] == "MATERIALIZED"
    first_source_row = next(
        item for item in _client(base, "/api/sources")[1]["sources"]
        if item["source_id"] == first_source["source_id"]
    )
    assert first_source_row["review_required_count"] == 0

    replacement_text = (
        "[2026-10-05T10:00:00Z] Bob: Someone proposes Wrenwell as a replacement.\n"
        "[2026-10-07T10:00:00Z] Alice: The team decided Wrenwell replaces Birchline as the launch supplier."
    )
    status, replacement = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={"name": "meeting-2026-10-07.md", "content": replacement_text},
    )
    assert status == 202
    replacement_source = _wait_source(base, replacement["source"]["source_id"])
    assert replacement_source["candidate_count"] == 2

    status, inbox = _client(base, "/api/review/candidates")
    assert status == 200 and len(inbox["candidates"]) == 1
    assert len(inbox["supporting_proposals"]) == 1
    proposal_candidate = inbox["supporting_proposals"][0]
    assert proposal_candidate["claim_type"] == "PROPOSAL"
    assert proposal_candidate["status"] == "SUPPORTING_ONLY"
    assert proposal_candidate["subject"] == "Launch"
    assert proposal_candidate["evidence"]["quote"] == "Someone proposes Wrenwell as a replacement."
    assert [item["value"] for item in _client(base, "/api/decisions")[1]["decisions"]] == ["Birchline"]
    second_candidate = inbox["candidates"][0]
    assert second_candidate["claim_type"] == "DECISION"
    assert second_candidate["proposed_value"] == "Wrenwell"
    status, approved = _client(
        base,
        f"/api/review/candidates/{second_candidate['candidate_id']}/approve",
        method="POST",
        data={"reviewer_id": "local-user", "reason": "Confirmed replacement decision"},
    )
    assert status == 200 and approved["workflow_state"] == "MATERIALIZED"
    replacement_row = next(
        item for item in _client(base, "/api/sources")[1]["sources"]
        if item["source_id"] == replacement_source["source_id"]
    )
    assert replacement_row["review_required_count"] == 0

    status, decisions = _client(base, "/api/decisions")
    assert status == 200
    assert len(decisions["decisions"]) == 1
    current = decisions["decisions"][0]
    assert current["value"] == "Wrenwell"
    assert current["memory_state"] == "ACTIVE"
    assert current["supersedes_id"]
    assert current["evidence"][0]["source_name"] == "meeting-2026-10-07.md"
    assert current["evidence"][0]["quote"] in replacement_text

    status, history = _client(base, f"/api/decisions/{current['decision_id']}/history")
    assert status == 200
    assert [item["value"] for item in history["history"]] == ["Birchline", "Wrenwell"]
    assert history["history"][0]["valid_to"] == history["history"][1]["valid_from"]

    status, answer = _client(
        base,
        "/api/ask",
        method="POST",
        data={"query": "What supplier are we currently using?"},
    )
    assert status == 200
    assert answer["answer"]["answer"]["value"] == "Wrenwell"
    assert answer["answer"]["answer"]["valid_from"] == current["valid_from"]
    assert answer["answer"]["answer"]["replaced"]["value"] == "Birchline"
    assert answer["answer"]["answer"]["evidence"][0]["source_name"] == "meeting-2026-10-07.md"
    assert _client(base, "/api/review/attention")[1]["decisions"] == []

    status, recovered = _client(
        base,
        "/api/decisions/recover",
        method="POST",
        data={"subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-03T00:00:00+00:00"},
    )
    assert status == 200
    assert recovered["recovery"]["status"] == "FOUND"
    assert recovered["recovery"]["effective"]["value"] == "Birchline"
    assert recovered["recovery"]["transitions"] == []

    status, recovered = _client(
        base,
        "/api/decisions/recover",
        method="POST",
        data={"subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-08T00:00:00+00:00"},
    )
    timeline = recovered["recovery"]
    assert status == 200 and timeline["status"] == "FOUND"
    assert timeline["effective"]["value"] == "Wrenwell"
    assert timeline["transitions"][0]["from_value"] == "Birchline"
    assert timeline["transitions"][0]["to_value"] == "Wrenwell"
    assert timeline["transitions"][0]["reason_status"] == "NOT_SOURCE_VERIFIED"
    assert timeline["effective"]["authorization"]["authorization_type"] == "HUMAN_APPROVAL"
    assert timeline["effective"]["evidence"][0]["verified"] is True
    assert [item["value"] for item in timeline["proposals"]] == ["Wrenwell"]
    assert timeline["as_of_semantics"] == "valid_time_using_currently_recorded_authorizations"
    assert timeline["transitions"][0]["reason_status"] == "NOT_SOURCE_VERIFIED"
    assert answer["answer"]["answer"]["timeline"]["effective"]["value"] == "Wrenwell"

    _client(base, "/api/workspaces", method="POST", data={"name": "Other workspace"})
    _, isolated = _client(base, "/api/decisions/recover", method="POST", data={
        "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-08"})
    assert isolated["recovery"]["effective"] is None
    assert isolated["recovery"]["history"] == []


def test_recovery_keeps_later_unapproved_proposal_out_of_current_answer(tmp_path):
    base, server, worker, app = _start_product_server(
        tmp_path, [_claim("Birchline"), _claim("Wrenwell"), _claim("Cedar", claim_type="PROPOSAL")]
    )
    try:
        _client(base, "/api/workspaces", method="POST", data={"name": "Northstar"})
        for day, value, claim_kind in (("01", "Birchline", "decision"),
                                       ("07", "Wrenwell", "decision"),
                                       ("09", "Cedar", "proposal")):
            statement = (
                f"Someone proposes {value} as a replacement."
                if claim_kind == "proposal" else
                f"We decided to use {value} as the supplier for launch."
            )
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": f"meeting-{day}.md",
                "content": f"[2026-10-{day}T10:00:00Z] Alice: {statement}",
            })
            _wait_source(base, imported["source"]["source_id"])
            if claim_kind == "decision":
                _, inbox = _client(base, "/api/review/candidates")
                candidate_id = next(item["candidate_id"] for item in inbox["candidates"]
                                    if item["proposed_value"] == value)
                status, approved = _client(base, f"/api/review/candidates/{candidate_id}/approve",
                                           method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})
                assert status == 200, approved
                assert approved["workflow_state"] == "MATERIALIZED"
        _, payload = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10"})
        recovery = payload["recovery"]
        assert recovery["status"] == "FOUND"
        assert recovery["effective"]["value"] == "Wrenwell"
        assert [item["value"] for item in recovery["proposals"]] == ["Cedar"]
        assert recovery["proposals"][0]["status"] == "PROPOSED_NOT_APPROVED"
        assert recovery["proposals"][0]["evidence"]["verified"] is True
        _, historic = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-06"})
        assert historic["recovery"]["effective"]["value"] == "Birchline"
        assert historic["recovery"]["proposals"] == []
    finally:
        _stop_product_server(server, worker, app)


@pytest.mark.parametrize("second_value", ["Wrenwell", "Birchline"])
def test_recovery_exposes_equal_time_source_conflict_without_promoting_either_claim(tmp_path, second_value):
    base, server, worker, app = _start_product_server(
        tmp_path, [_claim("Birchline"), _claim(second_value)]
    )
    try:
        _client(base, "/api/workspaces", method="POST", data={"name": "Launch"})
        for speaker, value in (("Alice", "Birchline"), ("Bob", second_value)):
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": f"{speaker}.md",
                "content": f"[2026-10-01T10:00:00Z] {speaker}: We decided to use {value} as the supplier for launch.",
            })
            _wait_source(base, imported["source"]["source_id"])
        status, payload = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10"})
        recovery = payload["recovery"]
        assert status == 200
        assert recovery["status"] == (
            "SOURCE_CONFLICT_UNREVIEWED" if second_value != "Birchline" else "SUBJECT_NOT_FOUND"
        )
        assert recovery["effective"] is None
        assert {item["value"] for item in recovery["source_conflicts"]} == (
            {"Birchline", "Wrenwell"} if second_value != "Birchline" else set()
        )
        assert all(item["evidence"]["verified"] for item in recovery["source_conflicts"])
        assert all(item["authority"] == "NON_AUTHORITATIVE" for item in recovery["source_conflicts"])
        server.investigation_service = ProductInvestigationService(
            app, model_factory=lambda _query: (_ for _ in ()).throw(
                AssertionError("unreviewed source conflict must not start an Agent run")
            ),
        )
        agent_status, agent_payload = _client(
            base, "/api/agent/decisions/ask", method="POST", data={
                "query": "Which supplier is effective?", "workspace_id": recovery["workspace_id"],
                "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
            },
        )
        assert agent_status == 409
        assert agent_payload["error"]["code"] == "TEMPORAL_SCOPE_UNVERIFIED"
    finally:
        _stop_product_server(server, worker, app)


def test_product_investigation_runtime_reads_only_selected_workspace_memory(tmp_path):
    base, server, worker, app = _start_product_server(tmp_path, [_claim("Birchline")])
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
        workspace_id = created["workspace"]["workspace_id"]
        _, imported = _client(base, "/api/sources/import", method="POST", data={
            "name": "decision.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for launch.",
        })
        _wait_source(base, imported["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
                method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})

        seen = []
        def search_memory(request):
            return ModelAction.tool(ToolCall(
                call_id="temporal-memory-1", tool_id="search_decision_memory",
                arguments={"query": "supplier", "limit": 5},
            ))
        def final_after_navigation(request):
            seen.extend(request.observation.value)
            return ModelAction.final(json.dumps({
                "schema_version": "team-decision-result/v1",
                "decision": {"value": None, "status": "not_found", "evidence_refs": []},
                "rationale": [], "rejected_alternatives": [], "actions": [],
                "unresolved_items": [],
                "uncertainty": {"status": "insufficient_evidence", "statement": "Source not read yet",
                                "unknown_fields": ["decision"], "evidence_refs": []},
                "evidence_refs": [],
            }))
        service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([search_memory, final_after_navigation]),
            max_steps=3,
        )
        started = service.start("Which supplier was reviewed for Launch?", workspace_id)
        deadline = monotonic() + 30
        snapshot = started
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            snapshot = service.inspect(started["run"]["id"])
        assert snapshot["kind"] != "running"
        assert len(seen) == 1
        assert seen[0]["workspace_id"] == workspace_id
        assert seen[0]["value"] == "Birchline"
        assert seen[0]["grounding_role"] == "navigation_only"
        assert snapshot["metrics"]["read_count"] == 0
        assert snapshot["kind"] != "success"  # Memory alone cannot prove the Final.
    finally:
        _stop_product_server(server, worker, app)


def test_recovery_separates_source_event_from_review_and_explicit_effective_date(tmp_path):
    first = _claim("Birchline")
    first.update(temporal_status="EXPLICIT", valid_from="2026-02-01")
    second = _claim("Wrenwell")
    second.update(temporal_status="EXPLICIT", valid_from="2026-03-15")
    base, server, worker, app = _start_product_server(tmp_path, [first, second])
    try:
        _client(base, "/api/workspaces", method="POST", data={"name": "Pioneer"})
        for source_day, effective_day, value in (
            ("2026-01-15", "2026-02-01", "Birchline"),
            ("2026-03-10", "2026-03-15", "Wrenwell"),
        ):
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": f"approval-{source_day}.md",
                "content": (f"[{source_day}T10:00:00Z] Alice: We decided to use {value} "
                            f"as the supplier for launch, effective {effective_day}."),
            })
            _wait_source(base, imported["source"]["source_id"])
            _, inbox = _client(base, "/api/review/candidates")
            candidate_id = next(item["candidate_id"] for item in inbox["candidates"]
                                if item["proposed_value"] == value)
            status, approved = _client(base, f"/api/review/candidates/{candidate_id}/approve",
                                       method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})
            assert status == 200, approved
        for when, expected in (("2026-01-20", None), ("2026-02-01", "Birchline"),
                               ("2026-02-20", "Birchline"), ("2026-03-07", "Birchline"),
                               ("2026-03-15", "Wrenwell"), ("2026-03-18", "Wrenwell")):
            _, payload = _client(base, "/api/decisions/recover", method="POST", data={
                "subject": "Launch", "relation": "uses supplier", "as_of": when})
            effective = payload["recovery"]["effective"]
            assert (effective["value"] if effective else None) == expected
        _, payload = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-03-18"})
        assert payload["recovery"]["transitions"][0]["effective_at"].startswith("2026-03-15")
        assert payload["recovery"]["transitions"][0]["source_event_at"].startswith("2026-03-10")
        assert payload["recovery"]["transitions"][0]["source_ingested_at"] != payload["recovery"]["transitions"][0]["source_event_at"]
        assert payload["recovery"]["effective"]["authorization"]["authorized_at"] != "2026-03-10"
    finally:
        _stop_product_server(server, worker, app)


def test_investigation_runtime_reads_replacement_source_before_final(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    replacement_claim = _claim("Wrenwell")
    replacement_claim.update(temporal_status="EXPLICIT", valid_from="2026-10-07")
    base, server, worker, app = _start_product_server(tmp_path, [_claim("Birchline"), replacement_claim])
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
        workspace_id = created["workspace"]["workspace_id"]
        for day, value, text in (
            ("01", "Birchline", "We decided to use Birchline as the supplier for launch."),
            ("07", "Wrenwell", "Wrenwell replaced Birchline on 2026-10-07 as the current supplier for the launch."),
        ):
            content = f"[2026-10-{day}T10:00:00Z] Alice: {text}"
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": f"decision-{day}.md", "content": content,
            })
            _wait_source(base, imported["source"]["source_id"])
            _, inbox = _client(base, "/api/review/candidates")
            candidate_id = next(item["candidate_id"] for item in inbox["candidates"]
                                if item["proposed_value"] == value)
            status, response = _client(base, f"/api/review/candidates/{candidate_id}/approve",
                                       method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})
            assert status == 200, response

        def memory(request):
            return ModelAction.tool(ToolCall(call_id="memory-1", tool_id="search_decision_memory",
                                             arguments={"query": "supplier", "limit": 5}))
        def search(request):
            assert request.observation.value[0]["grounding_role"] == "navigation_only"
            source_context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelAction.tool(ToolCall(call_id="source-search-2", tool_id="search_notes",
                                             arguments={"query": "Wrenwell Birchline", "source_context": source_context,
                                                        "limit": 5}))
        observed_read = []
        def read(request):
            evidence = next(item for item in request.observation.value
                            if "Wrenwell replaced" in item["quote"])
            return ModelAction.tool(ToolCall(call_id="source-read-3", tool_id="read_verified_note",
                                             arguments={"note_ref": evidence["evidence_id"]}))
        def final(request):
            evidence = request.observation.value
            observed_read.append(evidence)
            assert "Wrenwell replaced Birchline" in evidence["quote"]
            ref = evidence["evidence_id"]
            source_statement = timestamped_message_body(evidence["quote"])
            assert source_statement is not None
            return ModelAction.final(json.dumps({
                "schema_version": "team-decision-result/v1",
                "decision": {"value": source_statement,
                             "status": "approved", "evidence_refs": [ref]},
                "rationale": [], "rejected_alternatives": [], "actions": [], "unresolved_items": [],
                "uncertainty": {"status": "none", "statement": None, "unknown_fields": [], "evidence_refs": []},
                "evidence_refs": [ref],
            }))
        service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([memory, search, read, final]), max_steps=6,
        )
        started = service.start("Did Wrenwell replace Birchline for Launch?", workspace_id)
        deadline = monotonic() + 60
        snapshot = started
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            snapshot = service.inspect(started["run"]["id"])
        assert snapshot["kind"] != "running"
        state = service._jobs[started["run"]["id"]].backend.engine.checkpointer.get_latest(
            snapshot["run"]["thread_id"]
        )
        assert snapshot["metrics"]["search_count"] >= 1, state.error.to_dict() if state.error else snapshot
        assert snapshot["metrics"]["read_count"] >= 1, [item["quote"] for item in observed_search]
        assert snapshot["kind"] == "success", [item["quote"] for item in observed_read]
        _, recovered = _client(base, "/api/decisions/recover", method="POST", data={
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-08"})
        assert recovered["recovery"]["effective"]["value"] == "Wrenwell"
        assert recovered["recovery"]["transitions"][0]["reason_status"] == "NOT_SOURCE_VERIFIED"
    finally:
        _stop_product_server(server, worker, app)


def test_temporal_agent_http_entry_validates_reviewed_replacement_and_unknown_reason(tmp_path, monkeypatch):
    _exercise_temporal_agent_replacement(tmp_path, monkeypatch)


@pytest.mark.legacy_historical
def test_legacy_temporal_replacement_matches_original_gold(tmp_path, monkeypatch):
    gold_path = Path(__file__).resolve().parents[1] / "fixtures/legacy_temporal_decision/GOLD.json"
    _exercise_temporal_agent_replacement(tmp_path, monkeypatch, historical_gold=gold_path)


def _exercise_temporal_agent_replacement(tmp_path, monkeypatch, *, historical_gold=None):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    replacement = _claim("Wrenwell")
    replacement.update(temporal_status="EXPLICIT", valid_from="2026-10-07")
    base, server, worker, app = _start_product_server(
        tmp_path, [_claim("Birchline"), replacement, _claim("Cedar", claim_type="PROPOSAL")],
    )
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
        workspace_id = created["workspace"]["workspace_id"]
        for day, value, statement, approved_source in (
            ("01", "Birchline", "We decided to use Birchline as the supplier for launch.", True),
            ("07", "Wrenwell", "Wrenwell replaced Birchline on 2026-10-07 as the current supplier for the launch.", True),
            ("09", "Cedar", "Someone proposes Cedar as a replacement.", False),
        ):
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": f"decision-{day}.md",
                "content": f"[2026-10-{day}T10:00:00Z] Alice: {statement}",
            })
            _wait_source(base, imported["source"]["source_id"])
            if not approved_source:
                continue
            _, inbox = _client(base, "/api/review/candidates")
            candidate_id = next(item["candidate_id"] for item in inbox["candidates"]
                                if item["proposed_value"] == value)
            status, approved = _client(
                base, f"/api/review/candidates/{candidate_id}/approve",
                method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"},
            )
            assert status == 200, approved

        observed: dict[str, object] = {}
        def reviewed_memory(request):
            assert '"temporal_decision"' in request.user_input
            assert "ANSWERED_CORRECT" not in request.user_input
            memory_tool = next(tool for tool in request.available_tools
                               if tool.tool_id == "search_decision_memory")
            assert {"subject", "relation"} <= set(memory_tool.input_schema["properties"])
            return ModelAction.tool(ToolCall(
                call_id="temporal-view", tool_id="search_decision_memory",
                arguments={"query": "current reviewed supplier decision", "limit": 5},
            ))
        def source_search(request):
            observed["view"] = request.observation.value[0]["view"]
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelAction.tool(ToolCall(
                call_id="source-search", tool_id="search_notes",
                arguments={"query": "Birchline Wrenwell supplier launch",
                           "source_context": context, "limit": 5},
            ))
        def first_read(request):
            found = request.observation.value
            observed["search"] = found
            current = next(item for item in found if "Wrenwell replaced Birchline" in item["quote"])
            return ModelAction.tool(ToolCall(
                call_id="read-current", tool_id="read_verified_note",
                arguments={"note_ref": current["evidence_id"]},
            ))
        def second_read(request):
            observed["current_read"] = request.observation.value
            old = next(item for item in observed["search"] if "use Birchline" in item["quote"])
            return ModelAction.tool(ToolCall(
                call_id="read-prior", tool_id="read_verified_note",
                arguments={"note_ref": old["evidence_id"]},
            ))
        def typed_final(request):
            observed["prior_read"] = request.observation.value
            view = observed["view"]
            current = observed["current_read"]
            prior = observed["prior_read"]
            temporal_claim = {
                "subject": "Launch", "relation": "uses supplier", "as_of": view["as_of"],
                "decision_id": view["effective"]["decision_id"],
                "receipt_id": view["effective"]["review_receipt"]["receipt_id"],
                "value": "Wrenwell", "supersedes_id": view["effective"]["supersedes_id"],
                "evidence_ids": [current["evidence_id"]],
                "prior_evidence_ids": [prior["evidence_id"]],
            }
            observed["temporal_claim"] = temporal_claim
            return ModelAction.final(json.dumps({
                "schema_version": "claim-proposal/v1", "claims": [],
                "temporal_decision": temporal_claim,
                "uncertainty": {"status": "insufficient_evidence",
                                "unknown_fields": ["replacement_reason"]},
            }))
        service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                reviewed_memory, source_search, first_read, second_read, typed_final,
            ]), max_steps=8,
        )
        server.investigation_service = service
        status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "为什么从 Birchline 换成 Wrenwell？现在执行什么方案？",
            "workspace_id": workspace_id, "subject": "Launch",
            "relation": "uses supplier", "as_of": "2026-10-10",
        })
        assert status == 202, started
        run_id = started["run"]["id"]
        deadline = monotonic() + 60
        snapshot = started
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, snapshot = _client(base, f"/api/agent/decisions/runs/{run_id}")
        assert snapshot["kind"] in {"success", "insufficient"}, snapshot
        assert snapshot["temporal_decision"]["effective"]["value"] == "Wrenwell"
        effective = snapshot["temporal_decision"]["effective"]
        assert effective["authorization"]["receipt_id"] == observed["view"]["effective"]["review_receipt"]["receipt_id"]
        assert effective["authorization"]["authorized_at"]
        assert effective["authorization"]["effective_at"]
        assert effective["source_event_at"] and effective["source_ingested_at"]
        assert effective["valid_from"] and effective["evidence"][0]["verified"] is True
        assert snapshot["temporal_decision"]["transitions"][0]["from_value"] == "Birchline"
        assert snapshot["temporal_decision"]["transitions"][0]["reason_status"] == "NOT_SOURCE_VERIFIED"
        assert [item["value"] for item in snapshot["temporal_decision"]["proposals"]] == ["Cedar"]
        assert snapshot["metrics"]["read_count"] == 2
        assert snapshot["run"]["status"] == "completed"
        assert snapshot["decision"]["status"] == "partial"
        assert snapshot["uncertainty"]["unknown_fields"] == ["replacement_reason"]
        scored_snapshots = {"TD-03": snapshot}
        backend = service._jobs[run_id].backend
        status_record = backend.engine.inspect(snapshot["run"]["thread_id"])
        artifact = backend._safe_result_artifact(status_record)
        assert artifact["result"]["review_decision"] == "evidence_sufficient"
        claim = observed["temporal_claim"]
        evidence = {item["evidence_id"]: item for item in (
            observed["current_read"], observed["prior_read"],
        )}
        read_ids = set(evidence)
        assert app.verify_temporal_claim(workspace_id, claim, evidence, read_ids)["status"] == "FOUND"
        for changed in (
            {**claim, "receipt_id": "untrusted-receipt"},
            {**claim, "value": "Cedar"},
            {**claim, "decision_id": claim["supersedes_id"]},
            {**claim, "as_of": "2026-10-06T00:00:00+00:00"},
            {**claim, "prior_evidence_ids": []},
            {**claim, "evidence_ids": claim["prior_evidence_ids"]},
        ):
            with pytest.raises(ValidationError):
                app.verify_temporal_claim(workspace_id, changed, evidence, read_ids)
        with pytest.raises(ValidationError):
            app.verify_temporal_claim("other-workspace", claim, evidence, read_ids)

        for case_id, question in (
            ("TD-02", "Launch 现在执行什么供应商方案？它替代了哪个旧方案？"),
            ("TD-04", "有人提出 Cedar，它已经是 Launch 的现行方案了吗？"),
        ):
            status, extra_start = _client(base, "/api/agent/decisions/ask", method="POST", data={
                "query": question, "workspace_id": workspace_id,
                "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
            })
            assert status == 202
            extra_run = extra_start["run"]["id"]
            extra_snapshot = extra_start
            deadline = monotonic() + 60
            while extra_snapshot["kind"] == "running" and monotonic() < deadline:
                sleep(0.01)
                _, extra_snapshot = _client(base, f"/api/agent/decisions/runs/{extra_run}")
            assert extra_snapshot["kind"] in {"success", "insufficient"}, extra_snapshot
            assert extra_snapshot["temporal_decision"]["effective"]["value"] == "Wrenwell"
            assert extra_snapshot["temporal_decision"]["transitions"][0]["from_value"] == "Birchline"
            assert extra_snapshot["temporal_decision"]["proposals"][0]["value"] == "Cedar"
            assert extra_snapshot["temporal_decision"]["proposals"][0]["status"] == "PROPOSED_NOT_APPROVED"
            extra_status = service._jobs[extra_run].backend.engine.inspect(extra_snapshot["run"]["thread_id"])
            assert service._jobs[extra_run].backend._safe_result_artifact(extra_status)["result"]["review_decision"] == "evidence_sufficient"
            scored_snapshots[case_id] = extra_snapshot

        historical: dict[str, object] = {}
        def historical_search(request):
            historical["view"] = request.observation.value[0]["view"]
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelAction.tool(ToolCall(
                call_id="historic-search", tool_id="search_notes",
                arguments={"query": "Birchline supplier launch", "source_context": context, "limit": 5},
            ))
        def historical_read(request):
            old = next(item for item in request.observation.value if "use Birchline" in item["quote"])
            return ModelAction.tool(ToolCall(
                call_id="historic-read", tool_id="read_verified_note",
                arguments={"note_ref": old["evidence_id"]},
            ))
        def historical_final(request):
            view = historical["view"]
            source = request.observation.value
            return ModelAction.final(json.dumps({
                "schema_version": "claim-proposal/v1", "claims": [],
                "temporal_decision": {
                    "subject": "Launch", "relation": "uses supplier", "as_of": view["as_of"],
                    "decision_id": view["effective"]["decision_id"],
                    "receipt_id": view["effective"]["review_receipt"]["receipt_id"],
                    "value": "Birchline", "supersedes_id": None,
                    "evidence_ids": [source["evidence_id"]], "prior_evidence_ids": [],
                },
            }))
        historical_service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                reviewed_memory, historical_search, historical_read, historical_final,
            ]), max_steps=7,
        )
        server.investigation_service = historical_service
        status, historical_start = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "10月6日 Launch 采用什么供应商方案？",
            "workspace_id": workspace_id, "subject": "Launch",
            "relation": "uses supplier", "as_of": "2026-10-06",
        })
        assert status == 202
        historic_run = historical_start["run"]["id"]
        historic_snapshot = historical_start
        deadline = monotonic() + 60
        while historic_snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, historic_snapshot = _client(base, f"/api/agent/decisions/runs/{historic_run}")
        assert historic_snapshot["kind"] == "success", historic_snapshot
        assert historic_snapshot["temporal_decision"]["effective"]["value"] == "Birchline"
        assert historic_snapshot["temporal_decision"]["transitions"] == []
        assert historic_snapshot["decision"]["status"] == "approved"
        scored_snapshots["TD-01"] = historic_snapshot

        status, not_yet_start = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "10月6日是否已经改为 Wrenwell？",
            "workspace_id": workspace_id, "subject": "Launch",
            "relation": "uses supplier", "as_of": "2026-10-06",
        })
        assert status == 202
        not_yet_run = not_yet_start["run"]["id"]
        not_yet_snapshot = not_yet_start
        deadline = monotonic() + 60
        while not_yet_snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, not_yet_snapshot = _client(base, f"/api/agent/decisions/runs/{not_yet_run}")
        assert not_yet_snapshot["kind"] == "success", not_yet_snapshot
        assert not_yet_snapshot["temporal_decision"]["effective"]["value"] == "Birchline"
        assert "Wrenwell" not in not_yet_snapshot["decision"]["value"]

        def forged_receipt_final(request):
            view = observed["view"]
            return ModelAction.final(json.dumps({
                "schema_version": "claim-proposal/v1", "claims": [],
                "temporal_decision": {
                    "subject": "Launch", "relation": "uses supplier", "as_of": view["as_of"],
                    "decision_id": view["effective"]["decision_id"],
                    "receipt_id": "forged-receipt", "value": "Wrenwell",
                    "supersedes_id": view["effective"]["supersedes_id"],
                    "evidence_ids": [observed["current_read"]["evidence_id"]],
                    "prior_evidence_ids": [request.observation.value["evidence_id"]],
                },
            }))
        bad_service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                reviewed_memory, source_search, first_read, second_read, forged_receipt_final,
            ]), max_steps=8,
        )
        server.investigation_service = bad_service
        status, bad_start = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "现在是否采用 Wrenwell？", "workspace_id": workspace_id,
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
        })
        assert status == 202
        bad_run = bad_start["run"]["id"]
        bad_snapshot = bad_start
        deadline = monotonic() + 60
        while bad_snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, bad_snapshot = _client(base, f"/api/agent/decisions/runs/{bad_run}")
        assert bad_snapshot["kind"] == "error", bad_snapshot
        assert bad_snapshot["error"]["code"] == "TEMPORAL_DECISION_UNSUPPORTED"
        assert "temporal_decision" not in bad_snapshot

        refusal_service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                reviewed_memory, source_search,
                lambda _request: ModelAction.final(json.dumps({
                    "schema_version": "claim-proposal/v1", "claims": [],
                    "uncertainty": {"status": "insufficient_evidence",
                                    "unknown_fields": ["decision"]},
                })),
            ]), max_steps=5,
        )
        server.investigation_service = refusal_service
        status, refusal_start = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "Is Wrenwell the current supplier?", "workspace_id": workspace_id,
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-10",
        })
        assert status == 202
        refusal_run = refusal_start["run"]["id"]
        refusal_snapshot = refusal_start
        deadline = monotonic() + 60
        while refusal_snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, refusal_snapshot = _client(base, f"/api/agent/decisions/runs/{refusal_run}")
        assert refusal_snapshot["kind"] == "error", refusal_snapshot
        assert refusal_snapshot["error"]["code"] == "TEMPORAL_DECISION_MISSING"
        if historical_gold is None:
            return  # Core contract assertions above are independent of historical scores.
        # Only the historical gate opens original Gold, after Runtime termination.
        gold_cases = json.loads(historical_gold.read_text(encoding="utf-8"))["cases"]
        for case_id, actual in scored_snapshots.items():
            expected = gold_cases[case_id]
            timeline = actual["temporal_decision"]
            assert timeline["effective"]["value"] == expected["effective"]
            assert timeline["effective"]["authorization"]["receipt_id"]
            if "replaces" in expected:
                assert timeline["transitions"][0]["from_value"] == expected["replaces"]
            if "replacement_reason" in expected:
                assert timeline["transitions"][0]["reason_status"] == expected["replacement_reason"]
                assert actual["uncertainty"]["unknown_fields"] == ["replacement_reason"]
            if "candidate" in expected:
                assert any(item["value"] == expected["candidate"]
                           and item["status"] == expected["candidate_status"]
                           for item in timeline["proposals"])
            assert actual["metrics"]["read_count"] >= 1
    finally:
        _stop_product_server(server, worker, app)


@pytest.mark.legacy_historical
def test_temporal_agent_frozen_other_project_uses_tool_evidence_before_gold(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    frozen_root = Path(__file__).resolve().parents[1] / "fixtures/legacy_temporal_decision"
    cases = json.loads((frozen_root / "CASES.json").read_text(encoding="utf-8"))
    setup = cases["holdout_setup"]
    claims = []
    for source in setup["sources"]:
        value = source["content"].split("use ", 1)[1].split(" as the supplier", 1)[0] if "use " in source["content"] else (
            source["content"].split("] Morgan: ", 1)[1].split(" replaced ", 1)[0]
            if " replaced " in source["content"] else source["content"].split("proposes ", 1)[1].split(" as", 1)[0]
        )
        claim = _claim(value, claim_type="DECISION" if source["review"] == "approve" else "PROPOSAL")
        claim["subject"] = setup["subject"]
        if source["review"] == "approve" and " replaced " in source["content"]:
            claim.update(temporal_status="EXPLICIT", valid_from=source["content"][1:11])
        claims.append(claim)
    base, server, worker, app = _start_product_server(tmp_path, claims)
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": setup["workspace"]})
        workspace_id = created["workspace"]["workspace_id"]
        for source in setup["sources"]:
            _, imported = _client(base, "/api/sources/import", method="POST", data={
                "name": source["name"], "content": source["content"],
            })
            _wait_source(base, imported["source"]["source_id"])
            if source["review"] == "approve":
                _, inbox = _client(base, "/api/review/candidates")
                candidate = next(item for item in inbox["candidates"]
                                 if item["source_id"] == imported["source"]["source_id"])
                status, approved = _client(base, f"/api/review/candidates/{candidate['candidate_id']}/approve",
                                           method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})
                assert status == 200, approved

        observed = {}
        def lookup(_request):
            return ModelAction.tool(ToolCall(call_id="holdout-lookup", tool_id="search_decision_memory",
                                             arguments={"query": "reviewed supplier decision", "limit": 5}))
        def search(request):
            observed["view"] = request.observation.value[0]["view"]
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelAction.tool(ToolCall(call_id="holdout-search", tool_id="search_notes",
                                             arguments={"query": f"{setup['subject']} supplier",
                                                        "source_context": context, "limit": 5}))
        def read_current(request):
            observed["search"] = request.observation.value
            source_id = observed["view"]["effective"]["evidence"][0]["source_id"]
            found = next(item for item in observed["search"]
                         if item["relative_path"] == f"{source_id}.md")
            return ModelAction.tool(ToolCall(call_id="holdout-read-current", tool_id="read_verified_note",
                                             arguments={"note_ref": found["evidence_id"]}))
        def read_prior(request):
            observed["current"] = request.observation.value
            view = observed["view"]
            prior = next(item for item in view["history"]
                         if item["decision_id"] == view["effective"]["supersedes_id"])
            source_id = prior["evidence"][0]["source_id"]
            found = next(item for item in observed["search"]
                         if item["relative_path"] == f"{source_id}.md")
            return ModelAction.tool(ToolCall(call_id="holdout-read-prior", tool_id="read_verified_note",
                                             arguments={"note_ref": found["evidence_id"]}))
        def finish(request):
            view = observed["view"]
            return ModelAction.final(json.dumps({
                "schema_version": "claim-proposal/v1", "claims": [],
                "temporal_decision": {
                    "subject": view["subject"], "relation": view["relation"], "as_of": view["as_of"],
                    "decision_id": view["effective"]["decision_id"],
                    "receipt_id": view["effective"]["review_receipt"]["receipt_id"],
                    "value": view["effective"]["value"],
                    "supersedes_id": view["effective"]["supersedes_id"],
                    "evidence_ids": [observed["current"]["evidence_id"]],
                    "prior_evidence_ids": [request.observation.value["evidence_id"]],
                },
                "uncertainty": {"status": "insufficient_evidence",
                                "unknown_fields": ["replacement_reason"]},
            }))
        service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                lookup, search, read_current, read_prior, finish,
            ]), max_steps=8,
        )
        server.investigation_service = service
        case = cases["holdout_case"]
        status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": case["query"], "workspace_id": workspace_id,
            "subject": setup["subject"], "relation": setup["relation"], "as_of": case["as_of"],
        })
        assert status == 202
        snapshot = started
        deadline = monotonic() + 60
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, snapshot = _client(base, f"/api/agent/decisions/runs/{started['run']['id']}")
        assert snapshot["kind"] in {"success", "insufficient"}, snapshot
        status_record = service._jobs[started["run"]["id"]].backend.engine.inspect(snapshot["run"]["thread_id"])
        assert service._jobs[started["run"]["id"]].backend._safe_result_artifact(status_record)["result"]["review_decision"] == "evidence_sufficient"
        # The separate Gold is read only after the Runtime has terminated.
        gold = json.loads((frozen_root / "GOLD.json").read_text(encoding="utf-8"))["cases"][case["id"]]
        assert snapshot["temporal_decision"]["effective"]["value"] == gold["effective"]
        assert snapshot["temporal_decision"]["transitions"][0]["from_value"] == gold["replaces"]
        assert snapshot["temporal_decision"]["transitions"][0]["reason_status"] == gold["replacement_reason"]
        assert snapshot["uncertainty"]["unknown_fields"] == ["replacement_reason"]
        assert snapshot["temporal_decision"]["proposals"][0]["value"] in gold["forbidden_current"]
        assert snapshot["metrics"]["read_count"] == 2
    finally:
        _stop_product_server(server, worker, app)


def test_temporal_agent_rejects_out_of_scope_lookup_but_accepts_independently_verified_final(tmp_path, monkeypatch):
    """A rejected read-only lookup cannot poison a later strictly grounded Final."""
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    base, server, worker, app = _start_product_server(tmp_path, [_claim("Birchline")])
    try:
        _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Atlas"})
        workspace_id = created["workspace"]["workspace_id"]
        _, imported = _client(base, "/api/sources/import", method="POST", data={
            "name": "decision.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for launch.",
        })
        _wait_source(base, imported["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        candidate = next(item for item in inbox["candidates"]
                         if item["source_id"] == imported["source"]["source_id"])
        assert _client(base, f"/api/review/candidates/{candidate['candidate_id']}/approve",
                       method="POST", data={"reviewer_id": "local-user", "reason": "Reviewed"})[0] == 200

        observed = {}
        def scoped_lookup(_request):
            return ModelAction.tool(ToolCall(call_id="scope-ok", tool_id="search_decision_memory",
                                             arguments={"query": "Launch supplier", "limit": 5,
                                                        "subject": "Launch", "relation": "uses supplier",
                                                        "as_of": "2026-10-06T00:00:00+00:00"}))
        def source_search(request):
            observed["view"] = request.observation.value[0]["view"]
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            return ModelAction.tool(ToolCall(call_id="scope-search", tool_id="search_notes",
                                             arguments={"query": "Birchline Launch", "source_context": context,
                                                        "limit": 5}))
        def source_read(request):
            ref = next(item["evidence_id"] for item in request.observation.value
                       if "Birchline" in item["quote"])
            return ModelAction.tool(ToolCall(call_id="scope-read", tool_id="read_verified_note",
                                             arguments={"note_ref": ref}))
        def rejected_lookup(request):
            observed["read"] = request.observation.value
            return ModelAction.tool(ToolCall(call_id="scope-denied", tool_id="search_decision_memory",
                                             arguments={"query": "Launch supplier", "limit": 5,
                                                        "subject": "Launch", "relation": "uses supplier",
                                                        "as_of": "2026-10-09T00:00:00+00:00"}))
        def final_after_denial(request):
            assert request.observation.error is not None
            view = observed["view"]
            return ModelAction.final(json.dumps({
                "schema_version": "claim-proposal/v1", "claims": [],
                "temporal_decision": {
                    "subject": "Launch", "relation": "uses supplier", "as_of": view["as_of"],
                    "decision_id": view["effective"]["decision_id"],
                    "receipt_id": view["effective"]["review_receipt"]["receipt_id"],
                    "value": "Birchline", "supersedes_id": None,
                    "evidence_ids": [observed["read"]["evidence_id"]],
                    "prior_evidence_ids": [],
                },
            }))
        service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                scoped_lookup, source_search, source_read, rejected_lookup, final_after_denial,
            ]), max_steps=8,
        )
        server.investigation_service = service
        status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "10月6日 Launch 正式采用什么供应商？", "workspace_id": workspace_id,
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-06",
        })
        assert status == 202
        run_id = started["run"]["id"]
        snapshot = started
        deadline = monotonic() + 60
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, snapshot = _client(base, f"/api/agent/decisions/runs/{run_id}")
        assert snapshot["kind"] == "success", snapshot.get("error")
        assert snapshot["temporal_decision"]["effective"]["value"] == "Birchline"
        assert any(item["reason"] == "TOOL_INVALID_ARGUMENTS" for item in snapshot["trace"])
        backend = service._jobs[run_id].backend
        status_record = backend.engine.inspect(snapshot["run"]["thread_id"])
        assert backend._safe_result_artifact(status_record)["result"]["review_decision"] == "evidence_sufficient"

        def forged_final(request):
            valid = json.loads(final_after_denial(request).final_answer)
            valid["temporal_decision"]["receipt_id"] = "forged-receipt"
            return ModelAction.final(json.dumps(valid))
        bad_service = ProductInvestigationService(
            app, model_factory=lambda _query: FakeModelAdapter([
                scoped_lookup, source_search, source_read, rejected_lookup, forged_final,
            ]), max_steps=8,
        )
        server.investigation_service = bad_service
        status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
            "query": "10月6日 Launch 正式采用什么供应商？", "workspace_id": workspace_id,
            "subject": "Launch", "relation": "uses supplier", "as_of": "2026-10-06",
        })
        assert status == 202
        snapshot = started
        deadline = monotonic() + 60
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            _, snapshot = _client(base, f"/api/agent/decisions/runs/{started['run']['id']}")
        assert snapshot["kind"] == "error"
        assert snapshot["error"]["code"] == "TEMPORAL_DECISION_UNSUPPORTED"
    finally:
        _stop_product_server(server, worker, app)


def test_source_updates_and_deletes_reconcile_exact_versioned_evidence(product_server):
    base = product_server
    _, created = _client(base, "/api/workspaces", method="POST", data={"name": "Workspace A"})
    source_text = "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for launch."
    _, imported = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={"name": "decision.md", "content": source_text},
    )
    source = _wait_source(base, imported["source"]["source_id"])
    _, inbox = _client(base, "/api/review/candidates")
    candidate_id = inbox["candidates"][0]["candidate_id"]
    status, approved = _client(
        base,
        f"/api/review/candidates/{candidate_id}/approve",
        method="POST",
        data={"reviewer_id": "local-user", "reason": "Verified"},
    )
    assert status == 200 and approved["workflow_state"] == "MATERIALIZED"
    _, decisions = _client(base, "/api/decisions")
    original = decisions["decisions"][0]
    source_version_before = original["evidence"][0]["source_version"]

    status, unchanged = _client(
        base,
        f"/api/sources/{source['source_id']}",
        method="PUT",
        data={"name": "decision.md", "content": source_text},
    )
    assert status == 200 and unchanged["source"]["source_version"] == source_version_before
    _, after_unchanged = _client(base, f"/api/decisions/{original['decision_id']}")
    assert after_unchanged["decision"]["memory_state"] == "ACTIVE"

    changed_text = source_text + "\n[2026-10-02T10:00:00Z] Bob: The team discussed rollout timing."
    status, updated = _client(
        base,
        f"/api/sources/{source['source_id']}",
        method="PUT",
        data={"name": "decision.md", "content": changed_text},
    )
    assert status == 202
    updated_source = _wait_source(base, source["source_id"])
    assert updated_source["source_version"] != source_version_before
    unchanged_evidence_version = updated_source["source_version"]
    _, still_current = _client(base, f"/api/decisions/{original['decision_id']}")
    assert still_current["decision"]["memory_state"] == DecisionMemoryState.ACTIVE.value
    assert still_current["decision"]["evidence"][0]["source_version"] == source_version_before
    assert _client(base, "/api/review/attention")[1]["decisions"] == []

    changed_evidence_text = (
        "[2026-10-01T10:00:00Z] Alice: The team decided to use Wrenwell as the supplier for launch.\n"
        "[2026-10-02T10:00:00Z] Bob: The team discussed rollout timing."
    )
    status, changed_evidence = _client(
        base,
        f"/api/sources/{source['source_id']}",
        method="PUT",
        data={"name": "decision.md", "content": changed_evidence_text},
    )
    assert status == 202
    updated_source = _wait_source(base, source["source_id"])
    assert updated_source["source_version"] != unchanged_evidence_version
    _, stale = _client(base, f"/api/decisions/{original['decision_id']}")
    assert stale["decision"]["memory_state"] == DecisionMemoryState.STALE.value
    assert stale["decision"]["evidence"][0]["source_version"] == source_version_before
    attention = _client(base, "/api/review/attention")[1]["decisions"]
    assert [item["decision_id"] for item in attention] == [original["decision_id"]]
    _, stale_candidate = _client(base, f"/api/review/candidates/{candidate_id}")
    assert stale_candidate["candidate"]["status"] == "STALE"

    status, deleted = _client(base, f"/api/sources/{source['source_id']}", method="DELETE")
    assert status == 200 and deleted["source"]["active"] is False
    _, sources = _client(base, "/api/sources")
    assert sources["sources"] == []
    # The historical source version and decision provenance remain inspectable.
    _, still_stale = _client(base, f"/api/decisions/{original['decision_id']}")
    assert still_stale["decision"]["evidence"][0]["quote"] in source_text
    assert still_stale["decision"]["evidence"][0]["source_version"] == source_version_before


def test_review_requires_explicit_effective_time_for_unresolved_candidates(undated_product_server):
    base = undated_product_server
    _, _ = _client(base, "/api/workspaces", method="POST", data={"name": "Temporal"})
    status, imported = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={
            "name": "undated.md",
            "content": "[2026-10-01T10:00:00Z] Alice: This supplier works for launch.",
        },
    )
    assert status == 202
    source = _wait_source(base, imported["source"]["source_id"])
    assert source["ingestion_status"] == "COMPLETE"
    # The API never substitutes wall-clock time for an unresolved valid_from.
    status, inbox = _client(base, "/api/review/candidates")
    assert status == 200 and len(inbox["candidates"]) == 1
    candidate = inbox["candidates"][0]
    assert candidate["valid_from"] is None
    status, result = _client(
        base,
        f"/api/review/candidates/{candidate['candidate_id']}/approve",
        method="POST",
        data={
            "reviewer_id": "local-user",
            "reason": "Reviewed",
            "subject": "Launch",
            "relation": "uses supplier",
        },
    )
    assert status == 409
    assert result["error"]["code"] == "TEMPORAL_UNRESOLVED"


def test_provider_must_be_explicitly_configured_and_failure_is_actionable(unconfigured_product_server):
    base = unconfigured_product_server
    status, provider = _client(base, "/api/status")
    assert status == 200 and provider["ready"] is False
    assert "--allow-external-provider" in provider["message"]
    _client(base, "/api/workspaces", method="POST", data={"name": "Local only"})
    status, imported = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={
            "name": "meeting.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline.",
        },
    )
    assert status == 202
    source = _wait_source(base, imported["source"]["source_id"])
    assert source["ingestion_status"] == "FAILED"
    assert "explicitly enable source-content requests" in source["error"]
    status, retry = _client(
        base,
        f"/api/sources/{source['source_id']}/retry",
        method="POST",
        data={},
    )
    assert status == 202 and retry["source"]["ingestion_status"] == "INGESTING"
    retried_source = _wait_source(base, source["source_id"])
    assert retried_source["ingestion_status"] == "FAILED"
    assert "explicitly enable source-content requests" in retried_source["error"]


def test_workspaces_keep_sources_and_decisions_isolated(product_server):
    base = product_server
    status, workspace_a = _client(base, "/api/workspaces", method="POST", data={"name": "Workspace A"})
    assert status == 201
    workspace_a_id = workspace_a["workspace"]["workspace_id"]
    _, imported_a = _client(
        base,
        "/api/sources/import",
        method="POST",
        data={
            "name": "decision-a.md",
            "content": "[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for launch.",
        },
    )
    _wait_source(base, imported_a["source"]["source_id"])
    candidate_a = _client(base, "/api/review/candidates")[1]["candidates"][0]
    status, materialized = _client(
        base,
        f"/api/review/candidates/{candidate_a['candidate_id']}/approve",
        method="POST",
        data={"reviewer_id": "local-user", "reason": "Verified in Workspace A"},
    )
    assert status == 200 and materialized["workflow_state"] == "MATERIALIZED"

    status, workspace_b = _client(base, "/api/workspaces", method="POST", data={"name": "Workspace B"})
    workspace_b_id = workspace_b["workspace"]["workspace_id"]
    assert status == 201 and workspace_b["workspace"]["selected"] is True
    assert _client(base, "/api/workspaces/current")[1]["workspace"]["workspace_id"] == workspace_b_id
    assert _client(base, "/api/sources")[1]["sources"] == []
    assert _client(base, "/api/decisions")[1]["decisions"] == []

    status, selected = _client(
        base,
        "/api/workspaces/current",
        method="POST",
        data={"workspace_id": workspace_a_id},
    )
    assert status == 200 and selected["workspace"]["workspace_id"] == workspace_a_id
    decisions = _client(base, "/api/decisions")[1]["decisions"]
    assert len(decisions) == 1 and decisions[0]["value"] == "Birchline"
    assert decisions[0]["memory_state"] == "ACTIVE"
