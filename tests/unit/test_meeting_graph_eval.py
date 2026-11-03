"""Latest scope and offline metric checks use independent synthetic objects."""
from copy import deepcopy
from datetime import UTC,datetime
from types import SimpleNamespace
import json
import pytest
from run_meeting_graph_dev import route_names,preflight
from meeting_graph_scoring import MeetingDevScorer
from deepseek_eval_transport import assert_off_peak


def scorer():
    fixture=dict(topic='Project',meetings=[dict(id='M1',name='Meeting',date='2026-04-01',lines=['1 decided'])],
        confirmed_decisions=[dict(id='D1',meeting='M1',evidence='M1#1')],confirmed_tasks=[])
    return MeetingDevScorer(dict(meridian=dict(confirmed_edges=[dict(type='reports_to',**{'from':'Ada','to':'Bo'})]),
        **{'graph-dev-meetings':fixture}),dict(meetings=dict(decisions={'D1':'record-one'},tasks={})))


def test_latest_scope_excludes_bounded_graph_and_preserves_three_repetitions():
    assert route_names('A_BOUNDED',1)==route_names('B_BOUNDED',2)==['legacy_only']
    for group in ('D','B_MERIDIAN'):
        assert route_names(group,1)==['legacy_only','graph_only']
        assert route_names(group,2)==['graph_only','legacy_only']
        assert route_names(group,3)==['legacy_only','graph_only']
    class Inputs:
        def public_cases(self):
            yield 'one','A_BOUNDED',dict(question='Who is the direct leader of the owner of Bay?')
            yield 'two','D',dict(question='会议都定了什么')
    calls=[]
    class Budget:
        def preflight(self,i,o):calls.append((i,o));return .01
    result=preflight(Inputs(),Budget(),dict(legacy_max_turns=12,input_token_caps=dict(legacy_only=8192,graph_only=6144),max_output_tokens=1024))
    assert result['executions']==9 and result['legacy_call_cap']==36 and result['graph_call_cap']==3
    assert calls==[(36*8192+3*6144,39*1024)]


def test_score_requires_values_and_evidence_and_counts_extra_relationships():
    s=scorer();expected=dict(status='FOUND',values={'leader':'Bo'})
    public=dict(fixture_id='meridian');actual=dict(status='FOUND',values=['bo'],relations=[dict(subject='Ada',edge_type='reports_to',target='Bo')],evidence=['m'])
    assert s('B01',actual,expected,public,[])['correct']
    extra=deepcopy(actual);extra['relations'].append(dict(subject='Ada',edge_type='reports_to',target='Cora'))
    assert not s('B01',extra,expected,public,[])['correct']
    assert s('B01',extra,expected,public,[])['error_relations']==1
    missing=deepcopy(actual);missing['evidence']=[]
    assert not s('B01',missing,expected,public,[])['correct']
    missing=deepcopy(actual);missing['values']=[]
    # Relationship itself contains the requested value, so value is still present.
    missing['relations']=[]
    assert not s('B01',missing,expected,public,[])['correct']


def test_d_unknown_cannot_pass_a_positive_answer_and_proposal_is_explained():
    s=scorer();public=dict(fixture_id='graph-dev-meetings')
    actual=dict(status='NOT_FOUND',values=[],relations=[],evidence=[],answer='仅为提议，未成为正式任务',structured_outputs=[],result_steps=[])
    assert s('D13',actual,dict(status='UNKNOWN'),public,[])['correct']
    extra=deepcopy(actual);extra['relations']=[dict(subject='Ada',edge_type='owner_of',target='看板')]
    assert not s('D13',extra,dict(status='UNKNOWN'),public,[])['correct']
    assert s('D16',actual,dict(status='UNKNOWN'),public,[])['correct']


def test_pricing_window_closes_before_peak_and_before_frozen_deadline():
    config=dict(pricing_valid_until_utc='2026-11-03T00:30:00+00:00')
    assert_off_peak(config,datetime(2026,11,2,17,tzinfo=UTC))
    with pytest.raises(ValueError,match='PEAK'):assert_off_peak(config,datetime(2026,11,2,2,tzinfo=UTC))
    with pytest.raises(ValueError,match='ENDED'):assert_off_peak(config,datetime(2026,11,3,1,tzinfo=UTC))


def test_bounded_legacy_relation_errors_use_original_public_sources():
    s=scorer();actual=dict(status='FOUND',outcome='complete',values=['ada','bo'],relations=[
        dict(subject='Ada',edge_type='owner_of',target='Bay'),dict(subject='Ada',edge_type='reports_to',target='Bo')],evidence=['owner.md','manager.md'])
    expected=dict(outcome='complete',values=['ada','bo'],required_sources=['owner.md','manager.md'],reason=None)
    docs=[dict(content='Ada is the accountable owner of Bay.'),dict(content='Ada reports directly to Bo.')]
    assert s('DEV-A',actual,expected,dict(fixture_id='DEV-A'),docs)['correct']
    actual['relations'][1]['target']='Cora'
    result=s('DEV-A',actual,expected,dict(fixture_id='DEV-A'),docs)
    assert not result['correct'] and result['error_relations']==1
