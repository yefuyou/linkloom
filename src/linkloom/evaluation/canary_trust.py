"""Canary evidence v2: transport evidence is independent of business verdicts."""
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path

VERSION = 'canary-trust/2.0'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as stream:
        stream.write(canonical(value).decode('utf8') + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def classify_attempt(evidence):
    """Only attributable wire/SDK receipts or proven pre-SDK rejection count.

    Generic exception names and Session classifications provide no send proof.
    Callers must retain the raw receipt/trace supporting these observations.
    """
    if evidence.get('provider_response_received') is True:
        status = evidence.get('http_status')
        if type(status) is int and status >= 400:
            return 'SENT_FAILED'
        response_id = evidence.get('provider_response_id')
        if ((type(status) is int and 200 <= status < 300 and evidence.get('http_response_observed') is True)
                or (isinstance(response_id, str) and response_id.strip())):
            return 'SENT_SUCCEEDED'
    if (evidence.get('sdk_entered') is False and evidence.get('pre_transport_rejection')
            in {'request_mapping_failed', 'sdk_argument_validation', 'source_or_budget_guard'}):
        return 'NOT_SENT'
    return 'OUTCOME_UNKNOWN'


class AttemptJournal:
    """Single process, fresh-run journal. No recovery path can resend a request."""
    def __init__(self, root, *, run_id, identity, max_requests=30, max_usd=8):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self.run_id, self.identity = run_id, identity
        self.max_requests, self.max_usd = min(max_requests, 30), min(max_usd, 8)
        self.count, self.reserved = 0, 0.0
        save_new(self.root / 'identity.json', dict(version=VERSION, run_id=run_id, identity=identity))

    def reserve(self, request, *, source_hashes, reserved_usd):
        if (isinstance(reserved_usd, bool) or not math.isfinite(reserved_usd) or reserved_usd <= 0
                or self.count >= self.max_requests or self.reserved + reserved_usd > self.max_usd + 1e-10):
            raise ValueError('canary budget exceeded')
        self.count += 1
        self.reserved += reserved_usd
        row = dict(version=VERSION, run_id=self.run_id, sequence=self.count,
                   request_sha256=sha(canonical(request)), request=request,
                   identity_sha256=sha(canonical(self.identity)), source_hashes=source_hashes,
                   reserved_usd=reserved_usd, reservation_total_usd=self.reserved,
                   max_requests=self.max_requests, max_usd=self.max_usd,
                   reserved_at=datetime.now(UTC).isoformat())
        save_new(self.root / f'{self.count:04d}.request.json', row)
        return row

    def finish(self, ticket, evidence):
        save_new(self.root / f'{ticket["sequence"]:04d}.terminal.json', dict(
            run_id=self.run_id, sequence=ticket['sequence'], request_sha256=ticket['request_sha256'],
            evidence=evidence, outcome=classify_attempt(evidence), completed_at=datetime.now(UTC).isoformat()))


def summarize_attempts(root):
    root = Path(root)
    identity = json.loads((root / 'identity.json').read_text(encoding='utf8'))
    rows = []
    for path in sorted(root.glob('*.request.json')):
        request = json.loads(path.read_text(encoding='utf8'))
        if (request['run_id'] != identity['run_id']
                or request['identity_sha256'] != sha(canonical(identity['identity']))):
            raise ValueError('run identity mismatch')
        if sha(canonical(request['request'])) != request['request_sha256']:
            raise ValueError('request identity mismatch')
        terminal = path.with_name(path.name.replace('.request.', '.terminal.'))
        evidence = {}
        if terminal.exists():
            result = json.loads(terminal.read_text(encoding='utf8'))
            if any(result[k] != request[k] for k in ('run_id', 'sequence', 'request_sha256')):
                raise ValueError('terminal identity mismatch')
            evidence = result['evidence']
        rows.append(dict(sequence=request['sequence'], request_sha256=request['request_sha256'],
                         outcome=classify_attempt(evidence), evidence=evidence, reserved_usd=request['reserved_usd']))
    counts = Counter(r['outcome'] for r in rows)
    return dict(version=VERSION, attempts=rows, outcomes=dict(counts),
                unresolved_provider_outcomes=counts['OUTCOME_UNKNOWN'],
                cost_reserved_usd=sum(r['reserved_usd'] for r in rows), actual_billed_usd=None,
                missing_usage_is_free=False, evaluation_class='DEV_DIAGNOSTIC',
                independent_review='INDEPENDENT_REVIEW_NOT_VERIFIED')
