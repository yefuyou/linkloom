"""Reproduce public fixed-candidate Dev predictions, then score separate Gold.

Run from an installed checkout: python scripts/evaluate_task_relations_p2.py --output work/p2-replay-UNIQUE
No Provider/network. The only SQLite file is a new synthetic approval-flow DB.
"""
from argparse import ArgumentParser
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'src'), str(root)]
from tests.task_relations_support import INPUT, public_cases, run_case


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def evaluate(output):
    output = output.resolve()
    if not output.is_relative_to(root / 'work'):
        raise ValueError('use a new task-owned directory under work/')
    output.mkdir(parents=True, exist_ok=False)
    predictions = []
    for case in public_cases():
        temporary = output / ('tmp-' + case['case_id'])
        temporary.mkdir()
        result = run_case(case, temporary)
        predictions.append(result)
        write(output / (case['case_id'] + '.json'), result)
    # All predictions are durably written before reading the evaluator-only file.
    write(output / 'predictions.json', predictions)
    gold_path = root / 'tests/fixtures/task_relations_p2/gold/dev.json'
    gold = json.loads(gold_path.read_text(encoding='utf8'))
    expected = {q['question_id']:q for c in gold['cases'] for q in c['questions']}
    counts, states, readbacks, cases = {}, Counter(), Counter(), []
    unsupported_project = unsupported_impact = source_spoof = 0
    for case in predictions:
        sources = {s['artifact_version_id']:s for s in case['sources']}
        for row in case['predictions']:
            a = row['assessment']
            reference = expected[row['question_id']]
            p = a['predicate']
            counter = counts.setdefault(p, Counter())
            predicted, actual = a['verdict'], reference['verdict']
            counter['tp'] += predicted == actual == 'SUPPORTED'
            counter['fp'] += predicted == 'SUPPORTED' and actual != 'SUPPORTED'
            counter['fn'] += predicted != 'SUPPORTED' and actual == 'SUPPORTED'
            counter['n'] += 1
            if actual in {'UNKNOWN','REFUTED','NOT_ASSESSABLE','CONFLICT'}:
                states[actual + '_total'] += 1
                states[actual + '_correct'] += predicted == actual
            checked = []
            for e in [*a['supporting_evidence'], *a['counter_evidence'], *a['context_evidence']]:
                source = sources.get(e['span']['artifact_version_id'])
                ok = source is not None and source['content'][e['char_start']:e['char_end']] == e['quote']
                checked.append(ok)
                readbacks['quote_total'] += 1
                readbacks['quote_ok'] += ok
            for d in a['dependencies']:
                span = d['span']
                source = sources.get(span['artifact_version_id'])
                ok = source is not None and source['content'][span['char_start']:span['char_end']] == span['quote']
                checked.append(ok)
                readbacks['dependency_total'] += 1
                readbacks['dependency_ok'] += ok
            required_quotes = [e['quote'] for e in reference.get('evidence', []) if e['role'] in {'supporting', 'authority_chain'}]
            used = [e['quote'] for e in [*a['supporting_evidence'], *a['counter_evidence']]] + [d['span']['quote'] for d in a['dependencies']]
            gold_evidence_covered = all(any(q in proof for proof in used) for q in required_quotes)
            confirmed_valid = (predicted != 'SUPPORTED' or (a['evidence_validity'] and checked and all(checked)
                and gold_evidence_covered and actual == 'SUPPORTED'))
            unsupported_project += p == 'IN_PROJECT' and not confirmed_valid
            unsupported_impact += p == 'AFFECTED_BY' and not confirmed_valid
            if p == 'AFFECTED_BY' and a['object']['kind'] == 'SOURCE_CLAIM':
                source_spoof += 'HUMAN_APPROVAL_BOUND' in a.get('authority_checks',[]) or a['object']['decision_id'] is not None
            cases.append({'case_id':case['case_id'],'question_id':row['question_id'],'predicate':p,
                'gold':actual,'actual':predicted,'correct':actual==predicted,
                'gold_support_evidence_covered':gold_evidence_covered,'evidence_validity':a['evidence_validity']})
    metrics = {'relation_scores':{p:{**c,'precision':{'numerator':c['tp'],'denominator':c['tp']+c['fp']},
        'recall':{'numerator':c['tp'],'denominator':c['tp']+c['fn']}} for p,c in counts.items()},
        'state_accuracy':dict(states),'readbacks':dict(readbacks),'cases':cases,
        'unsupported_project_bindings':unsupported_project,'unsupported_direct_impacts':unsupported_impact,
        'source_claim_formal_spoofs':source_spoof,
        'excluded':[x for c in predictions for x in c['excluded']],
        'input_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        'gold_sha256':hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        'real_provider_calls':0,'holdout_executed':False,'scope':'fixed candidates; offline finite grammar; no discovery recall'}
    write(output / 'metrics.json', metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf8', errors='replace')
    result = evaluate(arguments.output)
    raise SystemExit(0 if all(c['correct'] for c in result['cases']) and not any(result[k] for k in
        ('unsupported_project_bindings','unsupported_direct_impacts','source_claim_formal_spoofs')) else 1)
