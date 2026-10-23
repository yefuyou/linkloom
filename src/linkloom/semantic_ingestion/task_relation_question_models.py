"""Host-owned investigation contracts. Plans suggest work; they prove no facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import unicodedata

from linkloom.agents.model_adapter import ModelUsage
from linkloom.semantic_ingestion.artifacts import EvidenceSpan
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_models import Endpoint, SemanticQuery


def premise_satisfied(kind, outcome):
    """A bounded attempt ending UNKNOWN is terminal, not a positive premise."""
    return outcome == 'SUPPORTED' if kind == 'relation' else outcome in {'COMPLETED', 'SUPPORTED'}


@dataclass(frozen=True, slots=True)
class PlanningLimits:
    max_subgoals: int = 12
    max_relation_queries: int = 8
    max_fanout: int = 4
    max_dependency_depth: int = 6
    max_relation_hops: int = 3
    max_input_tokens: int = 24576
    max_output_tokens: int = 4096
    max_provider_calls: int = 3
    max_total_input_tokens: int = 73728
    max_total_output_tokens: int = 12288

    def __post_init__(self):
        ceilings = (32, 24, 16, 16, 3, 30720, 8192, 3, 92160, 24576)
        for (name, value), ceiling in zip(asdict(self).items(), ceilings):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('invalid planning limit: ' + name)


@dataclass(frozen=True, slots=True)
class InvestigationStep:
    id: str
    kind: str
    question: str
    handles: tuple[str, ...]
    endpoints: tuple[Endpoint, ...]
    source_handles: tuple[str, ...]
    depends_on: tuple[str, ...]
    predicate: str
    field: str
    target_kind: str
    missing_entities: tuple[str, ...]
    workspace_id: str = ''
    source_bindings: tuple[EvidenceSpan, ...] = ()
    time_scope: tuple[str, str] = ('', '')
    premise_keys: tuple[str, ...] = ()
    evidence_gap: str = ''

    @property
    def operation_key(self):
        """The operation's target, without evidence/time/dependency scope."""
        refs = tuple(canonical_hash(ref.to_dict()) for ref in self.endpoints)
        if self.kind in {'inspect', 'discover', 'clarify'} or self.predicate in {
            'SAME_TASK', 'RELATED_TO', 'PROJECT_ALIAS',
        }:
            refs = tuple(sorted(refs))
        entities = sorted({' '.join(unicodedata.normalize('NFKC', e).casefold().split())
                           for e in self.missing_entities})
        return canonical_hash([self.workspace_id, self.kind, refs, self.predicate,
                               self.field, self.target_kind, entities])

    @property
    def execution_key(self):
        # Human prose and handle/step aliases cannot manufacture fresh work.
        sources = sorted({canonical_hash(asdict(span)) for span in self.source_bindings})
        return canonical_hash([self.operation_key, sources, self.time_scope, sorted(set(self.premise_keys))])

    def to_semantic_query(self):
        if self.kind == 'relation' and self.time_scope != ('', ''):
            raise ValueError('TIME_SCOPED_OPERATION_REQUIRES_HOST_BINDING')
        if self.kind == 'relation' and self.source_handles:
            raise ValueError('SOURCE_SCOPED_OPERATION_REQUIRES_HOST_BINDING')
        return SemanticQuery(self.predicate, *self.handles) if self.kind == 'relation' else None

    def to_semantic_proposal_query(self):
        """Project a relation into the non-authoritative semantic proposer.

        Source bindings are part of the authorized semantic context and may be
        supplied to the proposer. The P4 query projection above remains strict:
        source-scoped and time-scoped operations still require host binding.
        """
        if self.kind == 'relation' and self.time_scope != ('', ''):
            raise ValueError('TIME_SCOPED_OPERATION_REQUIRES_HOST_BINDING')
        return SemanticQuery(self.predicate, *self.handles) if self.kind == 'relation' else None

    def to_dict(self):
        return dict(id=self.id, kind=self.kind, question=self.question, handles=list(self.handles),
                    endpoints=[ref.to_dict() for ref in self.endpoints],
                    source_handles=list(self.source_handles), depends_on=list(self.depends_on),
                    predicate=self.predicate, field=self.field, target_kind=self.target_kind,
                    missing_entities=list(self.missing_entities), execution_key=self.execution_key,
                    workspace_id=self.workspace_id, source_bindings=[dict(
                        workspace_id=s.workspace_id, artifact_id=s.artifact_id,
                        artifact_version_id=s.artifact_version_id, evidence_ref=s.evidence_ref,
                        content_hash=s.content_hash, span_sha256=canonical_hash(asdict(s))) for s in self.source_bindings],
                    time_scope=dict(zip(('start', 'end'), self.time_scope)),
                    premise_keys=list(self.premise_keys), evidence_gap=self.evidence_gap)


