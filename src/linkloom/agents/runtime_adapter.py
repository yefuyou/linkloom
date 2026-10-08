"""Runtime bridge for the P4 deterministic multi-agent workflow.

The bridge owns the only VaultReader-backed callbacks exposed to specialists.
Agents receive opaque refs; this module resolves them back to a verified
Scanner-v1 document only at the tool boundary.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from typing import Any, Callable

from linkloom.agents.coordinator import Coordinator
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
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.context.assembler import ContextAssembler, ContextBundle, ContextSourceType
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.tool import DecisionMemorySearchTool
from linkloom.indexing import EmbeddingProvider, IndexUpdateCoordinator
from linkloom.loader import VaultReader
from linkloom.retrieval import find_relation_candidates_with_evidence
from linkloom.retrieval_v2 import RetrievedEvidence, RuntimeRetrievalBackend
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RuntimeState
from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.read_tools import _require_relative_note_ref
from linkloom.tools.ledger import ToolExecutionLedger
from linkloom.tools.contracts import ToolResult
from linkloom.tools.runtime import ToolCheckpointCallback, create_retrieval_tool_runtime


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
    ) -> None:
        self.reader = VaultReader(vault_root=vault_root, index_path=index_path)
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
        self._decision_memory_store = (
            decision_memory_store
            if decision_memory_store is not None
            else TemporalDecisionStore(":memory:")
        )
        self._decision_memory_store_unavailable = decision_memory_store_unavailable
        self._decision_memory_tool = DecisionMemorySearchTool(
            self._decision_memory_store,
            authorized_workspace_id=self.workspace_id,
        )
        if context_assembler is not None and not isinstance(context_assembler, ContextAssembler):
            raise ValueError("context_assembler must be a ContextAssembler or None")
        self._context_assembler = context_assembler or ContextAssembler()
        self._integration_event_sink: RuntimeAgentEventSink | None = None
        self._evidence_by_id: dict[str, dict[str, Any]] = {}
        self._candidate_docs: list[dict[str, Any]] = []
        self._active_runtime_state: RuntimeState | None = None
        self._latest_retrieved_evidence: tuple[RetrievedEvidence, ...] = ()
        self._latest_decision_records = ()
        self._latest_scope_path: str | None = None
        self._last_context_bundle: ContextBundle | None = None
        self._last_context_assembly_ms = 0.0

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

    def _search_decision_memory(
        self,
        query: str,
        as_of: str | None,
        limit: int,
    ) -> list[dict[str, object]]:
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

    def _assemble_context(
        self,
        *,
        query: str,
        retrieved_evidence: tuple[RetrievedEvidence, ...] | list[RetrievedEvidence],
        decision_memory=(),
        scope_path: str | None = None,
        temporal_query: bool | None = None,
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
        """Apply the existing ref boundary before the trusted runtime callback."""
        _require_relative_note_ref(ref)
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

    @staticmethod
    def _validate_schema(result_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not result_type or not isinstance(payload, dict):
            return {"status": "fail", "reason": "schema_invalid"}
        if any(key.casefold() in {"gold", "expected", "label", "mutation_approval"} for key in payload):
            return {"status": "fail", "reason": "forbidden_field"}
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
                if (document is None or value.get("content_sha256") != document.content_sha256
                        or value.get("status") != "verified" or not isinstance(quote, str)
                        or not quote or quote not in document.content
                        or hashlib.sha256(quote.encode("utf-8")).hexdigest() != value.get("quote_sha256")):
                    raise ValidationError("Durable evidence no longer matches its verified source.")
                self._evidence_by_id[value["evidence_id"]] = dict(value)

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
    ) -> dict[str, Any]:
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
        if resume_model_loop != (retrieval_task_id is not None):
            raise ValidationError("Resume mode requires a canonical retrieval task identity.")
        if retrieval_task_id is not None and not retrieval_task_id.strip():
            raise ValidationError("Resume task identity must be non-empty.")
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
                    or original.user_input != RetrievalAgent._retrieval_instruction(
                        query, source_context, workflow
                    )):
                raise ValidationError("Resume input does not match the original durable request.")

        state_cursor = {"state": initial_state}
        self._active_runtime_state = initial_state
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

        documents = self._read_documents()
        if resume_model_loop:
            self._restore_evidence(initial_state, retrieval_task_id, documents)
        initial_refs = [f"note_ref_{idx}" for idx, _ in enumerate(documents[:2])]
        tool_funcs = {
            "search_notes": self._search_notes,
            "search_decision_memory": self._search_decision_memory,
            "read_verified_note": self._read_verified_note_for_tool,
            "build_pair_signals": self._build_pair_signals,
            "validate_evidence": self._validate_evidence,
            "validate_schema": self._validate_schema,
        }
        retrieval_tool_runtime = create_retrieval_tool_runtime(
            self._search_notes,
            self._read_verified_note_for_tool,
            ledger=tool_ledger,
            checkpoint_callback=tool_checkpoint_callback,
            decision_memory_executor=self._search_decision_memory,
        )
        event_sink = RuntimeAgentEventSink(tracer) if tracer is not None else None
        self._integration_event_sink = event_sink
        coordinator = Coordinator(
            registry=create_default_registry(include_decision_memory=True),
            retrieval_agent=RetrievalAgent(
                model=model,
                initial_state=initial_state,
                artifact_store=artifact_store,
                state_checkpoint_callback=persist_state,
                resume_model_loop=resume_model_loop,
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
        if workflow == "team_decision":
            result["memory_candidate"] = self._capture_agent_memory_candidate(
                result,
                run_id=run_id,
            )
        result["source"] = source_context
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
