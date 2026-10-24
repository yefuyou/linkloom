"""Runtime bridge for the P4 deterministic multi-agent workflow.

The bridge owns the only VaultReader-backed callbacks exposed to specialists.
Agents receive opaque refs; this module resolves them back to a verified
Scanner-v1 document only at the tool boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
import time
from typing import Any, Callable

from linkloom.agents.coordinator import Coordinator
from linkloom.agents.claim_grounding import (
    verified_dependent_leader_values, verified_subgoal_conflict,
)
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.registry import create_default_registry
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.memory_candidate import (
    AgentMemoryBuildStatus,
    AgentMemoryResolutionContext,
    MemoryCandidateBuilder,
    WorkspaceSubjectRegistry,
)
from linkloom.agents.runtime_evidence import RuntimeEvidenceCatalogAdapter
from linkloom.agents.team_decision import (
    CLAIM_PROPOSAL_SCHEMA_VERSION,
    SCHEMA_VERSION,
    TeamDecisionResult,
)
from linkloom.context.assembler import ContextAssembler, ContextBundle, ContextSourceType
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.tool import DecisionMemorySearchTool
from linkloom.evidence_identity import passage_evidence_id
from linkloom.indexing import EmbeddingProvider, IndexUpdateCoordinator
from linkloom.loader import VaultReader
from linkloom.retrieval import find_relation_candidates_with_evidence
from linkloom.retrieval_v2 import RetrievedEvidence, RuntimeRetrievalBackend
from linkloom.retrieval_v2.subgoal_query_strategy import SubgoalQueryStrategy
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError, ToolArgumentValidationError
from linkloom.runtime.models import RuntimeState
from linkloom.runtime.task_plan import (
    TaskPlan, assess_task_plan, verified_dependency_entities,
    verified_plan_subgoal_values,
)
from linkloom.runtime.team_decision_planner import plan_team_decision_query
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.read_tools import _require_relative_note_ref
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.contracts import ToolResult
from linkloom.tools.runtime import ToolCheckpointCallback, ToolPreconditionError, create_retrieval_tool_runtime


class RuntimeAgentEventSink:
    """Adapt Coordinator callbacks to the existing P3 OptionalTracer."""

    def __init__(self, tracer: Any) -> None:
        self.tracer = tracer

    def emit(self, event_type: str, actor: str, status: str, **kwargs: Any) -> Any:
        # EventEmitter deliberately has a smaller actor enum than agent IDs.
        mapped_actor = actor if actor in {"runtime", "service", "provider", "human"} else "agent"
        return self.tracer.emit(
            event_type,
            status,
            actor=mapped_actor,
            attributes=kwargs.get("attributes"),
            error=kwargs.get("error"),
            node_name=kwargs.get("node_name"),
        )


class RuntimeAgentAdapter:
    """Build one isolated Coordinator and its verified read-only tool set."""

    def __init__(
        self,
        vault_root: Path | str,
        index_path: Path | str,
        *,
        retrieval_mode: str | None = None,
        decision_memory_store: TemporalDecisionStore | None = None,
        decision_memory_store_unavailable: bool = False,
        workspace_id: str | None = None,
        embedder: EmbeddingProvider | None = None,
        index_update_coordinator: IndexUpdateCoordinator | None = None,
        context_assembler: ContextAssembler | None = None,
        temporal_decision_lookup: Callable[[str | None, str | None, str | None], dict[str, Any]] | None = None,
        temporal_claim_validator: Callable[..., dict[str, Any]] | None = None,
        decision_memory_enabled: bool = True,
        task_relation_tools: Any = None,
        context_runtime_config: Any = None,
    ) -> None:
        self.reader = VaultReader(vault_root=vault_root, index_path=index_path)
        self._task_relation_tools = task_relation_tools
        self._context_runtime_config = context_runtime_config
        if context_runtime_config is not None:
            from linkloom.runtime.context_engineering import ContextRuntimeConfig
            if not isinstance(context_runtime_config, ContextRuntimeConfig):
                raise ValidationError("Explicit ContextRuntimeConfig required.")
            if context_assembler is None:
                context_assembler = ContextAssembler(policy=context_runtime_config.policy, budget=context_runtime_config.local_budget)
        self.workspace_id = workspace_id or self.reader.vault_root_fingerprint
        selected_mode = retrieval_mode or os.environ.get(
            "LINKLOOM_RETRIEVAL_MODE",
            "hybrid",
        )
        self._retrieval_backend = RuntimeRetrievalBackend(
            workspace_id=self.workspace_id,
            document_provider=self._read_documents,
            mode=selected_mode,
            embedder=embedder,
            index_update_coordinator=index_update_coordinator,
            index_version=2,
        )
        if not isinstance(decision_memory_enabled, bool):
            raise ValidationError("decision_memory_enabled must be boolean.")
        self._decision_memory_enabled = decision_memory_enabled
        self._decision_memory_store = None if not decision_memory_enabled else (
            decision_memory_store
            if decision_memory_store is not None
            else TemporalDecisionStore(":memory:")
        )
        self._decision_memory_store_unavailable = decision_memory_store_unavailable
        self._decision_memory_tool = None if not decision_memory_enabled else DecisionMemorySearchTool(
            self._decision_memory_store,
            authorized_workspace_id=self.workspace_id,
        )
        self._temporal_decision_lookup = temporal_decision_lookup
        self._temporal_claim_validator = temporal_claim_validator
        self._temporal_review_observation: tuple[
            str, dict[str, Any], dict[str, dict[str, Any]], set[str]
        ] | None = None
        if context_assembler is not None and not isinstance(context_assembler, ContextAssembler):
            raise ValueError("context_assembler must be a ContextAssembler or None")
        self._context_assembler = context_assembler or ContextAssembler()
        self._integration_event_sink: RuntimeAgentEventSink | None = None
        self._evidence_by_id: dict[str, dict[str, Any]] = {}
        self._search_evidence_ids: set[str] = set()
        self._candidate_docs: list[dict[str, Any]] = []
        self._active_runtime_state: RuntimeState | None = None
        self._latest_retrieved_evidence: tuple[RetrievedEvidence, ...] = ()
        self._latest_decision_records = ()
        self._latest_scope_path: str | None = None
        self._last_context_bundle: ContextBundle | None = None
        self._last_context_assembly_ms = 0.0
        self._task_plan: TaskPlan | None = None
        self._subgoal_query_strategy = SubgoalQueryStrategy()

    def _read_documents(self):
        # Re-reading at every tool boundary preserves Scanner hash freshness.
        return self.reader.read_notes()

    def _document_for_ref(self, ref: str, documents: list[Any]) -> Any:
        evidence = self._evidence_by_id.get(ref)
        relative_path = evidence.get("relative_path") if evidence else None
        if relative_path is None:
            aliases = {f"note_ref_{idx}": doc.relative_path for idx, doc in enumerate(documents)}
            relative_path = aliases.get(ref, ref)
        for document in documents:
            if document.relative_path == relative_path:
                return document
        raise ValueError("verified note reference was not found in the current index")

    def _search_notes(self, query: str, source_context: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        scope_path = source_context.get("scope_path")
        if scope_path is not None and not isinstance(scope_path, str):
            raise ValueError("source_context.scope_path must be text or null")
        if self._task_plan is not None:
            query = self._planned_search_query(source_context)
        result = self._retrieval_backend.search(
            query,
            top_k=limit,
            scope_path=scope_path,
        )
        evidence_items = [
            self._retrieved_evidence(value, rank=index + 1)
            for index, value in enumerate(result.evidence)
        ]
        bundle = self._assemble_context(
            query=query,
            retrieved_evidence=evidence_items,
            decision_memory=self._latest_decision_records,
            scope_path=scope_path,
        )
        selected_ids = {
            item.item_id
            for item in bundle.selected
            if item.source_type is ContextSourceType.EVIDENCE
        }
        values = [
            value
            for value in result.evidence
            if value["evidence_id"] in selected_ids
        ]
        self._latest_retrieved_evidence = tuple(
            item for item in evidence_items if item.evidence_id in selected_ids
        )
        self._latest_scope_path = scope_path
        self._evidence_by_id.update({value["evidence_id"]: value for value in values})
        self._search_evidence_ids.update(value["evidence_id"] for value in values)
        if self._integration_event_sink is not None:
            observation = result.observation
            self._integration_event_sink.emit(
                "retrieval.completed",
                "retrieval_agent",
                "ok",
                attributes={
                    "retrieval_mode": observation.retrieval_mode,
                    "scope_path": observation.scope_path,
                    "retrieval_latency_ms": observation.retrieval_latency_ms,
                    "candidate_count": observation.candidate_count,
                    "top_k": observation.top_k,
                    "index_version": observation.index_version,
                    "retrieval_stage_timings_ms": dict(observation.stage_timings_ms),
                },
            )
        return values

    def _planned_search_query(self, source_context: dict[str, Any]) -> str:
        """Resolve a bounded query only from durable, verified parent reads."""
        state = self._active_runtime_state
        plan = self._task_plan
        if state is None or plan is None:
            raise ValidationError("TaskPlan requires the active durable run.")
        pending = sorted(
            (record for record in state.tool_ledger
             if record.run_id == state.run_id and record.tool_id == "search_notes"
             and record.status == "pending"),
            key=lambda record: (record.sequence, record.call_id),
        )
        if not pending:
            raise ValidationError("TaskPlan search requires a durable pending call.")
        call = pending[-1]
        if call.arguments.get("source_context") != source_context:
            raise ValidationError("TaskPlan search arguments differ from the durable call.")
        subgoal_id = source_context.get("subgoal_id")
        if not isinstance(subgoal_id, str):
            raise ValidationError("TaskPlan search requires subgoal_id.")
        try:
            subgoal = plan.get(subgoal_id)
        except ValidationError as error:
            raise ToolArgumentValidationError(
                "Search named an unknown TaskPlan subgoal.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.subgoal_id"},
            ) from error
        assessment = assess_task_plan(
            plan, state.tool_ledger, run_id=state.run_id,
            task_id=call.task_id or "", agent_id=call.agent_id or "",
            expected_workspace_id=self.workspace_id,
        )
        variant = source_context.get("query_variant", "primary")
        if variant not in {"primary", "alternate"}:
            raise ToolArgumentValidationError(
                "TaskPlan query_variant is invalid.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.query_variant"},
            )
        read_values = assessment.evidence_by_subgoal[subgoal_id]
        supported_values = verified_plan_subgoal_values(
            plan, subgoal_id, assessment.evidence_by_subgoal,
        )
        if supported_values is None and len(subgoal.depends_on) == 1:
            parent_id = subgoal.depends_on[0]
            supported_values = verified_dependent_leader_values(
                plan.get(parent_id).question, subgoal.question,
                assessment.evidence_by_subgoal[parent_id], read_values,
            )
        if read_values and not (
            variant == "alternate"
            and supported_values == ()
            and not verified_subgoal_conflict(subgoal.question, read_values)
        ):
            raise ToolArgumentValidationError(
                "TaskPlan subgoal already has a verified relation read.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.subgoal_id"},
            )
        proposed = source_context.get("dependency_entities", {})
        if not isinstance(proposed, dict):
            raise ToolArgumentValidationError(
                "TaskPlan dependency_entities must be an object.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.dependency_entities"},
            )
        if any(not assessment.evidence_by_subgoal[parent] for parent in subgoal.depends_on):
            raise ToolPreconditionError("Dependent search requires a verified parent read.")
        try:
            entities = verified_dependency_entities(plan, assessment, subgoal_id, proposed)
        except ValidationError as error:
            raise ToolArgumentValidationError(
                "Dependency entity does not match the verified parent relation.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.dependency_entities"},
            ) from error
        if subgoal.depends_on:
            queries = self._subgoal_query_strategy.build_queries(
                subgoal.question, entities, alternate_subgoal=subgoal.alternate_question,
            )
        else:
            queries = tuple(dict.fromkeys(
                item for item in (subgoal.question, subgoal.alternate_question) if item
            ))
        previous = [
            record for record in state.tool_ledger
            if (record.run_id, record.task_id, record.agent_id)
            == (state.run_id, call.task_id, call.agent_id)
            and record.tool_id == "search_notes" and record.status in {"completed", "failed"}
            and isinstance(record.arguments.get("source_context"), dict)
            and record.arguments["source_context"].get("subgoal_id") == subgoal_id
        ]
        # A pre-search contract rejection did not execute retrieval. Permit
        # one corrected primary call; all source identity and plan checks run
        # again, and a second failure remains bounded by the ModelLoop.
        corrected_primary = (
            variant == "primary" and len(previous) == 1
            and previous[0].status == "failed"
            and isinstance(previous[0].error, dict)
            and previous[0].error.get("code") == "TOOL_INVALID_ARGUMENTS"
            and isinstance(previous[0].error.get("details"), dict)
            and previous[0].error["details"].get("reason") == "semantic_mismatch"
            and previous[0].error["details"].get("path") == "$.source_context"
        )
        if (variant == "primary" and previous and not corrected_primary) or (
            variant == "alternate"
            and (len(queries) < 2 or len(previous) != 1
                 or previous[0].arguments["source_context"].get("query_variant", "primary") != "primary")
        ):
            raise ToolArgumentValidationError(
                "TaskPlan permits one primary and one alternate search per subgoal.",
                details={"reason": "task_plan_semantic_mismatch", "path": "$.source_context.query_variant"},
            )
        return queries[0 if variant == "primary" else 1]

    def _search_decision_memory(
        self,
        query: str,
        as_of: str | None,
        limit: int,
        subject: str | None = None,
        relation: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._decision_memory_enabled:
            raise ValidationError("Decision Memory is disabled for this source investigation.")
        if self._temporal_decision_lookup is not None:
            if (subject is None) != (relation is None):
                raise ValidationError("Reviewed temporal lookup is unavailable for this run.")
            view = self._temporal_decision_lookup(subject, relation, as_of)
            if view.get("workspace_id") != self.workspace_id:
                raise ValidationError("Reviewed temporal lookup crossed the active workspace.")
            # Runtime checkpoints reserve `authorization` for secret-bearing
            # HTTP credentials. Expose the product's review receipt under a
            # business-specific name without changing its trusted identity.
            view = deepcopy(view)
            for field in ("effective", "history", "transitions", "conflicting"):
                rows = view.get(field)
                for row in rows if isinstance(rows, list) else [rows]:
                    if isinstance(row, dict) and "authorization" in row:
                        row["review_receipt"] = row.pop("authorization")
            return [{"kind": "reviewed_temporal_state", "grounding_role": "reviewed_state_not_source_evidence",
                     "view": view}]
        if subject is not None or relation is not None:
            raise ValidationError("Reviewed temporal lookup is unavailable for this run.")
        result = self._decision_memory_tool.search(
            workspace=self.workspace_id,
            query=query,
            as_of=as_of,
            limit=limit,
        )
        records = tuple(
            record
            for value in result.values
            if isinstance(value.get("decision_id"), str)
            for record in [
                self._decision_memory_store.get_decision(
                    self.workspace_id,
                    value["decision_id"],
                )
            ]
            if record is not None
        )
        bundle = self._assemble_context(
            query=query,
            retrieved_evidence=self._latest_retrieved_evidence,
            decision_memory=records,
            scope_path=self._latest_scope_path,
            temporal_query=as_of is not None,
        )
        selected_ids = {
            item.item_id
            for item in bundle.selected
            if item.source_type is ContextSourceType.DECISION_MEMORY
        }
        values = [
            value for value in result.values if value["decision_id"] in selected_ids
        ]
        self._latest_decision_records = tuple(
            record for record in records if record.decision_id in selected_ids
        )
        if self._integration_event_sink is not None:
            observation = result.observation
            self._integration_event_sink.emit(
                "decision_memory.completed",
                "retrieval_agent",
                "ok",
                attributes={
                    "workspace_id": observation.workspace_id,
                    "memory_hit_count": len(values),
                    "memory_latency_ms": observation.memory_latency_ms,
                },
            )
        return values

    def _verify_temporal_claim(
        self, workspace_id: str, claim: dict[str, Any],
        evidence_by_ref: dict[str, dict[str, Any]], read_ids: set[str],
    ) -> dict[str, Any]:
        validator = self._temporal_claim_validator
        if validator is None:
            raise ValidationError("Reviewed temporal validation is unavailable for this run.")
        view = validator(workspace_id, claim, evidence_by_ref, read_ids)
        self._temporal_review_observation = (
            workspace_id, deepcopy(claim), deepcopy(evidence_by_ref), set(read_ids),
        )
        return view

    def _assemble_context(
        self,
        *,
        query: str,
        retrieved_evidence: tuple[RetrievedEvidence, ...] | list[RetrievedEvidence],
        decision_memory=(),
        scope_path: str | None = None,
        temporal_query: bool | None = None,
        evidence_groups=(),
        task_subgoals=(),
        request_usage=None,
        required_evidence_limit=None,
    ) -> ContextBundle:
        started = time.perf_counter()
        bundle = self._context_assembler.assemble(
            workspace_id=self.workspace_id,
            query=query,
            retrieved_evidence=retrieved_evidence,
            decision_memory=decision_memory,
            runtime_state=self._active_runtime_state,
            scope_path=scope_path,
            temporal_query=temporal_query,
            evidence_groups=evidence_groups,
            task_subgoals=task_subgoals,
            request_usage=request_usage,
            required_evidence_limit=required_evidence_limit,
        )
        self._last_context_assembly_ms = (time.perf_counter() - started) * 1000.0
        self._last_context_bundle = bundle
        if self._integration_event_sink is not None:
            self._integration_event_sink.emit(
                "context.assembled",
                "retrieval_agent",
                "ok",
                attributes={
                    "workspace_id": bundle.workspace_id,
                    "selected_count": len(bundle.selected),
                    "dropped_count": len(bundle.dropped),
                    "estimated_tokens": bundle.estimated_tokens,
                    "context_assembly_ms": self._last_context_assembly_ms,
                    "selected_items": [
                        {
                            "item_id": item.item_id,
                            "source_type": item.source_type.value,
                            "estimated_tokens": item.estimated_tokens,
                            "source_refs": list(item.source_refs),
                        }
                        for item in bundle.selected
                    ],
                    "dropped_items": [
                        {
                            "item_id": item.item_id,
                            "source_type": item.source_type.value,
                            "reason": item.reason.value,
                            "estimated_tokens": item.estimated_tokens,
                        }
                        for item in bundle.dropped
                    ],
                },
            )
        return bundle

    def _retrieved_evidence(
        self,
        value: dict[str, Any],
        *,
        rank: int,
    ) -> RetrievedEvidence:
        relative_path = value["relative_path"]
        evidence_id = value["evidence_id"]
        return RetrievedEvidence(
            evidence_id=evidence_id,
            source_ref=str(value.get("source_ref") or relative_path),
            logical_path=value.get("logical_path") or f"/{relative_path}",
            score=float(value.get("score", 0.0)),
            rank=int(value.get("rank", rank)),
            retrieval_channel=str(value.get("retrieval_channel", "current")),
            resource_id=relative_path,
            metadata={**value, "text": value.get("quote", ""), "workspace_id": self.workspace_id},
        )

    def _read_verified_note(self, ref: str) -> dict[str, Any]:
        documents = self._read_documents()
        document = self._document_for_ref(ref, documents)
        evidence = self._evidence_by_id.get(ref)
        if evidence is not None:
            quote = evidence.get("quote")
            if (
                evidence.get("content_sha256") != document.content_sha256
                or evidence.get("relative_path") != document.relative_path
                or not isinstance(quote, str)
                or not quote
                or quote not in document.content
                or hashlib.sha256(quote.encode("utf-8")).hexdigest()
                != evidence.get("quote_sha256")
            ):
                raise ValueError("verified evidence no longer matches its source version")
            # Return the verified evidence record, not raw note content.
            return dict(evidence)
        return {
            "ref": ref,
            "relative_path": document.relative_path,
            "content_sha256": document.content_sha256,
            "status": "verified",
        }

    def _read_verified_note_for_tool(self, ref: str) -> dict[str, Any]:
        """Expose only evidence IDs returned by this run's verified search."""
        _require_relative_note_ref(ref)
        if ref not in self._search_evidence_ids or ref not in self._evidence_by_id:
            from linkloom.tools.runtime import EvidenceReferenceError
            candidates = sorted(self._search_evidence_ids.intersection(self._evidence_by_id))
            raise EvidenceReferenceError(
                "verified evidence reference was not returned by search_notes",
                available_evidence_ids=tuple(candidates[:32]),
                candidates_truncated=len(candidates) > 32,
            )
        return self._read_verified_note(ref)

    def _build_pair_signals(self, left_ref: str, right_ref: str) -> list[dict[str, Any]]:
        documents = self._read_documents()
        left = self._document_for_ref(left_ref, documents)
        right = self._document_for_ref(right_ref, documents)
        candidates, evidence = find_relation_candidates_with_evidence(
            documents, max_candidates=100
        )
        self._evidence_by_id.update({item.evidence_id: item.to_dict() for item in evidence})
        for candidate in candidates:
            paths = {candidate.left["relative_path"], candidate.right["relative_path"]}
            if paths == {left.relative_path, right.relative_path}:
                return [
                    {
                        "kind": item["kind"],
                        "value": item["value"],
                        "weight": item["weight"],
                        "left_ref": left_ref,
                        "right_ref": right_ref,
                        "status": "candidate",
                    }
                    for item in candidate.score_breakdown
                ]
        return []

    def _validate_evidence(self, evidence_ref: str) -> dict[str, Any]:
        evidence = self._evidence_by_id.get(evidence_ref)
        if not evidence:
            return {"status": "fail", "reason": "evidence_ref_not_found", "ref": evidence_ref}
        documents = self._read_documents()
        try:
            document = self._document_for_ref(evidence_ref, documents)
        except ValueError:
            return {"status": "fail", "reason": "source_not_found", "ref": evidence_ref}
        quote = evidence.get("quote", "")
        valid = (
            evidence.get("status") == "verified"
            and isinstance(quote, str)
            and bool(quote)
            and quote in document.content
            and evidence.get("content_sha256") == document.content_sha256
        )
        return {"status": "pass" if valid else "fail", "ref": evidence_ref}

    def _validate_schema(self, result_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not result_type or not isinstance(payload, dict):
            return {"status": "fail", "reason": "schema_invalid"}
        if any(key.casefold() in {"gold", "expected", "label", "mutation_approval"} for key in payload):
            return {"status": "fail", "reason": "forbidden_field"}
        if self._temporal_review_observation is not None:
            workspace_id, claim, evidence, read_ids = self._temporal_review_observation
            try:
                view = self._temporal_claim_validator(workspace_id, claim, evidence, read_ids)
                decision = TeamDecisionResult.from_dict(payload)
                effective = view["effective"]
                clauses = [
                    f"截至 {claim['as_of']}，{claim['subject']}的{claim['relation']}为{effective['value']}"
                ]
                if claim["supersedes_id"] is not None:
                    previous = next(item for item in view["history"]
                                    if item["decision_id"] == claim["supersedes_id"])
                    clauses.append(
                        f"{effective['value']}于{effective['valid_from']}替代{previous['value']}"
                    )
                refs = list(dict.fromkeys([
                    *claim["evidence_ids"], *claim["prior_evidence_ids"],
                ]))
                if (decision.decision["value"] != "；".join(clauses)
                        or decision.decision["evidence_refs"] != refs
                        or decision.evidence_refs != refs
                        or decision.decision["status"] not in {"approved", "partial"}):
                    return {"status": "fail", "reason": "temporal_projection_mismatch"}
            except (ValidationError, StopIteration, KeyError, TypeError, ValueError):
                return {"status": "fail", "reason": "temporal_review_failed"}
        return {"status": "pass", "result_type": result_type}

    def _restore_evidence(self, state: RuntimeState, task_id: str, documents: list[Any]) -> None:
        """Rebuild the ephemeral ref map from durable results, without tool replay."""
        by_path = {document.relative_path: document for document in documents}
        for record in state.tool_ledger:
            if (record.run_id, record.task_id, record.agent_id) != (state.run_id, task_id, "retrieval_agent"):
                raise ValidationError("Evidence restore scope does not match retrieval.")
            if record.status != "completed" or record.result is None:
                continue
            result = ToolResult.from_dict(record.result)
            if (result.call_id, result.tool_id, result.status) != (record.call_id, record.tool_id, "ok"):
                raise ValidationError("Evidence restore result identity is invalid.")
            values = result.value if isinstance(result.value, list) else [result.value]
            for value in values:
                if not isinstance(value, dict) or "evidence_id" not in value:
                    continue
                document = by_path.get(value.get("relative_path"))
                quote = value.get("quote")
                line_start = value.get("line_start")
                line_end = value.get("line_end")
                if (document is None or value.get("content_sha256") != document.content_sha256
                        or value.get("status") != "verified" or not isinstance(quote, str)
                        or not quote or quote not in document.content
                        or hashlib.sha256(quote.encode("utf-8")).hexdigest() != value.get("quote_sha256")
                        or value.get("workspace_id", self.workspace_id) != self.workspace_id
                        or not isinstance(line_start, int) or isinstance(line_start, bool)
                        or not isinstance(line_end, int) or isinstance(line_end, bool)
                        or line_start < 1 or line_end != line_start
                        or line_start > len(document.content.splitlines())
                        or document.content.splitlines()[line_start - 1] != quote):
                    raise ValidationError("Durable evidence no longer matches its verified source.")
                expected_ref = passage_evidence_id(
                    workspace_id=self.workspace_id,
                    resource_id=document.relative_path,
                    document_id=document.relative_path,
                    logical_path=f"/{document.relative_path}",
                    source_ref=document.relative_path,
                    content_hash=document.content_sha256,
                    line_start=line_start,
                    line_end=line_end,
                    quote_hash=value["quote_sha256"],
                )
                if value["evidence_id"] != expected_ref:
                    raise ValidationError("Durable evidence identity does not match this workspace.")
                self._evidence_by_id[value["evidence_id"]] = dict(value)
                if record.tool_id == "search_notes":
                    self._search_evidence_ids.add(value["evidence_id"])

    def _capture_agent_memory_candidate(
        self,
        result: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Persist only a grounded, reviewer-passed Team Decision candidate."""

        def response(
            status: str,
            reason: str,
            *,
            candidate_id: str | None = None,
            review_required: bool = False,
        ) -> dict[str, Any]:
            return {
                "status": status,
                "candidate_id": candidate_id,
                "review_required": review_required,
                "reason": reason,
            }

        if result.get("status") != "completed":
            return response("BLOCKED", "ANSWER_NOT_COMPLETED")
        review = result.get("review")
        payload = result.get("result")
        if (
            not isinstance(review, dict)
            or review.get("decision") != "evidence_sufficient"
            or not isinstance(payload, dict)
            or payload.get("review_decision") != "evidence_sufficient"
        ):
            return response("BLOCKED", "REVIEW_NOT_PASSED")
        if self._decision_memory_store_unavailable:
            return response("CAPTURE_FAILED", "PERSISTENT_STORE_UNAVAILABLE")
        if self._decision_memory_store.db_path == ":memory:":
            return response("BLOCKED", "PERSISTENT_STORE_REQUIRED")

        try:
            team_decision = TeamDecisionResult.from_dict(payload.get("team_decision"))
            documents = self._read_documents()
            passages = tuple(
                self._evidence_by_id[evidence_ref]
                for evidence_ref in team_decision.evidence_refs
                if evidence_ref in self._evidence_by_id
            )
            catalog_result = RuntimeEvidenceCatalogAdapter().build(
                run_id=run_id,
                workspace_id=self.workspace_id,
                passages=passages,
                documents=documents,
                source_registry=self._decision_memory_store.source_registry,
            )
            built = MemoryCandidateBuilder().build(
                team_decision,
                workspace_id=self.workspace_id,
                run_id=run_id,
                evidence_catalog=catalog_result.catalog,
                source_registry=self._decision_memory_store.source_registry,
                relation_resolver=FrozenRelationResolver(()),
                resolution_context=AgentMemoryResolutionContext(
                    self.workspace_id,
                    WorkspaceSubjectRegistry(self.workspace_id),
                ),
            )
            if built.status is AgentMemoryBuildStatus.BLOCKED or built.candidate is None:
                reason = (
                    built.reason_codes[0].value
                    if built.reason_codes
                    else "CANDIDATE_BUILD_BLOCKED"
                )
                return response("BLOCKED", reason)
            if any("UNGROUNDED" in reason.value for reason in built.candidate.review_reasons):
                return response("BLOCKED", "CANDIDATE_NOT_GROUNDED")

            assessment = SemanticDecisionMaterializer(self._decision_memory_store).capture(
                built.candidate,
                expected_workspace_id=self.workspace_id,
            )
            reason = (
                built.candidate.review_reasons[0].value
                if built.candidate.review_reasons
                else assessment.workflow_state.value
            )
            return response(
                "CAPTURED",
                reason,
                candidate_id=built.candidate.candidate_id,
                review_required=True,
            )
        except Exception:
            # Candidate persistence is post-answer and must never rewrite the
            # successful Team Decision outcome or leak exception details.
            return response("CAPTURE_FAILED", "CANDIDATE_CAPTURE_FAILED")

    def run(
        self,
        run_id: str,
        workflow: str,
        query: str,
        source_context: dict[str, Any],
        task_plan: dict[str, Any] | None = None,
        tracer: Any = None,
        max_total_steps: int = 12,
        injected_memory: list[dict[str, Any]] | None = None,
        tool_ledger: ToolExecutionLedger | None = None,
        tool_checkpoint_callback: ToolCheckpointCallback | None = None,
        model: Any | None = None,
        initial_state: RuntimeState | None = None,
        artifact_store: ModelArtifactStore | None = None,
        state_checkpoint_callback: Callable[[RuntimeState], None] | None = None,
        *,
        resume_model_loop: bool = False,
        retrieval_task_id: str | None = None,
        prompt_profile: str = "zero-v1",
        verified_evidence_context_limit: int = 8,
        final_protocol: str = SCHEMA_VERSION,
    ) -> dict[str, Any]:
        task_relations = workflow == "task_relations"
        context_runtime = None
        if task_relations:
            if self._task_relation_tools is None or self._task_relation_tools.workspace_id != self.workspace_id:
                raise ValidationError("Task relation tools require the active authorized workspace.")
            source_context = {**source_context, "workspace_id": self.workspace_id}
            task_relation_tools = self._task_relation_tools.for_run(run_id, artifact_store, query)
            source_context["semantic_analysis_enabled"] = task_relation_tools.semantic_analysis is not None
            if self._context_runtime_config is not None:
                from linkloom.runtime.context_engineering import RuntimeWorkingContext
                context_runtime = RuntimeWorkingContext(self._context_runtime_config, task_relation_tools,
                    artifact_store, query, self._assemble_context)
        if not callable(getattr(model, "decide", None)) and not callable(
            getattr(model, "complete", None)
        ):
            raise ValidationError("RuntimeAgentAdapter requires an injected model.")
        if not isinstance(initial_state, RuntimeState):
            raise ValidationError("RuntimeAgentAdapter requires an initial RuntimeState.")
        if initial_state.run_id != run_id:
            raise ValidationError("RuntimeAgentAdapter state identity does not match run_id.")
        if not isinstance(artifact_store, ModelArtifactStore):
            raise ValidationError("RuntimeAgentAdapter requires a ModelArtifactStore.")
        if not callable(state_checkpoint_callback):
            raise ValidationError("RuntimeAgentAdapter requires a RuntimeState checkpoint callback.")
        if task_plan is not None and workflow != "team_decision":
            raise ValidationError("TaskPlan is available only for team_decision.")
        if final_protocol not in {SCHEMA_VERSION, CLAIM_PROPOSAL_SCHEMA_VERSION}:
            raise ValidationError("RuntimeAgentAdapter Final protocol is unsupported.")
        if final_protocol == CLAIM_PROPOSAL_SCHEMA_VERSION and workflow != "team_decision":
            raise ValidationError("Typed claims Final is only available for team_decision.")
        self._task_plan = TaskPlan.from_dict(task_plan) if task_plan is not None else None
        if workflow == "team_decision" and self._task_plan is None:
            self._task_plan = plan_team_decision_query(query)
        if resume_model_loop != (retrieval_task_id is not None):
            raise ValidationError("Resume mode requires a canonical retrieval task identity.")
        if retrieval_task_id is not None and not retrieval_task_id.strip():
            raise ValidationError("Resume task identity must be non-empty.")
        memory_hints = tuple(
            {
                "memory_id": item["memory_id"], "kind": item["value"]["kind"],
                "key": item["key"], "text": item["value"]["text"],
                "source_refs": item["source_refs"], "expires_at": item.get("expires_at"),
                "review_state": item.get("review_state"),
            }
            for item in (injected_memory or [])
            if item.get("scope") == "project"
            and isinstance(item.get("key"), str)
            and isinstance(item.get("value"), dict)
            and item["value"].get("kind") in {"preference", "terminology"}
            and isinstance(item["value"].get("text"), str)
            and item.get("value_sha256") == hashlib.sha256(
                json.dumps(item["value"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        if resume_model_loop:
            # Bind the run-level query to the original durable instruction;
            # continuation must not silently accept an edited request file.
            first = min(initial_state.model_executions, key=lambda record: record.sequence)
            if not first.request_ref or not first.request_sha256:
                raise ValidationError("Resume requires the original durable model request.")
            payload = artifact_store.read(first.request_ref, expected_sha256=first.request_sha256)
            original = ModelTurnRequest.from_dict(payload.get("model_request"))
            identity = {key: getattr(first, key) for key in
                        ("run_id", "turn_id", "task_id", "agent_id", "sequence")}
            if (payload.get("runtime_identity") != identity
                    or any(getattr(original, key) != value for key, value in identity.items())
                    or original.task_id != retrieval_task_id
                    or payload.get("user_input") != original.user_input
                    or not (context_runtime.validate_instruction(first, RetrievalAgent._retrieval_instruction(
                        query, source_context, workflow, self._task_plan, prompt_profile, memory_hints, final_protocol))
                        if context_runtime is not None else original.user_input == RetrievalAgent._retrieval_instruction(
                            query, source_context, workflow, self._task_plan, prompt_profile, memory_hints, final_protocol))):
                raise ValidationError("Resume input does not match the original durable request.")
            if context_runtime is not None:
                context_runtime.restore(initial_state)

        state_cursor = {"state": initial_state}
        self._active_runtime_state = initial_state
        self._evidence_by_id = {}
        self._search_evidence_ids = set()
        self._latest_retrieved_evidence = ()
        self._latest_decision_records = ()
        self._latest_scope_path = None
        self._last_context_bundle = None

        def persist_state(current_state: RuntimeState) -> None:
            if not isinstance(current_state, RuntimeState):
                raise ValidationError("RuntimeAgentAdapter checkpoint requires RuntimeState.")
            if (
                current_state.run_id != run_id
                or current_state.thread_id != initial_state.thread_id
            ):
                raise ValidationError(
                    "RuntimeAgentAdapter checkpoint identity does not match the active run."
                )
            state_checkpoint_callback(current_state)
            state_cursor["state"] = current_state
            self._active_runtime_state = current_state

        documents = [] if task_relations else self._read_documents()
        if resume_model_loop:
            self._restore_evidence(initial_state, retrieval_task_id, documents)
        initial_refs = [f"note_ref_{idx}" for idx, _ in enumerate(documents[:2])]
        if task_relations:
            initial_refs = ["task_context_request"]
        tool_funcs = {
            "search_notes": self._search_notes,
            "read_verified_note": self._read_verified_note_for_tool,
            "build_pair_signals": self._build_pair_signals,
            "validate_evidence": self._validate_evidence,
            "validate_schema": self._validate_schema,
        }
        if self._decision_memory_enabled:
            tool_funcs["search_decision_memory"] = self._search_decision_memory
        if task_relations:
            from linkloom.agents.task_relation_result import review_task_relation_result, task_relation_task_id
            def review_task_payload(_result_type, payload):
                if context_runtime is not None:
                    context_check = context_runtime.review(state_cursor["state"])
                    if context_check["status"] != "pass":
                        return context_check
                return review_task_relation_result(payload, task_relation_tools,
                    records=state_cursor["state"].tool_ledger, run_id=run_id,
                    task_id=task_relation_task_id(state_cursor['state'].model_executions, run_id))
            tool_funcs["validate_schema"] = review_task_payload
        retrieval_tool_runtime = create_retrieval_tool_runtime(
            self._search_notes,
            self._read_verified_note_for_tool,
            ledger=tool_ledger,
            checkpoint_callback=tool_checkpoint_callback,
            decision_memory_executor=self._search_decision_memory if self._decision_memory_enabled else None,
            reuse_preflight=self._read_documents,
            expected_source_context=source_context,
            task_relation_tools=task_relation_tools if task_relations else None,
        )
        event_sink = RuntimeAgentEventSink(tracer) if tracer is not None else None
        self._integration_event_sink = event_sink
        coordinator = Coordinator(
            registry=create_default_registry(include_decision_memory=self._decision_memory_enabled, task_relations=task_relations,
                task_semantics=task_relations and task_relation_tools.semantic_analysis is not None),
            retrieval_agent=RetrievalAgent(
                model=model,
                initial_state=initial_state,
                artifact_store=artifact_store,
                state_checkpoint_callback=persist_state,
                resume_model_loop=resume_model_loop,
                context_runtime=context_runtime,
                task_plan=self._task_plan,
                expected_workspace_id=self.workspace_id,
                prompt_profile=prompt_profile,
                verified_evidence_context_limit=verified_evidence_context_limit,
                memory_hints=memory_hints,
                final_protocol=final_protocol,
                temporal_claim_validator=(
                    self._verify_temporal_claim
                    if self._temporal_claim_validator is not None else None
                ),
            ),
            curator_agent=CuratorAgent(),
            reviewer_agent=ReviewerAgent(),
            tool_funcs=tool_funcs,
            tool_runtime=retrieval_tool_runtime,
        )
        coordinator.max_total_steps = max_total_steps
        # RuntimeEngine supplies the model budget plus two Coordinator-only
        # review/publication slots.  Recover the bounded model allowance here
        # so the task cannot exceed the persisted RunRequest policy.
        coordinator.team_decision_model_max_steps = max(1, max_total_steps - 2)
        result = coordinator.run(
            run_id=run_id,
            workflow=workflow,
            query=query,
            source_context=source_context,
            initial_refs=initial_refs,
            event_sink=event_sink,
            retrieval_task_id=retrieval_task_id,
        )
        if workflow == "team_decision" and self._decision_memory_enabled:
            result["memory_candidate"] = self._capture_agent_memory_candidate(
                result,
                run_id=run_id,
            )
        result["source"] = source_context
        if context_runtime is not None:
            result["working_context_audit"] = context_runtime.latest
        if injected_memory:
            result["memory_refs"] = [
                {
                    "memory_id": m["memory_id"],
                    "scope": m["scope"],
                    "key": m["key"],
                    "value_sha256": m["value_sha256"],
                }
            for m in injected_memory
            ]
        result["tool_ledger"] = [
            record.to_dict() for record in state_cursor["state"].tool_ledger
        ]
        return result
