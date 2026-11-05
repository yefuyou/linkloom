"""Synthetic algebra only; no D3 fixture or system-output reads."""
from copy import deepcopy
import pytest
from graph_acceptance_extensions import AcceptanceEvaluator,compile_expected
from graph_acceptance_v2 import compile_expected as original_compile
from tests.unit.test_holdout_acceptance_adapter import comparison_sample,sample

BINDINGS=[dict(before_aliases=['旧流程','原流程'],after_aliases=['新流程'],before_id='X',after_id='Y')]


def text_sample(history=False):
    original,actual,_=comparison_sample();fixture=deepcopy(original.fixture);index=deepcopy(original.index)
    diff=actual['structured_outputs'][0]['values'][0];item=diff['changed'][0]
    for r in [item['before'],item['after']]:r['subject_key']='subject-confirmed'
    index['decision_records']={key:{k:deepcopy(r.get(k)) for k in ['record_id','subject_key','valid_from','valid_to','supersedes_id','status']} for key,r in [('X',item['before']),('Y',item['after'])]}
    index['text_transition_bindings']=deepcopy(BINDINGS)
    index['meeting_dates']={'ma':'2024-04-01','mb':'2024-04-02'}
    if history:
        a,b=item['before'],item['after'];chain=item['supersedes_chain']
        actual.update(structured_outputs=[dict(step='h',field='record',values=[a,b])],result_steps=[dict(id='h',status='FOUND',records=[a,b],edges=chain,effective_as_of='2024-04-03',evidence=a['evidence']+b['evidence'])])
        expected=dict(status='FOUND',decision_changes="主题由'旧流程'改为'新流程'")
    else:expected=dict(status='FOUND',decision_changes="主题由'旧流程'改为'新流程'",task_changes=['Artifact：截止 4/8→4/9'],added=['Extra · Bar · 4/10'])
    rules=compile_expected(expected,bindings=BINDINGS)
    return AcceptanceEvaluator(fixture,index),actual,rules


@pytest.mark.parametrize('history',[False,True])
def test_text_transitions_bind_native_records_chain_and_evidence(history):
    scorer,actual,rules=text_sample(history)
    assert scorer(actual,rules)['correct']
    actual['answer']='no expected phrases at all'
    assert scorer(actual,rules)['correct']  # Explanation is not a verbatim answer.


@pytest.mark.parametrize('attack',['subject','identity','value','chain','receipt','evidence','changed_date','missing_selected'])
@pytest.mark.parametrize('history',[False,True])
def test_text_transitions_cannot_borrow_or_forge_required_proofs(history,attack):
    scorer,actual,rules=text_sample(history)
    if history:
        selected=actual['structured_outputs'][0]['values'];chain=actual['result_steps'][0]['edges']
    else:
        item=actual['structured_outputs'][0]['values'][0]['changed'][0];selected=[item['before'],item['after']];chain=item['supersedes_chain']
    if attack=='subject':
        for r in selected:r['subject_key']='same-but-forged'
    if attack=='identity':selected[0]['record_id']='wrong-record'
    if attack=='value':selected[1]['value']='new words without source proof'
    if attack=='chain':chain[0]['target_ref']['key']='wrong-record'
    if attack=='receipt':scorer.index['decision_receipts']['Y']='wrong-receipt'
    if attack=='evidence':selected[0]['evidence'][0]['source_version_id']='wrong-version'
    if attack=='changed_date':next(r for r in rules if r['op']=='decision_text_comparison')['expected']['changed_on']='4/3'
    if attack=='missing_selected':
        if history:actual['structured_outputs'][0]['values'].pop()
        else:actual['structured_outputs'][0]['values'][0]['changed']=[]
    assert not scorer(actual,rules)['correct']


def test_history_requires_its_own_chain_and_cannot_show_future_change():
    scorer,actual,rules=text_sample(True)
    bad=deepcopy(actual);bad['result_steps'][0]['edges']=[];assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['result_steps'][0]['effective_as_of']='2024-04-01';assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['result_steps'][0]['edges'][0].pop('schema_version')
    for side in ['source','target']:
        reference=bad['result_steps'][0]['edges'][0].pop(side+'_ref');reference['kind']=reference.pop('node_type');bad['result_steps'][0]['edges'][0][side]=reference
    assert scorer(bad,rules)['correct']
    bad['result_steps'][0]['edges'][0]['edge_id']='edge_forged';assert not scorer(bad,rules)['correct']


def test_both_comparison_domains_keep_original_completeness_and_pair_constraints():
    scorer,actual,rules=text_sample()
    for attack in ['omit_task','splice','extra','wrong_task']:
        bad=deepcopy(actual)
        if attack=='omit_task':bad['structured_outputs'].pop()
        if attack=='splice':bad['structured_outputs'][1]['values'][0]['earlier_meeting']='unknown'
        if attack=='extra':bad['structured_outputs'][0]['values'].append(deepcopy(bad['structured_outputs'][0]['values'][0]))
        if attack=='wrong_task':bad['structured_outputs'][1]['values'][0]['added']=[]
        assert not scorer(bad,rules)['correct']


def test_expected_postponement_compiles_to_exact_original_history_rule():
    a=dict(status='FOUND',deadline='4/9',changes='原定 4/8，4/2 评审顺延到 4/9，负责人Foo不变')
    b=deepcopy(a);b['changes']=b['changes'].replace('顺延到','改为')
    assert compile_expected(a)==original_compile(b)


