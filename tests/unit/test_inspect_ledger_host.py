"""Real Runtime inspection -> P4 dependency, with checkpoint round trips."""
import json
from dataclasses import replace

import pytest

from linkloom.runtime.models import ToolExecutionRecord
from linkloom.tools.contracts import ToolCall
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.task_relations import TaskRelationTools
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer
from tests.unit.test_task_investigation_p4_steps import _fixture, _plan, _step


def setup_chain(root):
    case, _, snapshot, identity, _ = _fixture(root / 'p4.sqlite')
    ws = case['workspace_id']
    inspect = replace(_step('inspect_owner', 'inspect', '', (identity.subject,), ws), field='owner')
    relation = _step('identity', 'relation', 'SAME_TASK', (identity.subject, identity.object), ws,
                     ('inspect_owner',))
    plan = _plan(ws, (inspect, relation))
    tools = TaskRelationTools(ws, lambda: snapshot, relation_path=root / 'p4.sqlite', inspection_plan=plan,
        inspection_snapshot_hash=plan.snapshot_hash)
    ledger = ToolExecutionLedger()
    checkpoint = root / 'ledger.json'

    def persist(current):
        checkpoint.write_text(json.dumps([r.to_dict() for r in current.to_list()]), encoding='utf8')

    runtime = create_retrieval_tool_runtime(lambda *_: [], lambda *_: {}, ledger=ledger,
        checkpoint_callback=persist, task_relation_tools=tools)
    policy = ToolPolicyEnforcer(ToolCallPolicy('p4-readonly-tools-v1', 'retrieval_agent',
        ['inspect_task_context', 'query_task_relations'], [], 8, 'deny', 'deny', 'deny'))
    def call(tool, sequence, run='run_p2b'):
        return ToolCall('p2b_' + str(sequence), tool, {'workspace_id': ws},
            run_id=run, task_id='task_p2b', agent_id='retrieval_agent', sequence=sequence)
    return tools, snapshot, plan, runtime, policy, ledger, checkpoint, call


def test_completed_inspect_unblocks_only_matching_current_p4_relation(tmp_path):
    tools, _, _, runtime, policy, ledger, checkpoint, call = setup_chain(tmp_path)
    inspected = runtime.execute(call('inspect_task_context', 1), policy)
    assert inspected.status == 'ok'
    persisted = ToolExecutionLedger(json.loads(checkpoint.read_text(encoding='utf8')))
    assert persisted.get('p2b_1').status == 'completed'
    assert tools.inspection_receipts_from_record(persisted.get('p2b_1'))
    queried = runtime.execute(call('query_task_relations', 2), policy)
    assert queried.status == 'ok'
    assert len(json.loads(checkpoint.read_text(encoding='utf8'))) == 2
    row = queried.value['investigation_steps'][0]
    assert row['dependency_outcomes'] == {'inspect_owner': 'COMPLETED'}
    assert row['status'] == 'SUPPORTED'
    assert row['relation_reads'][0]['current_supported']
    assert ledger.get('p2b_2').status == 'completed'


def assert_blocked(result):
    assert result.status == 'ok'
    row = result.value['investigation_steps'][0]
    assert row['dependency_outcomes'] == {'inspect_owner': 'BLOCKED'}
    assert row['status'] == 'BLOCKED'


def test_unexecuted_inspection_and_later_completion_do_not_rewrite_earlier_query(tmp_path):
    tools, _, _, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    queried = runtime.execute(call('query_task_relations', 1), policy)
    assert_blocked(queried)
    assert runtime.execute(call('inspect_task_context', 2), policy).status == 'ok'
    assert tools.query_task_relations_from_record(ledger.get('p2b_1'), ledger.to_list(),
        run_id='run_p2b', task_id='task_p2b') == queried.value


def test_real_failed_inspection_is_durable_and_cannot_satisfy_dependency(tmp_path):
    tools, _, _, runtime, policy, ledger, checkpoint, call = setup_chain(tmp_path)
    tools.execution_blocked_reason = 'CANCELLED'
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'error'
    tools.execution_blocked_reason = None
    assert json.loads(checkpoint.read_text(encoding='utf8'))[0]['status'] == 'failed'
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy))
    assert ledger.get('p2b_1').status == 'failed'


