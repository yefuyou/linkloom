"""Freeze comparable F OFF/ON requests offline. No live runner or SDK client.

Only transcript-side members of the verified Canary archive feed request
construction. Case labels are evaluator-side and are never loaded here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    MEETING_DISCOURSE_PROMPT_VERSION, ProviderNeutralSemanticExtractor,
    SemanticExtractionContext, TaskDiscoveryContext, TaskDiscoveryProfile,
    _MEETING_DISCOURSE_GUIDANCE,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver


BASE_HEAD = '1bfd910bb3a53a902a708ca2d00a39f4dde68091'
CANDIDATE_SHA256 = '824adc88c85fd9b5005d7d27595b2c507f5c6e1643636b27abe6759025be296a'
CANARY_RESULTS = '3e7890866a7e17ce79fcb84f6defd95b1c07b6a5'
CANARY_CODE = '1ade9b2b359b0249ed21678ad09e6aa4d5e82c83'
GUARD = 'ami-explicit-two-meeting-canary/v1'
EXTRACTION = 'src/linkloom/semantic_ingestion/extraction.py'
# Input ordinal and existing profile only: no target labels enter construction.
PAIRS = (
    ('task-a06', 6, 'TASK_RECALL_V3'),
    ('task-c06', 14, 'TASK_RECALL_V3'),
    ('task-c17', 25, 'TASK_RECALL_V3'),
    ('general-a06', 6, 'null'),
    ('general-c05', 13, 'null'),
    ('general-c10', 18, 'null'),
    ('general-c11', 19, 'null'),
    ('general-c14', 22, 'null'),
    ('general-c15', 23, 'null'),
    ('general-c16', 24, 'null'),
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def payload(request):
    return json.loads(request['user_input'].split('Input:\n', 1)[1])


def capture_request(source, profile, arm, config):
    artifact = RawArtifact(source['workspace_id'], source['artifact_id'], 'manual_text',
        source['content'], ingestion_time=datetime(2026, 10, 23, tzinfo=UTC))
    if artifact.content_hash != source['content_hash'] or artifact.artifact_version_id != source['artifact_version_id']:
        raise ValueError('source identity changed')
    calls = []

    def offline_guard(request):
        calls.append(request.to_dict())
        raise PermissionError('OFFLINE_REQUEST_PREPARATION_NOT_SENT')

    class NeverProvider:
        def complete(self, request):
            raise AssertionError('offline preparation must stop before Provider')

    options = dict(provider_id='gemini', model_id=config['model'], request_guard=offline_guard,
        request_guard_id=GUARD, max_attempts=1, max_output_tokens=config['max_output_tokens'],
        temperature=config['temperature'], thinking_level=config['thinking'])
    if arm == 'ON':
        options['prompt_version'] = MEETING_DISCOURSE_PROMPT_VERSION
    extractor = ProviderNeutralSemanticExtractor(NeverProvider(), **options)
    task = None if profile == 'null' else TaskDiscoveryContext(artifact, TaskDiscoveryProfile.TASK_RECALL_V3)
    context = SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(()), task)
    result = extractor.extract(parse_source_text(artifact).segments[0], context)
    if len(calls) != 1 or result.receipt.attempts[0].outcome != 'preflight_blocked':
        raise ValueError('offline request capture did not stop at guard')
    return calls[0], result.receipt


def compare_pair(off, on, source):
    # IDs are derived from prompt/config fingerprints; no other request field
    # may vary. Profile/schema/source/generation are paired exactly.
    differences = {k for k in off if off[k] != on[k]}
    if differences != {'run_id', 'turn_id', 'task_id', 'user_input'}:
        raise ValueError('unexpected OFF/ON request differences: ' + str(differences))
    left, right = payload(off), payload(on)
    if left.get('prompt_version') == 'semantic-extraction/v1':
        if right.get('prompt_version') != MEETING_DISCOURSE_PROMPT_VERSION:
            raise ValueError('general opt-in version missing')
        right['prompt_version'] = left['prompt_version']
        expected = _MEETING_DISCOURSE_GUIDANCE + off['user_input'].replace(
            '"prompt_version":"semantic-extraction/v1"',
            '"prompt_version":' + json.dumps(MEETING_DISCOURSE_PROMPT_VERSION), 1)
    else:
        expected = _MEETING_DISCOURSE_GUIDANCE + off['user_input']
    if left != right or on['user_input'] != expected:
        raise ValueError('candidate changed payload beyond its declared prompt delta')
    if left['source_segment']['text'] != source['content']:
        raise ValueError('source changed or answer text injected')
    return sorted(differences)


def reservation(request, limits):
    # Match the existing Session's conservative framing estimate. No token API.
    size = len(json.dumps(request, ensure_ascii=False).encode('utf-8')) + 4096
    out = request['generation_options']['max_output_tokens']
    cost = max(limits['request_cost_upper_bound_usd'],
        (size * limits['input_usd_per_million'] + out * limits['output_usd_per_million']) / 1e6)
    return dict(input_upper_bound=size, output_upper_bound=out, reserved_usd=cost,
                estimation='existing TaskProviderSession byte bound; not measured tokens')


def budget_snapshot(budget):
    limits = budget['limits']
    attempts = budget['attempts']
    if len(attempts) != budget['requests_reserved'] or any(a['outcome'] != 'SUCCEEDED' for a in attempts):
        raise ValueError('parent session incomplete or uncertain')
    if sum(a['input_reserved'] for a in attempts) != budget['input_tokens_reserved']:
        raise ValueError('input reservations differ')
    if sum(a['output_reserved'] for a in attempts) != budget['output_tokens_reserved']:
        raise ValueError('output reservations differ')
    if abs(sum(a['cost_reserved_usd'] for a in attempts) - budget['cost_reserved_usd']) > 1e-10:
        raise ValueError('cost reservations differ')
    return dict(limits=limits, requests_reserved=budget['requests_reserved'],
        input_reserved=budget['input_tokens_reserved'], output_reserved=budget['output_tokens_reserved'],
        usd_reserved=budget['cost_reserved_usd'], remaining_arithmetic_only=dict(
            requests=limits['max_requests'] - budget['requests_reserved'],
            input_tokens=limits['max_input_tokens'] - budget['input_tokens_reserved'],
            output_tokens=limits['max_output_tokens'] - budget['output_tokens_reserved'],
            usd=limits['max_cost_usd'] - budget['cost_reserved_usd']),
        allocated_to_this_ab=False, live_shared_ledger_endpoint=None,
        original_session_sealed=True, can_resume_original_runner=False)


def prepare(repo, reference, output):
    if git(repo, 'rev-parse', 'HEAD').decode().strip() != BASE_HEAD:
        raise ValueError('candidate base changed')
    candidate = (repo / EXTRACTION).read_bytes()
    if sha(candidate) != CANDIDATE_SHA256:
        raise ValueError('the frozen 38-line candidate changed')
    base = git(repo, 'show', BASE_HEAD + ':' + EXTRACTION)
    canary_base = git(repo, 'show', CANARY_CODE + ':' + EXTRACTION)
    if base != canary_base:
        raise ValueError('Canary extraction code no longer matches candidate baseline')
    numstat = git(repo, 'diff', BASE_HEAD, '--numstat', '--', EXTRACTION).decode().strip()
    if numstat != '38\t0\t' + EXTRACTION:
        raise ValueError('production delta is not the preserved 38-line candidate')
    if inspect.signature(ProviderNeutralSemanticExtractor).parameters['prompt_version'].default != 'semantic-extraction/v1':
        raise ValueError('candidate is no longer disabled by default')

    manifest = json.loads((reference / 'EVIDENCE_MANIFEST.json').read_bytes())
    archive_bytes = (reference / 'evidence.zip').read_bytes()
    if sha(archive_bytes) != manifest['archive']['sha256']:
        raise ValueError('Canary archive changed')
    archive = ZipFile(reference / 'evidence.zip')
    if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(manifest['payload_files']):
        raise ValueError('Canary archive member population changed')
    for name, digest in manifest['payload_files'].items():
        if sha(archive.read(name)) != digest:
            raise ValueError('Canary member changed: ' + name)

    def read(name):
        return json.loads(archive.read(name))

    for name, digest in read('live/output_seal.json')['files'].items():
        if sha(archive.read('live/' + name)) != digest:
            raise ValueError('Canary output seal changed: ' + name)
    plan, completion = read('live/plan.json'), read('live/completion.json')
    config = plan['config']
    if plan['frozen_sha'] != CANARY_CODE or config['model'] != 'gemini-3.8-flash':
        raise ValueError('Canary code/model not the pinned completed experiment')
    if config['task_profile'] != 'TASK_RECALL_V3' or config['max_output_tokens'] != 4096:
        raise ValueError('Canary contract changed')
    if config['sdk_attempts'] != 1 or config['f_attempts'] != 1 or config['compactor']:
        raise ValueError('Canary call settings changed')
    terminals = [read(f'live/attempts/{seq:04d}.terminal.json') for seq in range(1, 29)]
    if any(t['outcome'] != 'SENT_SUCCEEDED' for t in terminals) or completion['unattempted_requests']:
        raise ValueError('original Canary is not complete without uncertainty')
    baseline = read('live/planned_requests.json')
    general_plan = read('offline-null-plan/planned_requests.json')
    sources = read('live/sources.json')
    if len(sources) != len(baseline) or len(sources) != 28 or len(general_plan) != 28:
        raise ValueError('incomplete transcript/request population')

    # Full current default reconstruction, not old responses relabelled as v3.
    for index, source in enumerate(sources):
        request, _ = capture_request(source, 'TASK_RECALL_V3', 'OFF', config)
        if request != baseline[index]:
            raise ValueError('default request changed at source ' + str(index))
        recorded = read(f'live/attempts/{index + 1:04d}.request.json')
        if recorded['request'] != request or recorded['request_sha256'] != sha(canonical(request)):
            raise ValueError('baseline plan differs from sent request')

    budget = budget_snapshot(read('live/provider/budget.json'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'candidate.diff').write_bytes(git(repo, 'diff', BASE_HEAD, '--', EXTRACTION))
    (output / 'sources.json').write_bytes(archive.read('live/sources.json'))
    pairs = []
    for pair_id, index, profile in PAIRS:
        source = sources[index]
        off, off_receipt = capture_request(source, profile, 'OFF', config)
        on, on_receipt = capture_request(source, profile, 'ON', config)
        expected = baseline[index] if profile == 'TASK_RECALL_V3' else general_plan[index]
        if off != expected:
            raise ValueError('OFF request differs from frozen original plan')
        differences = compare_pair(off, on, source)
        arms = {}
        for arm, request, receipt in [('OFF', off, off_receipt), ('ON', on, on_receipt)]:
            path = f'requests/{pair_id}/{arm}.json'
            save(output / path, request)
            arms[arm] = dict(path=path, request_sha256=sha(canonical(request)),
                prompt_sha256=receipt.prompt_sha256, schema_sha256=receipt.schema_sha256,
                config_sha256=receipt.config_sha256, reservation=reservation(request, budget['limits']),
                new_provider_status='NOT_SENT_OFFLINE_PREPARATION')
        pairs.append(dict(pair_id=pair_id, profile=profile, source_index=index,
            artifact_id=source['artifact_id'], content_hash=source['content_hash'],
            source_verbatim=True, request_fields_differ=differences, arms=arms,
            historical_off_receipt=(f'live/attempts/{index + 1:04d}.terminal.json'
                if profile == 'TASK_RECALL_V3' else None),
            historical_off_is_new_paired_trial=False))

    protected = [
        'src/linkloom/semantic_ingestion/task_observation_discovery.py',
        'src/linkloom/semantic_ingestion/text_scopes.py',
        'src/linkloom/semantic_ingestion/task_discovery_veto.py',
        'src/linkloom/semantic_ingestion/task_field_binding.py',
        'src/linkloom/semantic_ingestion/validation.py',
        'src/linkloom/agents/claim_grounding.py',
        'src/linkloom/agents/reviewer_agent.py',
        'src/linkloom/ui/task_provider.py',
    ]
    local = {}
    for name in protected:
        content = (repo / name).read_bytes()
        if content != git(repo, 'show', BASE_HEAD + ':' + name):
            raise ValueError('protected production file changed: ' + name)
        local[name] = sha(content)
    save(output / 'code-freeze.json', dict(base_head=BASE_HEAD, candidate_sha256=sha(candidate),
        baseline_extraction_sha256=sha(base), candidate_default='OFF',
        opt_in_prompt_version=MEETING_DISCOURSE_PROMPT_VERSION,
        default_prompt_version='semantic-extraction/v1', production_diff=numstat,
        candidate_diff_sha256=sha((output / 'candidate.diff').read_bytes()),
        protected_local_source_hashes=local,
        required_discovery_runtime=CANARY_CODE,
        required_runtime_source_hashes={name: manifest['payload_files']['frozen-source/' + name]
            for name in protected},
        local_runtime_equals_canary=False,
        runtime_note='Requests match; local quote scopes are older. No local Discovery outcome is a paired live result.',
        protected_canary_runner_sha256=manifest['payload_files']['frozen-source/scripts/run_ami_canary_v2.py']))
    all_reservations = [arm['reservation'] for p in pairs for arm in p['arms'].values()]
    planned = dict(requests=len(all_reservations),
        input_tokens_upper_bound=sum(r['input_upper_bound'] for r in all_reservations),
        output_tokens_upper_bound=sum(r['output_upper_bound'] for r in all_reservations),
        usd_upper_bound=sum(r['reserved_usd'] for r in all_reservations))
    status = dict(status='PREPARED_BLOCKED_SHARED_BUDGET_CONTINUATION',
        evaluation_class='DEV_DIAGNOSTIC', new_provider_calls=0,
        parent_canary=dict(results_commit=CANARY_RESULTS, frozen_code=CANARY_CODE,
            run_id=plan['run_id'], archive_sha256=sha(archive_bytes), verified_members=len(archive.namelist()),
            completed_sent_succeeded=28, outcome_unknown=0, exact_default_requests=28),
        budget=budget, planned_full_ab=planned,
        full_plan_fits_remaining_budget=False, max_complete_pairs_by_request_and_output_slots=1,
        allocation_selected=False, real_execution_enabled=False,
        blockers=['Parent budget is sealed evidence, not a live shared reservation endpoint',
                  '20 fresh requests exceed the inherited 2 request / 8192 output-token arithmetic balance',
                  'General decision OFF plans are not completed v3 model responses; decision live path requires separate qualification',
                  'Execution must preserve frozen Canary Discovery/quote/Reviewer runtime; local older runtime is not interchangeable'],
        no_new_cost_pool=True, raw_and_semantic_results='NOT_EVALUATED', pairs=pairs)
    save(output / 'PLAN.json', status)
    # Results are empty, not zero scores. All comparison fields await real data.
    save(output / 'RESULTS_TEMPLATE.json', dict(status='NOT_RUN',
        task_discovery=dict(OFF=None, ON=None), decision_discovery=dict(OFF=None, ON=None),
        task_exact_quotes=dict(OFF=None, ON=None), discovery_admission=dict(OFF=None, ON=None),
        decision_exact_quotes='N/E: general claim schema has no per-claim exact quote field; manual source verification is separate',
        source_verified_real_work_added=None, new_obvious_false_positives=None,
        semantic_precision_recall_f1='NOT_EVALUATED_DEV_DIAGNOSTIC',
        production_pr_allowed=False))
    save(output / 'MANIFEST.json', dict(files={p.relative_to(output).as_posix(): sha(p.read_bytes())
        for p in sorted(output.rglob('*')) if p.is_file()},
        generator_sha256=sha(Path(__file__).read_bytes()), new_provider_calls=0))
    print(json.dumps(dict(status=status['status'], pairs=len(pairs), requests=planned['requests'],
        required_reserved_usd=planned['usd_upper_bound'], theoretical_remaining=budget['remaining_arithmetic_only'],
        new_provider_calls=0)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--reference', type=Path, required=True,
                        help='Verified original Canary evidence.zip and EVIDENCE_MANIFEST.json directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.repo, args.reference, args.output)
