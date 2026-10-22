"""Product source identity and bounded F/G preparation for the existing Runtime.

No executor, model loop or authoritative task store lives here. Every semantic
endpoint comes from F/G, never from a query parser or a fixture answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import json
import re
from pathlib import Path
from linkloom.agents.providers import DeepSeekProviderAdapter, GeminiProviderAdapter
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService, DiscoveryLimits
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash
from linkloom.semantic_ingestion.task_relation_question_models import PlanningLimits, VerifiedStepResult
from linkloom.semantic_ingestion.task_relation_question_planner import InvestigationPlanner
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticContext
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools


# Planning vocabulary remains available; execution prerequisites are explicit.
HOST_OPERATION_CAPABILITIES = {
    'inspect': dict(execution='EXISTING_RUNTIME_TOOL', tool='inspect_task_context',
        result='COMPLETED inspection of UNCONFIRMED observations; no semantic approval',
        time_scope='UNSUPPORTED; explicit gap, never silently dropped'),
    'discover': dict(execution='BOUNDED_F_SNAPSHOT_ONLY',
        result='UNKNOWN receipt; no second F scan and no evidence of absence'),
    'relation': dict(execution='READY_UNSCOPED_SEMANTIC_PROPOSAL', tool='analyze_task_semantics',
        result='EVIDENCE_VALID proposal only; independent semantic support required for premises',
        time_scope='UNSUPPORTED', source_scope='UNSUPPORTED'),
    'compare_dates': dict(execution='TRUSTED_RELATIONS_REQUIRED', tool='query_task_relations',
        result='P4 can read existing verified relations; product F/G creates no such records'),
    'clarify': dict(execution='VISIBLE_GAP_ONLY', result='No tool or automatic user conversation'),
}


@dataclass(frozen=True)
class TaskInvestigationModels:
    discovery: object
    planner: object
    semantics: object
    execution_kind: str = 'FAKE'
    model_id: str = 'offline-task-investigation'
    provider_id: str = 'offline'
    discovery_limits: DiscoveryLimits = field(default_factory=DiscoveryLimits)
    planning_limits: PlanningLimits = field(default_factory=PlanningLimits)

    def __post_init__(self):
        # Product task investigation is deliberately offline in this milestone.
        # External Provider stages are not part of this Host composition.
        if self.execution_kind not in {'FAKE', 'REPLAY'}:
            raise ValueError('Only explicitly injected FAKE/REPLAY task stages are enabled.')
        if any(isinstance(stage, (GeminiProviderAdapter, DeepSeekProviderAdapter))
               for stage in (self.discovery, self.planner, self.semantics)):
            raise ValueError('LIVE_ADAPTER_CANNOT_BE_FAKE_OR_REPLAY')
        if not all(callable(getattr(stage, 'complete', None))
                   for stage in (self.discovery, self.planner, self.semantics)):
            raise ValueError('Task investigation stages must expose complete(request).')


class ProductTaskSources:
    def __init__(self, workspace_id, sources, current_sources, *, on_source_change=None,
                 current_evidence=None):
        self.workspace_id, self.sources, self.current_sources = workspace_id, sources, current_sources
        self.on_source_change = on_source_change
        self.current_evidence = current_evidence
        self.registry = SourceReferenceRegistry()
        self.mapping = {}
        artifacts, evidence_rows = [], []
        for source in sources:
            if re.fullmatch(r'src_[0-9a-f]{32}', source['source_id']) is None:
                raise ValueError('INVALID_SOURCE_ID')
            artifact = RawArtifact(workspace_id, source['source_id'], 'timestamped_text',
                source['content'], source_uri=source.get('source_uri'),
                ingestion_time=datetime.fromisoformat(source.get('version_created_at', source['created_at']).replace('Z', '+00:00')))
            if artifact.artifact_version_id != source['source_version']:
                if self.on_source_change is not None:
                    self.on_source_change()
                raise ValueError('PRODUCT_ARTIFACT_IDENTITY_MISMATCH')
            artifacts.append(artifact)
            self.mapping[artifact.artifact_version_id] = dict(source_id=source['source_id'],
                source_version=source['source_version'], source_name=source['name'],
                content_sha256=artifact.content_hash)
            for segment in parse_source_text(artifact).segments:
                span = segment.evidence_span
                evidence_rows.append(dict(source_id=artifact.artifact_id,
                    artifact_version_id=artifact.artifact_version_id, evidence_ref=span.evidence_ref,
                    content_sha256=span.content_hash, quote_sha256=span.quote_sha256,
                    line_start=span.line_start, line_end=span.line_end, quote=span.quote))
                self.registry.register_evidence(workspace_id, span.evidence_ref,
                    content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
        self.artifacts = tuple(artifacts)
        self.identity = self._identity(sources)
        self.evidence_identity = self._evidence_identity(evidence_rows)
        self.validate_current()

    @staticmethod
    def _evidence_identity(rows):
        fields = ('source_id', 'artifact_version_id', 'evidence_ref', 'content_sha256',
                  'quote_sha256', 'line_start', 'line_end', 'quote')
        return frozenset(tuple(row.get(key) for key in fields) for row in rows)

    @staticmethod
    def _identity(sources):
        return canonical_hash(sorted((s['source_id'], s['source_version'],
                                      canonical_hash(s['content'])) for s in sources))

    def validate_current(self):
        try:
            current = self._identity(self.current_sources())
            if current != self.identity:
                raise ValueError('SOURCE_SNAPSHOT_CHANGED')
            if (self.current_evidence is not None and not self.evidence_identity.issubset(
                    self._evidence_identity(self.current_evidence()))):
                raise ValueError('SOURCE_EVIDENCE_NOT_CURRENT')
        except Exception:
            if self.on_source_change is not None:
                self.on_source_change()
            raise

    def current(self, workspace, artifact_id):
        self.validate_current()
        return next((a for a in self.artifacts if a.workspace_id == workspace and a.artifact_id == artifact_id), None)


class _AuditedAdapter:
    def __init__(self, adapter, root, stage, validate, *, max_calls, max_request_bytes=48 * 1024):
        self.adapter, self.root, self.stage, self.validate = adapter, root, stage, validate
        self.count = 0
        self.max_calls, self.max_request_bytes = max_calls, max_request_bytes
        self.input_reserved = self.output_reserved = 0

    def complete(self, request):
        self.validate()
        size = len(json.dumps(request.to_dict(), ensure_ascii=False).encode('utf8'))
        if self.count >= self.max_calls or size > self.max_request_bytes:
            raise ValueError('HOST_STAGE_BUDGET_EXHAUSTED')
        self.count += 1
        self.input_reserved += size + 1024
        output_limit = request.generation_options.max_output_tokens
        self.output_reserved += output_limit
        path = self.root / f'{self.stage}-{self.count}.json'
        payload = {'request': request.to_dict(), 'request_sha256': canonical_hash(request.to_dict())}
        payload['reservation'] = dict(calls_reserved=self.count, max_calls=self.max_calls,
            input_tokens_upper_bound=size + 1024, output_tokens=output_limit,
            max_request_bytes=self.max_request_bytes,
            new_billable_cost_usd=0.0)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')
        try:
            response = self.adapter.complete(request)
            payload['response'] = response.to_dict()
            if ((response.usage.input_tokens or 0) > size + 1024
                    or (response.usage.output_tokens or 0) > output_limit):
                raise ValueError('HOST_STAGE_USAGE_EXCEEDED')
            self.validate()
            return response
        except Exception as exc:
            payload['failure'] = type(exc).__name__
            raise
        finally:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')


@dataclass
class TaskPreparation:
    sources: ProductTaskSources
    tools: TaskRelationTools
    manifest: dict


def restore_task_preparation(sources, manifest, *, relation_path=None):
    """Rebind saved F/G data for read-only validation, without calling F or G."""
    from linkloom.semantic_ingestion.task_observation import TaskObservation
    from linkloom.semantic_ingestion.task_relation_models import _endpoint
    from linkloom.semantic_ingestion.task_relation_question_models import InvestigationPlan, InvestigationStep
    sources.validate_current()
    if manifest['source_snapshot_hash'] != sources.identity or manifest['execution_kind'] not in {'FAKE', 'REPLAY'}:
        raise ValueError('SAVED_SNAPSHOT_UNAVAILABLE')
    discovery = manifest['discovery']
    observations = tuple(TaskObservation.from_dict(o) for o in
        (*discovery['valid_observations'], *discovery['partial_observations']))
    artifacts = {a.artifact_version_id: a for a in sources.artifacts}
    if any(o.provenance.artifact_version_id not in artifacts
           or not o.verify(artifacts[o.provenance.artifact_version_id]) for o in observations):
        raise ValueError('SAVED_OBSERVATION_INVALID')
    spans = tuple((f'source_{i}:{j}', segment.evidence_span)
        for i, artifact in enumerate(sources.artifacts)
        for j, segment in enumerate(parse_source_text(artifact).segments, 1))
    by_hash = {canonical_hash(asdict(s)): s for _, s in spans}
    data = manifest['planning'][-1]['plan']
    steps = []
    for row in data['steps']:
        steps.append(InvestigationStep(row['id'], row['kind'], row['question'], tuple(row['handles']),
            tuple(_endpoint(e) for e in row['endpoints']), tuple(row['source_handles']),
            tuple(row['depends_on']), row['predicate'], row['field'], row['target_kind'],
            tuple(row['missing_entities']), row['workspace_id'],
            tuple(by_hash[s['span_sha256']] for s in row['source_bindings']),
            (row['time_scope']['start'], row['time_scope']['end']), tuple(row['premise_keys']), row['evidence_gap']))
    plan = InvestigationPlan(data['goal'], data['workspace_id'], data['snapshot_hash'], tuple(steps),
        tuple(data['uncertainties']), tuple(data['refusal_reasons']),
        tuple(VerifiedStepResult(**r) for r in data['verified_results']), data['parent_plan_id'],
        tuple(data['gaps']), PlanningLimits(**data['limits']))
    if plan.to_dict() != data:
        raise ValueError('SAVED_PLAN_BINDING_INVALID')
    context = SemanticContext(sources.workspace_id, manifest['question'], sources.artifacts, observations,
        tuple((f'task_{i}', ObservationRef.from_observation(o)) for i, o in enumerate(observations)),
        spans, sources.registry, frozenset(artifacts), True, False)
    queries = tuple(step.to_semantic_query() for step in plan.ready_steps if step.kind == 'relation')
    semantic = None
    if queries:
        class ReadOnlyAdapter:
            def complete(self, _request):
                raise ValueError('SAVED_RESULT_CANNOT_CALL_MODEL')
        context = replace(context, queries=queries)
        semantic = SemanticAnalysis('product-investigation', sources.workspace_id,
            lambda: (sources.validate_current() or SemanticSourceSnapshot.from_context(context)),
            ReadOnlyAdapter(), model_id=manifest['model_id'],
            policy=SemanticCallPolicy(manifest['execution_kind'], max_cost_usd=0,
                                      request_cost_upper_bound_usd=0))
    raw = TaskContextSnapshot(sources.workspace_id, observations, sources.artifacts, sources.registry)
    tools = TaskRelationTools(sources.workspace_id, lambda: (sources.validate_current() or raw),
        relation_path=relation_path, semantic_analysis=semantic, inspection_plan=plan)
    return TaskPreparation(sources, tools, manifest)


def prepare_task_investigation(question, sources, models, root, *, relation_path=None):
    """F -> G -> typed ready work; all actual tool execution stays in Runtime."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources.validate_current()
    guard_id = 'product-task-host'

    def guard(request):
        sources.validate_current()
        return ProviderRequestAuthorization.approve(request, guard_id=guard_id,
            reservation_id=canonical_hash(request.to_dict()),
            estimated_cost_upper_bound_usd=0)

    discovery_adapter = _AuditedAdapter(models.discovery, root, 'discovery', sources.validate_current,
        max_calls=models.discovery_limits.max_calls)
    extractor = ProviderNeutralSemanticExtractor(
        discovery_adapter,
        provider_id=models.execution_kind, model_id=models.model_id,
        request_guard=guard, request_guard_id=guard_id, max_attempts=1)
    discovery = TaskObservationDiscoveryService(extractor, source_registry=sources.registry,
        current_source=sources.current, limits=models.discovery_limits).discover(
            sources.artifacts, workspace_id=sources.workspace_id)
    observations = (*discovery.valid_observations, *discovery.partial_observations)
    spans = tuple((f'source_{i}:{j}', segment.evidence_span)
        for i, artifact in enumerate(sources.artifacts)
        for j, segment in enumerate(parse_source_text(artifact).segments, 1))
    context = SemanticContext(sources.workspace_id, question, sources.artifacts, observations,
        tuple((f'task_{i}', ObservationRef.from_observation(o)) for i, o in enumerate(observations)),
        spans, sources.registry, frozenset(a.artifact_version_id for a in sources.artifacts), True, False)
    # G can plan with incomplete Discovery. That coverage is never evidence of
    # absence, and is separately retained in the final release gaps.
    receipts = {}
    planning_adapter = _AuditedAdapter(models.planner, root, 'planning', sources.validate_current,
        max_calls=models.planning_limits.max_provider_calls)
    planner = InvestigationPlanner(planning_adapter,
        model_id=models.model_id, request_guard=guard, guard_id=guard_id, limits=models.planning_limits,
        host_capabilities=HOST_OPERATION_CAPABILITIES,
        result_verifier=lambda receipt, current: receipts.get(receipt.receipt_id) == receipt)
    planning = planner.plan(context)
    plans = [planning.to_dict()]
    # A discover step requests new handles. F already examined this bounded
    # snapshot: the only honest receipt is UNKNOWN, followed by a bounded replan.
    # Do not call F again or pretend its unreturned tasks are absent.
    if planning.plan and any(s.kind == 'discover' for s in planning.plan.ready_steps):
        verified = []
        for step in planning.plan.ready_steps:
            if step.kind != 'discover':
                continue
            receipt = VerifiedStepResult(planning.plan.plan_id, step.id, 'host_' + step.execution_key,
                canonical_hash(discovery.to_dict()), 'Bounded Discovery examined; missing entities remain unknown.', 'UNKNOWN')
            receipts[receipt.receipt_id] = receipt
            verified.append(receipt)
        planning = planner.plan(context, previous=planning.plan, verified_results=tuple(verified),
            gaps=('BOUNDED_DISCOVERY_CANNOT_ESTABLISH_MISSING_ENTITY_ABSENCE',))
        plans.append(planning.to_dict())
    gaps = list(planning.plan.gaps) if planning.plan else []
    if not discovery.coverage_complete:
        gaps.append('DISCOVERY_COVERAGE_INCOMPLETE')
    if not observations:
        gaps.append('NO_TASK_CANDIDATES; ABSENCE_NOT_PROVEN')
    if planning.status != 'PLANNED':
        gaps.extend(planning.errors or (planning.status,))
    queries, ready_tools, execution_blocked = [], [], False
    if planning.plan:
        ready = {s.id for s in planning.plan.ready_steps}
        for step in planning.plan.steps:
            if step.id not in ready:
                if step.id not in {r.step_id for r in planning.plan.verified_results}:
                    gaps.append(f'UNSATISFIED_PREMISE:{step.id}')
            elif step.kind == 'relation':
                try:
                    queries.append(step.to_semantic_query())
                except ValueError as exc:
                    gaps.append(f'{exc}:{step.id}')
                    execution_blocked = True
            elif step.kind == 'inspect':
                if step.time_scope != ('', ''):
                    gaps.append(f'TIME_SCOPED_INSPECTION_UNSUPPORTED:{step.id}')
                    execution_blocked = True
                else:
                    ready_tools.append(dict(step_id=step.id, execution_key=step.execution_key,
                        tool_id='inspect_task_context', arguments=dict(workspace_id=sources.workspace_id,
                            anchor_ids=[step.endpoints[0].observation_id])))
            else:
                gaps.append(f'UNRESOLVED_{step.kind.upper()}:{step.id}')
    raw = TaskContextSnapshot(sources.workspace_id, tuple(observations), sources.artifacts, sources.registry)

    def current_raw():
        sources.validate_current()
        return raw

    semantic = None
    if queries:
        semantic_context = replace(context, queries=tuple(queries))
        semantic = SemanticAnalysis('product-investigation', sources.workspace_id,
            lambda: (sources.validate_current() or SemanticSourceSnapshot.from_context(semantic_context)),
            models.semantics, model_id=models.model_id, policy=SemanticCallPolicy(models.execution_kind,
                max_cost_usd=0, request_cost_upper_bound_usd=0))
    tools = TaskRelationTools(sources.workspace_id, current_raw, relation_path=relation_path, semantic_analysis=semantic,
        inspection_plan=planning.plan,
        execution_blocked_reason='UNSUPPORTED_OPERATION_SCOPE' if execution_blocked else None)
    manifest = dict(schema='product-task-preparation/v1', question=question,
        status='BLOCKED' if planning.status != 'PLANNED' or execution_blocked else 'UNKNOWN' if not observations else 'PARTIAL',
        execution_kind=models.execution_kind, provider_id=models.provider_id, model_id=models.model_id,
        authority='MODEL_PROPOSED_ONLY',
        operation_capabilities=HOST_OPERATION_CAPABILITIES, ready_tool_operations=ready_tools,
        discovery=discovery.to_dict(), planning=plans, source_mapping=sources.mapping,
        source_snapshot_hash=sources.identity, gaps=gaps, stage_budget=dict(
            discovery_calls_reserved=len(discovery.extractions), discovery_call_limit=models.discovery_limits.max_calls,
            discovery_input_tokens_reserved_upper_bound=discovery_adapter.input_reserved,
            discovery_output_tokens_reserved=discovery_adapter.output_reserved,
            discovery_max_request_bytes=discovery_adapter.max_request_bytes,
            planning=planning.budget, semantic_call_limit=1 if queries else 0,
            new_billable_cost_usd=0.0),
        evidence_layers=dict(component='SEPARATE_TEST_EVIDENCE', integration=models.execution_kind,
            real_model_semantics='NOT_EVALUATED', real_product_business_task='NOT_EVALUATED'))
    sources.validate_current()
    (root / 'preparation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    return TaskPreparation(sources, tools, manifest)
