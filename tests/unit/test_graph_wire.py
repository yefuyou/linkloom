"""Lossless graph receipts keep the existing 128KiB Ledger cap intact."""
from copy import deepcopy
from dataclasses import replace
import hashlib,json
import pytest
from linkloom.temporal_graph.wire import pack_graph_result,unpack_graph_result,canonical,key,REF,WIRE_SCHEMA
from linkloom.runtime.models import MAX_INLINE_TOOL_RESULT_BYTES


def large_result():
    evidence=dict(quote='经人工确认的原文。'*400,source_id='source',evidence_id='proof')
    return dict(schema_version='graph-query-result/v1',status='FOUND',steps=[dict(id=str(i),evidence=[evidence],record=dict(id='same',evidence=[evidence])) for i in range(32)])


def test_wire_roundtrip_preserves_every_field_and_stays_below_existing_ledger_cap():
    original=large_result();before=deepcopy(original);assert len(canonical(original))>MAX_INLINE_TOOL_RESULT_BYTES
    packed=pack_graph_result(original)
    assert packed['schema_version']==WIRE_SCHEMA and len(canonical(packed))<MAX_INLINE_TOOL_RESULT_BYTES
    assert unpack_graph_result(packed)==original==before


@pytest.mark.parametrize('attack',['tamper','missing','cycle','unused'])
def test_wire_reference_hash_missing_cycle_and_unbound_extra_data_fail_closed(attack):
    packed=deepcopy(pack_graph_result(large_result()));identifier=next(iter(packed['definitions']))
    if attack=='tamper':packed['definitions'][identifier]['tampered']=True
    elif attack=='missing':packed['definitions'].pop(identifier)
    elif attack=='cycle':packed['definitions'][identifier]={REF:identifier}
    else:packed['definitions'][key({'unused':True})]={'unused':True}
    with pytest.raises(ValueError):unpack_graph_result(packed)


def test_wire_expansion_bomb_is_rejected_without_raising_ledger_cap():
    body={'text':'x'*40000};identifier=key(body)
    packed=dict(schema_version=WIRE_SCHEMA,value=dict(schema_version='graph-query-result/v1',values=[{REF:identifier} for _ in range(60)]),definitions={identifier:body})
    assert len(canonical(packed))<MAX_INLINE_TOOL_RESULT_BYTES
    with pytest.raises(ValueError,match='EXPANSION_LIMIT'):unpack_graph_result(packed)


def test_full_meeting_comparison_can_be_ledger_recorded_and_reviewer_replayed_without_truncation():
    from datetime import UTC,datetime
    from linkloom.temporal_graph.models import Node,Edge,GraphSnapshot
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    from linkloom.ui.graph_investigation import GraphInvestigationService
    from tests.unit.test_meeting_graph_query import fixture
    store,first,second,old,new,edges,records=fixture()
    quote='已确认、可引用的长段原文。'*450
    proof=replace(edges[0].evidence[0],quote=quote,quote_sha256=hashlib.sha256(quote.encode()).hexdigest())
    edges[:]=[];records[:]=records[:2]
    for i in range(8):
        decision=Node('w','decision_record','stable-'+str(i),'确认决定'+str(i))
        records.append(dict(node_id=decision.node_id,kind='decision_record',record_id=decision.key,subject_key='slot-'+str(i),value='决定'+str(i),status='ACTIVE',valid_from='2026-04-01',valid_to=None,evidence=[proof.to_dict()]))
        edges.append(Edge(decision,first,'decided_in','2026-04-01',None,'interval','event-'+str(i),'approved-'+str(i),(proof,)))
    step=dict(id='diff',operation='compare',input={'node':first.node_id},other=second.node_id,domain='decisions',as_of=None)
    plan=MeetingGraphPlan.from_dict(dict(steps=[step],outputs=[dict(step='diff',field='comparison')],as_of='2026-06-01'),nodes=store.load_edges('w').nodes)
    class Planner:
        def plan(self,context):return dict(status='PLANNED',plan=plan)
    service=GraphInvestigationService(lambda ws:store,lambda ctx:Planner(),query_clock=lambda:datetime(2026,6,1,tzinfo=UTC))
    result=service.run('比较两场会议的已确认决定','w')
    assert result['status']=='COMPLETED' and result['reviewer']['accepted'],result
    assert len(result['source_investigation']['graph_query']['outputs'][0]['values'][0]['unchanged'])==8
    assert service.inspect(result['run_id'],'w')['status']=='COMPLETED'
    record=service._runs[result['run_id']][2]
    assert record.result['value']['schema_version']==WIRE_SCHEMA
    assert len(canonical(record.result))<MAX_INLINE_TOOL_RESULT_BYTES


def test_reference_depth_is_bounded_before_python_recursion():
    definitions={}
    for i in range(80):definitions[f'{i:064x}']={REF:f'{i+1:064x}'} if i<79 else {'end':True}
    value=dict(schema_version=WIRE_SCHEMA,value={REF:f'{0:064x}'},definitions=definitions)
    with pytest.raises(ValueError,match='DEPTH_LIMIT'):unpack_graph_result(value)


def nested(depth,value):
    for _ in range(depth):value={'child':value}
    return value


def test_cached_definition_cannot_bypass_expanded_depth_at_a_deeper_reference():
    body=nested(60,{'text':'x'});identifier=key(body)
    packed=dict(schema_version=WIRE_SCHEMA,definitions={identifier:body},value=dict(schema_version='graph-query-result/v1',first={REF:identifier},later=nested(10,{REF:identifier})))
    with pytest.raises(ValueError,match='DEPTH_LIMIT'):unpack_graph_result(packed)


def test_depth_limit_is_identical_for_small_plain_and_shared_results():
    too_deep=dict(schema_version='graph-query-result/v1',value=nested(66,'x'))
    with pytest.raises(ValueError,match='DEPTH_LIMIT'):pack_graph_result(too_deep)
    with pytest.raises(ValueError,match='DEPTH_LIMIT'):unpack_graph_result(too_deep)
    valid=dict(schema_version='graph-query-result/v1',rows=[{'item':nested(59,{'text':'x'*2000})} for _ in range(80)])
    packed=pack_graph_result(valid)
    assert packed['schema_version']==WIRE_SCHEMA
    assert unpack_graph_result(packed)==valid
