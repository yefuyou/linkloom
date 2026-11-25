from dataclasses import replace
from types import SimpleNamespace
import json
import time

import pytest

from linkloom.agents.model_adapter import ModelResponse, ModelAction, ModelUsage, ModelProviderError
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession


def limits(**changes):
    return TaskProviderLimits(**dict(max_seconds=5, request_timeout_seconds=1, **changes))


def request():
    from linkloom.agents.model_adapter import ModelGenerationOptions, ModelTurnRequest
    return ModelTurnRequest(run_id='budget-run', turn_id='turn-1', task_id='task',
        agent_id='agent', sequence=1, user_input='Synthetic source', observation=None,
        available_tools=[], model_id='gemini-3.8-flash',
        generation_options=ModelGenerationOptions(max_output_tokens=500))


def session(tmp_path, adapter, policy=None, validate=lambda: None):
    return TaskProviderSession(tmp_path, policy or limits(), validate).bind(adapter, 'test')


def test_many_requests_and_huge_usage_are_recorded_and_never_stop_the_session(tmp_path):
    calls = []
    adapter = SimpleNamespace(complete=lambda r: calls.append(r) or ModelResponse(action=ModelAction.final('ok'),
        usage=ModelUsage(input_tokens=10 ** 12, output_tokens=10 ** 12)))
    bound = session(tmp_path, adapter)
    for _ in range(60):
        assert bound.complete(request()).action.final_answer == 'ok'
    assert len(calls) == 60 and bound.session.failure is None
    journal = json.loads((tmp_path / 'provider_journal.json').read_text())
    assert journal["requests"] == 60 and len(journal["attempts"]) == 60
    assert all(row['outcome'] == 'SUCCEEDED' for row in journal['attempts'])
    assert journal['attempts'][0]['usage']['input_tokens'] == 10 ** 12


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
    assert json.loads((tmp_path / 'provider_journal.json').read_text())['attempts'][0]['outcome'] == 'UNCERTAIN'


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


def test_source_revocation_fails_closed_and_unreported_or_oversized_usage_is_accepted(tmp_path):
    calls = []
    def invalid():
        raise ValueError('SOURCE_EVIDENCE_NOT_CURRENT')
    bound = session(tmp_path / 'source', SimpleNamespace(complete=lambda r: calls.append(r)), validate=invalid)
    assert bound.complete(request()).error and not calls
    bound = session(tmp_path / 'usage', SimpleNamespace(complete=lambda r:
        ModelResponse(action=ModelAction.final('fine'), usage=ModelUsage(output_tokens=501))))
    assert bound.complete(request()).action.final_answer == 'fine'
    assert bound.session.failure is None
    bound = session(tmp_path / 'unreported', SimpleNamespace(complete=lambda r:
        ModelResponse(action=ModelAction.final('fine'), usage=ModelUsage())))
    assert bound.complete(request()).action.final_answer == 'fine'


@pytest.mark.parametrize('field,value', [('max_seconds', float('nan')), ('max_seconds', True),
    ('request_timeout_seconds', -1), ('request_timeout_seconds', 0)])
def test_explicit_finite_positive_durations_required(field, value):
    with pytest.raises(ValueError):
        replace(limits(), **{field: value})


def test_removed_budget_keys_are_not_limit_fields_and_legacy_dicts_are_ignored():
    assert set(TaskProviderLimits.__dataclass_fields__) == {'max_seconds', 'request_timeout_seconds'}
    with pytest.raises(TypeError):
        TaskProviderLimits(max_seconds=5, request_timeout_seconds=1, max_requests=1)
    legacy = dict(max_requests=1, max_input_tokens=1, max_output_tokens=1, max_cost_usd=0,
                  max_seconds=5, request_timeout_seconds=1, estimated_cost_per_request_usd=9)
    assert TaskProviderLimits.from_dict(legacy) == limits()


def test_cold_read_uses_execution_completion_time_not_later_read_time(tmp_path):
    from linkloom.ui.investigation import ProductInvestigationService
    directory = tmp_path / 'provider'
    bound = session(directory, SimpleNamespace(complete=lambda r: ModelResponse(action=ModelAction.final('ok'))))
    assert not bound.complete(request()).error
    journal = json.loads((directory / 'provider_journal.json').read_text(encoding='utf8'))
    journal['started_at'] = '2026-10-21T00:00:00+00:00'
    (directory / 'provider_journal.json').write_text(json.dumps(journal), encoding='utf8')
    preparation = SimpleNamespace(manifest=dict(execution_kind='LIVE'))
    # An old completed result is still readable after the wall-clock window;
    # a Runtime that completed after its execution budget is not publishable.
    ProductInvestigationService._validate_task_read(preparation,
        SimpleNamespace(updated_at='2026-10-21T00:00:03+00:00'), {}, tmp_path)
    with pytest.raises(ValueError, match='DURATION_EXCEEDED'):
        ProductInvestigationService._validate_task_read(preparation,
            SimpleNamespace(updated_at='2026-10-21T00:00:06+00:00'), {}, tmp_path)


@pytest.mark.parametrize('damage', ['usage_huge', 'unreported', 'sequence', 'outcome', 'hash', 'duration'])
def test_completed_provider_receipt_revalidates_structure_and_duration_only(tmp_path, damage):
    from datetime import datetime, UTC
    from linkloom.ui.task_provider import validate_task_provider_receipt
    usage = ModelUsage() if damage == 'unreported' else ModelUsage(input_tokens=10 ** 12, output_tokens=10 ** 12)
    bound = session(tmp_path, SimpleNamespace(complete=lambda r: ModelResponse(action=ModelAction.final('ok'), usage=usage)))
    bound.complete(request())
    path = tmp_path / 'provider_journal.json'
    journal = json.loads(path.read_text(encoding='utf8'))
    # A legacy limits dict with tiny budgets must not matter: it is read, never enforced.
    journal['limits'].update(max_requests=1, max_input_tokens=1, max_output_tokens=1, max_cost_usd=.0001)
    journal['attempts'][0]['estimated_cost_usd'] = 1e9
    if damage == 'sequence':
        journal['attempts'][0]['sequence'] = 2
    elif damage == 'outcome':
        journal['attempts'][0]['outcome'] = 'PENDING'
    elif damage == 'hash':
        journal['attempts'][0]['response_sha256'] = 'x'
    elif damage == 'duration':
        journal['elapsed_seconds'] = journal['limits']['max_seconds'] + 1
    path.write_text(json.dumps(journal), encoding='utf8')
    completed = datetime.now(UTC).isoformat()
    if damage in ('usage_huge', 'unreported'):
        validate_task_provider_receipt(path, completed)
    else:
        with pytest.raises(ValueError):
            validate_task_provider_receipt(path, completed)
