import copy
import hashlib

import pytest

from linkloom.ui import branch_synthesis as bs


def _item(source, quote, start=1):
    return dict(source_id=source, source_version='v1', line_start=start, line_end=start,
                quote_sha256=hashlib.sha256(quote.encode()).hexdigest(), source=dict(quote=quote))


def _row(name, question, value, items, key=None, claim_value=None, unresolved=(), status='approved'):
    return dict(
        run=dict(branch=name),
        evidence=items,
        conclusion=dict(
            question=question, decision=dict(value=value, status=status),
            unresolved_items=list(unresolved), uncertainty=dict(status='none', statement=None),
            evidence=[{k: i[k] for k in ('source_id', 'source_version', 'line_start', 'line_end',
                                         'quote_sha256')} for i in items],
            claim_key=key, claim_value=claim_value))


def _setup():
    a, b = _item('s1', 'alpha fact'), _item('s2', 'beta fact', 3)
    rows = [_row('branch-1', 'Q1?', 'A1', [a]), _row('branch-2', 'Q2?', 'A2', [b])]
    return rows, bs.merge_evidence(rows)


def _run(rows, index, gaps=None, unstarted=0, **kwargs):
    return bs.synthesize_reviewed(rows, gaps or {}, unstarted, index, questions=['Q1?', 'Q2?'], **kwargs)


def test_complete_with_every_question_cited():
    rows, index = _setup()
    result = _run(rows, index)
    assert result['status'] == 'COMPLETE' and result['published'] and result['fallback_reason'] is None
    assert [a['citations'] for a in result['answers']] == [['citation-1'], ['citation-2']]
    assert result['unknowns'] == []


def test_partial_lists_gap_question_as_unknown():
    rows, index = _setup()
    result = _run(rows[:1], index, gaps={'branch-2': 'FAILED'})
    assert result['status'] == 'PARTIAL' and result['published']
    assert result['unknowns'] == [dict(question='Q2?', branch='branch-2', reason='BRANCH_FAILED')]


def test_unstarted_and_unresolved_items_become_unknowns():
    rows, index = _setup()
    rows[0]['conclusion']['unresolved_items'] = ['owner missing']
    result = _run(rows[:1], index, unstarted=1)
    reasons = sorted((u['reason'], u['question']) for u in result['unknowns'])
    assert reasons == [('BRANCH_UNRESOLVED_ITEM', 'Q1?'), ('BRANCH_UNSTARTED', 'Q2?')]
    assert result['unknowns'][0]['detail'] == 'owner missing'


def test_no_decision_branch_is_unknown_and_all_unknown_status():
    item = _item('s1', 'alpha fact')
    row = _row('branch-1', 'Q1?', None, [item], status='not_found')
    result = bs.synthesize_reviewed([row], {}, 0, bs.merge_evidence([row]), questions=['Q1?'])
    assert result['status'] == 'UNKNOWN' and result['answers'] == []
    assert result['unknowns'][0]['reason'] == 'BRANCH_NO_DECISION'


def test_legacy_checkpoint_does_not_invent_worker_or_aggregate_review_receipts():
    from linkloom.orchestration.snapshot import render_snapshot
    from linkloom.ui.projection import WorkspacePresentationContext
    rows, index = _setup()
    rows[0]['run']['review_id'] = 'legacy-worker-review'
    answer = _run(rows[:1], index, gaps={'branch-2': 'FAILED'})
    answer.pop('reviewer')  # the prior persisted answer did not carry an aggregate receipt
    values = dict(status='PARTIAL', tasks={'branch-1': dict(description='Q1?', depends_on=[], status='accepted')},
                  rows={'branch-1': rows[0]}, answer=answer)
    snapshot = render_snapshot('legacy', 'Q1?', WorkspacePresentationContext('Test', 2), values, running=False)
    assert snapshot['reviewer']['performed'] is None
    assert snapshot['reviewer']['reason'] == 'REVIEW_RECORD_UNAVAILABLE'
    reviews = snapshot['parallel_investigation']['reviews']
    assert reviews['workers'] == {} and reviews['unavailable_worker_reviews'] == ['branch-1']


