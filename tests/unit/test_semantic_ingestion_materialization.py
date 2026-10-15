from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import sqlite3

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import (
    ClaimType,
    CandidateOutcome,
    CandidateReasonCode,
)
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
    SemanticExtractionContext,
)
from linkloom.semantic_ingestion.materialization import (
    CandidateWorkflowState,
    CandidateReviewResolution,
    CalibrationProfile,
    MaterializationBlockedError,
    MaterializationOutcome,
    MaterializationPolicy,
    SemanticDecisionMaterializer,
)
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tools.contracts import ToolCall
from tests.smoke.semantic_ingestion_review_flow import (
    authorize_review_and_materialize,
    synthetic_review_eligibility_gate,
)


def _claim(
    value: str,
    *,
    temporal_status: str = "SOURCE_EVENT_TIME",
    valid_from: str | None = None,
) -> dict[str, object]:
    return {
        "claim_type": "DECISION",
        "subject": "launch",
        "relation": "uses vendor",
        "value": value,
        "temporal_status": temporal_status,
        "valid_from": valid_from,
        "valid_to": None,
        "confidence": {
            "claim_type": 0.98,
            "entity": 0.95,
            "relation": 0.94,
            "temporal": 0.91,
            "overall": 0.94,
        },
    }


class _FakeProvider:
    def __init__(self, *claims: dict[str, object]) -> None:
        self.claims = list(claims)
        self.requests = 0

    def complete(self, request):
        self.requests += 1
        if not self.claims:
            raise AssertionError("unexpected provider request")
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall(
                    call_id=f"fake-{self.requests}",
                    tool_id=request.available_tools[0].tool_id,
                    arguments={"claims": [deepcopy(self.claims.pop(0))]},
                )
            ),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            provider_request_id=f"request-{self.requests}",
            provider_response_id=f"response-{self.requests}",
        )


def _build_candidates() -> tuple[RawArtifact, tuple[object, ...], _FakeProvider]:
    artifact = RawArtifact(
        workspace_id="ws-alpha",
        artifact_id="vendor-decision-log",
        source_type="timestamped_text",
        content=(
            "[2026-10-01T10:03:00+08:00] Alice: We decided to use Vendor A for launch.\n"
            "[2026-10-03T16:20:00+08:00] Bob: After the security review, Vendor B replaces Vendor A."
        ),
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    parsed = parse_timestamped_text(artifact)
    assert len(parsed.segments) == 2
    provider = _FakeProvider(_claim("Vendor A"), _claim("Vendor B"))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request,
            guard_id="fake-budget-guard/v1",
            reservation_id=f"fake-reservation-{provider.requests + 1}",
            estimated_cost_upper_bound_usd=0.001,
        ),
        request_guard_id="fake-budget-guard/v1",
        max_attempts=1,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    context = SemanticExtractionContext(
        expected_workspace_id="ws-alpha",
        relation_resolver=FrozenRelationResolver(
            ("uses vendor",), schema_version="fixture-relations/v1"
        ),
    )
    results = tuple(
        pipeline.process_segment(segment, artifact, context)
        for segment in parsed.segments
    )
    return artifact, results, provider


def _build_single_candidate(
    content: str,
    claim: dict[str, object],
    *,
    prompt_version: str = "semantic-extraction/v1",
    artifact_id: str = "vendor-decision-log",
) -> tuple[RawArtifact, object, _FakeProvider]:
    artifact = RawArtifact(
        workspace_id="ws-alpha",
        artifact_id=artifact_id,
        source_type="timestamped_text",
        content=content,
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    segment = parse_timestamped_text(artifact).segments[0]
    provider = _FakeProvider(claim)
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request,
            guard_id="fake-budget-guard/v1",
            reservation_id="fake-reservation-1",
            estimated_cost_upper_bound_usd=0.001,
        ),
        request_guard_id="fake-budget-guard/v1",
        max_attempts=1,
        prompt_version=prompt_version,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    context = SemanticExtractionContext(
        expected_workspace_id="ws-alpha",
        relation_resolver=FrozenRelationResolver(
            ("uses vendor",), schema_version="fixture-relations/v1"
        ),
    )
    results = pipeline.process_segment(segment, artifact, context)
    assert len(results) == 1
    return artifact, results[0], provider


def _registry_for_results(results) -> SourceReferenceRegistry:
    registry = SourceReferenceRegistry()
    for batch in results:
        for result in batch:
            candidate = result.candidate
            assert candidate is not None
            registry.register_episode(candidate.workspace_id, candidate.source_episode_id)
            registry.register_evidence(
                candidate.workspace_id,
                candidate.evidence_ref,
                content_hash=candidate.content_sha256,
                source_version=candidate.artifact_version_id,
            )
    return registry


def _review_and_materialize(
    service: SemanticDecisionMaterializer,
    workspace_id: str,
    candidate_id: str,
    *,
    reviewer_id: str,
    now: datetime,
    resolution: CandidateReviewResolution | None = None,
    correction_of_candidate_id: str | None = None,
):
    fingerprint = service.candidate_fingerprint(workspace_id, candidate_id)
    assert fingerprint is not None
    service.approve(
        workspace_id,
        candidate_id,
        reviewer_id=reviewer_id,
        expected_candidate_fingerprint=fingerprint,
        reason="Synthetic acceptance fixture reviewed",
        resolution=resolution,
        now=now,
    )
    return service.materialize(
        workspace_id,
        candidate_id,
        now=now,
        correction_of_candidate_id=correction_of_candidate_id,
    )


