"""Post-seal AMI diagnostic counts; never a semantic scorer or Provider client."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

VERSION = 'ami-postseal-diagnostic/1.0'
read = lambda p: json.loads(p.read_text(encoding='utf8'))
sha = lambda b: hashlib.sha256(b).hexdigest()
run, historical, official, output = map(Path, sys.argv[1:])
seal = read(run / 'output_seal.json')
assert all(sha((run / name).read_bytes()) == digest for name, digest in seal['files'].items())
sources = read(run / 'sources.json')
sidecars = {s['artifact_id']: s for s in read(run / 'native_sidecars.json')}
source_map = {s['artifact_id']: s for s in sources}
raw_claims, parsed_claims, batches, f_failures = [], [], [], []
for path in sorted((run / 'wire').glob('*.response.json')):
    sequence = int(path.name.split('.')[0])
    source = sources[sequence - 1]
    response = read(path)
    for candidate in response.get('candidates') or []:
        for part in (candidate.get('content') or {}).get('parts') or []:
            call = part.get('function_call') or {}
            if call.get('name') == 'emit_semantic_candidates':
                for index, claim in enumerate((call.get('args') or {}).get('claims') or []):
                    raw_claims.append(dict(artifact_id=source['artifact_id'], sequence=sequence,
                                           raw_index=index, claim=claim))
for path in sorted((run / 'discovery').glob('*.json')):
    batch = read(path)
    batches.append(batch)
    for local, extraction in enumerate(batch['result']['extractions']):
        identity = f'{batch["meeting_id"]}:chunk:{batch["batch_start"] + local:04d}'
        if extraction.get('failure_code'):
            f_failures.append(dict(artifact_id=identity, extraction=extraction))
        for index, claim in enumerate(extraction['claims']):
            parsed_claims.append(dict(artifact_id=identity, parsed_index=index, claim=claim))
old_raw = []
for response in [json.loads(line) for line in (historical / 'sdk_raw_responses.jsonl').read_text(encoding='utf8').splitlines()]:
    for candidate in response.get('candidates') or []:
        for part in (candidate.get('content') or {}).get('parts') or []:
            call = part.get('function_call') or {}
            old_raw.extend((call.get('args') or {}).get('claims') or [])


def types(rows):
    return dict(Counter(row['claim'].get('claim_type', 'MISSING') for row in rows))


def ground(row):
    claim, text = row['claim'], source_map[row['artifact_id']]['content']
    task = claim.get('task') or {}
    quote = task.get('text')
    exact = isinstance(quote, str) and bool(quote) and text.count(quote) == 1
    start = text.find(quote) if exact else -1
    canonical_start = sidecars[row['artifact_id']]['canonical_start'] + start if exact else None
    return dict(**row, exact_unique_quote=exact, canonical_start=canonical_start,
        parts_exact=exact and bool(task.get('description_parts')) and
            all(isinstance(p, str) and p in quote for p in task.get('description_parts', [])))


tasks = [ground(row) for row in raw_claims if row['claim'].get('task')]
official_cases = []
for case in read(official):
    candidates = []
    for row in tasks:
        if not row['artifact_id'].startswith(case['meeting_id'] + ':') or row['canonical_start'] is None:
            continue
        start, quote = row['canonical_start'], row['claim']['task']['text']
        if any(a['char_start'] < start + len(quote) and a['char_end'] > start for a in case['native_targets']):
            candidates.append(row)
    official_cases.append(dict(query_id=case['query_id'], meeting_id=case['meeting_id'], section=case['section'],
        query_text=case['query_text'], native_targets=case['native_targets'],
        raw_task_anchor_overlaps=candidates, semantic_qualification='NOT_EVALUATED'))
accepted = [dict(kind=kind, observation=o) for b in batches for kind in ('valid_observations', 'partial_observations')
            for o in b['result'][kind]]
value = dict(version=VERSION, source_sha256=sha(Path(__file__).read_bytes()),
    frozen_runner_sha=read(run / 'plan.json')['frozen_sha'],
    analyzer_git_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
    config_sha256=sha(json.dumps(dict(run_seal_sha256=sha((run / 'output_seal.json').read_bytes()),
        official_cases_sha256=sha(official.read_bytes())), sort_keys=True).encode()),
    run_id=read(run / 'plan.json')['run_id'],
    historical_raw_claim_types=dict(Counter(c.get('claim_type', 'MISSING') for c in old_raw)),
    historical_raw_task_proposals=sum(bool(c.get('task')) for c in old_raw),
    raw_claim_types=types(raw_claims), parsed_f_claim_types=types(parsed_claims),
    raw_task_proposals=len(tasks), raw_decision_proposals=sum(r['claim'].get('claim_type') == 'DECISION' for r in raw_claims),
    f_failures=f_failures, raw_tasks=tasks, raw_claims=raw_claims, parsed_claims=parsed_claims,
    accepted_observations=accepted, official_cases=official_cases,
    discovery_rejections=dict(Counter(f['reason'] for b in batches for f in b['result']['failures'])),
    quote_validation=dict(numerator=sum(t['exact_unique_quote'] and t['parts_exact'] for t in tasks), denominator=len(tasks)),
    verdict_consistency=dict(status='NOT_EVALUATED', numerator=None, denominator=None),
    semantic_qualification=dict(status='NOT_EVALUATED', precision=None, recall=None, f1=None, business_approval_accuracy=None),
    status='DEV_DIAGNOSTIC', independent_review='INDEPENDENT_REVIEW_NOT_VERIFIED')
with output.open('x', encoding='utf8') as stream:
    json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
print({k: value[k] for k in ('historical_raw_claim_types', 'historical_raw_task_proposals', 'raw_claim_types',
       'parsed_f_claim_types', 'raw_task_proposals', 'raw_decision_proposals', 'discovery_rejections', 'quote_validation')})
