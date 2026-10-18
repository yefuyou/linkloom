"""Offline evaluator. Imports no model/provider and executes no Agent."""
from collections import Counter
from dataclasses import asdict
import json

from linkloom.evaluation.bad_cases import classify_link_case
from linkloom.evaluation.metrics import binary_metrics

from .fixtures import ROOT, load_inputs, verify_proof


def load_dev():
    data = json.loads((ROOT / 'gold/dev.json').read_text(encoding='utf8'))
    if data['schema'] != 'm3-gold/v1' or data['split'] != 'dev':
        raise ValueError('unsupported annotations')
    return load_inputs(), data['cases']


def fraction(n, d):
    return dict(numerator=n, denominator=d, value=round(n/d,4) if d else None)


def keyed(rows, fields, *, annotations=False):
    if not isinstance(rows,list):
        raise ValueError('rows must be lists')
    result = {}
    for row in rows:
        if not isinstance(row,dict) or any(not isinstance(row.get(f),str) for f in fields):
            raise ValueError('missing row key')
        key = tuple(row[f] for f in fields)
        if key in result:
            raise ValueError('duplicate prediction key')
        if not isinstance(row.get('evidence'),list):
            raise ValueError('evidence must be a list')
        if annotations:
            valid = all(isinstance(e,str) for e in row['evidence'])
        else:
            valid = all(isinstance(e,dict) and isinstance(e.get('ref'),str) and isinstance(e.get('span'),dict)
                        for e in row['evidence'])
        if not valid:
            raise ValueError('malformed evidence')
        result[key] = row
    return result


def grounded(case, row, expected, *, allow_stale=False, catalog=None):
    if row is None or expected is None:
        return False
    evidence = row.get('evidence',[])
    required = set(expected['evidence'])
    return bool(required and evidence and required <= {e.get('ref') for e in evidence}
        and all(verify_proof(case,e,require_current=not allow_stale) for e in evidence)
        and (catalog is None or all(catalog.get(ref) in evidence for ref in required)))


