"""Gold-blind production route composition for the frozen DEV matrix.

Both routes use one injected, campaign-budgeted model configuration. Legacy uses
its existing template compiler, Runtime, grounding and Reviewer. Graph uses its
actual planner, ToolRuntime and receipt Reviewer, without invoking fallback.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import json,sqlite3

from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
from linkloom.agents.claim_grounding import assess_claim_text,ClaimSupportStatus
from linkloom.agents.model_adapter import ModelGenerationOptions
from linkloom.scanner import scan_vault
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.temporal_graph.planning import GraphQuestionPlanner
from linkloom.temporal_graph.models import GraphLimits,digest
from linkloom.semantic_ingestion.task_relation_question_models import PlanningLimits
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.ui.graph_investigation import GraphInvestigationService
from run_temporal_graph_dev import BudgetExceeded


def _empty(status,reason):return dict(status=status,reason=reason,values=[],relations=[],evidence=[])
def _label(value):return ' '.join(value.casefold().split())
def _relation(subject,kind,target):return dict(subject=_label(subject),edge_type=kind,target=_label(target))


class TemporalGraphDevRoutes:
    def __init__(self,inputs,budget,adapter_factory,output,*,preflight=True,mention_locator=None):
        self.mention_locator=mention_locator
        self.inputs=inputs;self.budget=budget;self.factory=adapter_factory;self.output=Path(output)
        inputs.verify()
        self.config=json.loads(inputs.dependencies['configuration'].read_text(encoding='utf-8-sig'))
        fixture=json.loads(inputs.dependencies['public_fixture'].read_text(encoding='utf-8-sig'))
        if fixture.get('split')!='dev':raise ValueError('DEV_ONLY')
        self.fixtures={c['case_id']:deepcopy(c) for c in fixture['cases']}
        if len(self.fixtures)!=len(fixture['cases']):raise ValueError('DUPLICATE_FIXTURE_ID')
        self.database=inputs.dependencies['confirmed_database']
        self.max_input=self.config['max_input_bytes'];self.max_output=self.config['max_output_tokens']
        self.legacy_turns=self.config['legacy_max_turns']
        if (type(self.max_input) is not int or not 8192<=self.max_input<=262144 or
            type(self.max_output) is not int or not 1<=self.max_output<=4096 or
            type(self.legacy_turns) is not int or not 1<=self.legacy_turns<=20 or
            self.config.get('retry_attempts')!=1 or self.config.get('retrieval_mode')!='bm25' or
            self.config['model_id']!=budget.price.model_id):raise ValueError('INVALID_FROZEN_CONFIGURATION')
        # One graph planning call plus the full legacy turn allowance per repeat.
        count=len(list(inputs.public_cases()))*3*(1+self.legacy_turns)
        self.upper_bound=budget.preflight(count*self.max_input,count*self.max_output) if preflight else None

    def _fixture(self,public):
        self.inputs.verify()
        if set(public)-{'question','fixture_id','workspace_id','as_of'}:raise ValueError('PUBLIC_FIELDS_ONLY')
        fixture=self.fixtures[public['fixture_id']]
        original_workspace=public.get('workspace_id') or fixture['workspace_id']
        docs=[d for d in fixture['documents'] if d['workspace_id']==original_workspace]
        if not docs:raise ValueError('FIXTURE_WORKSPACE_REQUIRED')
        name='模拟人工确认 '+fixture['case_id']+' '+original_workspace
        with sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True) as db:
            rows=db.execute('SELECT workspace_id FROM ll_workspace WHERE workspace_id=?',(fixture['confirmed_workspace_id'],)).fetchall() if fixture.get('confirmed_workspace_id') else db.execute('SELECT workspace_id FROM ll_workspace WHERE name=?',(name,)).fetchall()
        if len(rows)!=1:raise ValueError('UNIQUE_CONFIRMED_WORKSPACE_REQUIRED')
        return fixture,original_workspace,rows[0][0],docs

    def _adapter(self,route='graph_only'):
        options=dict(max_input_bytes=self.max_input,max_output_tokens=self.max_output)
        token_cap=self.config.get('input_token_caps',{}).get(route)
        if token_cap is not None:
            from deepseek_eval_transport import OfficialTokenCounter
            options.update(max_input_tokens=token_cap,token_counter=OfficialTokenCounter(self.config['tokenizer_directory']))
        return self.budget.wrap(self.factory(self.config),**options)

    def _check_budget(self,before):
        self.inputs.verify()
        # Planner/Runtime intentionally convert provider exceptions into typed
        # refusals. Restore campaign exhaustion so the matrix cannot hide it.
        if self.budget.stopped or self.budget.exhaustions>before:raise BudgetExceeded('CAMPAIGN_STOPPED_OR_EXHAUSTED')

    def graph_only(self,public):
        _,_,workspace,_=self._fixture(public);before=self.budget.exhaustions
        from tests.meeting_graph_eval_support import task_candidate_source
        store=SQLiteGraphStore(self.database,authorized_workspace=workspace,authorize_source=lambda ws,s,v:ws==workspace,task_candidate_source=task_candidate_source() if self.config.get('task_candidate_format')=='graph-dev-given-task/v1' else None,mention_locator=self.mention_locator)
        def factory(context):
            adapter=self._adapter();guard='frozen-dev-graph'
            return GraphQuestionPlanner(adapter,model_id=self.config['model_id'],guard_id=guard,
                request_guard=lambda request:ProviderRequestAuthorization.approve(request,guard_id=guard,
                  reservation_id=digest(request.to_dict()),estimated_cost_upper_bound_usd=0),
                alignment_review=self.config.get("graph_alignment_review",False),
                grounding_required=self.config.get("graph_grounding_required",False),
                mention_grounding=self.config.get("graph_mention_grounding",False),
                limits=PlanningLimits(max_provider_calls=self.config.get("graph_max_provider_calls",1),max_output_tokens=self.max_output,
                    max_input_tokens=min(self.max_input,30720),max_total_input_tokens=min(self.max_input,30720)*self.config.get("graph_max_provider_calls",1),
                    max_total_output_tokens=self.max_output*self.config.get("graph_max_provider_calls",1)))
        from datetime import UTC,datetime
        fixed=self.config.get('query_time_fixed')
        clock=(lambda:datetime.fromisoformat(fixed+'T00:00:00+00:00')) if fixed else None
        worker=GraphInvestigationService(lambda ws:store,factory,limits=GraphLimits(**self.config.get('graph_limits',{})),query_clock=clock)
        result=worker.run(public['question'],workspace,as_of=public.get('as_of'))
        self._check_budget(before)
        if 'source_investigation' not in result:return _empty('PLANNING_FAILED',result.get('reason',result['status']))
        source=result['source_investigation'];payload=source['graph_query']
        values=[_label(v['label']) if isinstance(v,dict) and 'label' in v else _label(v) for row in payload['outputs'] for v in row['values'] if isinstance(v,str) or isinstance(v,dict) and 'label' in v]
        relations=[_relation(f['subject'],f['predicate'],f['value']) for f in source['facts']]
        # Source paths, not generated evidence IDs, are the stable public fixture
        # names in human expected files. Actual exact quote hashes stay in output.
        refs={e['evidence_ref']:e for e in result['evidence']};evidence=[]
        with sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True) as db:
            for item in refs.values():
                row=db.execute('SELECT name FROM ll_source WHERE workspace_id=? AND source_id=?',(workspace,item['source_id'])).fetchone()
                if row:evidence.append(row[0])
        return dict(status=payload['status'],values=values,relations=relations,evidence=sorted(set(evidence)),
                    quote_hashes=sorted(e['quote_sha256'] for e in refs.values()),verified_by='graph-receipt-reviewer',
                    structured_outputs=payload['outputs'],result_steps=payload['steps'],answer=source['answer'],facts=source['facts'])

    def legacy_only(self,public):
        self.inputs.verify();fixture=self.fixtures[public['fixture_id']]
        original_workspace=public.get('workspace_id') or fixture['workspace_id']
        documents=[d for d in fixture['documents'] if d['workspace_id']==original_workspace];before=self.budget.exhaustions
        question=public['question']
        if public.get('as_of'):
            from linkloom.ui.scope_discovery import resolve_query_date
            _,error=resolve_query_date(question,public['as_of'])
            if error:return _empty('NOT_APPLICABLE',error['code'])
            plan_with_date=plan_relation_investigation(question)
            already_bound=plan_with_date is not None and any('As of '+public['as_of'] in g.question for g in plan_with_date.subgoals)
            if not already_bound:question='As of '+public['as_of']+', '+question
        plan=plan_relation_investigation(question) or plan_team_decision_query(question)
        if plan is None:return _empty('NOT_APPLICABLE','LEGACY_TEMPLATE_MISS')
        root=self.output/('legacy-'+uuid4().hex);vault=root/'vault';vault.mkdir(parents=True)
        for doc in documents:
            name=Path(doc['path'])
            if name.name!=doc['path'] or name.suffix!='.md':raise ValueError('RELATIVE_MARKDOWN_NAME_REQUIRED')
            (vault/name).write_text(doc['content'],encoding='utf8')
        index=scan_vault(vault,root/'scan').index_path;budgeted=self._adapter('legacy_only')
        # Legacy Runtime leaves Provider defaults unset. Pin them at this Host
        # seam so every actual request has the frozen model/output reservation.
        config=self.config
        class BoundedLegacy:
            capability=budgeted.capability
            def complete(self,request):
                options=replace(request.generation_options,max_output_tokens=config['max_output_tokens'],temperature=config.get('temperature',0))
                return budgeted.complete(replace(request,model_id=config['model_id'],generation_options=options))
        adapter=BoundedLegacy()
        engine=RuntimeEngine(vault_root=vault,index_path=index,checkpoint_dir=root/'runtime',trace_dir=root/'traces',
            checkpointer=InMemoryCheckpointer(),model=adapter)
        state_id='dev-'+uuid4().hex
        status=engine.start_multi_agent(RunRequest(request_id=state_id,thread_id=state_id,workflow='team_decision',
            query=question,task_plan=plan.to_dict(),max_steps=self.legacy_turns,max_provider_requests=self.legacy_turns,dry_run=True))
        self._check_budget(before)
        state=engine.checkpointer.get_latest(state_id)
        final_path=root/'runtime'/'results'/status.run_id/'result.json'
        artifact=json.loads(final_path.read_text(encoding='utf8')) if final_path.exists() else {}
        if (artifact.get('review') or {}).get('decision')!='evidence_sufficient':return _empty('RUNTIME_FAILURE','LEGACY_FINAL_UNVERIFIED')
        final=(artifact.get('result') or {}).get('team_decision')
        if not final:return _empty('RUNTIME_FAILURE','LEGACY_FINAL_MISSING')
        by_ref={}
        for record in state.tool_ledger:
            if record.tool_id=='read_verified_note' and record.status=='completed' and record.result.get('status')=='ok':
                item=record.result['value'];by_ref[item['evidence_id']]=item
        texts=[final['decision']['value']] if final['decision']['value'] else [r['point'] for r in final['rationale']]
        relations=[];values=[]
        for text in texts:
            assessed=assess_claim_text(text,final['evidence_refs'],by_ref)
            if assessed.status!=ClaimSupportStatus.SUPPORTED:return _empty('RUNTIME_FAILURE','LEGACY_GROUNDING_FAILED')
            for claim in assessed.claims:
                relation=claim.relation;subject=claim.subject;target=claim.value
                if relation=='owner_of':subject,target=target,subject
                elif relation=='superseded_by':subject,target=target,subject;relation='supersedes'
                elif relation=='direct_leader_of':relation='reports_to'
                if relation in {'owner_of','supersedes','reports_to','alias_of'}:relations.append(_relation(subject,relation,target))
                values.append(_label(claim.value))
        found=bool(final['decision']['value'])
        reason=final['uncertainty']['status']
        return dict(status='FOUND' if found else 'CONFLICT' if reason=='conflicting_evidence' else 'PARTIAL' if values else 'UNKNOWN',
            values=values,relations=relations,evidence=sorted({by_ref[r]['relative_path'] for r in final['evidence_refs'] if r in by_ref}),
            quote_hashes=sorted({by_ref[r]['quote_sha256'] for r in final['evidence_refs'] if r in by_ref}),verified_by='legacy-runtime-reviewer',
            outcome='complete' if found else 'partial' if values else 'refused',reason=reason,answer=final['decision']['value'])

    def routes(self):return dict(legacy_only=self.legacy_only,graph_only=self.graph_only)
