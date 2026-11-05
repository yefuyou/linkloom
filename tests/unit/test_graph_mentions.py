"""Generic mention, Host retrieval and receipt boundaries on synthetic records."""
from copy import deepcopy
from datetime import UTC,datetime
import hashlib,json
import numpy as np
import pytest
from linkloom.temporal_graph.entity_locator import LocalMentionLocator,LocatorConfig,mention_text,validate_definition
from linkloom.temporal_graph.models import Node,GraphSnapshot
from linkloom.temporal_graph.planning import GraphPlanningContext
from linkloom.temporal_graph.mention_planning import compile_plan,schema
from linkloom.temporal_graph.query import graph_query
from tests.unit.test_temporal_graph_planning import Model,planner
from tests.unit.test_meeting_graph_query import fixture


def mention(text,kind='meeting',reference='direct',key='m'):
    return dict(id=key,mention=text,kind=kind,reference=reference)


def raw(mentions,steps,outputs,mode='list',kinds=None):
    return dict(applicable=True,as_of='',refusal_reasons=[],intent=dict(mode=mode,result_kinds=kinds or ['decision_record']),mentions=mentions,steps=steps,outputs=outputs)


def membership(text):
    return raw([mention(text)],[dict(id='decisions',operation='traverse',input={'mention':'m'},as_of='',edge_type='decided_in',direction='in',cardinality='list',selection='all')],[dict(step='decisions',field='record')])


class FixedLocator:
    def __init__(self,nodes,status='FOUND'):self.nodes=nodes;self.status=status;self.calls=[]
    def locate(self,snapshot,text,kind,when,mode):
        self.calls.append((text,kind,when,mode))
        return dict(status=self.status,node_ids=[n.node_id for n in self.nodes],reason=None,catalogue_sha256=__import__('linkloom.temporal_graph.models',fromlist=['digest']).digest([n.to_dict() for n in snapshot.nodes]))


def test_model_sees_labels_but_never_directory_handles_and_cannot_choose_ids():
    store,first,*_=fixture();ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True)
    model=Model(membership(first.label));result=planner(model,mention_grounding=True).plan(ctx)
    assert result['status']=='PLANNED',result
    request=model.calls[0]
    payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1])
    assert 'nodes' not in payload and 'node_metadata' not in payload and 'directory' in payload
    for n in ctx.nodes:assert n.node_id not in request.user_input and n.node_id not in json.dumps(request.available_tools[0].input_schema)
    step=result['plan'].steps[0]
    assert step['input']['entity_mention']['text']==first.label
    assert all(n.node_id not in json.dumps(result['plan'].to_dict()) for n in ctx.nodes)
    forged=membership(first.label);forged['steps'][0]['input']={'node':first.node_id}
    assert planner(Model(forged),mention_grounding=True).plan(ctx)['status']=='OUTPUT_INVALID'


@pytest.mark.parametrize('value',['  “Quarterly digest”？  ',"'Quarterly digest'",'Quarterly digest'])
def test_only_outer_space_and_punctuation_can_be_removed(value):
    assert mention_text(value,'Who owns Quarterly digest?')=='Quarterly digest'
    with pytest.raises(ValueError,match='MENTION_NOT_LITERAL'):mention_text('Quarterly  digest','Quarterly digest')
    with pytest.raises(ValueError):mention_text('invented digest','Quarterly digest')


@pytest.mark.parametrize('attack',['unknown_ref','duplicate_id','extra_candidate_id','reword','source_position'])
def test_host_rejects_mention_reference_and_provenance_forgery(attack):
    store,first,*_=fixture();ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True);request=membership(first.label)
    if attack=='unknown_ref':request['steps'][0]['input']['mention']='future'
    if attack=='duplicate_id':request['mentions'].append(deepcopy(request['mentions'][0]))
    if attack=='extra_candidate_id':request['mentions'][0]['candidates']=[first.node_id]
    if attack=='reword':request['mentions'][0]['mention']='reworded'
    if attack=='source_position':
        plan=compile_plan(request,ctx);definition=deepcopy(plan.steps[0]['input']['entity_mention']);definition['source']['start']+=1
        with pytest.raises(ValueError):validate_definition(definition)
        return
    with pytest.raises(ValueError):compile_plan(request,ctx)


