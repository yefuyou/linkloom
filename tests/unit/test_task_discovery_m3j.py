"""M3-J offline contracts; scripted claims are not model quality scores."""
from dataclasses import replace
import re

import pytest

from tests.unit.test_task_observation_discovery import claim, discover, setup, source


def observations(result):
    return result.valid_observations + result.partial_observations


REFUSAL_CASES = [
    ("安排完成日志迁移。Task cancelled.", "日志迁移", False),
    ("安排完成日志迁移。任务取消：整理旧截图。", "日志迁移", True),
    ("安排完成日志迁移。不要执行上述任务。", "日志迁移", False),
    ("安排完成日志迁移。取消执行上述任务。", "日志迁移", False),
    ("安排完成日志迁移。取消：上述任务。", "日志迁移", False),
    ("安排完成日志迁移。停止执行上述任务。", "日志迁移", False),
    ("安排完成日志迁移。Do not execute the above task.", "日志迁移", False),
    ("安排完成日志迁移。任务取消。", "日志迁移", False),
    ("安排完成日志迁移。The task is cancelled.", "日志迁移", False),
    ("只有预算批准，才安排完成日志迁移。", "日志迁移", False),
    ("一旦预算批准，就安排完成日志迁移。", "日志迁移", False),
    ("当预算批准时，安排完成日志迁移。", "日志迁移", False),
    ("在预算批准的前提下，安排完成日志迁移。", "日志迁移", False),
    ("预算批准后再安排完成日志迁移。", "日志迁移", False),
    ("预算批准才安排完成日志迁移。", "日志迁移", False),
    ("安排完成日志迁移。讨论取消上述任务。", "日志迁移", True),
    ("安排完成日志迁移。探讨终止上述任务。", "日志迁移", True),
    ("安排完成日志迁移。取消上述任务。", "日志迁移", False),
    ("安排完成日志迁移；取消上述任务。", "日志迁移", False),
    ("安排完成日志迁移。此任务取消。", "日志迁移", False),
    ("安排完成日志迁移。Cancel this task.", "日志迁移", False),
    ("若预算批准，安排完成日志迁移。", "日志迁移", False),
    ("只要预算批准，安排完成日志迁移。", "日志迁移", False),
    ("除非预算批准，才安排完成日志迁移。", "日志迁移", False),
    ("安排完成日志迁移。若预算批准再执行。", "日志迁移", False),
    ("同事说：'完成日志迁移。'", "日志迁移", False),
    ("同事说：`完成日志迁移。`", "日志迁移", False),
    ("安排完成日志迁移。'This task is cancelled.'", "日志迁移", True),
    ("安排完成日志迁移；安排提交容量报告；这项任务取消。", "日志迁移", True),
    ("安排完成日志迁移；安排提交容量报告；这项任务取消。", "容量报告", False),
    ("上述任务取消；安排提交容量报告。", "容量报告", True),
    ("建议以后换肤，同时安排完成日志迁移。", "日志迁移", True),
    ("安排完成日志迁移，同时取消整理旧截图。", "日志迁移", True),
    ("禁止完成日志迁移。", "日志迁移", False),
    ("不得完成日志迁移。", "日志迁移", False),
    ("不应完成日志迁移。", "日志迁移", False),
    ("不安排完成日志迁移。", "日志迁移", False),
    ("不计划完成日志迁移。", "日志迁移", False),
    ("任务放弃：完成日志迁移。", "日志迁移", False),
    ("撤回完成日志迁移。", "日志迁移", False),
    ("作废完成日志迁移。", "日志迁移", False),
    ("安排完成日志迁移。无须继续。", "日志迁移", False),
    ("安排完成日志迁移。今天风很大。上述任务取消。", "日志迁移", False),
    ("安排完成日志迁移。无须再做日志迁移。", "日志迁移", False),
    ("安排完成日志迁移。我们不执行日志迁移。", "日志迁移", False),
    ("安排完成日志迁移；这项任务取消。", "日志迁移", False),
    ("任务取消：完成日志迁移；提交容量报告。", "容量报告", False),
    ("建议完成日志迁移；提交容量报告。", "容量报告", False),
    ("安排完成日志迁移。讨论仅供记录。上述任务取消。", "日志迁移", False),
    ("安排完成日志迁移。建议取消日志迁移。", "日志迁移", True),
    ("安排完成日志迁移。", "日志迁移", True),
    ("安排提交容量报告。", "容量报告", True),
    ("安排完成日志迁移。安排提交容量报告。上述任务取消。", "日志迁移", True),
    ("安排完成日志迁移。安排提交容量报告。上述任务取消。", "容量报告", False),
    ("安排完成日志迁移。会议引用：“上述任务取消。”", "日志迁移", True),
    ("安排完成日志迁移。会议引用：\n> This task is cancelled.", "日志迁移", True),
    ("安排完成日志迁移。会议引用：“日志迁移取消。”", "日志迁移", True),
    ("建议以后更换配色；安排完成日志迁移。", "日志迁移", True),
    ("任务取消：整理旧截图；安排完成日志迁移。", "日志迁移", True),
    ("安排完成日志迁移；不再安排整理旧截图。", "日志迁移", True),
    ("安排完成日志迁移。日志迁移取消。", "日志迁移", False),
    ("安排完成日志迁移。上述任务取消。", "日志迁移", False),
    ("安排完成日志迁移。This task is cancelled.", "日志迁移", False),
    ("会议引用：“安排完成日志迁移。”", "日志迁移", False),
    ("如果预算批准，安排完成日志迁移。", "日志迁移", False),
    ("如果预算批准；安排完成日志迁移。", "日志迁移", False),
    ("建议安排完成日志迁移。", "日志迁移", False),
    ("无需完成日志迁移。", "日志迁移", False),
]


