"""Post-exposure scoring audit of immutable FIRST predictions; never reruns M2.

Reason expectations were supplied by an independent Reviewer from the original
SPEC before that Reviewer read predictions. This supplements the frozen scorer;
it does not change the implementation, Gold, predictions or first metrics.
"""
from argparse import ArgumentParser
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from scripts.run_m2_final import digest, write
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, ChangeRef, EvidenceQuote

EXPECTATIONS={
    'H01-1':dict(verdict='SUPPORTED',codes=['EXPLICIT_PLAN_REPLACEMENT'],comparison='NOT_COMPARABLE',
                 comparison_codes=['DEADLINE_ROLE_OR_PRECISION_UNSUITABLE']),
    'H02-1':dict(verdict='SUPPORTED',codes=['EXPLICIT_DIRECT_CHANGE_IMPACT']),
    'H02-2':dict(verdict='REFUTED',codes=['EXPLICIT_DIRECT_CHANGE_IMPACT']),
    'H02-3':dict(verdict='UNKNOWN',codes=['NO_UNIQUE_DIRECT_CHANGE_BRIDGE'],missing_terms=['连接','具体供应商变更','共现不足']),
    'H03-1':dict(verdict='CONFLICT',codes=['CONFLICTING_REVISION_EVIDENCE'],comparison='BLOCKED',
                 comparison_codes=['CONFLICTING_REVISION_EVIDENCE']),
    'H04-1':dict(verdict='SUPPORTED',codes=['EXPLICIT_PLAN_REPLACEMENT']),
    'H04-2':dict(verdict='REFUTED',codes=['EXPLICIT_NO_BUSINESS_REVISION'],comparison='NOT_APPLICABLE',
                 comparison_codes=['EXPLICIT_NO_BUSINESS_REVISION'],detail='NO_BUSINESS_PLAN_REVISION'),
}


def reason_check(row, expected):
    assessment=row['assessment']
    text=' '.join(assessment['missing_evidence'])
    checks=dict(verdict=assessment['verdict']==expected['verdict'],
                relation_reason=set(expected['codes'])<=set(assessment['reason_codes']))
    if expected.get('missing_terms'):
        checks['missing_evidence']=bool(text) and all(term in text for term in expected['missing_terms'])
    if expected.get('comparison'):
        comparison=row['comparison']
        checks['comparison_status']=comparison['status']==expected['comparison']
        checks['comparison_reason']=set(expected['comparison_codes'])<=set(comparison['reason_codes'])
        checks['blocked_delta_null']=comparison['delta_calendar_days'] is None
    if expected.get('detail'):
        checks['relation_detail']=assessment['relation_detail']==expected['detail']
    return checks


def exact_change_check(assessment, snapshot, public):
    change=public['change']
    source=snapshot['sources'][change['source_index']]
    evidence=next(q for q in snapshot['evidence']
        if q['span']['artifact_version_id']==source['artifact_version_id'] and q['span']['ordinal']==change['segment_index'])
    endpoint=assessment['object']
    fields=dict(workspace_id=public['workspace_id'],project=change['project'],before_vendor=change['before'],
                after_vendor=change['after'],kind='SOURCE_CLAIM',decision_id=None,candidate_id=None,receipt_id=None)
    return dict(typed_fields=all(endpoint[k]==v for k,v in fields.items()),
        exact_evidence=endpoint['evidence']==evidence,
        dependency=any(d['span']==evidence['span'] and d['source_status']=='CURRENT' and d['readback_ok']
            and d['observation_valid'] is True
            for d in assessment['dependencies']),
        authority={'SOURCE_CLAIM_ONLY','FORMAL_AUTHORITY_NOT_ESTABLISHED'}<=set(assessment['authority_checks']))


