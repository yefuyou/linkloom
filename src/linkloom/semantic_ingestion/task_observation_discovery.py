"""Bounded, read-only M1 scheduling over the existing semantic extractor.

The host owns authorization and current-source reads. Nothing here registers,
persists, merges business tasks, retries calls, or asserts semantic completeness.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
import hashlib
import json

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.document_text import DocumentTextSegment, _labelled_date, parse_source_text
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor, SemanticExtractionContext, TaskDiscoveryContext, TaskDiscoveryProfile,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.task_observation import TaskObservation, TaskProposal, build_task_observation
from linkloom.semantic_ingestion.timestamped_text import ParseStatus
from linkloom.semantic_ingestion.task_discovery_veto import non_asserted


COVERAGE_POLICY = "bounded-document-single-pass/v1"


@dataclass(frozen=True, slots=True)
class DiscoveryLimits:
    max_sources: int = 8
    max_chars: int = 8000
    max_bytes: int = 16000
    max_calls: int = 3

    def __post_init__(self):
        for name, ceiling in (("max_sources", 64), ("max_chars", 16000),
                              ("max_bytes", 24000), ("max_calls", 64)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= ceiling:
                raise ValueError(f"{name} must be between 1 and {ceiling}")


@dataclass(frozen=True, slots=True)
class DiscoverySource:
    artifact_id: str
    artifact_version_id: str
    status: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryFailure:
    artifact_id: str
    reason: str
    proposal_index: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryGroup:
    """A model proposal's projections, never an accepted business identity."""
    group_id: str
    observation_ids: tuple[str, ...]
    source_evidence_refs: tuple[str, ...]
    unbound_fields: tuple[str, ...]
    status: str = "MODEL_PROPOSED"
    validation_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateCoverage:
    """Structural candidate coverage, not a semantic judgment of task absence."""
    artifact_id: str
    artifact_version_id: str
    evidence_ref: str
    verified_description_parts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskDiscoveryResult:
    valid_observations: tuple[TaskObservation, ...]
    partial_observations: tuple[TaskObservation, ...]
    failures: tuple[DiscoveryFailure, ...]
    sources: tuple[DiscoverySource, ...]
    groups: tuple[DiscoveryGroup, ...]
    extractions: tuple[SemanticExtractionResult, ...]
    coverage_complete: bool
    coverage_policy: str = COVERAGE_POLICY
    semantic_exhaustive: bool = False
    candidate_coverage: tuple[DiscoveryCandidateCoverage, ...] = ()

    @property
    def receipts(self):
        return tuple(result.receipt for result in self.extractions)

    @property
    def unprocessed_sources(self):
        return tuple(row for row in self.sources if row.status == "UNPROCESSED")

    def to_dict(self):
        return {
            "coverage_policy": self.coverage_policy, "coverage_complete": self.coverage_complete,
            "semantic_exhaustive": self.semantic_exhaustive,
            "valid_observations": [o.to_dict() for o in self.valid_observations],
            "partial_observations": [o.to_dict() for o in self.partial_observations],
            "failures": [asdict(f) for f in self.failures],
            "sources": [asdict(s) for s in self.sources],
            "unprocessed_sources": [asdict(s) for s in self.unprocessed_sources],
            "groups": [asdict(g) for g in self.groups],
            "extractions": [r.to_dict() for r in self.extractions],
            "receipts": [r.to_dict() for r in self.receipts],
            "candidate_coverage": [asdict(row) for row in self.candidate_coverage],
        }


def _registered(artifact, segments, registry):
    return all(registry.has_evidence(artifact.workspace_id, s.evidence_span.evidence_ref)
               and registry.evidence_hash(artifact.workspace_id, s.evidence_span.evidence_ref) == artifact.content_hash
               and registry.evidence_version(artifact.workspace_id, s.evidence_span.evidence_ref) == artifact.artifact_version_id
               and s.evidence_span.verify(artifact) for s in segments)


def _window(artifact):
    # The existing ordinary-document parser creates an exact whole-source span.
    # Preserve the ORIGINAL artifact/version, not a synthetic rewritten artifact.
    from linkloom.semantic_ingestion.artifacts import EvidenceSpan, evidence_ref_for, line_range_for, message_id_for, segment_id_for
    digest = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
    message = message_id_for(artifact.artifact_version_id, 0)
    segment = segment_id_for(artifact.artifact_version_id, message, 0, len(artifact.content), digest)
    first, last = line_range_for(artifact.content, 0, len(artifact.content))
    span = EvidenceSpan(artifact.workspace_id, artifact.artifact_id, artifact.artifact_version_id,
                        artifact.source_ref, artifact.content_hash, 0, message, segment,
                        evidence_ref_for(segment), 0, len(artifact.content), first, last, artifact.content, digest)
    return DocumentTextSegment(0, message, segment, artifact.content, span,
        _labelled_date(artifact.content, "文档日期|Document date"),
        _labelled_date(artifact.content, "会议日期|事件日期|Meeting date|Event date"))


