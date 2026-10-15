from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import numpy as np
import pytest

from linkloom.agents.model_adapter import is_verified_evidence_result
from linkloom.decision_memory.models import DecisionRecord, DecisionStatus
from linkloom.decision_memory.tool import DecisionMemorySearchTool
from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
from linkloom.schemas import NoteDocument
from linkloom.tools.contracts import ToolCall
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


class TermEmbedder:
    terms = ("supplier", "rollout", "budget")

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            [
                [text.casefold().count(term) for term in self.terms]
                for text in texts
            ],
            dtype=np.float32,
        )


class OneTimeLoadEmbedder(TermEmbedder):
    def __init__(self) -> None:
        self.model_load_total_ms = 0.0
        self.last_model_load_ms = 0.0
        self._loaded = False

    def encode(self, texts: list[str]) -> np.ndarray:
        if not self._loaded:
            self._loaded = True
            self.last_model_load_ms = 123.0
            self.model_load_total_ms += self.last_model_load_ms
        else:
            self.last_model_load_ms = 0.0
        return super().encode(texts)


def _note(path: str, content: str) -> NoteDocument:
    return NoteDocument(
        relative_path=path,
        title=path.rsplit("/", 1)[-1].removesuffix(".md"),
        content=content,
        headings=[],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        line_count=len(content.splitlines()),
    )


def test_runtime_hybrid_retrieval_returns_grounded_evidence_and_observability() -> None:
    documents = [
        _note("decisions/supplier.md", "Supplier B is the final supplier decision."),
        _note("actions/rollout.md", "Rina owns the rollout plan and budget review."),
    ]
    backend = RuntimeRetrievalBackend(
        workspace_id="workspace-1",
        document_provider=lambda: documents,
        mode="hybrid",
        embedder=TermEmbedder(),
        index_version=2,
    )

    result = backend.search("final supplier", top_k=2)

    assert result.evidence
    assert result.evidence[0]["relative_path"] == "decisions/supplier.md"
    assert result.evidence[0]["status"] == "verified"
    assert result.evidence[0]["retrieval_mode"] == "hybrid"
    assert result.evidence[0]["index_version"] == 2
    assert result.observation.retrieval_mode == "hybrid"
    assert result.observation.scope_path is None
    assert result.observation.candidate_count >= 1
    assert result.observation.top_k == 2
    assert result.observation.index_version == 2
    assert result.observation.retrieval_latency_ms >= 0


def test_runtime_retrieval_observation_decomposes_hybrid_stage_latency() -> None:
    backend = RuntimeRetrievalBackend(
        workspace_id="workspace-1",
        document_provider=lambda: [
            _note("decisions/supplier.md", "Supplier contract is approved.")
        ],
        mode="hybrid",
        embedder=TermEmbedder(),
    )

    result = backend.search("supplier contract", top_k=1)

    timings = result.observation.stage_timings_ms
    for name in (
        "total_retrieval_ms",
        "document_scan_ms",
        "index_setup_other_ms",
        "index_setup_entry_gap_ms",
        "index_setup_merge_ms",
        "index_setup_return_gap_ms",
        "index_setup_cpu_ms",
        "index_setup_total_ms",
        "bm25_index_build_ms",
        "directory_index_build_ms",
        "directory_ms",
        "bm25_ms",
        "embedding_model_load_ms",
        "query_embedding_model_load_ms",
        "document_embedding_ms",
        "vector_normalization_ms",
        "embedding_query_ms",
        "dense_search_ms",
        "fusion_ms",
        "retriever_other_ms",
        "retriever_total_ms",
        "materialization_ms",
        "other_ms",
    ):
        assert timings[name] >= 0
    assert timings["total_retrieval_ms"] == pytest.approx(
        result.observation.retrieval_latency_ms
    )
    setup_components = sum(
        timings[name]
        for name in (
            "bm25_index_build_ms",
            "directory_index_build_ms",
            "embedding_model_load_ms",
            "document_embedding_ms",
            "vector_normalization_ms",
        )
    )
    assert timings["index_setup_measured_components_ms"] == pytest.approx(
        setup_components,
        abs=0.01,
    )
    pipeline_total = sum(
        timings[name]
        for name in (
            "document_scan_ms",
            "index_setup_total_ms",
            "retriever_total_ms",
            "materialization_ms",
        )
    )
    assert pipeline_total == pytest.approx(timings["total_retrieval_ms"], abs=1.0)
    assert timings["other_ms"] < 1.0


