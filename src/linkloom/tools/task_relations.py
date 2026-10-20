"""Workspace-bound read tools over existing M1, M2 discovery and P4 queries.

Only trusted composition supplies snapshots and a sidecar path. Optional semantic
analysis is separately authorized and bound by the host. No assessment is
persisted and no writable relation store is opened.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
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


_INVESTIGATION_STEP_STATUSES = frozenset({
    "SUPPORTED", "REFUTED", "UNKNOWN", "CONFLICT", "BLOCKED", "COMPLETED",
})


def _endpoint_key(endpoint):
    """Return a stable key for a typed endpoint without narrowing its identity."""
    return canonical_hash(endpoint.to_dict()) if hasattr(endpoint, "to_dict") else None


def _endpoints_match(predicate, expected, actual):
    """Match full typed endpoints, retaining SAME_TASK's symmetric contract."""
    if len(expected) != 2 or len(actual) != 2 or any(value is None for value in actual):
        return False
    expected_keys = tuple(_endpoint_key(value) for value in expected)
    actual_keys = tuple(_endpoint_key(value) for value in actual)
    if predicate == "SAME_TASK":
        return sorted(expected_keys) == sorted(actual_keys)
    return expected_keys == actual_keys


def _planned_step_relation(step, read):
    assessment = read.assessment
    return (assessment is not None and assessment.predicate == step.predicate
            and _endpoints_match(step.predicate, step.endpoints,
                                 (assessment.subject, assessment.object)))


def _relation_read_summary(read):
    """Expose the exact P4 identity/lifecycle fields beside top-level full reads."""
    assessment = read.assessment
    if assessment is None:
        return {
            "assessment_id": None,
            "predicate": None,
            "subject": None,
            "object": None,
            "persistent_lifecycle": read.persistent_lifecycle,
            "evidence_state": read.evidence_state,
            "current_supported": read.current_supported,
            "category": read.category,
            "blocking_reasons": list(read.blocking_reasons),
        }
    return {
        "assessment_id": assessment.assessment_id,
        "predicate": assessment.predicate,
        "subject": assessment.subject.to_dict() if assessment.subject else None,
        "object": assessment.object.to_dict() if assessment.object else None,
        "verdict": assessment.verdict,
        "persistent_lifecycle": read.persistent_lifecycle,
        "evidence_state": read.evidence_state,
        "current_supported": read.current_supported,
        "category": read.category,
        "blocking_reasons": list(read.blocking_reasons),
    }


def _relation_status(reads):
    """Project P4 reads without turning a non-supported verdict into support."""
    if not reads:
        return "BLOCKED", False, None, ["NO_MATCHING_P4_RELATION"]
    verdicts = {read.assessment.verdict for read in reads if read.assessment is not None}
    reasons = {reason for read in reads for reason in read.blocking_reasons}
    if "CONFLICT" in verdicts:
        status = "CONFLICT"
    elif len(verdicts) > 1 and "SUPPORTED" in verdicts:
        status = "CONFLICT"
        reasons.add("MULTIPLE_P4_RELATION_VERDICTS")
    elif "REFUTED" in verdicts:
        status = "REFUTED"
    elif "UNKNOWN" in verdicts:
        status = "UNKNOWN"
    elif any(read.current_supported for read in reads) and verdicts == {"SUPPORTED"}:
        status = "SUPPORTED"
    else:
        status = "BLOCKED"
    if status in {"REFUTED", "UNKNOWN", "CONFLICT"}:
        reasons.add("P4_VERDICT_" + status)
    elif status == "BLOCKED" and not reasons:
        reasons.add("P4_RELATION_NOT_CURRENT")
    lifecycle_values = sorted({read.persistent_lifecycle for read in reads})
    lifecycle = lifecycle_values[0] if len(lifecycle_values) == 1 else None
    return status, status == "SUPPORTED", lifecycle, sorted(reasons)


def _dependency_outcomes(step, rows, verified_results):
    """Expose dependency outcomes without promoting an unverified receipt."""
    outcomes = {}
    receipts = {receipt.step_id: receipt.outcome for receipt in verified_results}
    for dependency in step.depends_on:
        if dependency in rows:
            outcomes[dependency] = rows[dependency]["status"]
        elif dependency in receipts:
            outcomes[dependency] = receipts[dependency]
        else:
            outcomes[dependency] = "BLOCKED"
    return outcomes


