"""Product source identity and bounded F/G preparation for the existing Runtime.

No executor, model loop or authoritative task store lives here. Every semantic
endpoint comes from F/G, never from a query parser or a fixture answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import json
import re
from pathlib import Path
from linkloom.agents.providers import GeminiProviderAdapter

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
        from linkloom.ui.canary import is_canary_adapter
        if self.execution_kind == 'LIVE':
            if (self.provider_id != 'gemini' or not all(is_canary_adapter(a, s) for a, s in
                    zip((self.discovery, self.planner, self.semantics), ('discovery', 'planning', 'semantic')))):
                raise ValueError('UNTRUSTED_CANARY_ADAPTER: arbitrary stages are limited to FAKE/REPLAY')
        elif any(is_canary_adapter(a) or isinstance(a, GeminiProviderAdapter)
                 for a in (self.discovery, self.planner, self.semantics)):
            raise ValueError('LIVE_ADAPTER_CANNOT_BE_FAKE_OR_REPLAY')
        elif self.execution_kind not in {'FAKE', 'REPLAY'}:
            raise ValueError('Only explicitly injected FAKE/REPLAY task stages are enabled.')


class ProductTaskSources:
    def __init__(self, workspace_id, sources, current_sources, *, on_source_change=None):
        self.workspace_id, self.sources, self.current_sources = workspace_id, sources, current_sources
        self.on_source_change = on_source_change
        self.registry = SourceReferenceRegistry()
        self.mapping = {}
        artifacts = []
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
                self.registry.register_evidence(workspace_id, segment.evidence_span.evidence_ref,
                    content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
        self.artifacts = tuple(artifacts)
        self.identity = self._identity(sources)

    @staticmethod
    def _identity(sources):
        return canonical_hash(sorted((s['source_id'], s['source_version'],
                                      canonical_hash(s['content'])) for s in sources))

    def validate_current(self):
        try:
            current = self._identity(self.current_sources())
        except Exception:
            if self.on_source_change is not None:
                self.on_source_change()
            raise
        if current != self.identity:
            if self.on_source_change is not None:
                self.on_source_change()
            raise ValueError('SOURCE_SNAPSHOT_CHANGED')

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
            new_billable_cost_usd=None if getattr(self.adapter, 'execution_kind', None) == 'LIVE' else 0.0)
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


def prepare_task_investigation(question, sources, models, root):
    """F -> G -> typed ready work; all actual tool execution stays in Runtime."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources.validate_current()
    guard_id = 'product-task-host'

    def guard(request):
        sources.validate_current()
        return ProviderRequestAuthorization.approve(request, guard_id=guard_id,
            reservation_id=canonical_hash(request.to_dict()),
            estimated_cost_upper_bound_usd=.079872 if models.execution_kind == 'LIVE' else 0)

    discovery_adapter = _AuditedAdapter(models.discovery, root, 'discovery', sources.validate_current,
        max_calls=models.discovery_limits.max_calls)
    extractor = ProviderNeutralSemanticExtractor(
        discovery_adapter,
        provider_id=models.provider_id if models.execution_kind == 'LIVE' else models.execution_kind, model_id=models.model_id,
        request_guard=guard, request_guard_id=guard_id, max_attempts=1)
    discovery = TaskObservationDiscoveryService(extractor, source_registry=sources.registry,
        current_source=sources.current, limits=models.discovery_limits).discover(
            sources.artifacts, workspace_id=sources.workspace_id)
    if models.execution_kind == 'LIVE':
        models.discovery.host.ledger.check_open()
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
        result_verifier=lambda receipt, current: receipts.get(receipt.receipt_id) == receipt)
    planning = planner.plan(context)
    if models.execution_kind == 'LIVE':
        models.planner.host.ledger.check_open()
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
        if models.execution_kind == 'LIVE':
            models.planner.host.ledger.check_open()
    gaps = list(planning.plan.gaps) if planning.plan else []
    if not discovery.coverage_complete:
        gaps.append('DISCOVERY_COVERAGE_INCOMPLETE')
    if not observations:
        gaps.append('NO_TASK_CANDIDATES; ABSENCE_NOT_PROVEN')
    if planning.status != 'PLANNED':
        gaps.extend(planning.errors or (planning.status,))
    queries = []
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
            elif step.kind == 'inspect':
                gaps.append(f'INSPECTION_RETURNS_UNCONFIRMED_CANDIDATES_ONLY:{step.id}')
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
                max_cost_usd=.079872 if models.execution_kind == 'LIVE' else 0,
                request_cost_upper_bound_usd=.079872 if models.execution_kind == 'LIVE' else 0))
    tools = TaskRelationTools(sources.workspace_id, current_raw, semantic_analysis=semantic)
    manifest = dict(schema='product-task-preparation/v1', question=question,
        status='BLOCKED' if planning.status != 'PLANNED' else 'UNKNOWN' if not observations else 'PARTIAL',
        execution_kind=models.execution_kind, provider_id=models.provider_id, model_id=models.model_id,
        authority='MODEL_PROPOSED_ONLY',
        discovery=discovery.to_dict(), planning=plans, source_mapping=sources.mapping,
        source_snapshot_hash=sources.identity, gaps=gaps, stage_budget=dict(
            discovery_calls_reserved=len(discovery.extractions), discovery_call_limit=models.discovery_limits.max_calls,
            discovery_input_tokens_reserved_upper_bound=discovery_adapter.input_reserved,
            discovery_output_tokens_reserved=discovery_adapter.output_reserved,
            discovery_max_request_bytes=discovery_adapter.max_request_bytes,
            planning=planning.budget, semantic_call_limit=1 if queries else 0,
            new_billable_cost_usd=None if models.execution_kind == 'LIVE' else 0.0),
        evidence_layers=dict(component='SEPARATE_TEST_EVIDENCE', integration=models.execution_kind,
            real_model_semantics='NOT_EVALUATED', real_product_business_task='NOT_EVALUATED'))
    sources.validate_current()
    if models.execution_kind == 'LIVE':
        manifest['campaign_budget'] = models.discovery.execution_receipt()
    (root / 'preparation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    return TaskPreparation(sources, tools, manifest)
