from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest

from linkloom.agents.model_adapter import (
    ModelAction,
    ModelProviderError,
    ModelResponse,
    ModelUsage,
)
from linkloom.decision_memory.models import DecisionRecord
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import (
    CandidateOutcome,
    CandidateReasonCode,
    ClaimType,
    RelationResolution,
    SemanticExtractionResult,
    TemporalBasis,
    TemporalResolution,
)
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
    SemanticExtractionContext,
)
from linkloom.semantic_ingestion.metrics import (
    SemanticEvaluationCase,
    SemanticEvaluationLabel,
    evaluate_semantic_extraction,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.tools.contracts import ToolCall


def _claim(**overrides: object) -> dict[str, object]:
    claim: dict[str, object] = {
        "claim_type": "DECISION",
        "subject": "launch",
        "relation": "uses vendor",
        "value": "Vendor A",
        "temporal_status": "SOURCE_EVENT_TIME",
        "valid_from": None,
        "valid_to": None,
        "confidence": {
            "claim_type": 0.98,
            "entity": 0.95,
            "relation": 0.94,
            "temporal": 0.91,
            "overall": 0.94,
        },
    }
    claim.update(overrides)
    return claim


def _payload(*claims: dict[str, object]) -> dict[str, object]:
    return {"claims": list(claims)}


def _source(content: str, *, workspace_id: str = "ws-alpha") -> tuple[RawArtifact, object]:
    artifact = RawArtifact(
        workspace_id=workspace_id,
        artifact_id="meeting-2026-10-01",
        source_type="timestamped_text",
        content=f"[2026-10-01T10:03:00Z] Alice: {content}",
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    parsed = parse_timestamped_text(artifact)
    assert len(parsed.segments) == 1
    return artifact, parsed.segments[0]


class _ScriptedProvider:
    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("unexpected extra Provider request")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(request)
        if isinstance(step, dict):
            step = ModelResponse(
                action=ModelAction.tool(
                    ToolCall(
                        call_id=f"fake-call-{len(self.requests)}",
                        tool_id=request.available_tools[0].tool_id,
                        arguments=deepcopy(step),
                    )
                ),
                usage=ModelUsage(input_tokens=12, output_tokens=9, total_tokens=21),
                provider_request_id=f"fake-request-{len(self.requests)}",
                provider_response_id=f"fake-response-{len(self.requests)}",
            )
        if not isinstance(step, ModelResponse):
            raise AssertionError("script step must return ModelResponse or extraction JSON")
        return step


def _context(
    *,
    workspace_id: str = "ws-alpha",
    relation_catalog: tuple[str, ...] = ("uses vendor",),
    aliases: dict[str, str] | None = None,
) -> SemanticExtractionContext:
    return SemanticExtractionContext(
        expected_workspace_id=workspace_id,
        relation_resolver=FrozenRelationResolver(
            relation_catalog,
            aliases=aliases or {},
            schema_version="fixture-relations/v1",
        ),
    )


def _authorize_request(
    request,
    *,
    guard_id: str = "test-budget-guard/v1",
    reservation_id: str = "test-reservation",
    authorized: bool = True,
) -> ProviderRequestAuthorization:
    return ProviderRequestAuthorization(
        guard_id=guard_id,
        request_sha256=ProviderRequestAuthorization.approve(
            request,
            guard_id=guard_id,
            reservation_id=reservation_id,
            estimated_cost_upper_bound_usd=0.001,
        ).request_sha256,
        reservation_id=reservation_id,
        estimated_cost_upper_bound_usd=0.001,
        authorized=authorized,
    )


def _run(
    claim_payload: dict[str, object] | ModelResponse,
    *,
    content: str = "We decided to use Vendor A for launch.",
    context: SemanticExtractionContext | None = None,
    max_attempts: int = 1,
    prompt_version: str = "semantic-extraction/v1",
    temperature: float | None = None,
) -> tuple[object, _ScriptedProvider, object, object]:
    artifact, segment = _source(content)
    provider = _ScriptedProvider(claim_payload)
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(
            request,
            reservation_id=f"test-reservation-{len(provider.requests) + 1}",
        ),
        request_guard_id="test-budget-guard/v1",
        max_attempts=max_attempts,
        prompt_version=prompt_version,
        temperature=temperature,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    result = pipeline.process_segment(segment, artifact, context or _context())
    return result, provider, artifact, segment


def test_decision_is_structured_source_grounded_and_non_authoritative() -> None:
    results, provider, artifact, segment = _run(_payload(_claim()))

    assert len(results) == 1
    result = results[0]
    assert result.outcome is CandidateOutcome.ACCEPTED
    candidate = result.candidate
    assert candidate is not None
    assert candidate.claim_type is ClaimType.DECISION
    assert candidate.subject == "launch"
    assert candidate.relation == "uses vendor"
    assert candidate.value == "Vendor A"
    assert candidate.relation_resolution is RelationResolution.CANONICAL_RELATION
    assert candidate.temporal_resolution is TemporalResolution.SOURCE_EVENT_TIME
    assert candidate.temporal_basis is TemporalBasis.DETERMINISTIC_RULE
    assert candidate.subject_key
    assert candidate.event_time == segment.event_time
    assert candidate.ingestion_time == artifact.ingestion_time
    assert candidate.source_episode_id.startswith("episode_v1_")
    assert candidate.valid_from == segment.event_time
    assert candidate.provenance == segment.evidence_span
    assert candidate.provenance.verify(artifact)
    assert candidate.authority == "NON_AUTHORITATIVE"
    assert not isinstance(candidate, DecisionRecord)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.generation_options.temperature is None
    assert request.generation_options.require_tool_call is True
    assert request.available_tools[0].input_schema["additionalProperties"] is False
    assert result.extraction_receipt.provider_id == "fake-provider"
    assert result.extraction_receipt.model_id == "fake-model-v1"
    assert result.extraction_receipt.request_guard_id == "test-budget-guard/v1"
    assert result.extraction_receipt.attempt_count == 1
    assert result.extraction_receipt.request_sha256
    assert result.extraction_receipt.response_sha256
    assert result.extraction_receipt.usage.input_tokens == 12
    assert result.extraction_receipt.latency_ms >= 0
    assert result.extraction_receipt.estimated_cost_upper_bound_usd == 0.001
    assert result.extraction_receipt.attempts[0].budget_reservation_id == "test-reservation-1"


def test_extractor_preserves_explicit_model_default_temperature() -> None:
    _, provider, _, _ = _run(_payload(_claim()), temperature=None)

    assert len(provider.requests) == 1
    assert provider.requests[0].generation_options.temperature is None


def test_gemini_3_semantic_extractor_defaults_to_low_thinking():
    provider = _ScriptedProvider(_payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="gemini",
        model_id="gemini-3.8-flash",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )

    assert extractor.generation_options.temperature is None
    assert extractor.generation_options.thinking_level == "low"


@pytest.mark.parametrize(
    ("claim_type", "content", "value"),
    [
        ("PROPOSAL", "Maybe we should use Vendor C for launch.", "Vendor C"),
        ("QUESTION", "Should we use Vendor C for launch?", "Vendor C"),
        ("REJECTED_OPTION", "We considered Vendor C but rejected it for launch.", "Vendor C"),
        ("PLAN", "We plan to use Vendor C for launch.", "Vendor C"),
        ("ACTION", "Alice will use Vendor C for launch.", "Vendor C"),
        ("OPINION", "I think Vendor C is best for launch.", "Vendor C"),
        ("SPECULATION", "Vendor C might work for launch.", "Vendor C"),
    ],
)
def test_non_decision_classes_remain_structurally_distinct_and_non_authoritative(
    claim_type: str,
    content: str,
    value: str,
) -> None:
    output = _claim(claim_type=claim_type, value=value, temporal_status="NOT_STATED")

    results, _, _, _ = _run(_payload(output), content=content)

    candidate = results[0].candidate
    assert candidate is not None
    assert candidate.claim_type is ClaimType(claim_type)
    assert candidate.authority == "NON_AUTHORITATIVE"
    assert results[0].outcome is CandidateOutcome.ACCEPTED


def test_conflicting_batch_candidates_remain_separate_and_require_review() -> None:
    artifact = RawArtifact(
        workspace_id="ws-alpha",
        artifact_id="meeting-conflict",
        source_type="timestamped_text",
        content=(
            "[2026-10-06T11:00:00Z] Alice: We selected Vendor C for the 2026 launch.\n"
            "[2026-10-06T11:01:00Z] Bob: For the 2026 launch, the decision is still Vendor B; no replacement was approved."
        ),
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    segments = parse_timestamped_text(artifact).segments
    assert len(segments) == 2
    provider = _ScriptedProvider(
        _payload(_claim(subject="2026 launch", value="Vendor C")),
        _payload(_claim(subject="2026 launch", value="Vendor B")),
    )
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    results = pipeline.process_segments(segments, artifact, _context())

    assert len(results) == 2
    assert all(result.outcome is CandidateOutcome.UNRESOLVED for result in results)
    assert all(
        CandidateReasonCode.CONFLICT_REQUIRES_REVIEW in result.reason_codes
        for result in results
    )
    assert [result.candidate.value for result in results if result.candidate] == [
        "Vendor C",
        "Vendor B",
    ]
    assert all(result.candidate.provenance.verify(artifact) for result in results if result.candidate)
    assert len(provider.requests) == 2


def test_explicit_replacement_language_is_not_misclassified_as_conflict() -> None:
    artifact = RawArtifact(
        workspace_id="ws-alpha",
        artifact_id="meeting-replacement",
        source_type="timestamped_text",
        content=(
            "[2026-10-03T16:20:00Z] Bob: We decided to use Vendor A for the 2026 launch.\n"
            "[2026-10-04T09:00:00Z] Bob: Vendor B replaces Vendor A for the 2026 launch."
        ),
        ingestion_time=datetime(2026, 10, 7, tzinfo=UTC),
    )
    segments = parse_timestamped_text(artifact).segments
    provider = _ScriptedProvider(
        _payload(_claim(subject="2026 launch", value="Vendor A")),
        _payload(_claim(subject="2026 launch", value="Vendor B")),
    )
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    results = pipeline.process_segments(segments, artifact, _context())

    assert len(results) == 2
    assert all(
        CandidateReasonCode.CONFLICT_REQUIRES_REVIEW not in result.reason_codes
        for result in results
    )


def test_future_effective_date_is_preserved_without_lifecycle_mutation() -> None:
    content = "Starting 2026-10-12, Vendor B replaces Vendor A for launch."
    claim = _claim(
        value="Vendor B",
        temporal_status="EXPLICIT",
        valid_from="2026-10-12",
    )

    results, _, _, _ = _run(_payload(claim), content=content)

    candidate = results[0].candidate
    assert candidate is not None
    assert candidate.valid_from == date(2026, 10, 12)
    assert candidate.event_time == datetime(2026, 10, 1, 10, 3, tzinfo=UTC)
    assert candidate.temporal_resolution is TemporalResolution.EXPLICIT
    assert candidate.temporal_basis is TemporalBasis.EXPLICIT
    assert candidate.authority == "NON_AUTHORITATIVE"


def test_ambiguous_relative_time_is_retained_as_unresolved() -> None:
    content = "Starting next Monday, Vendor B replaces Vendor A for launch."
    claim = _claim(
        value="Vendor B",
        temporal_status="UNRESOLVED",
        valid_from=None,
    )

    results, _, _, _ = _run(_payload(claim), content=content)

    assert results[0].outcome is CandidateOutcome.UNRESOLVED
    assert CandidateReasonCode.TEMPORAL_UNRESOLVED in results[0].reason_codes
    assert results[0].candidate is not None
    assert results[0].candidate.valid_from is None
    assert results[0].candidate.event_time is not None
    assert results[0].candidate.temporal_basis is TemporalBasis.UNRESOLVED


def test_subject_key_is_relation_scoped() -> None:
    results, _, _, _ = _run(
        _payload(
            _claim(subject="launch", relation="uses vendor", value="Vendor A"),
            _claim(subject="launch", relation="owns team", value="Team Alpha"),
        ),
        content="We decided the launch uses Vendor A and owns Team Alpha.",
        context=_context(relation_catalog=("uses vendor", "owns team")),
    )

    candidates = [result.candidate for result in results]
    assert all(candidate is not None for candidate in candidates)
    assert candidates[0].subject_key
    assert candidates[1].subject_key
    assert candidates[0].subject_key != candidates[1].subject_key


def test_unknown_relation_is_a_candidate_and_catalog_is_unchanged() -> None:
    relation_catalog = ("uses vendor",)
    resolver = FrozenRelationResolver(relation_catalog, schema_version="relations/v1")
    claim = _claim(relation="replaces", value="Vendor B")
    results, _, _, _ = _run(
        _payload(claim),
        content="Vendor B replaces Vendor A for launch.",
        context=SemanticExtractionContext("ws-alpha", resolver),
    )

    candidate = results[0].candidate
    assert results[0].outcome is CandidateOutcome.UNRESOLVED
    assert CandidateReasonCode.RELATION_UNRESOLVED in results[0].reason_codes
    assert candidate is not None
    assert candidate.relation is None
    assert candidate.subject_key is None
    assert candidate.relation_phrase == "replaces"
    assert candidate.relation_resolution is RelationResolution.NEW_RELATION_CANDIDATE
    assert resolver.canonical_relations == relation_catalog


def test_explicit_relation_alias_maps_only_to_a_frozen_canonical_relation() -> None:
    context = _context(aliases={"uses": "uses vendor"})
    claim = _claim(relation="uses")

    results, _, _, _ = _run(_payload(claim), context=context)

    assert results[0].candidate is not None
    assert results[0].candidate.relation == "uses vendor"
    assert results[0].candidate.relation_resolution is RelationResolution.CANONICAL_RELATION
    assert context.relation_resolver.canonical_relations == ("uses vendor",)


@pytest.mark.parametrize(
    ("extra", "expected_reason"),
    [
        ({"evidence_ref": "forged-evidence"}, CandidateReasonCode.PROVENANCE_INVALID),
        ({"workspace_id": "ws-other"}, CandidateReasonCode.WORKSPACE_MISMATCH),
        ({"unrecognized": "value"}, CandidateReasonCode.MALFORMED_EXTRACTION),
    ],
)
def test_fabricated_provenance_workspace_and_unknown_fields_fail_closed(
    extra: dict[str, object],
    expected_reason: CandidateReasonCode,
) -> None:
    claim = _claim(**extra)

    results, provider, _, _ = _run(_payload(claim), max_attempts=3)

    assert results[0].outcome is CandidateOutcome.REJECTED
    assert expected_reason in results[0].reason_codes
    assert results[0].candidate is None
    assert len(provider.requests) == 1


def test_candidate_subject_and_value_must_be_supported_by_the_source_quote() -> None:
    claim = _claim(value="Vendor Z")

    results, _, _, _ = _run(_payload(claim))

    assert results[0].outcome is CandidateOutcome.UNRESOLVED
    assert CandidateReasonCode.ENTITY_UNRESOLVED in results[0].reason_codes
    assert results[0].candidate is not None
    assert results[0].candidate.value == "Vendor Z"


def test_duplicate_inputs_have_stable_candidate_identity_and_config_changes_are_distinct() -> None:
    first, _, _, _ = _run(_payload(_claim()))
    replay, _, _, _ = _run(_payload(_claim()))
    changed, _, _, _ = _run(
        _payload(_claim()),
        prompt_version="semantic-extraction/v2",
    )

    first_candidate = first[0].candidate
    replay_candidate = replay[0].candidate
    changed_candidate = changed[0].candidate
    assert first_candidate is not None and replay_candidate is not None and changed_candidate is not None
    assert first_candidate.candidate_id == replay_candidate.candidate_id
    assert first_candidate.extraction_fingerprint == replay_candidate.extraction_fingerprint
    assert first_candidate.extraction_fingerprint != changed_candidate.extraction_fingerprint
    assert first_candidate.candidate_id != changed_candidate.candidate_id


def test_saved_extraction_replays_without_a_second_provider_call() -> None:
    artifact, segment = _source("We decided to use Vendor A for launch.")
    context = _context()
    provider = _ScriptedProvider(_payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    first = pipeline.process_segment_with_trace(segment, artifact, context)
    persisted_json = first.extraction.to_dict()
    restored = SemanticExtractionResult.from_dict(persisted_json)
    replay = pipeline.replay_extraction(persisted_json, segment, artifact, context)

    assert first.replayed is False
    assert replay.replayed is True
    assert restored == first.extraction
    assert replay.candidate_results == first.candidate_results
    assert replay.candidate_results[0].candidate.candidate_id == first.candidate_results[0].candidate.candidate_id
    assert len(provider.requests) == 1


def test_saved_extraction_rejects_claim_tampering_when_receipt_is_unchanged() -> None:
    artifact, segment = _source("We decided to use Vendor A for launch.")
    context = _context()
    provider = _ScriptedProvider(_payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    first = pipeline.process_segment_with_trace(segment, artifact, context)
    tampered = deepcopy(first.extraction.to_dict())
    claims = tampered["claims"]
    assert isinstance(claims, list) and isinstance(claims[0], dict)
    claims[0]["value"] = "Vendor B"

    with pytest.raises(ValueError, match="claims_sha256"):
        SemanticExtractionResult.from_dict(tampered)

    assert len(provider.requests) == 1


def test_saved_extraction_cannot_be_replayed_against_a_changed_source_version() -> None:
    first_artifact, first_segment = _source("We decided to use Vendor A for launch.")
    changed_artifact, changed_segment = _source("We decided to use Vendor A for the launch.")
    context = _context()
    provider = _ScriptedProvider(_payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    first = pipeline.process_segment_with_trace(first_segment, first_artifact, context)

    replay = pipeline.replay_extraction(
        first.extraction.to_dict(),
        changed_segment,
        changed_artifact,
        context,
    )

    assert replay.candidate_results[0].outcome is CandidateOutcome.REJECTED
    assert CandidateReasonCode.PROVENANCE_INVALID in replay.candidate_results[0].reason_codes
    assert provider.requests.__len__() == 1


def test_provider_failure_retries_only_explicit_retryable_known_failures() -> None:
    retryable_failure = ModelResponse(
        error=ModelProviderError(
            code="MODEL_RATE_LIMITED",
            category="rate_limit",
            message="rate limited",
            retryable=True,
            outcome="known_failure",
        )
    )
    artifact, segment = _source("We decided to use Vendor A for launch.")
    provider = _ScriptedProvider(retryable_failure, _payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
        max_attempts=2,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    results = pipeline.process_segment(segment, artifact, _context())

    assert results[0].outcome is CandidateOutcome.ACCEPTED
    assert results[0].extraction_receipt.attempt_count == 2
    assert [attempt.failure_code for attempt in results[0].extraction_receipt.attempts] == [
        "MODEL_RATE_LIMITED",
        None,
    ]
    assert len(provider.requests) == 2


def test_unknown_provider_outcome_is_not_replayed() -> None:
    unknown_outcome = ModelResponse(
        error=ModelProviderError(
            code="MODEL_TIMEOUT",
            category="timeout",
            message="timeout",
            retryable=True,
            outcome="unknown_provider_outcome",
        )
    )
    results, provider, _, _ = _run(unknown_outcome, max_attempts=3)

    assert results[0].outcome is CandidateOutcome.REJECTED
    assert CandidateReasonCode.EXTRACTION_FAILED in results[0].reason_codes
    assert results[0].extraction_receipt.attempt_count == 1
    assert len(provider.requests) == 1


def test_request_guard_blocks_before_provider_and_unknown_usage_stays_unknown() -> None:
    artifact, segment = _source("We decided to use Vendor A for launch.")
    provider = _ScriptedProvider(_payload(_claim()))
    guarded_requests = []

    def deny_request(request):
        guarded_requests.append(request)
        return _authorize_request(request, authorized=False)

    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=deny_request,
        request_guard_id="test-deny-all/v1",
        max_attempts=3,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    results = pipeline.process_segment(segment, artifact, _context())

    assert results[0].outcome is CandidateOutcome.REJECTED
    assert CandidateReasonCode.PROVIDER_REQUEST_BLOCKED in results[0].reason_codes
    assert results[0].extraction_receipt.attempt_count == 1
    attempt = results[0].extraction_receipt.attempts[0]
    assert attempt.outcome == "preflight_blocked"
    assert attempt.usage.input_tokens is None
    assert attempt.usage.output_tokens is None
    assert len(guarded_requests) == 1
    assert provider.requests == []


def test_workspace_preflight_blocks_before_sending_source_text() -> None:
    artifact, segment = _source("We decided to use Vendor A for launch.")
    provider = _ScriptedProvider(_payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    with pytest.raises(ValueError, match="workspace mismatch"):
        pipeline.process_segment(segment, artifact, _context(workspace_id="ws-other"))

    assert provider.requests == []


def test_accepted_malformed_response_is_not_replayed() -> None:
    malformed = _payload(_claim(unrecognized="extra"))
    artifact, segment = _source("We decided to use Vendor A for launch.")
    provider = _ScriptedProvider(malformed, _payload(_claim()))
    extractor = ProviderNeutralSemanticExtractor(
        provider,
        provider_id="fake-provider",
        model_id="fake-model-v1",
        request_guard=lambda request: _authorize_request(request),
        request_guard_id="test-budget-guard/v1",
        max_attempts=3,
    )
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())

    results = pipeline.process_segment(segment, artifact, _context())

    assert results[0].outcome is CandidateOutcome.REJECTED
    assert CandidateReasonCode.MALFORMED_EXTRACTION in results[0].reason_codes
    assert results[0].extraction_receipt.attempt_count == 1
    assert len(provider.requests) == 1


def test_metrics_cover_synthetic_class_relation_time_provenance_and_false_authority() -> None:
    results, _, artifact, _ = _run(_payload(_claim()))
    malformed, _, malformed_artifact, _ = _run(_payload(_claim(unrecognized="extra")))
    metrics = evaluate_semantic_extraction(
        [
            SemanticEvaluationCase(
                result=results[0],
                label=SemanticEvaluationLabel(
                    claim_type=ClaimType.DECISION,
                    subject="launch",
                    relation_resolution=RelationResolution.CANONICAL_RELATION,
                    temporal_resolution=TemporalResolution.SOURCE_EVENT_TIME,
                    malformed_output=False,
                ),
                artifact=artifact,
            ),
            SemanticEvaluationCase(
                result=malformed[0],
                label=SemanticEvaluationLabel(malformed_output=True),
                artifact=malformed_artifact,
            ),
        ]
    )

    assert metrics.case_count == 2
    assert metrics.semantic_class_denominator == 1
    assert metrics.subject_denominator == 1
    assert metrics.relation_resolution_denominator == 1
    assert metrics.temporal_resolution_denominator == 1
    assert metrics.provenance_denominator == 1
    assert metrics.malformed_output_denominator == 1
    assert metrics.false_authoritative_denominator == 1
    assert metrics.semantic_class_accuracy == 1.0
    assert metrics.subject_accuracy == 1.0
    assert metrics.relation_resolution_accuracy == 1.0
    assert metrics.temporal_resolution_accuracy == 1.0
    assert metrics.provenance_validity_rate == 1.0
    assert metrics.malformed_output_rejection_rate == 1.0
    assert metrics.false_authoritative_rate == 0.0
