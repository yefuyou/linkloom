"""Host graph worker: model planning, read-only tool, receipt review, no graph cache."""
from datetime import UTC,datetime
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from copy import deepcopy
from threading import Lock
from pathlib import Path
from uuid import uuid4
from linkloom.temporal_graph.models import EDGE_TYPES,GraphLimits,digest
from linkloom.temporal_graph.planning import GraphPlanningContext,GraphQuestionPlanner,ALIGNMENT_REASONS
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.temporal_graph.wire import unpack_graph_result
from linkloom.runtime.planner_metrics import PlannerMetrics
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.tools.contracts import ToolCall
from linkloom.tools.registry import ToolRegistry
from linkloom.tools.runtime import ToolRuntime
from linkloom.tools.graph import register_graph_query
from linkloom.tools.tool_policy import ToolCallPolicy,ToolPolicyEnforcer,P4_TOOL_POLICY_VERSION
from linkloom.agents.graph_result import review_graph_result,project_graph_result
from linkloom.agents.reviewer_agent import ReviewerAgent


_GRAPH_PROVIDER=ContextVar('graph_request_provider',default=None)


@contextmanager
def graph_request_budget():
    # Thread/task-local; a different Ask must never inherit this reservation.
    token=_GRAPH_PROVIDER.set(None)
    try:yield
    finally:_GRAPH_PROVIDER.reset(token)


def current_graph_provider_session():return _GRAPH_PROVIDER.get()


def bind_graph_fallback(adapter,stage):
    session=current_graph_provider_session()
    if session is None:return adapter
    from linkloom.agents.model_adapter import ModelGenerationOptions
    class BoundedLegacyAdapter:
        capability=getattr(adapter,'capability',None)
        def complete(self,request):
            if request.generation_options.max_output_tokens is None:
                options=replace(request.generation_options,max_output_tokens=min(4096,session.limits.max_output_tokens))
                request=replace(request,generation_options=options)
            return session.complete(adapter,stage,request)
    return BoundedLegacyAdapter()


def _policy(agent,tools,max_calls):
    return ToolPolicyEnforcer(ToolCallPolicy(P4_TOOL_POLICY_VERSION,agent,list(tools),[],max_calls,'deny','deny','deny'))


