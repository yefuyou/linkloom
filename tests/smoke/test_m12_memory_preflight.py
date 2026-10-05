"""Offline acceptance for the actual M1.2 harness memory injection."""
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4
from io import BytesIO
from urllib.error import HTTPError

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, is_verified_evidence_result
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.context.assembler import ContextSourceType
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall, ToolResult
from tests.smoke import test_m12_real_provider_team_decision_smoke as h


def test_offline_mps_runtime_uses_seeded_product_memory_path():
    from tests.smoke.m12_memory_fixture import smoke_memory_binding, fixture_final

    root = Path('work') / ('m12-memory-' + uuid4().hex)
    vault = root / 'vault'
    shutil.copytree(h._workspace_for_case(h.PUBLIC_CASES[0]), vault)
    index = scan_vault(vault, root / 'scan').index_path
    frozen_before = hashlib.sha256(h.DATASET_PATH.read_bytes()).hexdigest()
    with smoke_memory_binding(vault, index, root / 'temporal.sqlite') as binding:
        adapter = binding.adapter
        hits = adapter._search_decision_memory('Atlas Lantern', None, 5)
        assert hits and hits[0]['source_evidence_refs'] == [binding.source_ref]
        assert any(i.source_type is ContextSourceType.DECISION_MEMORY
                   for i in adapter._last_context_bundle.selected)
        assert not adapter._evidence_by_id  # memory seeding is not model evidence
        memory_result = ToolResult(call_id='memory', tool_id='search_decision_memory',
                                   status='ok', value=hits)
        assert not is_verified_evidence_result(memory_result)
        with pytest.raises(ValidationError, match='not observed'):
            TeamDecisionResult.from_grounded_final(fixture_final(binding.value, binding.source_ref), [])
        def memory(request):
            return ModelAction.tool(ToolCall(call_id='memory', tool_id='search_decision_memory',
                arguments={'query': 'Atlas Lantern', 'limit': 5}))

        def finish(request):
            assert request.observation.value[0]['source_evidence_refs'] == [binding.source_ref]
            assert request.evidence_context == []
            # Abstain: a memory hit is not source grounding.
            return ModelAction.final(json.dumps({
                'schema_version': 'team-decision-result/v1',
                'decision': {'value': None, 'status': 'insufficient_evidence', 'evidence_refs': []},
                'rationale': [], 'rejected_alternatives': [], 'actions': [], 'unresolved_items': [],
                'uncertainty': {'status': 'insufficient_evidence', 'statement': None,
                                'unknown_fields': [], 'evidence_refs': []}, 'evidence_refs': []}))

        model = FakeModelAdapter([memory, finish])
        runtime = RuntimeEngine(vault, index, root / 'checkpoints', trace_dir=root / 'traces', model=model)
        result = runtime.start_multi_agent(RunRequest(request_id='m12-offline', thread_id='m12-offline',
            workflow='team_decision', query=h.PUBLIC_CASES[0].user_question,
            max_steps=6, max_provider_requests=6, dry_run=True))
        assert result.status == 'completed', result.error
        assert len(model.requests) == 2
        assert binding.constructions >= 1  # actual RuntimeEngine received the seeded adapter

    with smoke_memory_binding(vault, index, root / 'empty.sqlite', seed=False) as empty:
        assert empty.adapter._search_decision_memory('Atlas Lantern', None, 5) == []
        # Same frozen source inventory; only the memory context was removed.
        assert empty.adapter.reader.read_notes() == adapter.reader.read_notes()
    assert hashlib.sha256(h.DATASET_PATH.read_bytes()).hexdigest() == frozen_before


def test_offline_diagnostic_shape_never_contains_payload_values():
    metadata = h.count_request_metadata({
        'model': h.MODEL,
        'contents': [{'role': 'user', 'parts': [{'text': 'private-secret'},
                     {'function_response': {'name': 'secret-name', 'response': {'secret': 'private'}}}]}],
        'config': {'system_instruction': 'private-system', 'tools': [{'function_declarations': []}]},
    })
    text = json.dumps(metadata)
    assert 'private' not in text and 'secret-name' not in text
    assert metadata['model'] == h.MODEL
    assert metadata['request_shape']['has_tools'] is True
    assert metadata['request_shape']['has_system_instruction'] is True
    assert metadata['request_shape']['has_function_responses'] is True
    assert metadata['request_endpoint'].endswith(':countTokens')


def test_offline_count_probe_requires_separate_authorization():
    from scripts.run_m12_count_tokens_preflight import require_probe_authorization
    with pytest.raises(h.SmokeBlocked):
        require_probe_authorization({h.AUTHORIZATION_ENV: h.REQUIRED_AUTHORIZATION})


def test_offline_actual_smoke_executor_never_reverts_to_empty_memory(monkeypatch):
    root = Path('work') / ('m12-executor-' + uuid4().hex)
    seen = []

    def memory(request):
        return ModelAction.tool(ToolCall(call_id='memory', tool_id='search_decision_memory', arguments={
            'query': 'Atlas Lantern', 'limit': 5}))

    def final(request):
        seen.extend(request.observation.value)
        assert seen and seen[0]['decision_id'] == 'mps-001-current'
        assert seen[0]['source_evidence_refs'] == ['03-final-decision.md']
        assert not request.evidence_context
        return ModelAction.final(json.dumps({
            'schema_version': 'team-decision-result/v1',
            'decision': {'value': None, 'status': 'insufficient_evidence', 'evidence_refs': []},
            'rationale': [], 'rejected_alternatives': [], 'actions': [], 'unresolved_items': [],
            'uncertainty': {'status': 'insufficient_evidence', 'statement': None,
                            'unknown_fields': [], 'evidence_refs': []}, 'evidence_refs': []}))

    monkeypatch.setattr(h, 'GeminiProviderAdapter', lambda *a, **k: FakeModelAdapter([memory, final]))
    delegate = h.RecordingDelegate()
    h.execute_real_case(case=h.PUBLIC_CASES[0], sdk_client=delegate, run_root=root,
                        aggregate=h.AggregateUsage())
    assert delegate.calls == []
    assert seen
    assert (root / 'mps-001' / 'temporal_decisions.sqlite').is_file()


