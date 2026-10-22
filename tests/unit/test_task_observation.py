from dataclasses import replace
from datetime import UTC, datetime

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation


def observe(text, *, speaker=None, **proposal):
    content = f"[2026-10-10T09:05:00+08:00] {speaker}: {text}" if speaker else text
    artifact = RawArtifact(workspace_id="ws-tasks", artifact_id="minutes", source_type="timestamped_text" if speaker else "manual_text",
                           content=content, ingestion_time=datetime(2026, 10, 20, tzinfo=UTC))
    segment = parse_source_text(artifact).segments[0]
    task = TaskProposal(text=text, description_parts=(proposal.pop("description", text),), **proposal)
    return artifact, segment, build_task_observation(task, segment, artifact)


def test_first_person_owner_and_chinese_deadline_have_readable_exact_evidence():
    text = "我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调。"
    artifact, segment, observation = observe(
        text, speaker="陈晨", description="短信接口接入", owner="陈晨",
        deadline_text="2026年10月14日", project="星河项目")
    assert observation.owner == "陈晨"
    assert observation.deadline == "2026-10-14"
    assert observation.deadline_precision == "DAY"
    assert observation.deadline_role == "TASK_DEADLINE"
    assert observation.project == "星河项目" and observation.vendor is None
    assert observation.relation_status == "UNCONFIRMED"
    assert observation.recorded_at == artifact.ingestion_time
    assert observation.event_time == segment.event_time
    assert observation.verify(artifact)
    owner_evidence = [e for e in observation.field_evidence if e.field == "owner"]
    assert {e.basis for e in owner_evidence} == {"SPEAKER", "SOURCE_TEXT"}
    for evidence in observation.field_evidence:
        assert artifact.content[evidence.char_start:evidence.char_end] == evidence.quote


@pytest.mark.parametrize("text,proposal,expected,precision,issue", [
    ("负责人：王宁；工作：完成审计；截止日期：2027-02-28。",
     {"description": "完成审计", "owner": "王宁", "deadline_text": "2027-02-28"},
     "2027-02-28", "DAY", None),
    ("Project Aurora; Owner: Mira Cole; deliver export adapter by 2028-03-04.",
     {"description": "deliver export adapter", "owner": "Mira Cole", "project": "Aurora", "deadline_text": "2028-03-04"},
     "2028-03-04", "DAY", None),
    ("计划在10月18日前完成接口测试。", {"description": "接口测试", "deadline_text": "10月18日"},
     None, "MONTH_DAY", "DEADLINE_YEAR_MISSING"),
    ("文档日期：2026-10-10；完成接口测试。", {"description": "接口测试", "deadline_text": "2026-10-10"},
     None, "UNKNOWN", "DEADLINE_ROLE_UNRESOLVED"),
    ("会议日期：2026-10-10；负责人：小林；测试接口。", {"description": "测试接口"},
     None, "UNKNOWN", None),
    ("计划在2027年2月30日前完成接口测试。", {"description": "接口测试", "deadline_text": "2027年2月30日"},
     None, "UNKNOWN", "DEADLINE_INVALID"),
])
def test_independent_wording_missing_year_and_time_roles(text, proposal, expected, precision, issue):
    artifact, _, task = observe(text, **proposal)
    assert task.deadline == expected and task.deadline_precision == precision
    if issue:
        assert issue in task.validation_issues
    assert task.verify(artifact)
    assert task.relation_status == "UNCONFIRMED"


def test_missing_owner_is_not_inferred_from_an_untrusted_speaker_or_incidental_name():
    text = "我负责接口测试；陈晨将参加会议；计划在2026年10月18日前完成。"
    _, _, task = observe(text, description="接口测试", owner="陈晨", deadline_text="2026年10月18日")
    assert task.owner is None and "owner" in task.unknown_fields
    assert "OWNER_UNGROUNDED" in task.validation_issues
    artifact, segment, _ = observe(text, speaker="陈晨", description="接口测试", owner="陈晨")
    forged = replace(segment, speaker="伪造姓名")
    task = build_task_observation(TaskProposal(text, ("接口测试",), owner="伪造姓名"), forged, artifact)
    assert task.owner is None


def test_hallucinated_fields_and_non_responsibility_first_person_are_not_accepted():
    text = "我建议由同事讨论接口测试；截止日期：2026-10-18。"
    _, _, task = observe(text, speaker="陈晨", description="编造的交付物", owner="陈晨",
                         deadline_text="2026-10-19", project="星河项目", vendor="星帆通信")
    assert task.description is None and task.owner is None and task.deadline is None
    assert task.project is None and task.vendor is None
    assert task.validation_status == "INVALID"


def test_observation_identity_and_field_evidence_survive_serialization():
    artifact, segment, task = observe("负责人：周琪；完成索引迁移；截止日期：2028-05-20。",
                                     description="索引迁移", owner="周琪", deadline_text="2028-05-20")
    restored = type(task).from_dict(task.to_dict())
    assert restored == task and restored.verify(artifact)
    later_import = replace(artifact, ingestion_time=datetime(2026, 11, 1, tzinfo=UTC))
    repeated = build_task_observation(TaskProposal(task.raw_text, ("索引迁移",), owner="周琪",
                                                deadline_text="2028-05-20"), segment, later_import)
    assert repeated.observation_id == task.observation_id
    assert not restored.verify(replace(artifact, content=artifact.content + "来源变化"))


