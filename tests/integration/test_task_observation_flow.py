"""Offline M1 acceptance. All databases are caller-owned synthetic tmp_path files."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.artifacts import RawArtifact, source_episode_id_for
from linkloom.semantic_ingestion.candidate_models import CandidateOutcome, SemanticExtractionResult
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext
from linkloom.semantic_ingestion.materialization import MaterializationBlockedError, SemanticDecisionMaterializer
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.task_observation_query import TaskObservationQuery
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tools.contracts import ToolCall


def claim(kind="PLAN", **overrides):
    return {"claim_type": kind, "subject": None, "relation": None, "value": None,
            "temporal_status": "NOT_STATED", "valid_from": None, "valid_to": None,
            "confidence": dict.fromkeys(("claim_type", "entity", "relation", "temporal", "overall"), .95), **overrides}


def task(text, parts, owner=None, deadline_text=None, project=None, vendor=None):
    return {"text": text, "description_parts": parts, "owner": owner, "deadline_text": deadline_text,
            "project": project, "vendor": vendor}


def original_case():
    # Existing public incident recordings, read without changing them or their Gold.
    recordings = json.loads((Path(__file__).parents[1] / "fixtures/semantic_quality_incident/recorded_responses.json").read_text(encoding="utf8"))["recordings"]
    headers = [("2026-10-10T09:00:00+08:00", "林悦"), ("2026-10-10T09:05:00+08:00", "陈晨"),
               ("2026-10-15T10:00:00+08:00", "林悦"), ("2026-10-15T10:05:00+08:00", "陈晨"),
               ("2026-10-15T10:10:00+08:00", "周宁")]
    content = "\n".join(f"[{stamp}] {speaker}: {row['text']}" for (stamp, speaker), row in zip(headers, recordings))
    replies = {row["text"]: deepcopy(row["claims"]) for row in recordings}
    for index, parts, project, vendor, deadline in [
        (1, ["短信接口接入", "联调"], "星河项目", None, "2026年10月14日"),
        (3, ["短信接口接入", "按新供应商星帆通信的接口调整"], None, "星帆通信", "2026年10月18日"),
    ]:
        text = recordings[index]["text"]
        next(c for c in replies[text] if c["claim_type"] == "PLAN")["task"] = task(text, parts, "陈晨", deadline, project, vendor)
    return content, replies


class RecordedProvider:
    def __init__(self, replies):
        self.replies = replies
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        text = json.loads(request.user_input.split("Input:\n", 1)[1])["source_segment"]["text"]
        return ModelResponse(action=ModelAction.tool(ToolCall(
            call_id="offline-task", tool_id=request.available_tools[0].tool_id,
            arguments={"claims": deepcopy(self.replies[text])})))


def prepare(content, replies, *, source_id="meeting.txt", recorded_at=None):
    artifact = RawArtifact(workspace_id="ws-m1", artifact_id=source_id,
                           source_type="timestamped_text" if content.startswith("[") else "manual_text",
                           content=content, ingestion_time=recorded_at or datetime(2026, 10, 20, tzinfo=UTC))
    segments = parse_source_text(artifact).segments
    context = SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(("uses vendor",)))
    provider = RecordedProvider(replies)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="offline-recorded", model_id="m1-script",
        request_guard=lambda request: ProviderRequestAuthorization.approve(request, guard_id="offline-m1", reservation_id="offline", estimated_cost_upper_bound_usd=0),
        request_guard_id="offline-m1", max_attempts=1)
    return artifact, segments, context, provider, SemanticIngestionPipeline(extractor, CandidateValidator())


def register(registry, artifact, segments):
    registry.register_episode(artifact.workspace_id, source_episode_id_for(artifact))
    for segment in segments:
        registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                                  content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)


def test_original_case_persists_queries_and_preserves_review_and_supplier_chain(tmp_path):
    content, replies = original_case()
    artifact, segments, context, provider, pipeline = prepare(content, replies)
    registry = SourceReferenceRegistry()
    register(registry, artifact, segments)
    db = tmp_path / "m1-original.sqlite"
    with TemporalDecisionStore(db, source_registry=registry) as store:
        materializer = SemanticDecisionMaterializer(store)
        results = [result for segment in segments for result in pipeline.process_segment(segment, artifact, context)]
        for result in results:
            materializer.capture(result, expected_workspace_id=artifact.workspace_id)
        observations = TaskObservationQuery(store).list(artifact.workspace_id, source_id=artifact.artifact_id)
        assert len(observations) == 2
        a, b = sorted((row.observation for row in observations), key=lambda o: o.provenance.ordinal)
        assert a.owner == b.owner == "陈晨"
        assert a.deadline == "2026-10-14" and b.deadline == "2026-10-18"
        assert a.description == "短信接口接入；联调"
        assert b.vendor == "星帆通信" and b.project is None
        assert a.observation_id != b.observation_id
        assert all(o.verify(artifact) and o.relation_status == "UNCONFIRMED" for o in (a, b))
        assert len(TaskObservationQuery(store).list("ws-m1", project="星河项目")) == 1
        assert TaskObservationQuery(store).list("foreign-workspace") == ()
        assert store.list_decisions("ws-m1") == ()
        for result in results:
            c = result.candidate
            if c and c.task_observation:
                assert c.valid_from is None and c.valid_to is None
                loaded = materializer.get_candidate("ws-m1", c.candidate_id)
                assert loaded.task_observation == c.task_observation
                with pytest.raises(MaterializationBlockedError) as blocked:
                    materializer.materialize("ws-m1", c.candidate_id)
                assert blocked.value.reason_code == "APPROVAL_MISSING"
        decisions = sorted([r.candidate for r in results if r.candidate and r.candidate.claim_type.value == "DECISION"], key=lambda c: c.valid_from)
        for candidate in decisions:
            with pytest.raises(MaterializationBlockedError):
                materializer.materialize("ws-m1", candidate.candidate_id)
            materializer.approve("ws-m1", candidate.candidate_id, reviewer_id="offline-human", expected_candidate_fingerprint=materializer.candidate_fingerprint("ws-m1", candidate.candidate_id), reason="Synthetic source checked")
            materializer.materialize("ws-m1", candidate.candidate_id)
        old, new = store.get_history("ws-m1", decisions[0].subject_key)
        assert new.supersedes_id == old.decision_id
        assert store.get_as_of("ws-m1", new.subject_key, datetime(2026, 10, 14, tzinfo=UTC)).value == "云桥科技"
        assert store.get_as_of("ws-m1", new.subject_key, datetime(2026, 10, 15, tzinfo=UTC)).value == "星帆通信"
    with TemporalDecisionStore(db, source_registry=registry) as reopened:
        assert len(TaskObservationQuery(reopened).list("ws-m1")) == 2


INDEPENDENT_CASES = [
    ("# 岚海工程\n文档日期：2027-02-01\n负责人：王宁；岚海工程需要迁移缓存；供应商：远川数据；截止日期：2027-02-28。",
     task("负责人：王宁；岚海工程需要迁移缓存；供应商：远川数据；截止日期：2027-02-28。", ["迁移缓存"], "王宁", "2027-02-28", "岚海工程", "远川数据"), "王宁", "2027-02-28"),
    ("Project Aurora; Owner: Mira Cole; deliver export adapter using supplier Cedar Labs by 2028-03-04.",
     task("Project Aurora; Owner: Mira Cole; deliver export adapter using supplier Cedar Labs by 2028-03-04.", ["deliver export adapter"], "Mira Cole", "2028-03-04", "Aurora", "Cedar Labs"), "Mira Cole", "2028-03-04"),
]


@pytest.mark.parametrize("text,proposal,expected_owner,expected_date", INDEPENDENT_CASES)
def test_independent_documents_roundtrip_repeated_ingestion_and_version_history(tmp_path, text, proposal, expected_owner, expected_date):
    replies = {text: [claim("ACTION", task=proposal)]}
    artifact, segments, context, provider, pipeline = prepare(text, replies, source_id="independent.md")
    registry = SourceReferenceRegistry()
    register(registry, artifact, segments)
    with TemporalDecisionStore(tmp_path / "versions.sqlite", source_registry=registry) as store:
        materializer = SemanticDecisionMaterializer(store)
        trace = pipeline.process_segment_with_trace(segments[0], artifact, context)
        candidate = trace.candidate_results[0].candidate
        payload, _ = serialize_candidate(candidate)
        assert deserialize_candidate(payload) == candidate
        assert candidate.task_observation.owner == expected_owner
        assert candidate.task_observation.deadline == expected_date
        assert candidate.task_observation.verify(artifact)
        materializer.capture(trace.candidate_results[0], expected_workspace_id="ws-m1")
        replay = pipeline.replay_extraction(SemanticExtractionResult.from_dict(trace.extraction.to_dict()), segments[0], artifact, context)
        materializer.capture(replay.candidate_results[0], expected_workspace_id="ws-m1")
        assert len(provider.requests) == 1
        # A genuine repeat can have a new import timestamp and receipt latency.
        reimported = replace(artifact, ingestion_time=datetime(2026, 11, 1, tzinfo=UTC))
        repeated = pipeline.process_segment(segments[0], reimported, context)[0]
        materializer.capture(repeated, expected_workspace_id="ws-m1")
        assert len(store.list_semantic_candidate_rows("ws-m1")) == 1
        before = TaskObservationQuery(store).list("ws-m1")[0].observation
        for segment in segments:
            registry.unregister_evidence("ws-m1", segment.evidence_span.evidence_ref)
        changed_text = text.replace("迁移缓存", "迁移缓存并验证容量").replace("export adapter", "export adapter and validate retries")
        changed_proposal = deepcopy(proposal)
        changed_proposal["text"] = proposal["text"].replace("迁移缓存", "迁移缓存并验证容量").replace("export adapter", "export adapter and validate retries")
        changed_proposal["description_parts"] = [p.replace("迁移缓存", "迁移缓存并验证容量").replace("export adapter", "export adapter and validate retries") for p in proposal["description_parts"]]
        newer, new_segments, ctx, _, next_pipeline = prepare(changed_text, {changed_text: [claim("ACTION", task=changed_proposal)]}, source_id=artifact.artifact_id)
        register(registry, newer, new_segments)
        materializer.capture(next_pipeline.process_segment(new_segments[0], newer, ctx)[0], expected_workspace_id="ws-m1")
        current = TaskObservationQuery(store).list("ws-m1")
        history = TaskObservationQuery(store).list("ws-m1", include_history=True)
        assert len(current) == 1 and len(history) == 2
        assert current[0].observation.observation_id != before.observation_id
        assert {row.source_status for row in history} == {"CURRENT", "NOT_CURRENT"}
        assert any(row.observation == before for row in history)
        assert all(row.observation.relation_status == "UNCONFIRMED" for row in history)


def test_failed_extraction_and_missing_task_fields_are_different_states(tmp_path):
    artifact, segments, context, provider, pipeline = prepare("完成接口测试，日期待定。", {"完成接口测试，日期待定。": [claim(task=task("完成接口测试，日期待定。", ["接口测试"]))]})
    result = pipeline.process_segment(segments[0], artifact, context)[0]
    assert result.candidate.task_observation.extraction_status == "EXTRACTED"
    assert "owner" in result.candidate.task_observation.unknown_fields
    provider.replies[artifact.content] = [{"malformed": True}]
    failed = pipeline.process_segment(segments[0], artifact, context)[0]
    assert failed.outcome is CandidateOutcome.REJECTED and failed.candidate is None
    assert failed.reason_codes[0].value == "MALFORMED_EXTRACTION"
