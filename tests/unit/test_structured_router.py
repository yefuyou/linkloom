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


def test_strict_deepseek_wire_call_is_one_proposal_with_only_offered_ids():
    from io import BytesIO
    from linkloom.ui.router_provider import _DeepSeekJSONClient

    class Opener:
        def open(self, request, *, timeout):
            payload = json.loads(request.data)
            assert request.full_url.endswith('/beta/chat/completions')
            assert payload['tool_choice']['function']['name'] == 'submit_route'
            assert 'response_format' not in payload
            assert payload['temperature'] == 0 and payload['thinking']['type'] == 'disabled'
            tool = payload['tools'][0]['function']
            assert tool['strict'] and tool['parameters']['additionalProperties'] is False
            assert tool['parameters']['properties']['candidate_id']['enum'] == ['', 'c0']
            fields = dict(decision='CLARIFY', operation='READ', intent='AS_OF', candidate_id='c0',
                          mentions=['not in original', '联调报告'], unresolved_names=[],
                          time_id='point', reason_code='NONE')
            response = {'model': CONFIG['model_id'], 'choices': [{'finish_reason': 'tool_calls', 'message': {
                'content': None, 'tool_calls': [{'type': 'function', 'function': {
                    'name': 'submit_route', 'arguments': json.dumps(fields)}}]}}]}
            return BytesIO(json.dumps(response).encode())

    ctx = context('联调报告 2026-10-07')
    client = _DeepSeekJSONClient('fake')
    client._opener = Opener()
    adapter = DeepSeekProviderAdapter(client, model_id=CONFIG['model_id'], json_output=True)
    from linkloom.ui.structured_router import route_with_adapter
    route = route_with_adapter(adapter, request_for(ctx), ctx)
    assert route.domain == 'TASK' and route.time_role == 'AS_OF' and route.time_ref_ids == ('point',)
    assert route.decision == 'ROUTE' and route.operation == 'READ' and len(route.mentions) == 1


def test_shortlist_excludes_unoffered_id_without_hiding_binding_conflicts():
    from linkloom.ui.structured_router import route_with_adapter

    ctx = context()
    base = ctx.candidates['c0']
    candidates = {f'c{i}': replace(base, identity=(str(i),)) for i in range(7)}
    ctx = replace(ctx, candidates=candidates, total=len(candidates))
    req = request_for(ctx)
    assert len(req.user_input.encode()) <= CONFIG['max_input_bytes']
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final(response(candidate_ids=['c6']))))
    with pytest.raises(RouterInvalid, match='CANDIDATE_OUT_OF_SET'):
        route_with_adapter(adapter, req, ctx)
    with pytest.raises(RouterInvalid, match='ENTITY_AMBIGUOUS'):
        bind_candidate(ctx, validate_output(response(), ctx))


def test_confirmed_task_qualifier_disambiguates_but_is_not_a_root():
    from linkloom.ui.router_context import admit_route

    ctx = context('Orchid 联调报告的负责人向谁汇报', second=True)
    ctx.candidates['c0'] = replace(ctx.candidates['c0'], anchors=('联调报告', 'Orchid'))
    ctx.candidates['c1'] = replace(ctx.candidates['c1'], anchors=('联调报告', 'Clover'))
    def proposal(mentions):
        return validate_output(response(intent='RELATION_MULTIHOP', domain='RELATION', mentions=mentions), ctx)

    assert admit_route(ctx, proposal(['Orchid', '联调报告'])).executor == 'graph'
    assert admit_route(ctx, proposal(['Orchid'])).reason == 'ENTITY_NOT_FOUND'
    assert admit_route(ctx, proposal(['联调报告'])).reason == 'ENTITY_AMBIGUOUS'


def test_punctuation_width_does_not_invalidate_an_intact_timestamp():
    ctx = context('2027-04-12T11:30:00+08:00 联调报告归谁？')
    route = validate_output(response(intent='AS_OF', time_role='AS_OF', time_ref_ids=['point']), ctx)
    assert ctx.time_options[route.time_ref_ids[0]].value == '2027-04-12T11:30:00+08:00'


def test_time_question_word_is_not_a_relative_date_but_keeps_real_qualifiers():
    assert not context('联调报告什么时候交？').unresolved_time
    assert context('上周联调报告什么时候交？').unresolved_time


def test_soft_unresolved_names_require_literal_and_full_catalogue_absence():
    from linkloom.ui.router_context import admit_route

    ctx = replace(context('Orchid 的联调报告归谁？'),
                  catalogue_names=frozenset({'orchid', '联调报告'}))
    def route(ctx, unresolved):
        return validate_output(response(schema_version='router/v3', unresolved_names=unresolved,
                                        mentions=['联调报告']), ctx)

    assert admit_route(ctx, route(ctx, ['made-up', 'Ｏｒｃｈｉｄ'])).executor == 'graph'
    unknown = replace(ctx, catalogue_names=frozenset({'联调报告'}))
    assert admit_route(unknown, route(unknown, ['Orchid'])).reason == 'ENTITY_NOT_FOUND'


@pytest.mark.parametrize('operation,reason,expected', [
    ('WRITE_REQUEST', 'NONE', 'WRITE_NOT_ALLOWED'),
    ('READ', 'SECRET_OR_POLICY_OVERRIDE', 'SECRET_OR_POLICY_OVERRIDE'),
    ('READ', 'WORKSPACE_DENIED', 'WORKSPACE_DENIED'),
])
def test_model_can_only_strengthen_safety_even_with_unusable_positive_fields(operation,reason,expected):
    from linkloom.ui.structured_router import route_with_adapter
    ctx = context('请处理素材卡片')
    raw = response(operation=operation, reason_code=reason, mentions=['invented'],candidate_ids=['unoffered'])
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final(raw)))
    route = route_with_adapter(adapter, request_for(ctx), ctx)
    assert route.decision == 'REFUSE' and route.reason_code == expected and not route.candidate_ids

@pytest.mark.parametrize('operation,reason', [('WRITE_REQUEST','NONE'),('READ','WRITE_NOT_ALLOWED')])
def test_native_tool_operation_and_reason_are_both_terminal_safety_signals(operation,reason):
    from linkloom.ui.router_provider import _route_text
    from linkloom.ui.structured_router import route_payload,route_with_adapter
    ctx=context('请处理素材卡片')
    args=dict(decision='CLARIFY',operation=operation,intent='OTHER',candidate_id='',
              mentions=[],unresolved_names=[],time_id='none',reason_code=reason)
    message=dict(content=None,tool_calls=[dict(type='function',function=dict(name='submit_route',arguments=json.dumps(args)))])
    raw=_route_text(message,route_payload(ctx))
    adapter=SimpleNamespace(complete=lambda _:ModelResponse(action=ModelAction.final(raw)))
    result=route_with_adapter(adapter,request_for(ctx),ctx)
    assert result.decision=='REFUSE' and result.operation=='WRITE_REQUEST'
    assert result.reason_code=='WRITE_NOT_ALLOWED' and not result.candidate_ids
