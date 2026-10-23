"""New synthetic combination scenarios; scripted responses are OFFLINE evidence."""
from dataclasses import replace
import json

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import compose_task_provider
from tests.integration.test_m3q_provider_host import ScriptedGeminiTransport, poll
from tests.integration.test_product_application_flow import _client
from tests.unit.test_m3q_provider_budget import limits
from tests.unit.test_task_field_binding import grounded
from tests.unit.test_task_observation_discovery import claim
from tests.unit.test_task_relation_question_planner import arguments, step

CASES = {
    'A': dict(text='兼容矩阵核验列入本轮执行，由乔雨负责，在2037-08-19之前交齐。',
              description='兼容矩阵核验', values=dict(owner='乔雨', deadline='2037-08-19'),
              question='兼容矩阵核验由谁负责，何时交齐？请调查并列出原文证据。'),
    'B': dict(text='索引重建列入本轮执行，承办人和交付日期尚未确定。',
              description='索引重建', values={},
              question='索引重建是否已列入工作，由谁负责，截止日是什么？未知字段请明确写未知。'),
    'C': dict(text='缓存校核已经列入本轮执行，执行人和交付日期尚未确定。',
              description='缓存校核', values={},
              question='调查缓存校核的执行人、截止日及缺失的补充确认；未知字段请明确写未知。'),
}
for case in CASES.values():
    case['source'] = '[2037-08-01T09:00:00Z] 记录员: ' + case['text']


class CombinedStage:
    def __init__(self, stage, case):
        self.stage, self.case, self.requests = stage, case, []

    def complete(self, request):
        self.requests.append(request)
        case = CASES[self.case]
        if self.stage == 'discovery':
            assert 'task-recall/v3' in request.user_input
            _, _, proposal, _ = grounded(case['text'], case['description'], values=case['values'])
            row = claim(case['text'], case['description'])
            row['task'] = proposal.to_dict()
            data = dict(claims=[row])
        elif self.stage == 'planning':
            payload = json.loads(request.user_input.split('INPUT_JSON\n', 1)[1])
            initial = payload['replan']['previous_plan'] is None
            if self.case == 'C' and initial:
                locate = dict(step('locate', 'discover', ()), target_kind='source',
                    missing_entities=['Supplementary assignment confirmation'],
                    evidence_gap='The authorized snapshot has no additional confirmation.')
                data = arguments(locate,
                    dict(step('prior_dependent', handles=('task_0',)), depends_on=['locate']))
            else:
                rows = [dict(step('read_owner', handles=('task_0',)),
                             evidence_gap='Inspect the admitted field in Runtime.'),
                        dict(step('read_date', handles=('task_0',)), field='deadline',
                             evidence_gap='Inspect the admitted date role in Runtime.')]
                if self.case == 'C':
                    rows.append(dict(step('still_dependent', handles=('task_0',)),
                        depends_on=['locate'], evidence_gap='New confirmation remains unavailable.'))
                rows.append(dict(step('ask_confirmation', 'clarify', ()),
                    missing_entities=['Final field confirmation']))
                data = arguments(*rows)
        else:
            raise AssertionError('These field-only scenarios do not require semantic calls')
        return ModelResponse(action=ModelAction.tool(ToolCall('offline-combined',
            request.available_tools[0].tool_id, data)),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30))


def combined_product(root, case):
    app = ProductApplication(database_path=root/'product.sqlite', extractor=None)
    stages = [CombinedStage(stage, case) for stage in ('discovery', 'planning', 'semantics')]
    transport = ScriptedGeminiTransport(stages)
    budget = replace(limits(), max_requests=12, max_input_tokens=500000,
        max_output_tokens=65536, max_cost_usd=3, max_seconds=120, request_timeout_seconds=30)
    return app, compose_task_provider(app, GeminiProviderAdapter(transport), budget), transport, stages


def start_case(base, case):
    _, body = _client(base, '/api/workspaces', method='POST', data=dict(name='Integration D '+case))
    ws = body['workspace']['workspace_id']
    _client(base, '/api/workspaces/current', method='POST', data=dict(workspace_id=ws))
    code, body = _client(base, '/api/sources/import', method='POST',
        data=dict(name='combined-'+case+'.txt', content=CASES[case]['source']))
    assert code == 202, body
    _, config = _client(base, '/api/product/ask/config')
    data = dict(query=CASES[case]['question'], workspace_id=ws, allow_external_provider=False,
        investigation_context=dict(domain='tasks', snapshot_hash=config['investigation_context']['snapshot_hash']))
    denied, body = _client(base, '/api/product/ask', method='POST', data=data)
    assert denied == 403, body
    code, body = _client(base, '/api/product/ask', method='POST', data=dict(data, allow_external_provider=True))
    assert code == 202, body
    return ws, poll(base, ws, body['result'])


def preparation(root):
    path, = (root/'work/investigations').glob('*/preparation/preparation.json')
    return path.parent, json.loads(path.read_text(encoding='utf8'))


def verify_case(case, result, prep_root, prep):
    assert result['status'] == 'PARTIAL', result
    assert result['reviewer']['accepted']
    observation, = result['task_review']['observations']
    assert CASES[case]['description'] in observation['description']
    assert observation['owner'] == CASES[case]['values'].get('owner')
    assert observation['deadline'] == CASES[case]['values'].get('deadline')
    assert observation['deadline_role'] == ('TASK_DEADLINE' if case == 'A' else 'UNKNOWN')
    assert result['evidence'] and all(e['verified'] for e in result['evidence'])
    assert not result['conclusions']
    assert {r['tool'] for r in result['task_review']['executed_tools']} == {
        'inspect_task_context', 'query_task_relations'}
    f_request = json.loads((prep_root/'discovery-1.json').read_text(encoding='utf8'))['request']
    assert 'task-recall/v3' in f_request['user_input']
    assert len(prep['planning']) == (2 if case == 'C' else 1)
    if case == 'C':
        first, final = [p['plan'] for p in prep['planning']]
        audited = json.loads((prep_root/'planning-2.json').read_text(encoding='utf8'))
        payload = json.loads(audited['request']['user_input'].split('INPUT_JSON\n', 1)[1])
        projected = payload['replan']['previous_plan']
        assert [(s['id'], s['depends_on']) for s in projected['steps']] == [
            (s['id'], s['depends_on']) for s in first['steps']]
        assert all('source_binding_handles' in s for s in projected['steps'])
        assert final['steps'][0] == first['steps'][0]
        receipt, = final['verified_results']
        assert receipt['step_id'] == 'locate' and receipt['outcome'] == 'UNKNOWN'
        assert audited['reservation']['input_tokens_upper_bound'] <= 24000
        kinds = audited['request']['available_tools'][0]['input_schema']['properties']['steps']['items']['properties']['kind']['enum']
        assert 'discover' not in kinds
        steps = {s['step_id']: s for s in result['task_review']['steps']}
        assert steps['locate']['status'] == 'UNKNOWN'
        assert steps['still_dependent']['status'] == 'BLOCKED'
        assert steps['read_owner']['status'] == 'COMPLETED'
    return observation
