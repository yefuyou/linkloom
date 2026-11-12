"""The fact contract is opt-in and normalized display never validates a value."""
from datetime import UTC, datetime
import json
from time import monotonic, sleep

import pytest
from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.fact_memory.extraction import FactDetails, parse_fact_claim
from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, SemanticExtractionContext
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication, ProductApplicationError
from test_semantic_ingestion_candidates import _source, _claim


class Provider:
    def __init__(self, value='a golden retriever', normalized='dog', event='last Tuesday'):
        self.value, self.normalized, self.event = value, normalized, event
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        claim = _claim(claim_type='FACT', subject='I', relation='pet', value=self.value,
                       temporal_status='NOT_STATED', task=None)
        if 'event_time_text' in json.dumps(request.to_dict()['available_tools']):
            claim.update(value_normalized=self.normalized, event_time_text=self.event)
        return ModelResponse(action=ModelAction.tool(ToolCall('c', request.available_tools[0].tool_id,
                                  {'claims': [claim]})), usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2))


def extractor(provider):
    return ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='offline',
        request_guard=lambda request: ProviderRequestAuthorization.approve(request, guard_id='g',
            reservation_id='r', estimated_cost_upper_bound_usd=0), request_guard_id='g', max_attempts=1)


def test_fact_prompt_and_schema_are_used_only_when_enabled(monkeypatch):
    artifact, segment = _source('I adopted a golden retriever last Tuesday.')
    context = SemanticExtractionContext(artifact.workspace_id, FrozenRelationResolver(('uses vendor',)))
    provider = Provider()
    monkeypatch.delenv('LINKLOOM_FACT_MEMORY', raising=False)
    old = extractor(provider).extract(segment, context)
    first = provider.requests[-1].to_dict()
    assert old.claims[0].fact_details is None
    assert 'event_time_text' not in json.dumps(first['available_tools'])
    monkeypatch.setenv('LINKLOOM_FACT_MEMORY', '1')
    new = extractor(provider).extract(segment, context)
    second = provider.requests[-1].to_dict()
    assert 'VERBATIM' in second['user_input']
    assert 'NONEMPTY' in second['user_input']
    assert 'DISPLAY ONLY' in second['user_input']
    assert 'event_time_text' in json.dumps(second['available_tools'])
    assert new.claims[0].fact_details == FactDetails('dog', 'last Tuesday')
    monkeypatch.delenv('LINKLOOM_FACT_MEMORY', raising=False)
    assert SemanticExtractionResult.from_dict(new.to_dict()).claims == new.claims
    extractor(provider).extract(segment, context)
    assert provider.requests[-1].to_dict() == first


def test_empty_attribute_and_fact_metadata_on_other_types_are_rejected():
    with pytest.raises(ValueError):
        parse_fact_claim(_claim(claim_type='FACT', subject='I', relation=None, value='blue'))
    with pytest.raises(ValueError):
        parse_fact_claim({**_claim(claim_type='DECISION'), 'event_time_text': 'yesterday'})
    with pytest.raises(ValueError):
        FactDetails.from_dict({'value_normalized': 'blue', 'event_time_text': None, 'authority': 'trusted'})