def test_successful_host_binding_uses_existing_temporal_tool_and_source_evidence():
    store,first,*_=fixture();store.mention_locator=FixedLocator([first]);ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True)
    plan=compile_plan(membership(first.label),ctx);result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND' and len(result['outputs'][0]['values'])==1
    assert result['steps'][1]['edges'][0]['evidence']
    assert result['steps'][0]['root_resolution']['node_ids']==[first.node_id]


@pytest.mark.parametrize('status',['NOT_FOUND','AMBIGUOUS','BLOCKED'])
def test_failed_host_binding_blocks_all_dependent_answers(status):
    from linkloom.agents.graph_result import project_meeting_result
    store,first,second,*_=fixture();store.mention_locator=FixedLocator([first,second] if status=='AMBIGUOUS' else [],status)
    ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True);plan=compile_plan(membership(first.label),ctx)
    result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']==status and result['outputs']==[]
    assert result['steps'][1]['status']=='BLOCKED'
    view,_=project_meeting_result(result);assert view['facts']==[]
    if status=='NOT_FOUND':assert "未找到已确认的'"+first.label+"'" in view['answer']


def test_compare_resolves_both_mentions_and_completes_both_domains():
    store,first,second,*_=fixture()
    class Dynamic(FixedLocator):
        def locate(self,snapshot,text,kind,when,mode):
            self.nodes=[first if text==first.label else second];return super().locate(snapshot,text,kind,when,mode)
    store.mention_locator=Dynamic([])
    request=raw([mention(first.label,key='a'),mention(second.label,key='b')],[dict(id='diff',operation='compare',input={'mention':'b'},other_mention='a',domain='decisions',as_of='')],[dict(step='diff',field='comparison')],'compare',['decision_record','task'])
    ctx=GraphPlanningContext(first.label+' vs '+second.label,'w',store.load_edges('w').nodes,lambda:True);plan=compile_plan(request,ctx)
    result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND',result
    assert {s['domain'] for s in plan.steps if s['operation']=='compare'}=={'decisions','tasks'}
    assert len(result['outputs'])==2 and result['outputs'][0]['values'][0]['order_swapped']


def test_temporal_root_filter_uses_consumer_explicit_date():
    store,first,*_=fixture();store.mention_locator=FixedLocator([first]);ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True)
    request=membership(first.label);request['steps'][0]['as_of']='2026-04-02';plan=compile_plan(request,ctx)
    result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND' and store.mention_locator.calls[0][2]=='2026-04-02'


class Embedder:
    def __init__(self):self.calls=[]
    def encode(self,texts):self.calls.append(texts);return np.array([[1,.01*(sum(map(ord,text))%7)] for text in texts])


class Local:
    mode='local'
    def __init__(self,scores=None,fallback=False):self.scores=scores or {};self.fallback=fallback;self.last_trace={};self.batch_sizes=[]
    def rerank(self,text,candidates):
        from linkloom.rerank.base import score_candidates
        self.batch_sizes.append(len(candidates));self.last_trace=dict(status='fallback' if self.fallback else 'success',backend='noop' if self.fallback else 'local')
        return score_candidates(candidates,[self.scores.get(c.evidence.resource_id,5.0) for c in candidates])


def directory(count=2,same=False):
    nodes=tuple(Node('w','meeting',str(i),'Digest' if same else 'Digest '+str(i)) for i in range(count))
    return GraphSnapshot('w',(),(),nodes,tuple(dict(node_id=n.node_id,kind='meeting',meeting_date='2030-01-01',pending_count=0) for n in nodes))


