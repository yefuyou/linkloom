"""Reusable synthetic D06 reviewed-change setup for relation evaluations."""

from contextlib import contextmanager
import re

from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
    SemanticExtractionContext,
)
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer, MaterializationBlockedError
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.validation import CandidateValidator

from tests.integration.test_task_observation_flow import RecordedProvider, claim, register


@contextmanager
def create_reviewed_change(tmp_path, artifact, registry, project, before, after):
    """Build a reviewed D06 change through the real approval lifecycle.

    The supplied artifact and registry remain the source of truth.  The
    yielded ``ChangeRef`` carries only durable decision/candidate/receipt IDs;
    the audit mapping contains setup references for callers that need to
    inspect the generated chain.
    """
    segments = parse_source_text(artifact).segments
    register(registry, artifact, segments)
    replies = {}
    for segment in segments:
        text = segment.body_text
        vendor = after if after in text else before if before in text else None
        if vendor is None:
            replies[text] = []
            continue
        date_match = re.search(r"(20\d{2})(?:-|年)(\d{1,2})(?:-|月)(\d{1,2})", text)
        valid_from = (
            f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
            if date_match else None
        )
        replies[text] = [claim(
            "DECISION", subject=project, relation="uses vendor", value=vendor,
            temporal_status="EXPLICIT", valid_from=valid_from,
        )]

    context = SemanticExtractionContext(
        artifact.workspace_id, FrozenRelationResolver(("uses vendor",))
    )
    provider = RecordedProvider(replies)
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="offline-task-change",
        model_id="m2-script",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="offline-m2", reservation_id="offline",
            estimated_cost_upper_bound_usd=0,
        ),
        request_guard_id="offline-m2",
        max_attempts=1,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    with TemporalDecisionStore(
        tmp_path / "d06-exact.sqlite", source_registry=registry
    ) as store:
        materializer = SemanticDecisionMaterializer(store)
        candidates = []
        for segment in segments:
            for result in pipeline.process_segment(segment, artifact, context):
                if result.candidate is None:
                    continue
                materializer.capture(result, expected_workspace_id=artifact.workspace_id)
                if result.candidate.claim_type.value == "DECISION":
                    candidates.append(result.candidate)
        candidates.sort(key=lambda candidate: candidate.valid_from)
        formal_before_review = len(store.list_decisions(artifact.workspace_id))
        assert formal_before_review == 0
        blocked_before_review = 0
        for candidate in candidates:
            fingerprint = materializer.candidate_fingerprint(
                artifact.workspace_id, candidate.candidate_id
            )
            try:
                materializer.materialize(artifact.workspace_id, candidate.candidate_id)
            except MaterializationBlockedError as error:
                assert error.reason_code == 'APPROVAL_MISSING'
                blocked_before_review += 1
            else:
                raise AssertionError('unreviewed source was materialized')
            materializer.approve(
                artifact.workspace_id,
                candidate.candidate_id,
                reviewer_id="offline-human",
                expected_candidate_fingerprint=fingerprint,
                reason="Synthetic D06 source checked",
            )
            materializer.materialize(artifact.workspace_id, candidate.candidate_id)

        if len(candidates) != 2:
            raise AssertionError("exact D06 setup must yield two decision candidates")
        old, current = store.get_history(
            artifact.workspace_id, candidates[0].subject_key
        )
        current_candidate = next(candidate for candidate in candidates if candidate.value == after)
        receipt = store.get_semantic_receipt_row(
            artifact.workspace_id, current_candidate.candidate_id
        )
        assert receipt is not None
        current_segment = next(segment for segment in segments if after in segment.body_text)
        span = current_segment.evidence_span
        change = ChangeRef(
            workspace_id=artifact.workspace_id,
            project=project,
            before_vendor=before,
            after_vendor=after,
            evidence=EvidenceQuote(
                span, span.char_start, span.char_end, span.quote, "CHANGE_CLAIM"
            ),
            kind="REVIEWED_DECISION",
            decision_id=current.decision_id,
            candidate_id=current_candidate.candidate_id,
            receipt_id=str(receipt["receipt_id"]),
        )
        yield store, change, {
            'formal_before_review': formal_before_review,
            'blocked_before_review': blocked_before_review,
            'old_decision_id': old.decision_id,
            'new_decision_id': current.decision_id,
            'supersedes_id': current.supersedes_id,
            'before_vendor': old.value,
            'after_vendor': current.value,
            'receipts': [store.get_semantic_receipt_row(artifact.workspace_id, c.candidate_id) for c in candidates],
            'approval_events': [e for c in candidates for e in store.get_semantic_candidate_events(artifact.workspace_id,c.candidate_id)
                                if e['event_type'] == 'HUMAN_APPROVAL'],
            'scripted_provider_calls': len(provider.requests),
            'real_provider_calls': 0,
        }


__all__ = ["create_reviewed_change"]
