"""Separate evaluator for NEW public M3-G questions; never imported by planning.

Coverage is an observable work-category check, not proof of correct semantics.
Hand-scripted FakeModel coverage establishes contracts only.
"""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path

# Fixed public acceptance categories, evaluated only after predictions exist.
# No historical Gold or existing scorer is read or modified.
REQUIRED = [
    {'inspect:owner'}, {'relation:IN_PROJECT'}, {'relation:AFFECTED_BY'},
    {'relation:SAME_TASK'}, {'relation:SAME_TASK', 'relation:REVISES_PLAN', 'compare_dates'},
    {'relation:AFFECTED_BY', 'conflict_check'}, {'clarify', 'relation:IN_PROJECT'},
    {'relation:SAME_TASK'},
    {'relation:AFFECTED_BY', 'inspect:owner', 'relation:SAME_TASK', 'relation:REVISES_PLAN', 'compare_dates'},
    {'clarify'}, {'refuse'}, {'discover:change'},
]


def categories(plan):
    found = set()
    for step in plan['steps']:
        if step['kind'] == 'relation':
            found.add('relation:' + step['predicate'])
        elif step['kind'] == 'inspect':
            found.add('inspect:' + step['field'])
        elif step['kind'] == 'discover':
            found.add('discover:' + step['target_kind'])
        else:
            found.add(step['kind'])
    sources = {h for step in plan['steps'] for h in step['source_handles']}
    if {'s_change', 's_counter'} <= sources:
        found.add('conflict_check')
    if plan['refusal_reasons']:
        found.add('refuse')
    return found


def score(report):
    rows, covered, required, wrong, endpoint_count, duplicates, incomplete = [], 0, 0, 0, 0, 0, 0
    rejected_endpoint_outputs = 0
    for entry in report['results']:
        result, ordinal = entry['result'], entry['ordinal']
        plan = result['plan']
        found = categories(plan) if plan else set()
        expected = REQUIRED[ordinal]
        missing = sorted(expected - found)
        covered += len(expected & found)
        required += len(expected)
        incomplete += bool(missing)
        steps = plan['steps'] if plan else []
        duplicates += sum(n - 1 for n in Counter(s['execution_key'] for s in steps).values())
        # Binding and workspace are host invariants; output-invalid unknown handles
        # are counted as failures even when no accepted plan is available.
        rejected_endpoint_outputs += bool(set(result['errors']) & {
            'UNKNOWN_HANDLE', 'INVALID_TYPED_RELATION', 'INVALID_INSPECTION', 'INVALID_DATE_COMPARISON',
        })
        for step in steps:
            endpoint_count += len(step['endpoints'])
            wrong += sum(e['workspace_id'] != plan['workspace_id'] for e in step['endpoints'])
        rows.append(dict(ordinal=ordinal, status=result['status'], required=sorted(expected),
                         observed=sorted(found), missing=missing, calls=result['provider_calls']))
    return dict(mode=report['mode'], semantic_quality_claim=False,
                coverage=covered / required if required else None, covered_goals=covered,
                required_goals=required, wrong_endpoints=wrong, accepted_endpoint_bindings=endpoint_count,
                wrong_endpoint_rate=wrong / endpoint_count if endpoint_count else None,
                endpoint_error_definition='Accepted unknown/type/workspace bindings; semantic relevance is not measured.',
                rejected_endpoint_outputs=rejected_endpoint_outputs,
                invalid_model_outputs=sum(r['result']['status'] == 'OUTPUT_INVALID' for r in report['results']),
                duplicate_subquestions=duplicates, missed_decomposition_questions=incomplete,
                provider_calls=report['calls'], per_question=rows,
                remote_provider_calls=report.get('remote_provider_calls', 0 if report['mode'] == 'fake' else report['calls']),
                limitations='Category coverage only; live subset is not a generalization benchmark. '
                            'Rejected malformed plans are not evidence of no relationships.')


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('predictions', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = score(json.loads(args.predictions.read_text(encoding='utf8')))
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False))
