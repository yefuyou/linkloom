"""OFFLINE: normal Host and Gemini adapter, scripted transport, real HTTP/Runtime."""
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest
import socket
from time import monotonic, sleep

from linkloom.agents.providers import GeminiProviderAdapter, GEMINI_38_FLASH_MODEL_ID
from linkloom.ui.task_provider import compose_task_provider
from tests.m3p_support import product_case, product_http, QUESTION, cold_read, TEXTS, ScriptedStage
from tests.integration.test_product_application_flow import _client
from tests.unit.test_m3q_provider_budget import limits


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """HTTP is real; any accidental non-local Provider connection is a failure."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def checked(original):
        def call(sock, address):
            assert address[0] in {'127.0.0.1', '::1'}, 'TOOL_EXECUTION: offline gate attempted network'
            return original(sock, address)
        return call

    monkeypatch.setattr(socket.socket, 'connect', checked(connect))
    monkeypatch.setattr(socket.socket, 'connect_ex', checked(connect_ex))


class SourceBoundPlanningStage(ScriptedStage):
    """G's wire output reproduces M3-Q's trigger, using current F handles.

    Only scripted model output changes. Host preparation, tool registration,
    Runtime, ledger, evidence validation and Reviewer remain production code.
    """
    def __init__(self):
        super().__init__('planning')

    def complete(self, request):
        response = super().complete(request)
        payload = json.loads(request.user_input.split('INPUT_JSON\n', 1)[1])
        steps = response.action.tool_call.arguments['steps']
        for step in steps:
            step['source_handles'] = [e['span_id'] for e in payload['allowed_evidence']]
        steps.append(dict(steps[-1], id='dates', kind='compare_dates', predicate='',
            question='正式日期差值是否可发布？', depends_on=['identity', 'revision']))
        return response


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
    # All existing Host permission/freshness/failure tests now cover the exact
    # source-bound LIVE composition that M3-Q's unscoped fixture missed.
    stages[1] = SourceBoundPlanningStage()
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
                assert code == 409, ('SOURCE_FRESHNESS', body)
                code, body = cold_read(app.repository.database_path, result['agent_run_id'], ws)
                assert code == 409, ('SOURCE_FRESHNESS', body)
            else:
                assert code == 200 and body['result']['status'] == 'BLOCKED', ('SOURCE_FRESHNESS', body)
                assert not body['result']['evidence'] and not body['result']['trace']
                assert not any(body['result'].get(k) for k in ('task_review', 'task_investigation', 'preparation')), 'SOURCE_FRESHNESS'
                code, body = cold_read(app.repository.database_path, result['agent_run_id'], ws)
                assert code == 200 and body['result']['status'] == 'BLOCKED', ('SOURCE_FRESHNESS', body)
                assert not body['result']['evidence'] and not body['result']['trace']
                assert not any(body['result'].get(k) for k in ('task_review', 'task_investigation', 'preparation')), 'SOURCE_FRESHNESS'
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
            discovery_calls = [r for r in transport.requests if 'emit_semantic_candidates' in str(r['config'])]
            assert len(discovery_calls) == 2
            from linkloom.semantic_ingestion.extraction import _recall_schema
            for request in discovery_calls:
                assert 'task-recall/v3' in json.dumps(request['contents'], ensure_ascii=False)
                declaration = request['config']['tools'][0]['function_declarations'][0]
                schema = declaration.get('parameters_json_schema', declaration.get('parameters'))
                date_schema = schema['properties']['claims']['items']['properties']['task']['properties']['deadline_text']
                expected = _recall_schema()['properties']['claims']['items']['properties']['task']['properties']['deadline_text']
                # Gemini's wire mapping rewrites nullable/type spelling; the
                # F-specific token constraint must survive that translation.
                assert date_schema['pattern'] == expected['pattern']
                assert date_schema['description'] == expected['description']
            assert stages[2].requests
            assert {p['predicate'] for p in result['task_review']['proposals']} == {'SAME_TASK', 'REVISES_PLAN'}
            count = len(transport.requests)
            status, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            assert status == 200 and cold['result']['status'] == 'PARTIAL', cold
            assert count == len(transport.requests)
    finally:
        app.close()


def test_m3r_source_bound_product_chain_matches_durable_tool_receipts(tmp_path):
    """M3-Q regression oracle: F -> G(source_handles) -> Host -> real tools -> UI.

    LIVE *composition* uses scripted wire responses, not a live model. Never
    replace preparation, registration, Runtime execution or Reviewer with mocks.
    """
    from linkloom.runtime.artifacts import ModelArtifactStore
    from linkloom.runtime.checkpoint import SQLiteCheckpointer
    from linkloom.agents.task_relation_result import review_task_relation_result, task_relation_task_id

    app, service, transport, stages, ws = provider_product(tmp_path)
    # A missing explicit sidecar must stay missing, rather than acquiring P4
    # records as a side effect of a SUPPORTED model proposal.
    sidecar = tmp_path / 'relations.sqlite'
    service.task_relation_paths[ws] = sidecar
    try:
        with product_http(app, service) as base:
            code, body = ask_request(base, ws, authorized=True)
            assert code in {200, 202}, ('PLANNING', body)
            result = poll(base, ws, body['result'])
            manifests = list(tmp_path.rglob('preparation.json'))
            assert len(manifests) == 1, 'PREPARATION: missing durable Host manifest'
            manifest = json.loads(manifests[0].read_text(encoding='utf8'))
            observations = (manifest['discovery']['valid_observations']
                            + manifest['discovery']['partial_observations'])
            assert len(observations) == 2, 'PLANNING: F did not yield both task observations'
            plan = manifest['planning'][-1]
            assert plan['status'] == 'PLANNED', ('PLANNING', plan)
            relations = [s for s in plan['plan']['steps'] if s['kind'] == 'relation']
            assert {s['predicate'] for s in relations} == {'SAME_TASK', 'REVISES_PLAN'}, 'PLANNING'
            assert all(s['source_handles'] and s['source_bindings'] for s in relations), 'PLANNING: trigger lost'
            assert manifest['status'] == 'PARTIAL', ('PREPARATION', manifest['gaps'])
            job = service._jobs[result['agent_run_id']]
            assert job.task_preparation.tools.semantic_analysis is not None, 'PREPARATION: no SemanticAnalysis'

            # Read a new checkpoint reader, not an in-memory fake ledger.
            root = manifests[0].parent.parent
            pointer = json.loads((root.parent / (result['agent_run_id'] + '.json')).read_text(encoding='utf8'))
            state = SQLiteCheckpointer(root / 'runtime').get_latest(pointer['thread_id'])
            assert state is not None and state.model_executions, 'TOOL_REGISTRATION: Runtime produced no model turn'
            store = ModelArtifactStore(root / 'runtime' / 'models')
            first = state.model_executions[0]
            request = store.read(first.request_ref, expected_sha256=first.request_sha256)['model_request']
            assert 'analyze_task_semantics' in {t['tool_id'] for t in request['available_tools']}, 'TOOL_REGISTRATION'
            assert 'plus semantic_call_id' in request['user_input'], 'PREPARATION: semantic_analysis_enabled was false'
            records = state.tool_ledger
            assert [r.tool_id for r in records] == [
                'inspect_task_context', 'query_task_relations', 'analyze_task_semantics'], 'TOOL_EXECUTION'
            assert all(r.status == 'completed' and r.result['status'] == 'ok' for r in records), 'TOOL_EXECUTION'
            assert all(r.run_id == state.run_id and r.agent_id == 'retrieval_agent' for r in records), 'TOOL_EXECUTION'
            semantic = records[-1]
            assert semantic.arguments == dict(workspace_id=ws, investigation_id='product-investigation'), 'TOOL_EXECUTION'
            analysis = semantic.result['value']
            assert analysis['analysis_status'] == 'EXECUTED', ('TOOL_EXECUTION', analysis)
            assert analysis['outer_call_id'] == semantic.call_id, 'TOOL_EXECUTION'
            assert len(stages[2].requests) == 1, 'TOOL_EXECUTION: semantic provider missing or repeated'
            artifact = store.read(analysis['result_artifact']['ref'], expected_sha256=analysis['result_artifact']['sha256'])
            inner = store.read(artifact['request_artifact']['ref'], expected_sha256=artifact['request_artifact']['sha256'])
            assert inner['outer_tool_call']['call_id'] == semantic.call_id, 'TOOL_EXECUTION'
            assert inner['outer_tool_call']['arguments'] == semantic.arguments, 'TOOL_EXECUTION'
            assert {p['predicate'] for p in analysis['proposals']} == {'SAME_TASK', 'REVISES_PLAN'}, 'EVIDENCE_VALIDATION'
            assert all(p['evidence_status'] == 'EVIDENCE_VALID' for p in analysis['proposals']), 'EVIDENCE_VALIDATION'
            payload = result['task_investigation']
            assert payload['semantic_analysis'] == analysis, 'PRODUCT_PROJECTION: result differs from Ledger'
            assert payload['receipts'] == [dict(call_id=r.call_id, tool_id=r.tool_id,
                arguments=r.arguments, sequence=r.sequence) for r in records], 'REVIEWER: receipt mismatch'
            tools = job.task_preparation.tools.for_run(state.run_id, store, QUESTION)
            # P2-B requires the independent Host model-turn task identity.
            assert review_task_relation_result(payload, tools, records=records, run_id=state.run_id)['status'] == 'fail', 'REVIEWER: missing Host task identity'
            task_id = task_relation_task_id(state.model_executions, state.run_id)
            assert review_task_relation_result(payload, tools, records=records, run_id=state.run_id,
                task_id=task_id)['status'] == 'pass', 'REVIEWER'
            assert result['reviewer']['accepted'], 'REVIEWER'
            view = result['task_review']
            assert result['status'] == 'PARTIAL' and len(view['proposals']) == 2, 'PRODUCT_PROJECTION'
            for proposal in view['proposals']:
                assert proposal['proposed_verdict'] == 'SUPPORTED', 'PRODUCT_PROJECTION'
                assert proposal['semantic_status'] == 'SEMANTIC_NOT_EVALUATED' and not proposal['publishable'], 'REVIEWER: semantic promotion'
                assert proposal['supporting_evidence'], 'PRODUCT_PROJECTION: no original evidence'
                for citation in proposal['supporting_evidence']:
                    span = citation['span']
                    source = next(a for a in job.task_preparation.sources.artifacts
                                  if a.artifact_version_id == span['artifact_version_id'])
                    assert source.content[span['char_start']:span['char_end']] == citation['quote'], 'EVIDENCE_VALIDATION'
            assert not view['relations'] and not result['conclusions'] and not sidecar.exists(), 'REVIEWER: P4 promotion'
            assert all(s['status'] == 'BLOCKED' and not s['assessment_ids'] for s in view['steps']), 'REVIEWER: P4 unlocked'
            assert all('SOURCE_SCOPED_OPERATION_UNSUPPORTED' in s['blocking_reasons'] for s in view['steps']), 'REVIEWER: P4 scope weakened'
            dates = next(s for s in view['steps'] if s['kind'] == 'compare_dates')
            assert not dates['comparisons'] and not dates['comparison_current_valid'], 'PRODUCT_PROJECTION: formal date delta'

            count = len(transport.requests)
            code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws, sidecar)
            assert code == 200 and cold['result']['status'] == 'PARTIAL', ('SOURCE_FRESHNESS', cold)
            assert cold['result']['task_review'] == view, 'PRODUCT_PROJECTION: cold read differs'
            assert cold['result']['recovery'] == dict(mode='SAVED_RESULT_READ_ONLY', session_resumed=False), 'TOOL_EXECUTION: cold resume'
            assert len(transport.requests) == count and len(stages[2].requests) == 1, 'TOOL_EXECUTION: repeated Provider'
    finally:
        app.close()


def test_m3r_no_task_candidates_cannot_manufacture_relation_or_semantic_call(tmp_path):
    app, service, transport, stages, ws = provider_product(tmp_path)
    stages[0].missing = True
    try:
        with product_http(app, service) as base:
            _, body = ask_request(base, ws, authorized=True)
            result = poll(base, ws, body['result'])
            assert result['status'] == 'BLOCKED', ('PLANNING', result)
            assert not result['conclusions'] and not result.get('task_review'), 'PRODUCT_PROJECTION'
            assert not stages[2].requests, 'TOOL_EXECUTION: no valid endpoints'
            assert len(transport.requests) <= 3, 'PLANNING: unbounded retry'
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
