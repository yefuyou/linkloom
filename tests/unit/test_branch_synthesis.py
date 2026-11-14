import copy
import hashlib

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


def test_conflict_lists_all_sides_without_winner():
    a, b = _item('s1', 'alpha fact'), _item('s2', 'beta fact', 3)
    key = dict(subject='Plan', relation='owner')
    rows = [_row('branch-1', 'Q1?', 'X', [a], key, 'Mira'), _row('branch-2', 'Q2?', 'Y', [b], key, 'Kai')]
    result = _run(rows, bs.merge_evidence(rows))
    assert result['status'] == 'PARTIAL'
    (conflict,) = result['conflicts']
    assert [s['branch'] for s in conflict['sides']] == ['branch-1', 'branch-2']
    assert not any(k in conflict for k in ('winner', 'chosen'))
    assert result['conflict_check']['uncompared'] == []


def test_same_claim_value_is_not_a_conflict_and_missing_keys_are_reported_uncompared():
    rows, index = _setup()
    assert _run(rows, index)['conflict_check'] == dict(
        cross_branch_compared=False, compared=[], uncompared=['branch-1', 'branch-2'])
    key = dict(subject='Plan', relation='owner')
    rows[0]['conclusion']['claim_key'] = rows[1]['conclusion']['claim_key'] = key
    rows[0]['conclusion']['claim_value'] = rows[1]['conclusion']['claim_value'] = 'Mira'
    result = _run(rows, index)
    assert result['conflicts'] == []
    assert result['conflict_check'] == dict(cross_branch_compared=True, compared=['branch-1', 'branch-2'], uncompared=[])


def test_distinct_keys_are_not_compared_and_text_claims_no_conflict_check():
    rows, index = _setup()
    rows[0]['conclusion'].update(claim_key=dict(subject='A', relation='r'), claim_value='x')
    rows[1]['conclusion'].update(claim_key=dict(subject='B', relation='r'), claim_value='y')
    result = _run(rows, index)
    assert result['status'] == 'COMPLETE' and result['conflicts'] == []
    assert result['conflict_check'] == dict(cross_branch_compared=False, compared=[], uncompared=['branch-1', 'branch-2'])
    assert 'conflict' not in result['text'].lower()


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
