from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from linkloom.agents.registry import create_default_registry
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter, RuntimeAgentEventSink
from linkloom.decision_memory import (
    DecisionMaterializer,
    DecisionRecord,
    DecisionStatus,
    SourceReferenceRegistry,
    TemporalDecisionStore,
)
from linkloom.context import ChangeType, ContextManifest, FileChangeEvent
from linkloom.context.assembler import ContextAssembler, ContextBudget
from linkloom.indexing import (
    BM25Index,
    DirectoryIndex,
    IndexUpdateCoordinator,
    SourceDocument,
    VectorIndex,
)
from linkloom.loader import VaultReader
from linkloom.retrieval_v2 import RetrievalMode
from linkloom.scanner import scan_vault


class ScannerEmbedder:
    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [[text.casefold().count("scanner")] for text in texts],
            dtype=np.float32,
        )


class RecordingTracer:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event_type: str, status: str, **kwargs):
        self.events.append(
            {
                "event_type": event_type,
                "status": status,
                "actor": kwargs.get("actor"),
                "attributes": kwargs.get("attributes") or {},
            }
        )


def _vault(tmp_path: Path) -> tuple[Path, Path]:
    project_root = Path(__file__).resolve().parents[2]
    vault_root = tmp_path / "vault"
    shutil.copytree(project_root / "tests" / "fixtures" / "sample_vault", vault_root)
    scan = scan_vault(vault_root, tmp_path / "scan")
    return vault_root, scan.index_path


@pytest.fixture
def integration_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"retrieval-v2-agent-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def test_runtime_adapter_defaults_to_hybrid_and_emits_retrieval_trace(
    integration_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINKLOOM_RETRIEVAL_MODE", raising=False)
    vault_root, index_path = _vault(integration_root)
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        embedder=ScannerEmbedder(),
    )
    tracer = RecordingTracer()
    adapter._integration_event_sink = RuntimeAgentEventSink(tracer)

    evidence = adapter._search_notes("Scanner", {"workspace_id": "untrusted"}, 2)

    assert adapter._retrieval_backend.mode is RetrievalMode.HYBRID
    assert evidence
    assert all(item["retrieval_mode"] == "hybrid" for item in evidence)
    assert all(item["index_version"] == 2 for item in evidence)
    assert adapter._validate_evidence(evidence[0]["evidence_id"])["status"] == "pass"
    event = tracer.events[-1]
    assert event["event_type"] == "retrieval.completed"
    assert event["attributes"]["retrieval_mode"] == "hybrid"
    assert event["attributes"]["top_k"] == 2
    assert event["attributes"]["candidate_count"] >= 1
    assert event["attributes"]["index_version"] == 2


def test_runtime_adapter_applies_context_budget_before_exposing_search_results(
    integration_root: Path,
) -> None:
    vault_root, index_path = _vault(integration_root)
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        retrieval_mode="current",
        embedder=ScannerEmbedder(),
        context_assembler=ContextAssembler(
            budget=ContextBudget(max_evidence=1, max_memory=0, max_experience=0)
        ),
    )
    tracer = RecordingTracer()
    adapter._integration_event_sink = RuntimeAgentEventSink(tracer)

    exposed = adapter._search_notes("Scanner", {}, 5)

    context_event = next(
        event for event in tracer.events if event["event_type"] == "context.assembled"
    )
    assert len(exposed) == 1
    assert context_event["attributes"]["selected_count"] == 2  # query + one source
    assert context_event["attributes"]["dropped_count"] >= 1
    assert context_event["attributes"]["estimated_tokens"] > 0
    assert context_event["attributes"]["selected_items"][-1]["source_type"] == "evidence"
    assert all("quote" not in item for item in context_event["attributes"]["selected_items"])


