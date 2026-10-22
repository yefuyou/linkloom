from dataclasses import asdict,replace
import json
from linkloom.semantic_ingestion.task_relation_question_models import VerifiedStepResult
from linkloom.ui.task_investigation import HOST_OPERATION_CAPABILITIES
from tests.m3s1_support import saved_context,saved_arguments,saved_attempt
from tests.unit.test_task_relation_question_planner import FakeModel,planner,arguments,step

def replan_fixture():
    context=saved_context();fake=FakeModel(saved_arguments())
    session=planner(fake,host_capabilities=HOST_OPERATION_CAPABILITIES,result_verifier=lambda receipt,current:True)
    initial=session.plan(context)
    assert initial.status=='PLANNED',initial.errors
    assert initial.plan.to_dict()==saved_attempt()['planning'][0]['plan']
    discover=next(s for s in initial.plan.ready_steps if s.kind=='discover')
    receipt=VerifiedStepResult(initial.plan.plan_id,discover.id,'host_'+discover.execution_key,'a'*64,
        'Bounded F snapshot examined; no new handles; missing information remains UNKNOWN.','UNKNOWN')
    fake.arguments=arguments(dict(step('read_owner',handles=('task_0',)),evidence_gap='Owner field has not yet been inspected by Runtime.'),
        dict(step('read_deadline',handles=('task_1',)),field='deadline',evidence_gap='Date role remains unknown; Runtime has not read this observation.'))
    return context,fake,session,initial.plan,receipt

def test_original_saved_replan_fits_original_budget_without_repeating_bindings():
    context,fake,session,previous,receipt=replan_fixture()
    before=previous.to_dict()
    result=session.plan(context,previous=previous,verified_results=(receipt,),gaps=('MISSING_ENTITY_REMAINS_UNKNOWN',))
    assert result.status=='PLANNED',result.errors
    assert len(fake.requests)==2
    request=fake.requests[-1]
    payload=json.loads(request.user_input.split('INPUT_JSON\n',1)[1])
    projected=payload['replan']['previous_plan']
    assert all('endpoints' not in s and 'source_bindings' not in s for s in projected['steps'])
    assert previous.to_dict()==before  # Authoritative Host history is untouched.
    assert result.plan.verified_results==(receipt,) and receipt.outcome=='UNKNOWN'
    assert len(json.dumps(request.to_dict(),ensure_ascii=False).encode('utf8'))+1024<=min(24576,context.limits.max_input_bytes)
    assert payload['allowed_evidence']==context.model_input()['allowed_evidence']
    assert payload['endpoints']==context.model_input()['endpoints']

def test_replan_can_remove_discover_without_discarding_unknown_receipt():
    context,fake,session,previous,receipt=replan_fixture()
    result=session.plan(context,previous=previous,verified_results=(receipt,),gaps=('NO_NEW_HANDLES',),
        unavailable_operations=('discover',))
    assert result.status=='PLANNED',result.errors
    kinds=fake.requests[-1].available_tools[0].input_schema['properties']['steps']['items']['properties']['kind']['enum']
    assert 'discover' not in kinds
    assert result.plan.steps[0].kind=='discover' and result.plan.verified_results[0].outcome=='UNKNOWN'
    assert {s.id for s in result.plan.ready_steps}=={'read_owner','read_deadline'}


def test_removed_source_or_changed_workspace_blocks_before_replan_provider():
    context,fake,session,previous,receipt=replan_fixture()
    for changed in (replace(context,workspace_id='other'),replace(context,allowed_spans=context.allowed_spans[:1]),
                    replace(context,endpoints=context.endpoints[:1])):
        result=session.plan(changed,previous=previous,verified_results=(receipt,),gaps=('NO_NEW_HANDLES',))
        assert result.status=='BLOCKED' and result.plan is None
    assert len(fake.requests)==1


def test_removed_operation_cannot_be_reintroduced_by_model_output():
    context,fake,session,previous,receipt=replan_fixture()
    fake.arguments=arguments(dict(step('new_discover','discover',()),target_kind='source',
        missing_entities=['unavailable'],evidence_gap='Unknown evidence'))
    result=session.plan(context,previous=previous,verified_results=(receipt,),gaps=('NO_NEW_HANDLES',),
        unavailable_operations=('discover',))
    assert result.status=='OUTPUT_INVALID' and result.errors==('UNSUPPORTED_OPERATION',)
    assert len(fake.requests)==2


def test_unknown_discovery_never_satisfies_dependent_inspection():
    context,fake,session,previous,receipt=replan_fixture()
    fake.arguments=arguments(dict(step('dependent',handles=('task_0',)),depends_on=[receipt.step_id],evidence_gap='Still unread'),
                             dict(step('independent',handles=('task_1',)),evidence_gap='Still unread'))
    result=session.plan(context,previous=previous,verified_results=(receipt,),gaps=('UNKNOWN',))
    assert result.status=='PLANNED',result.errors
    assert [s.id for s in result.plan.ready_steps]==['independent']


def test_replan_request_reserves_retained_ids_in_schema_and_context():
    context,fake,session,previous,receipt=replan_fixture()
    result=session.plan(context,previous=previous,verified_results=(receipt,),gaps=('UNKNOWN',))
    assert result.status=='PLANNED'
    req=fake.requests[-1];payload=json.loads(req.user_input.split('INPUT_JSON\n',1)[1])
    assert payload['operation_capabilities']['reserved_step_ids']==[receipt.step_id]
    assert receipt.step_id in req.available_tools[0].input_schema['properties']['steps']['items']['properties']['id']['description']