def test_cold_model_load_timing_is_not_overwritten_by_query_load_timing() -> None:
    backend = RuntimeRetrievalBackend(
        workspace_id="workspace-1",
        document_provider=lambda: [
            _note("decisions/supplier.md", "Supplier contract is approved.")
        ],
        mode="hybrid",
        embedder=OneTimeLoadEmbedder(),
    )

    timings = backend.search("supplier contract", top_k=1).observation.stage_timings_ms

    assert timings["embedding_model_load_ms"] == pytest.approx(123.0)
    assert timings["query_embedding_model_load_ms"] == pytest.approx(0.0)


def test_current_runtime_retrieval_does_not_initialize_dense_embeddings() -> None:
    class ForbiddenEmbedder:
        def encode(self, texts: list[str]) -> np.ndarray:
            raise AssertionError("current retrieval must not initialize dense embeddings")

    backend = RuntimeRetrievalBackend(
        workspace_id="workspace-1",
        document_provider=lambda: [
            _note("decisions/supplier.md", "Supplier B is the final supplier decision.")
        ],
        mode="current",
        embedder=ForbiddenEmbedder(),
    )

    result = backend.search("final supplier", top_k=1)

    assert result.evidence[0]["relative_path"] == "decisions/supplier.md"
    assert result.observation.retrieval_mode == "current"


def test_hybrid_runtime_retrieval_reembeds_only_changed_documents() -> None:
    class RecordingEmbedder(TermEmbedder):
        def __init__(self) -> None:
            self.batches: list[list[str]] = []

        def encode(self, texts: list[str]) -> np.ndarray:
            self.batches.append(list(texts))
            return super().encode(texts)

    documents = [
        _note("decisions/supplier.md", "Supplier B is the final supplier decision."),
        _note("actions/rollout.md", "Rina owns the rollout plan and budget review."),
    ]
    embedder = RecordingEmbedder()
    backend = RuntimeRetrievalBackend(
        workspace_id="workspace-1",
        document_provider=lambda: documents,
        mode="hybrid",
        embedder=embedder,
    )

    backend.search("final supplier", top_k=2)
    documents[0] = _note(
        "decisions/supplier.md",
        "Supplier C is now the final supplier decision.",
    )
    backend.search("final supplier", top_k=2)

    assert embedder.batches[0] == [
        "supplier\nSupplier B is the final supplier decision.",
        "rollout\nRina owns the rollout plan and budget review.",
    ]
    assert embedder.batches[2] == [
        "supplier\nSupplier C is now the final supplier decision."
    ]


class RecordingDecisionStore:
    def __init__(self, records: tuple[DecisionRecord, ...] = ()) -> None:
        self.records = records
        self.calls: list[tuple[str, str, datetime | None, int]] = []

    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        as_of: datetime | None = None,
        limit: int = 5,
    ) -> tuple[DecisionRecord, ...]:
        self.calls.append((workspace_id, query, as_of, limit))
        return self.records


def _decision() -> DecisionRecord:
    return DecisionRecord(
        decision_id="decision-b",
        workspace_id="workspace-1",
        subject_key="supplier",
        value="Supplier B",
        status=DecisionStatus.CURRENT,
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_to=None,
        supersedes_id="decision-a",
        source_episode_id="episode:final",
        source_evidence_refs=("decisions/supplier.md#L1",),
        provenance_run_id="run:decision-b",
    )


