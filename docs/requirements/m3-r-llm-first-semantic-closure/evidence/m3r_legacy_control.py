import json,sys
from pathlib import Path
sys.path[:0]=['src','.']
from tests.task_semantic_support import context_for
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote
cases=json.loads(Path('docs/requirements/m3-r-llm-first-semantic-closure/evidence/new-cases.json').read_text(encoding='utf8'))['cases']
rows=[]
for c in cases:
    if c.get('change') or 'reuse_context' in c:continue
    sources=[dict(id=n,artifact_id=n,source_type='timestamped_text',content=f'[2030-01-0{i+1}T09:00:00+08:00] 唐予: '+c[f]+'\n') for i,(n,f) in enumerate([('before.txt','before'),('after.txt','after'),('counter.txt','extra')]) if f in c]
    ctx=context_for(dict(workspace_id='m3r-'+c['case_id'],question='非付费旧架构对照',sources=sources,projects=[],changes=[],queries=c['queries'],
        tasks=[dict(handle=h,source=s,ordinal=0,project=None,deadline_text=None) for h,s in [('old','before.txt'),('new','after.txt')]]))
    evidence=tuple(EvidenceQuote(s,s.char_start,s.char_end,s.quote,'RELATION_PROPOSAL') for _,s in ctx.allowed_spans)
    args=dict(artifacts=ctx.artifacts,source_registry=ctx.source_registry,context_observations=ctx.observations)
    identity=assess_same_task(ctx.observations[1],ctx.observations[0],**args)
    row=dict(case_id=c['case_id'],old_same_task=identity.verdict,old_same_task_reasons=list(identity.reason_codes))
    if any(q[0]=='REVISES_PLAN' for q in c['queries']):
        revision=assess_plan_revision(ctx.observations[1],ctx.observations[0],same_task=identity,evidence=evidence,**args)
        row.update(old_revision=revision.verdict,old_revision_reasons=list(revision.reason_codes))
    real=json.loads(Path('work/m3r-independent-live-20261022',c['case_id']+'.json').read_text(encoding='utf8'))
    row['new_model_verdicts']=real['verdicts'];row['new_evidence_validation']=[v['stage'] for v in real['validation']]
    rows.append(row)
Path('work/m3r-independent-live-20261022/legacy-control.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(rows,ensure_ascii=True,indent=2))
