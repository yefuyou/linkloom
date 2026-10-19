from copy import deepcopy
from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_question_planner import (
    InvestigationPlanner, PlanningLimits, VerifiedStepResult,
)
from linkloom.tools.contracts import ToolCall
from tests.task_semantic_support import selected_context


class FakeModel:
    def __init__(self, arguments):
        self.arguments = arguments
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(action=ModelAction.tool(ToolCall(
            'planning-output', request.available_tools[0].tool_id, deepcopy(self.arguments))),
            usage=ModelUsage(input_tokens=90, output_tokens=60, total_tokens=150))


def step(identity='s1', kind='inspect', handles=('t_old',), **changes):
    return dict(id=identity, kind=kind, question='需要核对哪些原始任务信息？',
                handles=list(handles), source_handles=[], depends_on=[], predicate='',
                field='owner' if kind == 'inspect' else '', target_kind='', missing_entities=[],
                **changes)


def arguments(*steps):
    return dict(steps=list(steps), uncertainties=[], refusal_reasons=[])


def planner(fake, **kwargs):
    return InvestigationPlanner(fake, model_id='fake-planner', guard_id='contract',
        request_guard=lambda request: ProviderRequestAuthorization.approve(request,
            guard_id='contract', reservation_id='offline-only', estimated_cost_upper_bound_usd=0),
        **kwargs)


def snapshot():
    context = selected_context()
    # Public legacy fixture only supplies typed source contracts, never expected plans.
    return replace(context, question='请查清谁负责这项交付。', queries=(),
                   endpoints=tuple(('t_old' if i == 0 else 't_new', ref)
                                   for i, (_, ref) in enumerate(context.endpoints)))


def test_single_model_turn_binds_host_refs_without_claim_authority():
    fake = FakeModel(arguments(step()))
    result = planner(fake).plan(snapshot())
    assert result.status == 'PLANNED'
    assert len(fake.requests) == 1
    assert result.plan.goal == snapshot().question
    assert result.plan.steps[0].endpoints == (dict(snapshot().endpoints)['t_old'],)
    assert result.plan.to_dict()['publishable'] is False
    assert fake.requests[0].generation_options.require_tool_call


@pytest.mark.parametrize('patch', [dict(handles=['invented']), dict(approved=True),
                                  dict(database_permission='write')])
def test_forged_handles_and_authority_are_rejected(patch):
    row = step()
    row.update(patch)
    result = planner(FakeModel(arguments(row))).plan(snapshot())
    assert result.status == 'OUTPUT_INVALID' and result.plan is None


def test_missing_entities_can_be_discovered_with_no_endpoints():
    context = replace(snapshot(), artifacts=(), observations=(), endpoints=(), allowed_spans=(),
                      authorized_artifact_versions=frozenset(), inventory_complete=False)
    row = step(kind='discover', handles=())
    row.update(target_kind='change', missing_entities=['用户提及的供应商变更'],
               evidence_gap='用户要求的变更尚无已授权端点。')
    result = planner(FakeModel(arguments(row))).plan(context)
    assert result.status == 'PLANNED'
    assert result.plan.steps[0].endpoints == ()
    assert result.plan.needs_more_investigation


def test_synonymous_owner_questions_have_one_execution_signature():
    first = step()
    second = step('s2')
    second['question'] = '交付工作的责任人是哪位？'
    result = planner(FakeModel(arguments(first, second))).plan(snapshot())
    assert result.status == 'OUTPUT_INVALID'
    assert 'DUPLICATE_WORK' in result.errors


def test_context_and_input_budget_fail_before_provider_call():
    for context, limits in ((replace(snapshot(), workspace_id='another'), PlanningLimits()),
                            (snapshot(), PlanningLimits(max_input_tokens=1))):
        fake = FakeModel(arguments(step()))
        result = planner(fake, limits=limits).plan(context)
        assert result.status == 'BLOCKED' and not fake.requests


def relation(identity, predicate, depends=(), handles=('t_new', 't_old')):
    row = step(identity, 'relation', handles)
    row.update(predicate=predicate, depends_on=list(depends))
    return row


