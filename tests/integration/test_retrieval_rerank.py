"""Document reranking integration: bounded candidates and verified provenance."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import hashlib

import numpy as np
import pytest

from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.context import ContextManifest
from linkloom.indexing import BM25Index, DirectoryIndex, IndexUpdateCoordinator, SourceDocument, VectorIndex
from linkloom.rerank.base import NoopReranker, RerankError
from linkloom.retrieval_v2.models import RetrievedEvidence, RetrievalQuery
from linkloom.retrieval_v2.retrievers import HybridRetriever
from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
from linkloom.schemas import NoteDocument


class StaticRetriever:
    def __init__(self, hits):
        self.hits = hits

    def retrieve(self, query):
        return self.hits[:query.top_k]


class ReverseReranker:
    enabled = True

    def __init__(self):
        self.calls = []
        self.last_trace = {}

    def rerank(self, query, candidates):
        self.calls.append((query, deepcopy(candidates)))
        self.last_trace = {"mode": "local", "backend": "fake", "status": "ok",
                           "candidate_count": len(candidates), "attempts": []}
        return [replace(candidate.evidence, rerank_score=float(index),
                        metadata={**candidate.evidence.metadata, "rerank_backend": "fake"})
                for index, candidate in reversed(list(enumerate(candidates)))]


def _hit(index):
    text = f"Supplier approval document {index}."
    return RetrievedEvidence(
        evidence_id=f"e-{index:02d}", source_ref=f"doc-{index:02d}.md",
        logical_path=f"/doc-{index:02d}.md", score=40.0-index, rank=index+1,
        retrieval_channel="channel", resource_id=f"doc-{index:02d}.md",
        metadata={"workspace_id": "w", "content": text,
                  "content_sha256": hashlib.sha256(text.encode()).hexdigest()},
    )


def _retriever(reranker=None, text_provider=None):
    return HybridRetriever(
        StaticRetriever([_hit(i) for i in range(20)]),
        StaticRetriever([_hit(i) for i in range(20, 40)]),
        reranker=reranker, candidate_text_provider=text_provider,
    )


def test_only_rrf_top20_are_reranked_before_topk_and_tail_scores_survive():
    query = RetrievalQuery("w", "supplier approval", top_k=40)
    baseline = _retriever().retrieve(query)
    reranker = ReverseReranker()
    read_ids = []

    def text_provider(hit):
        read_ids.append(hit.evidence_id)
        return hit.metadata["content"]

    retriever = _retriever(reranker, text_provider)
    ranked = retriever.retrieve(query)
    assert [hit.evidence_id for hit in ranked[:20]] == [hit.evidence_id for hit in baseline[:20]][::-1]
    assert [hit.evidence_id for hit in ranked[20:]] == [hit.evidence_id for hit in baseline[20:]]
    assert read_ids == [hit.evidence_id for hit in baseline[:20]]
    assert len(reranker.calls[0][1]) == 20
    original = {hit.evidence_id: hit for hit in baseline}
    for rank, hit in enumerate(ranked, 1):
        assert hit.rank == rank
        assert hit.score == original[hit.evidence_id].score
        assert hit.metadata["channel_scores"] == original[hit.evidence_id].metadata["channel_scores"]
        assert hit.metadata["rrf_rank"] == original[hit.evidence_id].rank
    assert all(hit.rerank_score is not None for hit in ranked[:20])
    assert all(hit.rerank_score is None for hit in ranked[20:])
    assert retriever.last_rerank_trace["backend"] == "fake"
    assert retriever.last_stage_timings_ms["rerank_ms"] >= 0
    assert retriever.last_stage_timings_ms["fusion_ms"] >= 0

    # A caller asking for only 3 still scores the same RRF window first.
    top3 = _retriever(ReverseReranker()).retrieve(replace(query, top_k=3))
    assert [hit.evidence_id for hit in top3] == [hit.evidence_id for hit in baseline[:20]][::-1][:3]


def test_noop_and_empty_results_never_resolve_candidate_text():
    def forbidden(hit):
        raise AssertionError("off must not resolve source text")

    retriever = _retriever(NoopReranker(), forbidden)
    assert len(retriever.retrieve(RetrievalQuery("w", "supplier", top_k=5))) == 5
    assert retriever.last_stage_timings_ms["rerank_ms"] == 0.0
    fake = ReverseReranker()
    empty = HybridRetriever(StaticRetriever([]), StaticRetriever([]), reranker=fake,
                            candidate_text_provider=forbidden)
    assert empty.retrieve(RetrievalQuery("w", "supplier")) == []
    assert fake.calls == []


@pytest.mark.parametrize("defect", ["missing", "duplicate", "foreign", "nan", "source", "unscored"])
def test_malformed_or_source_replacing_batches_fall_back_atomically(defect):
    class Broken(ReverseReranker):
        def rerank(self, query, candidates):
            result = super().rerank(query, candidates)
            if defect == "missing":
                return result[:-1]
            if defect == "duplicate":
                return result[:-1] + result[:1]
            if defect == "foreign":
                result[0] = replace(result[0], evidence_id="foreign")
            elif defect == "nan":
                result[0] = replace(result[0], rerank_score=float("nan"))
            elif defect == "source":
                result[0].metadata["content_sha256"] = "forged"
            elif defect == "unscored":
                result = [replace(hit, rerank_score=None) for hit in result]
            return result

    query = RetrievalQuery("w", "supplier", top_k=25)
    expected = _retriever().retrieve(query)
    retriever = _retriever(Broken())
    actual = retriever.retrieve(query)
    assert actual == expected
    assert retriever.last_rerank_trace["status"] == "fallback"
    assert retriever.last_rerank_trace["reason"] == "invalid_result"


def test_mutating_failed_reranker_does_not_pollute_rrf_or_log_private_exception(caplog):
    class MutatingFailure:
        def rerank(self, query, candidates):
            candidates[0].evidence.metadata["channel_scores"]["bm25"] = 999
            raise RuntimeError("private document contents")

    query = RetrievalQuery("w", "supplier", top_k=25)
    retriever = _retriever(MutatingFailure())
    assert retriever.retrieve(query) == _retriever().retrieve(query)
    assert "private document contents" not in caplog.text
    assert retriever.last_rerank_trace["reason"] == "rerank_failed"


class TermEmbedder:
    def encode(self, texts):
        return np.asarray([[float(text.casefold().count("supplier")), 1.0] for text in texts],
                          dtype=np.float32)


def _note(path, text):
    return NoteDocument(relative_path=path, title=path.removesuffix(".md"), content=text,
                        headings=[], tags=[], wikilinks=[], size_bytes=len(text.encode()),
                        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        line_count=len(text.splitlines()))


@pytest.mark.parametrize("mode", ["hybrid", "directory_hybrid"])
def test_runtime_materializes_rerank_scores_without_replacing_verified_quote(mode):
    notes = [_note("a.md", "Supplier A is approved."), _note("b.md", "Supplier B is approved.")]
    baseline = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                      mode=mode, embedder=TermEmbedder(), reranker=NoopReranker())
    before = baseline.search("supplier approved", top_k=2)
    fake = ReverseReranker()
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                     mode=mode, embedder=TermEmbedder(), reranker=fake)
    after = backend.search("supplier approved", top_k=2)
    assert [item["relative_path"] for item in after.evidence] == [item["relative_path"] for item in before.evidence][::-1]
    originals = {item["relative_path"]: item for item in before.evidence}
    for item in after.evidence:
        original = originals[item["relative_path"]]
        assert item["quote"] == original["quote"]
        assert item["score"] == original["score"]
        assert item["evidence_id"] == original["evidence_id"]
        assert item["rerank_score"] is not None
        assert item["rrf_rank"] == original["rank"]
        assert item["status"] == "verified"
    assert all("rerank_score" not in item for item in before.evidence)
    assert after.observation.rerank_trace["backend"] == "fake"
    assert all(candidate.text in [f"{note.title}\n{note.content}" for note in notes]
               for candidate in fake.calls[0][1])


def _coordinator(notes):
    coordinator = IndexUpdateCoordinator(manifest=ContextManifest(), lexical_index=BM25Index(),
                                         vector_index=VectorIndex(TermEmbedder()),
                                         directory_index=DirectoryIndex(), index_version=3)
    for note in notes:
        coordinator.create(SourceDocument(workspace_id="w", resource_id=note.relative_path,
                           document_id=note.relative_path, logical_path=f"/{note.relative_path}",
                           content=note.content, source_timestamp=datetime(2026, 9, 1, tzinfo=UTC),
                           source_ref=f"fixture://w/{note.relative_path}", metadata={"title": note.title}))
    return coordinator


def test_coordinated_indexes_supply_current_text_but_stale_source_is_not_scored_or_exposed():
    notes = [_note("a.md", "Supplier A is approved."), _note("b.md", "Supplier B is approved.")]
    coordinator = _coordinator(notes)
    fake = ReverseReranker()
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                     embedder=TermEmbedder(), index_update_coordinator=coordinator,
                                     reranker=fake)
    result = backend.search("supplier approved", top_k=2)
    assert result.evidence and result.observation.rerank_trace["backend"] == "fake"
    assert {candidate.text for candidate in fake.calls[0][1]} == {note.content for note in notes}
    notes[0] = _note("a.md", "Supplier C is now approved.")
    stale = backend.search("supplier approved", top_k=2)
    assert len(fake.calls) == 1  # no partially prepared or stale batch reaches model
    assert stale.observation.rerank_trace["reason"] == "source_version_mismatch"
    assert all(item["relative_path"] != "a.md" for item in stale.evidence)


def test_runtime_text_resolver_rejects_foreign_workspace_and_forged_identity():
    notes = [_note("a.md", "Supplier A is approved.")]
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                     embedder=TermEmbedder(), reranker=NoopReranker())
    backend.search("supplier", top_k=1)
    retriever = backend._retrievers[backend.mode]
    hit = retriever.retrieve(RetrievalQuery("w", "supplier", top_k=1))[0]
    with pytest.raises(RerankError, match="source_workspace_mismatch"):
        backend._resolve_rerank_text(replace(hit, metadata={**hit.metadata, "workspace_id": "foreign"}))
    with pytest.raises(RerankError, match="source_identity_mismatch"):
        backend._resolve_rerank_text(replace(hit, source_ref="unrelated.md"))


def test_adapter_retains_rerank_score_when_constructing_context_evidence():
    adapter = object.__new__(RuntimeAgentAdapter)
    adapter.workspace_id = "w"
    value = {"relative_path": "a.md", "evidence_id": "e", "score": 0.03,
             "rank": 1, "quote": "Supplier A.", "rerank_score": 0.75,
             "retrieval_channel": "hybrid"}
    evidence = adapter._retrieved_evidence(value, rank=1)
    assert evidence.rerank_score == 0.75
    assert evidence.score == 0.03
    assert evidence.metadata["text"] == value["quote"]


@pytest.mark.parametrize("mode", ["hybrid", "directory_hybrid"])
def test_product_hybrid_resolves_configured_pipeline_once(monkeypatch, mode):
    fake = ReverseReranker()
    calls = []
    def factory():
        calls.append(True)
        return fake
    monkeypatch.setattr("linkloom.rerank.pipeline.build_reranker_from_env", factory)
    notes = [_note("a.md", "Supplier A is approved.")]
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                     mode=mode, embedder=TermEmbedder())
    backend.search("supplier", top_k=1)
    backend.search("supplier", top_k=1)
    assert calls == [True]
    assert len(fake.calls) == 2


@pytest.mark.parametrize("mode", ["bm25", "dense", "current"])
def test_other_retrieval_modes_do_not_construct_reranking_pipeline(monkeypatch, mode):
    def forbidden():
        raise AssertionError("document reranking applies only to RRF")
    monkeypatch.setattr("linkloom.rerank.pipeline.build_reranker_from_env", forbidden)
    notes = [_note("a.md", "Supplier A is approved.")]
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: notes,
                                     mode=mode, embedder=TermEmbedder())
    assert backend.search("supplier", top_k=1).evidence
