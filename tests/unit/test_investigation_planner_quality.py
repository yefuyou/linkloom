"""M3-K authored contract probes; never a measurement of model quality."""
from dataclasses import replace

import pytest

from linkloom.semantic_ingestion.task_relation_question_models import VerifiedStepResult
from linkloom.semantic_ingestion.task_relation_question_planner import INSTRUCTIONS
from tests.question_planning_support import public_inputs, planning_context
from tests.unit.test_task_relation_question_planner import FakeModel, arguments, planner, relation, step, snapshot


def context(ordinal=8):
    return planning_context(public_inputs()['questions'][ordinal])[0]


def test_planner_instructions_make_inspect_target_kind_empty():
    instructions = INSTRUCTIONS.lower()
    assert "for every inspect step, target_kind, predicate" in instructions
    assert "empty string/list" in instructions
    assert "for relation steps, handles provide the two typed endpoints" in instructions
    assert "for compare_dates, handles provide" in instructions
    assert "the two task observations" in instructions


def owner(identity='owner', **patch):
    row = step(identity, handles=('old',))
    row.update(source_handles=['s_old'], **patch)
    return row


def discover(identity='find', **patch):
    row = step(identity, 'discover', ())
    row.update(target_kind='change', missing_entities=['supplier switch'],
               evidence_gap='The goal requires a concrete switch but no change handle was admitted.')
    row.update(patch)
    return row


def test_inspection_rejection_has_exact_step_and_violations():
    row = owner(handles=['delivery'], predicate='SAME_TASK', field='unsupported')
    result = planner(FakeModel(arguments(row))).plan(context())
    assert result.errors == ('INVALID_INSPECTION',)
    issue = result.diagnostics['validation_issues'][0]
    assert issue['step_id'] == 'owner'
    assert set(issue['violations']) == {'OBSERVATION_HANDLE_REQUIRED', 'UNSUPPORTED_FIELD', 'PREDICATE_MUST_BE_EMPTY'}
    assert result.untrusted_output['tool_calls'][0]['arguments'] == arguments(row)


def test_wrong_authorized_source_cannot_inspect_another_observation():
    row = owner()
    row['source_handles'] = ['s_new']
    result = planner(FakeModel(arguments(row))).plan(context())
    assert result.errors == ('INSPECTION_SOURCE_MISMATCH',)


def test_discovery_without_explicit_goal_related_gap_is_counted_and_rejected():
    row = discover(evidence_gap='')
    result = planner(FakeModel(arguments(owner(), row))).plan(context())
    assert result.errors == ('EVIDENCE_GAP_REQUIRED',)
    assert result.diagnostics['proposed_steps'] == 2
    assert result.diagnostics['excess_steps'] == 1
    assert result.plan is None


def test_same_typed_work_paraphrase_is_duplicate_and_observable():
    first, second = owner(), owner('again', question='Who owns this delivery?')
    result = planner(FakeModel(arguments(first, second))).plan(context())
    assert result.errors == ('DUPLICATE_WORK',)
    assert result.diagnostics['excess_steps'] == 1


def test_different_source_scope_with_new_gap_does_not_false_deduplicate():
    first, second = owner(), owner('counter', evidence_gap='Check the contradictory source against the owner record.')
    second['source_handles'] = ['s_old', 's_counter']
    result = planner(FakeModel(arguments(first, second))).plan(context())
    assert result.status == 'PLANNED', result.errors
    assert len({s.execution_key for s in result.plan.steps}) == 2


def test_repeated_operation_with_new_scope_but_no_gap_is_rejected():
    second = owner('counter')
    second['source_handles'] = ['s_old', 's_counter']
    result = planner(FakeModel(arguments(owner(), second))).plan(context())
    assert result.errors == ('EVIDENCE_GAP_REQUIRED',)


def test_time_ranges_are_typed_identity_and_invalid_dates_are_rejected():
    first = owner(time_scope={'start': '2026-10-01', 'end': '2026-10-31'})
    second = owner('later', time_scope={'start': '2026-11-01', 'end': '2026-11-30'},
                   evidence_gap='The requested November window needs a separate inspection.')
    result = planner(FakeModel(arguments(first, second))).plan(context())
    assert result.status == 'PLANNED', result.errors
    assert result.plan.steps[0].execution_key != result.plan.steps[1].execution_key
    first['time_scope']['end'] = '2026-02-30'
    assert planner(FakeModel(arguments(first))).plan(context()).errors == ('INVALID_TIME_SCOPE',)


def test_different_unknown_entities_do_not_collapse_but_question_paraphrases_do():
    first, second = discover(), discover('different', missing_entities=['deployment incident'])
    result = planner(FakeModel(arguments(first, second))).plan(context(11))
    assert result.status == 'PLANNED', result.errors
    second = discover('paraphrase', question='Find the concrete vendor change.')
    assert planner(FakeModel(arguments(first, second))).plan(context(11)).errors == ('DUPLICATE_WORK',)


