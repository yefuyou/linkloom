"""Independent scoring counterexamples, authored without campaign output."""
from copy import deepcopy
from rescore_meeting_graph_dev import CorrectedScorer


def scorer():
    tasks=[dict(id='T1',title='批量对账脚本',history=[dict(meeting='M1',owner='李明',deadline='10/15')]),
        dict(id='T2',title='支付宝接口联调',history=[dict(meeting='M1',owner='王芳',deadline='10/12')]),
        dict(id='T3',title='联调报告',history=[dict(meeting='M1',owner='李明',deadline='10/12')]),
        dict(id='T4',title='实时对账服务',history=[dict(meeting='M1',owner='周宁',deadline='10/20')])]
    fixture=dict(topic='Project',meetings=[dict(id='M1',name='Meeting',date='2026-10-08',lines=['第一期只接入微信支付和支付宝。'])],
        confirmed_decisions=[dict(id='D2',meeting='M1',evidence='M1#1')],confirmed_tasks=tasks)
    return CorrectedScorer(dict(meridian=dict(confirmed_edges=[dict(type='reports_to',**{'from':'Ada','to':'Bo'})]),
        **{'graph-dev-meetings':fixture}),dict(meetings=dict(decisions={'D2':'d2'},tasks={t['id']:t['id'] for t in tasks})))


def test_background_names_cannot_substitute_for_typed_selected_answer():
    s=scorer();actual=dict(status='FOUND',values=['cora'],relations=[],evidence=['m'],answer='Bo exists in background; answer is Cora')
    assert not s('B03',actual,dict(status='FOUND',values={'leader':'Bo'}),dict(fixture_id='meridian'),[])['correct']
    actual.update(values=['bo'],relations=[dict(subject='Ada',edge_type='reports_to',target='Bo')])
    assert s('B03',actual,dict(status='FOUND',values={'leader':'Bo'}),dict(fixture_id='meridian'),[])['correct']


def test_d15_has_no_extra_deadline_requirement():
    s=scorer();records=[dict(kind='task',task_id='T1',title='批量对账脚本',current=dict(fields=dict(owner='李明'))),
        dict(kind='task',task_id='T4',title='实时对账服务',current=dict(fields=dict(owner='周宁')))]
    actual=dict(status='AMBIGUOUS',values=[],relations=[],evidence=['m'],structured_outputs=[],result_steps=[dict(id='root',status='AMBIGUOUS',records=records,edges=[])])
    assert s('D15',actual,dict(status='AMBIGUOUS'),dict(fixture_id='graph-dev-meetings'),[])['correct']
    actual['result_steps'][0]['records'].pop()
    assert not s('D15',actual,dict(status='AMBIGUOUS'),dict(fixture_id='graph-dev-meetings'),[])['correct']


def test_d08_scalar_values_are_bound_to_requested_task():
    s=scorer();record=dict(kind='task',task_id='T3',title='联调报告',current=dict(fields=dict(title='联调报告',owner='李明',deadline={'deadline':'2026-10-12'})))
    actual=dict(status='FOUND',values=['李明','2026-10-12'],relations=[],evidence=['m'],structured_outputs=[dict(step='task',field='owner',values=['李明']),dict(step='task',field='deadline',values=['2026-10-12'])],result_steps=[dict(id='task',status='FOUND',records=[record],edges=[])])
    assert s('D08',actual,dict(status='FOUND'),dict(fixture_id='graph-dev-meetings'),[])['correct']
    wrong=deepcopy(actual);wrong['result_steps'][0]['records'][0]['task_id']='T2';wrong['result_steps'][0]['records'][0]['title']='支付宝接口联调'
    assert not s('D08',wrong,dict(status='FOUND'),dict(fixture_id='graph-dev-meetings'),[])['correct']


def test_d11_decision_identity_does_not_substitute_for_verified_content():
    s=scorer();record=dict(kind='decision_record',record_id='d2',value='第一期只接入微信支付和支付宝。',valid_to=None)
    actual=dict(status='FOUND',values=[],relations=[],evidence=['m'],structured_outputs=[dict(step='decision',field='record',values=[record])],result_steps=[dict(id='decision',status='FOUND',records=[record],edges=[])])
    assert s('D11',actual,dict(status='FOUND'),dict(fixture_id='graph-dev-meetings'),[])['correct']
    wrong=deepcopy(actual);wrong['structured_outputs'][0]['values'][0]['value']='Only UnionPay'
    assert not s('D11',wrong,dict(status='FOUND'),dict(fixture_id='graph-dev-meetings'),[])['correct']


def test_legacy_replacement_effective_date_uses_grounded_claim_not_background():
    from graph_eval_date_score import DateParityScorer
    from linkloom.agents.claim_grounding import _claim_facts
    s=scorer();s.__class__=DateParityScorer
    s.truth.add(('new plan','supersedes','old plan'))
    actual=dict(status='FOUND',values=['new plan'],relations=[dict(subject='New Plan',edge_type='supersedes',target='Old Plan')],
        evidence=['m1.md'],verified_by='legacy-runtime-reviewer',answer='On 2025-02-14 New Plan replaced Old Plan.')
    expected=dict(status='FOUND',values=dict(plan='new plan',effective_date='2025-02-14'))
    docs=[dict(path='m1.md',content='On 2025-02-14 New Plan replaced Old Plan.')]
    assert _claim_facts(actual['answer'])
    assert s('B01',actual,expected,dict(fixture_id='meridian'),docs)['correct']
    wrong=deepcopy(actual);wrong['answer']='On 2025-02-15 New Plan replaced Old Plan.'
    assert not s('B01',wrong,expected,dict(fixture_id='meridian'),docs)['correct']
    absent=deepcopy(actual);absent['evidence']=[]
    assert not s('B01',absent,expected,dict(fixture_id='meridian'),docs)['correct']