def revision_steps():
    compare = step('dates', 'compare_dates', ('t_new', 't_old'))
    compare['depends_on'] = ['identity', 'revision']
    return [relation('identity', 'SAME_TASK'),
            relation('revision', 'REVISES_PLAN', ('identity',)), compare]


def test_identity_revision_date_dependencies_convert_to_semantic_queries():
    fake = FakeModel(arguments(*revision_steps()))
    result = planner(fake).plan(snapshot())
    assert result.status == 'PLANNED'
    assert [q.predicate for q in result.plan.semantic_queries] == ['SAME_TASK']
    assert [s.id for s in result.plan.ready_steps] == ['identity']


@pytest.mark.parametrize('rows', [
    [relation('revision', 'REVISES_PLAN')],
    [step('dates', 'compare_dates', ('t_new', 't_old'))],
    [relation('identity', 'SAME_TASK', ('future',))],
])
def test_missing_premises_and_forward_dependencies_fail(rows):
    assert planner(FakeModel(arguments(*rows))).plan(snapshot()).status == 'OUTPUT_INVALID'


def test_relation_and_subgoal_and_fanout_budgets():
    for limits in (PlanningLimits(max_subgoals=2), PlanningLimits(max_relation_queries=1),
                   PlanningLimits(max_fanout=1), PlanningLimits(max_relation_hops=1),
                   PlanningLimits(max_dependency_depth=1)):
        assert planner(FakeModel(arguments(*revision_steps())), limits=limits).plan(snapshot()).status == 'OUTPUT_INVALID'


def test_total_call_reservation_includes_invalid_outputs_without_retry():
    fake = FakeModel(dict(invalid=True))
    session = planner(fake, limits=PlanningLimits(max_provider_calls=1))
    assert session.plan(snapshot()).status == 'OUTPUT_INVALID'
    assert session.plan(snapshot()).status == 'BLOCKED'
    assert len(fake.requests) == 1


def test_replan_preserves_goal_completed_step_and_exact_host_receipt():
    fake = FakeModel(arguments(relation('identity', 'SAME_TASK')))
    session = planner(fake, result_verifier=lambda receipt, context: receipt.receipt_id == 'reviewed-identity')
    initial = session.plan(snapshot()).plan
    receipt = VerifiedStepResult(initial.plan_id, 'identity', 'reviewed-identity', 'a' * 64,
                                 'Identity checked by Host evidence validator and reviewer.', outcome='SUPPORTED')
    fake.arguments = arguments(relation('revision', 'REVISES_PLAN', ('identity',)))
    result = session.plan(snapshot(), previous=initial, verified_results=(receipt,), gaps=('Check plan replacement.',))
    assert result.status == 'PLANNED'
    assert result.plan.goal == initial.goal
    assert result.plan.verified_results == (receipt,)
    assert result.plan.steps[0] == initial.steps[0]
    assert result.plan.parent_plan_id == initial.plan_id
    assert [q.predicate for q in result.plan.semantic_queries] == ['REVISES_PLAN']
    assert 'reviewed-identity' in fake.requests[-1].user_input


def test_unverified_replan_receipt_and_goal_change_block_before_call():
    fake = FakeModel(arguments(step()))
    session = planner(fake)
    previous = session.plan(snapshot()).plan
    forged = VerifiedStepResult(previous.plan_id, 's1', 'forged', 'b' * 64, 'unverified')
    assert session.plan(snapshot(), previous=previous, verified_results=(forged,), gaps=('gap',)).status == 'BLOCKED'
    assert session.plan(replace(snapshot(), question='Change the original objective.')).status == 'BLOCKED'
    assert len(fake.requests) == 1


def test_all_new_public_inputs_with_explicit_fake_outputs_are_contract_valid():
    from tests.question_planning_support import public_inputs, planning_context
    from tests.question_planning_examples import scripted_outputs
    questions = public_inputs()['questions']
    assert len(questions) == len(scripted_outputs()) == 12
    for ordinal, (question, output) in enumerate(zip(questions, scripted_outputs())):
        context, discovery = planning_context(question)
        fake = FakeModel(output)
        result = planner(fake).plan(context, discovery=discovery)
        if ordinal == 11:
            # Preserve the historical authored output. M3-K now requires a
            # goal-related gap rather than silently upgrading old evidence.
            assert result.errors == ('EVIDENCE_GAP_REQUIRED',)
            continue
        assert result.status in {'PLANNED', 'REFUSED'}, result.errors
        prompt = fake.requests[0].user_input
        assert all(secret not in prompt for secret in ('case_id', 'gold/', 'fake_output', 'required_goals'))
        assert result.plan.goal == question['question']


