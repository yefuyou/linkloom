"""P3 contracts using independent names and caller-owned source snapshots."""
from dataclasses import replace
from datetime import UTC, datetime
import pytest

from tests.unit.test_task_identity_resolution import observe, registry_for
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote, RelationAssessment
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision


def pair(new_suffix=None):
    a = observe('我负责青岚项目任务 GATE-52 的接口适配，计划在2027年3月4日前完成。')
    b = observe('我负责青岚项目任务 GATE-52；本条就是A.md的同一任务，' + (new_suffix or
        '现修订其计划，将原定2027年3月4日前完成改为2027年3月8日前完成，本计划替代原计划。'), source_id='B.md')
    def with_deadline(item,token):
        artifact,observation=item
        proposal=TaskProposal(observation.raw_text,(observation.raw_text,),deadline_text=token,project='青岚项目')
        return artifact,build_task_observation(proposal,parse_source_text(artifact).segments[0],artifact)
    return with_deadline(a,'2027年3月4日'),with_deadline(b,'2027年3月8日' if '2027年3月8日' in b[1].raw_text else None)


def inputs(a,b,extra=()):
    artifacts = [x[0] for x in (a,b,*extra)]
    registry = registry_for(*artifacts)
    context = tuple(x[1] for x in extra)
    identity = assess_same_task(b[1],a[1],artifacts=artifacts,source_registry=registry,context_observations=context)
    quotes = [EvidenceQuote(o.provenance,o.provenance.char_start,o.provenance.char_end,o.provenance.quote,'RELATION_PROPOSAL')
              for _,o in (a,b,*extra)]
    return dict(artifacts=artifacts,source_registry=registry,same_task=identity,evidence=quotes,context_observations=context)


def test_explicit_revision_requires_reverified_identity_and_keeps_direction():
    a,b=pair()
    args=inputs(a,b)
    result=assess_plan_revision(b[1],a[1],**args)
    assert result.verdict=='SUPPORTED'
    assert result.predicate=='REVISES_PLAN'
    assert result.changed_fields==('deadline',)
    assert result.subject.observation_id==b[1].observation_id
    assert result.object.observation_id==a[1].observation_id
    assert result.premise_ids==(args['same_task'].assessment_id,)
    assert RelationAssessment.from_dict(result.to_dict()).to_dict()==result.to_dict()


def test_same_task_with_different_dates_does_not_imply_revision():
    a,b=pair('计划在2027年3月8日前完成。')
    assert assess_plan_revision(b[1],a[1],**inputs(a,b)).verdict=='UNKNOWN'


def test_explicit_non_revision_is_refuted():
    a,b=pair('仅更换称呼，不修订任务计划。')
    result=assess_plan_revision(b[1],a[1],**inputs(a,b))
    assert result.verdict=='REFUTED' and result.counter_evidence


def test_forged_supported_identity_is_not_a_revision_permission():
    a,b=pair('计划在2027年3月8日前完成。')
    args=inputs(a,b)
    forged=replace(args['same_task'],reason_codes=('FAKE_IDENTITY',))
    result=assess_plan_revision(b[1],a[1],**{**args,'same_task':forged})
    assert result.verdict=='NOT_ASSESSABLE'


def test_stale_identity_dependencies_are_not_trusted():
    a,b=pair()
    args=inputs(a,b)
    args['source_registry'].register_evidence(a[0].workspace_id,a[1].provenance.evidence_ref,
        content_hash='0'*64,source_version='superseding-version')
    result=assess_plan_revision(b[1],a[1],**args)
    assert result.verdict=='NOT_ASSESSABLE' and result.lifecycle=='STALE'


def test_narrow_revision_proposal_cannot_grow_into_whole_bridge():
    a,b=pair()
    args=inputs(a,b)
    span=b[1].provenance
    text='将原定2027年3月4日前完成改为2027年3月8日前完成'
    start=span.char_start+span.quote.index(text)
    args['evidence']=[EvidenceQuote(span,start,start+len(text),text,'RELATION_PROPOSAL')]
    result=assess_plan_revision(b[1],a[1],**args)
    assert result.verdict=='UNKNOWN' and not result.supporting_evidence


