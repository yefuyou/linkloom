"""F protocol boundaries; semantic recall is measured separately with real responses."""
from dataclasses import replace
import re

import pytest

from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_observation import TaskObservation
from tests.unit.test_task_field_binding import grounded
from tests.unit.test_task_observation_discovery import setup, claim


def discover(proposal, artifact):
    row = claim(proposal.text, *proposal.description_parts)
    row['task'] = proposal.to_dict()
    service, provider, registry, current = setup(artifact, [row])
    service.task_profile = TaskDiscoveryProfile.TASK_RECALL_V3
    return service.discover((artifact,), workspace_id=artifact.workspace_id), provider.requests[0]


def test_explicit_work_survives_all_unknown_fields_and_cold_read():
    a, _, p, _ = grounded('归档校验列入本轮执行，承办人和交付日期尚空。', '归档校验')
    result, request = discover(p, a)
    obs, = result.partial_observations
    assert obs.owner is None and obs.deadline is None
    assert set(obs.unknown_fields) == {'owner', 'deadline', 'project', 'vendor'}
    assert obs.description == '归档校验' and obs.work_state == 'ASSERTED'
    assert TaskObservation.from_dict(obs.to_dict()).verify(a)
    assert obs.authority == 'SUPPORTING_ONLY' and not result.failures
    assert 'task-recall/v3' in request.user_input


@pytest.mark.parametrize('state', ['CONDITIONAL', 'NEGATED', 'REPORTED', 'HISTORICAL', 'WITHDRAWN', 'NOT_STATED', 'AMBIGUOUS'])
def test_nonasserted_work_is_not_promoted_by_unknown_field_recall(state):
    a, _, p, _ = grounded('讨论归档校验的可能安排。', '归档校验')
    result, _ = discover(replace(p, work_state=state), a)
    assert not result.valid_observations and not result.partial_observations
    assert result.failures[0].reason == 'NON_ASSERTED_TASK'


@pytest.mark.parametrize('token,day,precision', [
    ('2036-04-17', '2036-04-17', 'DAY'), ('2036年4月17日', '2036-04-17', 'DAY'),
    ('8月9日', None, 'MONTH_DAY'), ('2036年8月', None, 'MONTH'), ('8月', None, 'MONTH')])
def test_role_quote_is_distinct_from_literal_date_token(token, day, precision):
    a, _, p, _ = grounded('归档校验约定在'+token+'之前交齐。', '归档校验', values={'deadline': token})
    result, request = discover(p, a)
    obs, = result.partial_observations
    assert (obs.deadline_text, obs.deadline, obs.deadline_precision) == (token, day, precision)
    assert obs.deadline_role == 'TASK_DEADLINE' and obs.verify(a)
    schema = request.available_tools[0].input_schema['properties']['claims']['items']['properties']['task']
    pattern = schema['properties']['deadline_text']['pattern']
    assert re.fullmatch(pattern, token)
    assert not re.fullmatch(pattern, token+'之前')
    assert not re.fullmatch(pattern, 'by '+token)


@pytest.mark.parametrize('token,change', [
    ('2036-02-31', {}), ('2036-04-17前', {}), ('2036-04-17', {'role': 'MEETING_DATE'}),
    ('2036-04-17', {'state': 'AMBIGUOUS'}), ('2036-04-17', {'quote': '别处的日期'}),
    ('2036-04-17', {'message_ordinal': 1})])
def test_schema_never_replaces_local_calendar_role_or_source_validation(token, change):
    a, _, p, _ = grounded('归档校验资料提及'+token+'。', '归档校验', values={'deadline': token},
                          changes={'deadline': change})
    result, _ = discover(p, a)
    obs, = result.partial_observations
    assert obs.description and obs.deadline is None and obs.deadline_text is None
    assert obs.deadline_role == 'UNKNOWN' and obs.verify(a)


def test_profile_is_versioned_without_changing_historical_v2_request():
    from linkloom.semantic_ingestion.extraction import _field_prompt, _field_schema, _recall_prompt, _recall_schema
    a, s, _, _ = grounded('归档校验列入本轮执行。', '归档校验')
    v2 = _field_schema()
    v3 = _recall_schema()
    old_date = v2['properties']['claims']['items']['properties']['task']['properties']['deadline_text']
    new_date = v3['properties']['claims']['items']['properties']['task']['properties']['deadline_text']
    assert 'pattern' not in old_date and 'pattern' in new_date
    assert 'Work existence and field availability are independent' in _recall_prompt(s, {'profile':'task-recall/v3'})
    assert 'Work existence and field availability are independent' not in _field_prompt(s, {'profile':'task-field-grounding/v2'})