def test_candidate_absence_does_not_limit_relation_endpoints():
    from tests.question_planning_support import public_inputs, planning_context
    context, discovery = planning_context(public_inputs()['questions'][2])
    output = arguments(relation('impact', 'AFFECTED_BY', handles=('old', 'change')))
    result = planner(FakeModel(output)).plan(context, discovery=discovery)
    assert result.status == 'PLANNED' and not discovery.candidates


def test_three_relation_hops_remain_questions_and_budgeted():
    from tests.question_planning_support import public_inputs, planning_context
    from tests.question_planning_examples import scripted_outputs
    context, _ = planning_context(public_inputs()['questions'][8])
    assert planner(FakeModel(scripted_outputs()[8])).plan(context).status == 'PLANNED'
    limits = PlanningLimits(max_relation_hops=2)
    assert planner(FakeModel(scripted_outputs()[8]), limits=limits).plan(context).status == 'OUTPUT_INVALID'


def test_wrong_workspace_same_business_number_is_rejected_as_input():
    from tests.question_planning_support import public_inputs, planning_context
    question = public_inputs()['questions'][7]
    east, _ = planning_context(question)
    west, _ = planning_context({**question, 'workspace_id': 'lab-west'})
    mixed = replace(east, endpoints=(*east.endpoints, ('foreign_Q27', dict(west.endpoints)['old'])))
    fake = FakeModel(arguments(step(handles=('old',))))
    assert planner(fake).plan(mixed).status == 'BLOCKED' and not fake.requests


@pytest.mark.parametrize('change', ['stale', 'revoked', 'unauthorized', 'truncated'])
def test_source_boundary_checks_happen_before_model(change):
    context = snapshot()
    if change == 'stale':
        span = context.allowed_spans[0][1]
        context.source_registry.register_evidence(context.workspace_id, span.evidence_ref,
                                                  content_hash='f' * 64, source_version='new-version')
    elif change == 'revoked':
        context = replace(context, revoked_observation_ids=frozenset([context.observations[0].observation_id]))
    elif change == 'unauthorized':
        context = replace(context, authorized_artifact_versions=frozenset())
    else:
        context = replace(context, truncated=True)
    fake = FakeModel(arguments(step()))
    assert planner(fake).plan(context).status == 'BLOCKED' and not fake.requests


def test_source_change_during_provider_call_does_not_publish_plan():
    context = snapshot()
    class ChangingFake(FakeModel):
        def complete(self, request):
            response = super().complete(request)
            span = context.allowed_spans[0][1]
            context.source_registry.register_evidence(context.workspace_id, span.evidence_ref,
                                                      content_hash='c' * 64, source_version='changed')
            return response
    fake = ChangingFake(arguments(step()))
    result = planner(fake).plan(context)
    assert result.status == 'BLOCKED' and result.provider_calls == 1
    assert not result.plan and len(fake.requests) == 1


def test_exact_request_guard_rejects_wrong_hash_and_wrong_guard():
    for guard in (lambda request: ProviderRequestAuthorization('contract', '0' * 64, 'fake', 0),
                  lambda request: ProviderRequestAuthorization.approve(request, guard_id='other',
                      reservation_id='fake', estimated_cost_upper_bound_usd=0)):
        fake = FakeModel(arguments(step()))
        session = InvestigationPlanner(fake, model_id='fake-planner', request_guard=guard, guard_id='contract')
        assert session.plan(snapshot()).status == 'BLOCKED' and not fake.requests


def test_single_final_text_or_wrong_function_never_becomes_a_plan():
    class WrongEmitter(FakeModel):
        def complete(self, request):
            self.requests.append(request)
            return ModelResponse(action=ModelAction.tool(ToolCall('bad', 'write_database', {})))
    result = planner(WrongEmitter({})).plan(snapshot())
    assert result.status == 'OUTPUT_INVALID' and result.plan is None
    assert result.untrusted_output['tool_calls'][0]['tool_id'] == 'write_database'