def test_positive_and_negative_revision_same_sentence_is_conflict():
    a,b=pair('现修订其计划，将原定2027年3月4日前完成改为2027年3月8日前完成，本计划替代原计划，同时声明不修订任务计划。')
    result=assess_plan_revision(b[1],a[1],**inputs(a,b))
    assert result.verdict=='CONFLICT' and result.supporting_evidence and result.counter_evidence


def test_invalid_workspace_and_wrong_pair_identity_are_rejected():
    a,b=pair()
    args=inputs(a,b)
    foreign=observe('我负责海棠项目任务 X-91 的接口适配。',workspace='foreign',project='海棠项目',source_id='foreign.md')
    assert assess_plan_revision(foreign[1],a[1],**args).verdict=='NOT_ASSESSABLE'
    other=pair('我负责另外的接口。')
    assert assess_plan_revision(other[1][1],a[1],**args).verdict=='NOT_ASSESSABLE'


def test_forged_source_version_and_invalid_input_are_not_assessable():
    a,b=pair()
    args=inputs(a,b)
    args['artifacts']=[a[0],replace(b[0],content=b[0].content+'另一版本。')]
    assert assess_plan_revision(b[1],a[1],**args).verdict=='NOT_ASSESSABLE'
    assert assess_plan_revision(None,None,**inputs(a,b)).verdict=='NOT_ASSESSABLE'


def test_non_revision_timestamp_or_source_edit_never_creates_plan_revision():
    a,b=pair('文档更新，计划在2027年3月8日前完成。')
    assert assess_plan_revision(b[1],a[1],**inputs(a,b)).verdict=='UNKNOWN'


def test_canonical_forged_identity_with_real_but_irrelevant_quote_is_rejected():
    a,b=pair()
    artifact,unknown=observe('我负责青岚项目的接口适配，计划在2027年3月8日前完成。',source_id='B.md')
    args=inputs(a,(artifact,unknown))
    assert args['same_task'].verdict=='UNKNOWN'
    span=unknown.provenance
    irrelevant=EvidenceQuote(span,span.char_start,span.char_end,span.quote,'IDENTITY_ASSERTION')
    forged=replace(args['same_task'],verdict='SUPPORTED',supporting_evidence=(irrelevant,),reason_codes=('EXPLICIT_TASK_REFERENCE',))
    assert RelationAssessment.from_dict(forged.to_dict()).assessment_id==forged.assessment_id
    assert assess_plan_revision(unknown,a[1],**{**args,'same_task':forged}).verdict=='NOT_ASSESSABLE'


@pytest.mark.parametrize('statement',[
    '其他任务现修订其计划，将原定2027年3月4日前完成改为2027年3月8日前完成，本计划替代原计划。',
    '其他任务不修订任务计划。',
])
def test_other_tasks_plan_statement_does_not_apply_to_this_observation(statement):
    a,b=pair(statement)
    result=assess_plan_revision(b[1],a[1],**inputs(a,b))
    assert result.verdict=='UNKNOWN'


def test_negated_replacement_is_not_positive_revision_proof():
    a,b=pair('资料更新，不应替代原计划，将原定2027年3月4日前完成改为2027年3月8日前完成。')
    assert assess_plan_revision(b[1],a[1],**inputs(a,b)).verdict!='SUPPORTED'


def test_revision_bridge_cannot_escape_observation_raw_range():
    a,_=pair()
    short='我负责青岚项目任务 GATE-52 的接口适配，计划在2027年3月8日前完成'
    content=short+'，现修订其计划，本条替代A.md计划，将原定2027年3月4日前完成改为2027年3月8日前完成。'
    artifact,_=observe(content,source_id='B.md')
    b=(artifact,build_task_observation(TaskProposal(short,(short,),deadline_text='2027年3月8日',project='青岚项目'),
                                     parse_source_text(artifact).segments[0],artifact))
    result=assess_plan_revision(b[1],a[1],**inputs(a,b))
    assert result.verdict=='UNKNOWN' and not result.supporting_evidence
