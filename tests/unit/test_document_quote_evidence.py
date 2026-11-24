"""Exact ordinary-document evidence through offline extraction and human review."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.candidate_models import CandidateOutcome, CandidateReasonCode
from linkloom.semantic_ingestion.document_text import (
    parse_source_text, persist_document_quote_evidence, resolve_source_evidence,
)
from linkloom.semantic_ingestion.extraction import (
    ProductTaskFieldContext, ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext,
)
from linkloom.semantic_ingestion.materialization import MaterializationBlockedError, SemanticDecisionMaterializer
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.store import TaskStore
from linkloom.ui.product import ProductRepository
from tests.unit.test_product_task_fields import A
from tests.unit.test_product_task_stages import Stages, annotations, core
from tests.unit.test_semantic_ingestion_materialization import _review_and_materialize


def decision(subject, value, **extra):
    return dict(claim_type="DECISION", subject=subject, relation="uses vendor", value=value,
                temporal_status="EXPLICIT", valid_from="2026-10-04", valid_to=None,
                confidence=dict.fromkeys(("claim_type", "entity", "relation", "temporal", "overall"), .95),
                **extra)


def extract(artifact, claims, *fields, enabled=True):
    provider = Stages(claims, *fields)
    context = SemanticExtractionContext(
        artifact.workspace_id, FrozenRelationResolver(("uses vendor",)),
        product_task_fields=ProductTaskFieldContext(artifact), document_quote_evidence=enabled,
    )
    extractor = ProviderNeutralSemanticExtractor(
        provider, provider_id="offline", model_id="quote-fixture",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="test", reservation_id="test"),
        request_guard_id="test",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    segment, = parse_source_text(artifact).segments
    result = pipeline.process_segment_with_trace(segment, artifact, context)
    replay = pipeline.replay_extraction(result.extraction.to_dict(), segment, artifact,
                                        replace(context, document_quote_evidence=not enabled))
    assert replay.candidate_results == result.candidate_results
    return segment, result, provider


@pytest.fixture
def saved_source(tmp_path):
    path = tmp_path / "quotes.sqlite"
    with ExitStack() as stack:
        repository = ProductRepository(path)
        stack.callback(repository.close)
        workspace = repository.create_workspace("Evidence")["workspace_id"]
        registry = SourceReferenceRegistry()
        store = TemporalDecisionStore(path, source_registry=registry)
        stack.callback(store.close)

        def save(content):
            artifact = RawArtifact(
                workspace_id=workspace, artifact_id="minutes", source_type="timestamped_text",
                content=content, source_uri=f"linkloom://{workspace}/minutes",
            )
            segment, = parse_source_text(artifact).segments
            span = segment.evidence_span
            repository.create_source(workspace, artifact.artifact_id, "Minutes", artifact, (span,))
            registry.register_episode(workspace, source_episode_id_for(artifact))
            registry.register_evidence(workspace, span.evidence_ref, content_hash=artifact.content_hash,
                                       source_version=artifact.artifact_version_id)
            return artifact

        def capture(artifact, results):
            persist_document_quote_evidence(repository._connection, registry, artifact,
                                            (item.candidate.provenance for item in results))
            materializer = SemanticDecisionMaterializer(store)
            for item in results:
                materializer.capture(item, expected_workspace_id=workspace)
            return materializer

        yield path, repository, store, save, capture


def test_two_decisions_share_context_but_formal_records_keep_distinct_exact_quotes(saved_source):
    _, repository, store, save, capture = saved_source
    first = "Launch uses Northwind.\r\n\r\nEffective date: 2026-10-04"
    second = "Storage uses Contoso. Effective date: 2026-10-04"
    artifact = save(f"🚀 正式会议\r\n\r\n{first}\r\n\r\n{second}\r\n背景说明")
    segment, trace, provider = extract(artifact, [
        decision("Launch", "Northwind", evidence_quote=first),
        decision("Storage", "Contoso", evidence_quote=second),
    ])
    assert len(provider.requests) == 1 and "背景说明" in provider.requests[0].user_input
    materializer = capture(artifact, trace.candidate_results)
    workspace = artifact.workspace_id
    for result, quote, lines in zip(trace.candidate_results, (first, second), ((3, 5), (7, 7))):
        candidate = result.candidate
        assert candidate.segment_id == segment.segment_id
        span = candidate.provenance
        assert (span.char_start, span.char_end) == (artifact.content.index(quote), artifact.content.index(quote) + len(quote))
        assert (span.line_start, span.line_end) == lines
        assert span.quote_sha256 == hashlib.sha256(quote.encode()).hexdigest()
        reviewed = _review_and_materialize(materializer, workspace, candidate.candidate_id,
                                           reviewer_id="reviewer", now=artifact.ingestion_time)
        assert store.get_decision_evidence(workspace, reviewed.receipt.decision_id) == (span.evidence_ref,)
        row = repository.evidence_source(workspace, span.evidence_ref)
        assert resolve_source_evidence(artifact, row) == span
        assert resolve_source_evidence(artifact, dict(row, quote_sha256="0" * 64)) is None
        assert not span.verify(replace(artifact, content=artifact.content + "修订"))


def test_ambiguous_missing_and_unsupported_quotes_stay_pending_and_cannot_be_approved(saved_source):
    _, _, store, save, capture = saved_source
    repeated = "Launch uses Northwind. Effective date: 2026-10-04"
    artifact = save(f"最终确认：{repeated}\n背景：{repeated}\n其他讨论")
    segment, trace, provider = extract(artifact, [
        decision("Launch", "Northwind", evidence_quote=repeated),
        decision("Launch", "Northwind", evidence_quote="最终确认：其他讨论"),
        decision("Launch", "Northwind", evidence_quote="其他讨论"),
        decision("Launch", "Northwind"),
        decision("Launch", "Northwind", evidence_quote=f"最终确认：{repeated}"),
    ])
    assert len(provider.requests) == 1
    unresolved = trace.candidate_results[:-1]
    assert trace.candidate_results[-1].candidate.provenance.quote == f"最终确认：{repeated}"
    materializer = capture(artifact, unresolved)
    for result in unresolved:
        assert result.outcome is CandidateOutcome.UNRESOLVED
        assert CandidateReasonCode.EVIDENCE_UNRESOLVED in result.reason_codes
        assert result.candidate.provenance == segment.evidence_span
        identifier = result.candidate.candidate_id
        with pytest.raises(MaterializationBlockedError) as blocked:
            materializer.approve(artifact.workspace_id, identifier, reviewer_id="reviewer", reason="test",
                expected_candidate_fingerprint=materializer.candidate_fingerprint(artifact.workspace_id, identifier))
        assert "EVIDENCE_UNRESOLVED" in blocked.value.reason_codes
    assert store.list_decisions(artifact.workspace_id) == ()


def test_two_stage_task_quote_survives_formal_task_creation_and_source_proof(saved_source):
    path, repository, _, save, capture = saved_source
    artifact = save(f"会前背景\n\n{A}\n其他讨论")
    _, trace, provider = extract(artifact, [core(A)], annotations(A))
    assert len(provider.requests) == 2
    candidate = trace.candidate_results[0].candidate
    observation = candidate.task_observation
    assert observation.provenance == candidate.provenance and observation.verify(artifact)
    capture(artifact, trace.candidate_results)
    workspace = artifact.workspace_id
    row = repository.evidence_source(workspace, candidate.evidence_ref)
    assert resolve_source_evidence(artifact, row) == observation.provenance
    tasks = TaskStore(path)
    try:
        source = LegacyTaskCandidateSource()
        service = TaskReviewService(tasks, source)
        preview = service.preview_candidate(workspace, observation.observation_id)
        value = service.confirm(workspace, observation.observation_id,
                                expected_candidate_fingerprint=preview["fingerprint"], reviewer="reviewer", reason="test")
        event, = tasks.get_events(workspace, value["task"]["id"])
        reference, = event.evidence
        assert (reference.quote, reference.char_start, reference.line_start) == (A, artifact.content.index(A), 3)
        with tasks._transaction():
            assert source.version_proof(tasks._connection, workspace, artifact.artifact_id,
                                        artifact.artifact_version_id, evidence=event.evidence, require_current=True)
    finally:
        tasks.close()


def test_existing_document_receipt_and_timestamped_spans_replay_unchanged(saved_source):
    _, _, _, save, _ = saved_source
    doc = save("Notes\nLaunch uses Northwind. Effective date: 2026-10-04")
    claim = decision("Launch", "Northwind")
    whole, historical, legacy_provider = extract(doc, [claim], enabled=False)
    assert "evidence_quote" not in historical.extraction.to_dict()["claims"][0]
    assert historical.candidate_results[0].candidate.provenance == whole.evidence_span
    transcript = replace(doc, content="[2026-10-08T10:00:00+08:00] Host: " + doc.content.split("\n")[1])
    stamped, trace, provider = extract(transcript, [claim])
    assert provider.requests[0].available_tools[0].input_schema == legacy_provider.requests[0].available_tools[0].input_schema
    assert trace.candidate_results[0].candidate.provenance == stamped.evidence_span
    assert stamped.evidence_span.char_start > 0