def test_two_tasks_in_one_segment_have_different_observation_ids():
    artifact = RawArtifact(workspace_id="ws-tasks", artifact_id="two", source_type="manual_text",
                           content="负责人：王宁；完成索引迁移。负责人：李明；提交回归报告。")
    segment = parse_source_text(artifact).segments[0]
    tasks = [build_task_observation(TaskProposal(text, (description,), owner=owner), segment, artifact)
             for text, description, owner in [("负责人：王宁；完成索引迁移。", "索引迁移", "王宁"),
                                               ("负责人：李明；提交回归报告。", "回归报告", "李明")]]
    assert len({task.observation_id for task in tasks}) == 2


@pytest.mark.parametrize("text", [
    "不是负责人：王宁；完成接口测试。",
    "建议王宁负责接口测试。",
    "我建议我负责接口测试。",
    "同事说：“我负责接口测试”。",
])
def test_negated_proposed_or_quoted_responsibility_is_not_a_confirmed_owner(text):
    _, _, observation = observe(text, speaker="王宁", description="接口测试", owner="王宁")
    assert observation.owner is None


@pytest.mark.parametrize("text", [
    "日期不是截止日期：2027-02-28；完成接口测试。",
    "尚未确定截止日期：2027-02-28；完成接口测试。",
])
def test_negated_or_unconfirmed_deadline_role_stays_unknown(text):
    _, _, observation = observe(text, description="接口测试", deadline_text="2027-02-28")
    assert observation.deadline is None


def test_successful_but_unbound_proposal_is_not_reported_as_omitted_extraction():
    artifact, segment, _ = observe("完成接口测试。", description="接口测试")
    observation = build_task_observation(TaskProposal("编造任务", ("编造任务",)), segment, artifact)
    assert observation.extraction_status == "EXTRACTED"
    assert observation.validation_status == "INVALID"


def test_multiline_speaker_binding_points_to_the_header_and_deadline_role_is_readable():
    content = "[2026-10-10T09:05:00Z] 2026:\n我负责接口测试，计划在2026年10月18日前完成。"
    artifact = RawArtifact(workspace_id="ws", artifact_id="multiline", source_type="timestamped_text", content=content)
    segment = parse_source_text(artifact).segments[0]
    task = build_task_observation(TaskProposal(segment.body_text, ("接口测试",), owner="2026", deadline_text="2026年10月18日"), segment, artifact)
    speaker = next(e for e in task.field_evidence if e.basis == "SPEAKER")
    assert speaker.char_start == content.index("2026:")
    assert any("前完成" in e.quote for e in task.field_evidence if e.field == "deadline")
    assert task.verify(artifact)


def test_repeated_deadline_evidence_is_bound_to_the_affirmative_occurrence():
    text = "日期不是截止日期：2027-02-28；截止日期：2027-02-28；完成接口测试。"
    artifact, _, task = observe(text, description="接口测试", deadline_text="2027-02-28")
    evidence = next(e for e in task.field_evidence if e.field == "deadline")
    assert evidence.char_start == text.rindex("截止日期")
    assert task.deadline == "2027-02-28" and task.verify(artifact)


@pytest.mark.parametrize("text,proposal", [
    ("不是星河项目；完成接口测试。", {"project": "星河项目"}),
    ("供应商不是星帆通信；完成接口测试。", {"vendor": "星帆通信"}),
    ("不是供应商：星帆通信；完成接口测试。", {"vendor": "星帆通信"}),
])
def test_negated_project_and_vendor_mentions_remain_unknown(text, proposal):
    _, _, task = observe(text, description="接口测试", **proposal)
    assert task.project is None and task.vendor is None


def test_repeated_entity_and_owner_evidence_uses_the_valid_role_occurrence():
    text = "不是星河项目；不是负责人：王宁；项目：星河项目；负责人：王宁；完成接口测试。"
    artifact, _, task = observe(text, description="接口测试", project="星河项目", owner="王宁")
    assert task.project == "星河项目" and task.owner == "王宁"
    project = next(e for e in task.field_evidence if e.field == "project")
    owner = next(e for e in task.field_evidence if e.field == "owner")
    assert project.char_start >= text.index("项目：")
    assert owner.char_start == text.rindex("负责人：")
    assert task.verify(artifact)


@pytest.mark.parametrize("text,owner", [
    ("负责人：王宁；完成接口测试。", "王"),
    ("Owner: Mira Cole; deliver export adapter.", "Mira"),
])
def test_incomplete_owner_names_are_not_silently_accepted(text, owner):
    _, _, task = observe(text, description="接口测试" if "接口测试" in text else "export adapter", owner=owner)
    assert task.owner is None


@pytest.mark.parametrize("prefix", ["无", "非", "未确定"])
def test_explicit_absent_or_unconfirmed_deadline_remains_unknown(prefix):
    _, _, task = observe(prefix + "截止日期：2027-02-28；完成接口测试。", description="接口测试", deadline_text="2027-02-28")
    assert task.deadline is None