def score_case(case, gold, answer):
    """Score a normalized answer, without claiming how it was produced.

    Call score_run for execution eligibility. Gold-shaped unit inputs are only
    scorer correctness checks, never reported as Agent capability evidence.
    """
    if answer is None:
        return dict(case_id=case['case_id'],status='NOT_EVALUATED',metrics=None,
                    failure_stage='capability_gap',counts={},bad_cases=[])
    sections = {'tasks':('ref',), 'relations':('predicate','subject','object'),
                'revisions':('subject','object')}
    predicted = {s:keyed(answer.get(s),f) for s,f in sections.items()}
    actual = {s:keyed(gold[s],f,annotations=True) for s,f in sections.items()}
    if answer.get('answer_status') not in {'COMPLETE','PARTIAL','ABSTAIN'}:
        raise ValueError('invalid answer_status')
    if not isinstance(answer.get('abstentions'),list) or any(not isinstance(r,str) for r in answer['abstentions']):
        raise ValueError('invalid abstentions')
    if not isinstance(answer.get('answer_evidence'),list):
        raise ValueError('missing answer_evidence')
    if any(not isinstance(e,dict) or not isinstance(e.get('ref'),str) or not isinstance(e.get('span'),dict)
           for e in answer['answer_evidence']):
        raise ValueError('malformed answer evidence')
    counts, metrics, bad, stages = Counter(), {}, [], set()
    false_revision_keys, published_revision_keys, merge_votes = set(),set(),{}
    ground = {}
    for section, rows in predicted.items():
        for key,row in rows.items():
            expected = actual[section].get(key)
            # Stale bytes may explain an abstention, never support publication.
            allow_stale = (row.get('verdict') == 'NOT_ASSESSABLE' or
                           (row.get('status') == 'BLOCKED' and row.get('reason') == 'STALE_EVIDENCE'))
            ok = grounded(case,row,expected,allow_stale=allow_stale,catalog=gold['evidence_catalog'])
            ground[(section,key)] = ok
            fields = {'tasks':('task','owner','deadline','project'),
                      'relations':('verdict','reason'),
                      'revisions':('status','old','new','delta','reason','changed_fields')}[section]
            semantic_ok = bool(expected and all(f in row and row[f]==expected[f] for f in fields))
            counts['grounded_claims'] += ok and semantic_ok
            counts['total_claims'] += 1
            for e in row['evidence']:
                counts['evidence_refs'] += 1
                counts['valid_evidence_refs'] += verify_proof(case,e)
            if not ok:
                stages.add('evidence')
    task_correct = Counter()
    for key, expected in actual['tasks'].items():
        row = predicted['tasks'].get(key,{})
        ok = ground.get(('tasks',key),False)
        counts['discovered_tasks'] += ok
        for field in ('task','owner','deadline','project'):
            task_correct[field] += ok and field in row and row[field] == expected[field]
        if not row:
            stages.add('discovery')
        elif any(field not in row or row[field] != expected[field] for field in ('task','owner','deadline','project')):
            stages.add('extraction')
    n_tasks = len(actual['tasks'])
    metrics['task_discovery_recall'] = fraction(counts['discovered_tasks'],n_tasks)
    counts['extra_tasks'] = len(predicted['tasks'].keys()-actual['tasks'].keys())
    metrics['task_discovery_precision'] = fraction(counts['discovered_tasks'],len(predicted['tasks']))
    for field in ('task','owner','deadline','project'):
        metrics[field+'_accuracy'] = fraction(task_correct[field],n_tasks)
    per_predicate = {}
    for key in actual['relations'].keys() | predicted['relations'].keys():
        expected, row = actual['relations'].get(key), predicted['relations'].get(key)
        if row and row.get('verdict') not in {'SUPPORTED','REFUTED','UNKNOWN','CONFLICT','NOT_ASSESSABLE'}:
            raise ValueError('invalid relation verdict')
        positive = bool(row and row['verdict'] == 'SUPPORTED')
        true = bool(expected and expected['verdict'] == 'SUPPORTED')
        ok = ground.get(('relations',key),False)
        tp = positive and true and ok
        fp, fn = positive and not tp, true and not tp
        counts['relation_tp'] += tp
        counts['relation_fp'] += fp
        counts['relation_fn'] += fn
        c = per_predicate.setdefault(key[0],Counter())
        c.update(tp=int(tp),fp=int(fp),fn=int(fn))
        if expected and not true:
            counts['state_questions'] += 1
            counts['correct_states'] += bool(row and row['verdict']==expected['verdict']
                and row.get('reason')==expected['reason'] and ok)
        if row and row['verdict']=='REFUTED' and (not expected or expected['verdict']!='REFUTED' or not ok):
            counts['unsupported_negative'] += 1
        if positive and key[0]=='SAME_TASK':
            pair = tuple(sorted(key[1:]))
            same = expected or actual['relations'].get(('SAME_TASK',key[2],key[1]))
            vote = merge_votes.setdefault(pair,dict(hard=False,valid=False))
            vote['hard'] |= bool(same and same['verdict']=='REFUTED')
            vote['valid'] |= bool(same and same['verdict']=='SUPPORTED' and
                grounded(case,row,same,catalog=gold['evidence_catalog']))
        if positive and key[0]=='REVISES_PLAN' and not tp:
            counts['false_revision_relations'] += 1
            false_revision_keys.add(key[1:])
        if positive and key[0]=='REVISES_PLAN':
            published_revision_keys.add(key[1:])
        error = classify_link_case(key[1],key[2],true,bool(tp))
        if fp:
            error = classify_link_case(key[1],key[2],False,True)
        if error:
            bad.append(asdict(error))
        if fp or fn or (expected and not true and not (row and row['verdict']==expected['verdict'] and row.get('reason')==expected['reason'] and ok)):
            stages.add('relation')
    counts['published_merges'] = len(merge_votes)
    counts['hard_false_merge'] = sum(v['hard'] for v in merge_votes.values())
    counts['unsupported_merge'] = sum(not v['hard'] and not v['valid'] for v in merge_votes.values())
    tp,fp,fn = (counts[k] for k in ('relation_tp','relation_fp','relation_fn'))
    # Existing deterministic implementation, wrapped for M2's N/A convention.
    common = binary_metrics(tp,fp,0,fn)
    metrics['relation_precision'] = {**fraction(tp,tp+fp),'value':common['precision'] if tp+fp else None}
    metrics['relation_recall'] = {**fraction(tp,tp+fn),'value':common['recall'] if tp+fn else None}
    metrics['relation_by_predicate'] = {p:dict(precision=fraction(c['tp'],c['tp']+c['fp']),
        recall=fraction(c['tp'],c['tp']+c['fn'])) for p,c in sorted(per_predicate.items())}
    metrics['relation_state_accuracy'] = fraction(counts['correct_states'],counts['state_questions'])
    revision_correct = 0
    for key in actual['revisions'].keys() | predicted['revisions'].keys():
        expected, row = actual['revisions'].get(key),predicted['revisions'].get(key)
        if row and (row.get('status') not in {'COMPUTABLE','BLOCKED','NOT_COMPARABLE','NOT_APPLICABLE'}
                    or (row.get('delta') is not None and type(row['delta']) is not int)):
            raise ValueError('invalid revision status or delta')
        ok = bool(expected and row and ground.get(('revisions',key)) and all(
            f in row and row[f]==expected[f] for f in ('status','old','new','delta','reason','changed_fields')))
        if ok:
            prerequisites = [('SAME_TASK',*key),('REVISES_PLAN',*key)]
            ok = all(k in predicted['relations'] and k in actual['relations']
                     and all(predicted['relations'][k].get(f)==actual['relations'][k][f]
                             for f in ('verdict','reason'))
                     and ground.get(('relations',k),False) for k in prerequisites)
        revision_correct += bool(ok)
        if row and (row.get('delta') is not None or row.get('status')=='COMPUTABLE') and not ok:
            counts['false_revision_comparisons'] += 1
            false_revision_keys.add(key)
        if row and (row.get('delta') is not None or row.get('status')=='COMPUTABLE'):
            published_revision_keys.add(key)
        if not ok:
            stages.add('revision')
    counts['false_revision'] = len(false_revision_keys)
    metrics['false_revision_rate'] = fraction(len(false_revision_keys),len(published_revision_keys))
    metrics['revision_accuracy'] = fraction(revision_correct,len(actual['revisions']))
    required_answer_refs = sorted({ref for s in sections for row in gold[s] for ref in row['evidence']})
    answer_grounded = grounded(case,dict(evidence=answer['answer_evidence']),
                              dict(evidence=required_answer_refs),allow_stale=True,catalog=gold['evidence_catalog'])
    answer_semantic = (answer['answer_status']==gold['answer_status']
                       and sorted(answer['abstentions'])==sorted(gold['abstentions']))
    counts['grounded_claims'] += answer_grounded and answer_semantic
    counts['total_claims'] += 1
    for e in answer['answer_evidence']:
        counts['evidence_refs'] += 1
        counts['valid_evidence_refs'] += verify_proof(case,e)
    abstention_ok = answer_semantic and answer_grounded
    metrics['abstention_accuracy'] = fraction(int(abstention_ok),1)
    if not abstention_ok:
        stages.add('answer')
    metrics['evidence_groundedness'] = fraction(counts['grounded_claims'],counts['total_claims'])
    metrics['evidence_readback'] = fraction(counts['valid_evidence_refs'],counts['evidence_refs'])
    metrics['false_merge_rate'] = fraction(counts['hard_false_merge']+counts['unsupported_merge'],counts['published_merges'])
    for k in ('hard_false_merge','unsupported_merge','false_revision','unsupported_negative'):
        counts[k] += 0
    order = ('extraction','discovery','evidence','relation','revision','answer')
    return dict(case_id=case['case_id'], status='SCORED',metrics=metrics,counts=dict(counts),
                failure_stage=next((s for s in order if s in stages),None),
                failure_stages=[s for s in order if s in stages],bad_cases=bad)


