from copy import deepcopy
import pytest
from run_graph_continuation import unknown_positive,compare,seal


def test_unknown_fabrication_counts_positive_answer_not_only_relation_errors():
    assert unknown_positive(dict(status='FOUND',relations=[],values=[]),dict(status='UNKNOWN'))
    assert unknown_positive(dict(status='PARTIAL',relations=[],values=['unrequested owner']),dict(status='UNKNOWN'))
    assert not unknown_positive(dict(status='NOT_FOUND',relations=[],values=[],structured_outputs=[]),dict(status='UNKNOWN'))
    assert not unknown_positive(dict(status='AMBIGUOUS',relations=[],values=[],structured_outputs=[]),dict(status='UNKNOWN'))
    assert not unknown_positive(dict(status='FOUND',relations=[],values=['owner']),dict(status='FOUND'))


def synthetic_rows():
    rows=[]
    for group,prefix,count in [('D','A',16),('D2','X',12),('B_MERIDIAN','Z',12)]:
        for i in range(count):
            for repeat in [1,2,3]:rows.append(dict(id=prefix+str(i),group=group,route='graph_only',repeat=repeat,actual=dict(status='FOUND'),score=dict(correct=True,checks={'values':True})))
    return rows


def test_comparison_preserves_all_repeats_and_reports_regressions():
    before=synthetic_rows();after=deepcopy(before)
    after[0]['score']['correct']=False;after[0]['score']['checks']['values']=False
    result=compare(after,before)
    assert result['worse']==['A0'] and result['better']==[]
    bad=deepcopy(after);bad.pop()
    with pytest.raises(ValueError,match='THREE_REPEATS'):compare(bad,before)


def test_no_more_than_three_rounds_and_no_existing_ledger_can_be_retried(tmp_path,monkeypatch):
    import run_graph_continuation as module
    with pytest.raises(ValueError,match='MAX_THREE'):seal(4)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'budget-r1.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError,match='LEDGER_SEQUENCE'):seal(1)