def _base_investigation_step(step):
    return {
        "step_id": step.id,
        "kind": step.kind,
        "predicate": step.predicate,
        "status": "BLOCKED",
        "depends_on": list(step.depends_on),
        "dependency_outcomes": {},
        "blocking_reasons": [],
        "assessment_ids": [],
        "relation_reads": [],
        "endpoints": [endpoint.to_dict() for endpoint in step.endpoints],
        "current_supported": False,
        "lifecycle": None,
    }


def _comparison_endpoint(comparison):
    if comparison is None:
        return None
    return comparison.subject, comparison.object


def _comparison_matches(step, comparison_read):
    comparison = comparison_read.current_comparison or comparison_read.historical_comparison
    return comparison is not None and _endpoints_match(
        "REVISES_PLAN", step.endpoints, _comparison_endpoint(comparison))


def _comparison_premises(comparison_read):
    comparison = comparison_read.current_comparison or comparison_read.historical_comparison
    if comparison is None:
        return None, None
    return comparison.same_task_id, comparison.revision_id


def _investigation_steps(plan, snapshot, reads, comparison_reads, *, no_relation_reason=None):
    """Bind G's relation/compare steps to current P4 read-only evidence.

    The returned rows are a product view over existing typed reads. They never
    create receipts or relation records, and every endpoint comparison uses the
    complete immutable ObservationRef rather than an observation ID alone.
    """
    if plan is None:
        return []
    steps = tuple(step for step in plan.steps if step.kind in {"relation", "compare_dates"})
    rows = {}
    for step in steps:
        row = _base_investigation_step(step)
        dependency_outcomes = _dependency_outcomes(step, rows, plan.verified_results)
        row["dependency_outcomes"] = dependency_outcomes
        blockers = set()
        if step.workspace_id and step.workspace_id != snapshot.workspace_id:
            blockers.add("STEP_WORKSPACE_MISMATCH")
        if step.source_handles:
            # RelationQuery is intentionally unscoped. A source-filtered plan
            # step cannot consume that broad read without an exact host-bound
            # evidence restriction, so keep the step blocked.
            blockers.add("SOURCE_SCOPED_OPERATION_UNSUPPORTED")
        if step.time_scope != ("", ""):
            # P4 has no step-scope widening contract. The caller's unscoped
            # read must never be reused for a bounded plan step.
            blockers.update(("TIME_SCOPED_OPERATION_UNSUPPORTED", "TIME_SCOPE_NOT_WIDENED"))
        if not _step_source_bindings_current(snapshot, step):
            blockers.add("SOURCE_BINDING_NOT_CURRENT")
        matching = [read for read in reads if _planned_step_relation(step, read)]
        matching.sort(key=lambda read: read.assessment.assessment_id if read.assessment else "")
        row["assessment_ids"] = [read.assessment.assessment_id for read in matching if read.assessment]
        row["relation_reads"] = [_relation_read_summary(read) for read in matching]
        if matching:
            _, _, lifecycle, _ = _relation_status(matching)
            row["lifecycle"] = lifecycle
            row["current_supported"] = any(read.current_supported for read in matching)
        if no_relation_reason:
            blockers.add(no_relation_reason)
        if step.kind == "relation":
            for dependency, outcome in dependency_outcomes.items():
                dependency_step = next((candidate for candidate in plan.steps
                                        if candidate.id == dependency), None)
                allowed = ({"SUPPORTED"} if dependency_step and dependency_step.kind == "relation"
                           else {"SUPPORTED", "COMPLETED"})
                if outcome not in allowed:
                    blockers.add("DEPENDENCY_NOT_SUPPORTED:" + dependency)
            if not blockers:
                status, current_supported, lifecycle, reasons = _relation_status(matching)
                row["status"] = status
                row["current_supported"] = current_supported
                row["lifecycle"] = lifecycle
                blockers.update(reasons)
            else:
                row["status"] = "BLOCKED"
                if not matching and not no_relation_reason:
                    blockers.add("NO_MATCHING_P4_RELATION")
        else:
            # A comparison may only use the exact supported identity and
            # directed revision premises belonging to this step.
            identity_ids = []
            revision_ids = []
            identity_ready = False
            revision_ready = False
            for dependency in step.depends_on:
                dependency_step = next((candidate for candidate in plan.steps if candidate.id == dependency), None)
                dependency_row = rows.get(dependency)
                if dependency_step is None or dependency_row is None:
                    blockers.add("DEPENDENCY_RESULT_UNAVAILABLE:" + dependency)
                    continue
                if dependency_step.kind != "relation" or dependency_row["status"] != "SUPPORTED":
                    blockers.add("DEPENDENCY_NOT_SUPPORTED:" + dependency)
                    continue
                if dependency_step.predicate == "SAME_TASK" and _endpoints_match(
                        "SAME_TASK", step.endpoints, dependency_step.endpoints):
                    identity_ready = True
                    identity_ids.extend(dependency_row["assessment_ids"])
                elif dependency_step.predicate == "REVISES_PLAN" and tuple(
                        _endpoint_key(endpoint) for endpoint in dependency_step.endpoints) == tuple(
                        _endpoint_key(endpoint) for endpoint in step.endpoints):
                    revision_ready = True
                    revision_ids.extend(dependency_row["assessment_ids"])
                else:
                    blockers.add("DEPENDENCY_ENDPOINT_MISMATCH:" + dependency)
            if not identity_ready:
                blockers.add("SUPPORTED_SAME_TASK_PREMISE_REQUIRED")
            if not revision_ready:
                blockers.add("SUPPORTED_REVISES_PLAN_PREMISE_REQUIRED")
            row["assessment_ids"] = sorted(set(identity_ids + revision_ids))
            candidates = [comparison for comparison in comparison_reads
                          if _comparison_matches(step, comparison)]
            candidates.sort(key=lambda comparison: (
                (comparison.historical_comparison or comparison.current_comparison).comparison_id
                if (comparison.historical_comparison or comparison.current_comparison) else ""))
            row["comparisons"] = [comparison.to_dict() for comparison in candidates]
            if not blockers and candidates and identity_ready and revision_ready:
                exact = []
                for comparison in candidates:
                    same_id, revision_id = _comparison_premises(comparison)
                    if same_id in identity_ids and revision_id in revision_ids:
                        exact.append(comparison)
                if not exact:
                    blockers.add("COMPARISON_ENDPOINT_OR_PREMISE_MISMATCH")
                else:
                    current = next((comparison for comparison in exact if comparison.current_valid), None)
                    if current is not None:
                        row["status"] = "COMPLETED"
                        row["current_supported"] = True
                        row["comparison_current_valid"] = True
                    else:
                        for comparison in exact:
                            blockers.update(comparison.blocking_reasons)
                        blockers.add("P4_COMPARISON_NOT_CURRENT")
            elif not blockers and not candidates:
                blockers.add("NO_MATCHING_P4_COMPARISON")
            row.setdefault("comparison_current_valid", False)
            if row["status"] != "COMPLETED":
                row["status"] = "BLOCKED"
        row["blocking_reasons"] = sorted(blockers)
        if row["status"] not in _INVESTIGATION_STEP_STATUSES:
            row["status"] = "BLOCKED"
        rows[step.id] = row
    return [rows[step.id] for step in steps]


