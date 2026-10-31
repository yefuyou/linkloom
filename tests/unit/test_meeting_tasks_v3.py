"""Corpus-independent tests for v3 proposal and human-review boundaries."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.tools.contracts import ToolCall
from linkloom.semantic_ingestion import meeting_tasks_v2 as v2
from linkloom.semantic_ingestion import meeting_tasks_v3 as v3
from linkloom.semantic_ingestion.task_extraction import TaskExtractionConfig, build_meeting_extractor
from tests.unit.test_meeting_tasks_v2 import source, task, ROSTER


def proposal(**kwargs):
    return task(**{'confidence':'HIGH', 'confidence_reason':'Explicit undertaking.', **kwargs})


class Adapter:
    model_id = 'gemini-3.8-flash'
    def __init__(self, rows=()):
        self.rows, self.requests = list(rows), []
    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(action=ModelAction.tool(ToolCall(call_id='offline', tool_id=v3.TOOL_ID,
            arguments={'coverage_complete': True, 'tasks': deepcopy(self.rows)})))


def extract(adapter, artifact=None, **config):
    return build_meeting_extractor(adapter, model_id=adapter.model_id,
        config=TaskExtractionConfig(task_extraction_mode='meeting_v3', **config),
        request_guard=lambda r: True).extract(artifact or source(), ROSTER)


def test_modes_are_explicit_and_v2_contract_unchanged():
    assert TaskExtractionConfig().task_extraction_mode == 'legacy'
    assert v2.TaskExtractionConfig().task_extraction_mode == 'legacy'
    assert 'confidence' not in v2.SCHEMA['properties']['tasks']['items']['properties']
    assert build_meeting_extractor(Adapter(), model_id='gemini-3.8-flash',
        config=TaskExtractionConfig(task_extraction_mode='meeting_v2'), request_guard=lambda r: True).__class__ is v2.MeetingTaskExtractor
    with pytest.raises(ValueError, match='legacy'):
        build_meeting_extractor(Adapter(), model_id='x', config=TaskExtractionConfig(), request_guard=lambda r: True)
    with pytest.raises(ValueError):
        TaskExtractionConfig(task_extraction_mode='unknown')


def test_low_uncertain_commitment_kept_with_exact_multi_evidence_no_date_resolution():
    adapter = Adapter([proposal(confidence='LOW', confidence_reason='The commitment is tentative.',
        adopted=False, scope='UNCERTAIN', end_state='UNCERTAIN')])
    result = extract(adapter)
    candidate, = result.candidates
    assert candidate.confidence == 'LOW' and candidate.review_required
    assert candidate.verify(source()) and len(candidate.evidence) == 2
    assert candidate.deadline.raw_text == 'before the next workshop' and candidate.deadline.resolved_at is None
    assert adapter.requests[0].generation_options.thinking_level == 'low'
    assert result.to_dict()['version'] == 'meeting-tasks/v3'


@pytest.mark.parametrize('state', ['COMPLETED', 'CANCELLED'])
def test_low_does_not_reinstate_explicitly_finished_work(state):
    result = extract(Adapter([proposal(confidence='LOW', confidence_reason='Tentative.', end_state=state)]))
    assert not result.candidates and len(result.excluded) == 1


def test_low_in_meeting_work_stays_excluded():
    result = extract(Adapter([proposal(confidence='LOW', confidence_reason='Tentative.', scope='IN_MEETING')]))
    assert not result.candidates and len(result.excluded) == 1


def test_owner_groups_and_distinct_deliverables_never_host_merge():
    result = extract(Adapter([proposal(), proposal(owner_ids=['Y']), proposal(deliverable_key='another deliverable')]))
    assert len(result.candidates) == 3
    assert all(len(c.member_ids) == 1 for c in result.candidates)
    assert {c.owner_ids for c in result.candidates} == {('person-x',), ('person-y',)}


def test_repeated_same_owner_delivery_merges_all_witnesses_and_never_promotes_low():
    result = extract(Adapter([proposal(), proposal(confidence='LOW', confidence_reason='Acceptance is uncertain.')]))
    item, = result.candidates
    assert len(item.member_ids) == 2 and item.confidence == 'LOW'
    assert item.confidence_reason == 'Acceptance is uncertain.'
    assert len(item.evidence) == 2


def test_unknown_owners_are_not_merged():
    assert len(extract(Adapter([proposal(owner_ids=[]), proposal(owner_ids=[])])).candidates) == 2


@pytest.mark.parametrize('changes,reason', [
    ({'confidence': 'MEDIUM'}, 'INVALID_CONFIDENCE'),
    ({'confidence_reason': ''}, 'INVALID_CONFIDENCE_REASON'),
    ({'evidence': [{'message_ordinal': 999, 'supports': ['work']}]}, 'INVALID_EVIDENCE_HANDLE'),
    ({'deadline': {'raw_text': 'next year', 'kind': 'RELATIVE_TIME'}}, 'DEADLINE_NOT_EXACT_SOURCE'),
    ({'owner_ids': ['I']}, 'UNRESOLVED_OWNER'),
])
def test_low_never_bypasses_source_or_schema_checks(changes, reason):
    row = proposal(confidence='LOW', confidence_reason='Tentative.')
    row.update(changes)
    result = extract(Adapter([row]))
    assert not result.candidates and result.rejected[0]['reason'] == reason


def test_large_meeting_windows_cover_every_complete_utterance():
    artifact = source([('X', f'Line {i} '+ 'x'*3500) for i in range(24)])
    adapter = Adapter()
    result = extract(adapter, artifact, max_input_bytes=32768, window_body_bytes=19000)
    covered = set()
    for request in adapter.requests:
        assert len(v3.canonical(request.user_input).encode()) <= 32768
        data=json.loads(request.user_input.split('\nDATA:\n')[1])
        covered.update(m['ordinal'] for m in data['messages'])
        assert all(len(m['text']) >= 3500 for m in data['messages'])
    assert covered == set(range(24)) and len(result.windows) == len(adapter.requests)


def test_changed_source_fails_before_provider():
    adapter=Adapter()
    extractor=build_meeting_extractor(adapter, model_id=adapter.model_id,
        config=TaskExtractionConfig(task_extraction_mode='meeting_v3'), request_guard=lambda r: True,
        current_source=lambda: source([('X','Changed.')]))
    with pytest.raises(ValueError,match='SOURCE_VERSION_CHANGED'):
        extractor.extract(source(),ROSTER)
    assert not adapter.requests


def test_generic_prompt_contains_no_corpus_examples():
    assert not any(x in v3.PROMPT for x in ('ES2002','ES2003','ES2004','Project Manager','Industrial Designer','remote control'))
    assert 'Different obligations' in v3.PROMPT and 'confidence=LOW' in v3.PROMPT
