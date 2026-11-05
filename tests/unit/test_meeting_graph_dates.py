"""Literal date binding and exact root lookup; no question templates or model calls."""
from copy import deepcopy
from dataclasses import replace
import pytest
from linkloom.temporal_graph.dates import parse_date_literal
from linkloom.temporal_graph.query import graph_query
from linkloom.temporal_graph.planning import GraphPlanningContext,GraphQuestionPlanner,_schema_for_model
from linkloom.agents.providers.gemini_api import map_tool_definition_to_gemini_function
from linkloom.agents.providers.deepseek_api import map_tool_definition_to_deepseek_tool
from linkloom.tools.contracts import ToolDefinition
from tests.unit.test_meeting_graph_query import fixture,traverse
from tests.unit.test_temporal_graph_planning import Model,planner


@pytest.mark.parametrize('literal,expected',[('4/1',(None,4,1)),('04/01',(None,4,1)),('4月1日',(None,4,1)),('2026-4-1',(2026,4,1)),('2026/04/01',(2026,4,1)),('2026.04.01',(2026,4,1)),('2026年4月1日',(2026,4,1))])
def test_date_types_are_independent_of_word_order(literal,expected):
    assert parse_date_literal(literal)==expected


@pytest.mark.parametrize('literal',['2/30','2026-02-29','4/1;DROP TABLE','2026-04-01T00:00:00','开会时'])
def test_invalid_or_non_literal_inputs_are_not_interpreted(literal):
    with pytest.raises(ValueError):parse_date_literal(literal)


def test_exact_meeting_date_lookup_never_selects_a_neighbour_and_retains_time_status():
    store,first,second,old,new,edges,records=fixture()
    root=dict(id='meeting',operation='resolve',input={'meeting_date':'4/2'},as_of=None)
    missing=graph_query(store,[root],'2026-06-01','w',outputs=[dict(step='meeting',field='node')])
    assert missing['status']=='NOT_FOUND' and missing['steps'][0]['output_nodes']==[]
    root['input']={'meeting_date':'4月1日'}
    found=graph_query(store,[root],'2026-06-01','w',outputs=[dict(step='meeting',field='node')])
    assert found['status']=='FOUND' and found['outputs'][0]['values'][0]['node_id']==first.node_id
    query=[root,dict(traverse(first),id='decisions',input={'step':'meeting'})]
    before=graph_query(store,query,'2026-03-01','w',outputs=[dict(step='decisions',field='record')])
    assert before['status']=='NO_VALID_RECORD_AT_TIME'
    direct=graph_query(store,[traverse(first,kind='assigned_in')],'2026-03-01','w',outputs=[dict(step='s',field='record')])
    assert direct['status']=='NO_VALID_RECORD_AT_TIME'
    records[1]['meeting_date']='2026-04-01'
    conflict=graph_query(store,[root],'2026-06-01','w',outputs=[dict(step='meeting',field='record')])
    assert conflict['status']=='AMBIGUOUS' and len(conflict['steps'][0]['records'])==2


def test_gemini_transport_subset_keeps_closed_host_operation_validation():
    store,first,*_=fixture();ctx=GraphPlanningContext('会上确认的事都列出','w',(first,),lambda:True)
    schema=_schema_for_model(ctx,'gemini-3.8-flash')
    declaration=ToolDefinition('emit_graph_plan','1','Structured plan',schema,{'type':'object'})
    assert map_tool_definition_to_gemini_function(declaration)['name']=='emit_graph_plan'
    assert map_tool_definition_to_deepseek_tool(declaration)['function']['name']=='emit_graph_plan'
    assert 'anyOf' not in str(schema)
    raw=dict(applicable=True,as_of='',refusal_reasons=[],steps=[dict(traverse(first),as_of='')],outputs=[dict(step='s',field='record')])
    def session(output):
        value=planner(Model(output));value.session.model_id='gemini-3.8-flash';return value
    assert session(raw).plan(ctx)['status']=='PLANNED'
    raw['steps'][0]['domain']='decisions'
    assert session(raw).plan(ctx)['status']=='OUTPUT_INVALID'


def test_dates_are_explicit_scalar_queries_not_host_question_templates():
    # Question semantics belong to the constrained model and its intent review.
    # The Host validates date scalars and exact authorized graph lookup only.
    store,first,*_=fixture()
    raw=dict(applicable=True,as_of='',refusal_reasons=[],steps=[dict(id='root',operation='resolve',input={'meeting_date':'4/2'},as_of='')],outputs=[dict(step='root',field='record')])
    ctx=GraphPlanningContext('4/2那场，确认了哪些事？','w',(first,),lambda:True)
    assert planner(Model(raw)).plan(ctx)['status']=='PLANNED'
    for bad in ['2026-02-30','2026_04_01','2026/13/01']:
        raw['steps'][0]['input']['meeting_date']=bad
        assert planner(Model(raw)).plan(ctx)['status']=='OUTPUT_INVALID'
