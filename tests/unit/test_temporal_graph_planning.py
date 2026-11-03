from copy import deepcopy
from dataclasses import replace
import pytest
from linkloom.agents.model_adapter import ModelAction,ModelResponse,ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_question_models import PlanningLimits
from linkloom.temporal_graph.planning import GraphQuestionPlanner,GraphPlanningContext
from linkloom.tools.contracts import ToolCall
from tests.unit.test_temporal_graph_query import node

class Model:
    def __init__(self,output,callback=None):self.output=output;self.calls=[];self.callback=callback
    def complete(self,request):
        self.calls.append(request)
        if self.callback:self.callback()
        return ModelResponse(action=ModelAction.tool(ToolCall('emit',request.available_tools[0].tool_id,deepcopy(self.output))),
                             usage=ModelUsage(input_tokens=20,output_tokens=20,total_tokens=40))

def context():return GraphPlanningContext('交付这摊事该找哪位、对方直接向哪位汇报？','w',(node('project','Bay'),node('person','Ada')),lambda:True,'2026-04-01')

def output(ctx):
    return dict(applicable=True,as_of='',refusal_reasons=[],steps=[
      dict(id='owner',input_node=ctx.nodes[0].node_id,input_step='',edge_type='owner_of',direction='in',as_of=''),
      dict(id='manager',input_node='',input_step='owner',edge_type='reports_to',direction='out',as_of='')],
      outputs=[dict(step='manager',field='node')])

def planner(model,**kwargs):
    guard=lambda request:ProviderRequestAuthorization.approve(request,guard_id='test',reservation_id='local',estimated_cost_upper_bound_usd=0)
    return GraphQuestionPlanner(model,model_id='fake',request_guard=guard,guard_id='test',**kwargs)


def test_paraphrase_is_passed_to_model_and_not_matched_by_question_regex():
    ctx=context();model=Model(output(ctx));session=planner(model)
    result=session.plan(ctx)
    assert result['status']=='PLANNED' and len(model.calls)==1
    assert ctx.question in model.calls[0].user_input
    assert result['plan'].steps[1]['input']=={'step':'owner'}
    assert session.plan(ctx)['status']=='BLOCKED' and len(model.calls)==1

@pytest.mark.parametrize('attack',['invented_node','future_parent','foreign_fields','wrong_direction','changed_date'])
def test_model_cannot_invent_scope_or_turn_plan_into_facts(attack):
    ctx=context();raw=output(ctx)
    if attack=='invented_node':raw['steps'][0]['input_node']='forged'
    if attack=='future_parent':raw['steps'][1]['input_step']='future'
    if attack=='foreign_fields':raw['approved']=True
    if attack=='wrong_direction':raw['steps'][0]['direction']='anything'
    if attack=='changed_date':raw['as_of']='2026-05-01'
    assert planner(Model(raw)).plan(ctx)['status']=='OUTPUT_INVALID'


def test_budget_and_guard_fail_before_provider_and_changed_context_after_call_cannot_publish():
    ctx=context();model=Model(output(ctx))
    assert planner(model,limits=PlanningLimits(max_input_tokens=1)).plan(ctx)['status']=='BLOCKED' and model.calls==[]
    current=[True];ctx=replace(ctx,validate_current=lambda:current[0]); model=Model(output(ctx),lambda:current.__setitem__(0,False))
    assert planner(model).plan(ctx)['status']=='OUTPUT_INVALID'
    assert len(model.calls)==1


def test_non_graph_refusal_is_structured_and_does_not_generate_answers():
    ctx=context();raw=dict(applicable=False,steps=[],outputs=[],as_of='',refusal_reasons=[])
    assert planner(Model(raw)).plan(ctx)['status']=='NOT_APPLICABLE'


def test_meeting_profile_emits_structured_list_operations_without_question_regex():
    from linkloom.temporal_graph.models import Node
    from linkloom.temporal_graph.planning import GraphPlanningContext
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    meeting=Node('w','meeting','M1','10月周会')
    raw=dict(applicable=True,as_of='',refusal_reasons=[],steps=[dict(id='decisions',operation='traverse',input_node=meeting.node_id,
        input_step='',as_of='',edge_type='decided_in',direction='in',cardinality='list',selection='all',other_node='',domain='')],
        outputs=[dict(step='decisions',field='record')])
    model=Model(raw)
    result=planner(model).plan(GraphPlanningContext('那天究竟拍板了几件事，都列一下','w',(meeting,),lambda:True))
    assert result['status']=='PLANNED' and isinstance(result['plan'],MeetingGraphPlan),result
    assert result['plan'].steps[0]['cardinality']=='list'
    assert len(model.calls)==1


@pytest.mark.parametrize('attack',[None,'comparison_field','forged_node','multiple_inputs','missing_time','invalid_time','foreign_field'])
def test_discriminated_meeting_schema_preserves_strict_known_scope_and_closed_fields(attack):
    from linkloom.temporal_graph.models import Node
    meeting=Node('w','meeting','m','例会')
    ctx=GraphPlanningContext('把会上确认的事项列出来','w',(meeting,),lambda:True)
    step=dict(id='list',operation='traverse',input={'node':meeting.node_id},as_of='',edge_type='decided_in',direction='in',cardinality='list',selection='all')
    if attack=='comparison_field':step['domain']='decisions'
    elif attack=='forged_node':step['input']['node']='forged'
    elif attack=='multiple_inputs':step['input']['step']='another'
    elif attack=='missing_time':step.pop('as_of')
    elif attack=='invalid_time':step['as_of']=0
    elif attack=='foreign_field':step['approved']=True
    raw=dict(applicable=True,steps=[step],outputs=[dict(step='list',field='record')],as_of='',refusal_reasons=[])
    model=Model(raw);result=planner(model).plan(ctx)
    assert result['status']==('PLANNED' if attack is None else 'OUTPUT_INVALID'),result
    schema=model.calls[0].available_tools[0].input_schema
    branches=schema['properties']['steps']['items']['anyOf']
    traversal=next(b for b in branches if b['properties']['operation']['enum']==['traverse'])
    assert 'domain' not in traversal['properties'] and 'other' not in traversal['properties']
    assert traversal['additionalProperties'] is False


