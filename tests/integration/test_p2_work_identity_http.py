"""P2-A: scripted wire transport through normal Product HTTP and cold read."""
import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.semantic_ingestion.task_field_binding import FIELD_ROLES
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import compose_task_provider
from linkloom.tools.contracts import ToolCall
from tests.m3p_support import product_http, cold_read
from tests.integration.test_m3q_provider_host import ScriptedGeminiTransport, poll
from tests.integration.test_product_application_flow import _client
from tests.unit.test_m3q_provider_budget import limits
from tests.unit.test_task_observation_discovery import claim
from tests.unit.test_task_relation_question_planner import arguments, step

TEXT = '乔雨负责兼容矩阵核验和索引重建，这两项工作都在2037-08-19之前交齐。'
WORK = ('兼容矩阵核验', '索引重建')
SOURCE = '[2037-08-01T09:00:00Z] 记录员: ' + TEXT


def task(part, *, unknown=False):
    values = dict(owner=None if unknown else '乔雨', deadline=None if unknown else '2037-08-19')
    row = claim(TEXT, part, owner=values['owner'], deadline_text=values['deadline'])
    row['task'].update(work_state='ASSERTED', field_bindings=[dict(
        field=name, value=values.get(name), role=role if values.get(name) else 'UNKNOWN',
        state='ASSERTED' if values.get(name) else 'NOT_STATED',
        quote=TEXT if values.get(name) else '', task_quote=part,
        basis='SOURCE_TEXT', message_ordinal=0) for name, role in FIELD_ROLES.items()])
    return row


class IdentityStage:
    def __init__(self, stage, rows):
        self.stage, self.rows, self.requests, self.payloads = stage, rows, [], []

    def complete(self, request):
        self.requests.append(request)
        if self.stage == 'discovery':
            data = dict(claims=deepcopy(self.rows))
        elif self.stage == 'planning':
            payload = json.loads(request.user_input.split('INPUT_JSON\n', 1)[1])
            self.payloads.append(payload)
            steps = [dict(step(f'read_{i}_{field}', handles=(e['handle'],)), field=field)
                     for i, e in enumerate(payload['endpoints']) for field in ('owner', 'deadline')]
            data = arguments(*(steps or [dict(step('missing', 'clarify', ()),
                missing_entities=['两项任务的可验证观察'])]))
        else:
            raise AssertionError('Field inspection must not request P4 semantics')
        return ModelResponse(action=ModelAction.tool(ToolCall('identity-fixture',
            request.available_tools[0].tool_id, data)),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30))


def run_product(root, rows):
    app = ProductApplication(database_path=root / 'product.sqlite', extractor=None)
    stages = [IdentityStage(s, rows) for s in ('discovery', 'planning', 'semantics')]
    transport = ScriptedGeminiTransport(stages)
    budget = replace(limits(), max_requests=10, max_input_tokens=500000,
        max_output_tokens=65536, max_cost_usd=3, max_seconds=90, request_timeout_seconds=10)
    service = compose_task_provider(app, GeminiProviderAdapter(transport), budget)
    return app, service, transport, stages


def start(base):
    _, body = _client(base, '/api/workspaces', method='POST', data=dict(name='P2 work identity'))
    ws = body['workspace']['workspace_id']
    _client(base, '/api/workspaces/current', method='POST', data=dict(workspace_id=ws))
    _client(base, '/api/sources/import', method='POST', data=dict(name='two-work.txt', content=SOURCE))
    _, config = _client(base, '/api/product/ask/config')
    code, body = _client(base, '/api/product/ask', method='POST', data=dict(
        query='请分别核对兼容矩阵核验和索引重建的负责人及交付日期。', workspace_id=ws,
        allow_external_provider=True, investigation_context=dict(domain='tasks',
            snapshot_hash=config['investigation_context']['snapshot_hash'])))
    return ws, code, poll(base, ws, body['result'])