class GraphInvestigationService:
    def __init__(self,store_factory,planner_factory,*,external_provider=False,limits=None,metrics=None,query_clock=None):
        self.store_factory=store_factory;self.planner_factory=planner_factory;self.external_provider=external_provider
        self.limits=limits or GraphLimits();self.metrics=metrics or PlannerMetrics()
        self.query_clock=query_clock or (lambda:datetime.now(UTC))
        self._runs={};self._lock=Lock()

    def _fallback(self,reason):
        # Finite codes only, no source/question content is retained as a label.
        self.metrics.record('legacy_fallback',reason)
        return dict(status='FALLBACK',reason=reason)

    def run(self,question,workspace,*,as_of=None,allow_external_provider=False):
        if self.external_provider and not allow_external_provider:
            return dict(status='UNAUTHORIZED',reason='EXTERNAL_PROVIDER_PERMISSION_REQUIRED')
        try:
            planning_now=self.query_clock()
            if not isinstance(planning_now,datetime) or planning_now.utcoffset() is None:raise ValueError('HOST_QUERY_TIME_REQUIRED')
            store=self.store_factory(workspace); snapshot=store.load_edges(workspace,None,EDGE_TYPES)
            if not snapshot.nodes:return self._fallback('GRAPH_NOT_READY')
            def snapshot_signature(value):return digest(dict(edges=[e.to_dict() for e in value.edges],
                nodes=[n.to_dict() for n in value.nodes],records=list(value.records)))
            signature=snapshot_signature(snapshot)
            def validate():
                fresh=store.load_edges(workspace,None,EDGE_TYPES)
                return snapshot_signature(fresh)==signature
            metadata=[]
            for record in snapshot.records:
                item={k:record[k] for k in ('node_id','meeting_date','meeting_time','subject_key','valid_from','valid_to','status') if k in record}
                item['entity_label']=record.get('title') or record.get('value') or next((n.label for n in snapshot.nodes if n.node_id==record['node_id']),'')
                parent=record.get('source_decision') or {}
                item['grounding_labels']=[parent['value']] if parent.get('value') else []
                metadata.append(item)
            context=GraphPlanningContext(question,workspace,snapshot.nodes,validate,as_of,self.limits,tuple(metadata),query_time=planning_now.isoformat())
            planner=self.planner_factory(context)
            if planner is None:return self._fallback('GRAPH_PLANNER_NOT_CONFIGURED')
            close=None
            if isinstance(planner,tuple):planner,close=planner
            self.metrics.record('graph_planning_attempt')
            try: planning=planner.plan(context)
            finally:
                if close:close()
            if planning['status']!='PLANNED':
                safety_failures={'CONTEXT_NOT_CURRENTLY_AUTHORIZED','FOREIGN_WORKSPACE_NODE',
                    'EXACT_REQUEST_NOT_AUTHORIZED','CONTEXT_CHANGED_BEFORE_CALL','CONTEXT_CHANGED_DURING_CALL',
                    'REQUEST_GUARD_FAILED','SOURCE_CHANGED'}
                if planning.get('reason') in safety_failures:
                    return dict(status='UNAUTHORIZED',reason='GRAPH_AUTHORIZATION_NOT_CURRENT')
                if planning['status']=='REFUSED':return dict(status='REFUSED',reason='GRAPH_PLANNER_REFUSED')
                intent_failures={'PLAN_INTENT_NOT_ALIGNED:'+code for code in ALIGNMENT_REASONS if code!='ALIGNED'}
                if planning['status']=='OUTPUT_INVALID' and planning.get('reason') in intent_failures:return self._fallback('GRAPH_PLAN_INTENT_REJECTED')
                return self._fallback('GRAPH_PLANNING_'+planning['status'])
            self.metrics.record('graph_planning_success')
            plan=planning['plan']; run_id='graph-'+uuid4().hex; task_id='graph-task-'+uuid4().hex
            registry=ToolRegistry(); now=planning_now
            if not isinstance(now,datetime) or now.utcoffset() is None:raise ValueError('HOST_QUERY_TIME_REQUIRED')
            register_graph_query(registry,store,workspace,limits=self.limits,query_time=now)
            runtime=ToolRuntime(registry)
            call=ToolCall(call_id='query-'+uuid4().hex,tool_id='graph_query',run_id=run_id,task_id=task_id,
                agent_id='graph-worker',sequence=1,arguments=dict(workspace=workspace,as_of=plan.as_of,
                    subgoals=list(plan.steps),outputs=list(plan.outputs)))
            result=runtime.execute(call,_policy('graph-worker',('graph_query',),1))
            if result.status!='ok': return dict(status='BLOCKED',reason='GRAPH_TOOL_FAILED')
            payload=unpack_graph_result(result.value); record=runtime.ledger.get(call.call_id)
            self.metrics.record('graph_query_status',payload['status'])
            refs=sorted({q['evidence_id'] for row in payload['steps'] for q in
                (*row.get('evidence',[]),*(q for edge in row['edges'] for q in edge['evidence']))})
            review=ReviewerAgent().execute('review-'+uuid4().hex,[call.call_id],[payload],refs,
                _policy('reviewer_agent',('validate_evidence','validate_schema'),len(refs)+1),
                lambda ref:dict(status='pass' if ref in refs else 'fail'),
                lambda kind,subject:review_graph_result(subject,record,store,run_id=run_id,task_id=task_id,limits=self.limits))
            if review.decision!='evidence_sufficient':return dict(status='BLOCKED',reason='GRAPH_FINAL_NOT_VERIFIED')
            source,evidence=project_graph_result(payload)
            public_result=dict(status='COMPLETED' if payload['status']=='FOUND' else 'INSUFFICIENT',
                source_investigation=source,evidence=evidence,reviewer=dict(performed=True,accepted=True),
                uncertainty=dict(status='none' if payload['status']=='FOUND' else
                    'conflicting_evidence' if payload['status']=='CONFLICT' else 'ambiguous' if payload['status']=='AMBIGUOUS' else 'insufficient_evidence',
                    unknown_fields=[r['id'] for r in payload['steps'] if r['status']!='FOUND'],evidence_refs=refs),
                conclusions=[dict(kind='SOURCE_RELATION',**fact) for fact in source['facts']],receipts=[],
                graph_status=payload['status'],run_id=run_id)
            with self._lock:
                self._runs[run_id]=(workspace,store,record,deepcopy(payload),deepcopy(public_result),task_id)
            return public_result
        except PermissionError:
            return dict(status='UNAUTHORIZED',reason='WORKSPACE_OR_SOURCE_PERMISSION_CHANGED')
        except (ValueError,TypeError,KeyError):
            return dict(status='BLOCKED',reason='GRAPH_CONTEXT_INVALID')


    def inspect(self,run_id,workspace):
        with self._lock: saved=self._runs.get(run_id)
        if saved is None:return None
        expected_workspace,store,record,payload,public_result,task_id=saved
        if workspace!=expected_workspace:
            return dict(status='UNAUTHORIZED',reason='WORKSPACE_NOT_AUTHORIZED')
        checked=review_graph_result(payload,record,store,run_id=run_id,task_id=task_id,limits=self.limits)
        if checked['status']!='pass':return dict(status='BLOCKED',reason='GRAPH_FINAL_NOT_CURRENTLY_VERIFIED')
        return deepcopy(public_result)


