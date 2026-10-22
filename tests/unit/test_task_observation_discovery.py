from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_observation_discovery import DiscoveryLimits, TaskObservationDiscoveryService
from linkloom.tools.contracts import ToolCall


def source(text, **kwargs):
    return RawArtifact(workspace_id="public-workspace", artifact_id="new-meeting",
                       source_type="timestamped_text" if text.startswith("[2029-") else "manual_text",
                       content=text, ingestion_time=datetime(2029, 6, 1, tzinfo=UTC), **kwargs)


def claim(text, *parts, owner=None, project=None, vendor=None, deadline_text=None, kind="PLAN"):
    return dict(claim_type=kind, subject=None, relation=None, value=None,
                temporal_status="NOT_STATED", valid_from=None, valid_to=None,
                confidence={key: .9 for key in ("claim_type", "entity", "relation", "temporal", "overall")},
                task=dict(text=text, description_parts=list(parts), owner=owner, project=project,
                          vendor=vendor, deadline_text=deadline_text))


class FakeModel:
    """Scripted tool output; never a semantic discovery score."""
    def __init__(self, payload, hook=None):
        self.payload, self.hook, self.requests = payload, hook, []

    def complete(self, request):
        self.requests.append(request)
        if self.hook:
            self.hook()
        return ModelResponse(action=ModelAction.tool(ToolCall(
            call_id="fake-discovery", tool_id=request.available_tools[0].tool_id,
            arguments=self.payload)), usage=ModelUsage(input_tokens=11, output_tokens=7, total_tokens=18))


def setup(artifact, claims=(), *, payload=None, limits=None, hook=None):
    registry = SourceReferenceRegistry()
    for segment in parse_source_text(artifact).segments:
        registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                                   content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    current = [artifact]
    provider = FakeModel(payload if payload is not None else {"claims": list(claims)}, hook)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="fake", model_id="fake-discovery",
        request_guard=lambda request: ProviderRequestAuthorization.approve(request,
            guard_id="discovery-test", reservation_id="fake", estimated_cost_upper_bound_usd=0),
        request_guard_id="discovery-test", max_attempts=1)
    service = TaskObservationDiscoveryService(extractor, source_registry=registry,
        current_source=lambda workspace, identity: current[0], limits=limits or DiscoveryLimits())
    return service, provider, registry, current


def discover(service, *artifacts):
    return service.discover(tuple(artifacts), workspace_id="public-workspace")


def test_real_tool_contract_multiple_distinct_tasks_and_exact_fields():
    first = "项目：青竹；负责人：林枫；供应商：云桥；完成日志迁移；截止日期：2029-06-20。"
    second = "项目：青竹；负责人：林枫；供应商：云桥；提交容量报告；截止日期：2029-06-20。"
    artifact = source(first + second)
    service, provider, _, _ = setup(artifact, [
        claim(first, "完成日志迁移", owner="林枫", project="青竹", vendor="云桥", deadline_text="2029-06-20"),
        claim(second, "提交容量报告", owner="林枫", project="青竹", vendor="云桥", deadline_text="2029-06-20")])
    result = discover(service, artifact)
    assert len(result.valid_observations) == 2 and not result.partial_observations
    assert len({o.observation_id for o in result.valid_observations}) == 2
    assert all(o.verify(artifact) for o in result.valid_observations)
    assert len(provider.requests) == 1
    assert provider.requests[0].available_tools[0].tool_id == "emit_semantic_candidates"
    assert result.coverage_complete and not result.semantic_exhaustive
    assert result.to_dict()["receipts"][0]["attempts"][0]["usage"]["total_tokens"] == 18