def test_different_dependency_premises_do_not_false_deduplicate():
    rows = [owner('owner'), relation('identity', 'SAME_TASK', handles=('new', 'old'))]
    a = relation('a', 'RELATED_TO', ('owner',), handles=('new', 'old'))
    b = relation('b', 'RELATED_TO', ('identity',), handles=('new', 'old'))
    b['evidence_gap'] = 'Recheck relation after the independent identity premise is resolved.'
    result = planner(FakeModel(arguments(*rows, a, b))).plan(context())
    assert result.status == 'PLANNED', result.errors


def test_handle_and_dependency_id_aliases_do_not_bypass_deduplication():
    ctx = context()
    ctx = replace(ctx, endpoints=(*ctx.endpoints, ('old_alias', dict(ctx.endpoints)['old'])),
                  allowed_spans=(*ctx.allowed_spans, ('source_alias', dict(ctx.allowed_spans)['s_old'])))
    second = owner('alias', handles=['old_alias'])
    second['source_handles'] = ['source_alias']
    assert planner(FakeModel(arguments(owner(), second))).plan(ctx).errors == ('DUPLICATE_WORK',)


def test_inspection_schema_capability_inventory_and_mapping_are_provider_compatible():
    from linkloom.agents.providers.gemini_api import map_tool_definition_to_gemini_function
    fake = FakeModel(arguments(owner()))
    result = planner(fake).plan(context())
    assert result.status == 'PLANNED'
    request = fake.requests[0]
    assert 'inspection_handles' in request.user_input
    assert 'Exactly one ObservationRef' in request.available_tools[0].input_schema['properties']['steps']['items']['properties']['handles']['description']
    assert map_tool_definition_to_gemini_function(request.available_tools[0])


@pytest.mark.parametrize('patch,violation', [
    ({'handles': []}, 'EXACTLY_ONE_HANDLE_REQUIRED'),
    ({'handles': ['old', 'new']}, 'EXACTLY_ONE_HANDLE_REQUIRED'),
    ({'handles': ['change']}, 'OBSERVATION_HANDLE_REQUIRED'),
    ({'field': ''}, 'UNSUPPORTED_FIELD'),
    ({'target_kind': 'task'}, 'TARGET_KIND_MUST_BE_EMPTY'),
    ({'missing_entities': ['unknown']}, 'MISSING_ENTITIES_MUST_BE_EMPTY'),
])
def test_all_inspection_constraints_remain_strict(patch, violation):
    row = owner()
    row.update(patch)
    result = planner(FakeModel(arguments(row))).plan(context())
    assert result.errors == ('INVALID_INSPECTION',)
    assert violation in result.diagnostics['validation_issues'][0]['violations']


def test_endpoint_and_source_only_versions_both_change_execution_identity():
    public = public_inputs()
    ctx, _ = planning_context(public['questions'][0], public)
    first_owner = planner(FakeModel(arguments(owner()))).plan(ctx).plan.steps[0]
    first_discover = planner(FakeModel(arguments(discover()))).plan(ctx).plan.steps[0]
    public['sources'][0]['text'] += ' 补充来源版本。'
    newer, _ = planning_context(public['questions'][0], public)
    next_owner = planner(FakeModel(arguments(owner()))).plan(newer).plan.steps[0]
    next_discover = planner(FakeModel(arguments(discover()))).plan(newer).plan.steps[0]
    assert first_owner.endpoints != next_owner.endpoints
    assert first_owner.execution_key != next_owner.execution_key
    assert first_discover.endpoints == next_discover.endpoints == ()
    assert first_discover.execution_key != next_discover.execution_key


@pytest.mark.parametrize('outcome', ['COMPLETED', 'UNKNOWN', 'REFUTED', 'CONFLICT', 'SUPPORTED'])
def test_dependency_readiness_requires_positive_host_relation_receipt(outcome):
    fake = FakeModel(arguments(relation('identity', 'SAME_TASK')))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(snapshot()).plan
    receipt = VerifiedStepResult(initial.plan_id, 'identity', 'host', 'a' * 64, 'Host evidence result', outcome)
    fake.arguments = arguments(relation('revision', 'REVISES_PLAN', ('identity',)))
    result = session.plan(snapshot(), previous=initial, verified_results=(receipt,), gaps=('Revision still unknown.',))
    assert result.status == 'PLANNED', result.errors
    assert [s.id for s in result.plan.ready_steps] == (['revision'] if outcome == 'SUPPORTED' else [])
    assert result.plan.goal == initial.goal
    assert result.plan.steps[0] == initial.steps[0]
    assert 'identity' not in [s.id for s in result.plan.ready_steps]


