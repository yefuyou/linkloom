"""Evaluator-only. Read frozen annotations only after predictions are on disk."""
from argparse import ArgumentParser
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_relation_models import RelationAssessment, EvidenceQuote, ObservationRef
from linkloom.semantic_ingestion.task_revision_comparison import RevisionComparison


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def fraction(n, d):
    return dict(numerator=n,denominator=d,value=n/d if d else None)


def unique_predictions(rows, allowed_ids):
    ids=[r['question_id'] for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(allowed_ids):
        raise ValueError('missing, duplicate or extra prediction question IDs')
    return {r['question_id']:r for r in rows}


def authority_safe(output, annotation):
    checks=output['approved_change_query']
    return (output['decision_store_supplied'] is False
        and output['formal_decisions_created']==annotation['formal_decisions']
        and 'AUTHORITY_NOT_ESTABLISHED' in checks['errors']
        and annotation['authority'] in checks['checks'])


def safety_failures(safety):
    names=('hard_false_merge','unsupported_merge','unsupported_causal_confirmation',
           'unsupported_negative_count','false_deadline_change','authority_overreach','stale_or_retracted_release')
    return [dict(case_id='GLOBAL',failure_stage='safety_gate',check=name,count=safety[name])
            for name in names if safety[name]]


def grounded(requirement, quotes, snapshots):
    original=snapshots[requirement['source_index']]
    for quote in quotes:
        span=quote.get('span',quote)
        if (span['workspace_id']==original.workspace_id and span['artifact_id']==original.artifact_id
            and span['artifact_version_id']==original.artifact_version_id and span['content_hash']==original.content_hash
            and requirement['quote'] in quote['quote']):
            return True
    return False


def proof_check(assessment, snapshots, gold=None):
    try:
        RelationAssessment.from_dict(assessment)
        sources = {a.artifact_version_id:a for a in snapshots}
        quotes = [q for key in ('supporting_evidence','counter_evidence','context_evidence') for q in assessment[key]]
        for item in quotes:
            quote = EvidenceQuote.from_dict(item)
            if quote.span.artifact_version_id not in sources or not quote.verify(sources[quote.span.artifact_version_id]):
                return False
        for dependency in assessment['dependencies']:
            span = dependency['span']
            source = sources.get(span['artifact_version_id'])
            if not source or source.content_hash!=span['content_hash'] or source.content[span['char_start']:span['char_end']]!=span['quote']:
                return False
        if gold:
            used = quotes + [d['span'] for d in assessment['dependencies']]
            for requirement in gold['required_evidence']:
                if not grounded(requirement,used,snapshots):
                    return False
            decisive = [q for key in ('supporting_evidence','counter_evidence') for q in assessment[key]]
            if not all(grounded(fragment,decisive,snapshots) for fragment in gold['decisive_fragments']):
                return False
        return bool(assessment['evidence_validity'])
    except (ValueError,KeyError,TypeError):
        return False


def comparison_check(comp, identity, revision, artifacts):
    try:
        RevisionComparison.from_dict(comp)
        if comp['same_task_id']!=identity['assessment_id'] or comp['revision_id']!=revision['assessment_id']:
            return False
        if any(comp[k]!=revision[k] for k in ('subject','object')):
            return False
        by_version={a.artifact_version_id:a for a in artifacts}
        for side in ('old_deadline','new_deadline'):
            for item in comp[side]['evidence']:
                quote=EvidenceQuote.from_dict(item)
                if not quote.verify(by_version[quote.span.artifact_version_id]):
                    return False
        return True
    except (ValueError,KeyError,TypeError):
        return False


def score(folder):
    inputs_path = ROOT/'tests/fixtures/m2_final/inputs/holdout.json'
    gold_path = ROOT/'tests/fixtures/m2_final/gold/holdout.json'
    inputs = {c['case_id']:c for c in read(inputs_path)['cases']}
    gold = read(gold_path)
    freeze=read(ROOT/'docs/requirements/m2-integration-acceptance/FREEZE.json')
    if sha(gold_path)!=freeze['evaluator_gold_sha256']:
        raise ValueError('frozen Gold changed')
    counts = defaultdict(Counter)
    states = Counter()
    revisions, atoms, failures = [], [], []
    checks = {}
    safety = Counter()
    published_merges = {}
    snapshots_path = ROOT/'docs/requirements/m2-integration-acceptance/evidence/holdout-snapshots.json'
    snapshots = {c['case_id']:c for c in read(snapshots_path)}

    def atom(case_id, snapshot, predicate, expected, actual, valid, *, publish=None):
        published = (actual['phase']=='ASSESSED' and actual['verdict']=='SUPPORTED'
            and actual['lifecycle']=='ACTIVE') if publish is None else publish
        positive = expected=='SUPPORTED'
        true = positive and published and valid
        c = counts[predicate]
        c['tp'] += true
        c['fp'] += published and not true
        c['fn'] += positive and not true
        c['total'] += 1
        if expected in {'UNKNOWN','CONFLICT','REFUTED'}:
            states[expected+'_total'] += 1
            states[expected+'_correct'] += actual['verdict']==expected and valid
        safety['unsupported_negative_count'] += expected=='UNKNOWN' and actual['verdict']=='REFUTED'
        if predicate=='SAME_TASK' and published:
            pair=tuple(sorted(json.dumps(actual[k],sort_keys=True) for k in ('subject','object')))
            prior=published_merges.setdefault(pair,[False,False])
            prior[0] = prior[0] or expected=='REFUTED'
            prior[1] = prior[1] or (not true and expected!='REFUTED')
        if predicate=='AFFECTED_BY':
            safety['unsupported_causal_confirmation'] += published and not true
        atoms.append(dict(case_id=case_id,snapshot=snapshot,predicate=predicate,gold=expected,
            actual=actual['verdict'],proof_valid=valid,published=bool(published),tp=bool(true)))

    for annotation in gold['cases']:
        ident = annotation['case_id']
        case = inputs[ident]
        prediction_path = folder/ident/'prediction.json'
        output = read(prediction_path)
        if 'failure_stage' in output:
            failures.append(output)
            continue
        artifacts = [RawArtifact(workspace_id=case['workspace_id'],**s) for s in case['sources']]
        if output['case_id']!=ident:
            raise ValueError('prediction case scope mismatch')
        by_id = unique_predictions(output['predictions'], [q['question_id'] for q in annotation['questions']])
        for expected in annotation['questions']:
            result = by_id[expected['question_id']]
            assessment = result['assessment']
            # Typed fixed endpoints must match the independent M1 snapshot.
            public = next(q for q in case['questions'] if q['question_id']==expected['question_id'])
            from linkloom.semantic_ingestion.task_observation import TaskObservation
            subject = TaskObservation.from_dict(snapshots[ident]['observations'][public['subject']])
            endpoint_ok = assessment['subject']==ObservationRef.from_observation(subject).to_dict()
            if 'object' in public:
                target = TaskObservation.from_dict(snapshots[ident]['observations'][public['object']])
                endpoint_ok = endpoint_ok and assessment['object']==ObservationRef.from_observation(target).to_dict()
            else:
                change = case['change']
                obj = assessment['object']
                endpoint_ok = endpoint_ok and obj['project']==change['project'] and obj['before_vendor']==change['before']
                endpoint_ok = endpoint_ok and obj['after_vendor']==change['after'] and obj['kind']=='SOURCE_CLAIM'
                endpoint_ok = endpoint_ok and obj['evidence']['span']['artifact_id']==case['sources'][change['source_index']]['artifact_id']
            valid = endpoint_ok and assessment['predicate']==public['predicate'] and proof_check(assessment,artifacts,expected)
            for snapshot in expected['evaluation_snapshots']:
                phase = output['lifecycle'][snapshot] if ident=='H04' else None
                atom(ident,snapshot,public['predicate'],expected['verdict'],assessment,valid,
                    publish=phase['current_revision']['current_supported'] if phase else None)
                complete = assessment['verdict']==expected['verdict'] and valid
                if 'same_task' in expected:
                    identity = result['same_task']
                    identity_valid = identity['predicate']=='SAME_TASK' and proof_check(identity,artifacts)
                    identity_valid = identity_valid and {json.dumps(identity[k],sort_keys=True) for k in ('subject','object')}=={
                        json.dumps(ObservationRef.from_observation(o).to_dict(),sort_keys=True) for o in (subject,target)}
                    atom(ident,snapshot,'SAME_TASK',expected['same_task'],identity,identity_valid,
                        publish=phase['current_identity']['current_supported'] if phase else None)
                    comp = result['comparison']
                    complete = complete and comparison_check(comp,identity,assessment,artifacts)
                    complete = complete and identity['verdict']==expected['same_task'] and identity_valid
                    complete = complete and assessment['changed_fields']==expected['changed_fields']
                    complete = complete and comp['status']==expected['comparison'] and comp['delta_calendar_days']==expected['delta']
                    date_correct = None
                    if expected['comparison']=='COMPUTABLE':
                        date_correct = comp['status']=='COMPUTABLE' and comp['delta_calendar_days']==expected['delta'] and all(comp[side+'_deadline']['value']==expected[side]
                            and comp[side+'_deadline']['bound']==expected['bound']
                            and comp[side+'_deadline']['precision']=='DAY'
                            and comp[side+'_deadline']['role']=='TASK_DEADLINE' for side in ('old','new'))
                        complete = complete and date_correct
                    if ident=='H04':
                        phase = output['lifecycle'][snapshot]
                        complete = complete and phase['current_identity']['current_supported']
                        complete = complete and phase['current_revision']['assessment']['verdict']==expected['verdict']
                        if snapshot!='s0':
                            old_read = phase['old_revision']
                            complete = complete and not old_read['current_supported']
                            complete = complete and phase['old_comparison']['current_comparison']['delta_calendar_days'] is None
                    revisions.append(dict(case_id=ident,snapshot=snapshot,question_id=expected['question_id'],
                        correct=bool(complete),gold=expected,actual_verdict=assessment['verdict'],
                        comparison_status=comp['status'],delta=comp['delta_calendar_days'],
                        date_delta_correct=date_correct,
                        reason_codes=assessment['reason_codes'],comparison_reasons=comp['reason_codes']))
                    safety['false_deadline_change'] += comp['delta_calendar_days'] is not None and comp['delta_calendar_days']!=expected['delta']
                if not complete:
                    failures.append(dict(case_id=ident,snapshot=snapshot,question_id=expected['question_id'],
                        failure_stage='relation_semantics' if assessment['verdict']!=expected['verdict'] else 'evidence_revision_or_lifecycle',
                        gold=expected,actual=assessment,comparison=result.get('comparison')))
        if ident=='H01':
            for observation in snapshots[ident]['observations'].values():
                if any(observation[k]!=v for k,v in annotation['observation_fields'].items()):
                    failures.append(dict(case_id=ident,failure_stage='extraction',actual=observation))
        if ident=='H02':
            if not authority_safe(output,annotation):
                safety['authority_overreach'] += 1
                failures.append(dict(case_id=ident,failure_stage='authority',actual=output['approved_change_query']))
        if ident=='H04':
            sequence = output['lifecycle']
            for phase_name in ('s1','s2'):
                old = sequence[phase_name]['old_revision']
                atom(ident,phase_name,'REVISES_PLAN','UNPUBLISHABLE',old['assessment'],False,publish=old['current_supported'])
            old = sequence['s1']['old_identity']
            atom(ident,'s1','SAME_TASK','UNPUBLISHABLE',old['assessment'],False,publish=old['current_supported'])
            checks = dict(s0_current=sequence['s0']['old_revision']['current_supported'],
                s1_stale=sequence['s1']['old_revision']['evidence_state']=='STALE',
                s1_old_identity_stale=not sequence['s1']['old_identity']['current_supported'],
                s2_retracted=sequence['s2']['old_revision']['persistent_lifecycle']=='RETRACTED',
                stale_never_published=all(not sequence[s]['old_revision']['current_supported'] for s in ('s1','s2')),
                historical_delta_preserved=all(sequence[s]['old_comparison']['historical_comparison']['delta_calendar_days']==2 for s in ('s0','s1','s2')),
                dependencies_blocked=all(not sequence[s]['old_comparison']['current_valid'] for s in ('s1','s2')),
                replay_idempotent=not any(sequence[s]['duplicate_created'] for s in ('s0','s1')) and not sequence['s2']['duplicate_event_created'])
            for name,passed in checks.items():
                if not passed:
                    failures.append(dict(case_id=ident,failure_stage='lifecycle',check=name))
            safety['stale_or_retracted_release'] += sum(sequence[s]['old_revision']['current_supported'] for s in ('s1','s2'))
    relation = {p:dict(**c,precision=fraction(c['tp'],c['tp']+c['fp']),recall=fraction(c['tp'],c['tp']+c['fn']))
        for p,c in counts.items()}
    for predicate in ('SAME_TASK','RELATED_TO','AFFECTED_BY','REVISES_PLAN','IN_PROJECT','PROJECT_ALIAS'):
        relation.setdefault(predicate,dict(tp=0,fp=0,fn=0,total=0,precision=fraction(0,0),recall=fraction(0,0)))
    totals = Counter()
    for count in counts.values():
        totals.update(count)
    computable = [r for r in revisions if r['gold']['comparison']=='COMPUTABLE']
    supported_revisions=[r for r in revisions if r['gold']['verdict']=='SUPPORTED']
    safety['published_same_task']=len(published_merges)
    safety['hard_false_merge']=sum(v[0] for v in published_merges.values())
    safety['unsupported_merge']=sum(v[1] and not v[0] for v in published_merges.values())
    failures.extend(safety_failures(safety))
    return dict(protocol='AUTHOR_VISIBLE_HOLDOUT; NOT_EXTERNAL_BLIND',relation_scores=relation,
        micro_precision=fraction(totals['tp'],totals['tp']+totals['fp']),micro_recall=fraction(totals['tp'],totals['tp']+totals['fn']),
        revision_accuracy=fraction(sum(r['correct'] for r in revisions),len(revisions)),
        supported_revision_accuracy=fraction(sum(r['correct'] for r in supported_revisions),len(supported_revisions)),
        computable_revision_accuracy=fraction(sum(r['correct'] for r in computable),len(computable)),
        date_delta_accuracy=fraction(sum(r['date_delta_correct'] for r in computable),len(computable)),
        state_accuracy=dict(states),safety=dict(safety),false_merge=fraction(safety['hard_false_merge']+safety['unsupported_merge'],safety['published_same_task']),
        cases_passed=[c['case_id'] for c in gold['cases'] if not any(f['case_id']==c['case_id'] for f in failures)],
        atoms=atoms,revisions=revisions,failures=failures,lifecycle_checks=checks,
        input_sha256=sha(inputs_path),gold_sha256=sha(gold_path),snapshot_sha256=sha(snapshots_path),
        predictions_sha256={ident:sha(folder/ident/'prediction.json') for ident in inputs},run=read(folder/'run.json'),
        real_candidate_recall='separate candidate evaluation; fixed candidates here',
        overall_m2_acceptance='PASS' if not failures and totals['tp']/(totals['tp']+totals['fn'])>=.9 else 'FAIL')


if __name__=='__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--predictions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=score(args.predictions)
    with args.output.open('x',encoding='utf8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(result['overall_m2_acceptance'])
