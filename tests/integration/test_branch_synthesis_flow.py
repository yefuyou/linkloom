"""Snapshot wiring of the task answer with reviewed task rows (no model, no network)."""
import hashlib

from linkloom.orchestration.snapshot import ABORTED, CANCELLED, render_snapshot
from linkloom.orchestration.state import ACCEPTED, FAILED
from linkloom.ui.branch_synthesis import identity_of, merge_evidence, plan_comparison, resolve_pairs, synthesize_reviewed
from linkloom.ui.projection import WorkspacePresentationContext

import pytest


def _item(source, quote, line):
    return dict(source_id=source, source_version='v1', line_start=line, line_end=line,
                quote_sha256=hashlib.sha256(quote.encode()).hexdigest(), source=dict(quote=quote))


def _row(name, question, value, item, claim_value=None, subject=None):
    return dict(run=dict(branch=name), evidence=[item], conclusion=dict(
        question=question, decision=dict(value=value, status='approved'), unresolved_items=[],
        uncertainty=dict(status='none', statement=None),
        evidence=[{k: item[k] for k in ('source_id', 'source_version', 'line_start', 'line_end', 'quote_sha256')}],
        claim_key=dict(subject=subject or question, relation='subgoal_question'),
        claim_value=claim_value or value))


def _values(rows, gaps=None, validate=lambda: None, *, extra_tasks=(), verdicts=None):
    """What the graph's synthesize node leaves in the LangGraph state, then rendered for the product."""
    gaps = gaps or {}
    tasks = {row['run']['branch']: dict(id=row['run']['branch'], description=row['conclusion']['question'],
        depends_on=[], status=ACCEPTED, depth=1, round=0, spawn_reason='test', failure=None, attempt=1,
        result_ref=None) for row in rows}
    for task_id, question in extra_tasks:
        tasks[task_id] = dict(id=task_id, description=question, depends_on=[], status=FAILED, depth=1, round=0,
                              spawn_reason='test', failure='WORKER_RUNTIME_FAILED', attempt=1, result_ref=None)
    questions = {task_id: task['description'] for task_id, task in tasks.items()}
    merged = merge_evidence(rows)
    comparison = resolve_pairs(plan_comparison(rows), verdicts)
    answer = synthesize_reviewed(rows, gaps, 0, merged, questions, source_check=validate, review_id='run-1-aggregate',
                                 comparison=comparison)
    return dict(status=answer['status'] if answer.get('published') else 'UNKNOWN', tasks=tasks,
                rows={row['run']['branch']: row for row in rows}, answer=answer, trace=[], round=1)


def _snapshot(rows, gaps=None, validate=lambda: None, **kwargs):
    return render_snapshot('run-1', 'query', WorkspacePresentationContext('Test', 2),
                           _values(rows, gaps, validate, **kwargs), running=False)


def _rows():
    return [_row('branch-1', 'Q1?', 'A1', _item('s1', 'alpha', 1)),
            _row('branch-2', 'Q2?', 'A2', _item('s2', 'beta', 2))]


def test_both_branches_pass_gives_complete_answer_with_snapshot_citations(tmp_path):
    snapshot = _snapshot(_rows())
    answer = snapshot['parallel_investigation']['answer']
    assert answer['status'] == 'COMPLETE' and answer['published'] is True and answer['conflicts'] == []
    known = {row['id'] for row in snapshot['evidence']}
    assert [set(a['citations']) <= known and bool(a['citations']) for a in answer['answers']] == [True, True]


def test_repeated_snapshots_return_the_same_answer():
    values = _values(_rows())
    workspace = WorkspacePresentationContext('Test', 2)
    first = render_snapshot('run-1', 'query', workspace, values, running=False)
    assert first['parallel_investigation']['answer'] == render_snapshot(
        'run-1', 'query', workspace, values, running=False)['parallel_investigation']['answer']


def test_gap_branch_gives_partial_with_unknown_question(tmp_path):
    answer = _snapshot(_rows()[:1], gaps={'branch-2': 'FAILED'}, extra_tasks=[('branch-2', 'Q2?')])['parallel_investigation']['answer']
    assert answer['status'] == 'PARTIAL'
    assert [(u['question'], u['reason']) for u in answer['unknowns']] == [('Q2?', 'BRANCH_FAILED')]


def test_same_plan_question_with_different_decisions_is_a_conflict_without_winner(tmp_path):
    rows = [_row('branch-1', 'Q1?', 'X', _item('s1', 'alpha', 1), subject='Same?'),
            _row('branch-2', 'Q2?', 'Y', _item('s2', 'beta', 2), subject='Same?')]
    # the same structured identity does not decide it: the values are model text, so the comparison judges them
    verdicts = {('branch-1', 'branch-2'): ('CONFLICT', 'semantic_comparison')}
    outcome = _snapshot(rows, verdicts=verdicts)['parallel_investigation']
    answer = outcome['answer']
    assert answer['status'] == 'PARTIAL' and answer['published'] is True
    (conflict,) = answer['conflicts']
    assert (conflict['subject'], conflict['relation']) == ('Same?', 'subgoal_question')
    assert [(s['branch'], s['value']) for s in conflict['sides']] == [('branch-1', 'X'), ('branch-2', 'Y')]
    assert not {'winner', 'chosen'} & set(conflict)
    assert outcome['conflict_check_status'] == 'CONFLICT' and outcome['conflict_check_verified'] is True