def _key_rows(first, second, first_key=None, second_key=None):
    a, b = _item('s1', 'alpha fact'), _item('s2', 'beta fact', 3)
    rows = [_row('branch-1', 'Q1?', first, [a], first_key), _row('branch-2', 'Q2?', second, [b], second_key)]
    return rows, bs.merge_evidence(rows)


def _verdict(verdict, basis='semantic_comparison'):
    return {('branch-1', 'branch-2'): (verdict, basis)}


def test_conflict_verdict_lists_all_sides_without_winner_and_makes_the_answer_partial():
    rows, index = _key_rows('X', 'Y')
    pairs = bs.resolve_pairs(bs.plan_comparison(rows), _verdict('CONFLICT'))
    result = _run(rows, index, comparison=pairs)
    assert result['status'] == 'PARTIAL'
    (conflict,) = result['conflicts']
    assert [s['branch'] for s in conflict['sides']] == ['branch-1', 'branch-2']
    assert [s['citations'] for s in conflict['sides']] == [['citation-1'], ['citation-2']]  # the Reviewer's evidence
    assert not any(k in conflict for k in ('winner', 'chosen'))
    assert result['conflict_check']['status'] == 'CONFLICT' and result['conflict_check']['uncompared'] == []


def test_pending_pair_without_a_comparison_is_unavailable_not_agreement():
    rows, index = _setup()
    plan = bs.plan_comparison(rows)
    assert plan['pending'] == [('branch-1', 'branch-2')] and plan['decided'] == []
    result = _run(rows, index)
    check = result['conflict_check']
    assert (check['status'], check['verified']) == ('UNAVAILABLE', False)
    assert check['reasons'] == ['COMPARISON_NOT_RUN'] and check['cross_branch_compared'] is False
    assert check['uncompared'] == ['branch-1', 'branch-2']
    assert result['status'] == 'COMPLETE'  # completeness is not rewritten by an unavailable check
    assert 'Conflict check UNAVAILABLE' in result['text']


@pytest.mark.parametrize('verdict', ['UNCERTAIN', 'UNAVAILABLE'])
def test_uncertain_or_unavailable_verdict_keeps_completeness_and_is_unverified(verdict):
    rows, index = _setup()
    result = _run(rows, index, comparison=bs.resolve_pairs(bs.plan_comparison(rows), _verdict(verdict, 'X')))
    assert result['status'] == 'COMPLETE' and result['conflicts'] == []
    assert (result['conflict_check']['status'], result['conflict_check']['verified']) == (verdict, False)


@pytest.mark.parametrize(('verdict', 'compared'), [('AGREE', True), ('UNRELATED', False)])
def test_agree_and_unrelated_are_verified_and_conflict_free(verdict, compared):
    rows, index = _setup()
    result = _run(rows, index, comparison=bs.resolve_pairs(bs.plan_comparison(rows), _verdict(verdict)))
    check = result['conflict_check']
    assert result['status'] == 'COMPLETE' and result['conflicts'] == []
    assert (check['status'], check['verified'], check['cross_branch_compared']) == (verdict, True, compared)


def test_identical_values_agree_without_a_comparison():
    rows, index = _key_rows('same answer', 'same answer')
    plan = bs.plan_comparison(rows)
    assert plan['pending'] == [] and plan['decided'][0]['verdict'] == 'AGREE'
    assert _run(rows, index)['conflict_check']['status'] == 'AGREE'


def test_different_structured_identities_are_unrelated_without_a_comparison():
    rows, index = _key_rows('x', 'y', dict(subject='A', relation='r'), dict(subject='B', relation='r'))
    plan = bs.plan_comparison(rows)
    assert plan['pending'] == [] and plan['decided'][0]['basis'] == 'structured_identity'
    result = _run(rows, index)
    assert result['status'] == 'COMPLETE' and result['conflicts'] == []
    assert result['conflict_check']['status'] == 'UNRELATED' and result['conflict_check']['verified'] is True


def test_same_structured_identity_with_different_values_still_needs_a_meaning_judgement():
    key = dict(subject='Plan', relation='owner', as_of='2025-02-20')
    rows, _ = _key_rows('Mira Chen', 'Chen, Mira', key, dict(key))
    assert bs.plan_comparison(rows)['pending'] == [('branch-1', 'branch-2')]
    other = dict(key, as_of='2025-03-01')  # a different time is a different fact
    rows, _ = _key_rows('Mira Chen', 'Kai Wu', key, other)
    assert bs.plan_comparison(rows)['pending'] == []