def _step_source_bindings_current(snapshot, step):
    """Small pure bridge for the module-level plan projection."""
    artifacts = {artifact.artifact_version_id: artifact for artifact in snapshot.artifacts}
    registry = snapshot.source_registry
    return all(span.workspace_id == snapshot.workspace_id
               and span.artifact_version_id in artifacts
               and span.verify(artifacts[span.artifact_version_id])
               and registry.has_evidence(snapshot.workspace_id, span.evidence_ref)
               and registry.evidence_hash(snapshot.workspace_id, span.evidence_ref) == span.content_hash
               and registry.evidence_version(snapshot.workspace_id, span.evidence_ref) == span.artifact_version_id
               for span in step.source_bindings)


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
                 *, relation_path: Path | str | None = None, semantic_analysis=None, inspection_plan=None,
                 execution_blocked_reason=None):
        if not isinstance(workspace_id, str) or not workspace_id.strip() or not callable(snapshot_provider):
            raise ValueError("explicit workspace and snapshot provider required")
        self.workspace_id = workspace_id
        self._snapshot_provider = snapshot_provider
        self._relation_path = Path(relation_path) if relation_path is not None else None
        self.semantic_analysis = semantic_analysis
        self.inspection_plan = inspection_plan
        self.execution_blocked_reason = execution_blocked_reason

    @property
    def planned_inspections(self):
        return tuple(s for s in self.inspection_plan.ready_steps
                     if s.kind == 'inspect' and s.time_scope == ('', '')) if self.inspection_plan else ()

    def for_run(self, run_id, artifact_store, question):
        if self.semantic_analysis is None:
            return self
        return TaskRelationTools(self.workspace_id, self._snapshot_provider, relation_path=self._relation_path,
            semantic_analysis=self.semantic_analysis.bind(run_id, artifact_store, question),
            inspection_plan=self.inspection_plan, execution_blocked_reason=self.execution_blocked_reason)

    def context_sources(self):
        """Host-only current source read; no model paths or cached currentness."""
        raw = self._snapshot(self.workspace_id)
        semantics = self.semantic_analysis
        owner = getattr(semantics, "owner", semantics)
        return raw, owner.snapshot_provider() if owner is not None else None

    def inspection_receipts_from_record(self, record):
        """Host bridge for a later G replan, using the actual durable tool row.

        No model-supplied receipt is an input to this method. The existing planner
        still requires its own result_verifier and revalidates current bindings.
        """
        from linkloom.runtime.models import ToolExecutionRecord
        from linkloom.semantic_ingestion.task_relation_question_models import VerifiedStepResult
        if (not isinstance(record, ToolExecutionRecord) or record.tool_id != 'inspect_task_context'
                or record.status != 'completed' or record.result.get('status') != 'ok'
                or record.arguments.get('workspace_id') != self.workspace_id):
            raise ValueError('UNVERIFIED_INSPECTION_RECORD')
        output = record.result['value']
        snapshot = self._snapshot(self.workspace_id)
        artifacts = {a.artifact_version_id:a for a in snapshot.artifacts}
        current = {(o.observation if isinstance(o,TaskObservationRead) else o).observation_id:
                   (o.observation if isinstance(o,TaskObservationRead) else o) for o in snapshot.observations}
        digest = canonical_hash({k:v for k,v in output.items() if k != 'investigation_receipts'})
        steps = {s.id:s for s in self.planned_inspections}
        receipts = []
        for data in output.get('investigation_receipts', ()):
            receipt = VerifiedStepResult(**data)
            step = steps.get(receipt.step_id)
            if (step is None or receipt.plan_id != self.inspection_plan.plan_id
                    or receipt.result_sha256 != digest or receipt.outcome != 'COMPLETED'
                    or receipt.receipt_id != 'inspection_' + canonical_hash([step.execution_key,digest])):
                raise ValueError('UNVERIFIED_INSPECTION_RECEIPT')
            ref = step.endpoints[0]
            observation = current.get(ref.observation_id)
            span = observation.provenance if observation else None
            if (observation is None or ObservationRef.from_observation(observation) != ref
                    or span.artifact_version_id not in artifacts
                    or not observation.verify(artifacts[span.artifact_version_id])
                    or snapshot.source_registry.evidence_hash(self.workspace_id,span.evidence_ref) != span.content_hash
                    or snapshot.source_registry.evidence_version(self.workspace_id,span.evidence_ref) != span.artifact_version_id
                    or not self._bound_spans_current(snapshot,step)):
                raise ValueError('INSPECTION_SOURCE_NOT_CURRENT')
            receipts.append(receipt)
        return tuple(receipts)

    def _bound_spans_current(self, snapshot, step):
        artifacts = {a.artifact_version_id:a for a in snapshot.artifacts}
        registry = snapshot.source_registry
        return all(s.workspace_id==self.workspace_id and s.artifact_version_id in artifacts
            and s.verify(artifacts[s.artifact_version_id])
            and registry.has_evidence(self.workspace_id,s.evidence_ref)
            and registry.evidence_hash(self.workspace_id,s.evidence_ref)==s.content_hash
            and registry.evidence_version(self.workspace_id,s.evidence_ref)==s.artifact_version_id
            for s in step.source_bindings)

    def _semantic_boundary(self):
        if self.semantic_analysis is None:
            return dict(SEMANTIC_BOUNDARY)
        return dict(SEMANTIC_BOUNDARY, status="CONNECTED_PENDING_SEMANTIC_REVIEW",
                    consumer="A SemanticProposer; host-owned complete raw source snapshot",
                    semantic_status="SEMANTIC_NOT_EVALUATED", publishable=False)

    def _snapshot(self, workspace_id: str):
        if workspace_id != self.workspace_id:
            raise ValueError("workspace is not authorized")
        if self.execution_blocked_reason:
            raise ValueError(self.execution_blocked_reason)
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
        planned = self.planned_inspections
        if any(not self._bound_spans_current(snapshot,step) for step in planned):
            raise ValueError('INSPECTION_SOURCE_NOT_CURRENT')
        # Ready inspections expose only their bound targets, including mixed plans.
        if planned:
            allowed = {s.endpoints[0].observation_id for s in planned}
            if anchor_ids is not None and not set(anchor_ids) <= allowed:
                raise ValueError('INSPECTION_TARGET_NOT_PLANNED')
            anchor_ids = tuple(anchor_ids) if anchor_ids is not None else tuple(sorted(allowed))
            snapshot = replace(snapshot, observations=tuple(o for o in snapshot.observations
                if (o.observation if isinstance(o, TaskObservationRead) else o).observation_id in anchor_ids))
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
        while len(json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf8")) > (24 if planned else 32) * 1024:
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
        if planned:
            from linkloom.semantic_ingestion.task_relation_question_models import VerifiedStepResult
            digest = canonical_hash(output)
            refs = [row['ref'] for row in observations]
            output['investigation_receipts'] = [asdict(VerifiedStepResult(
                self.inspection_plan.plan_id, step.id, 'inspection_' + canonical_hash([step.execution_key,digest]),
                digest, 'Read-only inspection completed; task observations remain UNCONFIRMED.', 'COMPLETED'))
                for step in planned if step.endpoints[0].to_dict() in refs]
            if len(json.dumps(output,ensure_ascii=False).encode('utf8')) > 32*1024:
                raise ValueError('task inspection receipt output budget exceeded')
        return output

    def query_task_relations(self, workspace_id, *, observation_id=None, include_history=False, as_of=None):
        snapshot = self._snapshot(workspace_id)
        when = date.fromisoformat(as_of) if as_of is not None else None
        output = {"schema_version": "task-relations-query/v1", "workspace_id": workspace_id,
                  "status": "NO_VERIFIED_RELATIONS", "relations": [], "plans": [],
                  "investigation_steps": [],
                  "truncated": False, "max_relations": 64, "max_plans": 4,
                  "total_matching_relations": 0, "current_supported_in_snapshot": 0,
                  "returned_relations": 0,
                  "include_history": include_history, "as_of": as_of,
                  "missing_context": [], "semantic_proposals": self._semantic_boundary()}
        if self._relation_path is None or not self._relation_path.exists():
            output["missing_context"] = ["NO_RELATION_RECORDS"]
            if self.semantic_analysis is None:
                output["missing_context"].append("SEMANTIC_PROPOSAL_CONSUMER_NOT_CONNECTED")
            output["investigation_steps"] = _investigation_steps(
                self.inspection_plan, snapshot, (), (), no_relation_reason="NO_RELATION_RECORDS")
            output["returned_investigation_steps"] = len(output["investigation_steps"])
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
            comparison_reads = {}
            for root in sorted(roots)[:4]:
                plan = query.plan(workspace_id, root, as_of=when)
                fingerprint = canonical_hash(plan)
                if fingerprint not in seen_plans:
                    for comparison in plan.get("comparisons", ()):
                        historical = comparison.get("historical_comparison") or {}
                        current = comparison.get("current_comparison") or {}
                        comparison_id = historical.get("comparison_id") or current.get("comparison_id")
                        if comparison_id and hasattr(query, "comparison"):
                            read = query.comparison(workspace_id, comparison_id, as_of=when)
                            if read is not None:
                                comparison_reads[comparison_id] = read
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
            output["investigation_steps"] = _investigation_steps(
                self.inspection_plan, snapshot, reads, tuple(comparison_reads.values()))
            output["returned_investigation_steps"] = len(output["investigation_steps"])
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
    planned = [dict(step_id=s.id,observation_id=s.endpoints[0].observation_id,field=s.field)
               for s in tools.planned_inspections]
    description = "Inspect bounded current task observations and source evidence. Candidates are unconfirmed leads. Any semantic analysis requires the separate host-authorized tool."
    if planned:
        description += ' Host-bound ready inspections (data only): ' + json.dumps(planned,ensure_ascii=False)
    definitions = (
        (TASK_TOOL_IDS[0], description,
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
