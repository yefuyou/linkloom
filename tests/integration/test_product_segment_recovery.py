from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from time import monotonic, sleep

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext,
)
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication


FIXTURE = (Path(__file__).parents[1] / "fixtures" / "v1_release_acceptance_meeting.txt").read_text(encoding="utf-8")


def claim(value: str, kind: str, date: str | None = None) -> dict[str, object]:
    return {
        "claim_type": kind,
        "subject": "2026 launch",
        "relation": "uses vendor",
        "value": value,
        "temporal_status": "EXPLICIT" if date else "NOT_STATED",
        "valid_from": date,
        "valid_to": None,
        "confidence": {key: 0.95 for key in ("claim_type", "entity", "relation", "temporal", "overall")},
    }


class ScriptedProvider:
    def __init__(self, *, malformed_replacement: int = 0, fact_initial: bool = False) -> None:
        self.calls: Counter[str] = Counter()
        self.malformed_replacement = malformed_replacement
        self.fact_initial = fact_initial

    def complete(self, request):
        prompt = request.user_input
        if "Kestrel Ledger was proposed" in prompt:
            segment = "proposal"
            payload = claim("Kestrel Ledger", "PROPOSAL")
        elif "replace Birchline with Wrenwell" in prompt:
            segment = "replacement"
            payload = claim("Wrenwell", "DECISION", "2026-10-08")
        else:
            segment = "initial"
            payload = claim("Birchline", "FACT" if self.fact_initial else "DECISION",
                            None if self.fact_initial else "2026-10-06")
        self.calls[segment] += 1
        number = self.calls[segment]
        if segment == "replacement" and number <= self.malformed_replacement:
            action = ModelAction.final("incomplete structured extraction")
        else:
            action = ModelAction.tool(ToolCall(
                call_id=f"{segment}-{number}", tool_id=request.available_tools[0].tool_id,
                arguments={"claims": [deepcopy(payload)]},
            ))
        return ModelResponse(
            action=action, usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            provider_response_id=f"offline-{segment}-{number}", finish_reason="STOP",
        )


def app_for(tmp_path, provider):
    extractor = ProviderNeutralSemanticExtractor(
        provider, provider_id="offline-test", model_id="offline-test-model",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="offline-guard", reservation_id="offline-reservation",
            estimated_cost_upper_bound_usd=0.0,
        ),
        request_guard_id="offline-guard", max_attempts=1,
    )
    app = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=extractor)
    app.handle_post("/api/workspaces", {"name": "Release reliability"})
    return app


