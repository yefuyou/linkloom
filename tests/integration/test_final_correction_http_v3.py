"""Labelled offline model faults through genuine Unified Ask HTTP."""
import json

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from run_bounded_multihop import SourceOnlyController
from unified_investigation_fixture import UnifiedInvestigationFixture, RELATIONS, TWO
from unified_ask_fixture import request_json, poll_product_ask


def test_http_invalid_final_corrects_once_with_grounding_and_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = UnifiedInvestigationFixture(tmp_path / "v3-http.sqlite")
    fixture.configure_agent()
    fixture.select_workspace(RELATIONS)
    controller = SourceOnlyController()
    saved = []

    def respond(request):
        if saved:
            assert request.available_tools == []
            assert "Final" in request.recovery_guidance
            return saved[0]
        action = controller(request)
        if action.kind == "final":
            saved.append(action)
            return ModelAction.final("{truncated")
        return action

    model = FakeModelAdapter([respond] * 12)
    fixture.service.model_factory = lambda query: model
    try:
        status, body = request_json(fixture.base_url, "/api/product/ask", method="POST", data={
            "workspace_id": fixture.app._workspace_id(), "query": TWO,
        })
        assert status == 202
        _, body = poll_product_ask(fixture, body["result"]["agent_run_id"], fixture.app._workspace_id(), timeout=120)
        result = body["result"]
        assert result["status"] == "COMPLETED"
        assert result["reviewer"]["accepted"]
        assert result["source_investigation"]["answer"] == "陈宁"
        assert result["evidence"] and all(item["verified"] for item in result["evidence"])
        assert result["receipts"] == []
        assert saved and model.call_count <= 12
    finally:
        fixture.stop()


def test_http_wrong_workspace_tool_call_cannot_produce_answer(tmp_path, monkeypatch):
    from dataclasses import replace

    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    fixture = UnifiedInvestigationFixture(tmp_path / "v3-invalid-tool.sqlite")
    fixture.configure_agent()
    fixture.select_workspace(RELATIONS)
    controller = SourceOnlyController()

    injected = []

    def respond(request):
        if injected:
            return ModelAction.final("invalid final after rejected tool")
        action = controller(request)
        call = action.tool_call
        arguments = {**call.arguments, "source_context": {
            **call.arguments["source_context"], "workspace_id": "foreign-workspace",
        }}
        injected.append(arguments)
        return ModelAction.tool(replace(call, arguments=arguments))

    model = FakeModelAdapter([respond] * 3)
    fixture.service.model_factory = lambda query: model
    try:
        status, body = request_json(fixture.base_url, "/api/product/ask", method="POST", data={
            "workspace_id": fixture.app._workspace_id(), "query": TWO,
        })
        assert status == 202
        run_id = body["result"]["agent_run_id"]
        _, body = poll_product_ask(fixture, run_id, fixture.app._workspace_id(), timeout=120)
        result = body["result"]
        assert result["status"] == "FAILED"
        assert result["conclusions"] == [] and result["evidence"] == []
        job = fixture.service._jobs[run_id]
        snapshot = job.backend.inspect(run_id)
        state = job.backend.engine.checkpointer.get_latest(snapshot["run"]["thread_id"])
        assert injected[0]["source_context"]["workspace_id"] == "foreign-workspace"
        # Identity validation rejects before an execution record is created.
        assert state.tool_ledger == []
        assert result["error"]
        assert model.call_count <= 3
    finally:
        fixture.stop()
