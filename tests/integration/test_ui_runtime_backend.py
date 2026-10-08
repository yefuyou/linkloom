from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import pytest
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunStatus
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.ui.backend import RuntimeRunBackend
from linkloom.ui.projection import WorkspacePresentationContext


@pytest.fixture
def ui_runtime_root() -> Path:
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"ui-runtime-{uuid4().hex}"
    root.mkdir(parents=True)
    return root


def _tool(request: ModelTurnRequest, tool_id: str, arguments: dict[str, object]) -> ModelAction:
    return ModelAction.tool(
        ToolCall(
            call_id=f"ui-{request.sequence}-{tool_id}",
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _model() -> FakeModelAdapter:
    def search(request: ModelTurnRequest) -> ModelAction:
        return _tool(
            request,
            "search_notes",
            {
                "query": "approved model provider",
                "source_context": {"intent": "recorded decision"},
                "limit": 5,
            },
        )

    def read(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        evidence = next(
            item
            for item in request.observation.value
            if " approved " in item["quote"]
        )
        return _tool(
            request,
            "read_verified_note",
            {"note_ref": evidence["evidence_id"]},
        )

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        evidence = request.observation.value
        match = re.search(r"approved (?P<provider>.+?) as", evidence["quote"])
        assert match is not None
        ref = evidence["evidence_id"]
        return ModelAction.final(
            json.dumps(
                {
                    "schema_version": "team-decision-result/v1",
                    "decision": {
                        "value": f"{match.group('provider')} is the approved model provider.",
                        "status": "approved",
                        "evidence_refs": [ref],
                    },
                    "rationale": [],
                    "rejected_alternatives": [],
                    "actions": [],
                    "unresolved_items": [],
                    "uncertainty": {
                        "status": "none",
                        "statement": None,
                        "unknown_fields": [],
                        "evidence_refs": [],
                    },
                    "evidence_refs": [ref],
                }
            )
        )

    return FakeModelAdapter([search, read, final])


def test_runtime_backend_projects_a_real_team_decision_run_without_source_writes(
    ui_runtime_root: Path,
) -> None:
    tmp_path = ui_runtime_root
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "decision.md"
    note.write_text(
        "# Meeting notes\n\n"
        "The team approved Aster A as the model provider for the pilot.\n",
        encoding="utf-8",
    )
    before = hashlib.sha256(note.read_bytes()).hexdigest()
    index = scan_vault(vault, tmp_path / "scan").index_path
    engine = RuntimeEngine(
        vault_root=vault,
        index_path=index,
        checkpoint_dir=tmp_path / "runtime",
        trace_dir=tmp_path / "traces",
        checkpointer=InMemoryCheckpointer(),
        model=_model(),
    )
    backend = RuntimeRunBackend(
        engine=engine,
        workspace=WorkspacePresentationContext(display_name="Test project", document_count=1),
        max_steps=3,
        max_provider_requests=3,
    )

    started = backend.start("Which model provider did the team approve?")
    deadline = time.monotonic() + 60
    snapshot = started
    while snapshot["kind"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = backend.inspect(started["run"]["id"])

    assert snapshot["kind"] == "success"
    assert snapshot["decision"]["value"] == "Aster A is the approved model provider."
    assert snapshot["decision"]["citations"] == ["citation-1"]
    assert snapshot["evidence"][0]["relative_path"] == "decision.md"
    assert snapshot["evidence"][0]["source"]["preview_kind"] == "document"
    assert hashlib.sha256(note.read_bytes()).hexdigest() == before

    with pytest.raises(ValueError, match="non-empty decision question"):
        backend.start("   ")

    unsafe_status = RunStatus(
        run_id="unsafe",
        thread_id="unsafe",
        status="completed",
        result_ref=r"C:\Windows\system32\config.json",
    )
    assert backend._safe_result_artifact(unsafe_status) is None
