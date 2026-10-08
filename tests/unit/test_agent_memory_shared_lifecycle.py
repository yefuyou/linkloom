from __future__ import annotations

from datetime import UTC, date, datetime
import json

import pytest

from linkloom.agents.memory_candidate import (
    AgentDecisionClaimProposal,
    AgentMemoryBuildStatus,
    AgentMemoryResolutionContext,
    AgentRunEvidence,
    AgentRunEvidenceCatalog,
    MemoryCandidateBuilder,
    WorkspaceSubject,
    WorkspaceSubjectRegistry,
)
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory.models import TemporalLookupStatus
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.materialization import (
    CandidateReviewResolution,
    CandidateWorkflowState,
    MaterializationBlockedError,
    SemanticDecisionMaterializer,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text


WORKSPACE = "workspace:agent-memory"
RUN_ID = "run:agent-memory:e2e"
CAPTURED_AT = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _build_candidate(
    content: str = (
        "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
        "Acme as supplier for Launch."
    ),
    *,
    artifact_id: str = "agent-meeting",
    subject_phrase: str = "Launch",
    relation_phrase: str = "supplier",
    status: str = "approved",
    uncertainty_status: str = "none",
    decision_value: str = "Acme",
    run_id: str = RUN_ID,
    action_values: list[dict[str, object]] | None = None,
    register_subject: bool = True,
    subject_registry_subjects: tuple[WorkspaceSubject, ...] | None = None,
):
    artifact = RawArtifact(
        workspace_id=WORKSPACE,
        artifact_id=artifact_id,
        source_type="timestamped_text",
        content=content,
        ingestion_time=CAPTURED_AT,
    )
    segment = parse_timestamped_text(artifact).segments[0]
    evidence = AgentRunEvidence(artifact, segment)
    evidence_ref = evidence.evidence_ref
    actions = []
    for item in action_values or []:
        action = dict(item)
        action["evidence_refs"] = [evidence_ref]
        actions.append(action)
    result = TeamDecisionResult(
        decision={"value": decision_value, "status": status, "evidence_refs": [evidence_ref]},
        rationale=[{"point": "The source records the selected supplier.", "evidence_refs": [evidence_ref]}],
        rejected_alternatives=[
            {"alternative": "Beta", "reason": "The source rejects it.", "evidence_refs": [evidence_ref]}
        ],
        actions=actions,
        unresolved_items=[],
        uncertainty={
            "status": uncertainty_status,
            "statement": "" if uncertainty_status != "none" else None,
            "unknown_fields": ["supplier"] if uncertainty_status != "none" else [],
            "evidence_refs": [evidence_ref] if uncertainty_status != "none" else [],
        },
        evidence_refs=[evidence_ref],
    )
    registry = SourceReferenceRegistry(
        episodes={WORKSPACE: [source_episode_id_for(artifact)]},
        evidence={WORKSPACE: [evidence_ref]},
        evidence_hashes={WORKSPACE: {evidence_ref: artifact.content_hash}},
        evidence_versions={WORKSPACE: {evidence_ref: artifact.artifact_version_id}},
    )
    subjects = (
        subject_registry_subjects
        if subject_registry_subjects is not None
        else ((WorkspaceSubject.create(WORKSPACE, "Launch"),) if register_subject else ())
    )
    subject_registry = WorkspaceSubjectRegistry(WORKSPACE, subjects)
    proposal = AgentDecisionClaimProposal(
        claim_type="DECISION",
        subject_phrase=subject_phrase,
        relation_phrase=relation_phrase,
        evidence_refs=(evidence_ref,),
    )
    context = AgentMemoryResolutionContext(WORKSPACE, subject_registry, proposal)
    built = MemoryCandidateBuilder().build(
        result,
        workspace_id=WORKSPACE,
        run_id=run_id,
        evidence_catalog=AgentRunEvidenceCatalog(run_id, [evidence]),
        source_registry=registry,
        relation_resolver=FrozenRelationResolver(
            ("supplier",), aliases={"vendor": "supplier"}, schema_version="agent-test/v1"
        ),
        resolution_context=context,
    )
    assert built.status is not AgentMemoryBuildStatus.BLOCKED
    assert built.candidate is not None
    return built.candidate, result, evidence, registry, subject_registry


def _service(tmp_path, registry: SourceReferenceRegistry):
    store = TemporalDecisionStore(tmp_path / "shared-lifecycle.sqlite", source_registry=registry)
    return store, SemanticDecisionMaterializer(store)


def _register_candidate_sources(registry: SourceReferenceRegistry, candidate) -> None:
    for item in candidate.evidence:
        registry.register_episode(WORKSPACE, item.source_episode_id)
        registry.register_evidence(
            WORKSPACE,
            item.evidence_ref,
            content_hash=item.content_sha256,
            source_version=item.artifact_version_id,
        )


def _approve_and_materialize(
    service: SemanticDecisionMaterializer,
    candidate,
    assessment,
    *,
    reason: str = "reviewed synthetic source evidence",
    resolution: CandidateReviewResolution | None = None,
):
    service.approve(
        WORKSPACE,
        candidate.candidate_id,
        reviewer_id="reviewer:test",
        expected_candidate_fingerprint=assessment.candidate_fingerprint,
        reason=reason,
        resolution=resolution,
        now=CAPTURED_AT,
    )
    return service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)


