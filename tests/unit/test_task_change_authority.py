"""P2 authority checks over the existing semantic approval lifecycle."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
    SemanticExtractionContext,
)
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tools.contracts import ToolCall
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.task_change_authority import verify_change_authority

from tests.integration.test_task_observation_flow import (
    RecordedProvider,
    claim,
    original_case,
    prepare,
    register,
)
from tests.task_change_support import create_reviewed_change


@contextmanager
def reviewed_change(tmp_path):
    content, replies = original_case()
    artifact, segments, context, _provider, pipeline = prepare(
        content, replies, source_id="A.md"
    )
    registry = SourceReferenceRegistry()
    register(registry, artifact, segments)
    with TemporalDecisionStore(tmp_path / "d06.sqlite", source_registry=registry) as store:
        materializer = SemanticDecisionMaterializer(store)
        decision_candidates = []
        for segment in segments:
            for result in pipeline.process_segment(segment, artifact, context):
                if result.candidate is None:
                    continue
                materializer.capture(result, expected_workspace_id=artifact.workspace_id)
                if result.candidate.claim_type.value == "DECISION":
                    decision_candidates.append(result.candidate)
        decision_candidates.sort(key=lambda candidate: candidate.valid_from)
        for candidate in decision_candidates:
            fingerprint = materializer.candidate_fingerprint(
                artifact.workspace_id, candidate.candidate_id
            )
            materializer.approve(
                artifact.workspace_id,
                candidate.candidate_id,
                reviewer_id="offline-human",
                expected_candidate_fingerprint=fingerprint,
                reason="Synthetic D06 source checked",
            )
            materializer.materialize(artifact.workspace_id, candidate.candidate_id)

        old, current = store.get_history("ws-m1", decision_candidates[0].subject_key)
        current_candidate = next(
            candidate
            for candidate in decision_candidates
            if candidate.value == current.value
        )
        current_receipt = store.get_semantic_receipt_row(
            artifact.workspace_id, current_candidate.candidate_id
        )
        assert current_receipt is not None
        change_span = segments[2].evidence_span
        change = ChangeRef(
            workspace_id=artifact.workspace_id,
            project="星河项目",
            before_vendor="云桥科技",
            after_vendor="星帆通信",
            evidence=EvidenceQuote(
                change_span,
                change_span.char_start,
                change_span.char_end,
                change_span.quote,
                "CHANGE_CLAIM",
            ),
            kind="REVIEWED_DECISION",
            decision_id=current.decision_id,
            candidate_id=current_candidate.candidate_id,
            receipt_id=str(current_receipt["receipt_id"]),
        )
        yield store, registry, (artifact,), change, old, current, current_candidate


def test_reviewed_change_accepts_real_receipt_and_exposes_chain(tmp_path):
    with reviewed_change(tmp_path) as (
        store,
        registry,
        artifacts,
        change,
        old,
        current,
        _candidate,
    ):
        before = store.get_semantic_receipt_row(change.workspace_id, change.candidate_id)
        errors, checks, extra_evidence = verify_change_authority(
            change, store, registry, artifacts
        )

        assert errors == set()
        assert "REVIEWED_DECISION_CHAIN_REQUIRED" in checks
        assert "HUMAN_APPROVAL_BOUND" in checks
        assert {quote.span.evidence_ref for quote in extra_evidence} == set(
            (*old.source_evidence_refs, *current.source_evidence_refs)
        )
        assert before == store.get_semantic_receipt_row(
            change.workspace_id, change.candidate_id
        )


def test_source_claim_is_explicitly_non_formal_and_refs_cannot_be_forged(tmp_path):
    with reviewed_change(tmp_path) as (store, registry, artifacts, reviewed, *_):
        source_claim = replace(reviewed, kind="SOURCE_CLAIM", decision_id=None,
                               candidate_id=None, receipt_id=None)
        errors, checks, extra_evidence = verify_change_authority(
            source_claim, store, registry, artifacts
        )

        assert "AUTHORITY_NOT_ESTABLISHED" in errors
        assert checks == ("SOURCE_CLAIM_ONLY",)
        assert extra_evidence == ()
        with pytest.raises(ValueError, match="source claim has no approval authority"):
            replace(source_claim, receipt_id="forged-receipt")


def test_reviewed_change_requires_the_referenced_receipt(tmp_path):
    with reviewed_change(tmp_path) as (store, registry, artifacts, reviewed, *_):
        forged = replace(reviewed, receipt_id="semantic_receipt_v1_forged")
        errors, _checks, _extra = verify_change_authority(
            forged, store, registry, artifacts
        )

        assert "AUTHORITY_RECEIPT_MISSING" in errors


def test_stale_source_evidence_blocks_reviewed_authority(tmp_path):
    with reviewed_change(tmp_path) as (
        store,
        registry,
        artifacts,
        change,
        *_,
    ):
        registry.register_evidence(
            change.workspace_id,
            change.evidence.span.evidence_ref,
            content_hash="0" * 64,
            source_version="artifact_v1_stale",
        )
        errors, _checks, _extra = verify_change_authority(
            change, store, registry, artifacts
        )

        assert "STALE_EVIDENCE" in errors


def test_revoked_candidate_cannot_keep_reviewed_authority(tmp_path):
    with reviewed_change(tmp_path) as (
        store,
        registry,
        artifacts,
        change,
        *_rest,
        candidate,
    ):
        fingerprint = str(
            store.get_semantic_candidate_row(
                change.workspace_id, candidate.candidate_id
            )["candidate_fingerprint"]
        )
        assert store.transition_semantic_candidate(
            workspace_id=change.workspace_id,
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=fingerprint,
            expected_states=("MATERIALIZED",),
            new_state="INVALIDATED",
            event_type="HUMAN_RETRACTION",
            actor_type="HUMAN",
            actor_id="offline-human",
            event_at=current_time(),
            reason_code="MANUAL_RETRACTION",
            reason="Synthetic authority retraction",
        )

        errors, _checks, _extra = verify_change_authority(
            change, store, registry, artifacts
        )

        assert "CANDIDATE_REVOKED" in errors


def current_time():
    from datetime import datetime

    return datetime.now(UTC)


def test_existing_store_prevents_receipt_fingerprint_mutation(tmp_path):
    import sqlite3
    with reviewed_change(tmp_path) as (store, registry, artifacts, change, *_):
        with pytest.raises(sqlite3.IntegrityError, match='receipts are immutable'):
            store._connection.execute('UPDATE semantic_materialization_receipt SET receipt_fingerprint = ? WHERE receipt_id = ?',
                                      ('0' * 64, change.receipt_id))
        errors, _, _ = verify_change_authority(change, store, registry, artifacts)
        assert not errors


def test_authority_checks_work_with_sql_writes_denied(tmp_path):
    import sqlite3
    with reviewed_change(tmp_path) as (store, registry, artifacts, change, *_):
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        store._connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        try:
            errors, _, _ = verify_change_authority(change, store, registry, artifacts)
            assert not errors
        finally:
            store._connection.set_authorizer(None)
