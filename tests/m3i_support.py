"""Scripted FakeModel product wiring fixture; no semantic intelligence score."""
import json
from time import monotonic, sleep
from uuid import uuid4

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_investigation import TaskInvestigationModels
from linkloom.ui.unified_ask import UnifiedAskService
from tests.unit.test_task_observation_discovery import claim

QUESTION = '请调查这几次会议里交付任务如何延续，截止日期的变动是否有可靠依据？'
TEXTS = (
    '[2029-06-01T09:00:00Z] 林枫: 项目：青竹；负责人：林枫；完成日志迁移；截止日期：2029-06-20。',
    '[2029-06-04T09:00:00Z] 林枫: 延续上次的完成日志迁移，截止日期调整为2029-06-24；项目：青竹；负责人：林枫。',
    '[2029-06-05T09:00:00Z] 陈宁: 完成日志迁移仍按2029-06-20，尚未批准日期调整。',
)


class ScriptedStage:
    def __init__(self, stage, *, conflict=False, missing=False, replan=False):
        self.stage, self.conflict, self.missing, self.replan = stage, conflict, missing, replan
        self.requests = []

    def complete(self, request):
        self.requests.append(request.to_dict())
        if self.stage == 'discovery':
            # This test supplies exact candidate output, not a semantic parser.
            source_index = next(i for i, text in enumerate(TEXTS) if text.split(': ', 1)[1] in request.user_input)
            text = TEXTS[source_index].split(': ', 1)[1]
            arguments = {'claims': [] if self.missing else [claim(text, '完成日志迁移', owner='林枫' if source_index < 2 else None,
                project='青竹' if source_index < 2 else None,
                deadline_text=('2029-06-24' if source_index == 1 else '2029-06-20'))]}
        else:
            marker = 'INPUT_JSON\n' if self.stage == 'planning' else 'AUTHORIZED_SNAPSHOT_JSON\n'
            payload = json.loads(request.user_input.split(marker, 1)[1])
            endpoints = payload['endpoints']
            handles = [e['handle'] for e in endpoints]
            if self.stage == 'planning':
                initial = payload['replan']['previous_plan'] is None
                if self.missing or (self.replan and initial):
                    steps = [dict(id='locate', kind='discover', question='还有哪份原始会议能确认任务身份？', handles=[],
                        source_handles=[], depends_on=[], predicate='', field='', target_kind='task',
                        missing_entities=['交付任务的已确认身份'], evidence_gap='当前候选无法确认跨消息身份。')]
                    if not initial:
                        steps = [dict(steps[0], id='clarify', kind='clarify', target_kind='',
                                      question='请提供缺失的任务确认原文。', depends_on=['locate'])]
                else:
                    steps = [dict(id='identity', kind='relation', question='两次会议是否延续同一交付任务？',
                        handles=handles[:2], source_handles=[], depends_on=[], predicate='SAME_TASK',
                        field='', target_kind='', missing_entities=[])]
                    steps.append(dict(steps[0], id='revision', question='后一次会议是否修订前一计划？',
                        predicate='REVISES_PLAN', handles=list(reversed(handles[:2])), depends_on=['identity']))
                arguments = dict(steps=steps, uncertainties=['需要核对原文及语义审查。'], refusal_reasons=[])
            else:
                arguments = {'proposals': [dict(predicate=q['predicate'], subject=q['subject'], object=q['object'],
                    proposed_verdict='UNKNOWN' if self.conflict else 'SUPPORTED',
                    supporting_evidence=[dict(span_id=e['span_id'], quote=e['source']['quote']) for e in payload['allowed_evidence']],
                    counter_evidence=[], missing_evidence=['日期存在冲突'] if self.conflict else [],
                    reason_codes=['SCRIPTED_WIRING_ONLY'], changed_fields=[]) for q in payload['queries']]}
        return ModelResponse(action=ModelAction.tool(ToolCall('fixture', request.available_tools[0].tool_id, arguments)),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30))


def runtime_model(query):
    def call(request, tool):
        arguments = {'workspace_id': next(t for t in request.available_tools if t.tool_id == tool).input_schema['properties']['workspace_id']['enum'][0]}
        if tool == 'analyze_task_semantics':
            arguments['investigation_id'] = 'product-investigation'
        return ModelAction.tool(ToolCall(tool, tool, arguments))
    def final(request):
        results = [h.tool_result for h in request.tool_history if h.tool_result]
        results.append(request.observation)
        by_tool = {r.tool_id: r.call_id for r in results}
        keys = {'context_call_id': 'inspect_task_context', 'relations_call_id': 'query_task_relations'}
        if 'analyze_task_semantics' in by_tool:
            keys['semantic_call_id'] = 'analyze_task_semantics'
        return ModelAction.final(json.dumps({k: by_tool[t] for k, t in keys.items()}))
    def third(request):
        if any(t.tool_id == 'analyze_task_semantics' for t in request.available_tools):
            return call(request, 'analyze_task_semantics')
        return final(request)
    return FakeModelAdapter([lambda r: call(r, 'inspect_task_context'),
        lambda r: call(r, 'query_task_relations'), third, final])


def product_case(root, *, conflict=False, missing=False, replan=False):
    app = ProductApplication(database_path=root / 'product.sqlite', extractor=None)
    workspace = app.repository.create_workspace('Synthetic M3-I sources')
    workspace_id = workspace['workspace_id']
    app.repository.select_workspace(workspace_id)
    for i, content in enumerate(TEXTS[:3 if conflict else 2]):
        source_id = 'src_' + uuid4().hex
        artifact = RawArtifact(workspace_id, source_id, 'timestamped_text', content,
                               source_uri=f'product://{source_id}')
        app.repository.create_source(workspace_id, source_id, f'meeting-{i + 1}.txt', artifact,
                                     tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        app.repository.set_ingestion_result(workspace_id, source_id, artifact.artifact_version_id,
            status='COMPLETE', candidate_count=0, review_required_count=0)
    stages = [ScriptedStage(s, conflict=conflict, missing=missing, replan=replan)
              for s in ('discovery', 'planning', 'semantics')]
    service = ProductInvestigationService(app, model_factory=runtime_model, external_provider=False,
        run_mode='FAKE_MODEL_INTEGRATION', task_models=TaskInvestigationModels(*stages))
    ask = UnifiedAskService(app, lambda: service)
    return app, service, ask, stages, workspace_id


def finish(ask, workspace_id, result):
    deadline = monotonic() + 20
    while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
        sleep(.03)
        result, status = ask.inspect(result['agent_run_id'], workspace_id)
        assert status == 200, result
    return result
