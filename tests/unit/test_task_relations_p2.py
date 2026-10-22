"""P2 fixed proposals: source truth is necessary but never sufficient proof."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, ProjectRef, ChangeRef, RelationAssessment
from linkloom.semantic_ingestion.task_relation_context import assess_relation
from linkloom.semantic_ingestion.task_relation_context import source_context_quote


def setup(text, raw=None, project=None, *, source='B.md', workspace='ws-p2'):
    artifact = RawArtifact(workspace_id=workspace, artifact_id=source, source_type='manual_text',
                           content=text, ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
    segment = parse_source_text(artifact).segments[0]
    raw = raw or text
    obs = build_task_observation(TaskProposal.from_dict(dict(text=raw, description_parts=['适配'],
        owner=None, deadline_text=None, project=project, vendor=None)), segment, artifact)
    assert obs.verify(artifact)
    registry = SourceReferenceRegistry()
    registry.register_evidence(workspace, segment.evidence_span.evidence_ref,
                              content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    evidence = EvidenceQuote(segment.evidence_span, segment.evidence_span.char_start,
        segment.evidence_span.char_end, segment.evidence_span.quote, 'RELATION_PROPOSAL')
    return artifact, obs, registry, evidence


def test_explicit_project_and_roundtrip_do_not_mutate_m1():
    a, o, registry, proof = setup('我负责青岚项目的接口适配。', project='青岚项目')
    before = o.to_dict()
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'SUPPORTED'
    assert result.predicate == 'IN_PROJECT'
    assert result.evidence_validity
    assert o.to_dict() == before
    assert RelationAssessment.from_dict(result.to_dict()).to_dict() == result.to_dict()


@pytest.mark.parametrize('prefix', ['如果', '建议', '据说', '引用：', '> '])
def test_nonassertive_project_is_not_confirmed(prefix):
    a, o, registry, proof = setup(prefix + '我负责青岚项目的接口适配。', project='青岚项目')
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'UNKNOWN'


def test_other_project_mention_in_same_document_does_not_bind_task():
    raw = '我负责接口适配。'
    a, o, registry, proof = setup('项目背景：青岚项目。\n' + raw, raw)
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'UNKNOWN'


def test_cross_workspace_project_rejected():
    a, o, registry, proof = setup('我负责青岚项目的接口适配。', project='青岚项目')
    result = assess_relation('IN_PROJECT', o, ProjectRef('foreign', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'NOT_ASSESSABLE'


def alias_fixture(text=None):
    a = RawArtifact(workspace_id='ws-p2', artifact_id='aliases.md', source_type='manual_text',
        content=text or '项目说明：本工作区的“羽舟”是“青岚项目”的内部代号，两者在本期网关接入资料中指同一项目。')
    registry = SourceReferenceRegistry()
    quote = source_context_quote(a)
    registry.register_evidence('ws-p2', quote.span.evidence_ref, content_hash=a.content_hash, source_version=a.artifact_version_id)
    result = assess_relation('PROJECT_ALIAS', ProjectRef('ws-p2', '羽舟'), ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[quote])
    return a, registry, quote, result


def test_alias_and_scoped_heading_bind_only_the_right_task():
    a, _, alias_quote, alias = alias_fixture()
    assert alias.verdict == 'SUPPORTED'
    raw = '我负责接口适配。'
    b, observation, registry, _ = setup('# 羽舟\n本节网关接入任务仅属于羽舟。\n' + raw +
        '\n# 海棠项目\n本节任务仅属于海棠项目。\n我负责重试适配。', raw)
    quote = source_context_quote(b)
    for artifact, q in ((a, alias_quote), (b, quote)):
        registry.register_evidence('ws-p2', q.span.evidence_ref, content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    result = assess_relation('IN_PROJECT', observation, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a, b], source_registry=registry, evidence=[quote], alias_assessments=[alias])
    assert result.verdict == 'SUPPORTED'
    assert result.premise_ids == (alias.assessment_id,)
    assert observation.project is None
    other = build_task_observation(TaskProposal('我负责重试适配。', ('重试适配',)), parse_source_text(b).segments[0], b)
    rejected = assess_relation('IN_PROJECT', other, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a, b], source_registry=registry, evidence=[quote], alias_assessments=[alias])
    assert rejected.verdict == 'REFUTED'


def test_heading_scope_and_explicit_direct_statement_conflict():
    raw = '我负责青岚项目的接口适配。'
    a, o, registry, proof = setup('# 海棠项目\n本节任务仅属于海棠项目。\n' + raw, raw, '青岚项目')
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2', '青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'CONFLICT'


def register_quote(registry, artifact, quote):
    registry.register_evidence(artifact.workspace_id, quote.span.evidence_ref,
        content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)


@pytest.mark.parametrize('statement,expected', [
    ('本任务与A.md的任务 GATE-42 有工作联系。', 'SUPPORTED'),
    ('本任务与A.md的任务 GATE-42 无工作联系。', 'REFUTED'),
    ('如果本任务与A.md的任务 GATE-42 有工作联系。', 'UNKNOWN'),
    ('引用：“本任务与A.md的任务 GATE-42 有工作联系。”', 'UNKNOWN'),
    ('本任务与A.md的任务 GATE-99 有工作联系。', 'UNKNOWN'),
])
def test_related_work_link_is_bound_to_exact_target(statement, expected):
    a, target, _, aq = setup('我负责青岚项目任务 GATE-42 的网关适配。', source='A.md', project='青岚项目')
    b, subject, registry, bq = setup('我负责青岚项目的回执适配；' + statement, project='青岚项目')
    register_quote(registry, a, aq)
    result = assess_relation('RELATED_TO', subject, target, artifacts=[a,b], source_registry=registry,
        evidence=[bq])
    assert result.verdict == expected
    if expected == 'SUPPORTED':
        assert result.relation_detail == 'WORK_LINK'
        assert result.predicate == 'RELATED_TO'


@pytest.mark.parametrize('statement,expected', [
    ('因为A.md记载的从杉林数据改用远川服务，此任务必须调整签名参数。', 'SUPPORTED'),
    ('本任务无需因A.md记载的从杉林数据改用远川服务做任何调整。', 'REFUTED'),
    ('我负责远川服务的签名参数适配。', 'UNKNOWN'),
    ('如果因为A.md记载的从杉林数据改用远川服务，此任务必须调整签名参数。', 'UNKNOWN'),
    ('因为A.md记载的从杉林数据改用远川服务，其他任务必须调整签名参数。', 'UNKNOWN'),
])
def test_source_claim_requires_direct_concrete_change_bridge(statement, expected):
    a, _, _, aq = setup('青岚项目供应商由杉林数据改为远川服务。\n接口适配。', raw='接口适配。', source='A.md')
    b, subject, registry, bq = setup('我负责青岚项目的接口适配；' + statement, project='青岚项目')
    register_quote(registry, a, aq)
    change = ChangeRef('ws-p2', '青岚项目', '杉林数据', '远川服务', aq)
    result = assess_relation('AFFECTED_BY', subject, change, artifacts=[a,b], source_registry=registry,
        evidence=[bq])
    assert result.verdict == expected
    assert 'SOURCE_CLAIM_ONLY' in result.authority_checks
    assert result.object.kind == 'SOURCE_CLAIM'


@pytest.mark.parametrize('predicate', ['IN_PROJECT', 'RELATED_TO', 'AFFECTED_BY', 'PROJECT_ALIAS'])
def test_invalid_typed_endpoints_are_not_assessable(predicate):
    assert assess_relation(predicate, {}, None, artifacts=[], source_registry=None).verdict == 'NOT_ASSESSABLE'


def test_partial_proposal_cannot_remove_if_prefix():
    a, o, registry, full = setup('如果我负责青岚项目的接口适配。', project='青岚项目')
    partial = EvidenceQuote(full.span, 2, full.char_end, full.quote[2:], 'RELATION_PROPOSAL')
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[partial])
    assert result.verdict == 'UNKNOWN'


def test_stale_alias_dependency_blocks_project_binding():
    a, _, aq, alias = alias_fixture()
    raw = '我负责接口适配。'
    b, o, registry, bq = setup('# 羽舟\n本节网关接入任务仅属于羽舟。\n' + raw, raw)
    register_quote(registry, a, aq)
    registry.register_evidence('ws-p2', aq.span.evidence_ref, content_hash='0'*64, source_version='new-version')
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'), artifacts=[a,b],
        source_registry=registry, evidence=[bq], alias_assessments=[alias])
    assert result.verdict == 'NOT_ASSESSABLE' and result.lifecycle == 'STALE'


def test_relation_id_stable_on_replay_import_time_and_serialization():
    a, o, registry, proof = setup('我负责青岚项目的接口适配。', project='青岚项目')
    arguments = dict(artifacts=[a], source_registry=registry, evidence=[proof])
    first = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'), **arguments)
    imported = replace(o, recorded_at=datetime(2028,1,1,tzinfo=UTC))
    second = assess_relation('IN_PROJECT', imported, ProjectRef('ws-p2','青岚项目'), **arguments)
    assert first.assessment_id == second.assessment_id
    assert RelationAssessment.from_dict(second.to_dict()).assessment_id == first.assessment_id


def test_work_link_conflict_is_not_resolved_by_source_order():
    a, target, _, aq = setup('我负责青岚项目任务 GATE-42 的网关适配。', source='A.md', project='青岚项目')
    b, subject, registry, bq = setup('我负责青岚项目的回执适配；本任务与A.md的任务 GATE-42 有工作联系；本任务与A.md的任务 GATE-42 无工作联系。', project='青岚项目')
    register_quote(registry, a, aq)
    result = assess_relation('RELATED_TO', subject, target, artifacts=[a,b], source_registry=registry, evidence=[bq])
    assert result.verdict == 'CONFLICT'
    assert result.supporting_evidence and result.counter_evidence


def test_alias_has_no_semantic_similarity_fallback_and_quoted_claim_is_unknown():
    _, _, _, result = alias_fixture('引用：“项目说明：本工作区的“羽舟”是“青岚项目”的内部代号。”')
    assert result.verdict == 'UNKNOWN'


def test_source_version_replacement_invalidates_relation_without_mutation():
    a, o, registry, proof = setup('我负责青岚项目的接口适配。', project='青岚项目')
    newer = replace(a, content=a.content + '来源已更新。')
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'),
        artifacts=[newer], source_registry=registry, evidence=[proof])
    assert result.verdict == 'NOT_ASSESSABLE'
    assert o.verify(a)


def test_heading_does_not_hide_conflicting_exclusive_project_statement():
    raw = '我负责接口适配。'
    a, o, registry, proof = setup('# 青岚项目\n本节任务仅属于海棠项目。\n' + raw, raw)
    result = assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'),
        artifacts=[a], source_registry=registry, evidence=[proof])
    assert result.verdict == 'CONFLICT'


def test_same_transition_twice_cannot_be_unique_change_reference():
    change_line = '青岚项目供应商由杉林数据改为远川服务。'
    a, _, _, aq = setup(change_line + '\n' + change_line + '\n接口适配。', raw='接口适配。', source='A.md')
    b, o, registry, bq = setup('我负责青岚项目的接口适配；因为A.md记载的从杉林数据改用远川服务，此任务必须调整签名参数。', project='青岚项目')
    register_quote(registry, a, aq)
    change = ChangeRef('ws-p2','青岚项目','杉林数据','远川服务',aq)
    assert assess_relation('AFFECTED_BY', o, change, artifacts=[a,b], source_registry=registry, evidence=[bq]).verdict == 'UNKNOWN'


def test_review_narrow_task_proposal_cannot_borrow_omitted_title():
    raw = '我负责接口适配。'
    a, o, registry, full = setup('# 青岚项目\n本节任务仅属于青岚项目。\n' + raw, raw)
    start = a.content.index(raw)
    narrow = EvidenceQuote(full.span, start, start + len(raw), raw, 'RELATION_PROPOSAL')
    assert assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'), artifacts=[a],
        source_registry=registry, evidence=[narrow]).verdict == 'UNKNOWN'


def test_review_parent_project_scope_survives_generic_subheading():
    raw = '我负责接口适配。'
    a, o, registry, full = setup('# 青岚项目\n本节任务仅属于青岚项目。\n## 接口子区\n' + raw + '\n# 海棠项目\n其他任务。', raw)
    assert assess_relation('IN_PROJECT', o, ProjectRef('ws-p2','青岚项目'), artifacts=[a],
        source_registry=registry, evidence=[full]).verdict == 'SUPPORTED'


def test_review_same_sentence_duplicate_transition_is_ambiguous():
    a, _, _, aq = setup('青岚项目供应商由杉林数据改为远川服务，青岚项目供应商由杉林数据改为远川服务。\n接口适配。', raw='接口适配。', source='A.md')
    b, o, registry, bq = setup('我负责青岚项目的接口适配；因为A.md记载的从杉林数据改用远川服务，此任务必须调整签名参数。', project='青岚项目')
    register_quote(registry, a, aq)
    change = ChangeRef('ws-p2','青岚项目','杉林数据','远川服务',aq)
    assert assess_relation('AFFECTED_BY', o, change, artifacts=[a,b], source_registry=registry, evidence=[bq]).verdict == 'UNKNOWN'
