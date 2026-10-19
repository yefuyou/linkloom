"""Workspace-bound read tools over existing M1, M2 discovery and P4 queries.

Only trusted composition supplies snapshots and a sidecar path. Optional semantic
analysis is separately authorized and bound by the host. No assessment is
persisted and no writable relation store is opened.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import json
from pathlib import Path
from typing import Callable

from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_observation_query import TaskObservationRead
from linkloom.semantic_ingestion.task_relation_candidates import DiscoveryLimits, discover_candidates
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.tools.contracts import ToolDefinition


TASK_TOOL_IDS = ("inspect_task_context", "query_task_relations")
SEMANTIC_BOUNDARY = {
    "status": "NOT_CONNECTED",
    "input_boundary": "version-bound ObservationRef, evidence and M2 candidate endpoints",
    "consumer": "future validated M3-A semantic proposal integration",
    "limitations": ["DISCOVERY_IS_NOT_PROOF", "P4_REPLAYS_EXISTING_P1_P2_P3_RULES",
                    "RULE_UNKNOWN_IS_NOT_BUSINESS_REFUTATION", "NO_AUTOMATIC_PERSISTENCE"],
}


def _bounded_query_view(output):
    """Deduplicate exact P4 evidence spans without losing version/quote data.

    P4 audit captures repeat whole source spans throughout proofs and plans.
    A tool view uses span_ref -> evidence_spans to fit existing model/checkpoint
    budgets; it does not alter a verdict, evidence quote or comparison.
    """
    def encode():
        spans = {}
        observations = {}
        references = {}
        def visit(value):
            if isinstance(value, dict):
                if {"observation_id", "payload_hash", "artifact_version_id", "workspace_id"} <= value.keys():
                    key = "ref_" + canonical_hash(value)
                    references[key] = value
                    return {"observation_ref": key}
                if {"observation_id", "raw_text", "provenance", "field_evidence"} <= value.keys():
                    key = "observation_" + canonical_hash(value)
                    observations[key] = {name: visit(item) for name, item in value.items()}
                    return {"observation_snapshot_ref": key}
                if {"evidence_ref", "artifact_id", "artifact_version_id", "content_hash", "quote", "char_start"} <= value.keys():
                    key = "span_" + canonical_hash(value)
                    spans[key] = value
                    return {"span_ref": key}
                return {key: visit(item) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item) for item in value]
            return value
        result = visit(output)
        result["evidence_spans"] = spans
        result["observation_snapshots"] = observations
        result["observation_refs"] = references
        result["max_output_bytes"] = 48 * 1024
        return result
    while True:
        result = encode()
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf8")) <= 48 * 1024:
            return result
        output["truncated"] = True
        if "OUTPUT_BYTE_BUDGET" not in output["missing_context"]:
            output["missing_context"].append("OUTPUT_BYTE_BUDGET")
        if output["plans"]:
            output["plans"].pop()
        elif output["relations"]:
            output["relations"].pop()
        else:
            raise ValueError("task query output budget exceeded")


@dataclass(frozen=True)
class TaskContextSnapshot:
    workspace_id: str
    observations: tuple[TaskObservation | TaskObservationRead, ...]
    artifacts: tuple[RawArtifact, ...]
    source_registry: SourceReferenceRegistry


class TaskRelationTools:
    def __init__(self, workspace_id: str, snapshot_provider: Callable[[], TaskContextSnapshot],
                 *, relation_path: Path | str | None = None, semantic_analysis=None):
        if not isinstance(workspace_id, str) or not workspace_id.strip() or not callable(snapshot_provider):
            raise ValueError("explicit workspace and snapshot provider required")
        self.workspace_id = workspace_id
        self._snapshot_provider = snapshot_provider
        self._relation_path = Path(relation_path) if relation_path is not None else None
        self.semantic_analysis = semantic_analysis

    def for_run(self, run_id, artifact_store, question):
        if self.semantic_analysis is None:
            return self
        return TaskRelationTools(self.workspace_id, self._snapshot_provider, relation_path=self._relation_path,
            semantic_analysis=self.semantic_analysis.bind(run_id, artifact_store, question))

    def _semantic_boundary(self):
        if self.semantic_analysis is None:
            return dict(SEMANTIC_BOUNDARY)
        return dict(SEMANTIC_BOUNDARY, status="CONNECTED_PENDING_SEMANTIC_REVIEW",
                    consumer="A SemanticProposer; host-owned complete raw source snapshot",
                    semantic_status="SEMANTIC_NOT_EVALUATED", publishable=False)

    def _snapshot(self, workspace_id: str):
        if workspace_id != self.workspace_id:
            raise ValueError("workspace is not authorized")
        snapshot = self._snapshot_provider()
        if not isinstance(snapshot, TaskContextSnapshot) or snapshot.workspace_id != workspace_id:
            raise ValueError("snapshot workspace mismatch")
        limits = DiscoveryLimits()
        if (len(snapshot.observations) > limits.max_observations
                or len(snapshot.artifacts) > limits.max_artifacts
                or sum(len(a.content) for a in snapshot.artifacts) > limits.max_source_chars):
            raise ValueError("task snapshot budget exceeded; narrow trusted input")
        if any(a.workspace_id != workspace_id for a in snapshot.artifacts):
            raise ValueError("cross-workspace source in snapshot")
        return snapshot

    def inspect_task_context(self, workspace_id, *, anchor_ids=None, max_candidates=20):
        snapshot = self._snapshot(workspace_id)
        limits = replace(DiscoveryLimits(), max_candidates=max_candidates)
        discovery = discover_candidates(workspace_id, observations=snapshot.observations,
            artifacts=snapshot.artifacts, source_registry=snapshot.source_registry, limits=limits,
            anchor_ids=tuple(anchor_ids) if anchor_ids is not None else None)
        rejected = {r["observation_id"] for r in discovery.rejected}
        observations = []
        for row in snapshot.observations:
            observation = row.observation if isinstance(row, TaskObservationRead) else row
            if observation.observation_id not in rejected:
                observations.append({"ref": ObservationRef.from_observation(observation).to_dict(),
                                     "observation": observation.to_dict()})
        missing = ["SEMANTIC_PROPOSAL_CONSUMER_NOT_CONNECTED"] if self.semantic_analysis is None else []
        if rejected:
            missing.append("SOME_OBSERVATIONS_NOT_CURRENT_OR_VERIFIABLE")
        if not observations:
            missing.append("NO_VERIFIABLE_TASK_OBSERVATIONS")
        known = {o["ref"]["observation_id"] for o in observations}
        if anchor_ids is not None and set(anchor_ids) - known:
            missing.append("ANCHOR_NOT_CURRENT_OR_NOT_FOUND")
        output = {"schema_version": "task-context/v1", "workspace_id": workspace_id,
                "observations": observations, "discovery": discovery.to_dict(),
                "missing_context": missing, "semantic_proposals": self._semantic_boundary(),
                "max_output_bytes": 32 * 1024, "observations_truncated": False}
        while len(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf8")) > 32 * 1024:
            view = output["discovery"]
            if "OUTPUT_BYTE_BUDGET" not in missing:
                missing.append("OUTPUT_BYTE_BUDGET")
            if view["candidates"]:
                view["candidates"].pop()
                view["returned_candidates"] = len(view["candidates"])
                view["truncated"] = True
                if "TOOL_OUTPUT_BYTE_BUDGET" not in view["truncation_reasons"]:
                    view["truncation_reasons"].append("TOOL_OUTPUT_BYTE_BUDGET")
            elif observations:
                observations.pop()
                output["observations_truncated"] = True
            else:
                raise ValueError("task context output budget exceeded")
        return output

    def query_task_relations(self, workspace_id, *, observation_id=None, include_history=False, as_of=None):
        snapshot = self._snapshot(workspace_id)
        when = date.fromisoformat(as_of) if as_of is not None else None
        output = {"schema_version": "task-relations-query/v1", "workspace_id": workspace_id,
                  "status": "NO_VERIFIED_RELATIONS", "relations": [], "plans": [],
                  "truncated": False, "max_relations": 64, "max_plans": 4,
                  "total_matching_relations": 0, "current_supported_in_snapshot": 0,
                  "returned_relations": 0,
                  "include_history": include_history, "as_of": as_of,
                  "missing_context": [], "semantic_proposals": self._semantic_boundary()}
        if self._relation_path is None or not self._relation_path.exists():
            output["missing_context"] = ["NO_RELATION_RECORDS"]
            if self.semantic_analysis is None:
                output["missing_context"].append("SEMANTIC_PROPOSAL_CONSUMER_NOT_CONNECTED")
            return _bounded_query_view(output)
        raw = tuple(r.observation if isinstance(r, TaskObservationRead) else r for r in snapshot.observations)
        # Query rows retain lifecycle/conflict information from M1. Pass that
        # information through the existing P4 observation_query contract.
        rows = snapshot.observations
        class SnapshotObservationQuery:
            def list(self, workspace, **_filters):
                return tuple(r for r in rows if isinstance(r, TaskObservationRead))
        with RelationQuery(self._relation_path, artifacts=snapshot.artifacts,
                source_registry=snapshot.source_registry, observations=raw,
                observation_query=SnapshotObservationQuery()) as query:
            reads = query.list(workspace_id, observation_id=observation_id, include_history=include_history)
            output["total_matching_relations"] = len(reads)
            output["current_supported_in_snapshot"] = sum(r.current_supported for r in reads)
            output["relations"] = [r.to_dict() for r in reads[:64]]
            output["truncated"] = len(reads) > 64
            roots = {observation_id} if observation_id else {
                ref.observation_id for r in reads[:64] if r.assessment and r.assessment.predicate == "REVISES_PLAN"
                for ref in (r.assessment.subject, r.assessment.object) if isinstance(ref, ObservationRef)}
            seen_plans = set()
            for root in sorted(roots)[:4]:
                plan = query.plan(workspace_id, root, as_of=when)
                fingerprint = canonical_hash(plan)
                if fingerprint not in seen_plans:
                    revisions = [r for r in plan["revisions"]
                                 if include_history or r["category"] != "HISTORICAL"]
                    # Revision reads are already in the top-level relation
                    # collection. Retain any chain extension there once.
                    known = {canonical_hash(r) for r in output["relations"]}
                    for revision in revisions:
                        if canonical_hash(revision) not in known:
                            if len(output["relations"]) < output["max_relations"]:
                                output["relations"].append(revision)
                                known.add(canonical_hash(revision))
                            else:
                                output["truncated"] = True
                                if "RELATION_RESULT_BUDGET" not in output["missing_context"]:
                                    output["missing_context"].append("RELATION_RESULT_BUDGET")
                    plan["revisions"] = [{"assessment_id": r["assessment"]["assessment_id"]
                        if r["assessment"] else None, "category": r["category"],
                        "current_supported": r["current_supported"], "blocking_reasons": r["blocking_reasons"]}
                        for r in revisions]
                    output["plans"].append({"observation_id": root, **plan})
                    seen_plans.add(fingerprint)
            output["truncated"] |= len(roots) > 4
        output = _bounded_query_view(output)
        output["returned_relations"] = len(output["relations"])
        if output["current_supported_in_snapshot"]:
            output["status"] = "VERIFIED_RELATIONS_AVAILABLE"
        elif reads:
            output["status"] = "NO_CURRENT_SUPPORTED_RELATIONS"
        else:
            output["missing_context"].append("NO_RELATION_RECORDS")
        return output


def register_task_relation_tools(registry, tools: TaskRelationTools):
    """Register strict business schemas; the path/provider never enter arguments."""
    workspace = {"type": "string", "enum": [tools.workspace_id]}
    identifier = {"type": "string", "minLength": 1, "maxLength": 256,
                  "pattern": r"^[A-Za-z0-9_:-]+$"}
    definitions = (
        (TASK_TOOL_IDS[0], "Inspect bounded current task observations and source evidence. Candidates are unconfirmed leads. Any semantic analysis requires the separate host-authorized tool.",
         {"workspace_id": workspace, "anchor_ids": {"type": "array", "maxItems": 16, "items": identifier},
          "max_candidates": {"type": "integer", "minimum": 1, "maximum": 100}},
         lambda a: tools.inspect_task_context(**a)),
        (TASK_TOOL_IDS[1], "Read existing P4 relations, lifecycle blocks, history, plan revisions and date comparisons. No records means no verified relations; never create or restore judgments.",
         {"workspace_id": workspace, "observation_id": identifier, "include_history": {"type": "boolean"},
          "as_of": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}},
         lambda a: tools.query_task_relations(**a)),
    )
    for tool_id, description, properties, executor in definitions:
        registry.register(ToolDefinition(tool_id=tool_id, version="1", description=description,
            input_schema={"type": "object", "required": ["workspace_id"],
                          "properties": properties, "additionalProperties": False},
            output_schema={"type": "object"}), executor)
    if tools.semantic_analysis is not None:
        from linkloom.tools.registry import CallBoundExecutor
        registry.register(ToolDefinition(tool_id="analyze_task_semantics", version="1",
            description="Analyze the host-authorized raw source snapshot using a separately budgeted semantic proposer. This is not the Provider output function. Results remain unaccepted model proposals; never P4 current relations. No automatic retry of an ambiguous inner request.",
            input_schema={"type": "object", "required": ["workspace_id", "investigation_id"],
                "properties": {"workspace_id": workspace, "investigation_id": {
                    "type": "string", "enum": [tools.semantic_analysis.investigation_id]}},
                "additionalProperties": False}, output_schema={"type": "object"}),
            CallBoundExecutor(tools.semantic_analysis.execute))