def test_uncertain_inspection_reloads_as_pending_and_cannot_unblock(tmp_path):
    _, _, _, runtime, policy, _, checkpoint, call = setup_chain(tmp_path)
    persist = runtime.checkpoint_callback
    def fail_terminal(current):
        if current.get('p2b_1').status == 'completed':
            raise OSError('synthetic terminal checkpoint outage')
        persist(current)
    result = runtime.execute(call('inspect_task_context', 1), policy, checkpoint_callback=fail_terminal)
    assert result.error.code == 'TOOL_TERMINAL_CHECKPOINT_FAILED'
    restored = ToolExecutionLedger(json.loads(checkpoint.read_text(encoding='utf8')))
    assert restored.get('p2b_1').status == 'pending'
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy, ledger=restored))


@pytest.mark.parametrize('foreign', ['run_id', 'task_id', 'agent_id', 'workspace_id', 'plan', 'sequence'])
def test_completed_foreign_inspection_cannot_satisfy_current_dependency(tmp_path, foreign):
    tools, _, plan, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    data = ledger.get('p2b_1').to_dict()
    if foreign == 'workspace_id':
        data['arguments']['workspace_id'] = 'foreign_workspace'
    elif foreign == 'plan':
        tools.inspection_plan = replace(plan, goal='A different plan')
    elif foreign == 'sequence':
        data['sequence'] = 3
    else:
        data[foreign] = 'foreign_identity'
    restored = ToolExecutionLedger([data])
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy, ledger=restored))


@pytest.mark.parametrize('tamper', ['call_id', 'tool_id', 'result_hash', 'receipt_id',
    'duplicate_receipt', 'duplicate_step', 'arguments', 'source_version'])
def test_completed_record_tampering_never_becomes_execution_proof(tmp_path, tamper):
    tools, _, plan, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    data = ledger.get('p2b_1').to_dict()
    output = data['result']['value']
    receipt = output['investigation_receipts'][0]
    if tamper in {'call_id', 'tool_id'}:
        data['result'][tamper] = 'fake_identity'
    elif tamper == 'result_hash':
        receipt['result_sha256'] = '0' * 64
    elif tamper == 'receipt_id':
        receipt['receipt_id'] = 'fake_receipt'
    elif tamper == 'duplicate_receipt':
        output['investigation_receipts'].append(dict(receipt))
    elif tamper == 'duplicate_step':
        tools.inspection_plan = replace(plan, steps=plan.steps + (plan.steps[0],))
    elif tamper == 'arguments':
        data['arguments']['anchor_ids'] = ['uninspected_observation']
    else:
        output['observations'][0]['ref']['artifact_version_id'] = 'fake_version'
    restored = ToolExecutionLedger([data])
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy, ledger=restored))


@pytest.mark.parametrize('change', ['revoke', 'version'])
def test_source_rechecked_between_completed_inspection_and_query(tmp_path, change):
    _, snapshot, plan, runtime, policy, _, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    observation = next(o for o in snapshot.observations if o.observation_id == plan.steps[0].endpoints[0].observation_id)
    span = observation.provenance
    if change == 'revoke':
        snapshot.source_registry.unregister_evidence(snapshot.workspace_id, span.evidence_ref)
    else:
        snapshot.source_registry.register_evidence(snapshot.workspace_id, span.evidence_ref,
            content_hash=span.content_hash, source_version='updated_version')
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy))


def test_reviewer_replays_persisted_ledger_and_rejects_self_consistent_wrong_view(tmp_path):
    from linkloom.agents.task_relation_result import project_task_relation_result, review_task_relation_result
    tools, _, _, runtime, policy, _, checkpoint, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    assert runtime.execute(call('query_task_relations', 2), policy).status == 'ok'
    records = ToolExecutionLedger(json.loads(checkpoint.read_text(encoding='utf8'))).to_list()
    def payload(rows):
        return project_task_relation_result(json.dumps(dict(context_call_id='p2b_1', relations_call_id='p2b_2')),
            rows, [r.result for r in rows], tools.workspace_id)
    checked = review_task_relation_result(payload(records), tools, records=records, run_id='run_p2b', task_id='task_p2b')
    assert checked['status'] == 'pass'
    assert 'HOST_LEDGER_INSPECTION_REPLAY' in checked['checks']
    # Reproduce the old internally consistent but wrong query receipt. Reviewer
    # must compare it with Host-verified execution, rather than self-consistency.
    bad_query = records[1].to_dict()
    bad_query['result']['value'] = tools.query_task_relations(tools.workspace_id)
    bad_records = [records[0], ToolExecutionRecord.from_dict(bad_query)]
    assert review_task_relation_result(payload(bad_records), tools,
        records=bad_records, run_id='run_p2b', task_id='task_p2b')['status'] == 'fail'
    assert review_task_relation_result(payload(records), tools,
        records=records + [records[0]], run_id='run_p2b', task_id='task_p2b')['status'] == 'fail'