def test_fake_provider_materializes_vendor_replacement_with_valid_time_and_receipts(tmp_path) -> None:
    artifact, batches, provider = _build_candidates()
    results = tuple(result for batch in batches for result in batch)
    assert results[0].outcome is CandidateOutcome.ACCEPTED
    assert results[1].outcome is CandidateOutcome.UNRESOLVED
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "semantic.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessments = []
        for result in results:
            captured = service.capture(
                result,
                expected_workspace_id="ws-alpha",
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )
            assert captured.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
            assert captured.workflow_state is CandidateWorkflowState.PENDING_REVIEW
            assessments.append(
                _review_and_materialize(
                    service,
                    "ws-alpha",
                    result.candidate.candidate_id,
                    reviewer_id="reviewer-1",
                    now=datetime(2026, 10, 7, tzinfo=UTC),
                    resolution=(
                        CandidateReviewResolution(subject="launch", relation="uses vendor")
                        if result.outcome is CandidateOutcome.UNRESOLVED
                        else None
                    ),
                )
            )

        assert provider.requests == 2
        assert [item.workflow_state for item in assessments] == [
            CandidateWorkflowState.MATERIALIZED,
            CandidateWorkflowState.MATERIALIZED,
        ]
        first, second = results[0].candidate, results[1].candidate
        assert first is not None and second is not None
        assert store.get_as_of(
            "ws-alpha", first.subject_key, datetime(2026, 10, 2, tzinfo=UTC)
        ).value == "Vendor A"
        assert store.get_as_of(
            "ws-alpha", first.subject_key, datetime(2026, 10, 3, 9, tzinfo=UTC)
        ).value == "Vendor B"
        history = store.get_history("ws-alpha", first.subject_key)
        assert [record.value for record in history] == ["Vendor A", "Vendor B"]
        assert history[1].supersedes_id == history[0].decision_id
        assert history[0].valid_to == history[1].valid_from
        assert history[0].source_evidence_refs == (first.evidence_ref,)
        assert history[1].source_evidence_refs == (second.evidence_ref,)
        for result, assessment in zip(results, assessments, strict=True):
            row = store.get_semantic_receipt_row("ws-alpha", result.candidate.candidate_id)
            assert row is not None
            assert row["candidate_fingerprint"] == assessment.candidate_fingerprint
            assert row["authorization_type"] == "HUMAN_APPROVAL"
            assert row["decision_id"] == assessment.receipt.decision_id
        assert assessments[1].receipt.review_resolution_fingerprint
        assert artifact.content_hash