def locator(scores=None,**kw):return LocalMentionLocator(LocatorConfig(min_score=2,min_margin=.2,min_identity_similarity=.5),embedder=Embedder(),reranker=Local(scores,**kw))


def test_actual_hybrid_and_local_score_gate_unique_close_and_low():
    snap=directory();a,b=snap.nodes
    assert locator({a.node_id:5,b.node_id:1}).locate(snap,'Digest','meeting','2031-01-01')['node_ids']==[a.node_id]
    assert locator({a.node_id:5,b.node_id:4.9}).locate(snap,'Digest','meeting','2031-01-01')['status']=='AMBIGUOUS'
    assert locator({a.node_id:1,b.node_id:1}).locate(snap,'Digest','meeting','2031-01-01')['status']=='NOT_FOUND'


def test_close_runner_just_below_threshold_and_identical_titles_stay_ambiguous():
    snap=directory();a,b=snap.nodes
    assert locator({a.node_id:2.05,b.node_id:1.95}).locate(snap,'Digest','meeting','2031-01-01')['status']=='AMBIGUOUS'
    snap=directory(same=True);a,b=snap.nodes
    assert locator({a.node_id:5,b.node_id:-5}).locate(snap,'Digest','meeting','2031-01-01')['status']=='AMBIGUOUS'


def test_all_directory_candidates_are_scored_in_local_batches_without_top20_guess():
    snap=directory(25);engine=locator();ranked=engine.rank(snap,'Digest','meeting','2031-01-01')
    assert len(ranked['candidates'])==25 and sum(engine.reranker.batch_sizes)==25 and max(engine.reranker.batch_sizes)<=20
    assert engine.locate(snap,'Digest','meeting','2031-01-01')['status']=='LIMIT_EXCEEDED'


def test_fallback_rrf_is_never_treated_as_local_success_or_unknown_absence():
    snap=directory();result=locator(fallback=True).locate(snap,'Digest','meeting','2031-01-01')
    assert result['status']=='BLOCKED' and result['reason']=='LOCAL_ENTITY_RETRIEVAL_UNAVAILABLE'
    engine=locator();engine.reranker.mode='jev'
    assert engine.locate(snap,'Digest','meeting','2031-01-01')['status']=='BLOCKED'


def test_workspace_filter_precedes_embedding_and_future_date_cannot_be_replaced():
    snap=directory();engine=locator();assert engine.locate(snap,'2032/1/2 Digest','meeting','2031-01-01')['status']=='NOT_FOUND'
    assert engine.embedder.calls==[]
    foreign=Node('other','meeting','x','Digest');bad=GraphSnapshot('w',(),(),(foreign,),())
    with pytest.raises(PermissionError):engine.locate(bad,'Digest','meeting','2031-01-01')
    assert engine.embedder.calls==[]


