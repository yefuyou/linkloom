"""RED regression tests for the final evidence trust boundary.

The first test preserves the original offline E07 inputs and model actions:
Final requires a successful read. Known counterevidence is checked against
Host-confirmed facts (see tests/unit/test_claim_grounding.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.graph import RuntimeEngine
from linkloom.tools.contracts import ToolCall

from tests.integration.test_runtime_agent_memory_capture import (
    _decision_json,
    _result,
    _runtime_fixture,
)


def _close_engine_store(engine: RuntimeEngine) -> None:
    store = getattr(engine, "_decision_memory_store", None)
    if store is not None:
        store.close()


def test_e07_search_only_final_cannot_be_trusted(tmp_path: Path):
    """Original E07: search evidence alone must not receive a trusted Final."""

    folder = tmp_path / "search_only"
    folder.mkdir()

    def search(request):
        context = json.loads(
            request.user_input.rsplit("team_decision_request=", 1)[1]
        )["source_context"]
        return ModelAction.tool(
            ToolCall(
                call_id="find",
                tool_id="search_notes",
                arguments={
                    "query": "Acme supplier",
                    "source_context": context,
                    "limit": 5,
                },
            )
        )

    def final(request):
        passage = next(
            item for item in request.observation.value if "Acme" in item["quote"]
        )
        return ModelAction.final(_decision_json(passage["evidence_id"]))

    model = FakeModelAdapter([search, final])
    _, _, engine, request = _runtime_fixture(folder, model)
    try:
        status = engine.start_multi_agent(request)
        result = _result(folder, status.run_id)

        ledger = result.get("tool_ledger", [])
        assert not any(
            row.get("tool_id") == "read_verified_note"
            and row.get("status") == "completed"
            for row in ledger
        )
        assert not (
            result.get("status") == "completed"
            and result.get("review", {}).get("decision") == "evidence_sufficient"
            and result.get("result", {})
            .get("team_decision", {})
            .get("decision", {})
            .get("status")
            == "approved"
        ), result
    finally:
        _close_engine_store(engine)


def test_search_then_read_remains_a_valid_trusted_path(tmp_path: Path):
    """Control: a successful read followed by Final remains acceptable."""

    folder = tmp_path / "search_then_read"
    folder.mkdir()

    def search(request):
        context = json.loads(
            request.user_input.rsplit("team_decision_request=", 1)[1]
        )["source_context"]
        return ModelAction.tool(
            ToolCall(
                call_id="find",
                tool_id="search_notes",
                arguments={
                    "query": "Acme supplier",
                    "source_context": context,
                    "limit": 5,
                },
            )
        )

    def read(request):
        passage = next(
            item for item in request.observation.value if "Acme" in item["quote"]
        )
        return ModelAction.tool(
            ToolCall(
                call_id="read",
                tool_id="read_verified_note",
                arguments={"note_ref": passage["evidence_id"]},
            )
        )

    def final(request):
        return ModelAction.final(_decision_json(request.observation.value["evidence_id"]))

    model = FakeModelAdapter([search, read, final])
    _, _, engine, request = _runtime_fixture(folder, model)
    try:
        status = engine.start_multi_agent(request)
        result = _result(folder, status.run_id)

        assert status.status == result["status"] == "completed", result
        assert result["review"]["decision"] == "evidence_sufficient"
        assert result["result"]["team_decision"]["decision"]["status"] == "approved"
        assert any(
            row.get("tool_id") == "read_verified_note"
            and row.get("status") == "completed"
            for row in result["tool_ledger"]
        )
    finally:
        _close_engine_store(engine)