def test_host_clock_is_explicit_and_covered_by_context_and_request_signature():
    import json
    ctx=replace(context(),query_time='2026-11-03T00:00:00+00:00');model=Model(output(ctx));result=planner(model).plan(ctx)
    assert result['status']=='PLANNED'
    payload=json.loads(model.calls[0].user_input.split('INPUT_JSON\n')[1])
    assert payload['query_time']=='2026-11-03T00:00:00+00:00'
    assert ctx.check()!=replace(ctx,query_time='2026-11-04T00:00:00+00:00').check()


class ReviewedModel(Model):
    def __init__(self,draft,review,callback=None):super().__init__(draft);self.review=review;self.review_callback=callback
    def complete(self,request):
        if len(self.calls)==1:
            self.output=self.review
            if self.review_callback:self.review_callback()
        return super().complete(request)


def test_two_call_intent_review_preserves_exact_plan_and_cumulative_budget():
    ctx=context();model=ReviewedModel(output(ctx),dict(aligned=True,reason='ALIGNED'))
    session=planner(model,alignment_review=True);result=session.plan(ctx)
    assert result['status']=='PLANNED' and len(model.calls)==2 and result['provider_calls']==2
    assert result['usage']['total_tokens']==80
    assert model.calls[1].sequence==2 and model.calls[1].generation_options.max_output_tokens==session.session.limits.max_output_tokens
    assert model.calls[1].available_tools[0].tool_id=='review_graph_plan'
    assert ctx.question in model.calls[1].user_input and 'proposed_plan' in model.calls[1].user_input
    assert result['plan'].steps[1]['input']=={'step':'owner'}
    assert session.plan(ctx)['status']=='BLOCKED' and len(model.calls)==2


@pytest.mark.parametrize('review,reason',[
    (dict(aligned=False,reason='MISALIGNED_ROOT'),'PLAN_INTENT_NOT_ALIGNED:MISALIGNED_ROOT'),
    (dict(aligned=False,reason='MISALIGNED_TIME'),'PLAN_INTENT_NOT_ALIGNED:MISALIGNED_TIME'),
    (dict(aligned=False,reason='MISSING_OUTPUT'),'PLAN_INTENT_NOT_ALIGNED:MISSING_OUTPUT'),
    (dict(aligned=True,reason='MISALIGNED_ROOT'),'INVALID_ALIGNMENT_REVIEW'),
    (dict(aligned=True,reason='ALIGNED',plan={'steps':[]}),'INVALID_ALIGNMENT_REVIEW')])
def test_intent_rejection_or_repair_attempt_cannot_publish_plan(review,reason):
    ctx=context();model=ReviewedModel(output(ctx),review);result=planner(model,alignment_review=True).plan(ctx)
    assert result['status']=='OUTPUT_INVALID' and result['plan'] is None and result['reason']==reason and len(model.calls)==2


def test_review_shares_authorization_and_never_resets_budget_or_trusts_changed_context():
    ctx=context();model=ReviewedModel(output(ctx),dict(aligned=True,reason='ALIGNED'))
    limits=PlanningLimits(max_provider_calls=1)
    result=planner(model,alignment_review=True,limits=limits).plan(ctx)
    assert result['reason']=='SESSION_BUDGET_EXHAUSTED' and len(model.calls)==1 and result['plan'] is None
    current=[True];ctx=replace(ctx,validate_current=lambda:current[0])
    model=ReviewedModel(output(ctx),dict(aligned=True,reason='ALIGNED'),lambda:current.__setitem__(0,False))
    result=planner(model,alignment_review=True).plan(ctx)
    assert result['status']=='OUTPUT_INVALID' and result['plan'] is None
    guarded=[];model=ReviewedModel(output(ctx),dict(aligned=True,reason='ALIGNED'))
    def guard(request):
        guarded.append(request.turn_id)
        if len(guarded)==2:object.__setattr__(request,'user_input',request.user_input+'tampered')
        return ProviderRequestAuthorization.approve(request,guard_id='test',reservation_id='local',estimated_cost_upper_bound_usd=0)
    ctx=context();session=GraphQuestionPlanner(model,model_id='fake',request_guard=guard,guard_id='test',alignment_review=True)
    result=session.plan(ctx)
    assert result['reason']=='EXACT_REQUEST_NOT_AUTHORIZED' and len(model.calls)==1 and len(guarded)==2



def test_draft_and_intent_review_share_same_deepseek_generation_parameters():
    from linkloom.agents.providers.deepseek_api import build_deepseek_request
    ctx=context();model=ReviewedModel(output(ctx),dict(aligned=True,reason='ALIGNED'))
    limits=PlanningLimits(max_output_tokens=1024,max_total_output_tokens=2048,max_provider_calls=2)
    assert planner(model,alignment_review=True,limits=limits).plan(ctx)['status']=='PLANNED'
    payloads=[build_deepseek_request(r) for r in model.calls]
    for payload in payloads:
        assert payload['model']=='fake' and payload['max_tokens']==1024 and payload['temperature']==0
        assert payload['thinking']=={'type':'disabled'}
