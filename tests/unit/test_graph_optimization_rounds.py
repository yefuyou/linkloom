from copy import deepcopy
from pathlib import Path
import pytest
from run_graph_optimization import case_summary,add_comparison


def report(correct=12,errors=0):
    rows=[]
    for prefix,count,group in [('D',16,'D'),('B',12,'B_MERIDIAN')]:
        for index in range(count):
            for repeat in (1,2,3):rows.append(dict(id=f'{prefix}{index+1:02d}',group=group,route='graph_only',repeat=repeat,
                actual={'status':'FOUND'},score=dict(correct=index<correct,error_relations=errors if index==0 else 0,checks={})))
    return dict(rows=rows,aggregate=[dict(group='D',route='graph_only',metrics=dict(accuracy={'mean':correct/16},error_relations={'max':errors}))],cost_reserved_usd=.2)


def test_missing_repeat_is_rejected_instead_of_comparing_partial_successes():
    given=report();given['rows'].pop()
    with pytest.raises(ValueError,match='THREE_REPEATS'):case_summary(given)


def test_round_reports_better_and_worse_cases_and_exact_stopping_rule(tmp_path):
    (tmp_path/'scored').mkdir();(tmp_path/'scored/report.md').write_text('original')
    old=report(11);new=report(12)
    for row in new['rows']:
        if row['id']=='D01':row['score']['correct']=False
        if row['id']=='D13':row['score']['correct']=True
    cfg=dict(optimization_round=2,previous_round_report='old.json',fix_category='generic',generic_fix='structural rule')
    final=add_comparison(new,old,config=cfg,output=tmp_path)
    assert final['optimization']['target_met'] and final['optimization']['stop']
    assert 'D01' in final['optimization']['worse'] and 'D12' in final['optimization']['better'] and 'D13' in final['optimization']['better']
    assert final['optimization']['regressions_require_explanation']
    bad=report(12,errors=1)
    assert not add_comparison(bad,old,config=cfg,output=tmp_path)['optimization']['target_met']
    cfg['optimization_round']=6
    assert add_comparison(report(8),old,config=cfg,output=tmp_path)['optimization']['stop']



def test_stage_ledger_paths_are_canonical_and_no_ledger_can_be_skipped(tmp_path,monkeypatch):
    import json
    import run_graph_optimization as module
    monkeypatch.chdir(tmp_path);root=Path('work/graph-opt');root.mkdir(parents=True)
    (root/'budget-r1.json').write_text('{}');(root/'previous.json').write_text(json.dumps({'status':'COMPLETE','optimization':{'stop':False}}))
    cfg=dict(optimization_round=2,previous_budget_ledgers=['work/graph-opt/budget-r1.json'],graph_alignment_review=False,graph_max_provider_calls=1,previous_round_report='work/graph-opt/previous.json')
    path=root/'configuration.json'
    class Inputs:dependencies={'configuration':path}
    called=[]
    monkeypatch.setattr(module,'run_revision',lambda *a:called.append(True) or {'verified':'fake'})
    monkeypatch.setattr(module,'add_comparison',lambda final,*a,**k:final)
    for spelling in ['work/graph-opt/budget-r1.json',str((root/'budget-r1.json').resolve())]:
        cfg['previous_budget_ledgers']=[spelling];path.write_text(json.dumps(cfg))
        assert module.run_round(Inputs(),root/'output',root/'budget-r2.json')=={'verified':'fake'}
    assert len(called)==2
    cfg['previous_budget_ledgers']=[];path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='LEDGER_SEQUENCE'):module.run_round(Inputs(),root/'output',root/'budget-r2.json')
    assert len(called)==2
