"""Read-only graph worker tool; authorization and ledger identity are Host-owned."""
from datetime import UTC,datetime
from linkloom.temporal_graph.models import EDGE_TYPES,GraphLimits
from linkloom.temporal_graph.query import graph_query
from linkloom.temporal_graph.wire import pack_graph_result
from linkloom.runtime.errors import ValidationError
from linkloom.tools.contracts import ToolDefinition
from linkloom.tools.registry import CallBoundExecutor


def register_graph_query(registry,store,workspace,*,limits=None,query_time=None):
    limits=limits or GraphLimits(); query_time=query_time or datetime.now(UTC)
    text=dict(type='string',maxLength=128)
    inp=dict(type='object',properties=dict(node=text,step=text,meeting_date=dict(type='string',maxLength=40),candidates=dict(type='array',maxItems=limits.max_fanout,items=text),entity=dict(type='object',additionalProperties=False,required=['text','kind'],properties=dict(text=dict(type='string',maxLength=160),kind=dict(type='string',enum=['task','decision_record','meeting','person','project','plan','alias','task_observation'])))),additionalProperties=False)
    inp['properties']['entity_mention']=dict(type='object',additionalProperties=False,required=['text','kind','reference','temporal_mode','source'],properties=dict(
        text=dict(type='string',maxLength=160),kind=dict(type='string',enum=['task','decision_record','meeting','person','project','plan','alias','task_observation']),
        reference=dict(type='string',enum=['direct','previous']),temporal_mode=dict(type='string',enum=['current','history']),
        source=dict(type='object',additionalProperties=False,required=['question','start','end','sha256'],properties=dict(question=dict(type='string',maxLength=2048),start=dict(type='integer',minimum=0),end=dict(type='integer',minimum=1),sha256=dict(type='string',maxLength=64)))))
    row=dict(type='object',additionalProperties=False,required=['id','input','as_of'],
      properties=dict(id=text,input=inp,edge_type=dict(type='string',enum=sorted(EDGE_TYPES)),
        direction=dict(type='string',enum=['in','out']),as_of=dict(type=['string','null'],maxLength=40),
        previous_anchor=text,baseline_scope=dict(type='string',enum=['workspace']),
        operation=dict(type='string',enum=['traverse','inspect','inspect_history','compare','resolve']),other=text,other_step=text,domain=dict(type='string',enum=['decisions','tasks']),
        target_kind=dict(type='string',enum=['task','decision_record','meeting','person','project','plan','alias','task_observation']),
        cardinality=dict(type='string',enum=['single','list']),selection=dict(type='string',enum=['all','latest'])))
    output=dict(type='object',additionalProperties=False,required=['step','field'],properties=dict(step=text,
        field=dict(type='string',enum=['node','valid_from','valid_to','record','owner','deadline','status','history','comparison','meeting_date','pending_count'])))
    props=dict(workspace=dict(type='string',enum=[workspace]),as_of=dict(type=['string','null'],maxLength=40),
      subgoals=dict(type='array',minItems=1,maxItems=limits.max_subgoals,items=row),
      outputs=dict(type='array',minItems=1,maxItems=limits.max_subgoals*3,items=output))
    definition=ToolDefinition('graph_query','1','Traverse confirmed temporal edges without side effects. Large results use lossless graph-query-wire/v1 JSON references; decode with unpack_graph_result.',
        dict(type='object',properties=props,required=list(props),additionalProperties=False),dict(type='object'))
    def execute(call):
        if not call.run_id or not call.task_id or call.agent_id!='graph-worker': raise ValueError('HOST_CALL_IDENTITY_REQUIRED')
        args=call.arguments
        if args['workspace']!=workspace: raise PermissionError('WORKSPACE_NOT_AUTHORIZED')
        result=graph_query(store,args['subgoals'],args['as_of'],workspace,outputs=args['outputs'],limits=limits,now=query_time)
        result['query_time']=query_time.isoformat()
        result['execution']=dict(run_id=call.run_id,task_id=call.task_id,agent_id=call.agent_id,call_id=call.call_id)
        return pack_graph_result(result)
    registry.register(definition,CallBoundExecutor(execute))
    return definition
