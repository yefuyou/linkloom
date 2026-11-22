from dataclasses import replace
from datetime import UTC, date, datetime
import sqlite3

import pytest

from linkloom.decision_memory.models import DecisionTimeBasis, UndatedDecisionState
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.materialization import (
    CandidateReviewResolution,
    CandidateWorkflowState,
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
)
from tests.unit.test_semantic_ingestion_materialization import (
    _build_single_candidate,
    _claim,
    _registry_for_results,
    _review_and_materialize,
)

SOURCE = "[2026-10-02T09:15:00+08:00] Mira: We decided to use Birchline for launch."
NOW = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.fixture
def review(tmp_path):
    source, result, _ = _build_single_candidate(SOURCE, _claim("Birchline", temporal_status="NOT_STATED"))
    candidate = result.candidate
    registry = _registry_for_results(((result,),))
    with TemporalDecisionStore(tmp_path / "review.sqlite", source_registry=registry) as store:
        materializer = SemanticDecisionMaterializer(store)
        assessment = materializer.capture(result, expected_workspace_id=source.workspace_id, now=NOW)
        arguments = dict(current_source=source, time_basis=DecisionTimeBasis.UNKNOWN,
                         reviewer_id="reviewer", expected_candidate_fingerprint=assessment.candidate_fingerprint,
                         reason="The notes confirm the choice but no effective date", now=NOW)
        yield materializer, candidate, arguments


def test_explicit_undated_approval_is_atomic_and_retries_reuse_the_receipt(review):
    materializer, candidate, arguments = review
    store = materializer.store
    workspace, identifier = candidate.workspace_id, candidate.candidate_id
    with pytest.raises(MaterializationBlockedError, match="eligible") as blocked:
        materializer.approve(workspace, identifier, reviewer_id=arguments["reviewer_id"],
                             expected_candidate_fingerprint=arguments["expected_candidate_fingerprint"],
                             reason=arguments["reason"], now=NOW)
    assert blocked.value.reason_code == "TEMPORAL_UNRESOLVED"
    assert not store.list_undated_decisions(workspace)

    record, receipt = materializer.approve_undated(workspace, identifier, **arguments)
    assert materializer.approve_undated(workspace, identifier, **arguments) == (record, receipt)
    with pytest.raises(MaterializationBlockedError) as conflict:
        materializer.approve_undated(workspace, identifier, **{**arguments, "reviewer_id": "someone else"})
    assert conflict.value.reason_code == "UNDATED_REVIEW_CONFLICT"
    assert store.list_undated_decisions(workspace) == (record,)
    assert store.get_undated_decision_receipts(workspace, record.undated_decision_id) == (receipt,)
    assert (receipt.candidate_id, receipt.authorization_id, receipt.authorization_reason) == (
        identifier, arguments["reviewer_id"], arguments["reason"],
    )
    assert record.time_basis is DecisionTimeBasis.UNKNOWN
    assert record.source_evidence_refs == (candidate.evidence_ref,)
    assert store.get_semantic_candidate_row(workspace, identifier)["workflow_state"] == CandidateWorkflowState.MATERIALIZED
    approvals = [event for event in store.get_semantic_candidate_events(workspace, identifier)
                 if event["event_type"] == "HUMAN_APPROVAL"]
    assert len(approvals) == 1
    assert not store.list_decisions(workspace)
    state = store.lookup_decision_state(workspace, candidate.subject, candidate.relation, as_of=NOW)
    assert state.current_uncertain_due_to_undated
    assert state.undated_decisions == (record,)


