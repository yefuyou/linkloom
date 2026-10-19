"""Offline M3-K contract evaluation. No live/provider mode and no Gold edits."""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_question_models import PlanningLimits
from scripts.score_question_planning import REQUIRED, categories
from tests.question_planning_examples import scripted_outputs
from tests.question_planning_support import planning_context, public_inputs
from tests.unit.test_task_relation_question_planner import FakeModel, arguments, planner, relation
from tests.unit.test_investigation_planner_quality import owner, discover


def output_rows():
    # A NEW authored M3-K response, not a repair of the historical prediction.
    outputs = deepcopy(scripted_outputs())
    outputs[11]['steps'][0]['evidence_gap'] = (
        'The question requires a concrete supplier switch, but no change endpoint was admitted; '
        'candidate misses cannot establish absence.')
    return outputs


def replay_contracts():
    results = []
    covered = required = planned = refused = 0
    for ordinal, (question, output) in enumerate(zip(public_inputs()['questions'], output_rows())):
        ctx, hints = planning_context(question)
        result = planner(FakeModel(output)).plan(ctx, discovery=hints)
        found = categories(result.plan.to_dict()) if result.plan else set()
        covered += len(REQUIRED[ordinal] & found)
        required += len(REQUIRED[ordinal])
        planned += result.status == 'PLANNED'
        refused += result.status == 'REFUSED'
        results.append(dict(ordinal=ordinal, question=question['question'],
                            observed_categories=sorted(found), missing_categories=sorted(REQUIRED[ordinal] - found),
                            result=result.to_dict()))
    return dict(contract_only=True, semantic_quality_claim=False,
                category_coverage=dict(covered=covered, required=required, rate=covered / required),
                valid_plan_rate=dict(valid=planned, attempted=len(results) - refused,
                                     rate=planned / (len(results) - refused)),
                valid_outputs=planned + refused, refusals=refused,
                excess_steps=sum(r['result']['diagnostics']['excess_steps'] for r in results),
                results=results)


def dedup_probes():
    ctx, _ = planning_context(public_inputs()['questions'][8])
    rows = []

    def probe(name, steps, duplicate):
        result = planner(FakeModel(arguments(*steps))).plan(ctx)
        observed = 'DUPLICATE_WORK' in result.errors
        rows.append(dict(name=name, expected_duplicate=duplicate, observed_duplicate=observed,
                         status=result.status, errors=list(result.errors)))

    probe('owner_question_paraphrase', [owner(), owner('again', question='Who is responsible?')], True)
    probe('symmetric_relation', [relation('a', 'SAME_TASK', handles=('new', 'old')),
                                relation('b', 'SAME_TASK', handles=('old', 'new'))], True)
    counter = owner('counter', evidence_gap='Reconcile the contradictory evidence before selecting an owner.')
    counter['source_handles'] = ['s_old', 's_counter']
    probe('different_evidence_scope', [owner(), counter], False)
    probe('different_time_range', [owner(time_scope=dict(start='2026-10-01', end='2026-10-31')),
          owner('later', time_scope=dict(start='2026-11-01', end='2026-11-30'),
                evidence_gap='The November scope needs separate evidence inspection.')], False)
    probe('same_discovery_different_question', [discover(), discover('again', question='Locate that vendor switch.')], True)
    probe('different_unknown_entities', [discover(), discover('other', missing_entities=['deployment incident'])], False)
    later = relation('later', 'RELATED_TO', ('identity',), handles=('new', 'old'))
    later['evidence_gap'] = 'Recheck the relation under the independent identity premise.'
    probe('different_dependencies', [owner(), relation('identity', 'SAME_TASK', handles=('new', 'old')),
          relation('first', 'RELATED_TO', ('owner',), handles=('new', 'old')), later], False)

    public = public_inputs()
    old, _ = planning_context(public['questions'][0], public)
    public['sources'][0]['text'] += ' Additional source revision.'
    new, _ = planning_context(public['questions'][0], public)
    for name, operation in [('endpoint_version', owner()), ('source_only_version', discover())]:
        keys = [planner(FakeModel(arguments(operation))).plan(c).plan.steps[0].execution_key for c in (old, new)]
        rows.append(dict(name=name, expected_duplicate=False, observed_duplicate=keys[0] == keys[1],
                         status='KEY_COMPARISON', errors=[]))
    return dict(probes=rows,
                false_dedup=sum(not r['expected_duplicate'] and r['observed_duplicate'] for r in rows),
                missed_dedup=sum(r['expected_duplicate'] and not r['observed_duplicate'] for r in rows),
                probe_failures=sum(r['observed_duplicate'] != r['expected_duplicate'] or
                                   (not r['expected_duplicate'] and r['status'] not in {'PLANNED', 'KEY_COMPARISON'})
                                   for r in rows))


