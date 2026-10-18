"""Offline fixed-candidate P3 predictions, then separate Gold scoring."""
from argparse import ArgumentParser
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'src'),str(root)]
from tests.task_revision_support import INPUT, public_cases, run_case, invalid_premise_trials


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def evaluate(output):
    output=output.resolve()
    if not output.is_relative_to(root/'work'):
        raise ValueError('use a fresh task-owned work/ directory')
    output.mkdir(parents=True,exist_ok=False)
    predictions=[]
    for case in public_cases():
        result=run_case(case)
        predictions.append(result)
        write(output/(case['case_id']+'.json'),result)
    write(output/'predictions.json',predictions)
    # The tested loader/validators cannot read this evaluator-only path.
    gold_path=root/'tests/fixtures/task_revisions/gold/dev.json'
    gold=json.loads(gold_path.read_text(encoding='utf8'))
    answers={q['question_id']:q for c in gold['cases'] for q in c['questions']}
    counts=Counter()
    states=Counter()
    rows=[]
    key_outputs=[]
    for case in predictions:
        sources={s['artifact_version_id']:s for s in case['sources']}
        for row in case['predictions']:
            rev,comparison=row['revision'],row['comparison']
            expected=answers[row['question_id']]
            counts['tp']+=rev['verdict']==expected['verdict']=='SUPPORTED'
            counts['fp']+=rev['verdict']=='SUPPORTED' and expected['verdict']!='SUPPORTED'
            counts['fn']+=rev['verdict']!='SUPPORTED' and expected['verdict']=='SUPPORTED'
            state_ok=rev['verdict']==expected['verdict']
            complete=state_ok and rev['changed_fields']==expected['changed_fields'] and comparison['status']==expected['comparison_status'] and comparison['delta_calendar_days']==expected['delta_calendar_days']
            for side in ('old','new'):
                if expected[side+'_bound'] is not None:
                    complete=complete and comparison[side+'_deadline']['bound']==expected[side+'_bound'] and comparison[side+'_deadline']['value']==expected[side+'_date']
            if expected.get('asserted_effective_at') is not None:
                complete=complete and rev['asserted_effective_at']==expected['asserted_effective_at'] and comparison['plan_currentness']==expected['plan_currentness']
            if expected['comparison_status']=='COMPUTABLE':
                counts['computable_total']+=1
                counts['computable_correct']+=complete
            if expected['verdict'] in {'UNKNOWN','REFUTED','CONFLICT'}:
                states[expected['verdict']+'_total']+=1
                states[expected['verdict']+'_correct']+=state_ok
            counts['revision_accuracy_total']+=1
            counts['revision_accuracy_correct']+=complete
            counts['unproved_revisions']+=rev['verdict']=='SUPPORTED' and expected['verdict']!='SUPPORTED'
            counts['false_deadline_changes']+=comparison['delta_calendar_days'] is not None and (
                expected['delta_calendar_days'] is None or comparison['delta_calendar_days']!=expected['delta_calendar_days'])
            for assessment in (row['same_task'],rev):
                for quote in (*assessment['supporting_evidence'],*assessment['counter_evidence'],*assessment['context_evidence']):
                    source=sources.get(quote['span']['artifact_version_id'])
                    counts['evidence_readback_total']+=1
                    counts['evidence_readback_ok']+=source is not None and source['content'][quote['char_start']:quote['char_end']]==quote['quote']
            for side in ('old_deadline','new_deadline'):
                for quote in comparison[side]['evidence']:
                    source=sources.get(quote['span']['artifact_version_id'])
                    counts['date_readback_total']+=1
                    counts['date_readback_ok']+=source is not None and source['content'][quote['char_start']:quote['char_end']]==quote['quote']
            for dependency in rev['dependencies']:
                span=dependency['span']
                source=sources.get(span['artifact_version_id'])
                counts['dependency_readback_total']+=1
                counts['dependency_readback_ok']+=source is not None and source['content'][span['char_start']:span['char_end']]==span['quote']
            rows.append(dict(case_id=case['case_id'],question_id=row['question_id'],gold=expected,actual_verdict=rev['verdict'],
                changed_fields=rev['changed_fields'],comparison_status=comparison['status'],delta_calendar_days=comparison['delta_calendar_days'],
                complete_match=complete,reason_codes=rev['reason_codes'],comparison_reasons=comparison['reason_codes']))
            if case['case_id'] in {'D04','D05','D07'}:
                key_outputs.append(dict(case_id=case['case_id'],**row))
    trials=invalid_premise_trials()
    write(output/'invalid-premise-trials.json',trials)
    metrics={'counts':dict(counts),'precision':dict(numerator=counts['tp'],denominator=counts['tp']+counts['fp']),
        'recall':dict(numerator=counts['tp'],denominator=counts['tp']+counts['fn']),
        'state_accuracy':dict(states),'cases':rows,'real_provider_calls':0,'holdout_executed':False,
        'input_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),'gold_sha256':hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        'invalid_identity_release':dict(numerator=sum(t['released'] for t in trials),denominator=len(trials)),
        'scope':'fixed candidates; independent Dev Gold; finite rule coverage, not discovery/Provider recall'}
    write(output/'metrics.json',metrics)
    write(output/'key-results.json',key_outputs)
    return metrics


if __name__=='__main__':
    parser=ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf8',errors='replace')
    metrics=evaluate(args.output)
    print(json.dumps({k:v for k,v in metrics.items() if k!='cases'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(c['complete_match'] for c in metrics['cases']) and metrics['invalid_identity_release']['numerator']==0 else 1)