def test_undated_review_rejects_stale_source_workspace_and_candidate(review):
    materializer, candidate, arguments = review
    workspace, identifier = candidate.workspace_id, candidate.candidate_id
    modified = replace(arguments["current_source"], content=SOURCE.replace("Birchline", "Wrenwell"))
    for workspace_id, patch, code in (
        ("other-workspace", {}, "WORKSPACE_MISMATCH"),
        (workspace, {"current_source": modified}, "PROVENANCE_INVALID"),
        (workspace, {"expected_candidate_fingerprint": "stale"}, "APPROVAL_STALE"),
        (workspace, {"time_basis": DecisionTimeBasis.USER_SELECTED}, "INVALID_VALID_TIME"),
    ):
        with pytest.raises(MaterializationBlockedError) as error:
            materializer.approve_undated(workspace_id, identifier, **{**arguments, **patch})
        assert error.value.reason_code == code

    materializer.reject(workspace, identifier, reviewer_id="reviewer",
                        expected_candidate_fingerprint=arguments["expected_candidate_fingerprint"],
                        reason="Rejected.", now=NOW)
    with pytest.raises(MaterializationBlockedError) as rejected:
        materializer.approve_undated(workspace, identifier, **arguments)
    assert rejected.value.reason_code == "UNDATED_REVIEW_CONFLICT"
    assert not materializer.store.list_undated_decisions(workspace)


def test_failed_audit_write_rolls_back_undated_record_and_candidate(review, monkeypatch):
    materializer, candidate, arguments = review
    store = materializer.store
    workspace, identifier = candidate.workspace_id, candidate.candidate_id

    def fail(**_):
        raise sqlite3.IntegrityError("test audit write failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_append_semantic_event_locked", fail)
        with pytest.raises(sqlite3.IntegrityError, match="audit write failure"):
            materializer.approve_undated(workspace, identifier, **arguments)
    assert not store.list_undated_decisions(workspace)
    assert store.get_semantic_candidate_row(workspace, identifier)["workflow_state"] == CandidateWorkflowState.PENDING_REVIEW
    materializer.approve_undated(workspace, identifier, **arguments)


def test_promotion_preserves_original_receipt_and_explicit_effective_boundary(review):
    materializer, candidate, arguments = review
    store = materializer.store
    workspace, identifier = candidate.workspace_id, candidate.candidate_id
    record, original = materializer.approve_undated(workspace, identifier, **arguments)
    promotion = dict(current_source=arguments["current_source"], valid_from=date(2026, 10, 9),
                     time_basis=DecisionTimeBasis.USER_SELECTED, reviewer_id="date-reviewer",
                     reason="The effective date was confirmed separately", now=NOW)
    modified = replace(promotion["current_source"], content=SOURCE + " Edited.")
    with pytest.raises(MaterializationBlockedError) as stale:
        materializer.promote_undated(workspace, record.undated_decision_id,
                                     **{**promotion, "current_source": modified})
    assert stale.value.reason_code == "PROVENANCE_INVALID"
    assert store.get_undated_decision_receipts(workspace, record.undated_decision_id) == (original,)

    dated, updated, receipt = materializer.promote_undated(workspace, record.undated_decision_id, **promotion)
    assert updated.decision_ref == original.decision_ref == receipt.decision_ref
    assert updated.memory_state is UndatedDecisionState.PROMOTED_TO_DATED
    assert set(store.get_undated_decision_receipts(workspace, record.undated_decision_id)) == {original, receipt}
    assert not store.lookup(workspace, candidate.subject, candidate.relation, as_of=NOW).records
    at_effective_date = store.lookup_decision_state(workspace, candidate.subject, candidate.relation,
                                                   as_of=datetime(2026, 10, 9, tzinfo=UTC), mode="AS_OF")
    assert at_effective_date.temporal_result.records == (dated,)
    assert not at_effective_date.undated_decisions
    with pytest.raises(MaterializationBlockedError) as repeated:
        materializer.promote_undated(workspace, record.undated_decision_id, **promotion)
    assert repeated.value.reason_code == "UNDATED_NOT_ACTIVE"
    assert store.list_decisions(workspace) == (dated,)


def test_ordinary_approval_with_a_date_still_writes_its_dated_receipt(review):
    materializer, candidate, _ = review
    result = _review_and_materialize(
        materializer, candidate.workspace_id, candidate.candidate_id,
        reviewer_id="reviewer", now=NOW, resolution=CandidateReviewResolution(valid_from=date(2026, 10, 3)),
    )
    assert result.workflow_state is CandidateWorkflowState.MATERIALIZED
    assert result.receipt is not None
    assert not materializer.store.list_undated_decisions(candidate.workspace_id)
