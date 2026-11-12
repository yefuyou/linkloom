"""Time scope contract: the model quotes the time text and names its role; the host only locates the
quote in the question and validates exact date formats. Simulated model responses, no provider call."""
import json
from types import SimpleNamespace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.ui.router_context import (
    RouteCandidate, RouterContext, admit_route, handoff_blocker, has_exact_date, router_query_date, time_context,
)
from linkloom.ui.router_provider import _route_text, _submission
from linkloom.ui.scope_discovery import resolve_query_date
from linkloom.ui.structured_router import RouterInvalid, request_for, route_payload, route_with_adapter


def context(query, task, selected=None):
    options, required, spans, unresolved, point = time_context(query, selected)
    candidate = RouteCandidate('TASK', 'task', task, (task,), 1, 1 if point else None, 1, False, ('task-key',))
    return RouterContext(query, 'local-workspace', '2026-11-05T00:00:00+00:00', options, required,
                         spans, unresolved, {'c0': candidate}, 1, 'hash', True)


def model_route(ctx, task, time_text, time_role, intent='CURRENT'):
    """Run one simulated submit_route tool call through the real wire parsing and host validation."""
    args = dict(decision='ROUTE', operation='READ', intent=intent, candidate_id='c0', mentions=[task],
                unresolved_names=[], time_text=time_text, time_role=time_role, reason_code='NONE')
    message = dict(content=None, tool_calls=[dict(type='function', function=dict(
        name='submit_route', arguments=json.dumps(args, ensure_ascii=False)))])
    raw = _route_text(message, route_payload(ctx))
    adapter = SimpleNamespace(complete=lambda _: ModelResponse(action=ModelAction.final(raw)))
    return route_with_adapter(adapter, request_for(ctx), ctx)


def test_tool_schema_asks_for_time_text_and_role_instead_of_a_host_time_id():
    properties = _submission(route_payload(context('联调报告归谁？', '联调报告')))['function']['parameters']['properties']
    assert properties['time_text'] == {'type': 'string'}
    assert properties['time_role']['enum'] == ['NONE', 'AS_OF', 'MEETING_DATE', 'RANGE', 'UNRESOLVED']
    assert 'time_id' not in properties


def test_identifier_digits_are_not_a_time_scope():
    ctx = context('Who owns INC-2291?', 'INC-2291')
    assert not ctx.unresolved_time and handoff_blocker(ctx) is None
    assert router_query_date(ctx.query, None) == (None, None)
    route = model_route(ctx, 'INC-2291', '', 'NONE')
    assert route.time_role == 'NONE' and admit_route(ctx, route).executor == 'graph'


@pytest.mark.parametrize('query,task', [
    ('联调报告现在归谁？', '联调报告'),
    ('联调报告目前归谁？', '联调报告'),
    ('Who owns the rollout checklist at the moment?', 'rollout checklist'),
    ('Who owns the rollout checklist right now?', 'rollout checklist'),
])
def test_present_state_model_reply_needs_no_time_quote(query, task):
    ctx = context(query, task)
    route = model_route(ctx, task, '', 'NONE')
    admission = admit_route(ctx, route)
    assert route.intent == 'CURRENT' and route.time_role == 'NONE' and not route.time_ref_ids
    assert admission.executor == 'graph' and admission.as_of is None


@pytest.mark.parametrize('query,task,quote', [
    ('上个月联调报告归谁？', '联调报告', '上个月'),
    ('Who owned the rollout checklist last week?', 'rollout checklist', 'last week'),
    ('去年联调报告归谁？', '联调报告', '去年'),
    ('两周前联调报告归谁？', '联调报告', '两周前'),
])
def test_relative_past_model_reply_still_needs_time_clarification(query, task, quote):
    ctx = context(query, task)
    route = model_route(ctx, task, quote, 'UNRESOLVED', 'AS_OF')
    admission = admit_route(ctx, route)
    assert route.time_role == 'UNRESOLVED' and not route.time_ref_ids
    assert admission.reason == 'TIME_SCOPE_UNRESOLVED' and admission.executor is None


def test_before_clause_is_left_to_the_model_and_only_checked_as_a_literal_quote():
    query = 'Before the security review starts, who owns the rollout checklist?'
    ctx = context(query, 'rollout checklist')
    assert not ctx.unresolved_time and handoff_blocker(ctx) is None
    assert router_query_date(query, None) == (None, None)
    assert admit_route(ctx, model_route(ctx, 'rollout checklist', '', 'NONE')).executor == 'graph'
    quoted = model_route(ctx, 'rollout checklist', 'Before the security review starts', 'UNRESOLVED')
    assert quoted.time_role == 'UNRESOLVED' and admit_route(ctx, quoted).reason == 'TIME_SCOPE_UNRESOLVED'