class TaskObservationDiscoveryService:
    def __init__(self, extractor: ProviderNeutralSemanticExtractor, *,
                 source_registry: SourceReferenceRegistry,
                 current_source: Callable[[str, str], RawArtifact | None],
                 limits: DiscoveryLimits = DiscoveryLimits(),
                 task_profile: TaskDiscoveryProfile | None = None):
        if not isinstance(extractor, ProviderNeutralSemanticExtractor) or extractor.max_attempts != 1:
            raise ValueError("discovery requires the existing extractor with exactly one attempt")
        if not isinstance(source_registry, SourceReferenceRegistry) or not callable(current_source):
            raise TypeError("host registry and current-source loader are required")
        if not isinstance(limits, DiscoveryLimits):
            raise TypeError("limits must be DiscoveryLimits")
        if task_profile is not None and not isinstance(task_profile, TaskDiscoveryProfile):
            raise TypeError("task_profile must be TaskDiscoveryProfile or None")
        self.extractor, self.registry, self.current_source, self.limits = extractor, source_registry, current_source, limits
        # Explicit opt-in preserves frozen REPLAY and closed Canary requests.
        self.task_profile = task_profile

    def _current(self, artifact, segments):
        try:
            current = self.current_source(artifact.workspace_id, artifact.artifact_id)
            return (isinstance(current, RawArtifact) and current.workspace_id == artifact.workspace_id
                    and current.artifact_id == artifact.artifact_id
                    and current.artifact_version_id == artifact.artifact_version_id
                    and current.content_hash == artifact.content_hash and current.source_ref == artifact.source_ref
                    and _registered(artifact, segments, self.registry))
        except Exception:
            return False

    def discover(self, artifacts: tuple[RawArtifact, ...], *, workspace_id: str) -> TaskDiscoveryResult:
        if not isinstance(artifacts, tuple) or any(not isinstance(a, RawArtifact) for a in artifacts):
            raise TypeError("artifacts must be a bounded tuple of RawArtifact")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id is required")
        if len(artifacts) > self.limits.max_sources:
            raise ValueError("source inventory exceeds max_sources; submit an explicit bounded batch")
        sources, failures, extractions, groups, observations = [], [], [], [], []
        seen, admitted = set(), []
        complete = True
        context = SemanticExtractionContext(workspace_id, FrozenRelationResolver(()))
        for artifact in artifacts:
            def source_row(status, reason=None):
                sources.append(DiscoverySource(artifact.artifact_id, artifact.artifact_version_id, status, reason))

            key = (artifact.workspace_id, artifact.artifact_version_id)
            if key in seen:
                source_row("DUPLICATE", "EXACT_INPUT_DUPLICATE")
                continue
            seen.add(key)
            reason = None
            parsed = None
            if artifact.workspace_id != workspace_id:
                reason = "WORKSPACE_MISMATCH"
            elif len(artifact.content) > self.limits.max_chars or len(artifact.content.encode("utf-8")) > self.limits.max_bytes:
                reason = "SOURCE_TOO_LARGE"
            else:
                parsed = parse_source_text(artifact)
                if not self._current(artifact, parsed.segments):
                    reason = "SOURCE_CHANGED_OR_UNAVAILABLE"
                elif len(extractions) >= self.limits.max_calls:
                    reason = "CALL_BUDGET_EXHAUSTED"
            if reason:
                source_row("UNPROCESSED", reason)
                complete = False
                continue
            if parsed.status != ParseStatus.COMPLETE:
                failures.append(DiscoveryFailure(artifact.artifact_id, "SOURCE_PARSE_INCOMPLETE"))
                source_row("UNPROCESSED", "SOURCE_PARSE_INCOMPLETE")
                complete = False
                continue
            window = parsed.segments[0] if len(parsed.segments) == 1 else _window(artifact)
            source_context = (replace(context, task_discovery=TaskDiscoveryContext(artifact, self.task_profile))
                              if self.task_profile is not None else context)
            result = self.extractor.extract(window, source_context)
            extractions.append(result)
            if not self._current(artifact, parsed.segments):
                reason = "SOURCE_CHANGED_OR_UNAVAILABLE"
            elif result.failure_code:
                reason = result.failure_code.value
            else:
                reason = None
            if reason:
                failures.append(DiscoveryFailure(artifact.artifact_id, reason))
                source_row("FAILED", reason)
                complete = False
                continue
            if len(result.claims) == 20:
                complete = False
                failures.append(DiscoveryFailure(artifact.artifact_id, "CLAIM_LIMIT_REACHED"))
            found, proposed_groups, rejected = _project(result, artifact, parsed.segments)
            admitted.append((artifact, parsed.segments))
            observations.extend(found)
            groups.extend(proposed_groups)
            failures.extend(rejected)
            if rejected:
                complete = False
            source_row("OBSERVATIONS_FOUND" if found else "NO_TASKS_DETECTED")
        # A later call/host callback can revoke an earlier source in this batch.
        stale = {a.artifact_version_id for a, segments in admitted if not self._current(a, segments)}
        if stale:
            complete = False
            observations = [o for o in observations if o.provenance.artifact_version_id not in stale]
            for index, row in enumerate(sources):
                if row.artifact_version_id in stale and row.status != "DUPLICATE":
                    sources[index] = replace(row, status="FAILED", reason="SOURCE_CHANGED_OR_UNAVAILABLE")
                    failures.append(DiscoveryFailure(row.artifact_id, "SOURCE_CHANGED_OR_UNAVAILABLE"))
        released = {o.observation_id for o in observations}
        groups = [replace(g, observation_ids=tuple(oid for oid in g.observation_ids if oid in released),
                          validation_issues=("OBSERVATIONS_WITHHELD",)
                          if any(oid not in released for oid in g.observation_ids) else ()) for g in groups]
        candidate_coverage = tuple(DiscoveryCandidateCoverage(
            a.artifact_id, a.artifact_version_id, segment.evidence_span.evidence_ref,
            tuple(dict.fromkeys(e.quote for o in observations
                if o.provenance.evidence_ref == segment.evidence_span.evidence_ref
                for e in o.field_evidence if e.field == "description")))
            for a, segments in admitted if a.artifact_version_id not in stale for segment in segments)
        return TaskDiscoveryResult(tuple(o for o in observations if o.validation_status == "VALID"),
            tuple(o for o in observations if o.validation_status == "PARTIAL"), tuple(failures),
            tuple(sources), tuple(groups), tuple(extractions), complete,
            candidate_coverage=candidate_coverage)


