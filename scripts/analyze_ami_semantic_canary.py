"""Offline post-seal diagnostics. No provider import or call."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]


def analyze(run, data, output):
    seal = read(run / 'output_seal.json')
    assert seal['sealed']
    for name, digest in seal['files'].items():
        assert hashlib.sha256((run / name).read_bytes()).hexdigest() == digest, name
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    batches, raw = rows(run / 'discovery.jsonl'), rows(run / 'sdk_raw_responses.jsonl')
    sources = {a['artifact_id']: a for a in read(run / 'sources.json')}
    sidecars = {a['artifact_id']: a for a in read(run / 'native_sidecars.json')}
    models = rows(run / 'model_responses.jsonl')
    claims, tasks, losses, per_call = [], [], [], []
    for batch in batches:
        result = batch['result']
        for local, extraction in enumerate(result['extractions']):
            identity = f"{batch['meeting_id']}:chunk:{batch['batch_start']+local:04d}"
            for index, claim in enumerate(extraction['claims']):
                row = dict(claim_id=f'{identity}:claim:{index}', artifact_id=identity,
                           evidence_ref=extraction['receipt']['evidence_ref'], **claim)
                claims.append(row)
                if claim.get('task'):
                    task = claim['task']
                    text = sources[identity]['content']
                    start = text.find(task['text'])
                    exact = start >= 0 and text.count(task['text']) == 1
                    canonical_start = sidecars[identity]['canonical_start'] + start if exact else None
                    anchors = [a for a in sidecars[identity]['native_utterances'] if exact and
                        a['char_start'] < canonical_start + len(task['text']) and a['char_end'] > canonical_start]
                    rejection = [f for f in result['failures']
                        if f['artifact_id'] == identity and f.get('proposal_index') == index]
                    tasks.append(dict(**row, exact_unique_quote=exact, canonical_start=canonical_start,
                        native_anchors=anchors, discovery_failures=rejection))
            losses.extend(result['failures'] if local == 0 else [])
    for sequence, (response, model) in enumerate(zip(raw, models), 1):
        usage = response['usage_metadata']
        prompt, candidate = usage['prompt_token_count'], usage['candidates_token_count']
        thoughts = usage.get('thoughts_token_count')
        per_call.append(dict(sequence=sequence, model=response['model_version'],
            response_id=response.get('response_id'), usage=usage,
            normalized_usage=model['usage'],
            estimated_list_price_usd=(prompt*.75+(candidate+(thoughts or 0))*3.75)/1e6,
            thought_count_policy='null is not reported; total minus prompt minus candidates used to check',
            finish_reason=response['candidates'][0]['finish_reason']))
    from linkloom.semantic_ingestion.text_scopes import quoted_ranges
    from linkloom.semantic_ingestion.task_discovery_veto import non_asserted
    veto_diagnostics = []
    for task in tasks:
        text = sources[task['artifact_id']]['content']
        quote = task['task']['text']
        start = text.find(quote)
        parts = task['task']['description_parts']
        overlaps = [(left, right) for left, right in quoted_ranges(text)
                    if start >= 0 and left < start + len(quote) and right > start]
        veto_diagnostics.append(dict(claim_id=task['claim_id'], exact_quote_start=start,
            full_chunk_non_asserted=non_asserted(text, parts, 0, len(text)),
            isolated_quote_non_asserted=non_asserted(quote, parts, 0, len(quote)),
            overlapping_quote_scopes=[dict(start=left, end=right, opening=text[left:left+100])
                                      for left, right in overlaps]))
    (output/'veto_diagnostics.json').write_text(json.dumps(veto_diagnostics, ensure_ascii=False, indent=2), encoding='utf8')
    gold = rows(data / 'evaluator_only/qrels.jsonl')
    provenance = rows(data / 'evaluator_only/provenance.jsonl')
    acts = {}
    for meeting in ('ES2002a', 'ES2005c'):
        for act in read(data / f'inputs/transcripts/{meeting}.json')['utterances']:
            acts[act['utterance_id']] = act
    # This narrow post-hoc diagnostic tests exact work-quote recovery ONLY.
    # Single linked DA must contain a complete work statement. The UI fragment
    # and two-DA marketing annotation cannot meet this mechanical contract.
    exact_action_ids = {'ES2002a:ES2002a.rdhillon.s.4', 'ES2005c:ES2005c.JacquelinePalmer.s.12'}
    official_cases = []
    for query in gold:
        if query['meeting_id'] not in ('ES2002a', 'ES2005c'):
            continue
        section = next(p['section'] for p in provenance if p['query_id'] == query['query_id'])
        targets = [acts[identity] for identity in query['relevant_dialogue_act_ids']]
        matches = [task for task in tasks if task['artifact_id'].startswith(query['meeting_id']+':')
            and any(task['task']['text'] == act['text'] for act in targets)]
        official_cases.append(dict(**query, section=section,
            native_targets=[{k: a[k] for k in ('utterance_id','speaker','text','char_start','char_end','source_member','member_sha256')} for a in targets],
            exact_work_quote_eligible=query['query_id'] in exact_action_ids,
            exact_work_quote_claim_ids=[task['claim_id'] for task in matches],
            extraction_semantic_precision=None, extraction_semantic_recall=None))
    eligible = [case for case in official_cases if case['exact_work_quote_eligible']]
    input_tokens = sum(r['usage']['prompt_token_count'] for r in per_call)
    output_tokens = sum(r['usage']['candidates_token_count'] for r in per_call)
    total = sum(r['usage']['total_token_count'] for r in per_call)
    summary = dict(evaluation_class='EXPLORATORY_DIAGNOSTIC', provider_calls=len(raw),
        input_tokens=input_tokens, output_candidate_tokens=output_tokens,
        thought_tokens_reported=sum(r['usage'].get('thoughts_token_count') or 0 for r in per_call),
        unallocated_tokens=total-input_tokens-output_tokens, total_tokens=total,
        estimated_list_price_usd=sum(r['estimated_list_price_usd'] for r in per_call),
        price_source='https://ai.google.dev/gemini-api/docs/pricing',
        rates_usd_per_million=dict(input=.75, output_including_thought=3.75),
        actual_billed_usd=None, credit_attribution='USER_CONFIRMED',
        budget=read(run/'provider/budget.json'), claim_types=dict(Counter(c['claim_type'] for c in claims)),
        f_failure_count=sum(bool(e['failure_code']) for b in batches for e in b['result']['extractions']),
        discovery_failure_counts=dict(Counter(f['reason'] for f in losses)),
        discovery_valid=sum(len(b['result']['valid_observations']) for b in batches),
        discovery_partial=sum(len(b['result']['partial_observations']) for b in batches),
        formal_extraction_metrics=dict(status='NOT_EVALUATED', precision=None, recall=None,
            reason='official summary links do not exhaustively label task/claim semantics, owners or deadlines'),
        posthoc_exact_work_quote_probe=dict(evaluation_class='EXPLORATORY_DIAGNOSTIC', denominator=len(eligible),
            f_hits=sum(bool(c['exact_work_quote_claim_ids']) for c in eligible),
            discovery_hits=0, f_coverage=sum(bool(c['exact_work_quote_claim_ids']) for c in eligible)/len(eligible),
            precision=None, is_full_task_recall=False, owner_deadline_evaluation='NOT_EVALUATED'),
        product_e2e='NOT_RUN', semantic_completeness=False)
    for name, value in [('summary.json',summary),('claims.json',claims),('tasks.json',tasks),
                        ('official_cases.json',official_cases),('per_call_usage.json',per_call)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    print({k: summary[k] for k in ('provider_calls','total_tokens','estimated_list_price_usd','claim_types','discovery_failure_counts')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run','data','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    analyze(args.run,args.data,args.output)
