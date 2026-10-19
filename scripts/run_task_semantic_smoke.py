"""Three explicit synthetic diagnostic calls; no Gold, retries or Agent runtime.

--freeze creates the exact input/request/schema/code manifest without API use.
--execute requires that unchanged manifest; outputs are exclusive, append-only.
Scoring is a separate command/process after predictions have been written.
"""
from argparse import ArgumentParser
from dataclasses import asdict
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from scripts.check_task_semantic_provider import connect
from tests.task_semantic_support import public_cases,context_for,INPUT
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter,GEMINI_38_FLASH_MODEL_ID
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_proposer import SemanticProposer,build_request
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals,compare_proposed_revision

SELECTED=('revision','causality','conflict')


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest():
    contexts=[(c['case_id'],context_for(c)) for c in public_cases() if c['case_id'] in SELECTED]
    files=['src/linkloom/semantic_ingestion/task_relation_semantic_'+n+'.py' for n in ('models','proposer','validation')]
    files+=['tests/task_semantic_support.py','scripts/run_task_semantic_smoke.py',
        'scripts/check_task_semantic_provider.py']
    files+=['src/linkloom/'+p+'.py' for p in (
        'agents/providers/gemini_api','agents/model_adapter','semantic_ingestion/extraction',
        'semantic_ingestion/artifacts','semantic_ingestion/document_text','semantic_ingestion/task_observation',
        'semantic_ingestion/task_relation_models','semantic_ingestion/task_relation_validation',
        'semantic_ingestion/task_change_authority','decision_memory/sources','tools/contracts')]
    return dict(schema='task-semantic-freeze/v1',model_id=GEMINI_38_FLASH_MODEL_ID,max_generation_attempts=3,
        scope='AUTHOR_VISIBLE_SYNTHETIC_FIXED_ENDPOINT_DIAGNOSTIC',selected=list(SELECTED),
        git_base=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        input_sha256=file_hash(INPUT),implementation_files={p:file_hash(ROOT/p) for p in files},
        requests=[dict(case_id=name,request_sha256=canonical_hash(build_request(c,GEMINI_38_FLASH_MODEL_ID).to_dict()),
            snapshot_sha256=c.source_snapshot_hash,
            schema_sha256=canonical_hash(build_request(c,GEMINI_38_FLASH_MODEL_ID).available_tools[0].input_schema))
            for name,c in contexts],
        prices=dict(input_usd_per_million=.75,output_usd_per_million=3.75,
            source='https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8',date='2026-10-18'),
        budget_authorization='User: 没有上限; this run voluntarily capped at three serial attempts, no retries.')


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.write('\n')


class MeteredClient:
    """Injection around the existing SDK, never another transport/client."""
    def __init__(self,models,output):
        self.models=models
        self.calls=[]
        self.output=output

    def generate_content(self,**kwargs):
        if len(self.calls)>=3:
            raise RuntimeError('THREE_ATTEMPT_LIMIT')
        row=dict(attempt=len(self.calls)+1,status='ATTEMPTED',started_utc=datetime.now(UTC).isoformat(),
            model_requested=kwargs.get('model'),usage=None,cost_estimate_usd=None)
        self.calls.append(row)
        # Exclusive durable attempt receipt exists before the network request.
        save(self.output/('attempt-'+str(len(self.calls))+'.json'),dict(row))
        start=time.monotonic()
        try:
            response=self.models.generate_content(**kwargs)
            usage=response.usage_metadata
            names=('prompt_token_count','candidates_token_count','thoughts_token_count','total_token_count','cached_content_token_count')
            values={n:getattr(usage,n,None) for n in names} if usage else None
            row.update(status='RETURNED',usage=values,actual_model_version=getattr(response,'model_version',None),
                provider_response_id=getattr(response,'response_id',None))
            if values and all(values[n] is not None for n in ('prompt_token_count','candidates_token_count','thoughts_token_count')):
                thoughts=values['thoughts_token_count']
                row['cost_estimate_usd']=(values['prompt_token_count']*.75+
                    (values['candidates_token_count']+thoughts)*3.75)/1000000
            return response
        except Exception as exc:
            row.update(status='FAILED',error_type=type(exc).__name__)
            raise
        finally:
            row['latency_ms']=round((time.monotonic()-start)*1000,3)
            save(self.output/('receipt-'+str(len(self.calls))+'.json'),row)


def execute(frozen,output):
    if frozen!=manifest():
        raise RuntimeError('FROZEN_MANIFEST_MISMATCH')
    # Exclusive run directory prevents accidental repeat of the fixed smoke.
    output.mkdir(parents=True,exist_ok=False)
    client=connect()
    meter=MeteredClient(client.models,output)
    adapter=GeminiProviderAdapter(meter)
    cases=[]
    try:
        for case in public_cases():
            if case['case_id'] not in SELECTED:
                cases.append(dict(case_id=case['case_id'],result=dict(status='NOT_RUN',proposals=[]),validation=[],comparisons=[]))
                continue
            context=context_for(case)
            def guard(request):
                expected=next(r for r in frozen['requests'] if r['case_id']==case['case_id'])
                if canonical_hash(request.to_dict())!=expected['request_sha256']:
                    raise RuntimeError('REQUEST_CHANGED')
                # Worst-case admitted input/output under this small fixed request.
                upper=(context.limits.max_input_tokens*.75+context.limits.max_output_tokens*3.75)/1000000
                return ProviderRequestAuthorization.approve(request,guard_id='m3-semantic-smoke-v1',
                    reservation_id='fixed-three-'+case['case_id'],estimated_cost_upper_bound_usd=upper)
            proposer=SemanticProposer(adapter,model_id=GEMINI_38_FLASH_MODEL_ID,request_guard=guard,guard_id='m3-semantic-smoke-v1')
            result=proposer.propose(context)
            row=dict(case_id=case['case_id'],result=result.to_dict(),
                validation=[r.to_dict() for r in validate_proposals(result,context)],
                comparisons=[compare_proposed_revision(p,result,context) for p in result.proposals if p.predicate=='REVISES_PLAN'])
            cases.append(row)
            save(output/(case['case_id']+'.json'),row)
            print(json.dumps(dict(case_id=case['case_id'],status=result.status,proposals=len(result.proposals),
                evidence=[r['stage'] for r in row['validation']]),ensure_ascii=False),flush=True)
    finally:
        client.close()
        cost=sum(r['cost_estimate_usd'] for r in meter.calls) if all(r['cost_estimate_usd'] is not None for r in meter.calls) else None
        report=dict(schema='task-semantic-predictions/v1',execution_kind='REAL_PROVIDER',model_id=GEMINI_38_FLASH_MODEL_ID,
            frozen_manifest_sha256=canonical_hash(frozen),cases=cases,provider_calls=len(meter.calls),
            provider_receipts=meter.calls,cost_estimate_usd=cost,not_invoice=True,
            semantic_acceptance='NOT_EVALUATED',production_agent_adapter='NOT_RUN')
        save(output/'predictions.json',report)


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',type=Path)
    parser.add_argument('--execute',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if bool(args.freeze)==bool(args.execute):
        parser.error('choose freeze or execute')
    if args.freeze:
        save(args.freeze,manifest())
        print('FROZEN; generation calls=0')
    else:
        if not args.output:
            parser.error('--output required')
        execute(json.loads(args.execute.read_text(encoding='utf8')),args.output)


if __name__=='__main__':
    main()
