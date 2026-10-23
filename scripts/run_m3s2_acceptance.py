"""Bounded synthetic F comparison. No retries; each run directory is single-use."""
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path[:0] = ['src', '.']
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService, _project
from linkloom.ui.task_provider import TaskProviderSession, TaskProviderLimits

DOC = Path('docs/requirements/m3-s2-task-recall')
OLD = Path('docs/requirements/m3-s-task-field-grounding')
OUT = Path('work/m3s2-live')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def original_cases():
    data = read(OLD/'field-cases.json')
    gold = {r['id']: r['expected'] for r in read(OLD/'holdout-expectations.json')['cases']}
    return data['cases'] + [dict(r, expected=gold[r['id']]) for r in data['holdout_inputs_fixed_before_expectation_reveal']]


def evaluate(cases, observations):
    totals, rows = Counter(), []
    for ordinal, case in enumerate(cases):
        selected = [o for o in observations if o.provenance.ordinal == ordinal]
        matched = set()
        for gold in case['expected']:
            preds = [o for o in selected if gold['anchor'] in o.raw_text]
            obs = preds[0] if len(preds) == 1 else None
            totals['expected_tasks'] += 1
            totals['recalled_tasks' if obs else 'missed_tasks'] += 1
            if obs:
                matched.add(obs.observation_id)
            for field in ('owner', 'deadline', 'project', 'vendor'):
                wanted = [gold['deadline_text'], gold['deadline'], gold['precision'], gold['role']] if field == 'deadline' else gold[field]
                actual = [obs.deadline_text, obs.deadline, obs.deadline_precision, obs.deadline_role] if field == 'deadline' and obs else getattr(obs, field) if obs else None
                unknown = gold['deadline_text'] is None if field == 'deadline' else wanted is None
                absent = obs is not None and (obs.deadline_text is None if field == 'deadline' else actual is None)
                outcome = ('MISSING_OBSERVATION' if obs is None else 'CORRECT_UNKNOWN' if unknown and absent else
                           'CORRECT_ACCEPT' if not unknown and actual == wanted else 'FALSE_ACCEPT' if not absent else 'REJECTED')
                totals[outcome] += 1
                rows.append(dict(case_id=case['id'], anchor=gold['anchor'], field=field, expected=wanted, actual=actual, outcome=outcome))
        totals['false_recalled_tasks'] += sum(o.observation_id not in matched for o in selected)
        if not case['expected']:
            totals['negative_cases'] += 1
            totals['negative_cases_with_false_recall'] += bool(selected)
    return dict(totals=dict(totals), fields=rows)


def restored_result(batch):
    # Recover canonical metadata from stored provenance, not newly inferred headers.
    first = next(iter(batch['discovery']['valid_observations']+batch['discovery']['partial_observations']), None)
    span = first['provenance']
    return RawArtifact(span['workspace_id'], span['artifact_id'], 'timestamped_text', batch['source'],
                       ingestion_time=datetime.fromisoformat(first['recorded_at']))


def audit():
    cases = {c['id']: c for c in original_cases()}
    attribution = []
    scores = Counter()
    for batch in read(OLD/'evidence/field-live-results.json')['batches']:
        path = Path('../m3s')/batch['original_file']
        assert digest(path) == batch['original_file_sha256'], 'ORIGINAL_RECEIPT_CHANGED'
        artifact = restored_result(batch)
        current, legacy, failures, vetoes = [], [], [], []
        proposals = []
        for data in batch['discovery']['extractions']:
            result = SemanticExtractionResult.from_dict(data)
            found, _, rejected = _project(result, artifact, parse_source_text(artifact).segments, task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2)
            assert all(o.verify(artifact) for o in found)
            current.extend(found); failures.extend(f.reason for f in rejected)
            old_claims = tuple(replace(c, task=replace(c.task, field_bindings=None, work_state=None)) if c.task else c for c in result.claims)
            old = SimpleNamespace(claims=old_claims)
            found, _, rejected = _project(old, artifact, parse_source_text(artifact).segments)
            legacy.extend(found); vetoes.extend(f.reason for f in rejected)
            proposals.extend(c.task.to_dict() for c in result.claims if c.task)
        score = evaluate([cases[k] for k in batch['cases']], current)
        scores.update(score['totals'])
        attribution.append(dict(cases=batch['cases'], original_sha256=digest(path), original_file=str(path),
            proposals=proposals, projection_failures=failures, legacy_failures=vetoes,
            receipts=[d['receipt'] for d in batch['discovery']['extractions']], evaluation=score))
    result = dict(totals=dict(scores), batches=attribution,
        findings={'F04':'NO_MODEL_PROPOSAL; no projection failure',
                  'H02':'PROTOCOL_DATE_TOKEN; observation survives, deadline rejected as DEADLINE_INVALID',
                  'F05_F06':'LEGACY_NON_ASSERTED_TASK_VETO; same real proposals survive v2 projection'})
    save(DOC/'evidence/original-audit.json', result)
    print('ORIGINAL', dict(scores), flush=True)


