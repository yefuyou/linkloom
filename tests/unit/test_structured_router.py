"""Offline router contract checks, independent of benchmark answers."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.ui.router_context import RouteCandidate, RouterContext, bind_candidate, time_context
from linkloom.ui.structured_router import CONFIG, RouterInvalid, request_for, validate_output
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession, pass_router_reservation


def context(query='联调报告现在谁负责', selected=None, *, second=False):
    options, required, spans, unresolved, point = time_context(query, selected)
    task = RouteCandidate('TASK', 'task', '联调报告', ('联调报告',), 1, 1 if point else None, 1, False, ('task-key',))
    candidates = {'c0': task}
    if second:
        candidates['c1'] = replace(task, identity=('other-confirmed-task',))
    return RouterContext(query, 'local-workspace', '2026-11-05T00:00:00+00:00', options, required,
                         spans, unresolved, candidates, len(candidates), 'hash', True)


def response(**changes):
    data = dict(schema_version='router/v2', decision='ROUTE', intent='CURRENT', domain='TASK', operation='READ',
        candidate_ids=['c0'], mentions=['联调报告'],
        time_ref_ids=[], time_role='NONE', reason_code='NONE')
    return json.dumps(dict(data, **changes), ensure_ascii=False)


@pytest.mark.parametrize('raw,code', [
    ('{}', 'ROUTER_SCHEMA_INVALID'), ('[]', 'ROUTER_SCHEMA_INVALID'),
    ('```json\n{}\n```', 'ROUTER_SCHEMA_INVALID'),
    ('{"intent":"CURRENT","intent":"AS_OF"}', 'ROUTER_SCHEMA_INVALID'),
    ('{"schema_version":NaN}', 'ROUTER_SCHEMA_INVALID'),
    (response(intent='DELETE'), 'ROUTER_SCHEMA_INVALID'),
    (response(unknown=True), 'ROUTER_SCHEMA_INVALID'),
    (response(candidate_ids=['foreign']), 'CANDIDATE_OUT_OF_SET'),
    (response(candidate_ids=['c0','c0']), 'ROUTER_SCHEMA_INVALID'),
    (response(domain='DECISION'), 'CANDIDATE_DOMAIN_MISMATCH'),
    (response(operation='WRITE_REQUEST'), 'ROUTER_COMBINATION_INVALID'),
    (response(reason_code='WORKSPACE_DENIED'), 'ROUTER_COMBINATION_INVALID'),
    (response(mentions=['其他事项']), 'MENTION_NOT_LITERAL'),
    (response(mentions=[{'text':'联调报告','start':0,'end':4}]), 'ROUTER_SCHEMA_INVALID'),
    (response(time_role='AS_OF', time_ref_ids=['invented']), 'TIME_CONFLICT'),
])
def test_invalid_structured_output_never_becomes_a_route(raw, code):
    with pytest.raises(RouterInvalid, match=code):
        validate_output(raw, context())


def test_confirmation_and_unique_binding_are_host_facts():
    ctx = context(second=True)
    route = validate_output(response(), ctx)
    with pytest.raises(RouterInvalid, match='ENTITY_AMBIGUOUS'):
        bind_candidate(ctx, route)
    single = context()
    assert bind_candidate(single, route).identity == ('task-key',)
    assert 'local-workspace' not in request_for(single).user_input
    assert 'task-key' not in request_for(single).user_input
    assert not request_for(single).available_tools


@pytest.mark.parametrize('literal,partial', [('2031/3/7','3/7'), ('11/20','1/20'), ('２０３１／３／７','3/7'),
    ('2026-10-07T11:00:00Z', '2026-10-07')])
def test_cannot_shorten_full_date_or_timestamp(literal, partial):
    query = '联调报告 ' + literal
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):
        validate_output(response(mentions=[partial]), context(query))


def test_explicit_time_cannot_be_omitted_or_replaced():
    dated = context('联调报告 2026-10-07', '2026-10-07')
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):
        validate_output(response(), dated)
    route = validate_output(response(intent='AS_OF', time_role='AS_OF',time_ref_ids=['point']), dated)
    assert dated.time_options[route.time_ref_ids[0]].value == '2026-10-07'
    mismatch = context('联调报告 2026-10-07', '2026-10-06')
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):
        validate_output(response(intent='LIST',time_role='MEETING_DATE',time_ref_ids=['meeting0']), mismatch)


def test_model_cannot_omit_both_dates_in_meeting_comparison():
    ctx = context('联调报告 2026-10-07 对比 2026-10-08')
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):
        validate_output(response(intent='COMPARE',time_role='MEETING_DATE',time_ref_ids=['meeting0']), ctx)


def test_deepseek_json_request_uses_fixed_model_and_no_tools():
    class Capture:
        def create_chat_completion(self, **payload):
            self.payload = payload
            return {'choices':[{'message':{'content':response()},'finish_reason':'stop'}],
                    'model':CONFIG['model_id'],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
    client = Capture()
    adapter = DeepSeekProviderAdapter(client, model_id=CONFIG['model_id'], json_output=True)
    answer = adapter.complete(request_for(context()))
    assert answer.error is None, answer.error
    assert client.payload['model'] == 'deepseek-flash' and client.payload['temperature'] == 0
    assert client.payload['response_format'] == {'type':'json_object'} and client.payload['thinking'] == {'type':'disabled'}
    assert 'tools' not in client.payload and client.payload['max_tokens'] == CONFIG['max_output_tokens']


def test_remaining_limits_are_request_local_and_cannot_restart_after_uncertain_send(tmp_path):
    limits = TaskProviderLimits(4, 40000, 4000, .3, 30, 6, .01)
    initial = TaskProviderSession(tmp_path/'router', limits, lambda: None)
    initial.requests, initial.input_tokens, initial.output_tokens, initial.cost = 1, 8000, 512, .02
    try:
        with pass_router_reservation(initial):
            child = TaskProviderSession(tmp_path/'graph', limits, lambda: None)
            assert child.limits.max_requests == 3 and child.limits.max_input_tokens == 32000
            assert child.limits.max_output_tokens == 3488 and child.limits.max_cost_usd == pytest.approx(.28)
            assert child.limits.max_seconds <= limits.max_seconds
            child.close()
            initial.failure = 'PROVIDER_TIMEOUT_UNCERTAIN'
            with pytest.raises(ValueError, match='TASK_PROVIDER_BUDGET_EXHAUSTED'):
                TaskProviderSession(tmp_path/'retry', limits, lambda: None)
        unrelated = TaskProviderSession(tmp_path/'unrelated', limits, lambda: None)
        assert unrelated.limits == limits
        unrelated.close()
    finally:
        initial.close()

def test_host_recovers_original_mention_under_width_case_and_space_normalization():
    ctx = context('ＢＵＩＬＤ   Review status?')
    ctx.candidates['c0'] = replace(ctx.candidates['c0'], anchors=('Build Review',))
    route = validate_output(response(mentions=['build review']), ctx)
    assert route.mentions == (('ＢＵＩＬＤ   Review', 0, 14),)
    assert bind_candidate(ctx, route).identity == ('task-key',)


def test_workspace_reference_requires_unique_reviewed_subject_and_a_topic_anchor():
    from linkloom.semantic_ingestion.relations import FrozenRelationResolver
    from linkloom.ui.router_context import build_context

    slots = [dict(workspace_id='local', reviewed=True, slot_id='one',
                  subject='Orchid', relation='deployment window', values=['Tuesday']),
             dict(workspace_id='foreign', reviewed=True, slot_id='two',
                  subject='Clover', relation='deployment window', values=['Thursday'])]
    resolver = FrozenRelationResolver(('deployment window',))

    def catalogue():
        return build_context('What is our deployment window?', 'local', slots, resolver,
                             view=lambda *_: dict(status='FOUND', history=[object()]),
                             query_time='2026-11-05T00:00:00+00:00')

    ctx = catalogue()
    assert ctx.public()['default_subject'] == 'Orchid'
    assert 'Clover' not in request_for(ctx).user_input
    route = validate_output(response(domain='DECISION', mentions=['our', 'deployment window']), ctx)
    assert bind_candidate(ctx, route).identity == ('Orchid', 'deployment window')
    with pytest.raises(RouterInvalid, match='ENTITY_NOT_FOUND'):
        bind_candidate(ctx, validate_output(response(domain='DECISION', mentions=['our']), ctx))
    slots[1]['workspace_id'] = 'local'
    assert catalogue().public()['default_subject'] is None


@pytest.mark.parametrize('value,expected', [(None, None), ('off', None), ('cascade', 'cascade'),
                                            ('llm_first', 'llm_first'), ('on', 'llm_first')])
def test_router_startup_mode_is_explicit(monkeypatch, value, expected):
    from linkloom.ui.router_provider import configured_router

    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    if value is None:
        monkeypatch.delenv('LINKLOOM_LLM_ROUTER', raising=False)
    else:
        monkeypatch.setenv('LINKLOOM_LLM_ROUTER', value)
    provider = configured_router()
    assert (provider.mode if provider else None) == expected


@pytest.mark.parametrize('code', ['WORKSPACE_DENIED', 'NO_CONFIRMED_RECORD'])
def test_negative_host_fact_blocks_binding_even_if_model_omits_name(code):
    from linkloom.ui.router_context import admit_route

    ctx = replace(context('Ａｓｔｅｒ: 联调报告状态'), blocked_entities=(('Aster', code),))
    for mentions in (['联调报告'], ['aster', '联调报告']):
        route = validate_output(response(mentions=mentions), ctx)
        assert admit_route(ctx, route).reason == code
        assert admit_route(ctx, route).candidate is None
