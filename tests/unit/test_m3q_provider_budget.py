from dataclasses import replace
from types import SimpleNamespace
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
    bound = session(tmp_path, SimpleNamespace(complete=uncertain))
    bound.complete(request())
    blocked = bound.complete(request())
    assert len(calls) == 1 and blocked.error
    assert bound.session.failure == 'PROVIDER_UNCERTAIN'
    assert json.loads((tmp_path / 'budget.json').read_text())['attempts'][0]['outcome'] == 'UNCERTAIN'


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
