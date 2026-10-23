"""Prompt/grounding boundary tests, never a model recall or precision score."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from linkloom.semantic_ingestion.extraction import (
    MEETING_DISCOURSE_PROMPT_VERSION, TaskDiscoveryProfile,
)
from tests.unit.test_task_observation_discovery import claim, setup, source


FIXTURE = Path(__file__).parents[1] / 'fixtures/f_cross_turn/recorded_cross_turn.json'


def recorded(label):
    return json.loads(FIXTURE.read_text(encoding='utf-8'))[label]


def run(text, claims=(), *, profile=None, opt_in=True):
    artifact = source(text)
    service, provider, _, _ = setup(artifact, claims)
    service.task_profile = profile
    if opt_in:
        service.extractor.prompt_version = MEETING_DISCOURSE_PROMPT_VERSION
    result = service.discover((artifact,), workspace_id=artifact.workspace_id)
    return artifact, result, provider.requests


@pytest.mark.parametrize('profile', [None, *TaskDiscoveryProfile])
def test_explicit_opt_in_only_changes_prompt_not_source_schema_or_budgets(profile):
    text = 'Moderator: The meeting starts in thirty minutes.\nDesigner: Could we discuss a layout?'
    _, before, old_requests = run(text, profile=profile, opt_in=False)
    _, after, new_requests = run(text, profile=profile)
    old, = old_requests
    new, = new_requests
    assert 'MEETING DISCOURSE AND EXACT EVIDENCE CONTRACT' not in old.user_input
    assert 'MEETING DISCOURSE AND EXACT EVIDENCE CONTRACT' in new.user_input
    old_input = json.loads(old.user_input.split('Input:\n', 1)[1])
    new_input = json.loads(new.user_input.split('Input:\n', 1)[1])
    assert old_input['source_segment'] == new_input['source_segment']
    assert new_input['source_segment']['text'] == text
    assert old_input.get('task_discovery') == new_input.get('task_discovery')
    assert old.available_tools == new.available_tools
    assert old.generation_options == new.generation_options
    assert old.model_id == new.model_id
    assert before.extractions[0].receipt.schema_sha256 == after.extractions[0].receipt.schema_sha256
    assert before.extractions[0].receipt.prompt_sha256 != after.extractions[0].receipt.prompt_sha256
    assert before.extractions[0].receipt.config_sha256 != after.extractions[0].receipt.config_sha256
    assert not after.valid_observations and not after.partial_observations
    assert new.user_input.count('MEETING DISCOURSE AND EXACT EVIDENCE CONTRACT') == 1


@pytest.mark.parametrize('label,rejected', [('B_FLASH', 1), ('D_ARGON', 2)])
@pytest.mark.parametrize('opt_in', [False, True])
def test_real_saved_bad_quotes_still_rejected_without_normalization(label, rejected, opt_in):
    row = recorded(label)
    artifact, result, requests = run(row['source'], row['claims'],
        profile=TaskDiscoveryProfile.TASK_RECALL_V3, opt_in=opt_in)
    assert len(requests) == 1
    assert len(result.partial_observations) == 1
    assert not result.valid_observations
    observation, = result.partial_observations
    assert observation.owner == 'industrial designer'  # A literal role, never a guessed person.
    assert observation.deadline is None and observation.authority == 'SUPPORTING_ONLY'
    assert observation.verify(artifact)
    assert len(result.failures) == rejected
    assert {f.reason for f in result.failures} == {'TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS'}
    assert [c.to_dict() for c in result.extractions[0].claims][1]['task'] == row['claims'][1]['task']


def test_constructed_contiguous_marketing_quote_is_structurally_usable():
    # Counterfactual, NOT a new model response or a repair to saved evidence.
    row = recorded('B_FLASH')
    original = deepcopy(row)
    candidate = deepcopy(row['claims'][1])
    part, = candidate['task']['description_parts']
    start = row['source'].index('Um and uh marketing executive ,')
    end = row['source'].index(part, start) + len(part)
    exact = row['source'][start:end]
    assert '\nami-nxt/' in exact and '\\n' not in exact
    candidate['task']['text'] = exact
    candidate['task']['field_bindings'][0]['quote'] = exact
    artifact, result, _ = run(row['source'], [candidate], profile=TaskDiscoveryProfile.TASK_RECALL_V3)
    observation, = result.partial_observations
    assert observation.owner == 'marketing executive'
    assert observation.description == part
    assert observation.deadline is None and observation.verify(artifact)
    assert not result.failures and row == original


def test_constructed_single_work_excerpt_keeps_cross_turn_owner_unknown():
    row = recorded('B_FLASH')
    candidate = deepcopy(row['claims'][1])
    part, = candidate['task']['description_parts']
    candidate['task']['text'] = part
    candidate['task']['owner'] = None
    for binding in candidate['task']['field_bindings']:
        binding.update(value=None, role='UNKNOWN', state='NOT_STATED', quote='')
    artifact, result, _ = run(row['source'], [candidate], profile=TaskDiscoveryProfile.TASK_RECALL_V3)
    observation, = result.partial_observations
    assert observation.owner is None and observation.deadline is None
    assert observation.verify(artifact)


@pytest.mark.parametrize('text', [
    '建议完成索引测试。', '如果预算批准，完成索引测试。',
    '同事说：“完成索引测试。”', '不需要完成索引测试。',
    '任务取消：完成索引测试。', '今天仅讨论完成索引测试。',
    'Perhaps we could deliver the layout.', 'If funded, deliver the layout.',
    'The task was cancelled: deliver the layout.',
])
def test_opt_in_does_not_release_legacy_non_asserted_negative_examples(text):
    work = '索引测试' if '索引测试' in text else 'deliver the layout'
    _, result, _ = run(text, [claim(work, work)])
    assert not result.valid_observations and not result.partial_observations
    assert any(f.reason == 'NON_ASSERTED_TASK' for f in result.failures)


@pytest.mark.parametrize('kind', ['FACT', 'QUESTION', 'PROPOSAL', 'DECISION', 'OPINION'])
def test_discussion_or_schedule_non_task_claim_cannot_be_promoted_by_candidate(kind):
    text = 'The next meeting starts in thirty minutes; perhaps we could discuss the layout.'
    c = claim(text, 'discuss the layout', kind=kind)
    c['task'] = None
    _, result, _ = run(text, [c])
    assert not result.valid_observations and not result.partial_observations
    # This tests a supplied classification, not whether a model classifies it correctly.


def test_schedule_misclassified_as_asserted_work_remains_a_known_limitation():
    # Deliberately document the unsolved semantic false positive. No regex added
    # to conceal it, and a fake-model test cannot prove the prompt fixes it.
    text = "the next meeting's gonna be in thirty minutes ."
    _, result, _ = run(text, [claim(text, text)])
    observation, = result.partial_observations
    assert observation.deadline is None
    assert observation.deadline_role == 'UNKNOWN'
    assert observation.authority == 'SUPPORTING_ONLY'


@pytest.mark.parametrize('state', ['CONDITIONAL', 'REPORTED', 'WITHDRAWN', 'NOT_STATED'])
def test_field_profile_requires_asserted_work_even_with_candidate(state):
    c = deepcopy(recorded('B_FLASH')['claims'][0])
    c['task']['work_state'] = state
    _, result, _ = run(c['task']['text'], [c], profile=TaskDiscoveryProfile.TASK_RECALL_V3)
    assert not result.valid_observations and not result.partial_observations
    assert any(f.reason == 'NON_ASSERTED_TASK' for f in result.failures)


def test_canonical_message_owner_cannot_be_imported_from_another_message():
    text = ('[2029-06-01T09:00:00Z] 主持人: 林枫负责下一项。\n'
            '[2029-06-01T09:01:00Z] 记录员: 完成索引测试。')
    c = deepcopy(recorded('B_FLASH')['claims'][0])
    c['task'].update(text=text, description_parts=['索引测试'], owner='林枫')
    for binding in c['task']['field_bindings']:
        binding.update(task_quote='索引测试', message_ordinal=0)
        if binding['field'] == 'owner':
            binding.update(value='林枫', quote='林枫负责下一项。')
    artifact, result, _ = run(text, [c], profile=TaskDiscoveryProfile.TASK_RECALL_V3)
    observation, = result.partial_observations
    assert observation.owner is None and observation.verify(artifact)
