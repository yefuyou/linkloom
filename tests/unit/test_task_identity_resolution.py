"""Offline identity contracts; these synthetic names are independent of the Dev set."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, ObservationRef, RelationAssessment


def observe(text, *, source_id="A.md", workspace="ws-independent", project="青岚项目", description=None, task_text=None,
            segment_index=0, owner=None):
    artifact = RawArtifact(workspace_id=workspace, artifact_id=source_id,
                           source_type="timestamped_text" if text.startswith("[") else "manual_text",
                           content=text, ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
    segment = parse_source_text(artifact).segments[segment_index]
    observation = build_task_observation(
        TaskProposal(task_text or text, (description or task_text or text,), owner=owner, project=project), segment, artifact)
    assert observation.verify(artifact)
    return artifact, observation


def registry_for(*artifacts):
    registry = SourceReferenceRegistry()
    for artifact in artifacts:
        for segment in parse_source_text(artifact).segments:
            registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
                                      content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    return registry


def resolve(left, right, *, extra=(), registry=None, artifacts=None):
    from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
    supplied = tuple(x[0] for x in (left, right, *extra)) if artifacts is None else artifacts
    return assess_same_task(left[1], right[1], artifacts=supplied,
                            source_registry=registry or registry_for(*supplied),
                            context_observations=tuple(x[1] for x in extra))


def test_observation_reference_is_versioned_and_ignores_capture_time():
    artifact, observation = observe("青岚项目任务 FLOW-31：完成报表导出。")
    reference = ObservationRef.from_observation(observation)
    assert reference == ObservationRef.from_dict(reference.to_dict())
    assert reference == ObservationRef.from_observation(
        replace(observation, recorded_at=observation.recorded_at + timedelta(days=2)))
    changed = observe(artifact.content + "更新说明。")
    assert reference.artifact_version_id != ObservationRef.from_observation(changed[1]).artifact_version_id


def test_exact_quote_readback_rejects_wrong_text_and_new_source_version():
    artifact, observation = observe("青岚项目任务 FLOW-31：完成报表导出。")
    start = artifact.content.index("FLOW-31")
    evidence = EvidenceQuote(observation.provenance, start, start + 7, "FLOW-31", "TASK_KEY")
    assert evidence.verify(artifact)
    assert not evidence.verify(replace(artifact, content=artifact.content + "新版本"))
    with pytest.raises(ValueError):
        replace(evidence, quote="FLOW-99")


def test_same_scoped_key_is_supported_without_using_owner_dates_or_similarity():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：增加审计列。", source_id="B.md")
    result = resolve(b, a)
    assert result.verdict == "SUPPORTED"
    assert "SCOPED_TASK_KEY" in result.reason_codes
    assert result.predicate == "SAME_TASK"
    assert all(item.verify(next(x for x in (a[0], b[0]) if x.artifact_id == item.span.artifact_id))
               for item in result.supporting_evidence)
    assert result.to_dict()["evidence_validity"] is True
    restored = RelationAssessment.from_dict(json.loads(json.dumps(result.to_dict(), ensure_ascii=False)))
    assert restored == result
    assert restored.assessment_id == resolve(b, a).assessment_id
    assert restored.assessment_id == resolve(a, b).assessment_id
    tampered = result.to_dict()
    tampered["verdict"] = "REFUTED"
    with pytest.raises(ValueError):
        RelationAssessment.from_dict(tampered)


def test_explicit_reference_can_confirm_identity_despite_description_change():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目的审计流输出；本条与A.md的FLOW-31是同一个任务。", source_id="B.md")
    result = resolve(b, a)
    assert result.verdict == "SUPPORTED"
    assert result.subject.observation_id == b[1].observation_id
    assert result.object.observation_id == a[1].observation_id
    assert "EXPLICIT_TASK_REFERENCE" in result.reason_codes


def test_explicit_distinct_task_is_refuted_and_not_unknown():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-32：完成沙箱导出；这是独立任务，并非A.md的FLOW-31。", source_id="B.md")
    result = resolve(b, a)
    assert result.verdict == "REFUTED"
    assert result.counter_evidence and not result.missing_evidence


def test_similar_work_with_same_project_remains_unknown():
    a = observe("青岚项目：负责人：许南；完成报表导出；截止日期：2028-01-14。")
    b = observe("青岚项目：负责人：许南；完成报表导出适配；截止日期：2028-01-18。", source_id="B.md")
    result = resolve(b, a)
    assert result.verdict == "UNKNOWN" and result.missing_evidence
    assert not result.supporting_evidence and not result.counter_evidence


@pytest.mark.parametrize("wording", [
    "如果通过确认，本条与A.md的FLOW-31是同一个任务。",
    "建议本条与A.md的FLOW-31是同一个任务。",
    "是否本条与A.md的FLOW-31是同一个任务？",
    "无法确认本条与A.md的FLOW-31是同一个任务。",
    "同事说：“本条与A.md的FLOW-31是同一个任务。”",
    '引用示例：本条与A.md的FLOW-31是同一个任务。',
    '> 本条与A.md的FLOW-31是同一个任务。',
    '`本条与A.md的FLOW-31是同一个任务。`',
    "并非不是A.md的FLOW-31，这是待确认的解释。",
    "如果变更被批准，这是独立任务，并非A.md的FLOW-31。",
])
def test_conditional_quoted_uncertain_and_double_negative_are_not_assertions(wording):
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目的导出适配。\n" + wording, source_id="B.md")
    assert resolve(b, a).verdict == "UNKNOWN"


def test_unrelated_negative_and_quoted_sentence_do_not_cancel_later_assertion():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目的导出适配。不是会议纪要。\n同事说：“不是同一任务。”\n"
                "本条与A.md的FLOW-31是同一个任务。", source_id="B.md")
    assert resolve(b, a).verdict == "SUPPORTED"


def test_valid_support_and_counter_evidence_produce_conflict():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目的导出适配。本条与A.md的FLOW-31是同一个任务。"
                "本条不是A.md的FLOW-31，是独立任务。", source_id="B.md")
    result = resolve(b, a)
    assert result.verdict == "CONFLICT"
    assert result.supporting_evidence and result.counter_evidence


def test_identity_predicate_for_another_reference_is_not_attached_to_this_pair():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    c = observe("青岚项目任务 FLOW-33：完成审计流。", source_id="C.md")
    b = observe("青岚项目导出适配；本条与A.md的FLOW-31相关，与C.md的FLOW-33是同一个任务。",
                source_id="B.md")
    assert resolve(b, a, extra=(c,)).verdict == "UNKNOWN"


def test_counter_predicate_for_another_reference_does_not_refute_this_pair():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    c = observe("青岚项目任务 FLOW-33：完成审计流。", source_id="C.md")
    b = observe("青岚项目导出适配；本条与A.md的FLOW-31相关，本条与C.md的FLOW-33不是同一个任务。",
                source_id="B.md")
    assert resolve(b, a, extra=(c,)).verdict == "UNKNOWN"


def test_explicit_reused_key_does_not_override_counter_evidence():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：完成另一个导出；编号被复用，这是独立任务，并非A.md的FLOW-31。",
                source_id="B.md")
    assert resolve(b, a).verdict == "REFUTED"


@pytest.mark.parametrize("project,text", [
    ("瀚海项目", "瀚海项目任务 FLOW-31：完成报表导出。"),
    (None, "任务 FLOW-31：完成报表导出。"),
])
def test_same_key_with_different_or_missing_project_is_not_identity(project, text):
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe(text, source_id="B.md", project=project)
    assert resolve(b, a).verdict == "UNKNOWN"


def test_explicit_reference_cannot_bypass_conflicting_project_scope():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("瀚海项目的接口适配。本条与A.md的FLOW-31是同一个任务。",
                source_id="B.md", project="瀚海项目")
    assert resolve(b, a).verdict == "UNKNOWN"


def test_missing_project_does_not_block_a_unique_explicit_reference_or_copy_fields():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("完成导出适配。本条与A.md的FLOW-31是同一个任务。", source_id="B.md", project=None)
    assert resolve(b, a).verdict == "SUPPORTED"
    assert b[1].project is None


@pytest.mark.parametrize("namespace", ["账务", "搜索"])
def test_explicit_number_namespace_is_checked(namespace):
    a = observe("青岚项目；系统：账务；任务 FLOW-31：完成报表导出。")
    b = observe(f"青岚项目；系统：{namespace}；任务 FLOW-31：调整报表列。", source_id="B.md")
    assert resolve(b, a).verdict == ("SUPPORTED" if namespace == "账务" else "UNKNOWN")


@pytest.mark.parametrize("header_a,header_b,expected", [
    ("系统：账务", "系统：搜索", "UNKNOWN"),
    ("System: Billing", "System: Search", "UNKNOWN"),
    ("系统：账务", "系统：账务", "SUPPORTED"),
])
def test_namespace_in_source_context_is_not_ignored_when_task_text_is_narrow(header_a, header_b, expected):
    task_a = "青岚项目任务 FLOW-31：完成报表导出。"
    task_b = "青岚项目任务 FLOW-31：调整报表列。"
    a = observe(header_a + "\n" + task_a, task_text=task_a)
    b = observe(header_b + "\n" + task_b, source_id="B.md", task_text=task_b)
    result = resolve(b, a)
    assert result.verdict == expected
    if expected == "SUPPORTED":
        assert any(e.role == "TASK_NAMESPACE" and e.quote == header_a for e in result.supporting_evidence)


def test_unbound_namespace_in_another_source_segment_prevents_a_default_scope():
    task = "青岚项目任务 FLOW-31：完成报表导出。"
    header = "[2026-10-10T08:00:00Z] 许南: 系统：账务\n"
    a = observe(header + "[2026-10-10T09:00:00Z] 许南: " + task, task_text=task, segment_index=1)
    b = observe(task, source_id="B.md")
    assert resolve(b, a).verdict == "UNKNOWN"


def test_task_key_reuse_in_context_prevents_shared_key_confirmation():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：调整报表列。", source_id="B.md")
    context = observe("青岚项目任务 FLOW-31 的编号被复用，无法唯一指代任务。", source_id="C.md")
    assert resolve(b, a, extra=(context,)).verdict == "UNKNOWN"


def test_explicit_identity_uncertainty_blocks_the_shared_key_shortcut():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：调整报表列。尚未确认本条与A.md的FLOW-31是同一个任务。",
                source_id="B.md")
    assert resolve(b, a).verdict == "UNKNOWN"


def test_conditional_hint_does_not_cancel_an_independent_explicit_assertion():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目导出适配。如果通过核对，本条与A.md的FLOW-31是同一个任务。"
                "本条与A.md的FLOW-31是同一个任务。", source_id="B.md")
    assert resolve(b, a).verdict == "SUPPORTED"


def test_semicolon_ends_conditional_scope_before_independent_assertion():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目导出适配。如果通过核对，本条与A.md的FLOW-31是同一个任务；"
                "本条与A.md的FLOW-31是同一个任务。", source_id="B.md")
    assert resolve(b, a).verdict == "SUPPORTED"


@pytest.mark.parametrize("wording", ["编号没有复用", "编号没有重复", "编号不存在冲突", "并未复用编号"])
def test_negated_reuse_statement_is_not_positive_ambiguity_evidence(wording):
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：调整报表列。", source_id="B.md")
    context = observe(f"青岚项目任务 FLOW-31 的{wording}。", source_id="C.md")
    assert resolve(b, a, extra=(context,)).verdict == "SUPPORTED"


def test_reimport_and_input_order_do_not_change_assessment_identity():
    a = observe("海棠项目任务 BUILD-802：生成增量索引。", project="海棠项目")
    b = observe("海棠项目任务 BUILD-802：修补增量索引。", source_id="B.md", project="海棠项目")
    initial = resolve(b, a)
    later_a = (a[0], replace(a[1], recorded_at=a[1].recorded_at + timedelta(days=1)))
    later_b = (b[0], replace(b[1], recorded_at=b[1].recorded_at + timedelta(days=2)))
    assert initial.assessment_id == resolve(later_b, later_a, artifacts=(b[0], a[0])).assessment_id


def test_ambiguous_source_basename_is_not_a_unique_reference():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。", source_id="team-a/A.md")
    other = observe("青岚项目任务 FLOW-32：完成沙箱导出。", source_id="team-b/A.md")
    b = observe("青岚项目导出适配。本条与A.md的FLOW-31是同一个任务。", source_id="B.md")
    assert resolve(b, a, extra=(other,)).verdict == "UNKNOWN"


def test_unqualified_reference_to_multi_task_source_is_ambiguous():
    text = "青岚项目任务 FLOW-31：完成报表导出。\n青岚项目任务 FLOW-32：完成沙箱导出。"
    a = observe(text)
    b = observe("青岚项目导出适配。本条就是A.md的同一任务。", source_id="B.md")
    assert resolve(b, a).verdict == "UNKNOWN"


@pytest.mark.parametrize("layout", ["partial_span", "multiple_segments"])
def test_unqualified_file_reference_does_not_assume_unexposed_tasks_are_absent(layout):
    first = "青岚项目：完成索引导出。"
    second = "青岚项目：完成独立验收。"
    content = first + "\n" + second
    if layout == "multiple_segments":
        content = "[2026-10-10T08:00:00Z] 许南: " + first + "\n[2026-10-10T09:00:00Z] 莫离: " + second
    a = observe(content, task_text=first)
    b = observe("青岚项目导出适配；本条就是A.md的同一任务。", source_id="B.md")
    assert resolve(b, a).verdict == "UNKNOWN"


@pytest.mark.parametrize("fault", ["cross_workspace", "new_version", "bad_hash", "missing_registry",
                                  "altered_observation", "not_provided", "wrong_type"])
def test_invalid_input_and_source_identity_are_not_assessable(fault):
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：调整报表列。", source_id="B.md")
    artifacts = (a[0], b[0])
    registry = registry_for(*artifacts)
    if fault == "cross_workspace":
        b = observe(b[0].content, source_id="B.md", workspace="ws-other")
        artifacts = (a[0], b[0])
        registry = registry_for(*artifacts)
    elif fault == "new_version":
        artifacts = (replace(a[0], content=a[0].content + "新增资料。"), b[0])
    elif fault == "bad_hash":
        registry.register_evidence(a[0].workspace_id, a[1].provenance.evidence_ref,
                                  content_hash="0" * 64, source_version=a[0].artifact_version_id)
    elif fault == "missing_registry":
        registry = SourceReferenceRegistry()
    elif fault == "altered_observation":
        b = (b[0], replace(b[1], description="编造的结构化描述"))
    elif fault == "not_provided":
        b = (b[0], replace(b[1], extraction_status="NOT_PROVIDED"))
    else:
        b = (b[0], {"raw_text": "invalid"})
    result = resolve(b, a, registry=registry, artifacts=artifacts)
    assert result.verdict == "NOT_ASSESSABLE"
    assert not result.supporting_evidence
    assert RelationAssessment.from_dict(result.to_dict()) == result


def test_conflicting_extractions_of_same_observation_are_not_silently_selected():
    a = observe("青岚项目任务 FLOW-31：完成报表导出。")
    b = observe("青岚项目任务 FLOW-31：调整报表列。", source_id="B.md")
    segment = parse_source_text(a[0]).segments[0]
    alternative = build_task_observation(TaskProposal(a[1].raw_text, ("报表导出",), project="青岚项目"), segment, a[0])
    assert alternative.observation_id == a[1].observation_id and alternative.verify(a[0])
    result = resolve(b, a, extra=((a[0], alternative),))
    assert result.verdict == "NOT_ASSESSABLE"
    assert "EXTRACTION_CONFLICT" in result.reason_codes


def test_other_names_and_key_prefixes_are_not_fixture_specific():
    a = observe("海棠项目任务 BUILD-802：负责人：陆青；生成增量索引。", project="海棠项目", owner="陆青")
    b = observe("海棠项目任务 BUILD-802：负责人：方禾；修补增量索引。", source_id="B.md", project="海棠项目", owner="方禾")
    assert (a[1].owner, b[1].owner) == ("陆青", "方禾")
    assert resolve(b, a).verdict == "SUPPORTED"
