"""Synthetic sprint probes; default is offline SDK preflight, --live is explicit.

Reuses the existing append-only Provider Ledger. Never loads Gold into a model.
All runtime material stays under ignored work/. No automatic retries or resume.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/integration'), str(ROOT)]

MODEL = 'gemini-3.8-flash'
INPUT_BOUND = 131072  # text-only: <= 65536 serialized UTF-8 bytes + generous protocol allowance
OUTPUT_BOUND = 8192  # includes thinking; no hosted tools, audio, media or caching
REQUEST_RESERVATION_USD = (INPUT_BOUND * .75 + OUTPUT_BOUND * 3.75) / 1_000_000
PUBLIC = ROOT / 'tests/fixtures/real_agent_reliability_live/public.json'
FREEZE = PUBLIC.with_name('freeze.json')
APPROVED_LIVE_DATASETS = {
    PUBLIC.resolve(): '8685ea0ebe4be65e524b92204e6bb38f29f539b62592928ad9c87d6ac467e00c',
    (PUBLIC.parent/'adversarial-inline/public.json').resolve():
        '44f21ab59e578cc280e094bdf5e160bac55daeb2bc590525247088a93daa6b68',
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


@contextmanager
def live_execution_lock(root):
    """An OS-held lease serializes all live runners; crashes release the lock.

    The persistent one-byte lock file contains no requests or credentials.
    Reservations remain exclusively in the existing Provider Ledger.
    """
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(root/'live_session.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'0')
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def serialized_live(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        if not kwargs.get('live', False):
            return operation(*args, **kwargs)
        with live_execution_lock(ROOT/'work/real-agent-reliability'):
            return operation(*args, **kwargs)
    return run


class SendBudget:
    """In-memory send admission around the existing durable ledger, not a ledger."""
    def __init__(self):
        self.started = None
        self.attempts = 0
        self.reserved_usd = 0.0
        self.busy = False

    def admit(self, payload):
        now = time.monotonic()
        if self.started is None:
            self.started = now
        if self.busy or self.attempts >= 60 or now - self.started >= 5400 - 130:
            raise RuntimeError('SPRINT_SEND_LIMIT')
        if self.reserved_usd + REQUEST_RESERVATION_USD > 10:
            raise RuntimeError('SPRINT_COST_LIMIT')
        encoded = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        if len(encoded) > 65536 or payload['model'] != MODEL:
            raise RuntimeError('SPRINT_INPUT_BOUND')
        # Only this runner's adapter-produced text/function payload is admitted.
        def check(value):
            if isinstance(value, dict):
                if any(k in value for k in ('inline_data', 'file_data', 'google_search', 'cached_content')):
                    raise RuntimeError('SPRINT_TEXT_ONLY')
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(payload)
        self.attempts += 1
        self.reserved_usd += REQUEST_RESERVATION_USD
        self.busy = True


def prior_live_budget():
    """Retain every prior attempt and its cost reservation, including UNCERTAIN."""
    budget = SendBudget()
    first = None
    for path in (ROOT/'work/real-agent-reliability').glob('live*/provider_ledger.jsonl'):
        pending = set()
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('event') == 'request_reserved':
                pending.add(row['request_id'])
                budget.attempts += 1
                sent = datetime.fromisoformat(row['utc'])
                first = min(first, sent) if first else sent
            elif row.get('event') == 'request_finished':
                pending.discard(row['request_id'])
        if pending:
            raise RuntimeError('UNCLOSED_PRIOR_REQUEST_STOP')
    budget.reserved_usd = budget.attempts*REQUEST_RESERVATION_USD
    if first:
        budget.started = time.monotonic()-(datetime.now(timezone.utc)-first).total_seconds()
    return budget


def seed(root, documents):
    from linkloom.ui.product import ProductApplication
    app = ProductApplication(database_path=root/'product.sqlite', extractor=None)
    workspace = app.repository.create_workspace('Synthetic Sprint')
    wid = workspace['workspace_id']
    app.repository.select_workspace(wid)
    for index, content in enumerate(documents):
        sid = 'src_' + uuid4().hex
        content = '[2026-10-15T00:00:00Z] Synthetic: ' + content
        artifact = app._artifact(wid, sid, content)
        app.repository.create_source(wid, sid, f'synthetic-{index}.md', artifact, app._parse_artifact(artifact))
    return app, wid


def bounded_sdk_client(sdk, ledger, budget):
    from tests.smoke.provider_ledger import LedgeredGeminiClient
    class BoundedClient(LedgeredGeminiClient):
        def generate_content(self, **payload):
            payload = {**payload, 'config': {**payload['config'], 'max_output_tokens': OUTPUT_BOUND}}
            budget.admit(payload)
            try:
                return super().generate_content(**payload)
            finally:
                budget.busy = False
    return BoundedClient(sdk, ledger)


def run_product(case, root, sdk, ledger, budget, *, offline=False):
    import httpx
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.runtime.team_decision_planner import plan_relation_investigation
    from run_bounded_multihop import SourceOnlyController
    root.mkdir(parents=True, exist_ok=False)
    app, workspace = seed(root, case['documents'])
    requests, responses = [], []
    controller = SourceOnlyController()

    def fake_wire(request):
        # Exercise the actual installed SDK serializer/parser without a socket.
        body = json.loads(request.content)
        assert 'parametersJsonSchema' in request.content.decode()
        current = requests[-1]
        observation = current.observation
        if observation is None and current.tool_turns:
            observation = current.tool_turns[-1].tool_results[-1].result
        action = controller(SimpleNamespace(user_input=current.user_input,
            sequence=current.sequence, observation=observation))
        part = ({'functionCall': {'name': action.tool_call.tool_id, 'args': action.tool_call.arguments},
                 'thoughtSignature': 'c3ByaW50'} if action.kind == 'tool_call' else {'text': action.final_answer})
        return httpx.Response(200, json={'modelVersion': MODEL, 'responseId': uuid4().hex,
            'candidates': [{'content': {'role': 'model', 'parts': [part]}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 20,
                              'thoughtsTokenCount': 10, 'totalTokenCount': 130}})

    if offline:
        from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
        from google.genai import types
        sdk = build_gemini_sdk_client('synthetic-unused-key', http_options_kwargs={
            'timeout': 130000, 'retry_options': types.HttpRetryOptions(attempts=1),
            'client_args': {'transport': httpx.MockTransport(fake_wire)}})
    client = bounded_sdk_client(sdk, ledger, budget)

    class ObservedAdapter(GeminiProviderAdapter):
        def complete(self, request):
            requests.append(request)
            response = super().complete(request)
            responses.append(response)
            return response

    adapter = ObservedAdapter(client, model_id=MODEL)
    service = ProductInvestigationService(app, model_factory=lambda _: adapter,
        run_mode='OFFLINE_SDK' if offline else 'REAL_GEMINI_SPRINT', external_provider=not offline)
    started = time.monotonic()
    initial_requests = ledger.request_count
    first_useful = None
    try:
        snapshot = service.start(case['query'], workspace, task_plan=plan_relation_investigation(case['query']))
        preparation_ms = (time.monotonic()-started)*1000
        rid = snapshot['run']['id']
        while True:
            snapshot = service.inspect(rid)
            if snapshot.get('partial_findings') and first_useful is None:
                first_useful = (time.monotonic()-started)*1000
            if snapshot['kind'] != 'running':
                break
            if time.monotonic()-started > 1700:
                raise RuntimeError('TASK_DEADLINE_STOP')
            time.sleep(.05)
        job = service._jobs[rid]
        state = job.backend.engine.checkpointer.get_latest(snapshot['run']['thread_id'])
        rows = [record.to_dict() for record in state.tool_ledger]
        report = {'case_id': case['id'], 'mode': 'OFFLINE_SDK' if offline else 'REAL_GEMINI',
            'kind': snapshot['kind'], 'error': snapshot.get('error'), 'reviewer': snapshot.get('reviewer'),
            'source_investigation': snapshot.get('source_investigation'), 'metrics': snapshot['metrics'],
            'plan_origin': 'deterministic_query_compiler', 'model_tool_selection': not offline,
            'elapsed_ms': (time.monotonic()-started)*1000, 'preparation_ms': preparation_ms,
            'time_to_first_useful_ms': first_useful,
            'provider_requests': ledger.request_count-initial_requests,
            'usage': client.generation_usage_records,
            'tool_calls': rows, 'provider_halted': ledger.halted,
            'request_context_bytes': [len(json.dumps(r.to_dict(), ensure_ascii=False).encode()) for r in requests]}
        write(root/'result.json', report)
        # Full request/response artifacts are local only, in ignored work/.
        write(root/'requests.json', [r.to_dict() for r in requests])
        write(root/'responses.json', [r.to_dict() for r in responses])
        return report
    finally:
        app.close()
        if offline:
            sdk.close()


@serialized_live
def execute(*, live=False, attempt=None, case_ids=None, dataset=None):
    from tests.smoke.provider_ledger import AppendOnlyProviderLedger
    from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
    from google.genai import types
    public = Path(dataset) if dataset else PUBLIC
    public_sha = hashlib.sha256(public.read_bytes()).hexdigest()
    if live and APPROVED_LIVE_DATASETS.get(public.resolve()) != public_sha:
        raise ValueError('UNAPPROVED_LIVE_DATASET')
    freeze = json.loads(public.with_name('freeze.json').read_text(encoding='utf-8'))
    if public_sha != freeze['public_sha256']:
        raise RuntimeError('FROZEN_PUBLIC_CHANGED')
    cases = json.loads(public.read_text(encoding='utf-8'))['cases']
    if case_ids:
        selected = set(case_ids.split(','))
        if not selected <= {case['id'] for case in cases}:
            raise ValueError('UNKNOWN_CASE')
        cases = [case for case in cases if case['id'] in selected]
    if attempt is not None and (not live or re.fullmatch(r'[a-z0-9-]{1,40}', attempt) is None):
        raise ValueError('INVALID_ATTEMPT_ID')
    budget = prior_live_budget() if live else SendBudget()
    initial_attempts = budget.attempts
    out = ROOT/'work/real-agent-reliability'/(
        'live-'+attempt if attempt else 'live' if live else 'sdk-preflight-'+uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=False)  # no resume, overwrite or replay
    sdk = None
    if live:
        key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
        if not key:
            raise RuntimeError('CREDENTIAL_UNAVAILABLE')
        sdk = build_gemini_sdk_client(key, http_options_kwargs={
            'timeout': 130000, 'retry_options': types.HttpRetryOptions(attempts=1)})
    identity = 'RAR-' + uuid4().hex
    ledger = AppendOnlyProviderLedger(out/'provider_ledger.jsonl', batch_id=identity,
        run_id=identity, case_id='SYNTHETIC_SPRINT', max_requests=60,
        max_runtime_runs=1, max_input_tokens_per_request=INPUT_BOUND)
    ledger.runtime_started()  # one exclusive serial experiment, including its cases
    rows = []
    write(out/'preflight.json', {'model': MODEL, 'sdk': 'google-genai==2.22.0',
        'max_requests': 60, 'max_usd': 10, 'max_seconds': 5400, 'sdk_retries': 0,
        'concurrency': 1, 'request_timeout_ms': 130000, 'max_output_tokens': OUTPUT_BOUND,
        'input_token_conservative_bound': INPUT_BOUND,
        'prior_generation_attempts': initial_attempts,
        'reservation_per_request_usd': REQUEST_RESERVATION_USD, 'gold_sent': False,
        'public_sha256': freeze['public_sha256'], 'real_vault': False})
    try:
        for case in cases:
            row = run_product(case, out/case['id'], sdk, ledger, budget, offline=not live)
            rows.append(row)
            print(json.dumps({k: row[k] for k in ('case_id','kind','metrics','elapsed_ms','provider_requests')}, ensure_ascii=False), flush=True)
            if ledger.halted or (case['id'] == 'LIVE-2H' and row['kind'] != 'success'):
                break
        ledger.finish_run(status='STOPPED' if ledger.halted else 'COMPLETE', usage_summary={})
    finally:
        write(out/'summary.json', {'real_provider': live, 'generation_attempts': budget.attempts,
            'this_experiment_attempts': budget.attempts-initial_attempts,
            'conservative_reserved_usd': budget.reserved_usd, 'ledger_halted': ledger.halted,
            'cases': rows})
        ledger.close()
        if sdk:
            sdk.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--attempt', help='New identity for an explicitly authorized follow-up; prior budget is retained.')
    parser.add_argument('--cases', help='Comma-separated frozen case IDs; never retries a case automatically.')
    parser.add_argument('--dataset', type=Path, help='Explicit frozen synthetic public dataset with adjacent freeze.json.')
    options = parser.parse_args()
    os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
    try:
        execute(live=options.live, attempt=options.attempt, case_ids=options.cases, dataset=options.dataset)
    except Exception as error:
        print(json.dumps({'state': 'STOPPED', 'error_class': type(error).__name__}))
        raise SystemExit(2)  # no SDK exception text or credential-bearing traceback