def test_host_cannot_mark_dependent_work_completed_with_conflicting_premise():
    fake = FakeModel(arguments(relation('identity', 'SAME_TASK'),
                               relation('revision', 'REVISES_PLAN', ('identity',))))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(snapshot()).plan
    receipts = tuple(VerifiedStepResult(initial.plan_id, name, name, 'a' * 64, 'verified', outcome)
                     for name, outcome in [('identity', 'CONFLICT'), ('revision', 'SUPPORTED')])
    result = session.plan(snapshot(), previous=initial, verified_results=receipts, gaps=('Date gap.',))
    assert result.errors == ('VERIFIED_PREMISE_NOT_SUPPORTED',)
    assert len(fake.requests) == 1


def test_completed_dependent_step_cannot_repeat_after_receipts_are_added():
    fake = FakeModel(arguments(relation('identity', 'SAME_TASK'),
                               relation('revision', 'REVISES_PLAN', ('identity',))))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(snapshot()).plan
    receipts = tuple(VerifiedStepResult(initial.plan_id, name, name, 'a' * 64, 'verified', 'SUPPORTED')
                     for name in ['identity', 'revision'])
    repeated = relation('revision_again', 'REVISES_PLAN', ('identity',))
    repeated['question'] = 'A paraphrase of the completed revision question.'
    repeated['evidence_gap'] = 'A textual excuse cannot change the same operation scope.'
    fake.arguments = arguments(repeated)
    result = session.plan(snapshot(), previous=initial, verified_results=receipts, gaps=('Further work.',))
    assert result.errors == ('DUPLICATE_WORK',)


def test_source_handle_rebinding_cannot_reuse_a_completed_source_operation():
    ctx = context(11)
    fake = FakeModel(arguments(discover(source_handles=['s_change'])))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(ctx).plan
    receipt = VerifiedStepResult(initial.plan_id, 'find', 'host', 'a' * 64, 'Discovery completed')
    # Removing an admitted source from the new view is enough to invalidate
    # its old receipt, even though no endpoint used it and the verifier errs.
    changed = replace(ctx, allowed_spans=tuple((h, s) for h, s in ctx.allowed_spans if h != 's_change'))
    result = session.plan(changed, previous=initial, verified_results=(receipt,), gaps=('New gap.',))
    assert result.errors == ('VERIFIED_SOURCE_BINDING_CHANGED',)
    assert len(fake.requests) == 1


def test_time_scoped_query_projection_cannot_silently_drop_bounds():
    row = relation('rel', 'SAME_TASK', handles=('new', 'old'))
    row['time_scope'] = dict(start='2026-10-01', end='2026-10-01')
    plan = planner(FakeModel(arguments(row))).plan(context()).plan
    assert plan.ready_steps[0].time_scope == ('2026-10-01', '2026-10-01')
    with pytest.raises(ValueError, match='TIME_SCOPED_OPERATION_REQUIRES_HOST_BINDING'):
        _ = plan.semantic_queries


def test_host_tool_capabilities_cannot_be_expanded_by_model():
    result = planner(FakeModel(arguments(discover())),
                     supported_operations=frozenset({'inspect', 'clarify'})).plan(context(11))
    assert result.errors == ('UNSUPPORTED_OPERATION',)


def test_unknown_handle_cannot_be_rescued_by_a_discovery_dependency():
    row = relation('illegal', 'AFFECTED_BY', ('find',), handles=('old', 'future_change'))
    result = planner(FakeModel(arguments(discover(), row))).plan(context(11))
    assert result.errors == ('UNKNOWN_HANDLE',)


def test_all_excess_steps_are_counted_even_when_the_plan_is_rejected():
    rows = [owner(), owner('repeat'), discover('unexplained', evidence_gap=''),
            discover('unexplained2', missing_entities=['another entity'], evidence_gap='')]
    result = planner(FakeModel(arguments(*rows))).plan(context())
    assert result.errors == ('DUPLICATE_WORK',)
    assert result.diagnostics['excess_steps'] == 3
    assert result.diagnostics['proposed_steps'] == 4
    assert len(result.diagnostics['validation_issues']) == 3


def test_pending_operation_replanned_without_new_gap_is_observable():
    fake = FakeModel(arguments(owner()))
    session = planner(fake)
    initial = session.plan(context()).plan
    fake.arguments = arguments(owner('replanned'))
    result = session.plan(context(), previous=initial, gaps=('A gap was asserted only at session level.',))
    assert result.errors == ('EVIDENCE_GAP_REQUIRED',)