def test_decision_memory_checks_workspace_before_store_lookup() -> None:
    store = RecordingDecisionStore((_decision(),))
    tool = DecisionMemorySearchTool(store, authorized_workspace_id="workspace-1")

    with pytest.raises(PermissionError, match="workspace"):
        tool.search(
            workspace="workspace-2",
            query="supplier",
            as_of=None,
            limit=5,
        )

    assert store.calls == []

    result = tool.search(
        workspace="workspace-1",
        query="supplier",
        as_of="2026-06-02T00:00:00+00:00",
        limit=5,
    )
    assert result.values[0]["decision_id"] == "decision-b"
    assert result.values[0]["source_evidence_refs"] == ["decisions/supplier.md#L1"]
    assert result.observation.memory_hit_count == 1
    assert result.observation.memory_latency_ms >= 0
    assert store.calls[0][0] == "workspace-1"


def test_memory_tool_is_provider_neutral_but_never_grounded_evidence() -> None:
    calls = []
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: [],
        lambda ref: {},
        decision_memory_executor=lambda query, as_of, limit: calls.append(
            (query, as_of, limit)
        ) or [{
                "decision_id": "decision-b",
                "subject_key": "supplier",
                "value": "Supplier B",
                "source_evidence_refs": ["decisions/supplier.md#L1"],
            }],
    )
    definition, _ = runtime.registry.resolve("search_decision_memory")
    assert definition.input_schema["required"] == ["query", "limit"]
    assert "workspace" not in definition.input_schema["properties"]
    assert "as_of" in definition.input_schema["properties"]

    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_decision_memory"],
            denied_tool_ids=["write_file", "rename_file", "read_gold", "raw_filesystem"],
            max_calls=1,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )
    result = runtime.execute(
        ToolCall(
            call_id="memory-call",
            tool_id="search_decision_memory",
            arguments={
                "query": "supplier",
                "limit": 5,
            },
        ),
        policy,
    )

    assert result.status == "ok"
    assert result.value[0]["decision_id"] == "decision-b"
    assert is_verified_evidence_result(result) is False
    assert calls == [("supplier", None, 5)]


def test_mps001_memory_workspace_mismatch_is_reproduced_offline_with_safe_diagnostic() -> None:
    store = RecordingDecisionStore()
    strict_tool = DecisionMemorySearchTool(
        store,
        authorized_workspace_id="root_09068b08f248fb8f",
    )
    runtime = create_retrieval_tool_runtime(
        lambda query, source_context, limit: [],
        lambda ref: {},
        decision_memory_executor=lambda query, as_of, limit: strict_tool.search(
            workspace="Atlas Lantern pilot",
            query=query,
            as_of=as_of,
            limit=limit,
        ),
    )
    policy = ToolPolicyEnforcer(
        ToolCallPolicy(
            policy_version="p4-readonly-tools-v1",
            agent_id="retrieval_agent",
            allowed_tool_ids=["search_decision_memory"],
            denied_tool_ids=[],
            max_calls=1,
            network="deny",
            vault_write="deny",
            gold_access="deny",
        )
    )

    result = runtime.execute(
        ToolCall(
            call_id="mps001-memory-mismatch",
            tool_id="search_decision_memory",
            arguments={
                "query": "approved model provider for Atlas Lantern pilot",
                "limit": 20,
            },
        ),
        policy,
    )

    assert result.status == "error"
    diagnostic = result.error.details["failure_diagnostic"]
    assert diagnostic["exception_type"] == "PermissionError"
    assert diagnostic["location"]["file"] == "tool.py"
    assert diagnostic["location"]["function"] == "search"
    assert diagnostic["exception_chain"] == [
        {"relationship": "raised", "exception_type": "PermissionError"}
    ]
    assert store.calls == []
