"""Synthetic data only: no existing system answers, provider, or corpus reads."""
from copy import deepcopy
import hashlib
import pytest
from holdout_graph_acceptance import compile_expected,AcceptanceEvaluator,month_day


def sample():
    quote='1 Artifact: Foo, due 4/8.';sha=hashlib.sha256(quote.encode()).hexdigest()
    fixture=dict(topic='Synthetic',meetings=[dict(id='M',name='Synthetic meeting',date='2024-04-01',lines=[quote])],confirmed_decisions=[],confirmed_tasks=[dict(id='K',title='Artifact',history=[dict(meeting='M',evidence='M#1',owner='Foo',deadline='4/8')])])
    index=dict(workspace_id='w',decisions={},tasks={'K':'task-k'},sources={'M':dict(source_id='s',source_version_id='v')})
    proof=dict(workspace_id='w',source_id='s',source_version_id='v',line_start=1,line_end=1,quote=quote,quote_sha256=sha,content_sha256=sha)
    state=dict(valid_from='2024-04-01',fields=dict(title='Artifact',owner='Foo',deadline={'deadline':'2024-04-08'}),evidence=[proof])
    record=dict(kind='task',task_id='task-k',title='Artifact',current=state,states=[state])
    actual=dict(status='FOUND',relations=[],evidence=['Synthetic meeting'],answer='Foo 4/8',structured_outputs=[dict(step='s',field='record',values=[record])],result_steps=[dict(id='s',status='FOUND',records=[record],evidence=[proof])])
    return AcceptanceEvaluator(fixture,index),actual


def test_expected_only_compilation_and_month_day_without_asserted_year():
    rules=compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8'))
    assert next(r['expected'] for r in rules if r['op']=='single_task_values')=={'owner':'Foo','deadline':'4/8'}
    assert month_day('2024-04-08')=='4/8'
    with pytest.raises(ValueError):compile_expected(dict(status='FOUND',unexpected_field=True))


def test_single_value_requires_status_value_and_matching_source_proof():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8'))
    assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);bad['status']='UNKNOWN'
    assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['result_steps'][0]['evidence'][0]['source_version_id']='wrong'
    assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values'][0]['current']['fields']['owner']='Other'
    assert not scorer(bad,rules)['correct']


def test_lists_reject_omission_extra_and_reference_masquerading_as_record():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',tasks=['Artifact · Foo · 4/8']))
    assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values']=[]
    assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values'].append(dict(kind='task',key='node-ref',label='Artifact'))
    assert not scorer(bad,rules)['correct']


def test_must_mention_is_not_satisfied_by_unselected_background():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8',must_mention='critical fact'))
    actual['unselected_background']='critical fact'
    assert not scorer(actual,rules)['correct']
    actual['answer']+=' critical fact'
    assert scorer(actual,rules)['correct']


def test_unknown_requires_nonpositive_response_and_reason():
    scorer,actual=sample();rules=compile_expected(dict(status='UNKNOWN',reason='提议，不能编造'))
    actual.update(status='NOT_FOUND',answer='只有提议，未确认',structured_outputs=[],relations=[])
    actual['result_steps']=[]
    assert scorer(actual,rules)['correct']
    actual['answer']='不知道'
    assert not scorer(actual,rules)['correct']
    actual['status']='AMBIGUOUS';actual['answer']='提议'
    assert not scorer(actual,rules)['correct']



def test_bare_scalars_cannot_borrow_unrelated_task_evidence():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',owner='Injected',deadline='4/9'))
    actual['structured_outputs']=[dict(step='s',field='owner',values=['Injected']),dict(step='s',field='deadline',values=['4/9'])]
    actual['result_steps'][0]['records']=[]
    assert not scorer(actual,rules)['correct']


def test_unbound_fabricated_history_never_satisfies_a_change():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',deadline='4/8',changes='原定 4/7，4/2 会议改为 4/8，负责人Foo不变'))
    fake=[dict(valid_from='2024-04-01',fields=dict(owner='Foo',deadline={'deadline':'2024-04-07'})),dict(valid_from='2024-04-02',fields=dict(owner='Foo',deadline={'deadline':'2024-04-08'}))]
    actual['structured_outputs'].append(dict(step='s',field='history',values=[fake]))
    assert not scorer(actual,rules)['correct']