def test_changed_handle_binding_is_blocked_even_if_old_span_is_still_authorized():
    ctx = context(11)
    fake = FakeModel(arguments(discover(source_handles=['s_change'])))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(ctx).plan
    receipt = VerifiedStepResult(initial.plan_id, 'find', 'host', 'a' * 64, 'Discovery completed')
    bindings = dict(ctx.allowed_spans)
    changed = replace(ctx, allowed_spans=tuple((h, bindings['s_counter'] if h == 's_change' else s)
                      for h, s in ctx.allowed_spans) + (('original_change', bindings['s_change']),))
    result = session.plan(changed, previous=initial, verified_results=(receipt,), gaps=('New gap.',))
    assert result.errors == ('VERIFIED_SOURCE_BINDING_CHANGED',)


def test_source_only_inspection_and_unknown_entity_clarification_remain_distinct():
    row = owner(handles=[])
    row['source_handles'] = ['s_old']
    assert planner(FakeModel(arguments(row))).plan(context()).errors == ('INVALID_INSPECTION',)
    row = step('ask', 'clarify', ())
    row['missing_entities'] = ['Which deployment incident does the user mean?']
    result = planner(FakeModel(arguments(row))).plan(context(11))
    assert result.status == 'PLANNED' and result.plan.steps[0].endpoints == ()


def test_dependency_step_names_do_not_define_identity_but_verified_result_digests_do():
    keys = []
    for parent_id, digest in [('first_name', 'a' * 64), ('alias_name', 'a' * 64), ('third_name', 'b' * 64)]:
        fake = FakeModel(arguments(relation(parent_id, 'SAME_TASK')))
        session = planner(fake, result_verifier=lambda receipt, ctx: True)
        first = session.plan(snapshot()).plan
        receipt = VerifiedStepResult(first.plan_id, parent_id, 'host', digest, 'verified', 'SUPPORTED')
        fake.arguments = arguments(relation('child', 'REVISES_PLAN', (parent_id,)))
        result = session.plan(snapshot(), previous=first, verified_results=(receipt,), gaps=('Revision gap.',))
        assert result.status == 'PLANNED'
        keys.append(result.plan.steps[-1].execution_key)
    assert keys[0] == keys[1]
    assert keys[1] != keys[2]


def test_full_source_selection_and_implicit_full_scope_are_identical_work():
    ctx = snapshot()
    first, second = step(), step('explicit_all')
    first['source_handles'] = []
    second['source_handles'] = [h for h, _ in reversed(ctx.allowed_spans)]
    assert planner(FakeModel(arguments(first, second))).plan(ctx).errors == ('DUPLICATE_WORK',)


def test_later_structural_error_does_not_erase_already_observed_excess_work():
    rows = [owner(), owner('repeat'), relation('bad', 'SAME_TASK', handles=('old', 'delivery'))]
    result = planner(FakeModel(arguments(*rows))).plan(context())
    assert result.errors == ('INVALID_TYPED_RELATION',)
    assert result.diagnostics['excess_steps'] == 1
    assert [i['code'] for i in result.diagnostics['validation_issues']] == ['DUPLICATE_WORK', 'INVALID_TYPED_RELATION']
    assert result.diagnostics['quality_scope'] == 'STRUCTURAL_ERROR'


@pytest.mark.parametrize('explicit', [False, True])
def test_inventory_expansion_revalidates_full_scope_but_preserves_explicit_discovery_scope(explicit):
    public = public_inputs()
    question = public['questions'][11]
    initial_context, _ = planning_context(question, public)
    fake = FakeModel(arguments(discover(source_handles=['s_change'] if explicit else [])))
    session = planner(fake, result_verifier=lambda receipt, ctx: True)
    initial = session.plan(initial_context).plan
    receipt = VerifiedStepResult(initial.plan_id, 'find', 'host', 'a' * 64, 'Concrete change discovered')
    public['sources'].append(dict(handle='s_added', text='A new independent source needs investigation.'))
    expanded, _ = planning_context({**question, 'include_change': True}, public)
    fake.arguments = arguments(relation('impact', 'AFFECTED_BY', ('find',), handles=('old', 'change')))
    result = session.plan(expanded, previous=initial, verified_results=(receipt,), gaps=('Investigate the newly bound change.',))
    if explicit:
        assert result.status == 'PLANNED', result.errors
        assert result.plan.goal == initial.goal
        assert result.plan.steps[0] == initial.steps[0]
        assert [s.id for s in result.plan.ready_steps] == ['impact']
    else:
        assert result.errors == ('VERIFIED_SOURCE_BINDING_CHANGED',)
        assert len(fake.requests) == 1


def test_explicit_source_scope_cannot_be_dropped_by_query_projection():
    row = relation('identity', 'SAME_TASK', handles=('new', 'old'))
    row['source_handles'] = ['s_old']
    plan = planner(FakeModel(arguments(row))).plan(context()).plan
    assert plan.ready_steps[0].source_handles == ('s_old',)
    with pytest.raises(ValueError, match='SOURCE_SCOPED_OPERATION_REQUIRES_HOST_BINDING'):
        _ = plan.semantic_queries