def test_first_person_missing_owner_year_and_mixed_correction():
    text = "我负责青竹项目的索引测试，截止日期：6月20日；资料纠错：旧资料把服务器内存记错了。"
    artifact = source("[2029-06-01T09:00:00Z] 林枫: " + text)
    service, _, _, _ = setup(artifact, [claim(text, "索引测试", owner="林枫", project="青竹项目", deadline_text="6月20日")])
    result = discover(service, artifact)
    observation, = result.partial_observations
    assert observation.owner == "林枫" and observation.deadline is None
    assert observation.deadline_precision == "MONTH_DAY" and observation.verify(artifact)
    assert "DEADLINE_YEAR_MISSING" in observation.validation_issues


@pytest.mark.parametrize("text", [
    "建议林枫完成索引测试。", "如果预算批准，林枫完成索引测试。", "同事说：‘林枫完成索引测试。’",
    "林枫不需要完成索引测试。", "任务取消：完成索引测试。", "假设林枫完成索引测试。",
    "今天讨论完成索引测试。", "未采纳完成索引测试。", "大家可能会完成索引测试。",
    "提议林枫完成索引测试。", "假如林枫完成索引测试。", "据说林枫完成索引测试。",
    "无须完成索引测试。", "任务终止：完成索引测试。", "今天仅讨论完成索引测试。",
])
def test_cropped_model_task_cannot_hide_non_task_sentence(text):
    artifact = source(text)
    service, _, _, _ = setup(artifact, [claim("完成索引测试", "索引测试")])
    result = discover(service, artifact)
    assert not result.valid_observations and not result.partial_observations
    assert any(f.reason == "NON_ASSERTED_TASK" for f in result.failures)


def test_empty_success_does_not_assert_absence():
    artifact = source("今天讨论了数据库的历史，没有形成具体安排。")
    service, _, _, _ = setup(artifact)
    result = discover(service, artifact)
    assert result.sources[0].status == "NO_TASKS_DETECTED"
    assert result.coverage_complete and not result.semantic_exhaustive


def test_cross_message_work_stays_separate_exact_partial_observations():
    artifact = source("[2029-06-01T09:00:00Z] 林枫: 我负责青竹项目的索引迁移。\n"
                      "[2029-06-01T09:01:00Z] 林枫: 迁移还包括补齐回归脚本，截止日期：2029-06-20。")
    service, _, _, _ = setup(artifact, [claim(artifact.content, "索引迁移", "补齐回归脚本",
                                            owner="林枫", project="青竹项目", deadline_text="2029-06-20")])
    result = discover(service, artifact)
    assert len(result.partial_observations) == 2
    assert all(o.verify(artifact) for o in result.partial_observations)
    assert [o.deadline for o in result.partial_observations] == [None, "2029-06-20"]
    assert [o.owner for o in result.partial_observations] == ["林枫", None]
    assert len(result.groups) == 1 and result.groups[0].status == "MODEL_PROPOSED"


def test_stale_revoked_or_foreign_inputs_never_reach_provider():
    artifact = source("安排完成索引测试。")
    for variant in ("stale", "revoked", "foreign", "unavailable"):
        service, provider, registry, current = setup(artifact)
        incoming = artifact
        if variant == "stale":
            current[0] = replace(artifact, content="新版讨论。")
        elif variant == "revoked":
            registry.unregister_evidence(artifact.workspace_id, parse_source_text(artifact).segments[0].evidence_span.evidence_ref)
        elif variant == "foreign":
            incoming = replace(artifact, workspace_id="foreign")
        else:
            current[0] = None
        result = discover(service, incoming)
        assert not provider.requests and len(result.unprocessed_sources) == 1


def test_update_during_call_withholds_all_observations():
    artifact = source("安排完成索引测试。")
    service, provider, _, current = setup(artifact, [claim(artifact.content, "索引测试")])
    provider.hook = lambda: current.__setitem__(0, replace(artifact, content="任务取消。"))
    result = discover(service, artifact)
    assert not result.partial_observations and not result.valid_observations
    assert result.failures[0].reason == "SOURCE_CHANGED_OR_UNAVAILABLE"
    assert not result.coverage_complete and len(result.receipts) == 1


