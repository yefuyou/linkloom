"""Offline v2 diagnostics, with quote, verdict and semantic denominators separate."""
import argparse
from collections import Counter
import json
from pathlib import Path

from linkloom.agents.model_adapter import ModelResponse
from linkloom.evaluation.canary_trust import VERSION, canonical, sha, save_new, summarize_attempts
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from scripts.ami_canary_sources import extract_sources, load_sources, GUARD


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]


def discovery_summary(batches, sources):
    by_id = {a['artifact_id']: a for a in sources}
    claims, tasks = [], []
    failures = Counter()
    valid = partial = 0
    for batch in batches:
        result = batch['result']
        failures.update(f['reason'] for f in result['failures'])
        valid += len(result['valid_observations'])
        partial += len(result['partial_observations'])
        for local, extraction in enumerate(result['extractions']):
            artifact_id = f'{batch["meeting_id"]}:chunk:{batch["batch_start"] + local:04d}'
            for index, claim in enumerate(extraction['claims']):
                claims.append(claim)
                if claim.get('task'):
                    text, task = by_id[artifact_id]['content'], claim['task']
                    quote = task['text']
                    exact = bool(quote) and text.count(quote) == 1
                    parts = task['description_parts']
                    tasks.append(dict(artifact_id=artifact_id, proposal_index=index, claim=claim,
                        exact_unique_quote=exact,
                        parts_exact=exact and all(quote.count(p) == 1 for p in parts),
                        discovery_failures=[f['reason'] for f in result['failures']
                            if f['artifact_id'] == artifact_id and f.get('proposal_index') == index]))
    types = Counter(c['claim_type'] for c in claims)
    return dict(f_claim_types=dict(types), f_task_proposals=len(tasks),
        f_plan_action_claims=types['PLAN'] + types['ACTION'],
        f_decision_proposals=types['DECISION'], f_proposal_claims=types['PROPOSAL'],
        discovery_valid=valid, discovery_partial=partial, discovery_rejections=dict(failures), tasks=tasks,
        quote_validation=dict(denominator=len(tasks), numerator=sum(t['exact_unique_quote'] and t['parts_exact'] for t in tasks),
                              meaning='mechanical exact quote/parts validation only'),
        verdict_consistency=dict(status='NOT_EVALUATED', denominator=None, numerator=None,
                                 reason='no independent verdict labels; never inferred from quote validity'),
        semantic_qualification=dict(status='NOT_EVALUATED', precision=None, recall=None, f1=None,
                                    business_approval_accuracy=None),
        status='DEV_DIAGNOSTIC', independent_review='INDEPENDENT_REVIEW_NOT_VERIFIED')


def replay(inputs, historical_run, output):
    """Replay only the original null-profile contract, never relabel outputs v3."""
    from scripts.run_ami_canary_v2 import code_identity, head
    seal = read(historical_run / 'output_seal.json')
    for relative, expected in seal['files'].items():
        if sha((historical_run / relative).read_bytes()) != expected:
            raise ValueError('historical seal mismatch: ' + relative)
    old_plan = read(historical_run / 'plan.json')
    if old_plan['task_profile'] is not None:
        raise ValueError('legacy replay requires null-profile historical contract')
    sources, _ = load_sources(inputs)
    original_requests = rows(historical_run / 'actual_requests.jsonl')
    original_models = rows(historical_run / 'model_responses.jsonl')
    cursor, batches = 0, []

    def authorize(request):
        if request.to_dict() != original_requests[cursor]:
            raise ValueError('historical request contract drift')
        return ProviderRequestAuthorization.approve(request, guard_id=GUARD,
            reservation_id=sha(canonical(request.to_dict())), estimated_cost_upper_bound_usd=.25)

    class Replay:
        def complete(self, request):
            nonlocal cursor
            response = ModelResponse.from_dict(original_models[cursor])
            cursor += 1
            return response

    extract_sources(sources, Replay(), authorize, batches.append, task_profile=None)
    if cursor != 28:
        raise ValueError('replay did not consume all 28 original outputs')
    output.mkdir(parents=True, exist_ok=False)
    save_new(output / 'discovery.json', batches)
    summary = discovery_summary(batches, read(historical_run / 'sources.json'))
    save_new(output / 'summary.json', summary)
    from scripts.run_ami_canary_v2 import runtime_identity
    save_new(output / 'replay_receipt.json', dict(version=VERSION, frozen_sha=head(), code_hashes=code_identity(), runtime=runtime_identity(),
        task_profile=None, provider_calls=0, replayed_outputs=cursor, complete_meetings={'ES2002a': 8, 'ES2005c': 20},
        source_hashes={a.artifact_id: a.content_hash for a in sources},
        input_files={relative: sha((inputs / relative).read_bytes()) for relative in
                     ('manifest.json', 'transcripts/ES2002a.json', 'transcripts/ES2005c.json')},
        historical_seal_path=str((historical_run / 'output_seal.json').resolve()),
        historical_seal_sha256=sha((historical_run / 'output_seal.json').read_bytes()),
        discovery_sha256=sha((output / 'discovery.json').read_bytes()), status='PASS'))
    print({k: v for k, v in summary.items() if k != 'tasks'})


