"""Canary input/cost boundaries; synthetic assertions are not semantic scores."""
import hashlib
import json

import pytest

from scripts import run_ami_canary_v2 as runner


def inputs(tmp_path):
    root = tmp_path / 'inputs'
    (root / 'transcripts').mkdir(parents=True)
    manifest = {'files': {}}
    for meeting, count in zip(('ES2002a', 'ES2005c'), (8, 20)):
        chunks, cursor = [], 0
        for index in range(count):
            text = f'[{meeting} speaker A {index}] Please review component {index}.\n'
            chunks.append(dict(text=text, start=cursor, end=cursor+len(text),
                               sha256=hashlib.sha256(text.encode()).hexdigest()))
            cursor += len(text)
        canonical = ''.join(c['text'] for c in chunks)
        transcript = dict(chunks=chunks, canonical_text=canonical, utterances=[],
                          canonical_sha256=hashlib.sha256(canonical.encode()).hexdigest())
        path = root / 'transcripts' / f'{meeting}.json'
        path.write_text(json.dumps(transcript), encoding='utf8')
        manifest['files'][f'transcripts/{meeting}.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    return root


def test_preflight_never_constructs_client_or_reads_official_labels(tmp_path, monkeypatch):
    root = inputs(tmp_path)
    secret = 'OFFICIAL_GOLD_MUST_NOT_REACH_A_MODEL_98765'
    for filename in ('queries.jsonl', 'qrels.jsonl', 'summary.xml'):
        (root / filename).write_text(secret, encoding='utf8')

    def forbidden(*args, **kwargs):
        raise AssertionError('no SDK construction in free preflight')

    monkeypatch.setattr(runner, 'build_gemini_sdk_client', forbidden)
    output = tmp_path / 'preflight'
    runner.run(root, output)
    requests = json.loads((output / 'planned_requests.json').read_text(encoding='utf8'))
    assert len(requests) == 28
    assert secret not in json.dumps(requests)
    assert not (output / 'actual_requests.jsonl').exists()
    assert sum(json.loads((output / 'plan.json').read_text())['config']['reservations']) <= 8
    with pytest.raises(FileExistsError):
        runner.run(root, output)


def test_source_tampering_is_rejected_before_any_request_or_client(tmp_path):
    root = inputs(tmp_path)
    with (root / 'transcripts' / 'ES2002a.json').open('a', encoding='utf8') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='manifest mismatch'):
        runner.run(root, tmp_path / 'tampered')


def test_timeout_is_unknown_stops_batch_without_retry_and_keeps_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from linkloom.evaluation.canary_trust import summarize_attempts
    calls = []

    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError('ambiguous transport timeout')

    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setattr(runner, 'verify_gate', lambda *args: None)
    monkeypatch.setattr(runner, 'build_gemini_sdk_client', lambda *args, **kwargs:
                        SimpleNamespace(models=SimpleNamespace(generate_content=timeout)))
    output = tmp_path / 'unknown'
    runner.run(inputs(tmp_path), output, execute=True, gate=tmp_path / 'test-gate')
    summary = summarize_attempts(output / 'attempts')
    assert len(calls) == 1
    assert summary['outcomes'] == {'OUTCOME_UNKNOWN': 1}
    assert summary['cost_reserved_usd'] == .25
    assert json.loads((output / 'completion.json').read_text())['session_failure']