def test_overlong_duplicate_malformed_and_partial_parse_are_explicit():
    artifact = source("安排完成索引测试。")
    service, provider, _, _ = setup(artifact, limits=DiscoveryLimits(max_chars=4))
    assert discover(service, artifact).unprocessed_sources[0].reason == "SOURCE_TOO_LARGE"
    assert not provider.requests
    service, provider, _, _ = setup(artifact, payload={"claims": "invalid"})
    result = discover(service, artifact, artifact)
    assert len(provider.requests) == 1 and len(result.failures) == 1
    assert any(row.status == "DUPLICATE" for row in result.sources)
    broken = source("[2029-06-01T09:00:00Z] 林枫: 安排完成索引测试。\n[2029-06-01Tbad] 段: 无效时间。")
    service, _, _, _ = setup(broken)
    result = discover(service, broken)
    assert not result.coverage_complete and any(f.reason == "SOURCE_PARSE_INCOMPLETE" for f in result.failures)


def test_missing_fields_hallucinations_and_absent_task_are_not_silent_success():
    artifact = source("安排完成索引测试。")
    service, _, _, _ = setup(artifact, [claim(artifact.content, "索引测试", owner="虚构", project="虚构项目",
                                            deadline_text="2029-06-20", vendor="虚构供方")])
    result = discover(service, artifact)
    observation, = result.partial_observations
    assert observation.owner is observation.project is observation.vendor is observation.deadline is None
    assert set(observation.validation_issues) == {"OWNER_UNGROUNDED", "PROJECT_UNGROUNDED", "VENDOR_UNGROUNDED", "DEADLINE_UNGROUNDED"}
    assert observation.verify(artifact)
    incomplete = claim(artifact.content, "索引测试")
    incomplete["task"] = None
    service, _, _, _ = setup(artifact, [incomplete])
    assert discover(service, artifact).failures[0].reason == "TASK_PROPOSAL_MISSING"


def test_current_version_update_requires_new_registration_and_old_unavailable_is_not_current():
    old = source("安排完成索引测试。")
    updated = replace(old, content="安排完成容量测试。")
    service, provider, registry, current = setup(old, [claim(updated.content, "容量测试")])
    current[0] = updated
    assert discover(service, old).unprocessed_sources
    assert discover(service, updated).unprocessed_sources
    assert not provider.requests
    for segment in parse_source_text(updated).segments:
        registry.register_evidence(updated.workspace_id, segment.evidence_span.evidence_ref,
            content_hash=updated.content_hash, source_version=updated.artifact_version_id)
    assert len(discover(service, updated).partial_observations) == 1


def test_registration_removed_during_call_is_withheld_and_loader_errors_do_not_leak():
    artifact = source("安排完成索引测试。")
    service, provider, registry, _ = setup(artifact, [claim(artifact.content, "索引测试")])
    provider.hook = lambda: registry.unregister_evidence(artifact.workspace_id, parse_source_text(artifact).segments[0].evidence_span.evidence_ref)
    result = discover(service, artifact)
    assert not result.partial_observations and result.failures[0].reason == "SOURCE_CHANGED_OR_UNAVAILABLE"
    service.current_source = lambda *args: (_ for _ in ()).throw(PermissionError("private detail"))
    assert "private detail" not in json.dumps(discover(service, artifact).to_dict())


def test_exact_duplicate_task_proposals_are_collapsed_but_conflicting_same_location_is_quarantined():
    text = "负责人：林枫；安排完成索引测试。"
    artifact = source(text)
    original = claim(text, "索引测试", owner="林枫")
    service, _, _, _ = setup(artifact, [original, original])
    result = discover(service, artifact)
    assert len(result.partial_observations) == 1
    assert result.failures[0].reason == "DUPLICATE_TASK_PROPOSAL"
    service, _, _, _ = setup(artifact, [original, claim(text, "索引测试")])
    result = discover(service, artifact)
    assert not result.valid_observations and not result.partial_observations
    assert result.failures[0].reason == "OBSERVATION_EXTRACTION_CONFLICT"


