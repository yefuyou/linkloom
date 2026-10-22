"""Host-owned complete raw snapshots; never built from a tool display result."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_models import (
    Endpoint, SemanticContext, SemanticLimits, SemanticQuery,
)
from linkloom.semantic_ingestion.task_relation_semantic_validation import context_errors


@dataclass(frozen=True)
class SemanticSourceSnapshot:
    workspace_id: str
    question: str
    artifacts: tuple[RawArtifact, ...]
    observations: tuple[TaskObservation, ...]
    endpoints: tuple[tuple[str, Endpoint], ...]
    queries: tuple[SemanticQuery, ...]
    source_handles: tuple[tuple[str, str], ...]
    source_registry: SourceReferenceRegistry
    authorized_artifact_versions: frozenset[str]
    expected_artifact_versions: frozenset[str]
    expected_observation_payloads: frozenset[str]
    inventory_complete: bool
    truncated: bool
    revoked_observation_ids: frozenset[str] = frozenset()
    revoked_evidence_refs: frozenset[str] = frozenset()
    limits: SemanticLimits = field(default_factory=SemanticLimits)

    @classmethod
    def from_context(cls, context: SemanticContext):
        """Host/test migration helper for an already declared A source catalog.

        The builder still parses the original artifacts and validates the full
        expected inventory. No spans or fields are read from B display output.
        """
        handles = {}
        for handle, span in context.allowed_spans:
            prefix = handle.rsplit(":", 1)[0]
            if span.artifact_version_id in handles and handles[span.artifact_version_id] != prefix:
                raise ValueError("one source handle per authorized source required")
            handles[span.artifact_version_id] = prefix
        return cls(context.workspace_id, context.question, context.artifacts, context.observations,
            context.endpoints, context.queries, tuple(handles.items()), context.source_registry,
            context.authorized_artifact_versions, frozenset(a.artifact_version_id for a in context.artifacts),
            frozenset(canonical_hash(ObservationRef.from_observation(o).to_dict()) for o in context.observations),
            context.inventory_complete, context.truncated, context.revoked_observation_ids,
            context.revoked_evidence_refs, context.limits)


@dataclass(frozen=True)
class ContextBuild:
    context: SemanticContext | None
    errors: tuple[str, ...]
    input_inventory: dict = field(default_factory=dict)

    @property
    def ready(self):
        return self.context is not None and not self.errors

    def manifest(self):
        context = self.context
        return dict(status="READY" if self.ready else "BLOCKED", errors=list(self.errors),
            workspace_id=context.workspace_id if context else None,
            source_snapshot_hash=context.source_snapshot_hash if context else None,
            inventory_complete=context.inventory_complete if context else False,
            truncated=context.truncated if context else True,
            source_versions=sorted(context.authorized_artifact_versions) if context else [],
            observation_count=len(context.observations) if context else 0,
            source_count=len(context.artifacts) if context else 0,
            revoked_observation_ids=sorted(context.revoked_observation_ids) if context else [],
            revoked_evidence_refs=sorted(context.revoked_evidence_refs) if context else [],
            limits=asdict(context.limits) if context else self.input_inventory.get("limits"),
            input_inventory=self.input_inventory,
            scope="HOST_DECLARED_BOUNDED_SOURCE_INVENTORY; NOT_GLOBAL_TASK_COMPLETENESS")


def build_semantic_context(snapshot: SemanticSourceSnapshot, *, expected_workspace_id: str) -> ContextBuild:
    if not isinstance(snapshot, SemanticSourceSnapshot):
        return ContextBuild(None, ("INVALID_HOST_SNAPSHOT",))
    inventory = dict(workspace_id=snapshot.workspace_id,
        source_versions=sorted(a.artifact_version_id for a in snapshot.artifacts),
        expected_source_versions=sorted(snapshot.expected_artifact_versions),
        observation_payloads=sorted(canonical_hash(ObservationRef.from_observation(o).to_dict()) for o in snapshot.observations),
        expected_observation_payloads=sorted(snapshot.expected_observation_payloads),
        source_count=len(snapshot.artifacts), observation_count=len(snapshot.observations),
        source_characters=sum(len(a.content) for a in snapshot.artifacts),
        inventory_complete=snapshot.inventory_complete, truncated=snapshot.truncated,
        revoked_observation_ids=sorted(snapshot.revoked_observation_ids),
        revoked_evidence_refs=sorted(snapshot.revoked_evidence_refs), limits=asdict(snapshot.limits))
    inventory["inventory_sha256"] = canonical_hash(inventory)
    inventory["display_lists_truncated"] = False
    for key, value in tuple(inventory.items()):
        if isinstance(value, list) and len(value) > 64:
            inventory[key] = value[:64]
            inventory["display_lists_truncated"] = True
    errors = set()
    if snapshot.workspace_id != expected_workspace_id:
        errors.add("CROSS_WORKSPACE")
    if not snapshot.inventory_complete or snapshot.truncated:
        errors.add("INCOMPLETE_CONTEXT")
    versions = frozenset(a.artifact_version_id for a in snapshot.artifacts)
    payloads = frozenset(canonical_hash(ObservationRef.from_observation(o).to_dict()) for o in snapshot.observations)
    if len(versions) != len(snapshot.artifacts) or len(payloads) != len(snapshot.observations):
        errors.add("DUPLICATE_SOURCE_OR_OBSERVATION")
    if versions != snapshot.expected_artifact_versions or payloads != snapshot.expected_observation_payloads:
        errors.add("EXPECTED_INVENTORY_MISSING_OR_CHANGED")
    if versions != snapshot.authorized_artifact_versions:
        errors.add("AUTHORIZED_SOURCE_CATALOG_MISMATCH")
    limits = snapshot.limits
    if (len(snapshot.artifacts) > limits.max_artifacts or len(snapshot.observations) > limits.max_observations
            or len(snapshot.endpoints) > limits.max_endpoints
            or sum(len(a.content) for a in snapshot.artifacts) > limits.max_allowed_chars):
        errors.add("CONTEXT_LIMIT_EXCEEDED")
    if errors:
        return ContextBuild(None, tuple(sorted(errors)), inventory)
    handles = dict(snapshot.source_handles)
    if len(handles) != len(snapshot.source_handles) or set(handles) != versions:
        return ContextBuild(None, ("SOURCE_CATALOG_MISMATCH",), inventory)
    parsed = tuple((a, parse_source_text(a)) for a in snapshot.artifacts)
    inventory["source_parsing"] = [dict(artifact_version_id=a.artifact_version_id, status=p.status.value,
        issue_count=len(p.issues), issues_truncated=len(p.issues) > 4,
        issues=[dict(reason_code=i.reason_code, line_start=i.line_start, line_end=i.line_end) for i in p.issues[:4]])
        for a, p in parsed]
    if any(p.status != "COMPLETE" or p.issues for _, p in parsed):
        return ContextBuild(None, ("SOURCE_PARSE_INCOMPLETE",), inventory)
    spans = tuple((handles[a.artifact_version_id] + ":" + str(index), segment.evidence_span)
                  for a, p in parsed for index, segment in enumerate(p.segments, 1))
    context = SemanticContext(snapshot.workspace_id, snapshot.question, snapshot.artifacts,
        snapshot.observations, snapshot.endpoints, spans, snapshot.source_registry,
        snapshot.authorized_artifact_versions, snapshot.inventory_complete, snapshot.truncated,
        snapshot.queries, snapshot.revoked_observation_ids, snapshot.revoked_evidence_refs, limits)
    errors.update(context_errors(context))
    return ContextBuild(context, tuple(sorted(errors)), inventory)


def evidence_coverage(proposal, context):
    """Predeclared investigation sources, not semantic truth or Gold lookup.

    A negative/unknown conclusion cannot claim to cover the explicit source
    inventory if it omits a separate change source or inspected document.
    Never add a citation the model did not emit.
    """
    required = set(context.authorized_artifact_versions)
    citations = (*proposal.supporting_evidence, *proposal.counter_evidence)
    cited = {c.span.artifact_version_id for c in citations}
    missing = required - cited
    return dict(status="INSUFFICIENT" if missing else "COMPLETE", required_source_versions=sorted(required),
        cited_source_versions=sorted(cited), missing_source_versions=sorted(missing),
        cited_evidence_refs=sorted({c.span.evidence_ref for c in citations}),
        policy="EXPLICIT_HOST_INVESTIGATION_SOURCE_COVERAGE/v1", semantic_status="SEMANTIC_NOT_EVALUATED")
