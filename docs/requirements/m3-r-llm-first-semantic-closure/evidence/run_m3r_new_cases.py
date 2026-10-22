import json, os, sys, hashlib
from dataclasses import replace
from pathlib import Path
from datetime import datetime, UTC
sys.path[:0] = ['src', '.']
from tests.task_semantic_support import context_for
from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client, GEMINI_38_FLASH_MODEL_ID
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_semantic_proposer import SemanticProposer
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession

sys.stdout.reconfigure(encoding='utf-8')
source = Path('docs/requirements/m3-r-llm-first-semantic-closure/evidence/new-cases.json')
raw = source.read_bytes()
cases = json.loads(raw)['cases']
out = Path('work/m3r-independent-live-20261022')
if (out / 'provider/budget.json').exists():
    raise SystemExit('EXISTING_RECEIPTS: no automatic rerun')
out.mkdir(parents=True, exist_ok=True)
key = os.environ.get('GEMINI_API_KEY')
if not key:
    raise SystemExit('GEMINI_API_KEY_UNSET')
if os.environ.get('GOOGLE_GENAI_USE_VERTEXAI', '').lower() in {'1','true'}:
    raise SystemExit('UNEXPECTED_PROVIDER_IDENTITY')
baseline_reserved = 12.18  # rounded up: reported M3-Q ~11.87 + M3-R journal 0.300037
maximum_new = 1.75
assert baseline_reserved + maximum_new <= 50
global_receipt = dict(schema='m3r-global-budget/v1', started_at=datetime.now(UTC).isoformat(),
    previous_requests=40, previous_cost_reserved_usd=baseline_reserved,
    new_request_limit=7, new_cost_limit_usd=maximum_new, global_limit_usd=50,
    fixture_sha256=hashlib.sha256(raw).hexdigest(), actual_bill_usd=None,
    billing_identity='Same local Gemini Developer API key used by authorized M3-Q/M3-R; project and billed amount are not exposed by generation response',
    no_automatic_retry=True)
with (out/'global-reservation.json').open('w',encoding='utf8') as f:
    json.dump(global_receipt,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
sdk=build_gemini_sdk_client(key,http_options_kwargs={'timeout':30000,'retry_options':{'attempts':1}})
class Bridge:
    def generate_content(self, *, model, contents, config):
        return sdk.models.generate_content(model=model,contents=contents,config=config)
adapter=GeminiProviderAdapter(Bridge(),model_id=GEMINI_38_FLASH_MODEL_ID)
limits=TaskProviderLimits(7,250000,57344,maximum_new,240,30,.25)
session=TaskProviderSession(out/'provider',limits,lambda: None)
def guard(request):
    return ProviderRequestAuthorization.approve(request,guard_id='m3r-independent',
        reservation_id='m3r-'+canonical_hash(request.to_dict()),estimated_cost_upper_bound_usd=.25)
proposer=SemanticProposer(session.bind(adapter,'independent-semantics'),model_id=GEMINI_38_FLASH_MODEL_ID,
    request_guard=guard,guard_id='m3r-independent')
def context(case):
    sources=[]
    for i,(name,field) in enumerate([('before.txt','before'),('after.txt','after'),('counter.txt','extra')]):
        if field in case:
            sources.append(dict(id=name,artifact_id=name,source_type='timestamped_text',
                content=f'[2030-01-0{i+1}T09:00:00+08:00] 唐予: '+case[field]+'\n'))
    tasks=[dict(handle='old',source='before.txt',ordinal=0,project=None,deadline_text=None),
           dict(handle='new',source='after.txt',ordinal=0,project=None,deadline_text=None)]
    changes=[]
    if case.get('change'):
        changes=[dict(handle='change',source='after.txt',ordinal=0,**case['change'])]
    return context_for(dict(case_id=case['case_id'],workspace_id='m3r-'+case['case_id'],
        question='根据全部授权原文判断所请求关系，保留正反证和不确定性，不赋予业务批准。',
        sources=sources,tasks=tasks,projects=[],changes=changes,queries=case['queries']))
rows=[]; first=None
try:
    for case in cases:
        if 'reuse_context' in case:
            if first:
                ctx,result=first
                revoked=replace(ctx,revoked_observation_ids=frozenset([ctx.observations[0].observation_id]))
                count=session.requests
                blocked=proposer.propose(revoked)
                reports=validate_proposals(result,revoked)
                span=ctx.observations[0].provenance
                ctx.source_registry.register_evidence(ctx.workspace_id,span.evidence_ref,
                    content_hash='0'*64,source_version='new-version')
                stale=validate_proposals(result,ctx)
                row=dict(case_id=case['case_id'],provider_calls=session.requests-count,
                    admission_status=blocked.status,admission_errors=list(blocked.errors),
                    revoked_evidence=[r.stage for r in reports],stale_evidence=[r.stage for r in stale])
                row['passed']=blocked.status=='BLOCKED' and session.requests==count and all(r.stage=='EVIDENCE_INVALID' for r in (*reports,*stale))
                rows.append(row)
            continue
        ctx=context(case)
        result=proposer.propose(ctx)
        if first is None and result.status=='EXECUTED': first=(ctx,result)
        row=dict(case_id=case['case_id'],expected=case['expected'],status=result.status,
            errors=list(result.errors),provider_calls=result.provider_calls,elapsed_ms=result.elapsed_ms,
            provider_response_id=result.provider_response_id,usage=result.usage.to_dict(),
            proposals=[p.to_dict() for p in result.proposals],
            validation=[r.to_dict() for r in validate_proposals(result,ctx)],
            public_output=result.untrusted_output)
        row['verdicts']=[p.proposed_verdict for p in result.proposals]
        row['matches_fixed_expectation']=row['verdicts']==case['expected']
        rows.append(row)
        (out/(case['case_id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({k:row[k] for k in ['case_id','status','verdicts','matches_fixed_expectation']},ensure_ascii=False),flush=True)
        (out/'summary.json').write_text(json.dumps(dict(cases=rows,fixture_sha256=global_receipt['fixture_sha256'],
            model=GEMINI_38_FLASH_MODEL_ID,sdk='google-genai 2.22.0',thinking_level='low',
            evaluation='SMALL_SYNTHETIC_SEMANTIC_ONLY; NO_PRODUCT_OR_P4_SCORE'),ensure_ascii=False,indent=2),encoding='utf8')
        if session.failure:
            print('STOP:'+session.failure,flush=True); break
finally:
    (out/'summary.json').write_text(json.dumps(dict(cases=rows,fixture_sha256=global_receipt['fixture_sha256'],
        model=GEMINI_38_FLASH_MODEL_ID,sdk='google-genai 2.22.0',thinking_level='low',
        evaluation='SMALL_SYNTHETIC_SEMANTIC_ONLY; NO_PRODUCT_OR_P4_SCORE'),ensure_ascii=False,indent=2),encoding='utf8')
    sdk.close()