def product_graph_service(app,investigation):
    """Compose with existing Host model/Provider budgets, never discover credentials."""
    def store_factory(workspace):
        if app._workspace_id()!=workspace: raise PermissionError('CURRENT_WORKSPACE_CHANGED')
        def authorized(ws,source,version):
            if ws!=workspace or app._workspace_id()!=workspace:return False
            return any(s['source_id']==source and s['source_version']==version and s['active']
                       for s in app.repository.list_sources(workspace))
        return SQLiteGraphStore(app.repository.database_path,authorized_workspace=workspace,authorize_source=authorized)
    def planner_factory(context):
        service=investigation()
        if service is None:return None
        explicit=getattr(service,'graph_planner_factory',None)
        if callable(explicit):return explicit(context)
        if service.task_only:return None
        models=service.task_models
        if service.external_provider:
            # Live graph planning must have the existing Host's explicit Provider budget.
            if models is None or models.execution_kind!='LIVE':return None
            from linkloom.ui.task_provider import TaskProviderSession
            root=Path(app.repository.database_path).parent/'graph-provider'/uuid4().hex
            def validate_provider():
                if context.validate_current() is not True:raise PermissionError('SOURCE_CHANGED')
            session=TaskProviderSession(root,models.provider_limits,validate_provider)
            # Existing Host owns the session through async legacy completion/shutdown.
            # A failed decomposition never grants a new per-question budget.
            with service._lock:service._provider_sessions.append(session)
            _GRAPH_PROVIDER.set(session)
            adapter=session.bind(models.planner,'graph-planning'); model_id=models.model_id
            close=None; cost=models.provider_limits.request_cost_upper_bound_usd
        else:
            if service.model_factory is None or models is None:return None
            adapter=models.planner if models is not None else service.model_factory(context.question)
            model_id=models.model_id if models is not None else getattr(adapter,'model_id',None) or 'offline-graph-planner'
            close=None;cost=0
        guard_id='product-graph-host'
        def guard(request):
            if context.validate_current() is not True:raise PermissionError('SOURCE_CHANGED')
            return ProviderRequestAuthorization.approve(request,guard_id=guard_id,
                reservation_id=digest(request.to_dict()),estimated_cost_upper_bound_usd=cost)
        planner=GraphQuestionPlanner(adapter,model_id=model_id,request_guard=guard,guard_id=guard_id,alignment_review=False,grounding_required=True,mention_grounding=True)
        return (planner,close) if close else planner
    def admitted_planner_factory(context):
        service=investigation()
        if service is None:return None
        from linkloom.ui.lifecycle import BackgroundWorkStopped
        try:
            # close() drains admitted creation before taking its session snapshot.
            with service._starts.operation('graph-planning-'+uuid4().hex):
                return planner_factory(context)
        except BackgroundWorkStopped as error:
            raise PermissionError('APPLICATION_SHUTTING_DOWN') from error
    service=GraphInvestigationService(store_factory,admitted_planner_factory)
    # The current configured provider can change between requests, so check it in the caller too.
    return service