def score_run(case, gold, record):
    """Validate execution provenance before granting any business score.

    Receipts detect accidental fabrication/mismatch; they are local audit data,
    not cryptographic attestation against a malicious process editing files.
    """
    from .fixtures import digest
    from .runner import PublicTools

    result = score_case(case,gold,None)
    if record is None:
        return result
    if not isinstance(record,dict) or not isinstance(record.get('execution'),dict):
        result.update(status='CONTRACT_ERROR',failure_stage='execution_contract')
        return result
    execution = record.get('execution',{})
    result['observability'] = dict(adapter_kind=record.get('adapter_kind'),execution=execution,
                                 run_status=record.get('status'),input_sha256=record.get('input_sha256'))
    if record.get('status') == 'NOT_RUN':
        return result
    if record.get('status') == 'RUN_ERROR':
        result.update(status='RUN_ERROR',failure_stage=record.get('failure_stage','agent_execution'))
        return result
    eligible = (record.get('schema')=='m3-run/v1' and record.get('case_id')==case['case_id']
        and record.get('input_sha256')==digest(case) and record.get('status')=='EXECUTED'
        and execution.get('target_action')=='analyze_task_impact' and execution.get('target_invocations')==1)
    tools = PublicTools(case)
    receipts = execution.get('tool_receipts',[])
    try:
        if not isinstance(receipts,list) or not receipts:
            eligible = False
        else:
            for receipt in receipts:
                if receipt['tool']=='list_sources' and receipt['arguments']=={}:
                    tools.list_sources()
                elif receipt['tool']=='read_source' and set(receipt['arguments'])=={'doc_id'}:
                    try:
                        tools.read_source(receipt['arguments']['doc_id'])
                    except KeyError:
                        pass
                else:
                    eligible = False
            eligible = eligible and tools.receipts==receipts and any(
                r['tool']=='read_source' and r['status']=='OK' for r in receipts)
    except (TypeError,KeyError,ValueError):
        eligible = False
    if not eligible:
        result['failure_stage'] = 'execution_contract'
        return result
    # A returned citation must have been available through an actual read.
    read_docs = {r['arguments']['doc_id'] for r in receipts if r['tool']=='read_source' and r['status']=='OK'}
    try:
        if record.get('answer') is None:
            raise ValueError('missing answer')
        answer = record['answer']
        if not isinstance(answer,dict):
            raise ValueError('answer must be an object')
        if set(answer)!={'tasks','relations','revisions','answer_status','abstentions','answer_evidence'}:
            raise ValueError('unexpected answer fields')
        proofs = [e for section in ('tasks','relations','revisions') for row in answer[section]
                  for e in row['evidence']] + answer['answer_evidence']
        if any(e['ref'].rsplit(':',1)[0] not in read_docs for e in proofs):
            result['failure_stage'] = 'execution_contract'
            return result
        scored = score_case(case,gold,record['answer'])
    except (ValueError,TypeError,KeyError):
        result.update(status='CONTRACT_ERROR',failure_stage='output_contract')
        return result
    scored['observability'] = result['observability']
    scored['scope'] = ('SCRIPTED_WIRING_ONLY' if record.get('adapter_kind')=='SCRIPTED'
                       else 'OFFLINE_ADAPTER_OUTPUT')
    return scored
