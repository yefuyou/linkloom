"""Offline qualification for the separate M1.2b memory-live-001 coverage case.

This fixture is synthetic, is not part of the frozen semantic benchmark, and
never invokes a Provider. It exercises the product SQLite memory and Runtime V2
tool path with a deterministic model adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re

import numpy as np
import pytest

from linkloom.agents import runtime_adapter as adapter_module
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.context.assembler import ContextSourceType
from linkloom.decision_memory import (
    DecisionMaterializer,
    DecisionRecord,
    DecisionStatus,
    SourceReferenceRegistry,
    TemporalDecisionStore,
)
from linkloom.loader import VaultReader
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


QUERY = "What is the current approved provider, and what source confirms it?"
SUBJECT = "approved provider"
SOURCE_REF = "final-approval.md"
FIXTURE_VAULT = Path(__file__).resolve().parents[1] / "fixtures" / "memory-live-001" / "vault"


class FixtureEmbedder:
    """Small deterministic embedding stand-in; no model download or API use."""

    TERMS = ("provider", "approved", "current", "decision", "source", "historical")

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [
                [text.casefold().split().count(term) for term in self.TERMS]
                for text in texts
            ],
            dtype=np.float32,
        )


def _final_json(value: str, evidence_ref: str) -> str:
    return json.dumps(
        {
            "schema_version": "team-decision-result/v1",
            "decision": {
                "value": value,
                "status": "approved",
                "evidence_refs": [evidence_ref],
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
            "evidence_refs": [evidence_ref],
        },
        ensure_ascii=False,
    )


def _tool(request, tool_id: str, arguments: dict[str, object]) -> ModelAction:
    return ModelAction.tool(
        ToolCall(
            call_id=f"m12b-{request.sequence}-{tool_id}",
            tool_id=tool_id,
            arguments=arguments,
        )
    )


def _workspace(tmp_path: Path) -> tuple[Path, Path, str, str]:
    vault = tmp_path / "vault"
    import shutil

    shutil.copytree(FIXTURE_VAULT, vault)
    index = scan_vault(vault, tmp_path / "scan").index_path
    reader = VaultReader(vault_root=vault, index_path=index)
    source = next(doc for doc in reader.read_notes() if doc.relative_path == SOURCE_REF)
    assert "Provider B" in source.content
    return vault, index, reader.vault_root_fingerprint, source.content_sha256


def _seed_memory(tmp_path: Path, vault: Path, index: Path, workspace_id: str, source_hash: str):
    registry = SourceReferenceRegistry()
    registry.register_episode(workspace_id, SOURCE_REF)
    registry.register_evidence(workspace_id, SOURCE_REF, content_hash=source_hash)
    store = TemporalDecisionStore(tmp_path / "temporal.sqlite", source_registry=registry)
    decision = DecisionRecord(
        decision_id="memory-live-001-current-provider",
        workspace_id=workspace_id,
        subject_key=SUBJECT,
        value="Provider B",
        status=DecisionStatus.CURRENT,
        valid_from=datetime(2026, 5, 12, tzinfo=UTC),
        valid_to=None,
        supersedes_id=None,
        source_episode_id=SOURCE_REF,
        source_evidence_refs=(SOURCE_REF,),
        provenance_run_id="memory-live-001-deterministic-approved-fixture",
    )
    # Seed only after the same strict contract/grounding and source-reference
    # checks used by the existing deterministic materialization path.
    TeamDecisionResult.from_grounded_final(_final_json("Provider B", SOURCE_REF), [SOURCE_REF])
    candidate = DecisionMaterializer(store).propose(
        decision,
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    DecisionMaterializer(store).materialize(
        candidate,
        approved_by="explicit-memory-live-001-test-fixture",
    )
    assert store.get_current(workspace_id, SUBJECT).decision_id == decision.decision_id
    return store


def _adapter(vault: Path, index: Path, store: TemporalDecisionStore) -> RuntimeAgentAdapter:
    return RuntimeAgentAdapter(
        vault,
        index,
        retrieval_mode="hybrid",
        decision_memory_store=store,
        embedder=FixtureEmbedder(),
    )


def test_memory_live_001_proves_hybrid_ambiguity_and_memory_information_gain(tmp_path: Path):
    vault, index, workspace_id, source_hash = _workspace(tmp_path)
    store = _seed_memory(tmp_path, vault, index, workspace_id, source_hash)
    try:
        adapter = _adapter(vault, index, store)
        assert adapter.workspace_id == workspace_id
        assert adapter._decision_memory_tool.authorized_workspace_id == workspace_id

        retrieval = adapter._retrieval_backend.search(QUERY, top_k=5)
        assert retrieval.observation.retrieval_mode == "hybrid"
        assert len(retrieval.evidence) >= 2
        ranked_quotes = [item["quote"] for item in retrieval.evidence]
        # Flat hybrid retrieval exposes both the superseded A decision and the
        # later B approval; its ranked evidence has no temporal state field.
        assert any("Provider A" in quote and "historical" in quote.casefold() for quote in ranked_quotes)
        assert any("Provider B" in quote for quote in ranked_quotes)
        assert all("memory_state" not in item and "supersedes_id" not in item for item in retrieval.evidence)

        memory_hits = adapter._search_decision_memory("approved provider", None, 5)
        assert len(memory_hits) == 1
        memory = memory_hits[0]
        assert memory["workspace_id"] == workspace_id
        assert memory["status"] == "CURRENT"
        assert memory["value"] == "Provider B"
        assert memory["source_evidence_refs"] == [SOURCE_REF]
        assert memory["grounding_role"] == "navigation_only"
        assert store.get_current(workspace_id, SUBJECT).memory_state.value == "ACTIVE"
        assert any(
            item.source_type is ContextSourceType.DECISION_MEMORY
            and item.item_id == memory["decision_id"]
            for item in adapter._last_context_bundle.selected
        )
        assert adapter._last_context_bundle.estimated_tokens <= adapter._context_assembler.budget.max_tokens

        # The memory ref must be used to locate, then actually read, the source
        # evidence. Reading the same opaque evidence ID keeps source identity.
        evidence = adapter._search_notes("Provider B final approval May 12", {}, 10)
        final_record = next(item for item in evidence if item["relative_path"] == SOURCE_REF)
        assert len({item["evidence_id"] for item in evidence}) == len(evidence)
        first_read = adapter._read_verified_note(final_record["evidence_id"])
        second_read = adapter._read_verified_note(final_record["evidence_id"])
        assert first_read["relative_path"] == second_read["relative_path"] == SOURCE_REF
        assert first_read["content_sha256"] == second_read["content_sha256"] == source_hash
        assert first_read["quote"] == second_read["quote"] and "Provider B" in first_read["quote"]

        # A memory hint alone is not an observed source citation and cannot
        # satisfy the TeamDecision grounding contract.
        with pytest.raises(ValidationError, match="not observed"):
            TeamDecisionResult.from_grounded_final(
                _final_json("Provider B", SOURCE_REF),
                [],
            )

        with pytest.raises(PermissionError, match="not authorized"):
            adapter._decision_memory_tool.search(
                workspace="another-workspace",
                query="approved provider",
                as_of=None,
                limit=5,
            )
        wrong_scope = adapter._context_assembler.assemble(
            workspace_id="another-workspace",
            query=QUERY,
            decision_memory=(store.get_current(workspace_id, SUBJECT),),
        )
        assert not any(item.source_type is ContextSourceType.DECISION_MEMORY for item in wrong_scope.selected)
        assert any(item.reason.value == "OUT_OF_SCOPE" for item in wrong_scope.dropped)
    finally:
        store.close()


def test_memory_live_001_runtime_uses_memory_then_reads_source_before_grounded_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    vault, index, workspace_id, source_hash = _workspace(tmp_path)
    store = _seed_memory(tmp_path, vault, index, workspace_id, source_hash)
    adapter = _adapter(vault, index, store)
    model_observations: dict[str, object] = {}

    def search_memory(request):
        assert request.observation is None
        return _tool(request, "search_decision_memory", {"query": "approved provider", "limit": 5})

    def search_source(request):
        assert request.observation is not None and request.observation.status == "ok"
        memory = request.observation.value[0]
        assert memory["status"] == "CURRENT" and memory["value"] == "Provider B"
        assert memory["source_evidence_refs"] == [SOURCE_REF]
        model_observations["memory_result"] = memory
        memory_context = adapter._last_context_bundle
        assert memory_context is not None
        model_observations["memory_context_selected"] = [
            item.item_id for item in memory_context.selected
            if item.source_type is ContextSourceType.DECISION_MEMORY
        ]
        model_observations["memory_context_tokens"] = memory_context.estimated_tokens
        # The targeted source query is derived from the returned memory value
        # and provenance path, not from evaluator data.
        source_hint = Path(memory["source_evidence_refs"][0]).stem.replace("-", " ")
        return _tool(request, "search_notes", {
            "query": f"{memory['value']} {source_hint} May 12",
            "source_context": {"intent": "verify memory provenance in source evidence"},
            "limit": 10,
        })

    def read_source(request):
        assert request.observation is not None and request.observation.status == "ok"
        result = request.observation.value
        source = next(item for item in result if item["relative_path"] == SOURCE_REF)
        model_observations["retrieved_source"] = source
        return _tool(request, "read_verified_note", {"note_ref": source["evidence_id"]})

    def final_from_observed_source(request):
        assert request.observation is not None and request.observation.status == "ok"
        readback = request.observation.value
        assert readback["relative_path"] == SOURCE_REF
        assert readback["content_sha256"] == source_hash
        assert "Provider B" in readback["quote"]
        model_observations["readback"] = readback
        model_observations["final_evidence_context"] = request.evidence_context
        match = re.search(r"Provider ([A-Z])", readback["quote"])
        assert match is not None
        return ModelAction.final(_final_json(f"Provider {match.group(1)}", readback["evidence_id"]))

    model = FakeModelAdapter([
        search_memory,
        search_source,
        read_source,
        final_from_observed_source,
    ])
    real_adapter = adapter_module.RuntimeAgentAdapter
    constructions = []

    def bind_to_seeded_workspace(actual_vault, actual_index, *args, **kwargs):
        assert Path(actual_vault).resolve() == vault.resolve()
        assert Path(actual_index).resolve() == index.resolve()
        constructions.append((actual_vault, actual_index))
        return adapter

    monkeypatch.setattr(adapter_module, "RuntimeAgentAdapter", bind_to_seeded_workspace)
    runtime_root = tmp_path / "runtime"
    engine = RuntimeEngine(
        vault,
        index,
        runtime_root / "checkpoints",
        trace_dir=runtime_root / "traces",
        model=model,
    )
    try:
        status = engine.start_multi_agent(RunRequest(
            request_id="memory-live-001",
            thread_id="memory-live-001",
            workflow="team_decision",
            query=QUERY,
            max_steps=8,
            max_provider_requests=8,
            dry_run=True,
        ))
        state = engine.checkpointer.get_latest(status.thread_id)
        assert status.status == "completed", status.error
        assert state is not None and state.status == "completed"
        assert len(constructions) >= 1
        assert model.call_count == 4

        tool_ids = [record.tool_id for record in state.tool_ledger]
        assert tool_ids.index("search_decision_memory") < tool_ids.index("search_notes")
        assert tool_ids.index("search_notes") < tool_ids.index("read_verified_note")
        results_by_tool = {
            record.tool_id: record.result.get("value")
            for record in state.tool_ledger
            if record.result is not None
        }
        assert results_by_tool["search_decision_memory"][0]["status"] == "CURRENT"
        assert results_by_tool["search_notes"]
        assert results_by_tool["read_verified_note"]["relative_path"] == SOURCE_REF

        result_path = runtime_root / "checkpoints" / "results" / status.run_id / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        final_payload = result["result"]["team_decision"]
        observed_ref = model_observations["retrieved_source"]["evidence_id"]
        checked = TeamDecisionResult.from_grounded_final(
            json.dumps(final_payload, ensure_ascii=False),
            [observed_ref],
        )
        assert checked.decision["value"] == "Provider B"
        assert checked.decision["evidence_refs"] == [observed_ref]
        assert model_observations["readback"]["evidence_id"] == observed_ref
        assert model_observations["memory_context_selected"] == ["memory-live-001-current-provider"]
        assert model_observations["memory_context_tokens"] <= adapter._context_assembler.budget.max_tokens
        assert adapter._last_context_bundle is not None
        assert adapter._last_context_bundle.estimated_tokens <= adapter._context_assembler.budget.max_tokens
        assert any(item.source_type is ContextSourceType.EVIDENCE for item in adapter._last_context_bundle.selected)
        assert adapter.workspace_id == adapter._decision_memory_tool.authorized_workspace_id == workspace_id
        assert all("expected_" not in json.dumps(request.to_dict(), ensure_ascii=False) for request in model.requests)
    finally:
        store.close()
        # Avoid retaining the monkeypatch-bound adapter after this test.
        monkeypatch.setattr(adapter_module, "RuntimeAgentAdapter", real_adapter)
