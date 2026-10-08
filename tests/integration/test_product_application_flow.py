from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from threading import Lock, Thread
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.decision_memory.models import DecisionMemoryState
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
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
