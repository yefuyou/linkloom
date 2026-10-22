import json, os, sys
from dataclasses import replace
from pathlib import Path
sys.path[:0]=['src','.']
from tests.task_semantic_support import context_for
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.semantic_ingestion.task_relation_models import ChangeRef
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_semantic_proposer import SemanticProposer
from linkloom.semantic_ingestion.task_relation_semantic_validation import context_errors, validate_proposals
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession
sys.stdout.reconfigure(encoding='utf-8')
root=Path('work/m3r-independent-live-20261022')
original=json.loads((root/'indirect-dependency.json').read_text(encoding='utf8'))
assert original['provider_calls']==0 and original['errors']==['CHANGE_FIELDS_NOT_IN_SOURCE']
budget=json.loads((root/'provider/budget.json').read_text(encoding='utf8'))
assert budget['requests_reserved']==6 and budget['cost_reserved_usd']==1.5 and not budget['failure']
out=root/'indirect-binding-correction'
assert not (out/'provider/budget.json').exists(), 'no automatic repeat'
out.mkdir(exist_ok=True)
case=next(c for c in json.loads(Path('docs/requirements/m3-r-llm-first-semantic-closure/evidence/new-cases.json').read_text(encoding='utf8'))['cases'] if c['case_id']=='indirect-dependency')
binding=dict(original_project='文档发布',corrected_scope='认证组件',reason='Harness scope was absent from source; bind to the exact named component already present. No source, expected verdict or semantic prompt changed.',expected=case['expected'],original_provider_calls=0)
(out/'binding-before-call.json').write_text(json.dumps(binding,ensure_ascii=False,indent=2),encoding='utf8')
sources=[dict(id=n,artifact_id=n,source_type='timestamped_text',content=f'[2030-01-0{i+1}T09:00:00+08:00] 唐予: '+case[f]+'\n') for i,(n,f) in enumerate([('before.txt','before'),('after.txt','after')])]
tasks=[dict(handle=h,source=s,ordinal=0,project=None,deadline_text=None) for h,s in [('old','before.txt'),('new','after.txt')]]
change=dict(handle='change',source='after.txt',ordinal=0,project='认证组件',before='本地票据',after='单点登录')
ctx=context_for(dict(workspace_id='m3r-indirect-dependency',question='根据全部授权原文判断所请求关系，保留正反证和不确定性，不赋予业务批准。',sources=sources,tasks=tasks,projects=[],changes=[change],queries=case['queries']))
assert not context_errors(ctx)
sdk=build_gemini_sdk_client(os.environ['GEMINI_API_KEY'],http_options_kwargs={'timeout':30000,'retry_options':{'attempts':1}})
class Bridge:
    def generate_content(self,*,model,contents,config):return sdk.models.generate_content(model=model,contents=contents,config=config)
session=TaskProviderSession(out/'provider',TaskProviderLimits(1,40000,8192,.25,40,30,.25),lambda:None)
proposer=SemanticProposer(session.bind(GeminiProviderAdapter(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID),'independent-semantics'),model_id=GEMINI_38_FLASH_MODEL_ID,
    request_guard=lambda r:ProviderRequestAuthorization.approve(r,guard_id='m3r-indirect',reservation_id='m3r-indirect',estimated_cost_upper_bound_usd=.25),guard_id='m3r-indirect')
try:
    result=proposer.propose(ctx)
    row=dict(case_id=case['case_id'],status=result.status,errors=list(result.errors),provider_calls=result.provider_calls,elapsed_ms=result.elapsed_ms,
        provider_response_id=result.provider_response_id,usage=result.usage.to_dict(),expected=case['expected'],
        verdicts=[p.proposed_verdict for p in result.proposals],proposals=[p.to_dict() for p in result.proposals],validation=[v.to_dict() for v in validate_proposals(result,ctx)])
    (out/'result.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:row[k] for k in ['status','errors','verdicts','usage']},ensure_ascii=False))
finally:sdk.close()