def manifest_for(root, run_id):
    directory = root / 'work' / 'investigations'
    pointer = json.loads((directory / (run_id + '.json')).read_text(encoding='utf8'))
    return json.loads((directory / pointer['root'] / 'preparation' / 'preparation.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('reverse,unknown', [(False, False), (True, False), (False, True)])
def test_two_work_http_identity_runtime_reviewer_and_cold_read(tmp_path, reverse, unknown):
    rows = [task(WORK[0], unknown=unknown), task(WORK[1])]
    if reverse:
        rows.reverse()
    app, service, transport, stages = run_product(tmp_path, rows)
    try:
        with product_http(app, service) as base:
            ws, post_code, result = start(base)
            manifest = manifest_for(tmp_path, result['agent_run_id'])
            from linkloom.ui.task_investigation import ProductTaskSources
            artifact = ProductTaskSources(ws, app.snapshot_investigation_sources(ws)[1],
                lambda: app.snapshot_investigation_sources(ws)[1]).artifacts[0]
            segment = parse_source_text(artifact).segments[0]
            proposed = [build_task_observation(TaskProposal.from_dict(r['task']), segment, artifact) for r in rows]
            cold_code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            receipt = dict(transport='scripted; no network or paid model', post_http_status=post_code,
                f_proposals=rows, built_observations=[o.to_dict() for o in proposed],
                ids_collide=proposed[0].observation_id == proposed[1].observation_id,
                discovery=manifest['discovery'], g_payloads=stages[1].payloads,
                http_result=result, cold_http_status=cold_code, cold_body=cold,
                transport_requests=len(transport.requests))
            if os.environ.get('LINKLOOM_P2_RECEIPT') and not reverse and not unknown:
                Path(os.environ['LINKLOOM_P2_RECEIPT']).write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
            assert len(result['task_review']['observations']) == 2, receipt['discovery']['failures']
            assert len({o.observation_id for o in proposed}) == 2
            assert all(o.observation_id.startswith('task_observation_v2_') for o in proposed)
            by_work = {o['description']: o for o in result['task_review']['observations']}
            assert by_work[WORK[1]]['owner'] == '乔雨' and by_work[WORK[1]]['deadline'] == '2037-08-19'
            if unknown:
                assert by_work[WORK[0]]['owner'] is by_work[WORK[0]]['deadline'] is None
            assert len(stages[1].payloads[0]['endpoints']) == 2
            assert result['status'] == 'PARTIAL' and result['reviewer']['accepted']
            assert not result['conclusions'] and not stages[2].requests
            assert all(o.verify(artifact) for o in proposed)
            assert [r['tool'] for r in result['task_review']['executed_tools']] == [
                'inspect_task_context', 'query_task_relations']
            assert cold_code == 200 and cold['result']['task_review'] == result['task_review']
    finally:
        app.close()


@pytest.mark.parametrize('mutation', ['update', 'revoke', 'foreign'])
def test_work_identity_hot_and_cold_permissions(tmp_path, mutation):
    app, service, transport, _ = run_product(tmp_path, [task(w) for w in WORK])
    try:
        with product_http(app, service) as base:
            ws, _, result = start(base)
            assert len(result['task_review']['observations']) == 2
            count = len(transport.requests)
            requested_ws = ws
            if mutation == 'update':
                saved_source = app.snapshot_investigation_sources(ws)[1][0]
                code, _ = _client(base, f"/api/sources/{saved_source['source_id']}", method='PUT',
                    data=dict(name=saved_source['name'], content=SOURCE + '\n来源更新。'))
                assert code == 202
            elif mutation == 'revoke':
                with app.repository._lock, app.repository._connection:
                    app.repository._connection.execute(
                        'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                        (ws, result['evidence'][0]['evidence_ref']))
            else:
                _, body = _client(base, '/api/workspaces', method='POST', data=dict(name='Foreign workspace'))
                requested_ws = body['workspace']['workspace_id']
                _client(base, '/api/workspaces/current', method='POST', data=dict(workspace_id=requested_ws))
            code, hot = _client(base, f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={requested_ws}")
            cold_code, cold = cold_read(app.repository.database_path, result['agent_run_id'], requested_ws)
            if mutation == 'foreign':
                assert code in {404, 409} and cold_code in {404, 409}
                assert TEXT not in json.dumps(hot, ensure_ascii=False)
                assert TEXT not in json.dumps(cold, ensure_ascii=False)
            else:
                assert code == cold_code == 200
                for body in (hot, cold):
                    assert body['result']['status'] == 'BLOCKED'
                    assert not body['result']['evidence'] and not body['result']['trace']
                    assert not body['result'].get('task_review')
            assert count == len(transport.requests)
    finally:
        app.close()


def test_legacy_profile_stored_v1_product_cold_read(tmp_path):
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.ui.task_investigation import TaskInvestigationModels
    from tests.m3p_support import runtime_model
    rows = [task(WORK[0])]
    rows[0]['task'].pop('field_bindings')
    rows[0]['task'].pop('work_state')
    app = ProductApplication(database_path=tmp_path / 'legacy.sqlite', extractor=None)
    stages = [IdentityStage(s, rows) for s in ('discovery', 'planning', 'semantics')]
    service = ProductInvestigationService(app, model_factory=runtime_model, external_provider=False,
        run_mode='FAKE_MODEL_INTEGRATION', task_models=TaskInvestigationModels(*stages, execution_kind='REPLAY'))
    try:
        with product_http(app, service) as base:
            ws, _, result = start(base)
            saved = manifest_for(tmp_path, result['agent_run_id'])
            observations = saved['discovery']['partial_observations'] + saved['discovery']['valid_observations']
            assert len(observations) == 1 and observations[0]['observation_id'].startswith('task_observation_v1_')
            assert result['reviewer']['accepted']
            code, cold = cold_read(app.repository.database_path, result['agent_run_id'], ws)
            assert code == 200 and cold['result']['task_review'] == result['task_review']
            assert manifest_for(tmp_path, result['agent_run_id']) == saved
    finally:
        app.close()