@pytest.mark.parametrize('limits', [PlanningLimits(max_total_input_tokens=1),
                                  PlanningLimits(max_total_output_tokens=1)])
def test_total_token_reservation_blocks_before_call(limits):
    fake = FakeModel(arguments(step()))
    assert planner(fake, limits=limits).plan(snapshot()).status == 'BLOCKED'
    assert not fake.requests


def test_replan_rejects_duplicate_completed_work_and_rechecks_old_receipts():
    fake = FakeModel(arguments(step()))
    accepted = True
    session = planner(fake, result_verifier=lambda receipt, context: accepted)
    previous = session.plan(snapshot()).plan
    receipt = VerifiedStepResult(previous.plan_id, 's1', 'host-receipt', 'a' * 64, 'checked')
    fake.arguments = arguments(step('same_work'))
    result = session.plan(snapshot(), previous=previous, verified_results=(receipt,), gaps=('gap',))
    assert result.status == 'OUTPUT_INVALID' and 'DUPLICATE_WORK' in result.errors
    accepted = False
    result = session.plan(snapshot(), previous=previous, verified_results=(receipt,), gaps=('gap',))
    assert result.status == 'BLOCKED' and result.errors == ('RESULT_NOT_CURRENTLY_VERIFIED',)


def test_multiple_replans_keep_all_earlier_verified_receipts():
    fake = FakeModel(arguments(relation('identity', 'SAME_TASK')))
    session = planner(fake, result_verifier=lambda receipt, context: True)
    first = session.plan(snapshot()).plan
    identity = VerifiedStepResult(first.plan_id, 'identity', 'i', 'a' * 64, 'checked identity', outcome='SUPPORTED')
    fake.arguments = arguments(relation('revision', 'REVISES_PLAN', ('identity',)))
    second = session.plan(snapshot(), previous=first, verified_results=(identity,), gaps=('revision',)).plan
    revision = VerifiedStepResult(second.plan_id, 'revision', 'r', 'b' * 64, 'checked revision', outcome='SUPPORTED')
    fake.arguments = arguments(revision_steps()[2])
    third = session.plan(snapshot(), previous=second, verified_results=(revision,), gaps=('dates',))
    assert third.status == 'PLANNED'
    assert third.plan.verified_results == (identity, revision)
    assert third.plan.goal == first.goal and len(fake.requests) == 3


def test_relation_types_and_symmetric_duplicate_are_checked():
    from tests.question_planning_support import public_inputs, planning_context
    context, _ = planning_context(public_inputs()['questions'][2])
    for rows in ([relation('bad', 'SAME_TASK', handles=('old', 'delivery'))],
                 [relation('a', 'SAME_TASK', handles=('new', 'old')),
                  relation('b', 'SAME_TASK', handles=('old', 'new'))]):
        assert planner(FakeModel(arguments(*rows))).plan(context).status == 'OUTPUT_INVALID'


def test_no_entity_schema_has_no_empty_enumerations():
    from tests.question_planning_support import public_inputs, planning_context
    context, _ = planning_context(public_inputs()['questions'][9])
    row = step(kind='clarify', handles=())
    row['missing_entities'] = ['业务对象']
    fake = FakeModel(arguments(row))
    assert planner(fake).plan(context).status == 'PLANNED'
    import json
    assert '"enum": []' not in json.dumps(fake.requests[0].available_tools[0].input_schema)


def test_result_revalidation_detects_revocation_during_replan_call():
    state = {'verified': True}
    class RevokingFake(FakeModel):
        revoke = False
        def complete(self, request):
            response = super().complete(request)
            if self.revoke:
                state['verified'] = False
            return response
    fake = RevokingFake(arguments(relation('identity', 'SAME_TASK')))
    session = planner(fake, result_verifier=lambda receipt, context: state['verified'])
    first = session.plan(snapshot()).plan
    receipt = VerifiedStepResult(first.plan_id, 'identity', 'reviewed', 'a' * 64, 'identity')
    fake.arguments = arguments(relation('revision', 'REVISES_PLAN', ('identity',)))
    fake.revoke = True
    result = session.plan(snapshot(), previous=first, verified_results=(receipt,), gaps=('revision',))
    assert result.status == 'BLOCKED' and result.errors == ('RESULT_CHANGED_DURING_CALL',)
    assert result.provider_calls == 1 and result.plan is None


