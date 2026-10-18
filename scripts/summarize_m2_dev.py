"""Deduplicated original D01-D08 semantic scores, with V cases separate."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from tests.task_identity_support import build_case, public_cases
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from scripts.run_m2_final import write, digest
from scripts.score_m2_final import fraction


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--p2',type=Path,required=True)
    parser.add_argument('--p3',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    predictions=[]
    for case in public_cases():
        subject,target,artifacts,registry=build_case(case)
        result=assess_same_task(subject,target,artifacts=artifacts,source_registry=registry)
        predictions.append(dict(case_id=case['case_id'],assessment=result.to_dict()))
    write(args.output/'identity-predictions.json',predictions)
    gold_path=ROOT/'tests/fixtures/task_identity/gold/dev.json'
    gold={c['case_id']:c for c in json.loads(gold_path.read_text(encoding='utf8'))['cases']}
    rows=[dict(case_id=r['case_id'],predicate='SAME_TASK',gold=gold[r['case_id']]['verdict'],
               actual=r['assessment']['verdict'],valid=r['assessment']['evidence_validity']) for r in predictions]
    p2=json.loads((args.p2/'metrics.json').read_text(encoding='utf8'))
    rows.extend(dict(case_id=r['case_id'],predicate=r['predicate'],gold=r['gold'],actual=r['actual'],
        valid=r['evidence_validity'] and r['gold_support_evidence_covered']) for r in p2['cases']
        if r['case_id'].startswith('D') and r['predicate']!='SAME_TASK')
    p3=json.loads((args.p3/'metrics.json').read_text(encoding='utf8'))
    original=[r for r in p3['cases'] if r['case_id'].startswith('D')]
    rows.extend(dict(case_id=r['case_id'],predicate='REVISES_PLAN',gold=r['gold']['verdict'],actual=r['actual_verdict'],
        valid=r['complete_match']) for r in original)
    p3pred=json.loads((args.p3/'predictions.json').read_text(encoding='utf8'))
    extra=next(r['same_task'] for c in p3pred if c['case_id']=='D04' for r in c['predictions'] if r['question_id']=='D04-c-revision')
    rows.append(dict(case_id='D04-C-B',predicate='SAME_TASK',gold='SUPPORTED',actual=extra['verdict'],valid=extra['evidence_validity']))
    counts=defaultdict(Counter)
    states=Counter()
    for r in rows:
        c=counts[r['predicate']]
        tp=r['gold']==r['actual']=='SUPPORTED' and r['valid']
        c['tp']+=tp
        c['fp']+=r['actual']=='SUPPORTED' and not tp
        c['fn']+=r['gold']=='SUPPORTED' and not tp
        c['total']+=1
        if r['gold'] in ('UNKNOWN','REFUTED','CONFLICT'):
            states[r['gold']+'_total']+=1
            states[r['gold']+'_correct']+=r['actual']==r['gold']
    counts={p:dict(**c,precision=fraction(c['tp'],c['tp']+c['fp']),recall=fraction(c['tp'],c['tp']+c['fn'])) for p,c in counts.items()}
    write(args.output/'metrics.json',dict(scope='original D01-D08; fixed pairs; duplicates removed across P1/P2/P3',
        relation_scores=counts,atoms=rows,state_accuracy=dict(states),
        revision_accuracy=fraction(sum(r['complete_match'] for r in original),len(original)),
        computable_accuracy=fraction(sum(r['complete_match'] for r in original if r['gold']['comparison_status']=='COMPUTABLE'),
            sum(r['gold']['comparison_status']=='COMPUTABLE' for r in original)),
        supplemental_p3_counts=p3['counts'],supplemental_p2_scores=p2['relation_scores'],
        false_merge=fraction(sum(r['predicate']=='SAME_TASK' and r['actual']=='SUPPORTED' and r['gold']!='SUPPORTED' for r in rows),
            sum(r['predicate']=='SAME_TASK' and r['actual']=='SUPPORTED' for r in rows)),
        input_sha256=digest(ROOT/'tests/fixtures/task_identity/inputs/dev.json'),gold_sha256=digest(gold_path),
        predictions_sha256=digest(args.output/'identity-predictions.json')))


if __name__=='__main__':
    main()
