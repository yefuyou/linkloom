import json

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.tools.contracts import ToolCall
from tests.m3i_support import product_case, finish


def owner_plan(stages, *, time_scope=None, mixed=False):
    def complete(request):
        stages[1].requests.append(request.to_dict())
        payload = json.loads(request.user_input.split('INPUT_JSON\n',1)[1])
        handle = payload['operation_capabilities']['inspection_handles'][0]
        row = dict(id='owner',kind='inspect',question='检查责任人原文。', handles=[handle],
            source_handles=[],depends_on=[],predicate='',field='owner',target_kind='',missing_entities=[])
        if time_scope:
            row['time_scope'] = time_scope
        steps = [row]
        if mixed:
            steps.append(dict(id='identity',kind='relation',question='核查两条观察是否同一任务。',
                handles=[e['handle'] for e in payload['endpoints'][:2]],source_handles=[],
                depends_on=[],predicate='SAME_TASK',field='',target_kind='',missing_entities=[]))
        return ModelResponse(action=ModelAction.tool(ToolCall('owner-plan','emit_investigation_plan',
            dict(steps=steps,uncertainties=[],refusal_reasons=[]))))
    stages[1].complete = complete


def test_owner_inspection_runs_through_existing_runtime_with_source_bound_receipt(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    owner_plan(stages)
    result, _ = ask.ask(dict(query='请调查会议任务的负责人是谁？',workspace_id=workspace))
    result = finish(ask,workspace,result)
    assert result['reviewer']['accepted'], result
    assert [len(s.requests) for s in stages] == [2,1,0]
    assert result['preparation']['operation_capabilities']['inspect']['execution'] == 'EXISTING_RUNTIME_TOOL'
    assert not any('INSPECTION_RETURNS' in gap for gap in result['preparation']['gaps'])
    job = service._jobs[result['agent_run_id']]
    backend = job.backend
    state = backend.engine.checkpointer.get_latest(backend._jobs[result['agent_run_id']].thread_id)
    record = next(r for r in state.tool_ledger if r.tool_id == 'inspect_task_context')
    value = record.result['value']
    assert len(value['observations']) == 1
    receipt, = value['investigation_receipts']
    assert receipt['step_id'] == 'owner' and receipt['outcome'] == 'COMPLETED'
    assert receipt['result_sha256']
    assert value['observations'][0]['observation']['relation_status'] == 'UNCONFIRMED'
    assert result['preparation']['operation_capabilities']['compare_dates']['execution'] == 'TRUSTED_RELATIONS_REQUIRED'
    assert not result['conclusions']
    tools = job.task_preparation.tools
    verified, = tools.inspection_receipts_from_record(record)
    assert verified.step_id == 'owner' and verified.outcome == 'COMPLETED'
    from dataclasses import replace
    from copy import deepcopy
    import pytest
    changed = deepcopy(record.result)
    changed['value']['investigation_receipts'][0]['outcome'] = 'SUPPORTED'
    with pytest.raises(ValueError,match='UNVERIFIED_INSPECTION_RECEIPT'):
        tools.inspection_receipts_from_record(replace(record,result=changed))
    source = app.snapshot_investigation_sources(workspace)[1][0]
    app.repository.delete_source(workspace,source['source_id'])
    with pytest.raises(ValueError,match='SOURCE_SNAPSHOT_CHANGED'):
        tools.inspection_receipts_from_record(record)


def test_time_scoped_inspect_is_explicit_gap_not_completed_receipt(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    owner_plan(stages,time_scope={'start':'2029-06-01','end':'2029-06-02'})
    result, _ = ask.ask(dict(query='请调查会议任务的负责人是谁？',workspace_id=workspace))
    result = finish(ask,workspace,result)
    assert 'TIME_SCOPED_INSPECTION_UNSUPPORTED:owner' in result['preparation']['gaps']
    assert not result['preparation']['ready_tool_operations']
    assert result['status'] == 'BLOCKED'
    assert not service._jobs and not stages[2].requests


def test_time_scoped_relation_never_falls_back_to_unscoped_runtime(tmp_path):
    app,service,ask,stages,workspace = product_case(tmp_path)
    original = stages[1].complete
    def scoped(request):
        response = original(request)
        response.action.tool_call.arguments['steps'][0]['time_scope'] = {'start':'2029-06-01','end':'2029-06-02'}
        return response
    stages[1].complete = scoped
    result,_ = ask.ask(dict(query='请调查会议任务的延续依据。',workspace_id=workspace))
    assert result['status'] == 'BLOCKED',result
    assert not service._jobs and not stages[2].requests


def test_extra_inspection_source_registry_revocation_invalidates_durable_receipt(tmp_path):
    import pytest
    app,service,ask,stages,workspace = product_case(tmp_path)
    owner_plan(stages)
    result,_ = ask.ask(dict(query='请调查会议任务的负责人是谁？',workspace_id=workspace))
    result = finish(ask,workspace,result)
    job = service._jobs[result['agent_run_id']]
    state = job.backend.engine.checkpointer.get_latest(job.backend._jobs[result['agent_run_id']].thread_id)
    record = next(r for r in state.tool_ledger if r.tool_id=='inspect_task_context')
    tools = job.task_preparation.tools
    step, = tools.planned_inspections
    own = step.endpoints[0].evidence_ref
    counter = next(s for s in step.source_bindings if s.evidence_ref != own)
    job.task_preparation.sources.registry.unregister_evidence(workspace,counter.evidence_ref)
    with pytest.raises(ValueError,match='INSPECTION_SOURCE_NOT_CURRENT'):
        tools.inspection_receipts_from_record(record)
    with pytest.raises(ValueError,match='INSPECTION_SOURCE_NOT_CURRENT'):
        tools.inspect_task_context(workspace)


def test_blocked_operation_scope_denies_both_generic_tool_executors():
    import pytest
    from linkloom.tools.task_relations import TaskRelationTools
    tools = TaskRelationTools('authorized',lambda:pytest.fail('blocked executor read a snapshot'),
        execution_blocked_reason='UNSUPPORTED_OPERATION_SCOPE')
    with pytest.raises(ValueError,match='UNSUPPORTED_OPERATION_SCOPE'):
        tools.inspect_task_context('authorized')
    with pytest.raises(ValueError,match='UNSUPPORTED_OPERATION_SCOPE'):
        tools.query_task_relations('authorized',as_of='2029-06-01')


def test_mixed_plan_inspection_returns_only_planned_targets(tmp_path):
    import pytest
    app,service,ask,stages,workspace = product_case(tmp_path)
    owner_plan(stages,mixed=True)
    result,_ = ask.ask(dict(query='请调查会议任务的负责人及延续依据。',workspace_id=workspace))
    result = finish(ask,workspace,result)
    tools = service._jobs[result['agent_run_id']].task_preparation.tools
    assert {s.kind for s in tools.inspection_plan.ready_steps} == {'inspect','relation'}
    planned, = tools.planned_inspections
    target = planned.endpoints[0].observation_id
    snapshot = tools._snapshot(workspace)
    outside = next(o.observation_id for o in snapshot.observations if o.observation_id != target)
    with pytest.raises(ValueError,match='INSPECTION_TARGET_NOT_PLANNED'):
        tools.inspect_task_context(workspace,anchor_ids=[outside])
    for anchors in (None,[target]):
        output = tools.inspect_task_context(workspace,anchor_ids=anchors)
        assert [o['ref']['observation_id'] for o in output['observations']] == [target]
        receipt, = output['investigation_receipts']
        assert receipt['step_id'] == 'owner' and receipt['outcome'] == 'COMPLETED'