def analyze(run, output):
    from scripts.run_ami_canary_v2 import code_identity, head
    seal = read(run / 'output_seal.json')
    for relative, expected in seal['files'].items():
        if sha((run / relative).read_bytes()) != expected:
            raise ValueError('output seal mismatch: ' + relative)
    batches = [read(p) for p in sorted((run / 'discovery').glob('*.json'))]
    summary = discovery_summary(batches, read(run / 'sources.json'))
    summary['transport'] = summarize_attempts(run / 'attempts')
    usages = []
    for attempt in summary['transport']['attempts']:
        evidence = attempt['evidence']
        if 'raw_response_canonical_sha256' in evidence:
            response = read(run / f'wire/{attempt["sequence"]:04d}.response.json')
            if (sha(canonical(response)) != evidence['raw_response_canonical_sha256']
                    or response.get('response_id') != evidence.get('provider_response_id')):
                raise ValueError('attributable SDK receipt mismatch')
        usage = evidence.get('usage') or {}
        prompt, total = usage.get('prompt_token_count'), usage.get('total_token_count')
        estimated = ((prompt * .75 + (total - prompt) * 3.75) / 1e6
                     if type(prompt) is int and type(total) is int and total >= prompt else None)
        usages.append(dict(sequence=attempt['sequence'], usage=usage or None,
                           estimated_list_price_usd=estimated, actual_billed_usd=None))
    summary['usage'] = dict(per_attempt=usages,
        estimated_list_price_usd_known_receipts=sum(r['estimated_list_price_usd'] for r in usages
                                                   if r['estimated_list_price_usd'] is not None),
        attempts_without_cost_receipt=sum(r['estimated_list_price_usd'] is None for r in usages),
        price_source='https://ai.google.dev/gemini-api/docs/pricing', price_checked_date='2026-10-23',
        rates_usd_per_million=dict(input=.75, output_including_thought=3.75),
        policy='all reported total-minus-prompt tokens charged at output rate; absent usage remains unknown',
        actual_billed_usd=None)
    summary['unattempted_requests'] = read(run / 'completion.json')['unattempted_requests']
    summary['analyzer_identity'] = dict(version=VERSION, frozen_sha=head(), code_hashes=code_identity())
    summary['run_identity'] = read(run / 'plan.json')
    summary['session'] = read(run / 'provider/budget.json')
    output.mkdir(parents=True, exist_ok=False)
    save_new(output / 'summary.json', summary)
    print({k: v for k, v in summary.items() if k not in ('tasks', 'run_identity', 'session', 'analyzer_identity', 'transport')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-inputs', type=Path)
    args = parser.parse_args()
    if args.replay_inputs:
        replay(args.replay_inputs, args.run, args.output)
    else:
        analyze(args.run, args.output)
