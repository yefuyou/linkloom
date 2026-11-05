"""Disabled graph matches the pre-graph Ask path with full response equality."""
import pytest
from linkloom.ui.unified_ask import UnifiedAskService
from unified_ask_fixture import UnifiedAskFixture, ATLAS, OTHER, SUBJECT, RELATION


@pytest.mark.parametrize("case", ["current", "historical", "unsupported", "scope_error", "permission"])
def test_off_preserves_legacy_responses_and_never_enters_graph_budget(tmp_path, monkeypatch, case):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    monkeypatch.setenv("LINKLOOM_GRAPH_QUERY", "off")
    fixture = UnifiedAskFixture(tmp_path / "app.sqlite")
    try:
        fixture.configure_agent(external_provider=True)
        provider = lambda: fixture.server.investigation_service
        disabled = UnifiedAskService(fixture.app, provider, graph_service=object())
        monkeypatch.setenv("LINKLOOM_GRAPH_QUERY", "on")
        baseline = UnifiedAskService(fixture.app, provider, graph_service=False)
        def forbidden(*args, **kwargs):
            raise AssertionError("legacy-only Ask must not enter graph budget")
        monkeypatch.setattr("linkloom.ui.graph_investigation.graph_request_budget", forbidden)
        request = dict(query="Who is the current supplier?", workspace_id=fixture.workspace_ids[ATLAS],
                       subject=SUBJECT, relation=RELATION, as_of="2026-10-10")
        if case == "historical": request["as_of"] = "2026-10-06"
        if case == "unsupported": request["query"] = "Summarize this workspace"
        if case == "scope_error": request["workspace_id"] = fixture.workspace_ids[OTHER]
        if case == "permission": request["query"] = "Why did the supplier change?"
        actual, status = disabled.ask(request)
        expected, expected_status = baseline.ask(request)
        assert (actual, status) == (expected, expected_status)
        assert status == (403 if case == "permission" else 409 if case == "scope_error" else 200)
        assert actual["decided_by"] != "graph"
        assert actual["agent_run_id"] is None
    finally:
        fixture.stop()