@pytest.mark.parametrize('value,normalized,event,accepted,reason', [
    ('a golden retriever', 'dog', 'last Tuesday', True, None),
    ('a husky', 'a golden retriever', 'last Tuesday', False, 'FACT_VALUE_NOT_IN_SOURCE'),
    ('a golden retriever', 'dog', 'last Friday', False, 'FACT_EVENT_TIME_NOT_IN_SOURCE'),
])
def test_product_fact_metadata_survives_cold_reload_and_does_not_bypass_evidence(
        tmp_path, monkeypatch, value, normalized, event, accepted, reason):
    monkeypatch.setenv('LINKLOOM_FACT_MEMORY', '1')
    provider = Provider(value, normalized, event)
    app = ProductApplication(database_path=tmp_path/'product.sqlite', extractor=extractor(provider))
    try:
        app.handle_post('/api/workspaces', {'name': 'Synthetic facts'})
        created, _ = app.handle_post('/api/sources/import', {'name': 'chat.txt',
            'content': '[2026-03-12T09:00:00Z] Speaker: I adopted a golden retriever last Tuesday.'})
        deadline = monotonic()+10
        while app.handle_get(f"/api/sources/{created['source']['source_id']}")['source']['ingestion_status']=='INGESTING':
            assert monotonic()<deadline
            sleep(.01)
        pending = app.materializer.list_pending(app._workspace_id())
        assert len(pending)==1
        candidate = pending[0]
        encoded, _ = serialize_candidate(candidate)
        assert deserialize_candidate(encoded).fact_details == FactDetails(normalized, event)
        corrupted=json.loads(encoded)
        corrupted["candidate"]["source_format"]="unsupported"
        with pytest.raises(ValueError):
            deserialize_candidate(json.dumps(corrupted))
        monkeypatch.delenv('LINKLOOM_FACT_MEMORY', raising=False)
        assert deserialize_candidate(encoded).fact_details == candidate.fact_details
        monkeypatch.setenv('LINKLOOM_FACT_MEMORY', '1')
        request = {'reviewer_id': 'repo-owner', 'reason': 'synthetic source checked'}
        if accepted:
            body,_=app.handle_post(f'/api/review/candidates/{candidate.candidate_id}/approve', request)
            assert body['value']==value
            record=app.fact_records(app._workspace_id())[0]
            assert record.value_text==value
            assert record.value_normalized!='dog'
            assert json.loads(record.fact_details_json)=={'value_normalized': normalized, 'event_time_text': event}
            assert record.effective_at==datetime(2026,3,12,9,tzinfo=UTC)
            from linkloom.fact_memory.ask import answer
            seen=[]
            class Capture:
                def complete(self,request):
                    seen.append(json.loads(request.user_input.split('Records: ',1)[1]))
                    return ModelResponse(action=ModelAction.final('{"status":"UNKNOWN"}'))
            answer('What pet did I adopt?',datetime(2026,4,1,tzinfo=UTC),(record,),Capture)
            assert seen[0][0]['event_time_text']==event
            assert seen[0][0]['source_stated_at']==record.effective_at.isoformat()
            assert 'value_normalized_display' not in seen[0][0]

        else:
            with pytest.raises(ProductApplicationError) as blocked:
                app.handle_post(f'/api/review/candidates/{candidate.candidate_id}/approve', request)
            assert reason in blocked.value.reason_codes
            assert app.fact_records(app._workspace_id())==()
    finally:
        app.close()


def test_existing_fact_table_is_readable_and_migrates_only_on_write():
    import sqlite3
    from dataclasses import fields
    from linkloom.fact_memory.store import FactRecord, FactStore, _SCHEMA, _storage
    from test_fact_memory_rules import _record
    connection=sqlite3.connect(':memory:')
    connection.row_factory=sqlite3.Row
    old_schema=_SCHEMA.replace("    fact_details_json TEXT NOT NULL DEFAULT '{}',\n", '')
    connection.executescript(old_schema)
    old=_record('legacy','jasmine',1)
    names=[field.name for field in fields(FactRecord) if field.name!='fact_details_json']
    connection.execute(f"INSERT INTO fact_record ({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                       [_storage(getattr(old,name)) for name in names])
    store=FactStore(connection)
    assert store.list('ws')[0].fact_details_json=='{}'
    assert 'fact_details_json' not in {r[1] for r in connection.execute('PRAGMA table_info(fact_record)')}
    store.add(_record('next','oolong',2))
    assert len(store.list('ws'))==2
    assert 'fact_details_json' in {r[1] for r in connection.execute('PRAGMA table_info(fact_record)')}
