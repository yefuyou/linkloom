from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
from copy import deepcopy
import json
import time

import pytest

from linkloom.agents.model_adapter import ModelResponse, ModelAction, ModelUsage, ModelProviderError
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession


def limits(**changes):
    return TaskProviderLimits(**dict(max_requests=2, max_input_tokens=20000,
        max_output_tokens=2000, max_cost_usd=.2, max_seconds=5,
        request_timeout_seconds=1, request_cost_upper_bound_usd=.1, **changes))


def request():
    from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
    return ModelTurnRequest(run_id='budget-run', turn_id='turn-1', task_id='task',
        agent_id='agent', sequence=1, user_input='Synthetic source', observation=None,
        available_tools=[], model_id='gemini-3.8-flash',
        generation_options=ModelGenerationOptions(max_output_tokens=500))


def session(tmp_path, adapter, policy=None, validate=lambda: None):
    return TaskProviderSession(tmp_path, policy or limits(), validate).bind(adapter, 'test')


def test_budget_reserves_all_stages_and_stops_before_third_request(tmp_path):
    calls = []
    adapter = SimpleNamespace(complete=lambda r: calls.append(r) or
        ModelResponse(action=ModelAction.final('ok'), usage=ModelUsage(input_tokens=10, output_tokens=4)))
    bound = session(tmp_path, adapter)
    assert bound.complete(request()).action.final_answer == 'ok'
    assert bound.complete(request()).action.final_answer == 'ok'
    assert bound.complete(request()).error.code == 'MODEL_CANCELLED'
    assert len(calls) == 2
    journal = json.loads((tmp_path / 'budget.json').read_text())
    assert journal['requests_reserved'] == 2
    assert journal['cost_reserved_usd'] == .2
    assert len(journal['attempts']) == 2


def test_unknown_outcome_poisoned_session_never_retries(tmp_path):
    calls = []
    def uncertain(r):
        calls.append(r)
        return ModelResponse(error=ModelProviderError('MODEL_UNKNOWN_FAILURE',
            'unknown_provider_error', 'Unknown provider result.', False, outcome='unknown_provider_outcome'))
    bound = TaskProviderSession(tmp_path, limits(), lambda: None).bind(
        SimpleNamespace(complete=uncertain), 'test', final_input_upper_bound=106496)
    bound.complete(request())
    blocked = bound.complete(request())
    assert len(calls) == 1 and blocked.error
    assert bound.session.failure == 'PROVIDER_UNCERTAIN'
    prepared = bound.prepare_request(request())
    assert prepared.final_only and bound.session.cost == .1 and bound.session.requests == 1
    assert bound.complete(prepared).error and len(calls) == 1
    assert json.loads((tmp_path / 'budget.json').read_text())['attempts'][0]['outcome'] == 'UNCERTAIN'


@pytest.mark.parametrize('resource', ['requests', 'input', 'output', 'cost', 'time'])
def test_worker_preflight_keeps_final_capacity_even_when_future_context_grows(tmp_path, monkeypatch, resource):
    from linkloom.ui import task_provider
    policy = TaskProviderLimits(8, 200000, 8000, .4, 10, 2, .02)
    changes = {'requests': dict(max_requests=1), 'input': dict(max_input_tokens=100000),
               'output': dict(max_output_tokens=500), 'cost': dict(max_cost_usd=.08)}
    policy = replace(policy, **changes.get(resource, {}))
    calls = []
    adapter = SimpleNamespace(estimate_input_tokens=lambda r: 1000,
        complete=lambda r: calls.append(r) or ModelResponse(action=ModelAction.final('{}')))
    bound = TaskProviderSession(tmp_path, policy, lambda: None).bind(adapter, 'runtime', final_input_upper_bound=106496)
    if resource == 'time':
        monkeypatch.setattr(task_provider, 'monotonic', lambda: bound.session.started + 8)
    prepared = bound.prepare_request(request())
    assert prepared.final_only is True and prepared.available_tools == []
    assert bound.session.requests == 0 and bound.session.cost == 0 and not calls
    assert not bound.complete(prepared).error and len(calls) == 1
    assert bound.session.requests == 1


