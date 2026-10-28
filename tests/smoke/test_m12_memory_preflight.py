"""Offline acceptance for the actual M1.2 harness memory injection."""
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4
from io import BytesIO
from urllib.error import HTTPError

import pytest

from tests.runtime_fixtures import source_context_from_request

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, is_verified_evidence_result
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.context.assembler import ContextSourceType
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall, ToolResult
from tests.smoke import test_m12_real_provider_team_decision_smoke as h


class _FixtureEmbedder:
    """Offline dense seam; source lookup and BM25 still run on real fixtures."""
    def encode(self, texts):
        import numpy as np
        terms = ("selected", "approved", "model", "provider")
        return np.asarray([[text.casefold().count(term) for term in terms]
                           for text in texts], dtype=np.float32)


def test_offline_mps_runtime_uses_seeded_product_memory_path():
    from tests.smoke.m12_memory_fixture import smoke_memory_binding, fixture_final

    root = Path('work') / ('m12-memory-' + uuid4().hex)
    vault = root / 'vault'
    shutil.copytree(h._workspace_for_case(h.PUBLIC_CASES[0]), vault)
    index = scan_vault(vault, root / 'scan').index_path
    frozen_before = hashlib.sha256(h.DATASET_PATH.read_bytes()).hexdigest()
    with smoke_memory_binding(vault, index, root / 'temporal.sqlite', embedder=_FixtureEmbedder()) as binding:
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

        def search_source(request):
            assert request.observation.value[0]['source_evidence_refs'] == [binding.source_ref]
            assert request.evidence_context == []
            return ModelAction.tool(ToolCall(call_id='search-source', tool_id='search_notes',
                arguments={'query': 'Atlas Lantern selected approved model provider',
                           'source_context': source_context_from_request(request), 'limit': 20}))

        def read_source(request):
            assert request.observation.status == 'ok'
            selected = next(item for item in request.observation.value
                            if item['relative_path'] == binding.source_ref
                            and 'selected' in item['quote'].casefold())
            return ModelAction.tool(ToolCall(call_id='read-source', tool_id='read_verified_note',
                arguments={'note_ref': selected['evidence_id']}))

        def finish(request):
            assert request.observation.status == 'ok'
            source = request.observation.value
            assert source['relative_path'] == binding.source_ref
            assert 'selected' in source['quote'].casefold()
            return ModelAction.final(fixture_final(source['quote'], source['evidence_id']))

        model = FakeModelAdapter([memory, search_source, read_source, finish])
        runtime = RuntimeEngine(vault, index, root / 'checkpoints', trace_dir=root / 'traces', model=model)
        result = runtime.start_multi_agent(RunRequest(request_id='m12-offline', thread_id='m12-offline',
            workflow='team_decision', query=h.PUBLIC_CASES[0].user_question,
            max_steps=6, max_provider_requests=6, dry_run=True))
        assert result.status == 'completed', result.error
        assert len(model.requests) == 4
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