def comparison_sample():
    # Entirely manually constructed algebraic fixture; never a live product output.
    from linkloom.temporal_graph.models import Node,Edge,Evidence
    lines={'A':['1 Launch on 4/8.','2 Artifact: Foo due 4/8.'], 'B':['1 Launch on 4/9.','2 Artifact: Foo due 4/9.','3 Extra: Bar due 4/10.']}
    fixture=dict(topic='Synthetic',meetings=[dict(id=k,name=k,date='2024-04-01' if k=='A' else '2024-04-02',lines=v) for k,v in lines.items()],confirmed_decisions=[dict(id='X',meeting='A',evidence='A#1',valid_from='2024-04-01',superseded_by='Y'),dict(id='Y',meeting='B',evidence='B#1',valid_from='2024-04-02',supersedes='X')],confirmed_tasks=[dict(id='Q',title='Artifact',history=[dict(meeting='A',evidence='A#2',owner='Foo',deadline='4/8'),dict(meeting='B',evidence='B#2',owner='Foo',deadline='4/9')]),dict(id='Z',title='Extra',history=[dict(meeting='B',evidence='B#3',owner='Bar',deadline='4/10')])])
    index=dict(workspace_id='w',decisions={'X':'dx','Y':'dy'},tasks={'Q':'tq','Z':'tz'},sources={k:dict(source_id=k,source_version_id='v'+k) for k in lines},decision_receipts={'X':'rx','Y':'ry'},task_events={'eq':'tq'})
    def proof(k,line):
        text=lines[k][line-1]
        return dict(workspace_id='w',evidence_id=k+str(line),source_id=k,source_version_id='v'+k,content_sha256=hashlib.sha256('\n'.join(lines[k]).encode()).hexdigest(),quote=text,quote_sha256=hashlib.sha256(text.encode()).hexdigest(),line_start=line,line_end=line,role='source')
    proofs=[proof(k,i) for k in lines for i in range(1,len(lines[k])+1)]
    old=dict(kind='decision_record',record_id='dx',value=lines['A'][0],valid_from='2024-04-01',valid_to='2024-04-02',status='ACTIVE',evidence=[proof('A',1)])
    new=dict(kind='decision_record',record_id='dy',value=lines['B'][0],supersedes_id='dx',valid_from='2024-04-02',valid_to=None,status='ACTIVE',evidence=[proof('B',1)])
    edge=Edge(Node('w','decision_record','dy','new'),Node('w','decision_record','dx','old'),'supersedes','2024-04-02',None,'interval','dy','ry',tuple(Evidence(**p) for p in [proof('A',1),proof('B',1)]))
    chain=edge.to_dict();chain['schema_version']='graph-edge-proof/v1'
    for side in ('source','target'):
        ref=chain.pop(side);ref['node_type']=ref.pop('kind');chain[side+'_ref']=ref
    def state(k,title,owner,deadline,line):return dict(valid_from='2024-04-01' if k=='A' else '2024-04-02',fields=dict(title=title,owner=owner,deadline={'deadline':deadline}),evidence=[proof(k,line)])
    a=state('A','Artifact','Foo','2024-04-08',2);b=state('B','Artifact','Foo','2024-04-09',2);z=state('B','Extra','Bar','2024-04-10',3)
    before=dict(kind='task',task_id='tq',title='Artifact',current=a,states=[a])
    after=dict(kind='task',task_id='tq',title='Artifact',current=b,states=[a,b])
    added=dict(kind='task',task_id='tz',title='Extra',current=z,states=[z])
    base=dict(earlier_meeting='ma',later_meeting='mb',order_swapped=False,added=[],changed=[],cancelled=[],unchanged=[],unaligned=[],pending={})
    decision=deepcopy(base);decision['changed']=[dict(before=old,after=new,supersedes_chain=[chain])]
    task=deepcopy(base);task.update(added=[added],changed=[dict(before=before,after=after,events=['eq'])])
    actual=dict(status='FOUND',relations=[],evidence=['A','B'],answer='synthetic',structured_outputs=[dict(step='d',field='comparison',values=[decision]),dict(step='t',field='comparison',values=[task])],result_steps=[dict(id='d',status='FOUND',records=[],evidence=proofs),dict(id='t',status='FOUND',records=[],evidence=proofs)])
    expected=dict(status='FOUND',decision_changes='时间从 4/8 推迟到 4/9',task_changes=['Artifact：截止 4/8→4/9'],added=['Extra · Bar · 4/10'])
    return AcceptanceEvaluator(fixture,index),actual,compile_expected(expected)


