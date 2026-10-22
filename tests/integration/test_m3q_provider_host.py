"""OFFLINE: normal Host and Gemini adapter, scripted transport, real HTTP/Runtime."""
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
from time import monotonic, sleep

from linkloom.agents.providers import GeminiProviderAdapter, GEMINI_38_FLASH_MODEL_ID
from linkloom.ui.task_provider import compose_task_provider
from tests.m3p_support import product_case, product_http, QUESTION, cold_read, TEXTS, ScriptedStage
from tests.integration.test_product_application_flow import _client
from tests.unit.test_m3q_provider_budget import limits


class ScriptedGeminiTransport:
    """Only the network boundary is replaced. No source or plan is preseeded."""
    def __init__(self, stages):
        self.stages = stages
        self.requests = []
        self.fail_stage = None
        self.semantic_verdict = None
        self.invalid_final = False
        self.latency_seconds = 0

    def generate_content(self, *, model, contents, config):
        if self.latency_seconds:
            sleep(self.latency_seconds)
        self.requests.append(dict(model=model, contents=contents, config=config))
        prompt = json.dumps(contents, ensure_ascii=False)
        if self.fail_stage and (self.fail_stage in prompt or self.fail_stage in str(config)):
            raise TimeoutError('Synthetic transport timeout')
        # Feed the same structured stage seam, using the actual wire prompt.
        if 'emit_semantic_candidates' in str(config):
            stage = self.stages[0]
        elif 'INPUT_JSON' in prompt:
            stage = self.stages[1]
        elif 'AUTHORIZED_SNAPSHOT_JSON' in prompt:
            stage = self.stages[2]
        else:
            stage = None
        texts = [part['text'] for content in contents for part in content.get('parts', []) if 'text' in part]
        if stage:
            from linkloom.agents.model_adapter import ModelGenerationOptions
            # Stage fixture reads only user_input / tool_id.
            from linkloom.tools.contracts import ToolDefinition
            tools = config['tools'][0]['function_declarations']
            req = SimpleNamespace(user_input='\n'.join(texts),
                available_tools=[SimpleNamespace(tool_id=tools[0]['name'])], to_dict=lambda: {})
            action = stage.complete(req).action
            call = action.tool_call
            if stage.stage == 'semantics':
                for row in call.arguments['proposals']:
                    verdict = self.semantic_verdict or row['proposed_verdict']
                    row['proposed_verdict'] = verdict
                    evidence = row['supporting_evidence']
                    # UNKNOWN cites the inspected context under the existing
                    # source-coverage contract; it still asserts no relation.
                    row['supporting_evidence'] = evidence if verdict in {'SUPPORTED', 'CONFLICT', 'UNKNOWN'} else []
                    row['counter_evidence'] = evidence if verdict in {'REFUTED', 'CONFLICT'} else []
                    row['missing_evidence'] = ['Reliable premise is missing.'] if verdict == 'UNKNOWN' else []
                    if row['predicate'] == 'REVISES_PLAN' and verdict == 'SUPPORTED':
                        row['changed_fields'] = ['deadline']
            part = dict(function_call=dict(name=call.tool_id, args=call.arguments, id='fc-' + str(len(self.requests))))
        else:
            # Tool calls selected from the current wire history; real Runtime
            # receives/executes them and verifies the final structured result.
            responses = [p['function_response'] for c in contents for p in c.get('parts', []) if 'function_response' in p]
            called = {r['name']: r for r in responses}
            declarations = config['tools'][0]['function_declarations']
            names = {d['name'] for d in declarations}
            next_tool = next((t for t in ('inspect_task_context', 'query_task_relations', 'analyze_task_semantics')
                              if t in names and t not in called), None)
            if next_tool:
                d = next(d for d in declarations if d['name'] == next_tool)
                schema = d.get('parameters_json_schema', d.get('parameters'))
                args = dict(workspace_id=schema['properties']['workspace_id']['enum'][0])
                if next_tool == 'analyze_task_semantics':
                    args['investigation_id'] = 'product-investigation'
                part = dict(function_call=dict(name=next_tool, args=args, id='fc-' + str(len(self.requests))))
            else:
                # Wire IDs are mapped by GeminiProviderAdapter into the existing
                # runtime call identities. Function response carries call_id.
                ids = {r['name']: r['response'].get('call_id') for r in responses}
                final = {key: ids[t] for key, t in (
                    ('context_call_id', 'inspect_task_context'), ('relations_call_id', 'query_task_relations'),
                    ('semantic_call_id', 'analyze_task_semantics')) if t in ids}
                part = dict(text=json.dumps(dict(final, context_call_id='invented') if self.invalid_final else final))
        return dict(candidates=[dict(content=dict(role='model', parts=[part]), finish_reason='STOP')],
            usage_metadata=dict(prompt_token_count=20, candidates_token_count=10, total_token_count=30),
            model_version=model)


