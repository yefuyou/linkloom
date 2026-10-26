import json, os, sys
from pathlib import Path
from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.tools.contracts import ToolCall
from linkloom.ui.offline_task_demo import _claim, _runtime_model
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_investigation import TaskInvestigationModels
from linkloom.ui.server import create_http_server
from linkloom.ui.demo import DemoRunBackend, DemoCase

# Only the model transport is scripted. Sources/workspaces are created by the
# browser through Product forms. No TaskRelationStore or fixture proof setup.
class SourceStage:
 def __init__(self, app, stage): self.app,self.stage=app,stage
 def complete(self, request):
  if self.stage=='discovery':
   _, sources=self.app.snapshot_investigation_sources(self.app._workspace_id())
   source=next(s for s in sources if s['content'].split(': ',1)[1].strip() in request.user_input)
   text=source['content'].split(': ',1)[1].strip()
   arguments={'claims':[_claim(text,text,owner='陈晨',project='星河项目',deadline_text='2026年10月18日' if '改为2026年10月18日' in text else '2026年10月14日')]}
  elif self.stage=='planning':
   payload=json.loads(request.user_input.split('INPUT_JSON\n',1)[1])
   endpoints=payload['endpoints']; handles=[e['handle'] for e in endpoints]
   # F preserves source order A then B. Explicit new -> old order for P3.
   newold=list(reversed(handles[:2]))
   template=dict(question='核对任务身份、计划修订与日期边界',handles=newold,source_handles=[],field='',target_kind='',missing_entities=[])
   arguments=dict(steps=[dict(template,id='identity',kind='relation',predicate='SAME_TASK',depends_on=[]),dict(template,id='revision',kind='relation',predicate='REVISES_PLAN',depends_on=['identity']),dict(template,id='dates',kind='compare_dates',predicate='',depends_on=['identity','revision'])],uncertainties=[],refusal_reasons=[])
  else:
   data=json.loads(request.user_input.split('AUTHORIZED_SNAPSHOT_JSON\n',1)[1])
   arguments={'proposals':[dict(predicate=q['predicate'],subject=q['subject'],object=q['object'],proposed_verdict='SUPPORTED',supporting_evidence=[dict(span_id=e['span_id'],quote=e['source']['quote']) for e in data['allowed_evidence']],counter_evidence=[],missing_evidence=['Offline model proposal only'],reason_codes=['SCRIPTED_PUBLIC_SOURCE_INPUT'],changed_fields=[]) for q in data['queries']]}
  return ModelResponse(action=ModelAction.tool(ToolCall('offline-review-transport',request.available_tools[0].tool_id,arguments)),usage=ModelUsage(input_tokens=20,output_tokens=10,total_tokens=30))

if __name__=='__main__':
 database=Path(sys.argv[1]);port=int(sys.argv[2]);database.parent.mkdir(parents=True,exist_ok=True)
 app=ProductApplication(database_path=database,extractor=None)
 models=TaskInvestigationModels(*[SourceStage(app,s) for s in ['discovery','planning','semantics']])
 service=ProductInvestigationService(app,model_factory=_runtime_model,task_models=models,external_provider=False,run_mode='OFFLINE_REVIEW_ACCEPTANCE',task_only=True)
 server=create_http_server(DemoRunBackend(DemoCase.load_mps_001()),product_app=app,investigation_service=service,allow_local_task_review=True,port=port)
 print(f'OFFLINE SOURCE TRANSPORT ONLY; no preseeded P4 proof; Product UI http://127.0.0.1:{port}',flush=True)
 try:server.serve_forever()
 finally:server.server_close();app.close()

