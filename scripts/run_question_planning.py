"""Reproduce public plans: scripted FakeModel contracts or <=3 live smoke calls.

Writes exact requests before network admission and structured outputs afterward.
No scorer or expected goals are imported. Each output directory is exclusive.
"""
from argparse import ArgumentParser
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_question_planner import InvestigationPlanner, PlanningLimits
from tests.question_planning_support import public_inputs, planning_context, INPUT


def save(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def run(mode, output):
    output.mkdir(parents=True, exist_ok=False)
    public = public_inputs()
    selected = list(range(12)) if mode == 'fake' else [0, 8, 11]
    limits = PlanningLimits(max_provider_calls=1)
    manifest = dict(mode=mode, selected_question_ordinals=selected,
                    input_sha256=canonical_hash(public), limits=asdict(limits),
                    max_generation_calls=0 if mode == 'fake' else 3,
                    max_live_input_tokens=3 * limits.max_input_tokens,
                    max_live_output_tokens=3 * limits.max_output_tokens,
                    estimated_usd_reservation_per_call=0.25 if mode == 'smoke' else 0,
                    retry_policy='NONE; SDK transport attempts=1',
                    input_file=str(INPUT.relative_to(ROOT)))
    save(output / 'manifest.json', manifest)
    client = None
    if mode == 'fake':
        from tests.question_planning_examples import scripted_outputs
        from tests.unit.test_task_relation_question_planner import FakeModel
        scripts = scripted_outputs()
        model_id = 'fake-planner'
    else:
        from scripts.check_task_semantic_provider import connect
        from linkloom.agents.providers.gemini_api import GeminiProviderAdapter, GEMINI_38_FLASH_MODEL_ID
        model_id = GEMINI_38_FLASH_MODEL_ID
        try:
            client = connect()
            adapter = GeminiProviderAdapter(client.models)
        except Exception as error:
            save(output / 'results.json', dict(mode=mode, status='PROVIDER_UNAVAILABLE',
                 error_type=type(error).__name__, calls=0, results=[]))
            return
    results = []
    admitted = 0
    try:
        for ordinal in selected:
            question = public['questions'][ordinal]
            context, discovery = planning_context(question, public)
            if mode == 'fake':
                adapter = FakeModel(scripts[ordinal])

            def guard(request):
                nonlocal admitted
                if mode != 'fake' and admitted >= 3:
                    raise ValueError('THREE_CALL_SMOKE_LIMIT')
                save(output / f'request-{ordinal}.json', request.to_dict())
                admitted += 1
                # A declared admission estimate, not a provider price or invoice.
                return ProviderRequestAuthorization.approve(request, guard_id='question-smoke',
                    reservation_id=f'question-{ordinal}', estimated_cost_upper_bound_usd=0 if mode == 'fake' else 0.25)

            result = InvestigationPlanner(adapter, model_id=model_id, request_guard=guard,
                guard_id='question-smoke', limits=limits).plan(context, discovery=discovery)
            row = dict(ordinal=ordinal, question=context.question, result=result.to_dict())
            save(output / f'result-{ordinal}.json', row)
            results.append(row)
            print(json.dumps(dict(ordinal=ordinal, status=result.status, errors=list(result.errors)), ensure_ascii=False), flush=True)
    finally:
        if client:
            client.close()
        save(output / 'results.json', dict(mode=mode, model_id=model_id,
             contract_only=mode == 'fake', calls=sum(r['result']['provider_calls'] for r in results),
             remote_provider_calls=0 if mode == 'fake' else sum(r['result']['provider_calls'] for r in results),
             results=results))


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['fake', 'smoke'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.mode, args.output)
