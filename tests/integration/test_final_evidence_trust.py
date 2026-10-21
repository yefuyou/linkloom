"""RED regression tests for the final evidence trust boundary.

The first two tests preserve the original offline E07/E15 inputs and model
actions.  They intentionally fail on the current baseline until Final
requires a successful read and accounts for known read counterevidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from linkloom.agents.claim_grounding import assess_claim_text
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
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


def test_e15_known_read_counterevidence_cannot_be_trusted(tmp_path: Path):
    """Original E15: a Final citing only one side of two reads must be rejected."""

    folder = tmp_path / "known_conflict"
    folder.mkdir()
    vault = folder / "vault"
    vault.mkdir()
    (vault / "positive.md").write_text(
        "Mira Chen is the accountable owner of Lantern Plan.\n", encoding="utf-8"
    )
    (vault / "negative.md").write_text(
        "Mira Chen is not the accountable owner of Lantern Plan.\n", encoding="utf-8"
    )

    refs: dict[str, str] = {}

    def search(request):
        context = json.loads(
            request.user_input.rsplit("team_decision_request=", 1)[1]
        )["source_context"]
        return ModelAction.tool(
            ToolCall(
                call_id="find",
                tool_id="search_notes",
                arguments={
                    "query": "Lantern Plan owner",
                    "source_context": context,
                    "limit": 5,
                },
            )
        )

    def read_positive(request):
        for item in request.observation.value:
            refs["negative" if "is not" in item["quote"] else "positive"] = item[
                "evidence_id"
            ]
        assert set(refs) == {"positive", "negative"}, refs
        return ModelAction.tool(
            ToolCall(
                call_id="positive",
                tool_id="read_verified_note",
                arguments={"note_ref": refs["positive"]},
            )
        )

    def read_negative(request):
        return ModelAction.tool(
            ToolCall(
                call_id="negative",
                tool_id="read_verified_note",
                arguments={"note_ref": refs["negative"]},
            )
        )

    def final(request):
        value = json.loads(_decision_json(refs["positive"]))
        value["decision"]["value"] = (
            "Mira Chen is the accountable owner of Lantern Plan."
        )
        value["rationale"] = []
        return ModelAction.final(json.dumps(value))

    model = FakeModelAdapter([search, read_positive, read_negative, final])
    engine = RuntimeEngine(
        vault_root=vault,
        index_path=scan_vault(vault, folder / "scan").index_path,
        checkpoint_dir=folder / "checkpoints",
        trace_dir=folder / "traces",
        model=model,
    )
    request = RunRequest(
        request_id="known-conflict",
        thread_id="known-conflict-thread",
        workflow="team_decision",
        query="Who is the accountable owner of Lantern Plan?",
        max_steps=7,
        max_provider_requests=7,
        dry_run=True,
    )
    try:
        status = engine.start_multi_agent(request)
        result = _result(folder, status.run_id)

        reads = [
            row
            for row in result.get("tool_ledger", [])
            if row.get("tool_id") == "read_verified_note"
            and row.get("status") == "completed"
        ]
        assert len(reads) == 2, result
        both = {row["result"]["value"]["evidence_id"]: row["result"]["value"] for row in reads}
        assessment = assess_claim_text(
            "Mira Chen is the accountable owner of Lantern Plan.",
            list(both),
            both,
        )
        assert assessment.status.value == "CONTRADICTED"

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