def test_three_segment_synthetic_ingestion_keeps_proposal_supporting_only_and_replaces_decision(
    tmp_path,
) -> None:
    content = (
        "[2026-10-02T09:15:00+08:00] Mira: For the Juniper receipts pilot, we decided to use "
        "Birchline as our vendor.\n"
        "[2026-10-02T11:10:00+08:00] Sol: Could we compare Kestrel Ledger as another vendor "
        "for the Juniper receipts pilot? This is only a proposal; no decision has changed.\n"
        "[2026-10-05T14:20:00+08:00] Mira: Following the mock settlement trial, Wrenwell "
        "replaces Birchline as the vendor for the Juniper receipts pilot.\n"
    )
    artifact = RawArtifact(
        workspace_id="ws-semantic-e2e",
        artifact_id="synthetic-live-pattern",
        source_type="timestamped_text",
        content=content,
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    segments = parse_timestamped_text(artifact).segments
    assert len(segments) == 3

    initial_claim = _claim("Birchline", temporal_status="NOT_STATED")
    initial_claim["subject"] = "Juniper receipts pilot"
    proposal_claim = _claim("Kestrel Ledger", temporal_status="NOT_STATED")
    proposal_claim["claim_type"] = "PROPOSAL"
    proposal_claim["subject"] = "Juniper receipts pilot"
    replacement_claim = _claim("Wrenwell", temporal_status="UNRESOLVED")
    replacement_claim["subject"] = "Juniper receipts pilot"
    provider = _FakeProvider(initial_claim, proposal_claim, replacement_claim)
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="synthetic-deterministic-v1",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request,
            guard_id="synthetic-semantic-e2e/v1",
            reservation_id=f"synthetic-semantic-e2e-{provider.requests + 1}",
            estimated_cost_upper_bound_usd=0.0,
        ),
        request_guard_id="synthetic-semantic-e2e/v1",
        max_attempts=1,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    context = SemanticExtractionContext(
        expected_workspace_id=artifact.workspace_id,
        relation_resolver=FrozenRelationResolver(
            ("uses vendor",), schema_version="synthetic-semantic-e2e/v1"
        ),
    )
    validations = tuple(
        pipeline.process_segment(segment, artifact, context)[0]
        for segment in segments
    )
    assert all(result.candidate is not None and result.provenance_valid for result in validations)
    initial, proposal, replacement = (result.candidate for result in validations)
    assert initial is not None and proposal is not None and replacement is not None
    assert initial.valid_from is None
    assert replacement.valid_from is None

    registry = _registry_for_results(tuple((result,) for result in validations))
    with TemporalDecisionStore(tmp_path / "three-segment-semantic-e2e.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        initial_capture = service.capture(
            validations[0],
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert initial_capture.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        assert initial_capture.policy_decision.materializable_after_review is False
        initial_fingerprint = service.candidate_fingerprint(
            artifact.workspace_id, initial.candidate_id
        )
        assert initial_fingerprint is not None
        initial_review, initial_assessment = authorize_review_and_materialize(
            service,
            artifact.workspace_id,
            initial.candidate_id,
            reviewer_id="TEST_REVIEWER/SYNTHETIC_E2E",
            expected_candidate_fingerprint=initial_fingerprint,
            reason="TEST ONLY: review source event timestamp for initial decision valid time.",
            resolution=CandidateReviewResolution(valid_from=segments[0].event_time),
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert initial_review.policy_decision.materializable_after_review is True
        initial_receipt = initial_assessment.receipt
        assert initial_receipt is not None
        assert initial_receipt.review_resolution_fingerprint is not None

        proposal_assessment = service.capture(
            validations[1],
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert proposal_assessment.workflow_state is CandidateWorkflowState.SUPPORTING_ONLY
        assert proposal_assessment.policy_decision.outcome is MaterializationOutcome.REJECT
        assert store.get_semantic_receipt_row(artifact.workspace_id, proposal.candidate_id) is None

        replacement_assessment = service.capture(
            validations[2],
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert replacement_assessment.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        replacement_fingerprint = service.candidate_fingerprint(
            artifact.workspace_id, replacement.candidate_id
        )
        assert replacement_fingerprint is not None
        replacement_review, replacement_assessment = authorize_review_and_materialize(
            service,
            artifact.workspace_id,
            replacement.candidate_id,
            reviewer_id="TEST_REVIEWER/SYNTHETIC_E2E",
            expected_candidate_fingerprint=replacement_fingerprint,
            reason="TEST ONLY: review source event timestamp for replacement valid time.",
            resolution=CandidateReviewResolution(valid_from=segments[2].event_time),
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert replacement_review.policy_decision.materializable_after_review is True
        replacement_receipt = replacement_assessment.receipt
        assert replacement_receipt is not None
        assert replacement_receipt.review_resolution_fingerprint is not None

        assert provider.requests == 3
        assert replacement.valid_from is None
        assert initial_review.workflow_state is CandidateWorkflowState.APPROVED
        assert replacement_review.workflow_state is CandidateWorkflowState.APPROVED
        assert replacement_receipt.supersedes_id == initial_receipt.decision_id
        assert store.get_as_of(
            artifact.workspace_id,
            initial.subject_key,
            datetime(2026, 10, 4, tzinfo=UTC),
        ).value == "Birchline"
        assert store.get_as_of(
            artifact.workspace_id,
            initial.subject_key,
            datetime(2026, 10, 6, tzinfo=UTC),
        ).value == "Wrenwell"
        history = store.get_history(artifact.workspace_id, initial.subject_key)
        assert [record.value for record in history] == ["Birchline", "Wrenwell"]
        assert history[1].supersedes_id == history[0].decision_id
        assert history[0].valid_to == history[1].valid_from == segments[2].event_time
        assert history[0].source_evidence_refs == (initial.evidence_ref,)
        assert history[1].source_evidence_refs == (replacement.evidence_ref,)
        assert all(
            registry.has_evidence(artifact.workspace_id, reference)
            for record in history
            for reference in record.source_evidence_refs
        )


@pytest.mark.parametrize(
    ("statement", "claim_type", "value"),
    (
        ("We decided to use Vendor X.", ClaimType.DECISION, "Vendor X"),
        ("We are considering Vendor X.", ClaimType.PROPOSAL, "Vendor X"),
        ("Vendor X is located in Tokyo.", ClaimType.FACT, "Tokyo"),
        ("We are replacing Vendor X with Vendor Y.", ClaimType.DECISION, "Vendor Y"),
        ("The current vendor is Vendor Y.", ClaimType.FACT, "Vendor Y"),
        (
            "The previous decision is superseded; use Vendor Y going forward.",
            ClaimType.DECISION,
            "Vendor Y",
        ),
        (
            "A prior report says the team replaced Vendor X with Vendor Y last year.",
            ClaimType.FACT,
            "Vendor Y",
        ),
    ),
)
def test_provider_neutral_pipeline_preserves_typed_semantic_cases(
    statement: str,
    claim_type: ClaimType,
    value: str,
) -> None:
    raw_claim = _claim(value, temporal_status="NOT_STATED")
    raw_claim["claim_type"] = claim_type.value
    raw_claim["subject"] = "the project"
    artifact, result, provider = _build_single_candidate(
        f"[2026-10-01T09:00:00+00:00] Mira: {statement}",
        raw_claim,
        artifact_id="generic-semantic-classification-case",
    )

    assert provider.requests == 1
    assert result.candidate is not None
    assert result.candidate.claim_type is claim_type


def test_materialization_revalidates_source_version_after_authorization(tmp_path) -> None:
    artifact, result, _ = _build_single_candidate(
        "[2026-10-01T10:03:00+08:00] Alice: We decided to use Vendor A for launch.",
        _claim("Vendor A"),
    )
    candidate = result.candidate
    assert candidate is not None
    registry = _registry_for_results(((result,),))

    with TemporalDecisionStore(tmp_path / "changed-source-version.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(
            result,
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        service.approve(
            artifact.workspace_id,
            candidate.candidate_id,
            reviewer_id="reviewer-1",
            expected_candidate_fingerprint=captured.candidate_fingerprint,
            reason="Approve before changing the registered source version.",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        registry.register_evidence(
            candidate.workspace_id,
            candidate.evidence_ref,
            content_hash=candidate.content_sha256,
            source_version="changed-source-version",
        )

        with pytest.raises(MaterializationBlockedError) as error:
            service.materialize(
                artifact.workspace_id,
                candidate.candidate_id,
                now=datetime(2026, 10, 8, tzinfo=UTC),
            )

        assert error.value.reason_code == "PROVENANCE_INVALID"
        stale_row = store.get_semantic_candidate_row(
            artifact.workspace_id, candidate.candidate_id
        )
        assert stale_row is not None
        assert stale_row["workflow_state"] == CandidateWorkflowState.STALE.value


def test_replay_capture_returns_persisted_materialized_state(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "replay.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        first = service.capture(result, expected_workspace_id="ws-alpha")
        _review_and_materialize(
            service,
            "ws-alpha",
            result.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        replay = service.capture(result, expected_workspace_id="ws-alpha")

        assert first.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        assert replay.workflow_state is CandidateWorkflowState.MATERIALIZED
        assert replay.receipt is not None
        events = store.get_semantic_candidate_events("ws-alpha", result.candidate.candidate_id)
        assert sum(event["event_type"] == "CANDIDATE_CAPTURED" for event in events) == 1


def test_unverified_source_is_persisted_as_rejected_not_reviewable(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None

    with TemporalDecisionStore(tmp_path / "invalid-source.sqlite") as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(result, expected_workspace_id="ws-alpha")
        assert assessment.workflow_state is CandidateWorkflowState.REJECTED
        assert assessment.policy_decision.outcome is MaterializationOutcome.REJECT
        with pytest.raises(MaterializationBlockedError, match="no durable authorization"):
            service.materialize(
                "ws-alpha",
                result.candidate.candidate_id,
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )


def test_materialization_requires_matching_durable_authorization_event(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "forged-authorization.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        fingerprint = service.candidate_fingerprint("ws-alpha", result.candidate.candidate_id)
        assert fingerprint is not None
        store._connection.execute(
            "UPDATE semantic_candidate SET workflow_state = 'APPROVED' WHERE candidate_id = ?",
            (result.candidate.candidate_id,),
        )
        with pytest.raises(MaterializationBlockedError, match="authorization audit event"):
            service.materialize(
                "ws-alpha",
                result.candidate.candidate_id,
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )
        assert store.get_semantic_receipt_row("ws-alpha", result.candidate.candidate_id) is None
        assert store.get_current("ws-alpha", result.candidate.subject_key) is None


def test_candidate_is_not_visible_outside_its_workspace(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "workspace.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        assert service.get_candidate("ws-beta", result.candidate.candidate_id) is None
        with pytest.raises(MaterializationBlockedError, match="unavailable"):
            service.approve(
                "ws-beta",
                result.candidate.candidate_id,
                reviewer_id="reviewer-1",
                expected_candidate_fingerprint="0" * 64,
                reason="not in this workspace",
            )


@pytest.mark.parametrize(
    "resolution",
    (
        CandidateReviewResolution(subject="different-project"),
        CandidateReviewResolution(valid_from=datetime(2026, 10, 2, tzinfo=UTC)),
    ),
)
def test_review_overlay_cannot_rewrite_source_resolved_fields(tmp_path, resolution) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "resolved-overlay.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        fingerprint = service.candidate_fingerprint("ws-alpha", result.candidate.candidate_id)
        assert fingerprint is not None

        with pytest.raises(MaterializationBlockedError) as error:
            service.approve(
                "ws-alpha",
                result.candidate.candidate_id,
                reviewer_id="reviewer-1",
                expected_candidate_fingerprint=fingerprint,
                reason="Must not rewrite a source-resolved field.",
                resolution=resolution,
            )

        assert error.value.reason_code in {
            "REVIEW_SUBJECT_ALREADY_RESOLVED",
            "REVIEW_TIME_ALREADY_RESOLVED",
        }
        row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        assert row["workflow_state"] == CandidateWorkflowState.PENDING_REVIEW.value
        assert store.get_semantic_candidate_events("ws-alpha", result.candidate.candidate_id)[-1]["event_type"] != "HUMAN_APPROVAL"


def test_review_overlay_can_set_valid_time_for_not_stated_candidate_from_source_event_time(
    tmp_path,
) -> None:
    artifact, result, _ = _build_single_candidate(
        "[2026-10-02T09:15:00+08:00] Mira: We decided to use Birchline for launch.",
        _claim("Birchline", temporal_status="NOT_STATED"),
    )
    candidate = result.candidate
    assert candidate is not None
    segment = parse_timestamped_text(artifact).segments[0]
    assert candidate.temporal_resolution.value == "NOT_STATED"
    assert candidate.valid_from is None
    registry = _registry_for_results(((result,),))

    with TemporalDecisionStore(tmp_path / "not-stated-time-review.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(
            result,
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert captured.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        materialized = _review_and_materialize(
            service,
            artifact.workspace_id,
            candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
            resolution=CandidateReviewResolution(valid_from=segment.event_time),
        )

        assert materialized.receipt is not None
        decision = store.get_decision(artifact.workspace_id, materialized.receipt.decision_id)
        assert decision.valid_from == segment.event_time
        persisted_candidate = service.get_candidate(artifact.workspace_id, candidate.candidate_id)
        assert persisted_candidate is not None
        assert persisted_candidate.temporal_resolution.value == "NOT_STATED"
        assert persisted_candidate.valid_from is None


def test_review_flow_applies_valid_overlay_before_final_materializability_check(tmp_path) -> None:
    artifact, result, _ = _build_single_candidate(
        "[2026-10-02T09:15:00+08:00] Mira: We decided to use Birchline for launch.",
        _claim("Birchline", temporal_status="NOT_STATED"),
    )
    candidate = result.candidate
    assert candidate is not None
    segment = parse_timestamped_text(artifact).segments[0]
    registry = _registry_for_results(((result,),))

    with TemporalDecisionStore(tmp_path / "review-before-eligibility.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(
            result,
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert captured.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        assert captured.policy_decision.materializable_after_review is False
        with pytest.raises(MaterializationBlockedError) as unreviewed:
            service.materialize(
                artifact.workspace_id,
                candidate.candidate_id,
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )
        assert unreviewed.value.reason_code == "APPROVAL_MISSING"

        fingerprint = service.candidate_fingerprint(artifact.workspace_id, candidate.candidate_id)
        assert fingerprint is not None
        approved, materialized = authorize_review_and_materialize(
            service,
            artifact.workspace_id,
            candidate.candidate_id,
            reviewer_id="TEST_REVIEWER/SYNTHETIC_E2E",
            expected_candidate_fingerprint=fingerprint,
            reason="TEST ONLY: resolve valid time from the exact source event timestamp.",
            resolution=CandidateReviewResolution(valid_from=segment.event_time),
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )

        assert approved.policy_decision.materializable_after_review is True
        assert approved.workflow_state is CandidateWorkflowState.APPROVED
        assert materialized.receipt is not None
        assert materialized.receipt.review_resolution_fingerprint is not None


def test_review_flow_keeps_unresolved_mandatory_time_blocked(tmp_path) -> None:
    artifact, result, _ = _build_single_candidate(
        "[2026-10-02T09:15:00+08:00] Mira: We decided to use Birchline for launch.",
        _claim("Birchline", temporal_status="NOT_STATED"),
    )
    candidate = result.candidate
    assert candidate is not None
    registry = _registry_for_results(((result,),))

    with TemporalDecisionStore(tmp_path / "unresolved-review-flow.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(
            result,
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        fingerprint = service.candidate_fingerprint(artifact.workspace_id, candidate.candidate_id)
        assert fingerprint is not None

        with pytest.raises(MaterializationBlockedError) as error:
            authorize_review_and_materialize(
                service,
                artifact.workspace_id,
                candidate.candidate_id,
                reviewer_id="TEST_REVIEWER/SYNTHETIC_E2E",
                expected_candidate_fingerprint=fingerprint,
                reason="TEST ONLY: no temporal resolution was supplied.",
                resolution=None,
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )

        assert error.value.reason_code == "TEMPORAL_UNRESOLVED"
        row = store.get_semantic_candidate_row(artifact.workspace_id, candidate.candidate_id)
        assert row is not None
        assert row["workflow_state"] == CandidateWorkflowState.PENDING_REVIEW.value
        assert store.get_semantic_receipt_row(artifact.workspace_id, candidate.candidate_id) is None
        assert captured.policy_decision.materializable_after_review is False


def test_review_overlay_cannot_make_proposal_authoritative(tmp_path) -> None:
    proposal = _claim("Vendor A", temporal_status="NOT_STATED")
    proposal["claim_type"] = "PROPOSAL"
    artifact, result, _ = _build_single_candidate(
        "[2026-10-02T09:15:00+08:00] Mira: We are considering Vendor A for launch.",
        proposal,
    )
    candidate = result.candidate
    assert candidate is not None
    segment = parse_timestamped_text(artifact).segments[0]
    registry = _registry_for_results(((result,),))

    with TemporalDecisionStore(tmp_path / "proposal-review-overlay.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        captured = service.capture(
            result,
            expected_workspace_id=artifact.workspace_id,
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert captured.workflow_state is CandidateWorkflowState.SUPPORTING_ONLY
        fingerprint = service.candidate_fingerprint(artifact.workspace_id, candidate.candidate_id)
        assert fingerprint is not None

        with pytest.raises(MaterializationBlockedError) as error:
            authorize_review_and_materialize(
                service,
                artifact.workspace_id,
                candidate.candidate_id,
                reviewer_id="TEST_REVIEWER/SYNTHETIC_E2E",
                expected_candidate_fingerprint=fingerprint,
                reason="TEST ONLY: temporal overlay cannot change semantic class.",
                resolution=CandidateReviewResolution(valid_from=segment.event_time),
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )

        assert error.value.reason_code == "SEMANTIC_CLASS_NOT_AUTHORITATIVE"
        row = store.get_semantic_candidate_row(artifact.workspace_id, candidate.candidate_id)
        assert row is not None
        assert row["workflow_state"] == CandidateWorkflowState.SUPPORTING_ONLY.value
        assert store.get_semantic_receipt_row(artifact.workspace_id, candidate.candidate_id) is None


def test_synthetic_review_gate_allows_unresolved_time_only_with_exact_event_time_review() -> None:
    claim = _claim("Wrenwell", temporal_status="UNRESOLVED")
    claim["subject"] = "the project"
    artifact, result, _ = _build_single_candidate(
        "[2026-10-05T14:20:00+08:00] Mira: For the project, Wrenwell replaces Vendor A as vendor.",
        claim,
        artifact_id="generic-replacement-review-gate",
    )
    candidate = result.candidate
    assert candidate is not None
    segment = parse_timestamped_text(artifact).segments[0]

    without_review, no_review_reason = synthetic_review_eligibility_gate(
        result,
        candidate.provenance.quote,
        source_artifact=artifact,
    )
    with_review, review_reason = synthetic_review_eligibility_gate(
        result,
        candidate.provenance.quote,
        source_artifact=artifact,
        reviewed_event_time=segment.event_time,
    )

    assert without_review is False
    assert no_review_reason == "UNRESOLVED_VALIDATION_REASON"
    assert with_review is True
    assert review_reason == "EXPLICIT_SOURCE_DECISION_REVIEWABLE_WITH_TEST_TIME_REVIEW"


def test_synthetic_review_gate_rejects_historical_report_without_current_choice() -> None:
    claim = _claim("Vendor Y", temporal_status="SOURCE_EVENT_TIME")
    claim["subject"] = "the team"
    artifact, result, _ = _build_single_candidate(
        "[2026-10-05T14:20:00+08:00] Mira: A prior report says the team replaced Vendor X with Vendor Y last year.",
        claim,
        artifact_id="generic-historical-report-review-gate",
    )
    candidate = result.candidate
    assert candidate is not None

    eligible, reason = synthetic_review_eligibility_gate(
        result,
        candidate.provenance.quote,
        source_artifact=artifact,
        reviewed_event_time=candidate.event_time,
    )

    assert eligible is False
    assert reason == "HISTORICAL_SOURCE_REPORT_ONLY"


def test_conflict_policy_state_and_reason_are_durable_and_reviewable(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    accepted = batches[0][0]
    assert accepted.candidate is not None
    conflict_code = CandidateReasonCode.CONFLICT_REQUIRES_REVIEW
    conflicted_candidate = replace(
        accepted.candidate,
        review_reasons=(conflict_code,),
    )
    conflicted = replace(
        accepted,
        outcome=CandidateOutcome.UNRESOLVED,
        candidate=conflicted_candidate,
        reason_codes=(conflict_code,),
    )
    registry = _registry_for_results(((conflicted,),))

    with TemporalDecisionStore(tmp_path / "conflict.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        assessment = service.capture(conflicted, expected_workspace_id="ws-alpha")
        row = store.get_semantic_candidate_row("ws-alpha", conflicted_candidate.candidate_id)
        events = store.get_semantic_candidate_events("ws-alpha", conflicted_candidate.candidate_id)
        policy_event = next(event for event in events if event["event_type"] == "POLICY_DECISION")

        assert assessment.workflow_state is CandidateWorkflowState.CONFLICT
        assert row["workflow_state"] == CandidateWorkflowState.CONFLICT.value
        assert conflicted_candidate.candidate_id in {
            candidate.candidate_id for candidate in service.list_pending("ws-alpha")
        }
        assert policy_event["candidate_fingerprint"] == assessment.candidate_fingerprint
        assert '"outcome":"REQUIRE_REVIEW"' in policy_event["metadata_json"]
        assert "CONFLICT_REQUIRES_REVIEW" in policy_event["metadata_json"]


def test_human_approval_is_bound_to_exact_candidate_fingerprint(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "stale-approval.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        with pytest.raises(MaterializationBlockedError, match="fingerprint does not match"):
            service.approve(
                "ws-alpha",
                result.candidate.candidate_id,
                reviewer_id="reviewer-1",
                expected_candidate_fingerprint="0" * 64,
                reason="Stale fingerprint must not authorize the candidate.",
            )
        row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        assert row["workflow_state"] == CandidateWorkflowState.PENDING_REVIEW.value
        assert not any(
            event["event_type"] == "HUMAN_APPROVAL"
            for event in store.get_semantic_candidate_events("ws-alpha", result.candidate.candidate_id)
        )


def test_calibrated_low_risk_candidate_uses_policy_authorization_not_human_approval(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)
    calibration = CalibrationProfile(
        "fixture-calibration",
        "v1",
        (("claim_type", 0.0), ("entity", 0.0), ("relation", 0.0), ("temporal", 0.0), ("overall", 0.0)),
    )

    with TemporalDecisionStore(tmp_path / "policy-auth.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(
            store,
            policy=MaterializationPolicy(calibration),
        )
        assessment = service.capture(result, expected_workspace_id="ws-alpha")
        assert assessment.policy_decision.outcome is MaterializationOutcome.AUTO_ALLOW
        authorized = service.authorize_by_policy(
            "ws-alpha",
            result.candidate.candidate_id,
            expected_candidate_fingerprint=assessment.candidate_fingerprint,
        )
        materialized = service.materialize(
            "ws-alpha", result.candidate.candidate_id, now=datetime(2026, 10, 7, tzinfo=UTC)
        )

        assert authorized.workflow_state is CandidateWorkflowState.POLICY_AUTHORIZED
        assert materialized.receipt.authorization_type == "POLICY_AUTHORIZATION"
        events = store.get_semantic_candidate_events("ws-alpha", result.candidate.candidate_id)
        policy_event = next(event for event in events if event["event_type"] == "POLICY_AUTHORIZATION")
        assert policy_event["actor_type"] == "POLICY"
        assert not any(event["event_type"] == "HUMAN_APPROVAL" for event in events)


def test_duplicate_requires_human_approval_without_calibration(tmp_path) -> None:
    content = "[2026-10-01T10:03:00+08:00] Alice: We decided to use Vendor A for launch."
    _, original, _ = _build_single_candidate(content, _claim("Vendor A"), prompt_version="extract-v1")
    _, duplicate, _ = _build_single_candidate(content, _claim("Vendor A"), prompt_version="extract-v2")
    assert original.candidate is not None and duplicate.candidate is not None
    registry = _registry_for_results(((original,), (duplicate,)))

    with TemporalDecisionStore(tmp_path / "duplicate-review.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(original, expected_workspace_id="ws-alpha")
        original_receipt = _review_and_materialize(
            service,
            "ws-alpha",
            original.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        ).receipt
        assert original_receipt is not None

        duplicate_assessment = service.capture(duplicate, expected_workspace_id="ws-alpha")
        duplicate_events = store.get_semantic_candidate_events("ws-alpha", duplicate.candidate.candidate_id)
        assert duplicate_assessment.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        assert duplicate_assessment.workflow_state is CandidateWorkflowState.PENDING_REVIEW
        assert not any(event["event_type"] == "POLICY_AUTHORIZATION" for event in duplicate_events)

        duplicate_receipt = _review_and_materialize(
            service,
            "ws-alpha",
            duplicate.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        ).receipt
        assert duplicate_receipt.result_state == "DUPLICATE"
        assert duplicate_receipt.decision_id == original_receipt.decision_id


def test_future_effective_successor_changes_current_value_at_valid_time_boundary(tmp_path) -> None:
    first_artifact, first_batches, _ = _build_candidates()
    first = first_batches[0][0]
    future_artifact, future_result, _ = _build_single_candidate(
        "[2026-10-03T16:20:00+08:00] Bob: After review, Vendor B replaces Vendor A for launch effective 2026-10-12T00:00:00+08:00.",
        _claim(
            "Vendor B",
            temporal_status="EXPLICIT",
            valid_from="2026-10-12T00:00:00+08:00",
        ),
    )
    assert first.candidate is not None and future_result.candidate is not None
    registry = _registry_for_results(((first,), (future_result,)))

    with TemporalDecisionStore(tmp_path / "future.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        initial = service.capture(first, expected_workspace_id="ws-alpha")
        _review_and_materialize(
            service,
            "ws-alpha",
            first.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        staged = service.capture(
            future_result,
            expected_workspace_id="ws-alpha",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        assert staged.policy_decision.outcome is MaterializationOutcome.REQUIRE_REVIEW
        _review_and_materialize(
            service,
            "ws-alpha",
            future_result.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        )
        slot = first.candidate.subject_key
        assert slot is not None
        assert store.get_current("ws-alpha", slot, as_of=datetime(2026, 10, 11, 15, tzinfo=UTC)).value == "Vendor A"
        assert store.get_current("ws-alpha", slot, as_of=datetime(2026, 10, 11, 16, tzinfo=UTC)).value == "Vendor B"
        assert initial.receipt is None
        assert first_artifact.content_hash and future_artifact.content_hash


def test_extraction_correction_invalidates_old_record_without_business_supersession(tmp_path) -> None:
    content = (
        "[2026-10-03T16:20:00+08:00] Bob: After the security review, "
        "Vendor B replaces Vendor A for launch."
    )
    artifact, incorrect, _ = _build_single_candidate(
        content, _claim("Vendor A"), prompt_version="extract-v1"
    )
    same_artifact, corrected, _ = _build_single_candidate(
        content, _claim("Vendor B"), prompt_version="extract-v2"
    )
    assert artifact.artifact_version_id == same_artifact.artifact_version_id
    assert incorrect.candidate is not None and corrected.candidate is not None
    assert incorrect.candidate.extraction_fingerprint != corrected.candidate.extraction_fingerprint
    registry = _registry_for_results(((incorrect,), (corrected,)))

    with TemporalDecisionStore(tmp_path / "correction.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(incorrect, expected_workspace_id="ws-alpha")
        old_record = _review_and_materialize(
            service,
            "ws-alpha",
            incorrect.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        ).receipt
        assert old_record is not None
        service.capture(corrected, expected_workspace_id="ws-alpha")
        service.record_extraction_correction(
            "ws-alpha",
            incorrect.candidate.candidate_id,
            corrected.candidate.candidate_id,
            reviewer_id="reviewer-1",
            reason="Corrected a wrong value extracted from the unchanged source span.",
            now=datetime(2026, 10, 8, tzinfo=UTC),
        )
        corrected_receipt = _review_and_materialize(
            service,
            "ws-alpha",
            corrected.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 8, tzinfo=UTC),
            correction_of_candidate_id=incorrect.candidate.candidate_id,
        ).receipt
        assert corrected_receipt is not None

        old_row = store.get_decision("ws-alpha", old_record.decision_id)
        new_row = store.get_decision("ws-alpha", corrected_receipt.decision_id)
        assert old_row.memory_state.value == "INVALIDATED"
        assert new_row.value == "Vendor B"
        assert new_row.supersedes_id is None
        new_receipt = store.get_semantic_receipt_row("ws-alpha", corrected.candidate.candidate_id)
        assert new_receipt["correction_of_candidate_id"] == incorrect.candidate.candidate_id
        old_events = store.get_semantic_candidate_events("ws-alpha", incorrect.candidate.candidate_id)
        assert any(event["event_type"] == "EXTRACTION_CORRECTED" for event in old_events)


def test_same_value_extraction_correction_does_not_dedupe_to_invalidated_record(tmp_path) -> None:
    content = "[2026-10-01T10:03:00+08:00] Alice: We decided to use Vendor A for launch."
    _, incorrect, _ = _build_single_candidate(content, _claim("Vendor A"), prompt_version="extract-v1")
    _, corrected, _ = _build_single_candidate(content, _claim("Vendor A"), prompt_version="extract-v2")
    assert incorrect.candidate is not None and corrected.candidate is not None
    registry = _registry_for_results(((incorrect,), (corrected,)))

    with TemporalDecisionStore(tmp_path / "same-value-correction.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(incorrect, expected_workspace_id="ws-alpha")
        old_receipt = _review_and_materialize(
            service,
            "ws-alpha",
            incorrect.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        ).receipt
        assert old_receipt is not None
        service.capture(corrected, expected_workspace_id="ws-alpha")
        service.record_extraction_correction(
            "ws-alpha",
            incorrect.candidate.candidate_id,
            corrected.candidate.candidate_id,
            reviewer_id="reviewer-1",
            reason="Re-evaluated the unchanged source span.",
            now=datetime(2026, 10, 8, tzinfo=UTC),
        )

        new_assessment = _review_and_materialize(
            service,
            "ws-alpha",
            corrected.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 8, tzinfo=UTC),
            correction_of_candidate_id=incorrect.candidate.candidate_id,
        )

        assert new_assessment.receipt.result_state == "MATERIALIZED"
        assert new_assessment.receipt.decision_id != old_receipt.decision_id
        assert store.get_decision("ws-alpha", new_assessment.receipt.decision_id).memory_state.value == "ACTIVE"


def test_correction_receipt_requires_a_materialized_same_workspace_target(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "missing-correction-target.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        fingerprint = service.candidate_fingerprint("ws-alpha", result.candidate.candidate_id)
        assert fingerprint is not None
        service.approve(
            "ws-alpha",
            result.candidate.candidate_id,
            reviewer_id="reviewer-1",
            expected_candidate_fingerprint=fingerprint,
            reason="Approve before testing invalid correction link.",
        )

        with pytest.raises(MaterializationBlockedError, match="correction target") as error:
            service.materialize(
                "ws-alpha",
                result.candidate.candidate_id,
                correction_of_candidate_id="missing-or-cross-workspace-candidate",
                now=datetime(2026, 10, 7, tzinfo=UTC),
            )

        assert error.value.reason_code == "CORRECTION_TARGET_INVALID"
        assert store.get_semantic_receipt_row("ws-alpha", result.candidate.candidate_id) is None


def test_reconciliation_updates_semantic_candidate_state_and_event(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "semantic-reconciliation.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        receipt = _review_and_materialize(
            service,
            "ws-alpha",
            result.candidate.candidate_id,
            reviewer_id="reviewer-1",
            now=datetime(2026, 10, 7, tzinfo=UTC),
        ).receipt
        assert receipt is not None

        store.reconcile({"ws-alpha": {}})
        stale_row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        stale_events = store.get_semantic_candidate_events("ws-alpha", result.candidate.candidate_id)
        assert stale_row["workflow_state"] == CandidateWorkflowState.STALE.value
        assert any(event["event_type"] == "RECONCILIATION_STATE_CHANGED" for event in stale_events)

        store.reconcile(
            {
                "ws-alpha": {
                    result.candidate.evidence_ref: result.candidate.content_sha256,
                }
            }
        )
        restored_row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        assert restored_row["workflow_state"] == CandidateWorkflowState.MATERIALIZED.value


def test_new_source_version_is_recorded_without_rewriting_prior_candidate(tmp_path) -> None:
    first_content = "[2026-10-01T10:03:00+08:00] Alice: We decided to use Vendor A for launch."
    corrected_content = first_content + " (minutes typo fixed)"
    first_artifact, first, _ = _build_single_candidate(first_content, _claim("Vendor A"))
    second_artifact, second, _ = _build_single_candidate(corrected_content, _claim("Vendor A"))
    assert first.candidate is not None and second.candidate is not None
    assert first.candidate.artifact_id == second.candidate.artifact_id
    assert first.candidate.artifact_version_id != second.candidate.artifact_version_id
    registry = _registry_for_results(((first,), (second,)))

    with TemporalDecisionStore(tmp_path / "source-version.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(first, expected_workspace_id="ws-alpha")
        service.capture(second, expected_workspace_id="ws-alpha")
        first_payload = store.get_semantic_candidate_row("ws-alpha", first.candidate.candidate_id)
        second_events = store.get_semantic_candidate_events("ws-alpha", second.candidate.candidate_id)
        assert first_payload["payload_json"]
        version_rows = store.list_semantic_candidate_rows(
            "ws-alpha",
            artifact_id=second.candidate.artifact_id,
            artifact_version_id=second.candidate.artifact_version_id,
        )
        assert [row["candidate_id"] for row in version_rows] == [second.candidate.candidate_id]
        fingerprint_rows = store.list_semantic_candidate_rows(
            "ws-alpha", candidate_fingerprint=first_payload["candidate_fingerprint"]
        )
        assert [row["candidate_id"] for row in fingerprint_rows] == [first.candidate.candidate_id]
        version_event = next(event for event in second_events if event["event_type"] == "SOURCE_VERSION_OBSERVED")
        assert first.candidate.artifact_version_id in str(version_event["metadata_json"])
        assert first_artifact.content_hash != second_artifact.content_hash


def test_receipt_failure_rolls_back_decision_evidence_and_state_change(tmp_path) -> None:
    _, batches, _ = _build_candidates()
    result = batches[0][0]
    assert result.candidate is not None
    registry = _registry_for_results(batches)

    with TemporalDecisionStore(tmp_path / "atomicity.sqlite", source_registry=registry) as store:
        service = SemanticDecisionMaterializer(store)
        service.capture(result, expected_workspace_id="ws-alpha")
        fingerprint = service.candidate_fingerprint("ws-alpha", result.candidate.candidate_id)
        service.approve(
            "ws-alpha",
            result.candidate.candidate_id,
            reviewer_id="reviewer-1",
            expected_candidate_fingerprint=fingerprint,
            reason="Inject a receipt failure after decision insertion.",
        )
        store._connection.execute(
            """
            CREATE TRIGGER fail_receipt_insert BEFORE INSERT ON semantic_materialization_receipt
            BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END
            """
        )
        store._connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="injected receipt failure"):
            service.materialize("ws-alpha", result.candidate.candidate_id)

        assert store._connection.execute(
            "SELECT COUNT(*) FROM decision WHERE workspace_id = 'ws-alpha'"
        ).fetchone()[0] == 0
        assert store.get_semantic_receipt_row("ws-alpha", result.candidate.candidate_id) is None
        row = store.get_semantic_candidate_row("ws-alpha", result.candidate.candidate_id)
        assert row["workflow_state"] == CandidateWorkflowState.APPROVED.value
