"""Provider-neutral semantic ingestion contracts through Step 3.

Extraction yields non-authoritative candidates. The separately authorized
Step 3 materializer applies policy/review and writes into temporal memory.
"""

from linkloom.semantic_ingestion.artifacts import (
    EvidenceSpan,
    RawArtifact,
    RawArtifactSourceType,
    TIMESTAMPED_TEXT_PARSER_VERSION,
)
from linkloom.semantic_ingestion.timestamped_text import (
    ParseStatus,
    SourceParseIssue,
    TimestampedTextParseResult,
    TimestampedTextSegment,
    parse_timestamped_text,
)
from linkloom.semantic_ingestion.candidate_models import (
    CandidateConfidence,
    CandidateDecisionFact,
    CandidateOutcome,
    CandidateReasonCode,
    CandidateValidationResult,
    ClaimType,
    EntityResolution,
    ExtractionAttempt,
    ExtractionReceipt,
    RelationResolution,
    SemanticExtractionResult,
    TemporalBasis,
    TemporalResolution,
)
from linkloom.semantic_ingestion.extraction import (
    SEMANTIC_EXTRACTOR_VERSION,
    SEMANTIC_EXTRACTION_SCHEMA_VERSION,
    TEMPORAL_POLICY_VERSION,
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
    SemanticExtractionContext,
    SemanticExtractor,
)
from linkloom.semantic_ingestion.metrics import (
    SemanticEvaluationCase,
    SemanticEvaluationLabel,
    SemanticEvaluationMetrics,
    evaluate_semantic_extraction,
)
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.pipeline import SemanticIngestionResult
from linkloom.semantic_ingestion.relations import (
    FrozenRelationResolver,
    RelationResolutionResult,
)
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.semantic_ingestion.materialization import (
    CalibrationProfile,
    CandidateReviewResolution,
    CandidateWorkflowState,
    MaterializationAssessment,
    MaterializationBlockedError,
    MaterializationOutcome,
    MaterializationPolicy,
    MaterializationPolicyDecision,
    MaterializationReceipt,
    SemanticDecisionMaterializer,
)

__all__ = [
    "CandidateConfidence",
    "CandidateDecisionFact",
    "CandidateOutcome",
    "CandidateReasonCode",
    "CandidateReviewResolution",
    "CandidateValidationResult",
    "CandidateValidator",
    "CandidateWorkflowState",
    "CalibrationProfile",
    "ClaimType",
    "EntityResolution",
    "EvidenceSpan",
    "ExtractionAttempt",
    "ExtractionReceipt",
    "FrozenRelationResolver",
    "ParseStatus",
    "ProviderNeutralSemanticExtractor",
    "ProviderRequestAuthorization",
    "RawArtifact",
    "RawArtifactSourceType",
    "RelationResolution",
    "RelationResolutionResult",
    "SEMANTIC_EXTRACTOR_VERSION",
    "SEMANTIC_EXTRACTION_SCHEMA_VERSION",
    "SemanticEvaluationCase",
    "SemanticEvaluationLabel",
    "SemanticEvaluationMetrics",
    "SemanticExtractionContext",
    "SemanticExtractionResult",
    "SemanticExtractor",
    "SemanticIngestionPipeline",
    "SemanticIngestionResult",
    "SemanticDecisionMaterializer",
    "MaterializationAssessment",
    "MaterializationBlockedError",
    "MaterializationOutcome",
    "MaterializationPolicy",
    "MaterializationPolicyDecision",
    "MaterializationReceipt",
    "SourceParseIssue",
    "TIMESTAMPED_TEXT_PARSER_VERSION",
    "TEMPORAL_POLICY_VERSION",
    "TimestampedTextParseResult",
    "TimestampedTextSegment",
    "TemporalResolution",
    "TemporalBasis",
    "evaluate_semantic_extraction",
    "parse_timestamped_text",
]
