"""Ordinary documents keep exact evidence and date precision without invented speech time."""
from dataclasses import replace
from datetime import date
import json

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import DocumentTextSegment, parse_source_text
from linkloom.semantic_ingestion.timestamped_text import ParseStatus, TimestampedTextSegment


def artifact(text):
    return RawArtifact(workspace_id="ws", artifact_id="src", source_type="timestamped_text", content=text)


def test_markdown_exact_span_and_separate_date_only_metadata():
    raw = artifact("# 采购会议\r\n文档日期：2026-10-09\r\n会议日期：2026-10-08\r\n\r\n决定自2026-11-01起采用供应商乙。\r\n")
    result = parse_source_text(raw)
    segment, = result.segments
    assert isinstance(segment, DocumentTextSegment)
    assert result.parser_version == "document-text/v1"
    assert segment.event_time is None and segment.speaker is None
    assert segment.document_date == date(2026, 10, 9)
    assert segment.event_date == date(2026, 10, 8)
    assert segment.evidence_span.verify(raw)
    assert segment.body_text == raw.content
    assert (segment.evidence_span.line_start, segment.evidence_span.line_end) == (1, 5)


def test_missing_or_conflicting_dates_stay_unknown_and_markdown_link_is_not_transcript():
    segment, = parse_source_text(artifact("[资料](https://example.test)\n文档日期：2026-10-01\n文档日期：2026-10-02\n仅讨论风险。\n")).segments
    assert segment.event_time is None and segment.event_date is None and segment.document_date is None
    task, = parse_source_text(artifact("[x] 任务：跟踪供应商进展\n")).segments
    assert isinstance(task, DocumentTextSegment) and task.event_time is None
    agenda, = parse_source_text(artifact("[Agenda] Owner: Bob\n")).segments
    assert isinstance(agenda, DocumentTextSegment)
    dated_label, = parse_source_text(artifact("[2026-10-08] Meeting: general notes\n")).segments
    assert isinstance(dated_label, DocumentTextSegment)


def test_transcript_dispatch_preserves_strict_timestamp_validation():
    good = parse_source_text(artifact("[2026-10-08T09:00:00Z] Alice: We decided to use A.\n"))
    assert isinstance(good.segments[0], TimestampedTextSegment)
    bad = parse_source_text(artifact("[2026-10-08T09:00:00] Alice: Missing timezone.\n"))
    assert bad.status is ParseStatus.FAILED and bad.issues and not bad.segments


def test_unknown_time_cannot_become_source_effective_time_and_v2_is_durable():
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    from linkloom.tools.contracts import ToolCall
    from linkloom.semantic_ingestion.candidate_persistence import serialize_candidate, deserialize_candidate
    from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext
    from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
    from linkloom.semantic_ingestion.relations import FrozenRelationResolver
    from linkloom.semantic_ingestion.validation import CandidateValidator

    class Fake:
        calls = 0

        def complete(self, request):
            self.calls += 1
            return ModelResponse(action=ModelAction.tool(ToolCall(
                call_id="fake", tool_id=request.available_tools[0].tool_id,
                arguments={"claims": [{"claim_type": "DECISION", "subject": "launch",
                    "relation": "uses vendor", "value": "Vendor A", "temporal_status": "SOURCE_EVENT_TIME",
                    "valid_from": None, "valid_to": None,
                    "confidence": {key: 0.99 for key in ("claim_type", "entity", "relation", "temporal", "overall")}}]},
            )))

    raw = artifact("# Decision\nWe decided to use Vendor A for launch.\n")
    segment, = parse_source_text(raw).segments
    provider = Fake()
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="fake", model_id="fake",
        request_guard=lambda request: ProviderRequestAuthorization.approve(request, guard_id="fake", reservation_id="fake", estimated_cost_upper_bound_usd=0), request_guard_id="fake", max_attempts=1)
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    context = SemanticExtractionContext("ws", FrozenRelationResolver(("uses vendor",)))
    result, = pipeline.process_segment(segment, raw, context)
    candidate = result.candidate
    assert result.outcome.value == "UNRESOLVED"
    assert "TEMPORAL_UNRESOLVED" in [r.value for r in result.reason_codes]
    assert candidate.event_time is None and candidate.document_date is None and candidate.event_date is None
    assert candidate.valid_from is None and candidate.valid_to is None
    encoded, fingerprint = serialize_candidate(candidate)
    assert json.loads(encoded)["record_version"] == "candidate-decision-fact/v2"
    restored = deserialize_candidate(encoded)
    assert restored == candidate and serialize_candidate(restored) == (encoded, fingerprint)
    with pytest.raises(ValueError, match="source metadata"):
        replace(candidate, document_date=date(2026, 10, 1))
    # Version / workspace substitution still fails before the first Provider call.
    other = RawArtifact(workspace_id="ws", artifact_id="different", source_type="timestamped_text", content=raw.content)
    with pytest.raises(ValueError, match="invalid source evidence"):
        pipeline.process_segment(segment, other, context)
    assert provider.calls == 1


@pytest.mark.parametrize("wrong_date", ["2026-10-09", "2026-10-08"])
def test_provider_cannot_reuse_document_or_meeting_date_as_effective_time(wrong_date):
    from test_semantic_ingestion_candidates import _ScriptedProvider, _authorize_request, _claim, _context
    from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor
    from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
    from linkloom.semantic_ingestion.validation import CandidateValidator
    raw = RawArtifact(workspace_id="ws-alpha", artifact_id="doc", source_type="timestamped_text",
        content="# launch\n文档日期：2026-10-09\n会议日期：2026-10-08\nWe decided to use Vendor A for launch effective 2026-11-01.\n")
    segment, = parse_source_text(raw).segments
    provider = _ScriptedProvider({"claims": [_claim(temporal_status="EXPLICIT", valid_from=wrong_date)]})
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="fake", model_id="fake",
        request_guard=_authorize_request, request_guard_id="test-budget-guard/v1", max_attempts=1)
    result, = SemanticIngestionPipeline(extractor, CandidateValidator()).process_segment(segment, raw, _context())
    assert result.outcome.value == "UNRESOLVED"
    assert result.candidate.valid_from is None
    assert "TEMPORAL_UNRESOLVED" in [code.value for code in result.reason_codes]
    from linkloom.semantic_ingestion.candidate_models import TemporalBasis, TemporalResolution
    with pytest.raises(ValueError, match="effective-time role"):
        replace(result.candidate, temporal_basis=TemporalBasis.EXPLICIT,
                temporal_resolution=TemporalResolution.EXPLICIT, valid_from=date.fromisoformat(wrong_date))


def test_document_effective_role_preserves_date_precision_and_timezone_equivalence():
    from datetime import UTC, datetime
    from linkloom.semantic_ingestion.document_text import document_valid_time_is_grounded
    assert document_valid_time_is_grounded("决定自2026-11-01起采用乙", date(2026, 11, 1))
    assert document_valid_time_is_grounded("effective from 2026-11-01T08:00:00+08:00", datetime(2026, 11, 1, tzinfo=UTC))
    assert document_valid_time_is_grounded("valid until 2026-12-01", date(2026, 12, 1), end=True)
    assert not document_valid_time_is_grounded("会议日期：2026-10-08", date(2026, 10, 8))
    assert not document_valid_time_is_grounded("文档编写于2026-10-09，决策于2026-11-01生效", date(2026, 10, 9))