def test_original_task2_journal_final_no_longer_charges_unsent_internal_history(tmp_path):
    from linkloom.agents.model_adapter import ModelToolInteraction
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.tools.contracts import ToolCall, ToolResult
    fixture = Path(__file__).resolve().parents[1] / 'fixtures/orchestration-final/task-2-budget.json'
    before = json.loads(fixture.read_text())
    assert before['requests_reserved'] == 6 and before['cost_reserved_usd'] == .194581
    # Fresh offline reconstruction immediately BEFORE the denied Final. Never reopen the failed real session.
    class OfflineGemini(GeminiProviderAdapter):
        def complete(self, r):
            return ModelResponse(action=ModelAction.final('{}'), usage=ModelUsage(input_tokens=10, output_tokens=2))
    adapter = OfflineGemini(SimpleNamespace(generate_content=lambda **_: pytest.fail('no transport permitted')))
    # The original three successful reservations stay spent. Request 4's mapped estimate
    # (22983) and immutable user size (8792 JSON bytes) come from its durable Q3 artifact.
    preflight = TaskProviderSession(tmp_path / 'preflight', TaskProviderLimits(**before['limits']), lambda: None)
    prefix = before['attempts'][:3]
    preflight.requests = len(prefix)
    preflight.input_tokens = sum(row['input_reserved'] for row in prefix)
    preflight.output_tokens = sum(row['output_reserved'] for row in prefix)
    preflight.cost = sum(row['cost_reserved_usd'] for row in prefix)
    preflight.attempts = deepcopy(prefix)
    recorded = SimpleNamespace(estimate_input_tokens=lambda r: 22983,
                               final_input_upper_bound=adapter.final_input_upper_bound)
    fourth = replace(request(), sequence=4, user_input='u' * 8790,
        generation_options=replace(request().generation_options, max_output_tokens=1027))
    assert not preflight.prepare_request(recorded, fourth, 135168, 1027).final_only
    assert preflight.attempts == prefix and preflight.cost == sum(row['cost_reserved_usd'] for row in prefix)
    session = TaskProviderSession(tmp_path, TaskProviderLimits(**before['limits']), lambda: None)
    session.requests = before['requests_reserved']
    session.input_tokens = before['input_tokens_reserved']
    session.output_tokens = before['output_tokens_reserved']
    session.cost = before['cost_reserved_usd']
    session.attempts = deepcopy(before['attempts'])
    call = ToolCall('old-search', 'search_notes', dict(query='old'), sequence=1)
    history = ModelToolInteraction(call, ToolResult(call.call_id, call.tool_id, 'ok', value='old history ' * 4000))
    final = replace(request(), sequence=7, final_only=True, tool_history=[history],
                    generation_options=replace(request().generation_options, max_output_tokens=1027, thinking_level='low'))
    old_size = len(json.dumps(final.to_dict(), ensure_ascii=False).encode()) + 4096
    remaining = session.limits.max_cost_usd - session.cost
    assert session.limits.reservation_cost(old_size, 1027) > remaining
    assert session.limits.reservation_cost(adapter.estimate_input_tokens(final), 1027) <= remaining
    assert not session.complete(adapter, 'runtime', final).error
    assert session.attempts[:6] == before['attempts'] and session.requests == 7
    assert before['failure'] == 'TASK_PROVIDER_BUDGET_EXHAUSTED'


def test_smaller_tool_turn_output_does_not_reduce_the_future_final_ceiling(tmp_path):
    policy = TaskProviderLimits(8, 200000, 1000, .4, 10, 2, .02)
    adapter = SimpleNamespace(estimate_input_tokens=lambda r: 1000, complete=lambda r: None)
    bound = TaskProviderSession(tmp_path, policy, lambda: None).bind(adapter, 'runtime',
        final_input_upper_bound=106496, final_output_upper_bound=1027)
    small = replace(request(), generation_options=replace(request().generation_options, max_output_tokens=128))
    assert bound.prepare_request(small).final_only is True
    assert bound.session.requests == 0