def test_tool_schema_never_accepts_model_supplied_execution_projection(tmp_path):
    _, _, _, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    query = call('query_task_relations', 1)
    query = replace(query, arguments={**query.arguments, '_inspection_results': [{'outcome': 'COMPLETED'}]})
    result = runtime.execute(query, policy)
    assert result.error.code == 'TOOL_INVALID_ARGUMENTS'
    assert ledger.to_list() == []


def test_plan_only_completion_cannot_replace_the_current_run_ledger(tmp_path):
    tools, _, plan, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    receipts = tools.inspection_receipts_from_record(ledger.get('p2b_1'))
    # Use a genuine receipt as the hostile planner's input, never invent a
    # successful VerifiedStepResult to make this contract pass.
    tools.inspection_plan = replace(plan, verified_results=receipts)
    assert_blocked(runtime.execute(call('query_task_relations', 2, run='another_run'), policy,
        ledger=ToolExecutionLedger()))


def test_uncertain_in_memory_inspection_cannot_be_laundered_by_later_checkpoint(tmp_path):
    _, _, _, runtime, policy, ledger, checkpoint, call = setup_chain(tmp_path)
    persist = runtime.checkpoint_callback
    def fail_terminal(current):
        if current.get('p2b_1').status == 'completed':
            raise OSError('synthetic terminal checkpoint outage')
        persist(current)
    result = runtime.execute(call('inspect_task_context', 1), policy, checkpoint_callback=fail_terminal)
    assert result.error.code == 'TOOL_TERMINAL_CHECKPOINT_FAILED'
    assert_blocked(runtime.execute(call('query_task_relations', 2), policy))
    restored = ToolExecutionLedger(json.loads(checkpoint.read_text(encoding='utf8')))
    assert restored.get('p2b_1').error['code'] == 'TOOL_TERMINAL_CHECKPOINT_FAILED'
    assert ledger.get('p2b_1').error == restored.get('p2b_1').error


def test_reviewer_cannot_derive_task_authority_from_self_consistent_ledger(tmp_path):
    from linkloom.agents.task_relation_result import project_task_relation_result, review_task_relation_result
    tools, _, _, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    assert runtime.execute(call('query_task_relations', 2), policy).status == 'ok'
    records = [replace(r, task_id='foreign_task') for r in ledger.to_list()]
    payload = project_task_relation_result(json.dumps(dict(context_call_id='p2b_1', relations_call_id='p2b_2')),
        records, [r.result for r in records], tools.workspace_id)
    assert review_task_relation_result(payload, tools, records=records, run_id='run_p2b')['status'] == 'fail'
    assert review_task_relation_result(payload, tools, records=records, run_id='run_p2b',
        task_id='task_p2b')['status'] == 'fail'


@pytest.mark.parametrize('metadata', ['workspace_id', 'snapshot_hash'])
def test_self_consistent_foreign_plan_metadata_is_rejected_before_execution(tmp_path, metadata):
    tools, _, plan, runtime, policy, _, _, call = setup_chain(tmp_path)
    tools.inspection_plan = replace(plan, **{metadata: 'foreign-workspace' if metadata == 'workspace_id' else '0' * 64})
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'error'
    assert runtime.execute(call('query_task_relations', 2), policy).status == 'error'


def test_uncertain_query_cannot_pass_host_or_context_lifecycle_replay(tmp_path):
    from types import SimpleNamespace
    from linkloom.runtime.context_engineering import RuntimeWorkingContext
    from linkloom.runtime.errors import ValidationError
    tools, _, _, runtime, policy, ledger, _, call = setup_chain(tmp_path)
    assert runtime.execute(call('inspect_task_context', 1), policy).status == 'ok'
    def fail_terminal(current):
        if current.get('p2b_2').status == 'completed':
            raise OSError('synthetic query terminal checkpoint outage')
    assert runtime.execute(call('query_task_relations', 2), policy,
        checkpoint_callback=fail_terminal).error.code == 'TOOL_TERMINAL_CHECKPOINT_FAILED'
    with pytest.raises(ValueError):
        tools.query_task_relations_from_record(ledger.get('p2b_2'), ledger.to_list(),
            run_id='run_p2b', task_id='task_p2b')
    context = object.__new__(RuntimeWorkingContext)
    context.tools = tools
    with pytest.raises(ValidationError):
        context._check_lifecycle(SimpleNamespace(tool_ledger=[ledger.get('p2b_2')],
            run_id='run_p2b', model_executions=[]))
