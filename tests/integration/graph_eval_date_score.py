"""Final offline scorer: preserve grounded replacement-date parity for both routes.

Root has observed execution status/cost counters, not saved answers or structured
answer contents. This correction was motivated by independent synthetic review.
"""
from copy import deepcopy
from pathlib import Path
import argparse,hashlib,json
import rescore_meeting_graph_dev as base
from meeting_graph_scoring import norm
from linkloom.agents.claim_grounding import _claim_facts,_fact_supports
from run_temporal_graph_dev import _dump,_json


class DateParityScorer(base.CorrectedScorer):
    def __call__(self,case_id,actual,expected,public,documents):
        selected=deepcopy(actual)
        if case_id.startswith('B') and 'effective_date' in expected.get('values',{}) and actual.get('verified_by')=='legacy-runtime-reviewer':
            claims=_claim_facts(actual.get('answer') or '') or ()
            grounded=[]
            for doc in documents:
                if doc['path'] not in actual['evidence']:continue
                grounded.extend(_claim_facts(doc['content']) or ())
            dates={claim.valid_on for claim in claims if claim.valid_on and claim.relation in {'superseded_by','became_effective_on'} and any(_fact_supports(fact,claim) for fact in grounded)}
            selected['values']=list(actual.get('values',[]))+sorted(dates)
        return super().__call__(case_id,selected,expected,public,documents)


def hashes():
    return {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in dict(
        final_scorer=Path(__file__),correction=Path(base.__file__),parent=Path(base.__file__).with_name('meeting_graph_scoring.py')).items()}


def freeze(path):
    data=dict(schema='graph-eval-final-scorer/v1',hashes=hashes(),frozen_before_saved_answers_read=True,
        previously_observed='Execution status/cost counters only; no saved answer/structured contents.',
        reason='Independent synthetic review: typed selected values; task identity/content; no unrequested deadline; grounded replacement valid_on parity')
    with Path(path).open('x',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    return data


def rescore(*,final_freeze,**kwargs):
    marker=_json(final_freeze)
    if marker['hashes']!=hashes():raise ValueError('FINAL_SCORER_CHANGED')
    original=base.CorrectedScorer
    try:
        base.CorrectedScorer=DateParityScorer
        report=base.rescore(**kwargs)
    finally:base.CorrectedScorer=original
    report['final_scoring_freeze']=marker
    output=Path(kwargs['output']);_dump(output/'report.json',report)
    with (output/'report.md').open('a',encoding='utf8') as stream:
        stream.write('\n最终评分冻结另含旧路径日期一致性修正：只从现有grounding支持的替代声明valid_on恢复日期，不从背景文本任意取日期。全部结果统一评分，无付费重跑。\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze',type=Path)
    for name in ('report','dataset','bindings','dataset_freeze','index','scorer_freeze','final_freeze','output'):p.add_argument('--'+name.replace('_','-'),type=Path)
    a=vars(p.parse_args());target=a.pop('freeze')
    if target:print(json.dumps(freeze(target)));return
    a['report_path']=a.pop('report');report=rescore(**a);print(json.dumps(dict(status=report['status'],completed=report['completed_executions'])))

if __name__=='__main__':main()