def test_whole_document_budget_saturation_and_unicode_size_are_reported():
    artifact = source("安排完成索引测试。")
    other = replace(artifact, artifact_id="other")
    service, provider, registry, current = setup(artifact, limits=DiscoveryLimits(max_calls=1))
    # The explicit host lookup authorizes both documents independently.
    service.current_source = lambda ws, identity: artifact if identity == artifact.artifact_id else other
    for segment in parse_source_text(other).segments:
        registry.register_evidence(other.workspace_id, segment.evidence_span.evidence_ref,
            content_hash=other.content_hash, source_version=other.artifact_version_id)
    result = discover(service, artifact, other)
    assert len(provider.requests) == 1 and result.unprocessed_sources[0].reason == "CALL_BUDGET_EXHAUSTED"
    service, provider, _, _ = setup(artifact, limits=DiscoveryLimits(max_bytes=5))
    assert discover(service, artifact).unprocessed_sources[0].reason == "SOURCE_TOO_LARGE"
    assert not provider.requests
    non_task = {**claim(artifact.content, "索引测试", kind="FACT"), "task": None}
    service, _, _, _ = setup(artifact, [non_task] * 20)
    result = discover(service, artifact)
    assert not result.coverage_complete and result.failures[0].reason == "CLAIM_LIMIT_REACHED"


@pytest.mark.parametrize("limits", [dict(max_sources=0), dict(max_calls=True), dict(max_chars=17000), dict(max_bytes=-1)])
def test_invalid_limits_are_rejected(limits):
    with pytest.raises(ValueError):
        DiscoveryLimits(**limits)


def test_empty_batch_wrong_type_and_too_many_sources():
    artifact = source("讨论索引历史。")
    service, provider, _, _ = setup(artifact, limits=DiscoveryLimits(max_sources=1))
    assert not discover(service).valid_observations
    with pytest.raises(TypeError):
        service.discover([artifact], workspace_id="public-workspace")
    with pytest.raises(ValueError, match="max_sources"):
        discover(service, artifact, artifact)
    assert not provider.requests


def test_later_call_revoking_an_earlier_source_withholds_the_earlier_observation():
    artifact = source("安排完成索引测试。")
    other = replace(artifact, artifact_id="other")
    service, provider, registry, _ = setup(artifact, [claim(artifact.content, "索引测试")])
    service.current_source = lambda ws, identity: artifact if identity == artifact.artifact_id else other
    for segment in parse_source_text(other).segments:
        registry.register_evidence(other.workspace_id, segment.evidence_span.evidence_ref,
            content_hash=other.content_hash, source_version=other.artifact_version_id)
    def revoke_on_second_call():
        if len(provider.requests) == 2:
            registry.unregister_evidence(artifact.workspace_id, parse_source_text(artifact).segments[0].evidence_span.evidence_ref)
    provider.hook = revoke_on_second_call
    result = discover(service, artifact, other)
    assert len(result.partial_observations) == 1
    assert result.partial_observations[0].provenance.artifact_id == "other"
    assert not result.coverage_complete


@pytest.mark.parametrize("later", ["上述任务取消，不需要继续执行。", "This task is cancelled."])
def test_referential_cancellation_cannot_release_an_earlier_task(later):
    work = "我负责青竹项目的索引迁移。"
    artifact = source("[2029-06-01T09:00:00Z] 林枫: " + work + "\n[2029-06-01T09:01:00Z] 林枫: " + later)
    service, _, _, _ = setup(artifact, [claim(work, "索引迁移", owner="林枫")])
    result = discover(service, artifact)
    assert not result.valid_observations and not result.partial_observations
    assert result.failures[0].reason == "NON_ASSERTED_TASK"
