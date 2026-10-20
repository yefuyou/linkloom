"""Date role/precision/boundary gates over real M1 observations."""
from dataclasses import replace
from datetime import date
import pytest

from tests.unit.test_task_plan_revision import pair, inputs
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision, RevisionComparison
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.document_text import parse_source_text
from tests.unit.test_task_identity_resolution import observe


def compare(a,b,**changes):
    args=inputs(a,b)
    revision=assess_plan_revision(b[1],a[1],**args)
    return compare_revision(b[1],a[1],revision=revision,**{**args,**changes})


def test_explicit_revision_compares_calendar_boundaries_not_actual_delay():
    a,b=pair()
    result=compare(a,b)
    assert result.status=='COMPUTABLE'
    assert result.delta_calendar_days==4
    assert result.old_deadline.bound==result.new_deadline.bound=='BEFORE'
    assert result.plan_currentness=='NOT_ESTABLISHED'
    assert result.asserted_effective_at is None
    assert RevisionComparison.from_dict(result.to_dict()).to_dict()==result.to_dict()


@pytest.mark.parametrize('suffix,status', [
    ('计划在2027年3月8日前完成。','BLOCKED'),
    ('仅更换称呼，不修订任务计划。','NOT_APPLICABLE'),
])
def test_blocked_and_inapplicable_never_return_zero(suffix,status):
    result=compare(*pair(suffix))
    assert result.status==status and result.delta_calendar_days is None


def test_forged_revision_cannot_authorize_date_math():
    a,b=pair()
    args=inputs(a,b)
    revision=replace(assess_plan_revision(b[1],a[1],**args),reason_codes=('FAKE_REVISION',))
    result=compare_revision(b[1],a[1],revision=revision,**args)
    assert result.status=='BLOCKED' and result.delta_calendar_days is None


def with_deadline(item,token):
    artifact,obs=item
    return artifact,build_task_observation(TaskProposal(obs.raw_text,(obs.raw_text,),deadline_text=token,project='青岚项目'),
                                           parse_source_text(artifact).segments[0],artifact)


@pytest.mark.parametrize('suffix,bound',[
    ('前完成','BEFORE'),('，按该日或之前完成','ON_OR_BEFORE'),('（当日完成）','ON'),('', 'UNKNOWN'),
])
def test_boundary_is_derived_from_explicit_role_context(suffix,bound):
    from linkloom.semantic_ingestion.task_revision_comparison import deadline_projection
    a=with_deadline(observe('我负责青岚项目任务 FLOW-31 的接口适配；截止日期为2027年3月8日'+suffix+'。'),'2027年3月8日')
    assert deadline_projection(a[1]).bound==bound


def test_event_and_import_times_cannot_fill_missing_deadline_year():
    a,b=pair()
    a=with_deadline(observe('我负责青岚项目任务 GATE-52 的接口适配，计划在3月4日前完成。'),'3月4日')
    b=with_deadline(observe('我负责青岚项目任务 GATE-52；本条就是A.md的同一任务，现修订其计划，将原定3月4日前完成改为2027年3月8日前完成，本计划替代原计划。',source_id='B.md'),'2027年3月8日')
    result=compare(a,b)
    assert result.status=='NOT_COMPARABLE' and result.old_deadline.value is None
    assert result.old_deadline.precision=='MONTH_DAY' and result.delta_calendar_days is None


def test_deadline_role_cannot_be_forged_to_bypass_m1_verify():
    a,b=pair()
    with pytest.raises(ValueError,match='complete source-grounded day'):
        replace(b[1],deadline_role='UNKNOWN')


def test_future_effective_revision_is_not_published_as_current_plan():
    a,b=pair('现修订其计划，将原定2027年3月4日前完成改为2027年3月8日前完成，本计划自2027年3月2日起生效并替代原计划。')
    result=compare(a,b,as_of=date(2027,3,1))
    assert result.status=='COMPUTABLE' and result.delta_calendar_days==4
    assert result.plan_currentness=='FUTURE_EFFECTIVE' and result.asserted_effective_at=='2027-03-02'


def test_zero_is_forbidden_when_only_scope_revision_and_dates_disagree():
    a,_=pair()
    b=with_deadline(observe('我负责青岚项目任务 GATE-52；本条就是A.md的同一任务；本条替代A.md计划，仅在原任务范围内增加签名校验，计划在2027年3月8日前完成。',source_id='B.md'),'2027年3月8日')
    result=compare(a,b)
    assert result.status=='NOT_COMPARABLE' and result.delta_calendar_days is None


def test_repeat_recording_and_serialization_do_not_change_ids():
    a,b=pair()
    first=compare(a,b)
    later=replace(b[1],recorded_at=b[1].recorded_at.replace(year=2029))
    second=compare(a,(b[0],later))
    assert first.comparison_id==second.comparison_id
    assert first.revision_id==second.revision_id


def test_invalid_comparison_cannot_encode_zero_for_unknown_state():
    result=compare(*pair('计划在2027年3月8日前完成。'))
    with pytest.raises(ValueError,match='null delta'):
        replace(result,delta_calendar_days=0)


def test_inclusive_and_exclusive_boundary_markers_cannot_be_silently_collapsed():
    a,b=pair()
    a=with_deadline(observe('我负责青岚项目任务 GATE-52 的接口适配，最晚在2027年3月4日前完成。'),'2027年3月4日')
    result=compare(a,b)
    assert result.status=='NOT_COMPARABLE' and result.delta_calendar_days is None
    assert result.old_deadline.bound=='UNKNOWN'


def test_trailing_comma_boundary_countercue_is_preserved():
    a,b=pair()
    a=with_deadline(observe('我负责青岚项目任务 GATE-52 的接口适配，截止2027年3月4日前完成，含当日。'),'2027年3月4日')
    result=compare(a,b)
    assert result.status=='NOT_COMPARABLE' and result.delta_calendar_days is None
    assert result.old_deadline.bound=='UNKNOWN'
