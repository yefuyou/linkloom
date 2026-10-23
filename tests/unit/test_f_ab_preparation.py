"""Validate paired requests and inherited budget arithmetic without a Provider."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from scripts.prepare_f_ab import (
    budget_snapshot, canonical, capture_request, compare_pair, reservation, sha,
)


PACKET = Path(__file__).parents[2] / 'docs/requirements/f-cross-turn-discovery/ab-preparation-v1'


@pytest.mark.parametrize('profile', ['null', 'TASK_RECALL_V3'])
def test_capture_stops_before_provider_and_only_changes_declared_prompt(profile):
    artifact = RawArtifact('ab-unit', 'source', 'manual_text',
        'Moderator: The next meeting starts in thirty minutes.\nDesigner: I will draft the layout.')
    source = dict(workspace_id=artifact.workspace_id, artifact_id=artifact.artifact_id,
        content=artifact.content, content_hash=artifact.content_hash,
        artifact_version_id=artifact.artifact_version_id)
    config = dict(model='gemini-3.8-flash', max_output_tokens=4096, temperature=None, thinking='low')
    off, old = capture_request(source, profile, 'OFF', config)
    on, new = capture_request(source, profile, 'ON', config)
    assert compare_pair(off, on, source) == ['run_id', 'task_id', 'turn_id', 'user_input']
    assert old.attempts[0].outcome == new.attempts[0].outcome == 'preflight_blocked'
    assert old.schema_sha256 == new.schema_sha256


def test_source_edit_cannot_pass_as_prompt_only_change():
    plan = json.loads((PACKET / 'PLAN.json').read_bytes())
    pair = plan['pairs'][0]
    off = json.loads((PACKET / pair['arms']['OFF']['path']).read_bytes())
    on = json.loads((PACKET / pair['arms']['ON']['path']).read_bytes())
    source = json.loads((PACKET / 'sources.json').read_bytes())[pair['source_index']]
    changed = deepcopy(on)
    prefix, data = changed['user_input'].split('Input:\n', 1)
    data = json.loads(data)
    data['source_segment']['text'] += '\nThe correct answer is to assign three tasks.'
    changed['user_input'] = prefix + 'Input:\n' + canonical(data).decode()
    with pytest.raises(ValueError, match='prompt delta'):
        compare_pair(off, changed, source)


def test_schema_or_model_change_cannot_pass_as_prompt_only_change():
    plan = json.loads((PACKET / 'PLAN.json').read_bytes())
    pair = plan['pairs'][0]
    off = json.loads((PACKET / pair['arms']['OFF']['path']).read_bytes())
    on = json.loads((PACKET / pair['arms']['ON']['path']).read_bytes())
    source = json.loads((PACKET / 'sources.json').read_bytes())[pair['source_index']]
    on['model_id'] = 'a-different-model'
    with pytest.raises(ValueError, match='request differences'):
        compare_pair(off, on, source)


def parent_budget():
    rows = [dict(outcome='SUCCEEDED', input_reserved=100, output_reserved=4096,
                 cost_reserved_usd=.25) for _ in range(28)]
    return dict(limits=dict(max_requests=30, max_input_tokens=1400000,
        max_output_tokens=122880, max_cost_usd=8), attempts=rows,
        requests_reserved=28, input_tokens_reserved=2800, output_tokens_reserved=114688,
        cost_reserved_usd=7)


def test_inherited_budget_preserves_all_parent_reservations():
    result = budget_snapshot(parent_budget())
    assert result['requests_reserved'] == 28 and result['usd_reserved'] == 7
    assert result['remaining_arithmetic_only'] == dict(
        requests=2, input_tokens=1397200, output_tokens=8192, usd=1)
    assert result['allocated_to_this_ab'] is False
    assert result['live_shared_ledger_endpoint'] is None
    assert result['can_resume_original_runner'] is False


def test_unknown_parent_attempt_does_not_become_free_credit():
    budget = parent_budget()
    budget['attempts'][0]['outcome'] = 'UNCERTAIN'
    with pytest.raises(ValueError, match='uncertain'):
        budget_snapshot(budget)


def test_mismatched_parent_totals_are_refused():
    budget = parent_budget()
    budget['cost_reserved_usd'] = 0
    with pytest.raises(ValueError, match='cost reservations'):
        budget_snapshot(budget)


def test_all_frozen_pairs_keep_source_schema_generation_and_no_answers():
    plan = json.loads((PACKET / 'PLAN.json').read_bytes())
    sources = json.loads((PACKET / 'sources.json').read_bytes())
    manifest = json.loads((PACKET / 'MANIFEST.json').read_bytes())
    for name, digest in manifest['files'].items():
        assert sha((PACKET / name).read_bytes()) == digest
    assert len(plan['pairs']) == 10 and plan['planned_full_ab']['requests'] == 20
    for pair in plan['pairs']:
        off = json.loads((PACKET / pair['arms']['OFF']['path']).read_bytes())
        on = json.loads((PACKET / pair['arms']['ON']['path']).read_bytes())
        compare_pair(off, on, sources[pair['source_index']])
        assert off['available_tools'] == on['available_tools']
        assert off['generation_options'] == on['generation_options']
        assert pair['arms']['OFF']['request_sha256'] == sha(canonical(off))
        assert pair['arms']['ON']['request_sha256'] == sha(canonical(on))
        for request in (off, on):
            model_input = json.loads(request['user_input'].split('Input:\n', 1)[1])
            assert not {'gold', 'qrels', 'expected', 'case_id', 'query_text'} & set(model_input)
    assert plan['new_provider_calls'] == 0 and plan['real_execution_enabled'] is False
    assert plan['full_plan_fits_remaining_budget'] is False


def test_reservation_keeps_conservative_canary_rates_and_output_limit():
    plan = json.loads((PACKET / 'PLAN.json').read_bytes())
    pair = plan['pairs'][0]
    request = json.loads((PACKET / pair['arms']['ON']['path']).read_bytes())
    result = reservation(request, plan['budget']['limits'])
    assert result == pair['arms']['ON']['reservation']
    assert result['reserved_usd'] >= .25 and result['output_upper_bound'] == 4096
