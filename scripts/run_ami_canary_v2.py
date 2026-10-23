"""Frozen, transcript-only F -> Discovery canary. Preparation never builds SDK."""
from dataclasses import asdict
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
from threading import Event, Lock
import uuid

from scripts.ami_canary_sources import load_sources, extract_sources, MODEL, GUARD
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.evaluation.canary_trust import AttemptJournal, VERSION, canonical, sha, save_new, summarize_attempts, classify_attempt
from linkloom.agents.model_adapter import ModelResponse, ModelProviderError
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization, TaskDiscoveryProfile
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession

ROOT = Path(__file__).resolve().parents[1]
LIMITS = TaskProviderLimits(30, 1_400_000, 122_880, 8, 3600, 90, .25,
                          input_usd_per_million=4, output_usd_per_million=20)
PROFILES = {'null': None, 'TASK_RECALL_V3': TaskDiscoveryProfile.TASK_RECALL_V3}


def code_identity():
    files = list((ROOT / 'src/linkloom').rglob('*.py'))
    files += list((ROOT / 'tests').rglob('*.py'))
    files += [ROOT / 'scripts' / name for name in
              ('ami_canary_sources.py', 'run_ami_canary_v2.py', 'analyze_canary_v2.py', 'analyze_sprint_trust_v2.py', 'prepare_canary_gate.py', 'run_canary_checks.py', 'pytest_windows_tempdir.py')]
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in sorted(files)}


def head():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()


def runtime_identity():
    return dict(python=platform.python_version(), google_genai=importlib.metadata.version('google-genai'),
                platform=platform.platform())


def prepare(inputs, output, profile):
    output.mkdir(parents=True, exist_ok=False)
    sources, sidecars = load_sources(inputs)
    requests = []

    def plan(request):
        requests.append(request.to_dict())
        raise PermissionError('offline-only preparation')

    class Offline:
        def complete(self, request):
            raise AssertionError('offline Provider invocation')

    extract_sources(sources, Offline(), plan, lambda row: None, task_profile=PROFILES[profile])
    sizes = [len(json.dumps(r, ensure_ascii=False).encode('utf8')) + 4096 for r in requests]
    reserves = [max(.25, (s * 4 + 4096 * 20) / 1e6) for s in sizes]
    if (len(requests) != 28 or sum(sizes) > LIMITS.max_input_tokens
            or sum(reserves) > 8 or len(requests) > 30):
        raise ValueError('offline budget/population mismatch')
    config = dict(model=MODEL, task_profile=profile, max_output_tokens=4096, thinking='low',
                  temperature=None, sdk_attempts=1, f_attempts=1, limits=asdict(LIMITS),
                  reservations=reserves, input_token_upper_bound=sum(sizes), compactor=False)
    source_hashes = {a.artifact_id: a.content_hash for a in sources}
    identity = dict(version=VERSION, frozen_sha=head(), code_hashes=code_identity(),
                    config_sha256=sha(canonical(config)), config=config, source_hashes=source_hashes,
                    input_files={relative: sha((inputs / relative).read_bytes())
                                 for relative in ('manifest.json', 'transcripts/ES2002a.json', 'transcripts/ES2005c.json')},
                    run_id=str(uuid.uuid4()), runtime=runtime_identity(), process_id=os.getpid())
    save_new(output / 'plan.json', identity)
    save_new(output / 'planned_requests.json', requests)
    save_new(output / 'sources.json', [dict(artifact_id=a.artifact_id, workspace_id=a.workspace_id,
             artifact_version_id=a.artifact_version_id, content_hash=a.content_hash, content=a.content,
             source_ref=a.source_ref) for a in sources])
    save_new(output / 'native_sidecars.json', sidecars)
    return sources, requests, identity


def verify_gate(gate, identity):
    from scripts.prepare_canary_gate import validate_gate
    validate_gate(gate, identity)
    committed = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', identity['frozen_sha']],
                                            cwd=ROOT, text=True).splitlines())
    if set(identity['code_hashes']) - committed:
        raise ValueError('frozen commit does not contain all runner/source files')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True).strip():
        raise ValueError('frozen source tree must be clean')


