"""Offline Q3 replay. Item assignments and judgments are test annotations, not historical fields."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from linkloom.orchestration.coverage import review_operands, validated_judgments
from linkloom.ui.branch_synthesis import merge_evidence, plan_comparison, resolve_pairs, synthesize_reviewed


@pytest.fixture
def replay(tmp_path):
    fixture = Path(__file__).parents[1] / 'fixtures/q152_answer_coverage.json'
    saved = json.loads(fixture.read_text(encoding='utf-8'))
    meta = saved['sources']
    for path, source in meta.items():
        (tmp_path / path).write_bytes(source['content'].encode())
    deps = SimpleNamespace(vault=tmp_path, source_versions=meta)
    rows = {r['run']['branch']: r for r in saved['rows']}
    tasks = {t['id']: t for t in saved['tasks']}
    assert saved['run_id'] == 'ui-parallel-6135165be798' and saved['status'] == 'PARTIAL'
    assert all('item_claims' not in row['conclusion'] for row in rows.values())
    items = dict(Q1='What replaced Meridian A?', Q2='Who owns Cobalt B?',
                 Q3='Who is Mira Chen’s direct leader?', Q4='When did the replacement take effect?')
    annotations = [('task-2', 'Q1', 'Cobalt B', 'meeting-5.md'),
                   ('task-2', 'Q2', 'Mira Chen', 'meeting-2.md'),
                   ('task-3', 'Q3', 'Kai Wu', 'meeting-3.md'),
                   ('task-2', 'Q4', '2025-02-14', 'meeting-1.md')]
    for branch, item_id, fact, source_name in annotations:
        row = rows[branch]
        assert fact in row['conclusion']['decision']['value']
        [citation] = [e for e in row['evidence'] if e['source_name'] == source_name]
        tasks[branch].setdefault('item_ids', []).append(item_id)
        row['conclusion'].setdefault('item_claims', []).append(
            dict(item_id=item_id, claim_id=branch + ':' + item_id, fact=fact, evidence=[citation]))
    return deps, dict(rows=rows, tasks=tasks, required_items=items)


def draft(deps, state, edit=lambda rows: None, *, legacy=False):
    tasks = state['tasks']
    rows = list(state['rows'].values())
    evidence = merge_evidence(rows)
    operands = review_operands(deps, state, evidence)
    judgments = [dict(item_id=k[0], claim_id=k[1], citation_id=k[2], verdict='SUPPORTS',
                      supporting_quote=v['quote'], explanation='Manually annotated offline.')
                 for k,v in operands.items()]
    edit(judgments)
    options = {} if legacy else dict(items=state['required_items'],
        coverage=validated_judgments(operands, judgments), expected_coverage=tuple(operands))
    comparison = resolve_pairs(plan_comparison(rows, {k:t['depends_on'] for k,t in tasks.items()}))
    return synthesize_reviewed(rows, {}, 0, evidence, comparison=comparison, **options)


def test_real_q3_replay_resolves_only_aggregate_abstention(replay):
    deps, state = replay
    before = deepcopy(state)
    old = draft(deps, state, legacy=True)
    assert old['status'] == 'PARTIAL' and old['unknowns'][0]['reason'] == 'BRANCH_NO_DECISION'
    result = draft(deps, state)
    assert result['published'] and result['status'] == 'COMPLETE' and result['unknowns'] == []
    assert {r['item_id']: r['text'] for r in result['answers']} == dict(
        Q1='Cobalt B', Q2='Mira Chen', Q3='Kai Wu', Q4='2025-02-14')
    assert {r['support'] for r in result['answers']} == {'CITATION_VERIFIED'}
    assert len({c for r in result['answers'] for c in r['citations']}) == 4
    assert state == before and state['tasks']['task-1']['failure'] == 'NO_DECISION'


@pytest.mark.parametrize('field,value', [
    ('verdict', 'IRRELEVANT'), ('verdict', 'CONTRADICTS'), ('verdict', 'INSUFFICIENT'),
    ('verdict', 'AMBIGUOUS'), ('verdict', 'SUPPORTED'), ('item_id', 'foreign'),
    ('claim_id', 'forged'), ('citation_id', 'citation-999'), ('supporting_quote', 'invented'),
])
def test_ineligible_judgment_does_not_cover_item(replay, field, value):
    def edit(rows):
        next(row for row in rows if row['item_id'] == 'Q3')[field] = value
    result = draft(*replay, edit)
    assert result['status'] == 'PARTIAL'
    assert {r['item_id'] for r in result['unknowns']} == {'Q3'}
    assert {r['item_id'] for r in result['answers']} == {'Q1', 'Q2', 'Q4'}


def test_no_review_or_tampered_source_never_completes(replay):
    assert draft(*replay, list.clear)['status'] == 'UNKNOWN'
    deps, state = replay
    claim = state['rows']['task-3']['conclusion']['item_claims'][0]
    (deps.vault / claim['evidence'][0]['relative_path']).write_text('modified', encoding='utf-8')
    result = draft(deps, state)
    assert result['status'] == 'PARTIAL'
    assert [row['item_id'] for row in result['unknowns']] == ['Q3']


@pytest.mark.parametrize('value,expected', [('Kai Wu', 'COMPLETE'), ('Another leader', 'PARTIAL')])
def test_duplicate_or_conflicting_supported_value(replay, value, expected):
    deps, state = replay
    claims = state['rows']['task-3']['conclusion']['item_claims']
    claims.append(dict(claims[0], claim_id='task-3:extra', fact=value))
    result = draft(deps, state)
    assert result['status'] == expected
    assert len([r for r in result['answers'] if r['item_id'] == 'Q3']) == (expected == 'COMPLETE')
    assert bool(result['conflicts']) == (expected == 'PARTIAL')

@pytest.mark.parametrize('quote,verdict', [
    ('Mira Chen attended a meeting about Cobalt B.', 'IRRELEVANT'),
    ('Mira Chen is not the owner of Cobalt B.', 'CONTRADICTS'),
    ('Mira Chen is the owner of Cobalt B.', 'SUPPORTS'),
    ('Mira Chen owns Cobalt B as of 2024 only.', 'INSUFFICIENT'),
    ('Kai Wu reports directly to Mira Chen.', 'CONTRADICTS'),
    ('Mira Chen reports to Kai Wu.', 'INSUFFICIENT'),
    ('Lee reports directly to Kai Wu.', 'IRRELEVANT'),
    ('Mira Chen reports directly to Kai Wu.', 'SUPPORTS'),
])
def test_scripted_semantic_verdict_does_not_confuse_quote_integrity_with_support(replay, quote, verdict):
    import hashlib

    deps, state = replay
    item = 'Q2' if 'Cobalt B' in quote else 'Q3'
    branch = 'task-2' if item == 'Q2' else 'task-3'
    row = state['rows'][branch]
    claim = next(c for c in row['conclusion']['item_claims'] if c['item_id'] == item)
    [citation] = claim['evidence']
    data = quote.encode()
    digest = hashlib.sha256(data).hexdigest()
    citation.update(quote_sha256=digest, content_sha256=digest)
    citation['source']['quote'] = quote
    (deps.vault / citation['relative_path']).write_bytes(data)

    def edit(rows):
        judgment = next(r for r in rows if r['item_id'] == item)
        assert judgment['supporting_quote'] == quote
        judgment['verdict'] = verdict

    result = draft(deps, state, edit)
    assert (item in {r['item_id'] for r in result['answers']}) == (verdict == 'SUPPORTS')
    assert (item in {r['item_id'] for r in result['unknowns']}) == (verdict != 'SUPPORTS')


def test_unassigned_claim_cannot_cover_an_item(replay):
    deps, state = replay
    state['tasks']['task-3']['item_ids'] = []
    result = draft(deps, state)
    assert result['status'] == 'PARTIAL'
    assert [r['item_id'] for r in result['unknowns']] == ['Q3']
