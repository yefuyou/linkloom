"""Offline Final-contract communication through the production Agent path."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

import pytest
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


@pytest.fixture
def offline_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"final-contract-{uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _engine(root: Path, model: FakeModelAdapter) -> RuntimeEngine:
    vault = root / "vault"
    vault.mkdir()
    (vault / "decision.md").write_text(
        "# Meeting notes\n\n"
        "The team approved Quartz Index as the archive search engine.\n",
        encoding="utf-8",
    )
    index_path = scan_vault(vault, root / "scan").index_path
    return RuntimeEngine(
        vault_root=vault,
        index_path=index_path,
        checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces",
        checkpointer=InMemoryCheckpointer(),
        model=model,
    )


def _search(request: ModelTurnRequest) -> ModelAction:
    assert request.observation is None
    assert "team_decision_final_schema=" in request.user_input
    assert '"unknown_fields":[]' in request.user_input
    return ModelAction.tool(
        ToolCall(
            call_id="contract-search",
            tool_id="search_notes",
            arguments={
                "query": "approved archive search engine",
                "source_context": {"intent": "recorded decision"},
                "limit": 5,
            },
        )
    )


def _read(request: ModelTurnRequest) -> ModelAction:
    assert request.observation is not None and request.observation.status == "ok"
    evidence = next(
        item for item in request.observation.value if " approved " in item["quote"]
    )
    return ModelAction.tool(
        ToolCall(
            call_id="contract-read",
            tool_id="read_verified_note",
            arguments={"note_ref": evidence["evidence_id"]},
        )
    )


def _final_from_read(request: ModelTurnRequest, *, omit_unknown_fields: bool) -> ModelAction:
    assert request.observation is not None and request.observation.status == "ok"
    evidence = request.observation.value
    match = re.search(r"approved (?P<decision>.+?) as the archive", evidence["quote"])
    assert match is not None
    uncertainty = {
        "status": "none",
        "statement": None,
        "unknown_fields": [],
        "evidence_refs": [],
    }
    if omit_unknown_fields:
        uncertainty.pop("unknown_fields")
    ref = evidence["evidence_id"]
    return ModelAction.final(
        json.dumps(
            {
                "schema_version": "team-decision-result/v1",
                "decision": {
                    "value": match.group("decision"),
                    "status": "approved",
                    "evidence_refs": [ref],
                },
                "rationale": [],
                "rejected_alternatives": [],
                "actions": [],
                "unresolved_items": [],
                "uncertainty": uncertainty,
                "evidence_refs": [ref],
            }
        )
    )


def _run(root: Path, *, omit_unknown_fields: bool):
    model = FakeModelAdapter(
        [
            _search,
            _read,
            lambda request: _final_from_read(
                request,
                omit_unknown_fields=omit_unknown_fields,
            ),
        ]
    )
    engine = _engine(root, model)
    status = engine.start_multi_agent(
        RunRequest(
            request_id="offline-final-contract",
            thread_id="offline-final-contract-thread",
            workflow="team_decision",
            query="Which archive search engine did the team approve?",
            max_steps=3,
            max_provider_requests=3,
            dry_run=True,
        )
    )
    state = engine.checkpointer.get_latest(status.thread_id)
    assert state is not None
    result = json.loads(
        (root / "checkpoints" / "results" / status.run_id / "result.json").read_text(
            encoding="utf-8"
        )
    )
    return status, state, model, result


def test_search_tool_result_complete_final_is_accepted_and_grounded(
    offline_root: Path,
) -> None:
    status, state, model, result = _run(offline_root, omit_unknown_fields=False)

    assert status.status == state.status == "completed"
    assert [record.tool_id for record in state.tool_ledger] == ["search_notes", "read_verified_note"]
    assert model.call_count == 3
    assert result["result"] is not None
    payload = result["result"]["team_decision"]
    assert payload["decision"]["value"] == "Quartz Index"
    assert payload["uncertainty"]["unknown_fields"] == []
    observed_refs = {state.tool_ledger[1].result["value"]["evidence_id"]}
    assert set(payload["evidence_refs"]) <= observed_refs


def test_search_tool_result_final_missing_required_nested_field_fails_closed(
    offline_root: Path,
) -> None:
    status, state, model, result = _run(offline_root, omit_unknown_fields=True)

    assert status.status == state.status == "failed"
    assert state.error is not None
    assert state.error.code == "TEAM_DECISION_CONTRACT_ERROR"
    assert result["result"] is None
    assert [record.tool_id for record in state.tool_ledger] == ["search_notes", "read_verified_note"]
    assert model.call_count == 3