def test_agent_candidate_persists_and_reloads_complete_payload_after_restart(tmp_path) -> None:
    candidate, result, evidence, registry, _ = _build_candidate(
        content=(
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            "Acme as supplier for Launch. Alice will confirm purchase order."
        ),
        action_values=[
            {
                "description": "confirm purchase order",
                "owner": "Alice",
                "deadline": None,
                "status": "pending",
            }
        ]
    )

    with TemporalDecisionStore(tmp_path / "shared-lifecycle.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        row = store.get_semantic_candidate_row(WORKSPACE, candidate.candidate_id)

        assert assessment.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        assert row is not None
        assert row["candidate_source_kind"] == "AGENT_RESULT"
        assert row["payload_version"] == "agent-memory-candidate/v2"
        assert service.get_candidate(WORKSPACE, candidate.candidate_id) == candidate
        stored_candidate = json.loads(row["payload_json"])["candidate"]
        assert stored_candidate["team_decision_result_json"] == candidate.team_decision_result_json
        assert store.get_semantic_candidate_events(WORKSPACE, candidate.candidate_id)[0][
            "candidate_source_kind"
        ] == "AGENT_RESULT"

    with TemporalDecisionStore(tmp_path / "shared-lifecycle.sqlite", source_registry=registry) as reopened:
        reloaded = SemanticDecisionMaterializer(reopened).get_candidate(WORKSPACE, candidate.candidate_id)
        assert reloaded == candidate
        assert reloaded.team_decision_result_json == candidate.team_decision_result_json
        assert reloaded.evidence[0].provenance.semantic_span == evidence.segment.evidence_span
        assert reloaded.actions == candidate.actions


def test_approved_team_decision_result_does_not_bypass_memory_authorization(tmp_path) -> None:
    candidate, result, _evidence, registry, _ = _build_candidate()
    assert result.decision["status"] == "approved"

    with TemporalDecisionStore(tmp_path / "memory-auth.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        assert captured.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        with pytest.raises(MaterializationBlockedError, match="authorization"):
            service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)
        with pytest.raises(MaterializationBlockedError, match="did not authorize"):
            service.authorize_by_policy(
                WORKSPACE,
                candidate.candidate_id,
                expected_candidate_fingerprint=captured.candidate_fingerprint,
                now=CAPTURED_AT,
            )


def test_human_review_is_bound_to_candidate_fingerprint_and_workspace(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate()

    with TemporalDecisionStore(tmp_path / "human-review.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        with pytest.raises(MaterializationBlockedError, match="fingerprint"):
            service.approve(
                WORKSPACE,
                candidate.candidate_id,
                reviewer_id="reviewer:test",
                expected_candidate_fingerprint="0" * 64,
                reason="stale review",
                now=CAPTURED_AT,
            )
        assert service.get_candidate("workspace:other", candidate.candidate_id) is None
        service.approve(
            WORKSPACE,
            candidate.candidate_id,
            reviewer_id="reviewer:test",
            expected_candidate_fingerprint=captured.candidate_fingerprint,
            reason="reviewed synthetic evidence",
            now=CAPTURED_AT,
        )
        events = store.get_semantic_candidate_events(WORKSPACE, candidate.candidate_id)
        approval = next(event for event in events if event["event_type"] == "HUMAN_APPROVAL")
        assert approval["candidate_fingerprint"] == captured.candidate_fingerprint
        assert approval["candidate_source_kind"] == "AGENT_RESULT"


def test_unresolved_subject_is_persisted_but_cannot_be_materialized(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate(register_subject=False)

    with TemporalDecisionStore(tmp_path / "unresolved-subject.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        assert assessment.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        assert service.get_candidate(WORKSPACE, candidate.candidate_id) == candidate
        with pytest.raises(MaterializationBlockedError):
            service.approve(
                WORKSPACE,
                candidate.candidate_id,
                reviewer_id="reviewer:test",
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                reason="identity unresolved",
                now=CAPTURED_AT,
            )
        with pytest.raises(MaterializationBlockedError, match="authorization"):
            service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)


def test_explicit_review_resolution_requires_a_workspace_subject_identity(tmp_path) -> None:
    candidate, _result, _evidence, registry, subject_registry = _build_candidate(
        register_subject=False
    )
    # A registry snapshot created at candidate time has no canonical identity to select.
    assert subject_registry.subjects == ()

    with TemporalDecisionStore(tmp_path / "subject-resolution.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        with pytest.raises(MaterializationBlockedError):
            service.approve(
                WORKSPACE,
                candidate.candidate_id,
                reviewer_id="reviewer:test",
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                reason="unregistered subject",
                resolution=CandidateReviewResolution(subject="Launch"),
                now=CAPTURED_AT,
            )


def test_unresolved_relation_is_retained_for_review_and_never_authorized(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate(relation_phrase="supplier for")

    with TemporalDecisionStore(tmp_path / "unresolved-relation.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        assert assessment.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        with pytest.raises(MaterializationBlockedError):
            service.approve(
                WORKSPACE,
                candidate.candidate_id,
                reviewer_id="reviewer:test",
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                reason="relation is unresolved",
                now=CAPTURED_AT,
            )
        with pytest.raises(MaterializationBlockedError):
            service.authorize_by_policy(
                WORKSPACE,
                candidate.candidate_id,
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                now=CAPTURED_AT,
            )


def test_unresolved_effective_time_needs_explicit_human_resolution(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate(
        content="[2026-06-01T09:00:00Z] Alice: Launch supplier is Acme.",
        artifact_id="no-valid-from",
    )

    with TemporalDecisionStore(tmp_path / "unresolved-time.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        with pytest.raises(MaterializationBlockedError):
            service.approve(
                WORKSPACE,
                candidate.candidate_id,
                reviewer_id="reviewer:test",
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                reason="time unresolved",
                now=CAPTURED_AT,
            )
        service.approve(
            WORKSPACE,
            candidate.candidate_id,
            reviewer_id="reviewer:test",
            expected_candidate_fingerprint=assessment.candidate_fingerprint,
            reason="source review supplied explicit effective date",
            resolution=CandidateReviewResolution(valid_from=date(2026, 6, 1)),
            now=CAPTURED_AT,
        )
        receipt = service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT).receipt
        assert receipt is not None
        assert receipt.review_resolution_fingerprint is not None
        assert receipt.effective_at == datetime(2026, 6, 1, tzinfo=UTC)


def test_offline_agent_result_lifecycle_materializes_decision_action_and_lookup(tmp_path) -> None:
    candidate, _result, evidence, registry, _ = _build_candidate(
        content=(
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            "Acme as supplier for Launch. Alice will confirm purchase order."
        ),
        action_values=[
            {
                "description": "confirm purchase order",
                "owner": "Alice",
                "deadline": None,
                "status": "pending",
            }
        ],
    )

    database_path = tmp_path / "agent-e2e.sqlite"
    with TemporalDecisionStore(database_path, source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)

        assert captured.candidate_source_kind.value == "AGENT_RESULT"
        with pytest.raises(MaterializationBlockedError, match="authorization"):
            service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)

        result = _approve_and_materialize(service, candidate, captured)
        receipt = result.receipt
        assert result.workflow_state is CandidateWorkflowState.MATERIALIZED
        assert receipt is not None
        assert receipt.candidate_source_kind.value == "AGENT_RESULT"
        assert receipt.authorization_type == "HUMAN_APPROVAL"
        assert receipt.action_dispositions[0].status == "MATERIALIZED"
        assert receipt.action_dispositions[0].reason_codes == ()

        decision = store.get_decision(WORKSPACE, receipt.decision_id)
        assert decision is not None
        assert decision.subject == "Launch"
        assert decision.relation == "supplier"
        assert decision.value == "Acme"
        assert decision.source_episode_id == evidence.source_episode_id
        assert decision.provenance_run_id == RUN_ID
        assert decision.source_evidence_refs == candidate.decision_evidence_refs
        assert store.get_decision_evidence(WORKSPACE, decision.decision_id) == candidate.decision_evidence_refs

        lookup = store.lookup(
            WORKSPACE,
            "Launch",
            "supplier",
            as_of=datetime(2026, 6, 15, tzinfo=UTC),
        )
        assert lookup.status is TemporalLookupStatus.FOUND
        assert [item.value for item in lookup.records] == ["Acme"]

        actions = store.get_actions(WORKSPACE, decision.decision_id)
        assert len(actions) == 1
        assert actions[0].description == "confirm purchase order"
        assert actions[0].owner == "Alice"
        assert actions[0].source_evidence_refs == candidate.decision_evidence_refs
        events = store.get_semantic_candidate_events(WORKSPACE, candidate.candidate_id)
        assert any(event["event_type"] == "MATERIALIZATION_COMMITTED" for event in events)
        assert all(event["candidate_source_kind"] == "AGENT_RESULT" for event in events)

    with TemporalDecisionStore(database_path, source_registry=registry) as reopened:
        service = SemanticDecisionMaterializer(reopened)
        assert service.get_candidate(WORKSPACE, candidate.candidate_id) == candidate
        replay = service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)
        assert replay.receipt is not None
        assert replay.receipt.receipt_id == receipt.receipt_id
        assert len(reopened.get_actions(WORKSPACE, receipt.decision_id)) == 1


def test_action_without_grounded_owner_stays_candidate_only(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate(
        content=(
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            "Acme as supplier for Launch. Confirm the purchase order."
        ),
        action_values=[
            {
                "description": "Confirm the purchase order",
                "owner": None,
                "deadline": None,
                "status": "pending",
            }
        ],
    )

    with TemporalDecisionStore(tmp_path / "action-owner.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        result = _approve_and_materialize(service, candidate, captured)

        assert result.receipt is not None
        disposition = result.receipt.action_dispositions[0]
        assert disposition.status == "RETAINED_CANDIDATE"
        assert disposition.reason_codes == ("ACTION_OWNER_MISSING",)
        assert store.get_actions(WORKSPACE, result.receipt.decision_id) == ()
        assert service.get_candidate(WORKSPACE, candidate.candidate_id).actions == candidate.actions


def test_subject_review_can_select_only_a_captured_workspace_identity(tmp_path) -> None:
    known_subject = WorkspaceSubject.create(WORKSPACE, "Program Launch")
    candidate, _result, _evidence, registry, _ = _build_candidate(
        subject_registry_subjects=(known_subject,),
    )
    assert candidate.subject_id is None
    assert candidate.subject_resolution.status.value == "NEW_SUBJECT_CANDIDATE"

    with TemporalDecisionStore(tmp_path / "known-subject-review.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        result = _approve_and_materialize(
            service,
            candidate,
            captured,
            resolution=CandidateReviewResolution(
                subject=known_subject.display_label,
                subject_id=known_subject.subject_id,
            ),
        )

        assert result.receipt is not None
        decision = store.get_decision(WORKSPACE, result.receipt.decision_id)
        assert decision is not None
        assert decision.subject == known_subject.display_label
        assert WorkspaceSubjectRegistry(WORKSPACE, (known_subject,)).resolve("Launch").status.value == (
            "NEW_SUBJECT_CANDIDATE"
        )


def test_stale_registered_evidence_prevents_materialization_after_approval(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate()

    with TemporalDecisionStore(tmp_path / "stale-evidence.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        service.approve(
            WORKSPACE,
            candidate.candidate_id,
            reviewer_id="reviewer:test",
            expected_candidate_fingerprint=captured.candidate_fingerprint,
            reason="approved before source registry changed",
            now=CAPTURED_AT,
        )
        registry.unregister_evidence(WORKSPACE, candidate.evidence[0].evidence_ref)

        with pytest.raises(MaterializationBlockedError, match="source version changed"):
            service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)
        row = store.get_semantic_candidate_row(WORKSPACE, candidate.candidate_id)
        assert row is not None
        assert row["workflow_state"] == CandidateWorkflowState.STALE.value


def test_agent_candidate_capture_and_materialization_are_idempotent(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate(
        content=(
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            "Acme as supplier for Launch. Alice will confirm purchase order."
        ),
        action_values=[
            {
                "description": "confirm purchase order",
                "owner": "Alice",
                "deadline": None,
                "status": "pending",
            }
        ],
    )

    with TemporalDecisionStore(tmp_path / "idempotent-agent.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        first_capture = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        initial_events = store.get_semantic_candidate_events(WORKSPACE, candidate.candidate_id)
        replayed_capture = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        assert replayed_capture.workflow_state is first_capture.workflow_state
        assert store.get_semantic_candidate_events(WORKSPACE, candidate.candidate_id) == initial_events

        first = _approve_and_materialize(service, candidate, first_capture)
        replay = service.materialize(WORKSPACE, candidate.candidate_id, now=CAPTURED_AT)
        assert replay.receipt == first.receipt
        assert replay.receipt is not None
        assert len(store.lookup(WORKSPACE, "Launch", "supplier", as_of=datetime(2026, 6, 15, tzinfo=UTC)).records) == 1
        assert len(store.get_actions(WORKSPACE, replay.receipt.decision_id)) == 1


def test_distinct_agent_run_with_same_materialization_identity_reuses_decision(tmp_path) -> None:
    first, _result, _evidence, registry, _ = _build_candidate()
    replay_candidate, _result, _evidence, _replay_registry, _ = _build_candidate(
        run_id="run:agent-memory:replay",
    )
    assert replay_candidate.candidate_id != first.candidate_id
    assert replay_candidate.evidence[0].evidence_ref == first.evidence[0].evidence_ref

    with TemporalDecisionStore(tmp_path / "agent-duplicate-identity.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        first_assessment = service.capture(first, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        first_result = _approve_and_materialize(service, first, first_assessment)
        assert first_result.receipt is not None

        duplicate = service.capture(
            replay_candidate,
            expected_workspace_id=WORKSPACE,
            now=CAPTURED_AT,
        )
        duplicate_result = _approve_and_materialize(service, replay_candidate, duplicate)

        assert duplicate_result.receipt is not None
        assert duplicate_result.receipt.result_state == "DUPLICATE"
        assert duplicate_result.receipt.decision_id == first_result.receipt.decision_id
        assert duplicate_result.receipt.candidate_id == replay_candidate.candidate_id
        assert duplicate_result.receipt.candidate_fingerprint == duplicate.candidate_fingerprint
        assert len(store.lookup(WORKSPACE, "Launch", "supplier", as_of=datetime(2026, 6, 15, tzinfo=UTC)).records) == 1


def test_workspace_mismatch_is_rejected_before_candidate_persistence(tmp_path) -> None:
    candidate, _result, _evidence, registry, _ = _build_candidate()

    with TemporalDecisionStore(tmp_path / "workspace-mismatch.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        with pytest.raises(MaterializationBlockedError, match="another workspace"):
            service.capture(candidate, expected_workspace_id="workspace:other", now=CAPTURED_AT)
        assert service.get_candidate(WORKSPACE, candidate.candidate_id) is None
        assert service.get_candidate("workspace:other", candidate.candidate_id) is None


def test_value_conflict_requires_human_review_before_supersession(tmp_path) -> None:
    first, _result, _evidence, registry, _ = _build_candidate()
    successor, _result, _evidence, _successor_registry, _ = _build_candidate(
        content=(
            "[2026-07-01T09:00:00Z] Alice: Effective 2026-07-01, we decided to use "
            "Globex as supplier for Launch."
        ),
        decision_value="Globex",
        artifact_id="agent-meeting-successor",
    )
    _register_candidate_sources(registry, successor)

    with TemporalDecisionStore(tmp_path / "agent-conflict.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        first_assessment = service.capture(first, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        first_result = _approve_and_materialize(service, first, first_assessment)
        assert first_result.receipt is not None

        conflict = service.capture(successor, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        assert conflict.workflow_state is CandidateWorkflowState.CONFLICT
        assert "CONFLICT_REQUIRES_REVIEW" in conflict.policy_decision.reason_codes
        with pytest.raises(MaterializationBlockedError, match="authorization"):
            service.materialize(WORKSPACE, successor.candidate_id, now=CAPTURED_AT)

        successor_result = _approve_and_materialize(service, successor, conflict)
        assert successor_result.receipt is not None
        assert successor_result.receipt.supersedes_id == first_result.receipt.decision_id
        before = store.lookup(
            WORKSPACE,
            "Launch",
            "supplier",
            as_of=datetime(2026, 6, 30, 23, 59, tzinfo=UTC),
        )
        after = store.lookup(
            WORKSPACE,
            "Launch",
            "supplier",
            as_of=datetime(2026, 7, 1, tzinfo=UTC),
        )
        assert [item.value for item in before.records] == ["Acme"]
        assert [item.value for item in after.records] == ["Globex"]


def test_explicit_replacement_uses_existing_temporal_supersession_path(tmp_path) -> None:
    first, _result, _evidence, registry, _ = _build_candidate()
    successor, _result, _evidence, _successor_registry, _ = _build_candidate(
        content=(
            "[2026-07-01T09:00:00Z] Alice: Effective 2026-07-01, Globex replaces "
            "Acme as supplier for Launch."
        ),
        decision_value="Globex",
        artifact_id="agent-meeting-explicit-successor",
    )
    _register_candidate_sources(registry, successor)

    with TemporalDecisionStore(tmp_path / "agent-supersession.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        first_assessment = service.capture(first, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        first_result = _approve_and_materialize(service, first, first_assessment)
        assert first_result.receipt is not None

        next_assessment = service.capture(successor, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        assert next_assessment.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        assert "SUPERSESSION_REQUIRES_REVIEW" in next_assessment.policy_decision.reason_codes
        result = _approve_and_materialize(service, successor, next_assessment)

        assert result.receipt is not None
        assert result.receipt.supersedes_id == first_result.receipt.decision_id
        history = store.get_history(WORKSPACE, first.subject_key)
        assert len(history) == 2
        assert history[0].supersedes_id is None
        assert history[1].supersedes_id == history[0].decision_id


def test_all_agent_decision_evidence_is_written_to_decision_record(tmp_path) -> None:
    artifacts_and_evidence = []
    for index, content in enumerate(
        (
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use Acme as supplier for Launch.",
            "[2026-06-02T10:00:00Z] Bob: Acme remains the selected supplier for Launch.",
        )
    ):
        artifact = RawArtifact(
            workspace_id=WORKSPACE,
            artifact_id=f"agent-multi-evidence-{index}",
            source_type="timestamped_text",
            content=content,
            ingestion_time=CAPTURED_AT,
        )
        segment = parse_timestamped_text(artifact).segments[0]
        artifacts_and_evidence.append((artifact, AgentRunEvidence(artifact, segment)))
    refs = tuple(item.evidence_ref for _artifact, item in artifacts_and_evidence)
    registry = SourceReferenceRegistry(
        episodes={WORKSPACE: [item.source_episode_id for _artifact, item in artifacts_and_evidence]},
        evidence={WORKSPACE: refs},
        evidence_hashes={
            WORKSPACE: {
                item.evidence_ref: artifact.content_hash
                for artifact, item in artifacts_and_evidence
            }
        },
        evidence_versions={
            WORKSPACE: {
                item.evidence_ref: artifact.artifact_version_id
                for artifact, item in artifacts_and_evidence
            }
        },
    )
    result = TeamDecisionResult(
        decision={"value": "Acme", "status": "approved", "evidence_refs": list(refs)},
        rationale=[{"point": "Both notes support the selected supplier.", "evidence_refs": list(refs)}],
        rejected_alternatives=[],
        actions=[],
        unresolved_items=[],
        uncertainty={"status": "none", "statement": None, "unknown_fields": [], "evidence_refs": []},
        evidence_refs=list(refs),
    )
    built = MemoryCandidateBuilder().build(
        result,
        workspace_id=WORKSPACE,
        run_id=RUN_ID,
        evidence_catalog=AgentRunEvidenceCatalog(
            RUN_ID,
            [item for _artifact, item in artifacts_and_evidence],
        ),
        source_registry=registry,
        relation_resolver=FrozenRelationResolver(("supplier",), schema_version="agent-test/v1"),
        resolution_context=AgentMemoryResolutionContext(
            WORKSPACE,
            WorkspaceSubjectRegistry(WORKSPACE, (WorkspaceSubject.create(WORKSPACE, "Launch"),)),
            AgentDecisionClaimProposal(
                claim_type="DECISION",
                subject_phrase="Launch",
                relation_phrase="supplier",
                evidence_refs=(refs[0],),
            ),
        ),
    )
    assert built.candidate is not None
    candidate = built.candidate
    assert candidate.decision_evidence_refs == refs

    with TemporalDecisionStore(tmp_path / "multi-evidence-agent.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        materialized = _approve_and_materialize(service, candidate, assessment)
        assert materialized.receipt is not None
        decision = store.get_decision(WORKSPACE, materialized.receipt.decision_id)
        assert decision is not None
        assert decision.source_episode_id == candidate.evidence[0].source_episode_id
        assert decision.source_evidence_refs == refs
        assert store.get_decision_evidence(WORKSPACE, decision.decision_id) == refs


def test_calibration_does_not_treat_agent_result_as_policy_authorization(tmp_path) -> None:
    from linkloom.semantic_ingestion.materialization import CalibrationProfile, MaterializationPolicy

    candidate, _result, _evidence, registry, _ = _build_candidate()
    policy = MaterializationPolicy(
        CalibrationProfile(
            "low-risk-agent-test",
            "v1",
            (("claim_type", 0.0), ("entity", 0.0), ("relation", 0.0), ("temporal", 0.0), ("overall", 0.0)),
        )
    )

    with TemporalDecisionStore(tmp_path / "agent-policy-auth.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store, policy=policy)
        assessment = service.capture(candidate, expected_workspace_id=WORKSPACE, now=CAPTURED_AT)
        assert assessment.policy_decision.outcome.value == "REQUIRE_REVIEW"
        with pytest.raises(MaterializationBlockedError, match="did not authorize"):
            service.authorize_by_policy(
                WORKSPACE,
                candidate.candidate_id,
                expected_candidate_fingerprint=assessment.candidate_fingerprint,
                now=CAPTURED_AT,
            )