def candidate_coverage(public, gold, output, snapshot):
    predictions={r['question_id']:r for r in output['predictions']}
    rows=[]
    for annotation in gold['questions']:
        row=predictions[annotation['question_id']]
        question=next(q for q in public['questions'] if q['question_id']==annotation['question_id'])
        subject=ObservationRef.from_observation(TaskObservation.from_dict(snapshot['observations'][question['subject']])).to_dict()
        if 'object' in question:
            expected_object=ObservationRef.from_observation(TaskObservation.from_dict(snapshot['observations'][question['object']])).to_dict()
        else:
            change=public['change']
            source=snapshot['sources'][change['source_index']]
            proof=next(q for q in snapshot['evidence'] if q['span']['artifact_version_id']==source['artifact_version_id']
                       and q['span']['ordinal']==change['segment_index'])
            expected_object=ChangeRef(public['workspace_id'],change['project'],change['before'],change['after'],
                EvidenceQuote.from_dict(proof)).to_dict()
        candidates=row.get('discovery',output.get('discovery',{})).get('candidates',[])
        for snapshot_name in annotation['evaluation_snapshots']:
            targets=[]
            if annotation['verdict']=='SUPPORTED':
                targets.append(dict(subject=subject,object=expected_object,predicate=question['predicate']))
            if annotation.get('same_task')=='SUPPORTED':
                targets.append(dict(subject=subject,object=expected_object,predicate='SAME_TASK'))
            for target in targets:
                def endpoint_equal(candidate):
                    left,right=candidate['subject'],candidate['object']
                    a,b=target['subject'],target['object']
                    # Change discovery locates the declaration, not the later
                    # validator's chosen proof role/range within that source.
                    def key(value):
                        if 'observation_id' in value:
                            return value
                        span=value['evidence']['span']
                        return {**{k:value[k] for k in ('workspace_id','project','before_vendor','after_vendor','kind')},
                            **{k:span[k] for k in ('artifact_id','artifact_version_id','content_hash','ordinal')}}
                    return (key(left)==key(a) and key(right)==key(b)) or (target['predicate'] in {'SAME_TASK','RELATED_TO'}
                        and key(left)==key(b) and key(right)==key(a))
                pair=any(endpoint_equal(c) for c in candidates)
                typed=any(endpoint_equal(c) and c['predicate_hint']==target['predicate'] for c in candidates)
                rows.append(dict(case_id=public['case_id'],snapshot=snapshot_name,predicate=target['predicate'],
                    endpoint_pair_recalled=pair,predicate_hint_recalled=typed))
    return rows


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--predictions',type=Path,required=True)
    parser.add_argument('--metrics',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding='utf8'))
    base=ROOT/'docs/requirements/m2-integration-acceptance'
    freeze=read(base/'FREEZE.json')
    original=read(args.metrics)
    snapshots={r['case_id']:r for r in read(base/'evidence/holdout-snapshots.json')}
    public={c['case_id']:c for c in read(ROOT/'tests/fixtures/m2_final/inputs/holdout.json')['cases']}
    gold={c['case_id']:c for c in read(ROOT/'tests/fixtures/m2_final/gold/holdout.json')['cases']}
    reasons,changes,coverage=[],[],[]
    for ident,case in public.items():
        path=args.predictions/ident/'prediction.json'
        if digest(path)!=original['predictions_sha256'][ident]:
            raise ValueError('first prediction changed')
        output=read(path)
        for row in output['predictions']:
            checks=reason_check(row,EXPECTATIONS[row['question_id']])
            reasons.append(dict(case_id=ident,question_id=row['question_id'],checks=checks,passed=all(checks.values()),
                actual_codes=row['assessment']['reason_codes'],actual_missing=row['assessment']['missing_evidence'],
                comparison_codes=row.get('comparison',{}).get('reason_codes')))
            if ident=='H02':
                checks=exact_change_check(row['assessment'],snapshots[ident],case)
                changes.append(dict(question_id=row['question_id'],checks=checks,passed=all(checks.values())))
        coverage.extend(candidate_coverage(case,gold[ident],output,snapshots[ident]))
    unchanged=all(digest(ROOT/name)==expected for name,expected in freeze['protected_hashes'].items())
    gold_unchanged=digest(ROOT/'tests/fixtures/m2_final/gold/holdout.json')==freeze['evaluator_gold_sha256']
    assert unchanged and gold_unchanged
    write(args.output,dict(protocol='POST_EXPOSURE_AUDIT_OF_SAME_FIRST_PREDICTIONS; NO_M2_RERUN; NOT_BLIND',
        audit_script_sha256=digest(Path(__file__)),
        original_metrics_sha256=digest(args.metrics),implementation_commit=freeze['implementation_commit'],
        original_predictions_unchanged=True,protected_files_unchanged=unchanged,gold_unchanged=gold_unchanged,
        expectations_source='independent Reviewer, original M2_EVAL_SPEC; supplied before Reviewer read predictions',
        expectations=EXPECTATIONS,reason_checks=reasons,exact_change_checks=changes,candidate_atoms=coverage,
        candidate_endpoint_pair_recall=dict(numerator=sum(r['endpoint_pair_recalled'] for r in coverage),denominator=len(coverage)),
        candidate_predicate_hint_recall=dict(numerator=sum(r['predicate_hint_recalled'] for r in coverage),denominator=len(coverage)),
        original_overall=original['overall_m2_acceptance'],
        audited_overall='PASS' if original['overall_m2_acceptance']=='PASS' and all(r['passed'] for r in reasons+changes) else 'FAIL'))
    print('post-exposure audit written; original predictions and frozen implementation unchanged')


if __name__=='__main__':
    main()
