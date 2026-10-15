"""Small adapter over the prior probe and durable Ledger; no Gold is loaded."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'src'), str(ROOT/'tests/integration')]
from scripts import reliability_probe as probe

OUT = ROOT/'work/agent-recovery'
INPUTS = ROOT/'tests/fixtures/agent_recovery'
APPROVED = {
    'public_canary.json': 'a5fcddbc63891841d828b167312166368602aca662ffb8b43c935ba2c44ecc57',
    'public_dev.json': '331b7188357b4f7d065e62733d51b65417e1f9668c95b42c507be77f67ec0381',
    'public_frozen.json': '9669b84f29c26bc812013d7b878db531194b77227c6f23ee5ba781b20d8df594',
}


def accounting():
    attempts, spent, first, strict, blocked = 0, 0., None, 0, set()
    for path in OUT.glob('live-*/*/provider_ledger.jsonl'):
        pending = set()
        unknown = False
        for line in path.read_text(encoding='utf8').splitlines():
            row = json.loads(line)
            if row['event'] == 'request_reserved':
                attempts += 1
                pending.add(row['request_id'])
                stamp = datetime.fromisoformat(row['utc'])
                first = min(first, stamp) if first else stamp
            elif row['event'] == 'request_finished':
                pending.discard(row['request_id'])
                unknown |= row.get('status') == 'UNCERTAIN'
                usage = row.get('usage') or {}
                cost = usage.get('estimated_cost_usd')
                spent += cost if isinstance(cost, (int, float)) else probe.REQUEST_RESERVATION_USD
        # A live owner may have its own in-flight reservation. Accounting is
        # called only before admission under the OS lease, never during a send.
        if pending:
            raise RuntimeError('UNCLOSED_REQUEST_STOP')
        if unknown:
            blocked.add(path.parent.name)
            if not path.with_name('outcome.json').exists():
                strict += 1  # crash before diagnosis is never a known failure
    for path in OUT.glob('live-*/*/outcome.json'):
        row = json.loads(path.read_text(encoding='utf8'))
        if row['classification'] == 'provider_outcome_uncertain':
            strict += 1
            blocked.add(row['case_id'])
    return attempts, spent, first, strict, blocked


class Budget(probe.SendBudget):
    def __init__(self, *, live, canary=False):
        # 96 KiB serialized text is bounded by 131072 input tokens even at
        # one token per byte, with 32 KiB additional protocol allowance.
        super().__init__(max_requests=120, max_usd=8, max_seconds=10800, input_byte_bound=98304)
        self.live = live
        self.canary = canary
        self.local_attempts = 0

    def admit(self, payload):
        prior_canary = 0
        if self.live and self.canary:
            for path in OUT.glob('live-*/preflight.json'):
                if json.loads(path.read_text(encoding='utf8')).get('canary_cap') == 20:
                    prior_canary += sum(json.loads(line).get('event') == 'request_reserved'
                        for ledger in path.parent.glob('*/provider_ledger.jsonl')
                        for line in ledger.read_text(encoding='utf8').splitlines())
        if self.canary and (prior_canary if self.live else self.local_attempts) >= 20:
            raise RuntimeError('CANARY_REQUEST_LIMIT')
        if self.live:
            attempts, spent, first, strict, _ = accounting()
            if strict >= 2:
                raise RuntimeError('TWO_STRICT_UNCERTAIN_STOP')
            self.attempts, self.reserved_usd = attempts, spent
            if first:
                self.started = time.monotonic() - (datetime.now(timezone.utc)-first).total_seconds()
        try:
            super().admit(payload)
        except RuntimeError as error:
            self.last_rejection = str(error)
            raise
        self.local_attempts += 1


def classify_exception(error):
    """Use Runtime's classifier, separately from Ledger's cautious reservation."""
    from linkloom.agents.providers.gemini_api import _exception_code
    from linkloom.agents.providers.retry_policy import classify_retryable_failure
    code, outcome = _exception_code(error)
    transport = classify_retryable_failure(error)
    if transport in {'TRANSPORT_CONNECT', 'TRANSPORT_DNS', 'TRANSPORT_PROXY_CONNECT'}:
        outcome = 'known_failure'  # connection establishment failed; never replayed here
    return {'classification': 'provider_outcome_uncertain' if outcome == 'unknown_provider_outcome'
            else 'provider_known_failure', 'error_class': type(error).__name__,
            'provider_code': code, 'transport_classification': transport}


class ObservedSDK:
    def __init__(self, sdk):
        self.sdk, self.failure = sdk, None
        self.models = self

    def __getattr__(self, name):
        return getattr(self.sdk, name)

    def generate_content(self, **kwargs):
        try:
            return self.sdk.models.generate_content(**kwargs)
        except Exception as error:
            self.failure = classify_exception(error)
            raise


def seed_documents(root, documents):
    """Import public synthetic notes; apply the public update through repository."""
    from linkloom.ui.product import ProductApplication
    app = ProductApplication(database_path=root/'product.sqlite', extractor=None)
    wid = app.repository.create_workspace('Synthetic Agent Recovery')['workspace_id']
    app.repository.select_workspace(wid)
    for document in documents:
        sid = 'src_' + uuid4().hex
        current = document['content']
        prior = document.get('_previous_content')
        def timestamped(text):
            return '\n'.join('[2026-10-15T00:00:00Z] Synthetic: '+line
                             for line in text.splitlines() if line.strip())
        text = timestamped(prior or current)
        artifact = app._artifact(wid, sid, text)
        app.repository.create_source(wid, sid, document['path'], artifact, app._parse_artifact(artifact))
        app.repository.set_ingestion_result(wid, sid, artifact.artifact_version_id,
            status='COMPLETE', candidate_count=0, review_required_count=0)
        if prior is not None:
            updated = app._artifact(wid, sid, timestamped(current))
            app.repository.update_source(wid, sid, document['path'], updated, app._parse_artifact(updated))
    return app, wid


def execute(args):
    if args.version == 'baseline':
        baseline = OUT/'baseline-code/src'
        if not baseline.is_dir():
            raise RuntimeError('BASELINE_ARCHIVE_MISSING')
        if any(name == 'linkloom' or name.startswith('linkloom.') for name in sys.modules):
            raise RuntimeError('BASELINE_REQUIRES_FRESH_PROCESS')
        import subprocess, tarfile, io
        archive = subprocess.check_output(['git','archive',
            '49736a7098b23c8b9f021f98f1fe6d3e7c70e755','src'], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as frozen:
            for member in frozen.getmembers():
                if member.isfile() and (OUT/'baseline-code'/member.name).read_bytes() != frozen.extractfile(member).read():
                    raise RuntimeError('BASELINE_ARCHIVE_MISMATCH')
        sys.path.insert(0, str(baseline))
    public = INPUTS/args.dataset
    digest = hashlib.sha256(public.read_bytes()).hexdigest()
    if APPROVED.get(public.name) != digest:
        raise RuntimeError('UNAPPROVED_SYNTHETIC_INPUT')
    cases = json.loads(public.read_text(encoding='utf8'))['cases']
    if args.canary and (public.name != 'public_canary.json' or len(cases) != 2):
        raise RuntimeError('CANARY_REQUIRES_FROZEN_MISSING_AND_SAMEFILE_PAIR')
    if args.cases:
        selected = set(args.cases.split(','))
        if not selected <= {c['id'] for c in cases}:
            raise RuntimeError('UNKNOWN_CASE')
        cases = [c for c in cases if c['id'] in selected]
    if not args.attempt or not all(c.isalnum() or c == '-' for c in args.attempt):
        raise RuntimeError('INVALID_ATTEMPT')
    from tests.smoke.test_p14_cn2_typed_gemini38_smoke import AppendOnlyProviderLedger
    from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
    from google.genai import types
    # Canonical one-level output allows every prior Ledger to be accounted.
    out = OUT/(('live-' if args.live else 'offline-')+args.attempt)
    out.mkdir(parents=True, exist_ok=False)
    budget = Budget(live=args.live, canary=args.canary)
    _, _, _, strict, blocked = accounting() if args.live else (0,0,None,0,set())
    if strict >= 2 or any(c['id'] in blocked for c in cases):
        raise RuntimeError('UNCERTAIN_CASE_OR_BATCH_STOP')
    for case in cases:
        prior_trials = sum(1 for p in OUT.glob('live-*/*/provider_ledger.jsonl')
                          if p.parent.name == case['id'] and 'request_reserved' in p.read_text(encoding='utf8'))
        if prior_trials >= 3:
            raise RuntimeError('CASE_TRIAL_LIMIT')
    sdk = None
    if args.live:
        key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if not key:
            raise RuntimeError('CREDENTIAL_UNAVAILABLE')
        sdk = ObservedSDK(build_gemini_sdk_client(key, http_options_kwargs={
            'timeout': 130000, 'retry_options': types.HttpRetryOptions(attempts=1)}))
    probe.seed = seed_documents
    probe.write(out/'preflight.json', {'experiment': args.attempt, 'version': args.version,
        'model':probe.MODEL, 'public_sha256':digest, 'gold_sent':False, 'real_vault':False,
        'max_requests':120, 'max_usd':8, 'max_seconds':10800, 'concurrency':1, 'sdk_retries':0,
        'request_timeout_ms':130000, 'max_steps':12, 'max_output_tokens':probe.OUTPUT_BOUND,
        'serialized_input_byte_bound':98304, 'conservative_input_token_bound':probe.INPUT_BOUND,
        'reservation_per_request_usd':probe.REQUEST_RESERVATION_USD, 'canary_cap':20 if args.canary else None})
    rows = []
    try:
        for case in cases:
            identity = 'AR-'+uuid4().hex
            ledger = AppendOnlyProviderLedger(out/case['id']/'provider_ledger.jsonl', batch_id=identity,
                run_id=identity, case_id=case['id'], max_requests=12,
                max_runtime_runs=1, max_input_tokens_per_request=probe.INPUT_BOUND)
            # Ledger path creation must precede runtime root's exclusive mkdir.
            ledger.runtime_started()
            try:
                run_case = dict(case, documents=[dict(d) for d in case['documents']])
                for update in case.get('source_updates', []):
                    doc = next(d for d in run_case['documents'] if d['path'] == update['path'])
                    doc['_previous_content'] = doc['content'].replace(update['current_content'], update['previous_content'])
                if sdk:
                    sdk.failure = None
                row = probe.run_product(run_case, out/case['id']/'runtime', sdk, ledger, budget,
                    offline=not args.live)
                failure = sdk.failure if sdk else None
                classification = (failure or {}).get('classification') or (
                    (row.get('error') or {}).get('classification') if row['kind'] == 'error' else row['kind'])
                if getattr(budget, 'last_rejection', None):
                    classification = ('budget_exhausted' if budget.last_rejection in {
                        'SPRINT_COST_LIMIT', 'SPRINT_SEND_LIMIT'} else 'input_admission_failure')
                outcome = {'case_id':case['id'], 'classification':classification,
                    'transport_failure':failure, 'ledger_halted':ledger.halted,
                    'provider_requests':row['provider_requests'], 'elapsed_ms':row['elapsed_ms']}
                probe.write(out/case['id']/'outcome.json', outcome)
                rows.append(outcome)
                ledger.finish_run(status='STOPPED' if ledger.halted else 'COMPLETE', usage_summary={})
                print(json.dumps(outcome,ensure_ascii=False),flush=True)
            finally:
                ledger.close()
            if (getattr(budget, 'last_rejection', None) or (args.canary and row['kind'] == 'error')
                    or (args.live and accounting()[3] >= 2)):
                break
    finally:
        probe.write(out/'summary.json', {'version':args.version, 'cases':rows,
            'accounting':list(accounting()[:2]) if args.live else None})
        if sdk:
            sdk.sdk.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--version', choices=['baseline','after'], default='after')
    parser.add_argument('--dataset', choices=list(APPROVED), default='public_frozen.json')
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--cases')
    parser.add_argument('--canary', action='store_true')
    options = parser.parse_args()
    os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
    try:
        with probe.live_execution_lock(OUT) if options.live else __import__('contextlib').nullcontext():
            execute(options)
    except Exception as error:
        print(json.dumps({'state':'STOPPED', 'error_class':type(error).__name__}),flush=True)
        raise SystemExit(2)