@pytest.mark.parametrize("text,work,accepted", REFUSAL_CASES)
def test_scoped_refusal(text, work, accepted):
    artifact = source(text)
    # Deliberately cropped mistaken PLAN: host must check original context.
    excerpt = work if text.count(work) == 1 else next(
        clause for clause in re.split(r"[。；;]", text) if work in clause)
    service, provider, _, _ = setup(artifact, [claim(excerpt, work)])
    result = discover(service, artifact)
    assert bool(observations(result)) is accepted
    assert all(o.verify(artifact) for o in observations(result))
    assert len(provider.requests) == 1


def test_uncovered_continuation_is_visible_without_synthesizing_work():
    first = "我负责青竹项目的索引迁移，截止日期：2029-06-20。"
    second = "索引迁移还包括补齐回归脚本。"
    artifact = source("[2029-06-01T09:00:00Z] 林枫: " + first +
                      "\n[2029-06-01T09:01:00Z] 周岚: " + second)
    service, provider, _, _ = setup(artifact, [claim(first, "索引迁移", owner="林枫",
        project="青竹项目", deadline_text="2029-06-20")])
    result = discover(service, artifact)
    assert [o.description for o in observations(result)] == ["索引迁移"]
    coverage = result.to_dict()["candidate_coverage"]
    assert len(coverage) == 2
    assert coverage[0]["verified_description_parts"] == ("索引迁移",)
    assert coverage[1]["verified_description_parts"] == ()
    assert coverage[1]["evidence_ref"] != coverage[0]["evidence_ref"]
    assert result.semantic_exhaustive is False and len(provider.requests) == 1


def test_cross_message_continuation_preserves_fields_per_original_speaker():
    artifact = source("[2029-06-01T09:00:00Z] 林枫: 我负责青竹项目的索引迁移，截止日期：2029-06-20。\n"
                      "[2029-06-01T09:01:00Z] 周岚: 迁移还包括补齐回归脚本。")
    service, _, _, _ = setup(artifact, [claim(artifact.content, "索引迁移", "补齐回归脚本",
        owner="林枫", project="青竹项目", deadline_text="2029-06-20")])
    result = discover(service, artifact)
    first, second = observations(result)
    assert (first.owner, first.project, first.deadline) == ("林枫", "青竹项目", "2029-06-20")
    assert (second.owner, second.project, second.deadline) == (None, None, None)
    assert first.provenance.evidence_ref != second.provenance.evidence_ref
    assert all(o.verify(artifact) for o in observations(result))
    assert len(result.groups) == 1 and len(result.groups[0].observation_ids) == 2


def test_same_fields_do_not_merge_and_missing_year_is_not_inherited():
    first = "项目：青竹；负责人：林枫；完成日志迁移；截止日期：2029-06-20。"
    second = "项目：青竹；负责人：林枫；提交容量报告；截止日期：6月20日。"
    artifact = source(first + second)
    service, _, _, _ = setup(artifact, [
        claim(first, "完成日志迁移", owner="林枫", project="青竹", deadline_text="2029-06-20"),
        claim(second, "提交容量报告", owner="林枫", project="青竹", deadline_text="6月20日")])
    result = discover(service, artifact)
    assert len(observations(result)) == len(result.groups) == 2
    missing = next(o for o in observations(result) if o.description == "提交容量报告")
    assert missing.deadline is None and missing.deadline_precision == "MONTH_DAY"
    assert "DEADLINE_YEAR_MISSING" in missing.validation_issues
    assert all(o.verify(artifact) for o in observations(result))


@pytest.mark.parametrize("state", ["stale", "revoked", "foreign", "during_call"])
def test_candidate_inventory_never_releases_withheld_source_references(state):
    artifact = source("安排完成日志迁移。")
    service, provider, registry, current = setup(artifact, [claim("日志迁移", "日志迁移")])
    incoming = artifact
    if state == "stale":
        current[0] = replace(artifact, content="新版原文。")
    elif state == "revoked":
        from linkloom.semantic_ingestion.document_text import parse_source_text
        ref = parse_source_text(artifact).segments[0].evidence_span.evidence_ref
        registry.unregister_evidence(artifact.workspace_id, ref)
    elif state == "foreign":
        incoming = replace(artifact, workspace_id="foreign")
    else:
        provider.hook = lambda: current.__setitem__(0, replace(artifact, content="新版原文。"))
    result = discover(service, incoming)
    assert not observations(result)
    assert result.to_dict()["candidate_coverage"] == []


def test_wide_proposal_with_duplicate_quoted_work_stays_unresolved():
    text = "引用：“完成日志迁移。”安排完成日志迁移。"
    artifact = source(text)
    service, _, _, _ = setup(artifact, [claim(text, "完成日志迁移")])
    result = discover(service, artifact)
    assert not observations(result)
    assert not result.coverage_complete
    # An exact independent model excerpt can still release the actual work.
    service, _, _, _ = setup(artifact, [claim("安排完成日志迁移。", "完成日志迁移")])
    assert len(observations(discover(service, artifact))) == 1