@pytest.mark.parametrize('role,intent', [('RANGE', 'CURRENT'), ('UNRESOLVED', 'CURRENT'), ('AS_OF', 'AS_OF')])
def test_quoted_open_ended_month_is_never_turned_into_a_date(role, intent):
    ctx = context('2025年3月以后联调报告归谁？', '联调报告')
    assert not ctx.unresolved_time and 'point' not in ctx.time_options
    route = model_route(ctx, '联调报告', '2025年3月以后', role, intent)
    assert route.time_role == 'UNRESOLVED' and not route.time_ref_ids
    admission = admit_route(ctx, route)
    assert admission.executor is None and admission.reason == 'TIME_SCOPE_UNRESOLVED'


@pytest.mark.parametrize('query,task', [('联调报告归谁？', '联调报告'), ('Who owns INC-2291?', 'INC-2291')])
@pytest.mark.parametrize('time_text,role', [('2025-03-01', 'AS_OF'), ('上个月', 'UNRESOLVED'), ('last week', 'NONE'),
                                            ('', 'AS_OF'), ('', 'RANGE'), ('', 'UNRESOLVED')])
def test_time_text_that_is_not_in_the_question_is_invalid(query, task, time_text, role):
    with pytest.raises(RouterInvalid, match='TIME_TEXT_NOT_LITERAL'):
        model_route(context(query, task), task, time_text, role, 'AS_OF' if role == 'AS_OF' else 'CURRENT')


def test_quoted_exact_iso_date_becomes_the_as_of_point():
    ctx = context('截至 2027-04-12 联调报告归谁？', '联调报告')
    route = model_route(ctx, '联调报告', '截至 2027-04-12', 'AS_OF', 'AS_OF')
    admission = admit_route(ctx, route)
    assert route.time_ref_ids == ('point',) and admission.executor == 'graph' and admission.as_of == '2027-04-12'
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):  # An explicit date cannot be dropped.
        model_route(ctx, '联调报告', '', 'NONE')


def test_selected_as_of_needs_no_quote_from_the_question():
    ctx = context('联调报告归谁？', '联调报告', selected='2027-04-12')
    route = model_route(ctx, '联调报告', '', 'AS_OF', 'AS_OF')
    assert admit_route(ctx, route).as_of == '2027-04-12'


@pytest.mark.parametrize('query,selected,resolved,error', [
    ('2026-10-06 supplier', None, '2026-10-06', None),
    ('2026年10月6日采用哪家供应商？', None, '2026-10-06', None),
    ('supplier', '2026-09-15', '2026-09-15', None),
    ('2026-10-06 supplier', '2026-10-10', None, 'DATE_SCOPE_REQUIRED'),
    ('2026-10-01 and 2026-10-10 supplier', None, None, 'DATE_SCOPE_REQUIRED'),
    ('2026-02-30 supplier', None, None, 'INVALID_AS_OF'),
    ('INC-2291 supplier', None, None, None),
    ('ticket 2291-05 supplier', None, None, None),
    ('2026-10 supplier', None, None, None),
])
def test_model_mode_host_keeps_only_exact_date_format_validation(query, selected, resolved, error):
    value, failure = router_query_date(query, selected)
    assert value == resolved and (failure or {}).get('code') == error


OPEN_ENDED = [('2026-10-01至今联调报告归谁？', '2026-10-01至今'),
              ('联调报告 2026-10-01 to now 归谁？', '2026-10-01 to now'),
              ('联调报告 between 2026-10-01 and now 归谁？', 'between 2026-10-01 and now')]


@pytest.mark.parametrize('query,quote', OPEN_ENDED)
def test_model_judges_whether_an_exact_date_is_a_point_or_the_edge_of_a_range(query, quote):
    ctx = context(query, '联调报告')
    assert has_exact_date(query) and not ctx.unresolved_time and handoff_blocker(ctx) is None
    ranged = model_route(ctx, '联调报告', quote, 'RANGE')
    admission = admit_route(ctx, ranged)
    assert ranged.time_role == 'UNRESOLVED' and not ranged.time_ref_ids
    assert admission.executor is None and admission.reason == 'TIME_SCOPE_UNRESOLVED' and admission.as_of is None
    with pytest.raises(RouterInvalid, match='TIME_CONFLICT'):  # The model may not ignore the literal date.
        model_route(ctx, '联调报告', '', 'NONE')


@pytest.mark.parametrize('query', [q for q, _ in OPEN_ENDED] + ['上个月联调报告归谁？', 'INC-2291 supplier'])
def test_rules_mode_only_detects_a_time_expression_and_never_sets_a_date(query):
    value, failure = resolve_query_date(query, None)
    assert value is None and failure['code'] == 'DATE_SCOPE_REQUIRED'


def test_rules_mode_keeps_a_plain_iso_date_as_the_as_of_point():
    assert resolve_query_date('2026-10-01的支付网关', None) == ('2026-10-01', None)
    assert not has_exact_date('上个月的支付网关') and has_exact_date('2026-10-01的支付网关')