def test_runtime_adapter_exposes_scoped_memory_navigation_with_trace(
    integration_root: Path,
) -> None:
    vault_root, index_path = _vault(integration_root)
    registry = SourceReferenceRegistry(
        episodes={"workspace-1": {"episode:final"}},
        evidence={"workspace-1": {"decisions/supplier.md#L1"}},
    )
    store = TemporalDecisionStore(":memory:", source_registry=registry)
    materializer = DecisionMaterializer(store)
    decision = DecisionRecord(
        decision_id="decision-b",
        workspace_id="workspace-1",
        subject_key="supplier",
        value="Supplier B",
        status=DecisionStatus.CURRENT,
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_to=None,
        supersedes_id=None,
        source_episode_id="episode:final",
        source_evidence_refs=("decisions/supplier.md#L1",),
        provenance_run_id="run:decision-b",
    )
    materializer.materialize(
        materializer.propose(
            decision,
            team_decision_contract_pass=True,
            grounding_pass=True,
        ),
        approved_by="fixture:integration",
    )
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        retrieval_mode="current",
        decision_memory_store=store,
        workspace_id="workspace-1",
        embedder=ScannerEmbedder(),
    )
    tracer = RecordingTracer()
    adapter._integration_event_sink = RuntimeAgentEventSink(tracer)

    values = adapter._search_decision_memory("supplier", None, 5)

    assert values[0]["decision_id"] == "decision-b"
    assert values[0]["grounding_role"] == "navigation_only"
    event = tracer.events[-1]
    assert event["event_type"] == "decision_memory.completed"
    assert event["attributes"]["memory_hit_count"] == 1
    assert event["attributes"]["memory_latency_ms"] >= 0
    assert values[0]["workspace_id"] == adapter.workspace_id
    capabilities = create_default_registry(
        include_decision_memory=True
    ).get_agent("retrieval_agent").capabilities
    assert capabilities == [
        "search_notes",
        "read_verified_note",
        "search_decision_memory",
    ]


def test_mps001_retrieved_evidence_ids_round_trip_to_same_source_and_version(
    integration_root: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    frozen_workspace = (
        project_root
        / "docs"
        / "requirements"
        / "m1_team_decision_eval_seed"
        / "workspaces"
        / "model_provider_selection"
    )
    vault_root = integration_root / "mps-001-vault"
    shutil.copytree(frozen_workspace, vault_root)
    index_path = scan_vault(vault_root, integration_root / "mps-001-scan").index_path
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        retrieval_mode="hybrid",
        embedder=ScannerEmbedder(),
    )

    raw_candidates = adapter._retrieval_backend.search(
        "Atlas Lantern pilot approved model provider",
        top_k=20,
    ).evidence
    assert len(raw_candidates) == 6
    assert len({item["evidence_id"] for item in raw_candidates}) == 6

    evidence = adapter._search_notes(
        "Atlas Lantern pilot approved model provider",
        {"vault_root_fingerprint": adapter.workspace_id},
        20,
    )

    assert len(evidence) >= 2
    assert len({item["evidence_id"] for item in evidence}) == len(evidence)
    for item in evidence:
        assert item["source_ref"] == item["relative_path"]
        context_evidence = adapter._retrieved_evidence(item, rank=item["rank"])
        assert context_evidence.source_ref == item["source_ref"]
        read_back = adapter._read_verified_note(item["evidence_id"])
        assert read_back["relative_path"] == item["relative_path"]
        assert read_back["content_sha256"] == item["content_sha256"]
        assert read_back["quote"] == item["quote"]


def test_stale_evidence_id_cannot_resolve_to_updated_current_source(
    integration_root: Path,
) -> None:
    vault_root, index_path = _vault(integration_root)
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        retrieval_mode="current",
        embedder=ScannerEmbedder(),
    )
    original = adapter._search_notes("Scanner", {}, 1)[0]
    source_path = vault_root / original["relative_path"]
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\nUpdated after retrieval.\n",
        encoding="utf-8",
        newline="\n",
    )
    updated_index = scan_vault(vault_root, integration_root / "updated-scan").index_path
    adapter.reader = VaultReader(vault_root=vault_root, index_path=updated_index)

    with pytest.raises(ValueError, match="no longer matches"):
        adapter._read_verified_note(original["evidence_id"])