@dataclass(frozen=True, slots=True)
class VerifiedStepResult:
    """Receipt supplied and revalidated by Host, never accepted from the model."""
    plan_id: str
    step_id: str
    receipt_id: str
    result_sha256: str
    summary: str
    outcome: str = 'COMPLETED'

    def __post_init__(self):
        for value in asdict(self).values():
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError('invalid verified step receipt')
        if len(self.result_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.result_sha256):
            raise ValueError('invalid result digest')
        if self.outcome not in {'COMPLETED', 'SUPPORTED', 'REFUTED', 'UNKNOWN', 'CONFLICT'}:
            raise ValueError('invalid verified result outcome')


@dataclass(frozen=True, slots=True)
class InvestigationPlan:
    goal: str
    workspace_id: str
    snapshot_hash: str
    steps: tuple[InvestigationStep, ...]
    uncertainties: tuple[str, ...]
    refusal_reasons: tuple[str, ...]
    verified_results: tuple[VerifiedStepResult, ...] = ()
    parent_plan_id: str | None = None
    gaps: tuple[str, ...] = ()
    limits: PlanningLimits = field(default_factory=PlanningLimits)

    @property
    def plan_id(self):
        return 'investigation_v2_' + canonical_hash(self._payload())

    @property
    def needs_more_investigation(self):
        done = {r.step_id for r in self.verified_results}
        return any(s.id not in done for s in self.steps) or bool(self.gaps)

    @property
    def semantic_queries(self):
        """Ready endpoint projections only; Host must retain each full step scope."""
        return tuple(s.to_semantic_query() for s in self.ready_steps if s.kind == 'relation')

    @property
    def ready_steps(self):
        receipts = {r.step_id: r for r in self.verified_results}
        steps = {s.id: s for s in self.steps}

        def satisfied(identity):
            receipt = receipts.get(identity)
            return (receipt is not None and
                    premise_satisfied(steps[identity].kind, receipt.outcome) and
                    all(satisfied(parent) for parent in steps[identity].depends_on))

        return tuple(s for s in self.steps if s.id not in receipts and all(satisfied(p) for p in s.depends_on))

    def _payload(self):
        return dict(schema='task-investigation-plan/v2', goal=self.goal, workspace_id=self.workspace_id,
                    snapshot_hash=self.snapshot_hash, steps=[s.to_dict() for s in self.steps],
                    uncertainties=list(self.uncertainties), refusal_reasons=list(self.refusal_reasons),
                    verified_results=[asdict(r) for r in self.verified_results],
                    parent_plan_id=self.parent_plan_id, gaps=list(self.gaps),
                    limits=asdict(self.limits),
                    authority='ACTION_SUGGESTION_ONLY', publishable=False,
                    conclusion_policy='Evidence and independent Reviewer required.')

    def to_dict(self):
        return dict(plan_id=self.plan_id, needs_more_investigation=self.needs_more_investigation,
                    ready_step_ids=[s.id for s in self.ready_steps],
                    **self._payload())


@dataclass(frozen=True, slots=True)
class PlanningResult:
    status: str
    plan: InvestigationPlan | None = None
    errors: tuple[str, ...] = ()
    provider_calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    request_sha256: str | None = None
    response_sha256: str | None = None
    model_id: str | None = None
    actual_model_version: str | None = None
    budget: dict = field(default_factory=dict)
    untrusted_output: dict | None = None
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self):
        return dict(schema='task-investigation-result/v1', status=self.status,
                    plan=self.plan.to_dict() if self.plan else None, errors=list(self.errors),
                    provider_calls=self.provider_calls, usage=self.usage.to_dict(),
                    request_sha256=self.request_sha256, response_sha256=self.response_sha256,
                    model_id=self.model_id, actual_model_version=self.actual_model_version,
                    budget=dict(self.budget), untrusted_output=self.untrusted_output,
                    diagnostics=self.diagnostics)