def run(inputs, output, *, profile='TASK_RECALL_V3', execute=False, gate=None):
    sources, requests, identity = prepare(inputs, output, profile)
    if not execute:
        print(f'OFFLINE: 28 requests / {profile}; reserved USD {sum(identity["config"]["reservations"]):.2f}; no Provider calls', flush=True)
        return
    if profile != 'TASK_RECALL_V3' or gate is None:
        raise ValueError('live requires v3 contract and frozen offline gate')
    verify_gate(gate, identity)
    journal = AttemptJournal(output / 'attempts', run_id=identity['run_id'], identity=identity)
    sdk = build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],
        http_options_kwargs=dict(timeout=90000, retry_options=dict(attempts=1)))
    active = {}
    closed, receipt_lock = Event(), Lock()
    safety_failures = []

    def persist(path, value):
        with receipt_lock:
            if closed.is_set():
                path = output.parent / (output.name + '-late-receipts') / path.relative_to(output)
                ticket = active['ticket']
                terminal = journal.root / f'{ticket["sequence"]:04d}.terminal.json'
                value = dict(run_id=identity['run_id'], sequence=ticket['sequence'],
                    request_sha256=ticket['request_sha256'], late=True,
                    original_terminal_sha256=sha(terminal.read_bytes()), receipt=value)
            save_new(path, value)

    class RecordingClient:
        def generate_content(self, **kwargs):
            ticket = active['ticket']
            sequence = ticket['sequence']
            with receipt_lock:
                if closed.is_set():
                    raise RuntimeError('sealed run cannot initiate transport')
                save_new(output / f'wire/{sequence:04d}.request.json', kwargs)
                active['sdk_entered'] = True
            try:
                raw = sdk.models.generate_content(**kwargs)
            except Exception as exc:
                http_response = getattr(exc, 'response', None)
                status = getattr(http_response, 'status_code', None)
                evidence = dict(sdk_entered=True, sdk_exception=type(exc).__name__)
                # SDK APIError holds an HTTP response; transport errors do not.
                from google.genai.errors import APIError
                if isinstance(exc, APIError) and type(status) is int and status >= 400:
                    evidence.update(provider_response_received=True, http_status=status,
                                    sdk_error_code=getattr(exc, 'code', None))
                persist(output / f'wire/{sequence:04d}.exception.json', evidence)
                active['evidence'] = evidence
                raise
            raw_dict = raw.model_dump(mode='json')
            raw_path = output / f'wire/{sequence:04d}.response.json'
            persist(raw_path, raw_dict)
            active['evidence'] = dict(sdk_entered=True, provider_response_received=True,
                provider_response_id=raw_dict.get('response_id'),
                provider_error_received=isinstance(raw_dict.get('error'), dict) and bool(raw_dict['error']),
                http_status=(raw_dict.get('sdk_http_response') or {}).get('status_code'),
                raw_response_canonical_sha256=sha(canonical(raw_dict)), usage=raw_dict.get('usage_metadata'))
            return raw

    adapter = GeminiProviderAdapter(RecordingClient(), model_id=MODEL)

    def validate():
        try:
            if (head() != identity['frozen_sha'] or code_identity() != identity['code_hashes']
                    or runtime_identity() != identity['runtime']):
                raise ValueError('code/runtime changed since freeze')
            for relative, expected in identity['input_files'].items():
                if sha((inputs / relative).read_bytes()) != expected:
                    raise ValueError('source changed since plan')
        except Exception:
            safety_failures.append('SOURCE_CODE_OR_RUNTIME_CHANGED')
            raise

    session = TaskProviderSession(output / 'provider', LIMITS, validate)

    class RecordingAdapter:
        def complete(self, request):
            index = len(session.attempts) - 1
            if request.to_dict() != requests[index]:
                raise ValueError('request differs from offline plan')
            ticket = active['ticket']
            try:
                response = adapter.complete(request)
                persist(output / f'model/{ticket["sequence"]:04d}.json', response.to_dict())
                evidence = active.get('evidence', dict(sdk_entered=active['sdk_entered']))
                if not active['sdk_entered']:
                    evidence['pre_transport_rejection'] = 'request_mapping_failed'
                evidence['normalized_error'] = response.error.code if response.error else None
                active['evidence'] = evidence
                persist(output / f'normalization/{ticket["sequence"]:04d}.json', dict(
                    run_id=identity['run_id'], sequence=ticket['sequence'],
                    request_sha256=ticket['request_sha256'], evidence=evidence))
                if classify_attempt(evidence) == 'OUTCOME_UNKNOWN' and not response.error:
                    response = ModelResponse(error=ModelProviderError(code='MODEL_UNKNOWN_FAILURE',
                        category='unknown_provider_error', message='Canary receipt is inconclusive.',
                        retryable=False, outcome='unknown_provider_outcome'))
                return response
            except BaseException:
                # An incomplete reservation remains UNKNOWN, including process death.
                raise

    def authorize(request):
        if session.failure or safety_failures:
            raise PermissionError('canary stopped; no further requests authorized')
        validate()
        index = journal.count
        if index >= len(requests) or request.to_dict() != requests[index]:
            raise ValueError('authorization request differs from frozen plan')
        ticket = journal.reserve(request.to_dict(), source_hashes=identity['source_hashes'],
                                 reserved_usd=identity['config']['reservations'][index])
        active.clear()
        active.update(ticket=ticket, sdk_entered=False)
        return ProviderRequestAuthorization.approve(request, guard_id=GUARD,
            reservation_id=sha(canonical(request.to_dict())), estimated_cost_upper_bound_usd=.25)

    def record_batch(row):
        save_new(output / f'discovery/{row["meeting_id"]}-{row["batch_start"]:04d}.json', row)

    class MainThreadRecorder:
        def complete(self, request):
            response = session.complete(RecordingAdapter(), 'F', request)
            with receipt_lock:
                ticket = active['ticket']
                late_cutoff = None
                if session.failure == 'PROVIDER_TIMEOUT_UNCERTAIN':
                    closed.set()
                    evidence = dict(sdk_entered=active['sdk_entered'],
                                    session_outcome=session.failure, receipt_cutoff='SESSION_RETURN')
                    if active.get('evidence'):
                        late_cutoff = active['evidence']
                else:
                    evidence = active.get('evidence', dict(sdk_entered=active['sdk_entered']))
                    if not active['sdk_entered']:
                        evidence['pre_transport_rejection'] = 'source_or_budget_guard'
                journal.finish(ticket, evidence)
                if late_cutoff:
                    save_new(output.parent / (output.name + '-late-receipts') /
                        f'{ticket["sequence"]:04d}.at-cutoff.json', dict(
                            run_id=identity['run_id'], sequence=ticket['sequence'],
                            request_sha256=ticket['request_sha256'], late=True,
                            original_terminal_sha256=sha((journal.root / f'{ticket["sequence"]:04d}.terminal.json').read_bytes()),
                            evidence=late_cutoff))
            print(f'Attempt {ticket["sequence"]}/28: {classify_attempt(evidence)}', flush=True)
            return response

    extract_sources(sources, MainThreadRecorder(), authorize, record_batch,
        stopped=lambda: bool(session.failure or safety_failures), task_profile=PROFILES[profile])
    with receipt_lock:
        closed.set()
        if active:
            ticket = active['ticket']
            terminal = journal.root / f'{ticket["sequence"]:04d}.terminal.json'
            if not terminal.exists():
                evidence = active.get('evidence', dict(sdk_entered=active['sdk_entered']))
                if not active['sdk_entered']:
                    evidence['pre_transport_rejection'] = 'source_or_budget_guard'
                journal.finish(ticket, evidence)
    save_new(output / 'completion.json', dict(session_failure=session.failure or (safety_failures[0] if safety_failures else None),
        attempts=summarize_attempts(output / 'attempts'), product_e2e='NOT_RUN',
        unattempted_requests=len(requests)-journal.count, unattempted_status='N/E',
        late_receipts_policy='separate unsealed sibling directory; never rewrite sealed UNKNOWN'))
    save_new(output / 'output_seal.json', {'files': {
        p.relative_to(output).as_posix(): sha(p.read_bytes()) for p in sorted(output.rglob('*')) if p.is_file()}})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=PROFILES, default='TASK_RECALL_V3')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--gate', type=Path)
    args = parser.parse_args()
    run(args.inputs, args.output, profile=args.profile, execute=args.execute, gate=args.gate)