def test_live_gate_fails_before_sdk_construction(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('client must not be constructed')
    monkeypatch.setattr(runner, 'build_gemini_sdk_client', forbidden)
    with pytest.raises(ValueError, match='gate'):
        runner.run(inputs(tmp_path), tmp_path / 'without-gate', execute=True)


def test_late_sdk_receipt_cannot_rewrite_sealed_unknown(tmp_path, monkeypatch):
    from dataclasses import replace
    from threading import Event
    from types import SimpleNamespace
    from linkloom.evaluation.canary_trust import sha, summarize_attempts
    from linkloom.agents.model_adapter import ModelResponse
    release, finished = Event(), Event()

    class LateResponse:
        def model_dump(self, **kwargs):
            return {'response_id': 'late-receipt', 'usage_metadata': None}

    def delayed(**kwargs):
        release.wait(10)
        finished.set()
        return LateResponse()

    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setattr(runner, 'verify_gate', lambda *args: None)
    monkeypatch.setattr(runner, 'LIMITS', replace(runner.LIMITS, request_timeout_seconds=.05))
    monkeypatch.setattr(runner, 'build_gemini_sdk_client', lambda *args, **kwargs:
                        SimpleNamespace(models=SimpleNamespace(generate_content=delayed)))
    class Adapter:
        def __init__(self, client, **kwargs):
            self.client = client
        def complete(self, request):
            self.client.generate_content(model='gemini-3.8-flash', contents=[], config={})
            return ModelResponse(error=runner.ModelProviderError(code='MODEL_UNKNOWN_FAILURE',
                category='unknown_provider_error', message='late synthetic response',
                retryable=False, outcome='unknown_provider_outcome'))
    monkeypatch.setattr(runner, 'GeminiProviderAdapter', Adapter)
    output = tmp_path / 'late'
    runner.run(inputs(tmp_path), output, execute=True, gate=tmp_path / 'test-gate')
    seal = json.loads((output / 'output_seal.json').read_text())
    release.set()
    assert finished.wait(5)
    # Wait for the worker's immutable late terminal using bounded event polling.
    import time
    terminal = tmp_path / 'late-late-receipts/normalization/0001.json'
    deadline = time.monotonic() + 5
    while not terminal.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert terminal.exists()
    assert summarize_attempts(output / 'attempts')['outcomes'] == {'OUTCOME_UNKNOWN': 1}
    assert all(sha((output / p).read_bytes()) == h for p, h in seal['files'].items())


def test_gate_rejects_stale_test_source_and_incompatible_profile(tmp_path):
    from scripts.prepare_canary_gate import validate_gate, REQUIRED
    from scripts.run_canary_checks import SUITES
    from linkloom.evaluation.canary_trust import save_new, sha
    identity = {'frozen_sha': 'frozen', 'code_hashes': {'code': 'hash'}, 'runtime': {'sdk': 'fixed'},
                'source_hashes': {'source': 'hash'}, 'input_files': {'manifest.json': 'hash'}}
    tests = {}
    for name in REQUIRED:
        report = tmp_path / f'{name}.xml'
        report.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"/>')
        tests[name] = {'path': str(report), 'sha256': sha(report.read_bytes())}
        save_new(report.with_suffix('.identity.json'), dict(**identity,
            report_sha256=tests[name]['sha256'], suite=name, tests=SUITES[name]))
    discovery = tmp_path / 'discovery.json'
    discovery.write_text(json.dumps([dict(meeting_id=m, result={'extractions': [{}] * count})
                                     for m, count in [('ES2002a', 8), ('ES2005c', 20)]]))
    historical_seal = tmp_path / 'historical-seal.json'
    historical_seal.write_text('{}')
    replay = tmp_path / 'replay_receipt.json'
    value = dict(**identity, provider_calls=0, replayed_outputs=28, task_profile=None,
                 status='PASS', discovery_sha256=sha(discovery.read_bytes()),
                 complete_meetings={'ES2002a': 8, 'ES2005c': 20},
                 historical_seal_path=str(historical_seal), historical_seal_sha256=sha(historical_seal.read_bytes()))
    save_new(replay, value)
    gate = tmp_path / 'gate.json'
    packet = dict(**identity, tests=tests, replay={'path': str(replay), 'sha256': sha(replay.read_bytes())})
    save_new(gate, packet)
    validate_gate(gate, identity)
    with pytest.raises(ValueError, match='identity drift'):
        validate_gate(gate, dict(identity, frozen_sha='different'))
    with pytest.raises(ValueError, match='runtime identity drift'):
        validate_gate(gate, dict(identity, runtime={'sdk': 'different'}))
    with pytest.raises(ValueError, match='input source identity drift'):
        validate_gate(gate, dict(identity, source_hashes={'different': 'source'}))
    value['task_profile'] = 'TASK_RECALL_V3'
    replay.write_text(json.dumps(value))
    packet['replay']['sha256'] = sha(replay.read_bytes())
    gate.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match='regression not verified'):
        validate_gate(gate, identity)


def test_source_change_after_first_send_stops_with_explicit_safety_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    root, calls = inputs(tmp_path), []
    class Raw:
        def model_dump(self, **kwargs):
            return {'response_id': 'receipt', 'usage_metadata': None}
    def change_source(**kwargs):
        calls.append(kwargs)
        path = root / 'transcripts/ES2002a.json'
        path.write_text(path.read_text() + ' ')
        return Raw()
    class Adapter:
        def __init__(self, client, **kwargs):
            self.client = client
        def complete(self, request):
            self.client.generate_content(model='gemini-3.8-flash', contents=[], config={})
            return ModelResponse(action=ModelAction.final('fixture'))
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setattr(runner, 'verify_gate', lambda *args: None)
    monkeypatch.setattr(runner, 'GeminiProviderAdapter', Adapter)
    monkeypatch.setattr(runner, 'build_gemini_sdk_client', lambda *args, **kwargs:
                        SimpleNamespace(models=SimpleNamespace(generate_content=change_source)))
    output = tmp_path / 'drift'
    runner.run(root, output, execute=True, gate=tmp_path / 'test-gate')
    completion = json.loads((output / 'completion.json').read_text())
    assert len(calls) == 1
    assert completion['session_failure'] == 'TASK_SOURCE_AUTHORIZATION_CHANGED'
    assert completion['unattempted_requests'] == 27


def test_pre_sdk_mapping_failure_is_not_sent_with_reservation_retained(tmp_path, monkeypatch):
    from linkloom.evaluation.canary_trust import summarize_attempts
    class Adapter:
        def __init__(self, client, **kwargs):
            pass
        def complete(self, request):
            return runner.ModelResponse(error=runner.ModelProviderError(code='MODEL_INVALID_REQUEST',
                category='invalid_request', message='pre-SDK mapping fixture', retryable=False,
                outcome='known_failure'))
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setattr(runner, 'verify_gate', lambda *args: None)
    monkeypatch.setattr(runner, 'GeminiProviderAdapter', Adapter)
    monkeypatch.setattr(runner, 'build_gemini_sdk_client', lambda *args, **kwargs: object())
    output = tmp_path / 'not-sent'
    runner.run(inputs(tmp_path), output, execute=True, gate=tmp_path / 'test-gate')
    summary = summarize_attempts(output / 'attempts')
    assert summary['outcomes'] == {'NOT_SENT': 1}
    assert summary['cost_reserved_usd'] == .25