def test_unavailable_conflict_check_keeps_the_real_completeness_and_is_disclosed_as_unverified(tmp_path):
    rows = [_row('branch-1', 'Q1?', 'X', _item('s1', 'alpha', 1), subject='Same?'),
            _row('branch-2', 'Q2?', 'Y', _item('s2', 'beta', 2), subject='Same?')]
    outcome = _snapshot(rows)['parallel_investigation']  # no comparison result at all
    answer = outcome['answer']
    assert answer['status'] == 'COMPLETE' and answer['conflicts'] == []  # completeness is not rewritten
    assert outcome['status'] == 'COMPLETE'
    assert outcome['conflict_check_status'] == 'UNAVAILABLE' and outcome['conflict_check_verified'] is False
    assert answer['conflict_check']['cross_branch_compared'] is False
    assert 'UNAVAILABLE' in answer['text']


def test_forged_quote_from_mock_investigator_is_rejected_with_fallback(tmp_path):
    rows = _rows()
    rows[0]['evidence'][0]['source']['quote'] = 'forged text'
    answer = _snapshot(rows)['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_QUOTE_HASH_MISMATCH'
    assert answer['answers'] == [] and answer['evidence'] and answer['unknowns']


def test_branch_conclusion_citing_an_identity_the_branch_did_not_read_gets_no_citation_and_is_withheld(tmp_path):
    # The conclusion's hash matches no evidence the branch carried, so the answer has an empty
    # citation list. This fails at the schema check (citation missing), before any hash or source check.
    rows = _rows()
    rows[0]['conclusion']['evidence'][0]['quote_sha256'] = hashlib.sha256(b'old version').hexdigest()
    answer = _snapshot(rows)['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_CITATION_MISSING'
    assert answer['answers'] == [] and answer['evidence']


def test_quote_changed_after_branch_review_is_rejected_by_hash_check(tmp_path):
    # Identity (with the original hash) is carried and cited, but the quote text no longer hashes to it.
    rows = _rows()
    rows[1]['evidence'][0]['source']['quote'] = 'beta, edited after the branch review'
    answer = _snapshot(rows)['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_QUOTE_HASH_MISMATCH'
    assert answer['answers'] == [] and answer['evidence']
    assert {u['reason'] for u in answer['unknowns']} == {'AGGREGATE_QUOTE_HASH_MISMATCH'}


def test_source_version_change_during_review_is_rejected_and_withholds_answer(tmp_path):
    def validate():
        raise RuntimeError('source version changed')  # the aggregate review re-checks every source version

    answer = _snapshot(_rows(), validate=validate)['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_SOURCE_CHANGED'
    assert answer['answers'] == [] and answer['evidence']


def test_citations_map_back_to_the_evidence_their_own_branch_read(tmp_path):
    rows = [_row('branch-1', 'Q1?', 'A1', _item('s1', 'alpha', 1)),
            _row('branch-2', 'Q2?', 'A2', _item('s2', 'beta', 2)),
            _row('branch-3', 'Q3?', 'A3', _item('s3', 'gamma', 3))]
    snapshot = _snapshot(rows)
    by_id = {row['id']: identity_of(row) for row in snapshot['evidence']}
    answer = snapshot['parallel_investigation']['answer']
    assert [a['branch'] for a in answer['answers']] == ['branch-1', 'branch-2', 'branch-3']
    by_branch = {r['run']['branch']: {identity_of(i) for i in r['evidence']} for r in rows}
    for a in answer['answers']:
        assert a['citations'] and {by_id[c] for c in a['citations']} == by_branch[a['branch']]


def test_template_style_distinct_keys_report_no_cross_branch_comparison(tmp_path):
    answer = _snapshot(_rows())['parallel_investigation']['answer']
    assert answer['status'] == 'COMPLETE'
    assert answer['conflict_check']['cross_branch_compared'] is False
    assert answer['conflict_check']['compared'] == [] and answer['conflicts'] == []
    assert 'conflict' not in answer['text'].lower()


@pytest.mark.parametrize('kind', [ABORTED, CANCELLED])
def test_withheld_states_carry_no_answer(kind):
    values = _values(_rows())
    assert 'answer' in render_snapshot('run-1', 'query', WorkspacePresentationContext('Test', 2), values,
                                       running=False)['parallel_investigation']
    withheld = render_snapshot('run-1', 'query', WorkspacePresentationContext('Test', 2), values, running=False,
                               withheld=(kind, 'STOPPED_FOR_TEST'))
    assert 'answer' not in withheld['parallel_investigation'] and not withheld.get('evidence')


def test_authorization_loss_after_publication_drops_the_answer():
    values = _values(_rows())
    workspace = WorkspacePresentationContext('Test', 2)
    assert render_snapshot('run-1', 'query', workspace, values, running=False)[
        'parallel_investigation']['answer']['published'] is True
    lost = render_snapshot('run-1', 'query', workspace, values, running=False,
                           withheld=(ABORTED, 'TASK_SOURCE_AUTHORIZATION_CHANGED'))
    assert lost['parallel_investigation']['status'] == 'ABORTED' and 'answer' not in lost['parallel_investigation']
    assert not lost.get('evidence')


def test_running_run_has_no_answer():
    values = dict(_values(_rows()), status='RUNNING', answer=None)
    running = render_snapshot('run-1', 'query', WorkspacePresentationContext('Test', 2), values, running=True)
    assert 'answer' not in running['parallel_investigation']
    assert running['parallel_investigation']['status'] == 'RUNNING'
