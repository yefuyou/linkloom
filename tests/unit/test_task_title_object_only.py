"""OBJECT_ONLY is a v2 model declaration, grounded by exact source witnesses."""
from dataclasses import replace
from copy import deepcopy

import pytest

from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import (
    TaskTitleBinding, TaskObservation, TaskProposal,
    PRODUCT_TASK_FIELDS_VERSION, PRODUCT_TASK_FIELDS_V2,
)
from tests.unit.test_product_task_fields import A, B, artifact, proposal, run
from tests.unit.test_product_task_stages import annotations, core, execute


def object_only(quote):
    return {'state': 'OBJECT_ONLY', 'object_quote': quote}


@pytest.mark.parametrize('text,quote', [
    ('报告由某人负责。', '报告'),
    ('迁移方案待交付。', '迁移方案'),
    ('Report assigned to Lee.', 'Report'),
    # One lexical English word satisfies the explicit one-word minimum,
    # including a single letter; no dictionary or semantic word list is used.
    ('X is the assigned deliverable.', 'X'),
    ('Prepare release-notes.', 'release-notes'),
])
def test_object_only_uses_unique_nominal_source_title_without_a_declared_action(text, quote):
    a, _, provider, _, _, result = execute([core(text)],
        [{'title_binding': object_only(quote)}], a=artifact(text))
    observation = result.candidate_results[0].candidate.task_observation
    assert observation.description == text
    assert observation.title == quote and observation.title_needs_edit is False
    assert observation.title_binding == TaskTitleBinding(quote, 'OBJECT_ONLY', quote, '')
    assert observation.grounding_policy_version == PRODUCT_TASK_FIELDS_V2
    assert observation.verify(a) and len(provider.requests) == 2


@pytest.mark.parametrize('text,quote', [
    ('报由某人负责。', '报'),
    ('报告由某人负责。', ''),
    ('报告  按时交付。', '  '),
    ('报告；。请确认。', '；。'),
    ('报告交付后更新报告。', '报告'),
    ('ababab is the deliverable.', 'abab'),
    ('哈哈哈待交付。', '哈哈'),
    ('报告由某人负责。', '方案'),
    ('Report assigned to Lee.', 'report'),
])
def test_bad_object_witness_falls_back_without_discarding_core_work(text, quote):
    a, _, provider, _, _, result = execute([core(text)],
        [{'title_binding': object_only(quote)}], a=artifact(text))
    observation = result.candidate_results[0].candidate.task_observation
    assert observation.description == text
    assert observation.title == text and observation.title_needs_edit is True
    assert observation.verify(a) and len(provider.requests) == 2


@pytest.mark.parametrize('mutation', [
    {'text': 'different'},
    {'text': ''},
    {'action_quote': '负责'},
    {'object_quote': None},
    {'unexpected': 'field'},
])
def test_malformed_object_only_is_a_local_title_failure_not_a_task_or_field_failure(mutation):
    payload = annotations()
    payload['title_binding'] = {**object_only('联调报告'), **mutation}
    a, _, _, _, _, result = execute([core()], [payload])
    observation = result.candidate_results[0].candidate.task_observation
    assert observation.description == A and observation.owner == '李明'
    assert observation.deadline is None and observation.deadline_text == '10/12'
    assert observation.title == A and observation.title_needs_edit
    assert 'TITLE_STAGE_INVALID' in observation.validation_issues
    assert 'FIELD_STAGE_FAILED' not in observation.validation_issues
    assert observation.verify(a)


def test_sparse_object_title_cold_record_is_canonical_and_replays_without_provider_calls():
    a = artifact(A + B)
    fields = [annotations(A), annotations(B, owner='王芳', token='10/16')]
    for payload in fields:
        payload['title_binding'] = object_only('联调报告')
    _, context, provider, _, pipeline, result = execute([core(A), core(B)], fields, a=a)
    observations = [entry.candidate.task_observation for entry in result.candidate_results]
    assert [(o.title, o.title_needs_edit, o.owner, o.deadline_text) for o in observations] == [
        ('联调报告', False, '李明', '10/12'), ('联调报告', False, '王芳', '10/16')]
    assert all(o.deadline is None and o.deadline_precision == 'MONTH_DAY' and o.verify(a) for o in observations)
    encoded = result.extraction.to_dict()
    expected = {'text': '联调报告', 'state': 'OBJECT_ONLY', 'object_quote': '联调报告', 'action_quote': ''}
    assert all(c['task']['title_binding'] == expected for c in encoded['claims'])
    # Keep the exact sparse model answer in audit, separate from normalized claims.
    assert all(item['payload']['title_binding'] == object_only('联调报告')
               for item in encoded['task_stages']['stage_two'])
    cold = SemanticExtractionResult.from_dict(deepcopy(encoded))
    assert cold.to_dict() == encoded
    replayed = pipeline.replay_extraction(encoded, parse_source_text(a).segments[0], a, context)
    assert replayed.candidate_results == result.candidate_results and len(provider.requests) == 3
    for observation in observations:
        restored = TaskObservation.from_dict(observation.to_dict())
        assert restored == observation and restored.verify(a)


def test_object_only_does_not_change_legacy_v1_title_contract():
    payload = proposal()
    payload['task']['title_binding'] = {
        'text': '联调报告', 'state': 'OBJECT_ONLY', 'object_quote': '联调报告', 'action_quote': ''}
    _, _, provider, _, result = run([payload])
    assert result.extraction.failure_code is not None and not result.extraction.claims
    assert len(provider.requests) == 1
    with pytest.raises(ValueError, match='grounding contract'):
        TaskProposal.from_dict(payload['task'])

    fields = annotations(); fields['title_binding'] = object_only('联调报告')
    _, _, _, _, _, result = execute([core()], [fields])
    observation = result.candidate_results[0].candidate.task_observation
    with pytest.raises(ValueError, match='title or grounding policy'):
        replace(observation, grounding_policy_version=PRODUCT_TASK_FIELDS_VERSION)


def test_object_title_does_not_rescue_ungrounded_core_or_expand_stage_one_or_sampling():
    row = core(); row['task']['text'] = 'invented work'; row['task']['description_parts'] = ['invented work']
    _, _, provider, _, _, result = execute([row])
    assert result.candidate_results[0].candidate.task_observation is None
    assert len(provider.requests) == 1

    fields = annotations(); fields['title_binding'] = object_only('联调报告')
    _, _, provider, _, _, result = execute([core()], [fields])
    assert all(request.generation_options.temperature is None for request in provider.requests)
    title_schema = provider.requests[1].available_tools[0].input_schema['properties']['title_binding']
    assert 'OBJECT_ONLY' in title_schema['properties']['state']['enum']
    assert set(title_schema['required']) == {'state', 'object_quote'}
    assert result.candidate_results[0].candidate.task_observation.title == '联调报告'