def historical_live():
    directory = ROOT / 'docs/requirements/m3-question-planning/evidence/live'
    report = json.loads((directory / 'results.json').read_text(encoding='utf8'))
    metrics = json.loads((directory / 'metrics.json').read_text(encoding='utf8'))
    replayed = []
    required_fields = {'id', 'kind', 'question', 'handles', 'source_handles', 'depends_on',
                       'predicate', 'field', 'target_kind', 'missing_entities'}
    for entry in report['results']:
        original = entry['result']
        if not original['plan']:
            replayed.append(dict(ordinal=entry['ordinal'], status='NOT_REPLAYABLE_ARGUMENTS_NOT_RETAINED',
                                 original_errors=original['errors']))
            continue
        plan = original['plan']
        output = dict(steps=[{k: s[k] for k in required_fields} for s in plan['steps']],
                      uncertainties=plan['uncertainties'], refusal_reasons=plan['refusal_reasons'])
        ctx, hints = planning_context(public_inputs()['questions'][entry['ordinal']])
        result = planner(FakeModel(output)).plan(ctx, discovery=hints)
        replayed.append(dict(ordinal=entry['ordinal'], status=result.status, errors=list(result.errors),
                             diagnostics=result.diagnostics))
    return dict(original_valid_plan_rate=dict(valid=2, attempted=3, rate=2 / 3),
                original_category_coverage=dict(covered=metrics['covered_goals'], required=metrics['required_goals'],
                                                rate=metrics['coverage']),
                manual_excess_review=dict(confirmed_unjustified_steps=1, potentially_justified_steps=1,
                    confirmed=[dict(ordinal=0, step_id='step_3', reason='Supplier impact is not linked to the owner evidence gap.')],
                    potential=[dict(ordinal=0, step_id='step_2', reason='Current owner may require binding the newer task source.')],
                    scope='Conservative human-readable audit of saved owner plan; no blanket extra-step judgment by category.'),
                current_validator_replay=replayed, original_provider_calls=report['calls'],
                original_provider_tokens=sum(e['result']['usage']['total_tokens'] for e in report['results']),
                saved_results_sha256=canonical_hash(report), saved_metrics_sha256=canonical_hash(metrics),
                quality_improvement_claim=False)


def budget_checks():
    ctx, _ = planning_context(public_inputs()['questions'][0])
    checks = []
    for name, limits in [('input', PlanningLimits(max_input_tokens=1)),
                         ('total_input', PlanningLimits(max_total_input_tokens=1)),
                         ('total_output', PlanningLimits(max_total_output_tokens=1))]:
        fake = FakeModel(arguments(owner()))
        result = planner(fake, limits=limits).plan(ctx)
        checks.append(dict(name=name, passed=result.status == 'BLOCKED' and not fake.requests,
                           errors=list(result.errors), fake_calls=len(fake.requests)))
    fake = FakeModel({'invalid': True})
    session = planner(fake, limits=PlanningLimits(max_provider_calls=1))
    first, second = session.plan(ctx), session.plan(ctx)
    checks.append(dict(name='invalid_output_reserves_call_without_retry',
                       passed=first.status == 'OUTPUT_INVALID' and second.status == 'BLOCKED' and len(fake.requests) == 1,
                       errors=list(second.errors), fake_calls=len(fake.requests)))
    return checks


def evaluate():
    contracts, dedup = replay_contracts(), dedup_probes()
    return dict(schema='m3-k-offline-evaluation/v1', contracts=contracts, deduplication=dedup,
                historical_live=historical_live(),
                cost_budget=dict(new_paid_provider_calls=0, new_paid_provider_cost_usd=0,
                                 model_request_allowance_increased=False,
                                 original_live_allocation_exhausted=True,
                                 fake_usage_is_not_provider_cost=True,
                                 admission_checks=budget_checks()),
                limitations=['Authored FakeModel outputs establish contracts only.',
                             'Missing historical rejected arguments prevent exact root-cause attribution.',
                             'Gap text is observable model justification; semantic necessity is not Host-certified.',
                             'Distinct unknown-entity aliases need semantic evaluation; only typed/label identity is deduplicated.'])


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: report[k] for k in ('cost_budget', 'deduplication')}, ensure_ascii=False))