def provider_product(root):
    app, _service, _ask, stages, ws = product_case(root)
    transport = ScriptedGeminiTransport(stages)
    provider = GeminiProviderAdapter(transport)
    budget = replace(limits(), max_requests=16, max_input_tokens=500000,
        max_output_tokens=100000, max_cost_usd=2, max_seconds=60, request_timeout_seconds=10)
    service = compose_task_provider(app, provider, budget)
    return app, service, transport, stages, ws


def empty_provider_product(root):
    from linkloom.ui.product import ProductApplication
    app = ProductApplication(database_path=root / 'product.sqlite', extractor=None)
    stages = [ScriptedStage(s) for s in ('discovery', 'planning', 'semantics')]
    transport = ScriptedGeminiTransport(stages)
    budget = replace(limits(), max_requests=16, max_input_tokens=500000,
        max_output_tokens=100000, max_cost_usd=2, max_seconds=120, request_timeout_seconds=30)
    return app, compose_task_provider(app, GeminiProviderAdapter(transport), budget), transport


def test_explicit_product_discovery_output_exceeding_budget_never_reaches_provider(tmp_path):
    app, service, transport, _, ws = provider_product(tmp_path)
    service.task_models = replace(service.task_models,
        provider_limits=replace(service.task_models.provider_limits, max_output_tokens=3000))
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status']=='BLOCKED', result
            assert not transport.requests
    finally:
        app.close()


def test_cold_read_rejects_dual_field_and_role_binding_tamper(tmp_path):
    from linkloom.semantic_ingestion.task_observation import TaskObservation, TaskProposal, build_task_observation
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    app, service, transport, _, ws = provider_product(tmp_path)
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status']=='PARTIAL', result
            run_id=result['agent_run_id']
            directory=tmp_path/'work'/'investigations'
            pointer=json.loads((directory/(run_id+'.json')).read_text(encoding='utf8'))
            manifest_path=directory/pointer['root']/'preparation'/'preparation.json'
            manifest=json.loads(manifest_path.read_text(encoding='utf8'))
            key=next(k for k in ('valid_observations','partial_observations') if manifest['discovery'][k])
            original=TaskObservation.from_dict(manifest['discovery'][key][0])
            # Name 青竹 exists as a project. An internally consistent false owner
            # declaration can pass source replay; that does not prove semantics.
            bindings=tuple(replace(b,value='青竹',quote=original.raw_text,basis='SOURCE_TEXT')
                           if b.field=='owner' else b for b in original.field_bindings)
            proposal=TaskProposal(original.raw_text,
                tuple(e.quote for e in original.field_evidence if e.field=='description'),
                '青竹',original.deadline_text,original.project,original.vendor,bindings,'ASSERTED')
            artifact=RawArtifact(ws,original.provenance.artifact_id,'timestamped_text',TEXTS[0])
            altered=build_task_observation(proposal,parse_source_text(artifact).segments[0],artifact)
            assert altered.owner=='青竹' and altered.verify(artifact)
            manifest['discovery'][key][0]=altered.to_dict()
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf8')
            count=len(transport.requests)
            status,cold=cold_read(app.repository.database_path,run_id,ws)
            assert status==200 and cold['result']['status']=='BLOCKED',cold
            assert not cold['result']['evidence'] and not cold['result']['trace']
            assert not cold['result'].get('task_review')
            assert len(transport.requests)==count
    finally:
        app.close()