def test_comparisons_are_complete_and_cannot_splice_different_diffs():
    scorer,actual,rules=comparison_sample();assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);task=bad['structured_outputs'][1]['values'][0]
    other=deepcopy(task);other['changed']=[];other['earlier_meeting']='other';task['added']=[]
    bad['structured_outputs'][1]['values'].append(other)
    assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values'][0]['changed'][0]['supersedes_chain'][0]['target_ref']['key']='forged'
    assert not scorer(bad,rules)['correct']


def test_source_meeting_is_bound_to_the_answered_record_not_any_label():
    scorer,actual,_=comparison_sample();new=deepcopy(actual['structured_outputs'][0]['values'][0]['changed'][0]['after'])
    actual['structured_outputs']=[dict(step='s',field='record',values=[new,{'label':'A'}])]
    rules=compile_expected(dict(status='FOUND',answer='4/9',source='A'))
    assert not scorer(actual,rules)['correct']
    rules=compile_expected(dict(status='FOUND',answer='4/9',source='B'))
    assert scorer(actual,rules)['correct']


def test_correct_scalar_selectors_bind_only_to_unique_task_in_same_step():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8'))
    actual['structured_outputs']=[dict(step='s',field='owner',values=['Foo']),dict(step='s',field='deadline',values=['2024-04-08'])]
    assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['values']=['Injected']
    assert not scorer(bad,rules)['correct']
    bad=deepcopy(actual);bad['structured_outputs'][0]['step']='unrelated'
    assert not scorer(bad,rules)['correct']


def test_driver_requires_frozen_runtime_and_rejects_sqlite_side_files(tmp_path,monkeypatch):
    import json
    from run_graph_holdout_acceptance import LockedInputs,sha
    monkeypatch.chdir(tmp_path)
    final=tmp_path/'work'/'graph-opt';final.mkdir(parents=True)
    config=final/'config-r2-final.json';config.write_text(json.dumps(dict(graph_alignment_review=False,graph_max_provider_calls=1)),encoding='utf8')
    (final/'runtime-r2-final.json').write_text(json.dumps(dict(dependencies={})),encoding='utf8')
    root=tmp_path/'holdout';root.mkdir()
    seal=root/'rules-freeze.json';seal.write_text(json.dumps(dict(hashes={},final_configuration_sha256=sha(config),price_quote=dict(valid_until_utc='2099-01-01T00:00:00+00:00'))),encoding='utf8')
    LockedInputs(root).verify()
    locked=LockedInputs(root,require_runtime=True)
    with pytest.raises(ValueError,match='RUNTIME_FREEZE_REQUIRED'):locked.verify()
    runtime=root/'runtime-freeze.json';runtime.write_text(json.dumps(dict(hashes={})),encoding='utf8')
    locked.verify()
    journal=root/'confirmed.sqlite-journal';journal.write_text('synthetic',encoding='utf8')
    with pytest.raises(ValueError,match='SIDE_FILE_PRESENT'):locked.verify()
    # No removal: use another isolated root for immutable-seal mutation checks.
    other=tmp_path/'second';other.mkdir();(other/'rules-freeze.json').write_bytes(seal.read_bytes())
    (other/'runtime-freeze.json').write_bytes(runtime.read_bytes())
    locked=LockedInputs(other,require_runtime=True);locked.verify()
    (other/'runtime-freeze.json').write_text(json.dumps(dict(hashes={},tampered=True)),encoding='utf8')
    with pytest.raises(ValueError,match='RUNTIME_SEAL_CHANGED'):locked.verify()


def test_extra_unbound_history_is_rejected_even_with_a_valid_history():
    scorer,actual=sample();rules=compile_expected(dict(status='FOUND',owner='Foo',deadline='4/8'))
    history=deepcopy(actual['result_steps'][0]['records'][0]['states'])
    actual['structured_outputs'].append(dict(step='s',field='history',values=[history]))
    assert scorer(actual,rules)['correct']
    bad=deepcopy(actual);forged=deepcopy(history);forged[0]['fields']['owner']='Injected'
    bad['structured_outputs'][-1]['values'].append(forged)
    assert not scorer(bad,rules)['correct']
