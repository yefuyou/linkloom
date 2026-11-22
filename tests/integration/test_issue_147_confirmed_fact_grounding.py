"""Issue #147 end to end: a product Ask grounds unpatterned source wording via a human receipt."""
from __future__ import annotations

import json
from time import monotonic, sleep

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.contracts import ToolCall
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.product import ProductApplication

# No legacy clause pattern parses this sentence.
SOURCE = "Ada 自 2026-04-01 起牵头 Bay 项目。"


class OwnerCandidateProvider:
    """Offline extractor: proposes one typed owner candidate (a model proposal, not a fact)."""

    def complete(self, request):
        claim = {"claim_type": "FACT", "subject": "Bay", "relation": "owner_of_project", "value": "Ada",
                 "temporal_status": "EXPLICIT", "valid_from": "2026-04-01", "valid_to": None,
                 "confidence": dict.fromkeys(("claim_type", "entity", "relation", "temporal", "overall"), 0.95)}
        return ModelResponse(
            action=ModelAction.tool(ToolCall(call_id="candidate-1", tool_id=request.available_tools[0].tool_id,
                                             arguments={"claims": [claim]})),
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            provider_response_id="offline", finish_reason="STOP")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    extractor = ProviderNeutralSemanticExtractor(
        OwnerCandidateProvider(), provider_id="offline", model_id="offline-model",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="guard", reservation_id="r", estimated_cost_upper_bound_usd=0.0),
        request_guard_id="guard", max_attempts=1)
    # The Host opts typed relations into its review catalogue.
    application = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=extractor,
                                      relation_resolver=FrozenRelationResolver(("owner_of_project",)))
    application.handle_post("/api/workspaces", {"name": "Issue 147"})
    created, _ = application.handle_post("/api/sources/import", {"name": "owner.md", "content": SOURCE})
    deadline = monotonic() + 10
    while application.handle_get(f"/api/sources/{created['source']['source_id']}")["source"]["ingestion_status"] == "INGESTING":
        assert monotonic() < deadline
        sleep(0.01)
    yield application
    application.close()


def _approve(app):
    candidate = app.handle_get("/api/review/candidates")["candidates"][0]
    _, status = app.handle_post(f"/api/review/candidates/{candidate['candidate_id']}/approve",
                                {"reviewer_id": "reviewer", "reason": "checked against the source"})
    assert status == 200


def _ask(app, claim):
    workspace_id = app._workspace_id()
    seen = {}

    def search(request):
        context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
        return ModelAction.tool(ToolCall(call_id="s1", tool_id="search_notes", arguments={
            "query": "Bay Ada", "source_context": context, "limit": 5}))

    def read(request):
        evidence = next(item for item in request.observation.value if "Ada" in item["quote"])
        return ModelAction.tool(ToolCall(call_id="r1", tool_id="read_verified_note",
                                         arguments={"note_ref": evidence["evidence_id"]}))

    def final(request):
        ref = request.observation.value["evidence_id"]
        seen["quote"] = request.observation.value["quote"]
        return ModelAction.final(json.dumps({
            "schema_version": "team-decision-result/v1",
            "decision": {"value": claim, "status": "approved", "evidence_refs": [ref]},
            "rationale": [], "rejected_alternatives": [], "actions": [], "unresolved_items": [],
            "uncertainty": {"status": "none", "statement": None, "unknown_fields": [], "evidence_refs": []},
            "evidence_refs": [ref],
        }))
    service = ProductInvestigationService(
        app, model_factory=lambda _query: FakeModelAdapter([search, read, final]), max_steps=6)
    try:
        started = service.start("Who owns Bay as of 2026-04-01?", workspace_id)
        snapshot, deadline = started, monotonic() + 60
        while snapshot["kind"] == "running" and monotonic() < deadline:
            sleep(0.01)
            snapshot = service.inspect(started["run"]["id"])
        assert seen.get("quote") == SOURCE, snapshot
        return snapshot
    finally:
        service.close()


CLAIM = "As of 2026-04-01 Ada is the accountable owner of Bay."


def test_human_approved_receipt_grounds_unpatterned_source_in_a_product_ask(app):
    _approve(app)
    assert _ask(app, CLAIM)["kind"] == "success"
    assert _ask(app, "Ada is the accountable owner of Bay.")["kind"] == "success"  # open interval


def test_pending_model_candidate_is_not_a_source_fact(app):
    # Before #147 every Ask fails here; a model proposal still must not change that.
    snapshot = _ask(app, CLAIM)
    assert (snapshot["kind"], snapshot["error"]["code"]) == ("error", "TEAM_DECISION_CONTRACT_ERROR")


@pytest.mark.parametrize("claim", [
    "As of 2026-04-01 Bay is the accountable owner of Ada.",  # entities reversed
    "As of 2026-03-01 Ada is the accountable owner of Bay.",  # before the confirmed interval
    "As of 2026-04-01 Cy is the accountable owner of Bay.",   # wrong owner
])
def test_approved_receipt_does_not_support_a_different_claim(app, claim):
    _approve(app)
    snapshot = _ask(app, claim)
    assert (snapshot["kind"], snapshot["error"]["code"]) == ("error", "TEAM_DECISION_CONTRACT_ERROR")