def test_normal_http_import_does_not_send_sources_before_ask_authorization(tmp_path):
    app, service, transport = empty_provider_product(tmp_path)
    try:
        with product_http(app, service) as base:
            code, body = _client(base, '/api/workspaces', method='POST', data={'name': 'Public synthetic task meetings'})
            assert code == 201, body
            ws = body['workspace']['workspace_id']
            _client(base, '/api/workspaces/current', method='POST', data={'workspace_id': ws})
            for index, text in enumerate(TEXTS[:2]):
                code, body = _client(base, '/api/sources/import', method='POST',
                    data=dict(name=f'meeting-{index+1}.txt', content=text))
                assert code == 202, body
            assert not transport.requests
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL', result
            assert {o['owner'] for o in result['task_review']['observations']} == {'林枫'}
            assert result['task_review']['observations'][1]['deadline_role'] == 'TASK_DEADLINE'
            assert result['task_review']['observations'][1]['deadline'] == '2029-06-24'
    finally:
        app.close()


@pytest.mark.parametrize('mutation', ['source_update', 'evidence_revoke', 'workspace'])
def test_live_composed_hot_and_cold_reads_revalidate_current_authority(tmp_path, mutation):
    app, service, transport, _, ws = provider_product(tmp_path)
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL'
            if mutation == 'evidence_revoke':
                with app.repository._lock, app.repository._connection:
                    app.repository._connection.execute('DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                        (ws, result['evidence'][0]['evidence_ref']))
            elif mutation == 'source_update':
                source = app.snapshot_investigation_sources(ws)[1][0]
                code, body = _client(base, f"/api/sources/{source['source_id']}", method='PUT',
                    data=dict(name=source['name'], content=source['content'] + '\nSource update.'))
                assert code == 202, body
            else:
                _, body = _client(base, '/api/workspaces', method='POST', data={'name': 'Other workspace'})
                _client(base, '/api/workspaces/current', method='POST', data={'workspace_id': body['workspace']['workspace_id']})
            count = len(transport.requests)
            code, body = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
            if mutation == 'workspace':
                assert code == 409
            else:
                assert code == 200 and body['result']['status'] == 'BLOCKED', body
                assert not body['result']['evidence'] and not body['result']['trace']
                code, body = cold_read(app.repository.database_path, result['agent_run_id'], ws)
                assert code == 200 and body['result']['status'] == 'BLOCKED', body
                assert not body['result']['evidence'] and not body['result']['trace']
            assert count == len(transport.requests)
    finally:
        app.close()


def ask_request(base, ws, *, authorized=False):
    _, config = _client(base, '/api/product/ask/config')
    return _client(base, '/api/product/ask', method='POST', data=dict(query=QUESTION,
        workspace_id=ws, allow_external_provider=authorized,
        investigation_context=dict(domain='tasks', snapshot_hash=config['investigation_context']['snapshot_hash'])))


def poll(base, ws, result):
    end = monotonic() + 30
    while result['status'] == 'INVESTIGATING' and monotonic() < end:
        sleep(.05)
        status, body = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
        assert status == 200, body
        result = body['result']
    return result


def test_normal_host_requires_source_permission_then_runs_actual_adapter(tmp_path):
    app, service, transport, stages, ws = provider_product(tmp_path)
    try:
        with product_http(app, service) as base:
            _, config = _client(base, '/api/product/ask/config')
            assert config['agent']['task_investigation']
            assert config['agent']['task_provider']['model_id'] == GEMINI_38_FLASH_MODEL_ID
            status, body = ask_request(base, ws)
            assert status == 403, body
            assert not transport.requests
            status, body = ask_request(base, ws, authorized=True)
            assert status == 202, body
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL', result
            assert result['reviewer']['accepted']
            assert len(result['task_review']['observations']) == 2
            assert stages[2].requests
            assert {p['predicate'] for p in result['task_review']['proposals']} == {'SAME_TASK', 'REVISES_PLAN'}
            count = len(transport.requests)
            status, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            assert status == 200 and cold['result']['status'] == 'PARTIAL', cold
            assert count == len(transport.requests)
    finally:
        app.close()


@pytest.mark.parametrize('damage', ['missing', 'UNCERTAIN', 'PENDING', 'FAILED', 'WITHHELD', 'counters', 'malformed'])
def test_live_hot_and_cold_reads_require_complete_provider_receipts(tmp_path, damage):
    app, service, transport, _, ws = provider_product(tmp_path)
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL', result
            run_id = result['agent_run_id']
            directory = tmp_path / 'work' / 'investigations'
            pointer = json.loads((directory / (run_id + '.json')).read_text(encoding='utf8'))
            journal = directory / pointer['root'] / 'provider' / 'budget.json'
            if damage == 'missing':
                journal.rename(journal.with_suffix('.unavailable'))
            elif damage == 'malformed':
                journal.write_text('{}', encoding='utf8')
            else:
                budget = json.loads(journal.read_text(encoding='utf8'))
                if damage == 'counters':
                    budget['requests_reserved'] -= 1
                else:
                    budget['attempts'][-1]['outcome'] = damage
                    budget['failure'] = None
                journal.write_text(json.dumps(budget), encoding='utf8')
            count = len(transport.requests)
            status, hot = _client(base, f'/api/product/ask/runs/{run_id}?workspace_id={ws}')
            assert status == 200 and hot['result']['status'] == 'BLOCKED', hot
            status, cold = cold_read(app.repository.database_path, run_id, ws)
            assert status == 200 and cold['result']['status'] == 'BLOCKED', cold
            for readback in (hot, cold):
                assert not readback['result']['evidence'] and not readback['result']['trace']
                assert not readback['result'].get('task_review')
            assert len(transport.requests) == count
    finally:
        app.close()


@pytest.mark.parametrize('verdict', ['SUPPORTED', 'REFUTED', 'CONFLICT', 'UNKNOWN'])
def test_http_preserves_unconfirmed_semantic_states_without_unlocking_p4(tmp_path, verdict):
    app, service, transport, stages, ws = provider_product(tmp_path)
    transport.semantic_verdict = verdict
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'PARTIAL', result
            assert {p['proposed_verdict'] for p in result['task_review']['proposals']} == {verdict}
            assert all(not p['publishable'] for p in result['task_review']['proposals'])
            assert result['task_review']['steps'][1]['status'] == 'BLOCKED'
            assert not result['task_review']['human_review']['available']
    finally:
        app.close()


@pytest.mark.parametrize('mode', ['discovery_timeout', 'semantics_timeout', 'budget', 'reviewer'])
def test_normal_http_failures_never_release_successful_partial_or_old_trace(tmp_path, mode):
    app, service, transport, stages, ws = provider_product(tmp_path)
    if mode.endswith('timeout'):
        transport.fail_stage = 'emit_semantic_candidates' if mode.startswith('discovery') else 'AUTHORIZED_SNAPSHOT_JSON'
    elif mode == 'budget':
        service.task_models = replace(service.task_models,
            provider_limits=replace(service.task_models.provider_limits, max_requests=5))
    else:
        transport.invalid_final = True
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'BLOCKED', result
            assert not result['evidence'] and not result.get('task_review'), result
            if mode != 'reviewer':
                assert not result['trace']
            count = len(transport.requests)
            if result.get('agent_run_id', '').startswith('ui-'):
                _, body = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
                assert body['result']['status'] == 'BLOCKED'
            assert count == len(transport.requests)
    finally:
        app.close()