def _project(result, artifact, segments):
    found, groups, failures = {}, [], []
    conflicted, proposals_seen = set(), set()
    for index, claim in enumerate(result.claims):
        if claim.claim_type.value not in {"PLAN", "ACTION"}:
            continue
        proposal = claim.task

        def fail(reason):
            failures.append(DiscoveryFailure(artifact.artifact_id, reason, index))

        if proposal is None:
            fail("TASK_PROPOSAL_MISSING")
            continue
        signature = json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True)
        if signature in proposals_seen:
            fail("DUPLICATE_TASK_PROPOSAL")
            continue
        proposals_seen.add(signature)
        if (not proposal.description_parts or artifact.content.count(proposal.text) != 1
                or any(part not in proposal.text for part in proposal.description_parts)):
            fail("TASK_TEXT_OR_DESCRIPTION_UNGROUNDED_OR_AMBIGUOUS")
            continue
        start = artifact.content.index(proposal.text)
        end = start + len(proposal.text)
        if non_asserted(artifact.content, proposal.description_parts, start, end):
            fail("NON_ASSERTED_TASK")
            continue
        projections, refs, bound_parts = [], [], set()
        for segment in segments:
            span = segment.evidence_span
            left, right = max(start, span.char_start), min(end, span.char_end)
            if left >= right:
                continue
            refs.append(span.evidence_ref)
            fragment = artifact.content[left:right]
            parts = tuple(part for part in proposal.description_parts if part in fragment)
            if not parts:
                continue
            bound_parts.update(parts)
            projected = TaskProposal(fragment, parts, proposal.owner, proposal.deadline_text,
                                     proposal.project, proposal.vendor)
            observation = build_task_observation(projected, segment, artifact)
            if observation.validation_status == "INVALID" or not observation.verify(artifact):
                fail("M1_VALIDATION_FAILED")
                continue
            previous = found.get(observation.observation_id)
            if previous is not None and previous != observation:
                conflicted.add(observation.observation_id)
                fail("OBSERVATION_EXTRACTION_CONFLICT")
            else:
                found[observation.observation_id] = observation
            projections.append(observation)
        if set(proposal.description_parts) != bound_parts:
            fail("DESCRIPTION_NOT_BOUND_TO_CANONICAL_MESSAGE")
        if not projections:
            fail("NO_VERIFIED_TASK_FRAGMENT")
            continue
        unbound = tuple(name for name in ("owner", "project", "vendor", "deadline_text")
                        if getattr(proposal, name) is not None and
                        not any(getattr(o, name) == getattr(proposal, name) for o in projections))
        group_id = hashlib.sha256((artifact.artifact_version_id + signature).encode("utf-8")).hexdigest()
        groups.append(DiscoveryGroup("task_discovery_group_" + group_id,
            tuple(o.observation_id for o in projections), tuple(refs), unbound))
    # An immutable location cannot silently acquire competing extractions.
    return [o for key, o in found.items() if key not in conflicted], groups, failures