def test_time_guard_uses_shortened_tool_timeout_and_keeps_full_final_timeout(tmp_path, monkeypatch):
    from linkloom.ui import task_provider
    policy = TaskProviderLimits(8, 200000, 8000, .4, 10, 2, .02)
    adapter = SimpleNamespace(estimate_input_tokens=lambda r: 1000,
        complete=lambda r: ModelResponse(action=ModelAction.final('{}')))
    bound = TaskProviderSession(tmp_path, policy, lambda: None).bind(adapter, 'runtime', final_input_upper_bound=106496)
    monkeypatch.setattr(task_provider, 'monotonic', lambda: bound.session.started + 7)
    prepared = bound.prepare_request(request())
    assert not prepared.final_only  # 3 seconds remaining: 1 second tool window + 2 seconds Final
    assert not bound.complete(prepared).error
    assert bound.session.attempts[-1]['timeout_seconds'] == 1
    final = replace(prepared, final_only=True)
    assert not bound.complete(final).error
    assert bound.session.attempts[-1]['timeout_seconds'] == 2


@pytest.mark.parametrize('max_seconds,tools_allowed,tool_window', [
    (48, False, None),      # 3 s would be left for a tool request: go straight to Final-only
    (56.2, False, None),    # just under the quarter-timeout minimum (11.25 s)
    (56.25, True, 11.25),   # exactly the minimum window is still a normal tool request
    (60, True, 15),
    (231, True, 45),        # the real slice: ordinary tool calls keep the full request timeout
])
def test_short_tool_window_goes_to_final_only_and_final_stays_sendable(
        tmp_path, monkeypatch, max_seconds, tools_allowed, tool_window):
    from linkloom.ui import task_provider
    calls = []
    adapter = SimpleNamespace(estimate_input_tokens=lambda r: 20000,
        complete=lambda r: calls.append(r) or ModelResponse(action=ModelAction.final('{}')))
    policy = TaskProviderLimits(8, 400000, 8000, 1.0, max_seconds, 45, .02)
    bound = TaskProviderSession(tmp_path, policy, lambda: None).bind(adapter, 'runtime', final_input_upper_bound=106496)
    monkeypatch.setattr(task_provider, 'monotonic', lambda: bound.session.started)
    prepared = bound.prepare_request(request())
    assert prepared.final_only is (not tools_allowed)
    if tools_allowed:
        assert not bound.complete(prepared).error
        assert bound.session.attempts[-1]['timeout_seconds'] == tool_window
        prepared = bound.prepare_request(request())  # unchanged clock: still a tool request
        assert not prepared.final_only
        prepared = replace(prepared, final_only=True)
    else:
        assert not calls  # no short request was ever sent
    assert not bound.complete(prepared).error
    assert bound.session.attempts[-1]['timeout_seconds'] == min(45, max_seconds)  # the Final keeps its full window
    assert bound.session.failure is None


def test_provider_timeout_is_still_uncertain_and_never_retried_after_a_normal_tool_window(tmp_path):
    calls = []
    def slow(r):
        calls.append(r)
        time.sleep(.6)
        return ModelResponse(action=ModelAction.final('{}'))
    adapter = SimpleNamespace(estimate_input_tokens=lambda r: 1000, complete=slow)
    bound = TaskProviderSession(tmp_path, TaskProviderLimits(8, 200000, 8000, .4, 1.5, .4, .02), lambda: None).bind(
        adapter, 'runtime', final_input_upper_bound=106496)
    prepared = bound.prepare_request(request())
    assert not prepared.final_only  # 1.1 s remain after the Final window: a 0.4 s tool request
    assert bound.complete(prepared).error
    assert bound.session.failure == 'PROVIDER_TIMEOUT_UNCERTAIN' and len(calls) == 1
    assert bound.prepare_request(request()).final_only  # a poisoned session only ever finalizes
    assert bound.complete(replace(prepared, final_only=True, available_tools=[])).error
    assert len(calls) == 1  # the unknown receipt is never retried or re-sent
    assert [a['outcome'] for a in bound.session.attempts] == ['UNCERTAIN']


