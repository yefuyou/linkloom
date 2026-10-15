"""Small offline-capable orchestration for one parsed source segment."""

from __future__ import annotations

from dataclasses import dataclass

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import (
    CandidateValidationResult,
    SemanticExtractionResult,
)
from linkloom.semantic_ingestion.extraction import SemanticExtractionContext, SemanticExtractor
from linkloom.semantic_ingestion.timestamped_text import TimestampedTextSegment
from linkloom.semantic_ingestion.validation import CandidateValidator


@dataclass(frozen=True, slots=True)
class SemanticIngestionResult:
    extraction: SemanticExtractionResult
    candidate_results: tuple[CandidateValidationResult, ...]
    replayed: bool


class SemanticIngestionPipeline:
    """Run extraction and local validation without persisting or materializing."""

    def __init__(self, extractor: SemanticExtractor, validator: CandidateValidator) -> None:
        if not callable(getattr(extractor, "extract", None)):
            raise ValueError("extractor must implement SemanticExtractor.extract")
        if not isinstance(validator, CandidateValidator):
            raise ValueError("validator must be CandidateValidator")
        self._extractor = extractor
        self._validator = validator

    def process_segment(
        self,
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> tuple[CandidateValidationResult, ...]:
        return self.process_segment_with_trace(segment, artifact, context).candidate_results

    def process_segments(
        self,
        segments: tuple[TimestampedTextSegment, ...] | list[TimestampedTextSegment],
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> tuple[CandidateValidationResult, ...]:
        """Extract a validated segment batch and flag cross-segment conflicts."""
        if not isinstance(segments, (tuple, list)):
            raise TypeError("segments must be a tuple or list")
        if not segments:
            return ()
        # Validate the full source batch before the first Provider request.
        for segment in segments:
            self._validate_source(segment, artifact, context)

        candidate_results: list[CandidateValidationResult] = []
        for segment in segments:
            extraction = self._extractor.extract(segment, context)
            candidate_results.extend(
                self._validator.validate(extraction, segment, artifact, context)
            )
        return self._validator.flag_conflicts(tuple(candidate_results))

    def process_segment_with_trace(
        self,
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> SemanticIngestionResult:
        self._validate_source(segment, artifact, context)
        extraction = self._extractor.extract(segment, context)
        candidate_results = self._validator.validate(
            extraction,
            segment,
            artifact,
            context,
        )
        return SemanticIngestionResult(
            extraction=extraction,
            candidate_results=self._validator.flag_conflicts(candidate_results),
            replayed=False,
        )

    def replay_extraction(
        self,
        saved_extraction: SemanticExtractionResult | dict[str, object],
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> SemanticIngestionResult:
        """Revalidate a serialized accepted extraction without calling a Provider."""
        self._validate_source(segment, artifact, context)
        extraction = (
            SemanticExtractionResult.from_dict(saved_extraction)
            if isinstance(saved_extraction, dict)
            else saved_extraction
        )
        if not isinstance(extraction, SemanticExtractionResult):
            raise TypeError("saved_extraction must be a result or its versioned JSON object")
        candidate_results = self._validator.validate(
            extraction,
            segment,
            artifact,
            context,
        )
        return SemanticIngestionResult(
            extraction=extraction,
            candidate_results=self._validator.flag_conflicts(candidate_results),
            replayed=True,
        )

    @staticmethod
    def _validate_source(
        segment: TimestampedTextSegment,
        artifact: RawArtifact,
        context: SemanticExtractionContext,
    ) -> None:
        if not isinstance(segment, TimestampedTextSegment):
            raise TypeError("segment must be a Step 1 TimestampedTextSegment")
        if not isinstance(artifact, RawArtifact):
            raise TypeError("artifact must be RawArtifact")
        if not isinstance(context, SemanticExtractionContext):
            raise TypeError("context must be SemanticExtractionContext")
        if artifact.workspace_id != context.expected_workspace_id:
            raise ValueError("workspace mismatch; source content was not sent to the Provider")
        if not segment.evidence_span.verify(artifact):
            raise ValueError("invalid source evidence; segment was not sent to the Provider")