def test_actual_receipt_reviewer_reruns_locator_after_candidate_directory_change():
    from linkloom.ui.graph_investigation import GraphInvestigationService
    store,first,second,*_=fixture();locator_=FixedLocator([first]);store.mention_locator=locator_
    ctx=GraphPlanningContext(first.label+' decisions','w',store.load_edges('w').nodes,lambda:True);plan=compile_plan(membership(first.label),ctx)
    class Planner:
        def plan(self,context):return dict(status='PLANNED',plan=plan)
    service=GraphInvestigationService(lambda ws:store,lambda context:Planner(),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=service.run(ctx.question,'w');assert result['status']=='COMPLETED',result
    locator_.nodes=[first,second];locator_.status='AMBIGUOUS'
    assert service.inspect(result['run_id'],'w')['status']=='BLOCKED'



def test_raw_question_whitespace_is_preserved_in_mention_source_hash():
    store,first,*_=fixture();ctx=GraphPlanningContext('  '+first.label+' decisions  ','w',store.load_edges('w').nodes,lambda:True)
    plan=compile_plan(membership(first.label),ctx)
    definition=plan.steps[0]['input']['entity_mention']
    assert validate_definition(definition)['source']['question']==ctx.question


def test_history_operation_follows_real_endpoint_type_after_decision_to_task():
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','item','Artifact')
    ctx=GraphPlanningContext(new.label+' Artifact history','w',store.load_edges('w').nodes+(task,),lambda:True)
    request=raw([mention(new.label,'decision_record')],[dict(id='tasks',operation='traverse',input={'mention':'m'},as_of='',edge_type='derived_from',direction='in',cardinality='list',selection='all'),dict(id='history',operation='inspect',input={'step':'tasks'},as_of='')],[dict(step='history',field='history')],'history',['task'])
    plan=compile_plan(request,ctx)
    assert next(s for s in plan.steps if s['id']=='history')['operation']=='inspect'


def test_historical_directory_never_uses_a_later_task_title_or_parent_change():
    from linkloom.temporal_graph.entity_locator import directory_rows
    store,first,second,old,new,edges,records=fixture();task=Node('w','task','item','Future artifact')
    proof=[e.to_dict() for e in edges[0].evidence]
    common=dict(status='open',confirmation_ref='approved',event_ids=['e'],review_event_id='review',evidence=proof)
    states=[dict(common,valid_from='2026-04-01',valid_to='2026-05-01',fields=dict(title='Earlier artifact',owner='One')),dict(common,valid_from='2026-05-01',valid_to=None,fields=dict(title='Future artifact',owner='Two'))]
    record=dict(node_id=task.node_id,kind='task',task_id=task.key,title=task.label,states=states,status='open')
    snapshot=GraphSnapshot('w',(),(),(task,),(record,))
    earlier=directory_rows(snapshot,'task','2026-04-02');later=directory_rows(snapshot,'task','2026-05-02')
    assert earlier[0]['identity']=='Earlier artifact' and 'Future artifact' not in earlier[0]['text']
    assert later[0]['identity']=='Future artifact'



def test_previous_mention_is_resolved_inside_tool_with_workspace_baseline():
    store,first,second,*_=fixture();store.mention_locator=FixedLocator([second])
    request=raw([mention(second.label,key='current'),mention(second.label,reference='previous',key='before')],[dict(id='diff',operation='compare',input={'mention':'current'},other_mention='before',domain='tasks',as_of='')],[dict(step='diff',field='comparison')],'compare',['task'])
    ctx=GraphPlanningContext(second.label+' changes','w',store.load_edges('w').nodes,lambda:True);plan=compile_plan(request,ctx)
    result=graph_query(store,list(plan.steps),'2026-06-01','w',outputs=list(plan.outputs))
    assert result['status']=='FOUND'
    prior=next(r for r in result['steps'] if r.get('root_resolution',{}).get('anchor'))
    assert prior['root_resolution']['node_ids']==[first.node_id]
    assert all(o['values'][0]['baseline_scope']=='workspace' for o in result['outputs'])


def test_even_an_injected_locator_cannot_return_future_meeting_identity():
    store,first,second,*_=fixture();store.mention_locator=FixedLocator([second])
    ctx=GraphPlanningContext(second.label+' decisions','w',store.load_edges('w').nodes,lambda:True);plan=compile_plan(membership(second.label),ctx)
    with pytest.raises(ValueError,match='LOCATOR_RETURNED_INELIGIBLE_NODE'):
        graph_query(store,list(plan.steps),'2026-03-01','w',outputs=list(plan.outputs))


def test_explicit_meeting_timestamp_is_filtered_before_embedding():
    snapshot=directory(1);record=dict(snapshot.records[0],meeting_time='2030-01-01T18:00:00+00:00')
    snapshot=GraphSnapshot('w',(),(),snapshot.nodes,(record,));engine=locator()
    result=engine.locate(snapshot,'Digest','meeting','2030-01-01T09:00:00+00:00')
    assert result['status']=='NOT_FOUND' and not engine.embedder.calls