def test_timeout_is_uncertain_and_no_late_result_can_publish(tmp_path):
    def slow(r):
        time.sleep(.15)
        return ModelResponse(action=ModelAction.final('late'))
    policy = replace(limits(), request_timeout_seconds=.02)
    bound = session(tmp_path, SimpleNamespace(complete=slow), policy)
    response = bound.complete(request())
    assert response.error and response.error.outcome == 'unknown_provider_outcome'
    assert bound.session.failure == 'PROVIDER_TIMEOUT_UNCERTAIN'
    assert bound.complete(request()).error


def test_source_revocation_and_output_overrun_fail_closed(tmp_path):
    calls = []
    def invalid():
        raise ValueError('SOURCE_EVIDENCE_NOT_CURRENT')
    bound = session(tmp_path / 'source', SimpleNamespace(complete=lambda r: calls.append(r)), validate=invalid)
    assert bound.complete(request()).error and not calls
    bound = session(tmp_path / 'usage', SimpleNamespace(complete=lambda r:
        ModelResponse(action=ModelAction.final('bad'), usage=ModelUsage(output_tokens=501))))
    assert bound.complete(request()).error
    assert bound.session.failure == 'PROVIDER_USAGE_EXCEEDED'


@pytest.mark.parametrize('field,value', [('max_requests', True), ('max_input_tokens', 0),
    ('max_seconds', float('nan')), ('max_cost_usd', 0), ('request_timeout_seconds', -1)])
def test_explicit_finite_positive_limits_required(field, value):
    with pytest.raises(ValueError):
        replace(limits(), **{field: value})


def test_cold_read_uses_execution_completion_time_not_later_read_time(tmp_path):
    from linkloom.ui.investigation import ProductInvestigationService
    directory = tmp_path / 'provider'
    bound = session(directory, SimpleNamespace(complete=lambda r: ModelResponse(action=ModelAction.final('ok'))))
    assert not bound.complete(request()).error
    journal = json.loads((directory / 'budget.json').read_text(encoding='utf8'))
    journal['started_at'] = '2026-10-21T00:00:00+00:00'
    (directory / 'budget.json').write_text(json.dumps(journal), encoding='utf8')
    preparation = SimpleNamespace(manifest=dict(execution_kind='LIVE'))
    # An old completed result is still readable after the wall-clock window;
    # a Runtime that completed after its execution budget is not publishable.
    ProductInvestigationService._validate_task_read(preparation,
        SimpleNamespace(updated_at='2026-10-21T00:00:03+00:00'), {}, tmp_path)
    with pytest.raises(ValueError, match='DURATION_EXCEEDED'):
        ProductInvestigationService._validate_task_read(preparation,
            SimpleNamespace(updated_at='2026-10-21T00:00:06+00:00'), {}, tmp_path)


@pytest.mark.parametrize('damage', ['input', 'output', 'cost', 'usage', 'sequence', 'limits'])
def test_completed_provider_receipt_revalidates_budget_and_usage(tmp_path, damage):
    from datetime import datetime, UTC
    from linkloom.ui.task_provider import validate_task_provider_receipt
    bound = session(tmp_path, SimpleNamespace(complete=lambda r: ModelResponse(action=ModelAction.final('ok'))))
    bound.complete(request())
    path = tmp_path / 'budget.json'
    budget = json.loads(path.read_text(encoding='utf8'))
    if damage == 'input':
        budget['input_tokens_reserved'] += 1
    elif damage == 'output':
        budget['output_tokens_reserved'] += 1
    elif damage == 'cost':
        budget['attempts'][0]['cost_reserved_usd'] = .0001
        budget['cost_reserved_usd'] = .0001
    elif damage == 'usage':
        budget['attempts'][0]['usage']['total_tokens'] = 999999
    elif damage == 'sequence':
        budget['attempts'][0]['sequence'] = 2
    else:
        budget['limits']['max_input_tokens'] = 1
    path.write_text(json.dumps(budget), encoding='utf8')
    with pytest.raises(ValueError):
        validate_task_provider_receipt(path, datetime.now(UTC).isoformat())