def wait_source(app, source_id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        source = app.handle_get(f"/api/sources/{source_id}")["source"]
        if source["ingestion_status"] != "INGESTING":
            return source
        sleep(0.01)
    raise AssertionError("source ingestion timed out")


def import_fixture(app):
    created, status = app.handle_post("/api/sources/import", {"name": "acceptance-meeting.txt", "content": FIXTURE})
    assert status == 202
    return wait_source(app, created["source"]["source_id"])


def retry(app, source_id):
    response, status = app.handle_post(f"/api/sources/{source_id}/retry", {})
    assert status == 202
    return wait_source(app, source_id)


def test_targeted_recovery_receipts_and_complete_flow(tmp_path):
    provider = ScriptedProvider(malformed_replacement=1)
    app = app_for(tmp_path, provider)
    try:
        source = import_fixture(app)
        assert source["ingestion_status"] == "PARTIAL"
        assert source["segment_summary"] == {"total": 3, "processed": 2, "failed": 1, "retryable": 1}
        first_attempts = app.handle_get(f"/api/sources/{source['source_id']}/ingestion-attempts")["attempts"]
        malformed = next(item for item in first_attempts if item["outcome"] == "MALFORMED")
        assert malformed["failure_category"] == "RETRYABLE_EXTRACTION"
        assert malformed["replayable"] is True
        assert ModelResponse.from_dict(malformed["response"]).action.kind == "final"
        assert malformed["receipt"]["terminal_reason"] == "MALFORMED_EXTRACTION"
        class ReplayProvider:
            def complete(self, request):
                return ModelResponse.from_dict(malformed["response"])

        replay = ProviderNeutralSemanticExtractor(
            ReplayProvider(), provider_id="offline-replay", model_id="offline-replay-model",
            request_guard=lambda request: ProviderRequestAuthorization.approve(
                request, guard_id="replay-guard", reservation_id="replay-reservation",
                estimated_cost_upper_bound_usd=0.0,
            ), request_guard_id="replay-guard", max_attempts=1,
        )
        artifact = app._source_artifact(source)
        replacement_segment = parse_timestamped_text(artifact).segments[-1]
        replayed = replay.extract(replacement_segment, SemanticExtractionContext(
            expected_workspace_id=source["workspace_id"], relation_resolver=app.relation_resolver,
        ))
        assert replayed.failure_code.value == "MALFORMED_EXTRACTION"

        source = retry(app, source["source_id"])
        assert source["ingestion_status"] == "COMPLETE"
        assert source["candidate_count"] == 3
        assert provider.calls == Counter(initial=1, proposal=1, replacement=2)
        attempts = app.handle_get(f"/api/sources/{source['source_id']}/ingestion-attempts")["attempts"]
        assert sorted(item["attempt_number"] for item in attempts) == [1, 1, 1, 2]
        inbox = app.handle_get("/api/review/candidates")
        assert len(inbox["candidates"]) == 2
        assert len(inbox["supporting_proposals"]) == 1
        for value in ("Birchline", "Wrenwell"):
            candidate = next(item for item in inbox["candidates"] if item["proposed_value"] == value)
            approved, status = app.handle_post(
                f"/api/review/candidates/{candidate['candidate_id']}/approve",
                {"reviewer_id": "release-reviewer", "reason": "Explicit meeting decision",
                 "valid_from": "2026-10-06" if value == "Birchline" else "2026-10-08"},
            )
            assert status == 200 and approved["workflow_state"] == "MATERIALIZED"
        decisions = app.handle_get("/api/decisions")["decisions"]
        assert len(decisions) == 1
        assert decisions[0]["value"] == "Wrenwell"
        ask = app.handle_post("/api/ask", {"query": "What vendor are we currently using for the 2026 launch?"})[0]["answer"]
        assert ask["answer"]["value"] == "Wrenwell"
        assert ask["answer"]["replaced"]["value"] == "Birchline"
        before = app.handle_post("/api/ask", {"query": "2026 launch vendor", "as_of": "2026-10-07"})[0]["answer"]
        after = app.handle_post("/api/ask", {"query": "2026 launch vendor", "as_of": "2026-10-09"})[0]["answer"]
        assert before["answer"]["value"] == "Birchline"
        assert after["answer"]["value"] == "Wrenwell"
        old_version = source["source_version"]
        changed_source = FIXTURE.splitlines()[-1].replace(
            "effective October 8, 2026.", "effective October 8, 2026, after supplier audit."
        )
        updated, status = app.handle_put(
            f"/api/sources/{source['source_id']}",
            {"name": "acceptance-meeting.txt", "content": changed_source},
        )
        assert status == 202 and updated["changed"] is True
        source = wait_source(app, source["source_id"])
        assert source["source_version"] != old_version
        attention = app.handle_get("/api/review/attention")["decisions"]
        assert any(item["decision_id"] == decisions[0]["decision_id"]
                   and item["memory_state"] in {"STALE", "NEEDS_REVALIDATION"} for item in attention)
        assert provider.calls == Counter(initial=1, proposal=1, replacement=3)
    finally:
        app.close()


def test_two_failures_stop_without_reextracting_successful_segments(tmp_path):
    provider = ScriptedProvider(malformed_replacement=2)
    app = app_for(tmp_path, provider)
    try:
        source = import_fixture(app)
        source = retry(app, source["source_id"])
        assert source["ingestion_status"] == "PARTIAL"
        assert source["segment_summary"]["retryable"] == 0
        assert provider.calls == Counter(initial=1, proposal=1, replacement=2)
        with pytest.raises(Exception, match="No failed segment"):
            retry(app, source["source_id"])
    finally:
        app.close()


def test_accepted_segments_are_idempotent_and_new_version_is_isolated(tmp_path):
    provider = ScriptedProvider(malformed_replacement=1)
    app = app_for(tmp_path, provider)
    try:
        source = import_fixture(app)
        old_version = source["source_version"]
        old_source_id = source["source_id"]
        source = retry(app, source["source_id"])
        assert source["candidate_count"] == 3
        old_attempts = len(app.repository.ingestion_attempts(
            source["workspace_id"], old_source_id, old_version
        ))
        old_calls = provider.calls.copy()
        app._ingest(source["workspace_id"], old_source_id, app._source_artifact(source))
        assert provider.calls == old_calls
        assert len(app.repository.ingestion_attempts(
            source["workspace_id"], old_source_id, old_version
        )) == old_attempts
        assert app.handle_get(f"/api/sources/{old_source_id}")["source"]["candidate_count"] == 3
        # A new immutable version must not inherit the old version's attempts.
        changed = FIXTURE.replace("Kestrel Ledger was proposed", "Kestrel Ledger was discussed")
        result, status = app.handle_put(
            f"/api/sources/{source['source_id']}",
            {"name": "acceptance-meeting.txt", "content": changed},
        )
        assert status == 202 and result["changed"] is True
        updated = wait_source(app, source["source_id"])
        assert updated["source_version"] != old_version
        assert updated["segment_summary"]["total"] == 3
        assert len(app.repository.ingestion_attempts(
            updated["workspace_id"], updated["source_id"], old_version
        )) == 4
        assert len(app.handle_get(f"/api/sources/{source['source_id']}/ingestion-attempts")["attempts"]) == 3
    finally:
        app.close()


def test_semantically_valid_fact_is_not_retried(tmp_path):
    provider = ScriptedProvider(fact_initial=True)
    app = app_for(tmp_path, provider)
    try:
        source = import_fixture(app)
        assert source["ingestion_status"] == "COMPLETE"
        assert source["segment_summary"]["retryable"] == 0
        assert provider.calls == Counter(initial=1, proposal=1, replacement=1)
        with pytest.raises(Exception, match="No failed segment"):
            retry(app, source["source_id"])
    finally:
        app.close()


def test_version_change_does_not_retry_or_attach_old_failed_attempt(tmp_path):
    provider = ScriptedProvider(malformed_replacement=1)
    app = app_for(tmp_path, provider)
    try:
        source = import_fixture(app)
        assert source["ingestion_status"] == "PARTIAL"
        old_version = source["source_version"]
        changed = FIXTURE.replace("vendor for the 2026 launch is Birchline", "vendor for the 2026 launch is Birchline today")
        response, status = app.handle_put(
            f"/api/sources/{source['source_id']}",
            {"name": "acceptance-meeting.txt", "content": changed},
        )
        assert status == 202 and response["changed"] is True
        current = wait_source(app, source["source_id"])
        assert current["source_version"] != old_version
        assert current["ingestion_status"] == "COMPLETE"
        assert len(app.repository.ingestion_attempts(
            source["workspace_id"], source["source_id"], old_version
        )) == 3
        current_attempts = app.handle_get(f"/api/sources/{source['source_id']}/ingestion-attempts")["attempts"]
        assert len(current_attempts) == 3
        assert all(item["source_version"] == current["source_version"] for item in current_attempts)
    finally:
        app.close()