def test_dependent_tasks_are_decided_by_structure_and_transitive_dependency_counts():
    rows, _ = _key_rows('x', 'y')
    plan = bs.plan_comparison(rows, {'branch-2': ['mid'], 'mid': ['branch-1']})
    assert plan['pending'] == [] and plan['decided'][0]['basis'] == 'dependent_task'


def test_a_single_answer_has_nothing_to_compare():
    rows, index = _setup()
    result = _run(rows[:1], index, gaps={'branch-2': 'FAILED'})
    assert result['conflict_check']['status'] == 'NOT_APPLICABLE' and result['conflict_check']['pairs'] == []


def test_deterministic():
    rows, index = _setup()
    first = bs.synthesize(copy.deepcopy(rows), {}, 0, copy.deepcopy(index), ['Q1?', 'Q2?'])
    second = bs.synthesize(copy.deepcopy(rows), {}, 0, copy.deepcopy(index), ['Q1?', 'Q2?'])
    assert first == second and first['text'] == second['text']


def _rejected(result, code):
    assert result['published'] is False and result['fallback_reason'] == code
    assert result['answers'] == [] and result['evidence']
    assert {u['branch'] for u in result['unknowns']} >= {'branch-1'}


def test_forged_quote_text_rejected():
    rows, index = _setup()
    index[next(iter(index))]['source']['quote'] = 'forged text'
    _rejected(_run(rows, index), 'AGGREGATE_QUOTE_HASH_MISMATCH')


def test_unknown_identity_rejected():
    rows, index = _setup()
    rows[0]['evidence'] = [_item('s9', 'stale fact')]  # the index still points at an identity no branch carries
    _rejected(_run(rows, index), 'AGGREGATE_EVIDENCE_UNKNOWN')


def test_citation_outside_merged_set_rejected():
    rows, index = _setup()
    schema = bs.validate_aggregate_schema(rows, index)
    verdict = schema('subject', dict(branch='branch-1', question='Q1?', text='A1', citations=['citation-99']))
    assert verdict['reason_code'] == 'AGGREGATE_CITATION_MISSING'
    verdict = schema('subject', dict(branch='branch-1', question='Q1?', text='A1', citations=[]))
    assert verdict['reason_code'] == 'AGGREGATE_CITATION_MISSING'


def test_unverified_operand_branch_rejected():
    rows, index = _setup()
    schema = bs.validate_aggregate_schema(rows[1:], index)
    verdict = schema('subject', dict(branch='branch-1', question='Q1?', text='A1', citations=['citation-1']))
    assert verdict['reason_code'] == 'AGGREGATE_BRANCH_UNVERIFIED'


def test_forbidden_field_rejected():
    rows, index = _setup()
    schema = bs.validate_aggregate_schema(rows, index)
    payload = dict(branch='branch-1', question='Q1?', text='A1', citations=['citation-1'],
                   extra=[dict(mutation_approval=True)])
    assert schema('subject', payload)['reason_code'] == 'AGGREGATE_FIELD_FORBIDDEN'
    original = bs.synthesize

    def forged(*args):
        draft = original(*args)
        draft['answers'][0]['gold'] = 'x'
        return draft
    bs.synthesize = forged
    try:
        _rejected(_run(rows, index), 'AGGREGATE_FIELD_FORBIDDEN')
    finally:
        bs.synthesize = original


def test_source_changed_rejected():
    rows, index = _setup()

    def changed():
        raise RuntimeError('source changed')
    _rejected(_run(rows, index, source_check=changed), 'AGGREGATE_SOURCE_CHANGED')


def test_exception_falls_back_without_raising():
    rows, index = _setup()
    result = _run([None, rows[1]], index)  # synthesis itself fails
    assert result['published'] is False and result['answers'] == []
    assert result['fallback_reason'] == 'AGGREGATE_SYNTHESIS_FAILED' and result['status'] == 'UNKNOWN'
    rows[1]['evidence'] = None  # review fails after a draft exists; the draft status is kept
    result = _run(rows, index)
    assert result['published'] is False and result['answers'] == []
    assert result['fallback_reason'] == 'AGGREGATE_SYNTHESIS_FAILED' and result['status'] == 'COMPLETE'