@pytest.mark.parametrize('fail', [False, True])
def test_offline_probe_sends_count_once_and_never_generates(fail):
    from scripts.run_m12_count_tokens_preflight import probe, AUTHORIZATION_ENV, REQUIRED_AUTHORIZATION
    calls = []
    request = {'model': h.MODEL, 'contents': [{'role': 'user', 'parts': [{'text': 'synthetic'}]}],
               'config': {'max_output_tokens': 8192, 'automatic_function_calling': {'disable': True}}}

    def transport(req, timeout):
        calls.append(req)
        assert req.full_url.endswith(':countTokens')
        assert json.loads(req.data)['generateContentRequest']['contents'] == request['contents']
        if fail:
            raise HTTPError(req.full_url, 400, 'bad', {}, BytesIO(json.dumps({'error': {
                'code': 400, 'status': 'INVALID_ARGUMENT',
                'message': 'Authorization: Bearer synthetic-secret',
            }}).encode()))
        return BytesIO(b'{"totalTokens": 123}')

    result = probe(request, {AUTHORIZATION_ENV: REQUIRED_AUTHORIZATION,
                            'GEMINI_API_KEY': 'synthetic-secret'}, transport=transport)
    assert len(calls) == 1 and result['generation_requests'] == 0
    assert result['status'] == ('FAIL' if fail else 'PASS')
    assert 'synthetic-secret' not in json.dumps(result)
    assert 'Authorization' not in json.dumps(result)
    if fail:
        assert result['failure_diagnostic']['http_status'] == 400
        assert result['failure_diagnostic']['exception_class'] == 'HTTPError'
        assert result['failure_diagnostic']['provider_error_code'] == 'INVALID_ARGUMENT'
    else:
        assert result['token_count'] == 123


def test_offline_current_count_payload_matches_installed_sdk():
    from types import SimpleNamespace
    from google.genai import types, models
    from scripts.run_m12_count_tokens_preflight import prepare_request
    request = prepare_request(Path('work') / ('m12-shape-test-' + uuid4().hex))
    validated = types._GenerateContentParameters.model_validate(request)
    sdk = models._GenerateContentParameters_to_mldev(
        SimpleNamespace(vertexai=False), validated.model_dump(exclude_none=True))
    sdk = json.loads(json.dumps(sdk, default=lambda x:
        x.model_dump(mode='json', by_alias=True, exclude_none=True)))
    sdk['model'] = sdk.pop('_url')['model']
    assert sdk == h._count_endpoint_generate_request(request)
    declarations = sdk['tools'][0]['functionDeclarations']
    assert 'search_decision_memory' in [item['name'] for item in declarations]


def test_offline_readiness_requires_every_gate():
    from scripts.run_m12_count_tokens_preflight import readiness_status, READINESS_GATES
    gates = dict.fromkeys(READINESS_GATES, True)
    assert readiness_status(gates) == 'READY'
    for name in READINESS_GATES:
        assert readiness_status({**gates, name: False}) == 'BLOCKED'
    assert readiness_status({}) == 'BLOCKED'


def test_offline_probe_preserves_nested_urlerror_before_wrapper_discards_it():
    from urllib.error import URLError
    from scripts.run_m12_count_tokens_preflight import probe, AUTHORIZATION_ENV, REQUIRED_AUTHORIZATION
    cause = ConnectionRefusedError(10061, 'connection refused synthetic-secret')
    cause.winerror = 10061
    error = URLError(cause)
    error.__cause__ = cause
    error.__context__ = cause

    def transport(*args, **kwargs):
        raise error

    result = probe({'model': h.MODEL, 'contents': [], 'config': {}},
                   {AUTHORIZATION_ENV: REQUIRED_AUTHORIZATION, 'GEMINI_API_KEY': 'synthetic-secret'},
                   transport=transport)
    chain = result['transport_exception_chain']
    assert chain['exception_type'] == 'URLError'
    assert 'ConnectionRefusedError' in chain['repr']
    assert chain['reason']['winerror'] == 10061
    assert chain['reason']['errno'] == 10061
    assert chain['reason']['transport_hint'] == 'CONNECTION_REFUSED'
    assert '__cause__' in chain and '__context__' in chain
    assert 'synthetic-secret' not in json.dumps(result)


def test_offline_transport_diagnostics_preserve_ssl_and_redact_credentials():
    import ssl
    from urllib.error import URLError
    from scripts.run_m12_count_tokens_preflight import serialize_transport_exception
    nested = ssl.SSLCertVerificationError(1, 'certificate verify failed')
    result = serialize_transport_exception(URLError(nested), secret='key-value')
    assert result['reason']['is_ssl_exception'] is True
    assert result['reason']['transport_hint'] == 'TLS_CERTIFICATE'
    result = serialize_transport_exception(URLError(
        'https://username:password@proxy.invalid:7897/?token=abc Authorization: Bearer xyz'), secret='key-value')
    text = json.dumps(result)
    for secret in ('password', 'username', 'token=abc', 'Bearer xyz'):
        assert secret not in text
