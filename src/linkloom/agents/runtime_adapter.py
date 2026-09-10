"""Runtime bridge for the P4 deterministic multi-agent workflow.

The bridge owns the only VaultReader-backed callbacks exposed to specialists.
Agents receive opaque refs; this module resolves them back to a verified
Scanner-v1 document only at the tool boundary.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from linkloom.agents.coordinator import Coordinator
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.registry import create_default_registry
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.loader import VaultReader
from linkloom.retrieval import find_relation_candidates_with_evidence, retrieve_evidence
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import RuntimeState
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

    def __init__(self, vault_root: Path | str, index_path: Path | str) -> None:
        self.reader = VaultReader(vault_root=vault_root, index_path=index_path)
        self._evidence_by_id: dict[str, dict[str, Any]] = {}
        self._candidate_docs: list[dict[str, Any]] = []

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
        documents = self._read_documents()
        values = [evidence.to_dict() for evidence in retrieve_evidence(query, documents, max_results=limit)]
        self._evidence_by_id.update({value["evidence_id"]: value for value in values})
        return values

    def _read_verified_note(self, ref: str) -> dict[str, Any]:
        documents = self._read_documents()
        document = self._document_for_ref(ref, documents)
        evidence = self._evidence_by_id.get(ref)
        if evidence is not None:
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
                    or original.user_input != RetrievalAgent._retrieval_instruction(query, source_context)):
                raise ValidationError("Resume input does not match the original durable request.")

        state_cursor = {"state": initial_state}

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
        )
        event_sink = RuntimeAgentEventSink(tracer) if tracer is not None else None
        coordinator = Coordinator(
            registry=create_default_registry(),
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
        result = coordinator.run(
            run_id=run_id,
            workflow=workflow,
            query=query,
            source_context=source_context,
            initial_refs=initial_refs,
            event_sink=event_sink,
            retrieval_task_id=retrieval_task_id,
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
