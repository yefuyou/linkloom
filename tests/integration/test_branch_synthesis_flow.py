"""Snapshot wiring of the branch answer with a Mock Investigator (no model, no network)."""
import hashlib

import pytest

from linkloom.ui.branch_budget import CANCELLED, HARD_ABORT
from linkloom.ui.parallel_investigation import ParallelInvestigation
from linkloom.ui.projection import WorkspacePresentationContext
from linkloom.ui.task_provider import TaskProviderLimits

LIMITS = TaskProviderLimits(14, 2_000_000, 60_000, 3.0, 600, 10, .01)


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


def _job(tmp_path, rows, gaps=None, validate=lambda: None):
    job = ParallelInvestigation(tmp_path / 'run', 'run-1', 'query', WorkspacePresentationContext('Test', 2),
                                LIMITS, validate, [('Q1?', None), ('Q2?', None)], None, None, None, {}, {}, 'w')
    job.outcome = dict(status='COMPLETE' if not gaps else 'PARTIAL', results={r['run']['branch']: r for r in rows},
                       gaps=gaps or {}, unstarted=0)
    return job


def _rows():
    return [_row('branch-1', 'Q1?', 'A1', _item('s1', 'alpha', 1)),
            _row('branch-2', 'Q2?', 'A2', _item('s2', 'beta', 2))]


def test_both_branches_pass_gives_complete_answer_with_snapshot_citations(tmp_path):
    snapshot = _job(tmp_path, _rows()).snapshot()
    answer = snapshot['parallel_investigation']['answer']
    assert answer['status'] == 'COMPLETE' and answer['published'] is True and answer['conflicts'] == []
    known = {row['id'] for row in snapshot['evidence']}
    assert [set(a['citations']) <= known and bool(a['citations']) for a in answer['answers']] == [True, True]


def test_repeated_snapshots_return_the_same_answer(tmp_path):
    job = _job(tmp_path, _rows())
    assert job.snapshot()['parallel_investigation']['answer'] == job.snapshot()['parallel_investigation']['answer']


def test_gap_branch_gives_partial_with_unknown_question(tmp_path):
    answer = _job(tmp_path, _rows()[:1], gaps={'branch-2': 'FAILED'}).snapshot()['parallel_investigation']['answer']
    assert answer['status'] == 'PARTIAL'
    assert [(u['question'], u['reason']) for u in answer['unknowns']] == [('Q2?', 'BRANCH_FAILED')]


def test_same_plan_question_with_different_decisions_is_a_conflict_without_winner(tmp_path):
    rows = [_row('branch-1', 'Q1?', 'X', _item('s1', 'alpha', 1), subject='Same?'),
            _row('branch-2', 'Q2?', 'Y', _item('s2', 'beta', 2), subject='Same?')]
    answer = _job(tmp_path, rows).snapshot()['parallel_investigation']['answer']
    assert answer['status'] == 'PARTIAL' and answer['published'] is True
    (conflict,) = answer['conflicts']
    assert [(s['branch'], s['value']) for s in conflict['sides']] == [('branch-1', 'X'), ('branch-2', 'Y')]
    assert not {'winner', 'chosen'} & set(conflict)


def test_forged_quote_from_mock_investigator_is_rejected_with_fallback(tmp_path):
    rows = _rows()
    rows[0]['evidence'][0]['source']['quote'] = 'forged text'
    answer = _job(tmp_path, rows).snapshot()['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_QUOTE_HASH_MISMATCH'
    assert answer['answers'] == [] and answer['evidence'] and answer['unknowns']


def test_stale_hash_from_mock_investigator_is_rejected_with_fallback(tmp_path):
    rows = _rows()
    rows[0]['conclusion']['evidence'][0]['quote_sha256'] = hashlib.sha256(b'old version').hexdigest()
    answer = _job(tmp_path, rows).snapshot()['parallel_investigation']['answer']
    assert answer['published'] is False and answer['fallback_reason'] == 'AGGREGATE_CITATION_MISSING'
    assert answer['answers'] == [] and answer['evidence']


@pytest.mark.parametrize('kind', [HARD_ABORT, CANCELLED])
def test_withheld_states_carry_no_answer(tmp_path, kind):
    job = _job(tmp_path, _rows())
    assert 'answer' in job.snapshot()['parallel_investigation']
    job.pool.stop(kind, 'STOPPED_FOR_TEST')
    assert 'answer' not in job.snapshot()['parallel_investigation']


def test_authorization_loss_after_publication_drops_the_answer(tmp_path):
    state = dict(valid=True)

    def validate():
        if not state['valid']:
            raise RuntimeError('source changed')

    job = _job(tmp_path, _rows(), validate=validate)
    assert job.snapshot()['parallel_investigation']['answer']['published'] is True
    state['valid'] = False
    lost = job.snapshot()
    assert lost['parallel_investigation']['status'] == 'ABORTED' and 'answer' not in lost['parallel_investigation']
    assert not lost.get('evidence')


def test_running_job_has_no_answer(tmp_path):
    job = _job(tmp_path, _rows())
    job.outcome = None
    assert 'answer' not in job.snapshot()['parallel_investigation']
