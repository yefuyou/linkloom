"""Read-only typed queries over existing Supporting Only candidate persistence."""
from __future__ import annotations

from dataclasses import dataclass

from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.semantic_ingestion.task_observation import TaskObservation


@dataclass(frozen=True, slots=True)
class TaskObservationRead:
    observation: TaskObservation
    candidate_ids: tuple[str, ...]
    source_status: str
    extraction_conflict: bool = False


class TaskObservationQuery:
    """Never initializes a database or changes candidate/source lifecycle state.

    Project matching is normalized text equality, not entity resolution. Pass an
    already workspace-authorized store; an empty registry never implies current.
    """
    def __init__(self, store: TemporalDecisionStore):
        self.store = store

    def list(self, workspace_id: str, *, source_id: str | None = None,
             project: str | None = None, source_version: str | None = None,
             include_history: bool = False) -> tuple[TaskObservationRead, ...]:
        for name, value in (("workspace_id", workspace_id), ("source_id", source_id),
                            ("project", project), ("source_version", source_version)):
            if value is None and name != "workspace_id":
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if type(include_history) is not bool:
            raise ValueError("include_history must be boolean")
        registry = self.store.source_registry
        groups = {}
        for row in self.store.list_semantic_candidate_rows(
            workspace_id, artifact_id=source_id, artifact_version_id=source_version,
            candidate_source_kind="SOURCE_INGESTION",
        ):
            candidate = deserialize_candidate(str(row["payload_json"]))
            observation = candidate.task_observation
            if observation is None:
                continue
            encoded, fingerprint = serialize_candidate(candidate)
            if encoded != row["payload_json"] or fingerprint != row["candidate_fingerprint"]:
                raise ValueError("stored task candidate failed its integrity check")
            if project is not None and (observation.project is None or
                normalize_temporal_component(observation.project) != normalize_temporal_component(project)):
                continue
            span = observation.provenance
            current = (row["workflow_state"] == "SUPPORTING_ONLY"
                       and registry.has_evidence(workspace_id, span.evidence_ref)
                       and registry.evidence_hash(workspace_id, span.evidence_ref) == span.content_hash
                       and registry.evidence_version(workspace_id, span.evidence_ref) == span.artifact_version_id)
            if not current and not include_history:
                continue
            key = observation.observation_id
            groups.setdefault(key, []).append((observation, str(row["candidate_id"]), current))
        results = []
        for variants in groups.values():
            signatures = {}
            for observation, candidate_id, current in variants:
                data = observation.to_dict()
                data.pop("recorded_at")
                signature = repr(data)
                signatures.setdefault(signature, []).append((observation, candidate_id, current))
            for matching in signatures.values():
                observation = matching[0][0]
                results.append(TaskObservationRead(observation, tuple(x[1] for x in matching),
                               "CURRENT" if all(x[2] for x in matching) else "NOT_CURRENT",
                               extraction_conflict=len(signatures) > 1))
        return tuple(sorted(results, key=lambda row: (row.observation.provenance.artifact_id,
                                                     row.observation.provenance.ordinal,
                                                     row.observation.observation_id)))