def budget_snapshot():
    summary = read(OLD/'evidence/live-summary.json')
    prior = dict(requests=summary['cumulative_requests'], reserved_usd=summary['cumulative_reserved_usd_conservative'])
    snapshots = []
    # Read the actual concurrent sibling journals, never change them or reset budgets.
    capacity = prior['reserved_usd']
    for root in sorted(Path('../m3s1/work').glob('m3s1-live*')):
        ledger = root/'global-provider/budget.json'
        if not ledger.exists():
            continue
        data = read(ledger)
        prior['requests'] += data['requests_reserved']
        prior['reserved_usd'] += data['cost_reserved_usd']
        capacity += data['limits']['max_cost_usd']  # include unspent S1 allocation
        snapshots.append(dict(path=str(ledger), sha256=digest(ledger), requests=data['requests_reserved'],
            reserved_usd=data['cost_reserved_usd'], allocation_usd=data['limits']['max_cost_usd']))
    return dict(prior=prior, s1_snapshots=snapshots, prior_plus_s1_full_allocation_usd=capacity)


def live(*, unattempted=False):
    global OUT
    prior_campaign = None
    if unattempted:
        prior_campaign = read(OUT/'provider/budget.json')
        # Transport returned all nine responses. The ninth has a known schema
        # error, not a retryable/uncertain call. Never send that experiment again.
        assert prior_campaign['requests_reserved'] == 9 and prior_campaign['failure'] is None
        assert read(OUT/'new-FIELD_GROUNDED_V2-1.json')['discovery']['extractions'][0]['failure_code'] == 'MALFORMED_EXTRACTION'
        if unattempted == 2:
            followup = read('work/m3s2-unattempted/provider/budget.json')
            assert followup['requests_reserved'] == 1 and all(a['outcome'] != 'UNCERTAIN' for a in followup['attempts'])
        OUT = Path('work/m3s2-unattempted-2' if unattempted == 2 else 'work/m3s2-unattempted')
    assert not OUT.exists(), 'SINGLE_USE_RUN; no automatic reruns'
    snap = budget_snapshot()
    assert snap['prior_plus_s1_full_allocation_usd']+5 <= 50
    paths = [DOC/'cases.json', OLD/'field-cases.json', OLD/'holdout-expectations.json']
    fixed = {str(p): digest(p) for p in paths}
    save(OUT/'reservation.json', dict(started_at=datetime.now(UTC).isoformat(), **snap,
        fixed_inputs=fixed, max_requests=4 if unattempted == 2 else 5 if unattempted else 20,
        max_reserved_usd=1 if unattempted == 2 else 1.25 if unattempted else 5, project_cap_usd=50,
        prior_campaign=prior_campaign, execution_scope='Previously unsent independent experiments only' if unattempted else 'Initial campaign',
        sdk='2.22.0', attempts=1, model=GEMINI_38_FLASH_MODEL_ID,
        actual_bill_usd=None, billing_project_verified=False))
    sdk = build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],
        http_options_kwargs=dict(timeout=45000, retry_options=dict(attempts=1)))
    class Bridge:
        def generate_content(self, **kwargs):
            return sdk.models.generate_content(**kwargs)
    real = GeminiProviderAdapter(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    session = TaskProviderSession(OUT/'provider', TaskProviderLimits(4 if unattempted == 2 else 5 if unattempted else 20,
        1500000, 100000, 1 if unattempted == 2 else 1.25 if unattempted else 5, 1000, 50, .25), lambda: None)
    class Campaign(GeminiProviderAdapter):
        def complete(self, request):
            save(OUT/f'requests/{session.requests+1:02}.json', request.to_dict())
            response = session.complete(real, request.agent_id, request)
            save(OUT/f'responses/{session.requests:02}.json', response.to_dict())
            return response
    provider = Campaign(Bridge(), model_id=GEMINI_38_FLASH_MODEL_ID)
    def guard(request):
        return ProviderRequestAuthorization.approve(request, guard_id='m3s2', reservation_id='m3s2-fixed', estimated_cost_upper_bound_usd=.25)
    reports = []
    try:
        originals = {c['id']: c for c in original_cases()}
        batches = [(f'original-{b["batch"]}', TaskDiscoveryProfile.TASK_RECALL_V3, [originals[k] for k in b['cases']], b['source'])
                   for b in read(OLD/'evidence/field-live-results.json')['batches']]
        new = read(DOC/'cases.json')['cases']
        for profile in (TaskDiscoveryProfile.FIELD_GROUNDED_V2, TaskDiscoveryProfile.TASK_RECALL_V3):
            for i in range(0, len(new), 4):
                group = new[i:i+4]
                source = '\n'.join(f'[2030-01-0{j+1}T09:00:00Z] {c["speaker"]}: {c["text"]}' for j, c in enumerate(group))
                batches.append((f'new-{profile.name}-{i//4+1}', profile, group, source))
        if unattempted == 2:
            batches = batches[12:] + batches[9:11]
        elif unattempted:
            batches = batches[11:] + batches[9:11]
        for name, profile, cases, source in batches:
            assert all(digest(p) == sha for p, sha in fixed.items()), 'FIXTURE_CHANGED'
            artifact = RawArtifact('m3s2-public', name+'.txt', 'timestamped_text', source)
            parsed = parse_source_text(artifact)
            registry = SourceReferenceRegistry()
            for seg in parsed.segments:
                registry.register_evidence(artifact.workspace_id, seg.evidence_span.evidence_ref,
                    content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
            extractor = ProviderNeutralSemanticExtractor(provider, provider_id='gemini', model_id=GEMINI_38_FLASH_MODEL_ID,
                request_guard=guard, request_guard_id='m3s2', max_attempts=1, max_output_tokens=4096)
            result = TaskObservationDiscoveryService(extractor, source_registry=registry, current_source=lambda ws, aid: artifact,
                task_profile=profile).discover((artifact,), workspace_id=artifact.workspace_id)
            obs = (*result.valid_observations, *result.partial_observations)
            assert all(o.verify(artifact) for o in obs)
            row = dict(name=name, profile=profile.value, source=source, cases=[c['id'] for c in cases],
                discovery=result.to_dict(), evaluation=evaluate(cases, obs))
            save(OUT/(name+'.json'), row); reports.append(row)
            print(name, row['evaluation']['totals'], flush=True)
            if session.failure or any(e.failure_code for e in result.extractions):
                raise RuntimeError('STOP_NO_RETRY: '+str(session.failure or 'EXTRACTION_FAILURE'))
        save(OUT/'f-results.json', reports)
    finally:
        sdk.close()
        save(OUT/'final-budget-snapshot.json', budget_snapshot())


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    if sys.argv[1:] == ['audit']:
        audit()
    elif sys.argv[1:] == ['live']:
        live()
    elif sys.argv[1:] == ['unattempted']:
        live(unattempted=True)
    elif sys.argv[1:] == ['unattempted-2']:
        live(unattempted=2)
    else:
        raise SystemExit('Choose audit or live; LIVE requires an authorized finite campaign.')
