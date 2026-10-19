"""Host-owned investigation contracts. Plans suggest work; they prove no facts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from linkloom.agents.model_adapter import ModelUsage
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_models import Endpoint, SemanticQuery


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

    @property
    def execution_key(self):
        # Text paraphrases cannot create another execution of the same typed work.
        refs = tuple(canonical_hash(ref.to_dict()) for ref in self.endpoints)
        if self.kind in {'inspect', 'discover', 'clarify'} or self.predicate in {
            'SAME_TASK', 'RELATED_TO', 'PROJECT_ALIAS',
        }:
            refs = tuple(sorted(refs))
        return canonical_hash([self.kind, refs, self.predicate, self.field, self.target_kind])

    def to_semantic_query(self):
        return SemanticQuery(self.predicate, *self.handles) if self.kind == 'relation' else None

    def to_dict(self):
        return dict(id=self.id, kind=self.kind, question=self.question, handles=list(self.handles),
                    endpoints=[ref.to_dict() for ref in self.endpoints],
                    source_handles=list(self.source_handles), depends_on=list(self.depends_on),
                    predicate=self.predicate, field=self.field, target_kind=self.target_kind,
                    missing_entities=list(self.missing_entities), execution_key=self.execution_key)


@dataclass(frozen=True, slots=True)
class VerifiedStepResult:
    """Receipt supplied and revalidated by Host, never accepted from the model."""
    plan_id: str
    step_id: str
    receipt_id: str
    result_sha256: str
    summary: str

    def __post_init__(self):
        for value in asdict(self).values():
            if not isinstance(value, str) or not value.strip() or len(value) > 1024:
                raise ValueError('invalid verified step receipt')
        if len(self.result_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.result_sha256):
            raise ValueError('invalid result digest')


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
        return 'investigation_v1_' + canonical_hash(self._payload())

    @property
    def needs_more_investigation(self):
        done = {r.step_id for r in self.verified_results}
        return any(s.id not in done for s in self.steps) or bool(self.gaps)

    @property
    def semantic_queries(self):
        done = {r.step_id for r in self.verified_results}
        return tuple(s.to_semantic_query() for s in self.steps if s.kind == 'relation' and s.id not in done)

    def _payload(self):
        return dict(schema='task-investigation-plan/v1', goal=self.goal, workspace_id=self.workspace_id,
                    snapshot_hash=self.snapshot_hash, steps=[s.to_dict() for s in self.steps],
                    uncertainties=list(self.uncertainties), refusal_reasons=list(self.refusal_reasons),
                    verified_results=[asdict(r) for r in self.verified_results],
                    parent_plan_id=self.parent_plan_id, gaps=list(self.gaps),
                    limits=asdict(self.limits),
                    authority='ACTION_SUGGESTION_ONLY', publishable=False,
                    conclusion_policy='Evidence and independent Reviewer required.')

    def to_dict(self):
        return dict(plan_id=self.plan_id, needs_more_investigation=self.needs_more_investigation,
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

    def to_dict(self):
        return dict(schema='task-investigation-result/v1', status=self.status,
                    plan=self.plan.to_dict() if self.plan else None, errors=list(self.errors),
                    provider_calls=self.provider_calls, usage=self.usage.to_dict(),
                    request_sha256=self.request_sha256, response_sha256=self.response_sha256,
                    model_id=self.model_id, actual_model_version=self.actual_model_version,
                    budget=dict(self.budget), untrusted_output=self.untrusted_output)
