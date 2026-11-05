from dataclasses import replace
from datetime import UTC,datetime
import hashlib
import pytest
from linkloom.temporal_graph.models import Node,Edge,Evidence,GraphSnapshot,GraphLimits
from linkloom.temporal_graph.query import graph_query


def evidence(ref='ev',workspace='w'):
    text='已确认的结构化关系，证据原文可采用任何句式。'
    hashed=hashlib.sha256(text.encode()).hexdigest()
    return Evidence(workspace,ref,'source','v1',hashed,text,hashed,1,1)


def node(kind,label,scope=''): return Node('w',kind,label,label,scope)

def edge(a,b,kind='reports_to',start='2026-01-01',end=None,**changes):
    return Edge(a,b,kind,start,end,'interval','record-'+a.key+'-'+b.key,'human-review',(evidence(),),**changes)

class Store:
    def __init__(self,edges): self.edges=tuple(edges)
    def load_edges(self,workspace,as_of=None,edge_types=None):
        if workspace!='w': raise PermissionError('workspace')
        return GraphSnapshot(workspace,self.edges)

def step(key,inp,relation='reports_to',direction='out',date=None):
    return dict(id=key,input=inp,edge_type=relation,direction=direction,as_of=date)


def test_generic_new_combination_uses_only_edge_evidence_and_direction():
    alias,old,new,owner=node('alias','code'),node('plan','old'),node('plan','new'),node('person','Ada')
    store=Store([edge(alias,old,'alias_of'),edge(new,old,'supersedes'),edge(owner,new,'owner_of')])
    rows=[step('a',{'node':alias.node_id},'alias_of'),step('b',{'step':'a'},'supersedes','in'),
          step('c',{'step':'b'},'owner_of','in')]
    result=graph_query(store,rows,'2026-06-01','w')
    assert result['status']=='FOUND'
    assert result['outputs'][0]['values'][0]['label']=='Ada'
    assert len(result['steps'])==3
    assert all(row['edges'][0]['evidence'][0]['quote']==evidence().quote for row in result['steps'])


def test_multiple_candidates_never_selected_by_arrival_order():
    a,b,c=node('person','A'),node('person','B'),node('person','C')
    rows=[step('a',{'node':a.node_id})]
    for edges in ([edge(a,b),edge(a,c)],[edge(a,c),edge(a,b)]):
        result=graph_query(Store(edges),rows,'2026-06-01','w')
        assert result['status']=='AMBIGUOUS' and result['outputs']==[]
        assert {v['label'] for v in result['steps'][0]['candidates']}=={'B','C'}


def test_dated_edges_are_left_closed_right_open_and_day_precision_is_not_guessed():
    a,b,c=node('person','A'),node('person','B'),node('person','C')
    rows=[step('a',{'node':a.node_id})]
    store=Store([edge(a,b,end='2026-06-01'),edge(a,c,start='2026-06-01')])
    assert graph_query(store,rows,'2026-05-31','w')['outputs'][0]['values'][0]['label']=='B'
    assert graph_query(store,rows,'2026-06-01','w')['outputs'][0]['values'][0]['label']=='C'
    store=Store([edge(a,b,start='2026-01-01T00:00:00Z',end='2026-06-01T12:00:00Z'),edge(a,c,start='2026-06-01T12:00:00Z')])
    assert graph_query(store,rows,'2026-06-01','w')['status']=='AMBIGUOUS'
    assert graph_query(store,rows,'2026-06-01T13:00:00Z','w')['outputs'][0]['values'][0]['label']=='C'


def test_snapshot_never_extends_and_step_date_can_override_global():
    a,b=node('person','A'),node('person','B')
    e=replace(edge(a,b),valid_from='2026-04-01',valid_time_kind='snapshot')
    assert graph_query(Store([e]),[step('s',{'node':a.node_id})],'2026-04-02','w')['status']=='UNKNOWN'
    assert graph_query(Store([e]),[step('s',{'node':a.node_id},date='2026-04-01')],'2026-04-02','w')['status']=='FOUND'


def test_confirmed_counter_evidence_blocks_and_missing_parent_never_advances():
    a,b,c=node('person','A'),node('person','B'),node('person','C')
    rows=[step('a',{'node':a.node_id}),step('b',{'step':'a'})]
    store=Store([edge(a,b),edge(a,b,blocker=True),edge(b,c)])
    result=graph_query(store,rows,'2026-04-01','w')
    assert result['status']=='CONFLICT'
    assert result['steps'][1]['status']=='BLOCKED' and result['outputs']==[]
    assert len(result['steps'][0]['edges'])==2


def test_repeated_relation_is_generic_but_four_hops_and_cycles_fail_closed():
    nodes=[node('person',str(i)) for i in range(5)]
    store=Store([edge(a,b) for a,b in zip(nodes,nodes[1:])])
    rows=[step('s0',{'node':nodes[0].node_id})]+[step('s'+str(i),{'step':'s'+str(i-1)}) for i in range(1,4)]
    assert graph_query(store,rows[:3],'2026-04-01','w')['status']=='FOUND'
    with pytest.raises(ValueError,match='HOP_LIMIT'): graph_query(store,rows,'2026-04-01','w')
    cycle=Store([edge(nodes[0],nodes[1]),edge(nodes[1],nodes[0])])
    assert graph_query(cycle,rows[:2],'2026-04-01','w')['status']=='LIMIT_EXCEEDED'


def test_missing_last_hop_preserves_only_verified_prefix():
    a,b=node('person','A'),node('person','B')
    rows=[step('a',{'node':a.node_id}),step('b',{'step':'a'})]
    result=graph_query(Store([edge(a,b)]),rows,'2026-04-01','w',outputs=[dict(step='a',field='node'),dict(step='b',field='node')])
    assert result['status']=='PARTIAL' and len(result['outputs'])==1
    assert result['outputs'][0]['values'][0]['label']=='B'


def test_limits_and_quote_integrity_are_enforced():
    with pytest.raises(ValueError): GraphLimits(max_hops=4)
    with pytest.raises(ValueError,match='HASH'): replace(evidence(),quote='tampered')
    a,b,c=node('person','A'),node('person','B'),node('person','C')
    result=graph_query(Store([edge(a,b),edge(b,c)]),[step('s',{'node':a.node_id})],'2026-04-01','w',limits=GraphLimits(max_edges=1))
    assert result['status']=='LIMIT_EXCEEDED' and result['outputs']==[]