def test_evidence_id_is_workspace_scoped_and_never_resolves_in_another_workspace(
    integration_root: Path,
) -> None:
    workspace_a = integration_root / "workspace-a"
    workspace_b = integration_root / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    vault_a, index_a = _vault(workspace_a)
    vault_b, index_b = _vault(workspace_b)
    adapter_a = RuntimeAgentAdapter(
        vault_a,
        index_a,
        retrieval_mode="current",
        workspace_id="workspace-a",
        embedder=ScannerEmbedder(),
    )
    adapter_b = RuntimeAgentAdapter(
        vault_b,
        index_b,
        retrieval_mode="current",
        workspace_id="workspace-b",
        embedder=ScannerEmbedder(),
    )

    evidence_a = adapter_a._search_notes("Scanner", {}, 1)[0]
    evidence_b = adapter_b._search_notes("Scanner", {}, 1)[0]

    assert evidence_a["evidence_id"] != evidence_b["evidence_id"]
    with pytest.raises(ValueError, match="was not found"):
        adapter_b._read_verified_note(evidence_a["evidence_id"])


def test_runtime_retrieval_uses_event_updated_context_indexes(
    integration_root: Path,
) -> None:
    workspace_id = "workspace-event-loop"
    vault_root = integration_root / "event-vault"
    vault_root.mkdir()
    note_path = vault_root / "supplier.md"
    original = "Supplier A is selected for the supplier rollout.\n"
    note_path.write_text(original, encoding="utf-8", newline="\n")
    index_path = scan_vault(vault_root, integration_root / "event-scan").index_path

    coordinator = IndexUpdateCoordinator(
        manifest=ContextManifest(),
        lexical_index=BM25Index(),
        vector_index=VectorIndex(ScannerEmbedder()),
        directory_index=DirectoryIndex(index_version=9),
        index_version=9,
    )
    timestamp = datetime(2026, 9, 1, tzinfo=UTC)
    before = SourceDocument(
        workspace_id=workspace_id,
        resource_id="supplier.md",
        document_id="supplier.md",
        logical_path="/supplier.md",
        content=original,
        source_timestamp=timestamp,
        source_ref="fixture://workspace-event-loop/supplier.md",
        metadata={"title": "Decision"},
    )
    coordinator.create(before)
    adapter = RuntimeAgentAdapter(
        vault_root,
        index_path,
        retrieval_mode="hybrid",
        workspace_id=workspace_id,
        embedder=ScannerEmbedder(),
        index_update_coordinator=coordinator,
    )

    initial = adapter._search_notes("Supplier A selected rollout", {}, 5)
    assert initial, (
        "the shared indexes should provide a candidate; lexical hits="
        f"{coordinator.lexical_index.search('Supplier A selected rollout', workspace_id=workspace_id)}"
    )
    assert any("Supplier A" in item["quote"] for item in initial), initial

    updated = "Supplier B is now selected for the supplier rollout.\n"
    note_path.write_text(updated, encoding="utf-8", newline="\n")
    after = SourceDocument(
        workspace_id=workspace_id,
        resource_id="supplier.md",
        document_id="supplier.md",
        logical_path="/supplier.md",
        content=updated,
        source_timestamp=datetime(2026, 9, 2, tzinfo=UTC),
        source_ref=before.source_ref,
        metadata={"title": "Decision"},
    )
    refreshed_scan = scan_vault(vault_root, integration_root / "event-scan-updated")
    adapter.reader = VaultReader(vault_root, refreshed_scan.index_path)
    unreconciled = adapter._search_notes("Supplier B selected rollout", {}, 5)

    result = coordinator.apply(
        FileChangeEvent(
            event_type=ChangeType.MODIFY,
            workspace_id=workspace_id,
            logical_path="/supplier.md",
            content_hash=after.content_hash,
            timestamp=after.source_timestamp,
        ),
        after,
    )

    current = adapter._search_notes("Supplier B selected rollout", {}, 5)
    obsolete = adapter._search_notes("Supplier A selected rollout", {}, 5)

    assert result.affected_indexes == ("lexical", "vector", "directory", "manifest")
    assert unreconciled == []
    assert any("Supplier B" in item["quote"] for item in current)
    assert all("Supplier A" not in item["quote"] for item in current + obsolete)