def test_provider_exception_is_charged_and_not_retried():
    class BrokenFake(FakeModel):
        def complete(self, request):
            self.requests.append(request)
            raise RuntimeError('offline transport failure')
    fake = BrokenFake({})
    result = planner(fake).plan(snapshot())
    assert result.status == 'PROVIDER_FAILED' and result.provider_calls == 1
    assert result.budget['calls_reserved'] == len(fake.requests) == 1


def test_provider_output_over_reservation_stops_session():
    class OverBudgetFake(FakeModel):
        def complete(self, request):
            response = super().complete(request)
            return replace(response, usage=ModelUsage(output_tokens=5000))
    fake = OverBudgetFake(arguments(step()))
    session = planner(fake)
    assert session.plan(snapshot()).errors == ('PROVIDER_USAGE_EXCEEDED_RESERVATION',)
    assert session.plan(snapshot()).errors == ('SESSION_BUDGET_EXHAUSTED',)
    assert len(fake.requests) == 1


def test_plan_carries_limits_and_rejected_output_for_diagnostics():
    result = planner(FakeModel(arguments(step()))).plan(snapshot())
    assert result.plan.to_dict()['limits']['max_relation_hops'] == 3
    invalid = planner(FakeModel({'approved': True})).plan(snapshot())
    assert invalid.untrusted_output['tool_calls'][0]['arguments'] == {'approved': True}


def test_snapshot_output_token_budget_also_limits_request_and_reservation():
    context = snapshot()
    context = replace(context, limits=replace(context.limits, max_output_tokens=128))
    fake = FakeModel(arguments(step()))
    result = planner(fake).plan(context)
    assert fake.requests[0].generation_options.max_output_tokens == 128
    assert result.budget['output_tokens_reserved'] == 128
    assert result.plan.limits.max_output_tokens == 128


def test_guard_cannot_mutate_schema_after_authorizing_request():
    fake = FakeModel(arguments(step()))
    def mutating_guard(request):
        permit = ProviderRequestAuthorization.approve(request, guard_id='contract',
                    reservation_id='before-mutation', estimated_cost_upper_bound_usd=0)
        request.available_tools[0].input_schema['description'] = 'unauthorized mutation'
        return permit
    session = InvestigationPlanner(fake, model_id='fake-planner', guard_id='contract', request_guard=mutating_guard)
    result = session.plan(snapshot())
    assert result.status == 'BLOCKED' and not fake.requests


def test_run_identity_distinguishes_sessions_and_survives_replan():
    first_fake = FakeModel(arguments(step()))
    second_fake = FakeModel(arguments(step()))
    first = planner(first_fake)
    second = planner(second_fake)
    initial = first.plan(snapshot()).plan
    second.plan(replace(snapshot(), question='核对该任务的负责人身份。'))
    assert first_fake.requests[0].run_id != second_fake.requests[0].run_id
    first_fake.arguments = arguments(relation('identity', 'SAME_TASK'))
    assert first.plan(snapshot(), previous=initial, gaps=('核对任务身份',)).status == 'PLANNED'
    assert first_fake.requests[1].run_id == first_fake.requests[0].run_id
    assert first_fake.requests[1].turn_id != first_fake.requests[0].turn_id


def test_provider_error_with_over_budget_usage_exhausts_session():
    from linkloom.agents.model_adapter import ModelProviderError
    class OverBudgetError(FakeModel):
        def complete(self, request):
            self.requests.append(request)
            return ModelResponse(error=ModelProviderError('MODEL_TIMEOUT', 'timeout', 'Timed out.'),
                                 usage=ModelUsage(output_tokens=5000))
    fake = OverBudgetError({})
    session = planner(fake)
    result = session.plan(snapshot())
    assert result.errors == ('PROVIDER_USAGE_EXCEEDED_RESERVATION',)
    assert session.plan(snapshot()).errors == ('SESSION_BUDGET_EXHAUSTED',)
    assert len(fake.requests) == 1
