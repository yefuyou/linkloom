"""Count archived raw F proposals per phase; no cross-contract semantic score."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

archive, audit, output = map(Path, sys.argv[1:])
sha = lambda b: hashlib.sha256(b).hexdigest()
result = defaultdict(list)
with ZipFile(archive) as z:
    for attempt in json.loads(audit.read_text(encoding='utf8'))['attempts']:
        reference = attempt['member_hashes']['wire_response']
        if reference is None:
            continue
        raw = z.read(reference['member'])
        assert sha(raw) == reference['sha256']
        envelope = json.loads(raw)
        response = envelope.get('response', envelope)
        for candidate in response.get('candidates') or []:
            for part in (candidate.get('content') or {}).get('parts') or []:
                call = part.get('function_call') or {}
                if call.get('name') == 'emit_semantic_candidates':
                    result[attempt['phase']].append(dict(root=attempt['root'], sequence=attempt['sequence'],
                        model=response.get('model_version'), response_id=response.get('response_id'),
                        claims=(call.get('args') or {}).get('claims') or [], evidence=reference))
value = dict(version='historical-f-raw/1.0', source_sha256=sha(Path(__file__).read_bytes()),
    archive_sha256=sha(archive.read_bytes()), counts={}, calls=result,
    status='DEV_DIAGNOSTIC', independent_review='INDEPENDENT_REVIEW_NOT_VERIFIED',
    scoring_policy='Raw counts only; phases/models/profiles must never be combined into semantic scores')
for phase, calls in result.items():
    claims = [claim for call in calls for claim in call['claims']]
    value['counts'][phase] = dict(f_calls=len(calls), raw_claims=len(claims),
        raw_tasks=sum(bool(c.get('task')) for c in claims),
        raw_decisions=sum(c.get('claim_type') == 'DECISION' for c in claims),
        raw_types=dict(Counter(c.get('claim_type', 'MISSING') for c in claims)))
with output.open('x', encoding='utf8') as stream:
    json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
print(value['counts'])