def test_author_reason_override_keeps_unknown_nonpositive_and_consistency():
    expected=dict(status='UNKNOWN',reason='explanatory author prose')
    rules=compile_expected(expected,overrides=[dict(replace_op='reason_text',replacement=dict(op='reason_text',expected=['未找到']))])
    assert [r['op'] for r in rules]==['status','no_positive_facts','reason_text','record_and_relation_consistency']
    old,actual=sample();scorer=AcceptanceEvaluator(old.fixture,old.index)
    actual.update(status='NOT_FOUND',answer='未找到已确认事项',structured_outputs=[],result_steps=[],relations=[])
    assert scorer(actual,rules)['correct']
    actual['structured_outputs']=[dict(step='x',field='owner',values=['invented'])];assert not scorer(actual,rules)['correct']


def test_compiler_requires_authoritative_bindings_and_preserves_other_rules():
    for expected in [dict(status='FOUND',tasks=['Artifact · Foo · 4/8']),dict(status='FOUND',owner='Foo',deadline='4/8',must_mention='fact')]:
        assert compile_expected(expected)==original_compile(expected)
    with pytest.raises(ValueError,match='BINDING_REQUIRED'):compile_expected(dict(status='FOUND',decision_changes="主题由'旧流程'改为'新流程'"))
    with pytest.raises(ValueError):compile_expected(dict(status='FOUND',unknown=True))


def test_transition_annotation_and_both_exposed_evidence_are_not_optional():
    scorer,actual,rules=text_sample(True)
    bad_rules=deepcopy(rules);next(r for r in bad_rules if r['op']=='decision_text_comparison')['expected']['before']='not authorized'
    assert not scorer(actual,bad_rules)['correct']
    actual['result_steps'][0]['evidence']=actual['result_steps'][0]['evidence'][1:]
    assert not scorer(actual,rules)['correct']


def test_historical_active_view_is_not_confused_with_native_superseded_status():
    scorer,actual,rules=text_sample()
    scorer.index['decision_records']['X']['status']='SUPERSEDED'
    assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values'][0]['changed'][0]['before']['status']='SUPERSEDED'
    assert not scorer(bad,rules)['correct']
    scorer,actual,rules=text_sample(True)
    scorer.index['decision_records']['X']['status']='SUPERSEDED'
    for r in actual['structured_outputs'][0]['values']:
        if r['record_id']=='dx':r['status']='SUPERSEDED'
    assert scorer(actual,rules)['correct']


def test_author_override_cannot_replace_status_or_consistency():
    with pytest.raises(ValueError,match='UNAUTHORIZED_OVERRIDE_PAIR'):
        compile_expected(dict(status='UNKNOWN'),overrides=[dict(replace_op='status',replacement=dict(op='reason_text',expected=['text']))])


def test_history_supports_complete_multihop_chain_but_not_missing_intermediate():
    import hashlib
    from linkloom.temporal_graph.models import Node,Edge,Evidence
    from linkloom.temporal_graph.meeting_query import _edge_proof
    scorer,actual,_=text_sample(True);fixture=deepcopy(scorer.fixture);index=deepcopy(scorer.index)
    x,y=actual['structured_outputs'][0]['values'];x['status']='SUPERSEDED';y.update(status='SUPERSEDED',valid_to='2024-04-03')
    text='1 Final confirmed strategy.';hashed=hashlib.sha256(text.encode()).hexdigest()
    proof=dict(workspace_id='w',evidence_id='C1',source_id='C',source_version_id='vC',content_sha256=hashed,quote=text,quote_sha256=hashed,line_start=1,line_end=1,role='source')
    z=dict(kind='decision_record',record_id='dz',subject_key='subject-confirmed',value=text,valid_from='2024-04-03',valid_to=None,status='ACTIVE',supersedes_id='dy',evidence=[proof])
    fixture['meetings'].append(dict(id='C',name='C',date='2024-04-03',lines=[text]));fixture['confirmed_decisions'][1]['superseded_by']='Z';fixture['confirmed_decisions'].append(dict(id='Z',meeting='C',evidence='C#1',valid_from='2024-04-03',supersedes='Y'))
    index['decisions']['Z']='dz';index['decision_receipts']['Z']='rz';index['sources']['C']=dict(source_id='C',source_version_id='vC')
    for key,r in [('X',x),('Y',y),('Z',z)]:index['decision_records'][key]={k:deepcopy(r.get(k)) for k in ['record_id','subject_key','valid_from','valid_to','supersedes_id','status']}
    bindings=deepcopy(BINDINGS);bindings[0]['after_id']='Z';index['text_transition_bindings']=bindings
    edge=Edge(Node('w','decision_record','dz','final'),Node('w','decision_record','dy','new'),'supersedes','2024-04-03',None,'interval','dz','rz',tuple(Evidence(**p) for p in [y['evidence'][0],proof]))
    actual['structured_outputs'][0]['values'].append(z);row=actual['result_steps'][0];row['records'].append(z);row['edges'].insert(0,_edge_proof(edge));row['evidence'].append(proof);row['effective_as_of']='2024-04-04'
    scorer=AcceptanceEvaluator(fixture,index);rules=compile_expected(dict(status='FOUND',decision_changes="主题由'旧流程'改为'新流程'"),bindings=bindings)
    assert scorer(actual,rules)['correct']
    actual['structured_outputs'][0]['values'].pop(1)
    assert not scorer(actual,rules)['correct']
