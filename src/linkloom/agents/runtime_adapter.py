"""Runtime bridge for the P4 deterministic multi-agent workflow.

The bridge owns the only VaultReader-backed callbacks exposed to specialists.
Agents receive opaque refs; this module resolves them back to a verified
Scanner-v1 document only at the tool boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from linkloom.agents.coordinator import Coordinator
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.registry import create_default_registry
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.loader import VaultReader
from linkloom.retrieval import find_relation_candidates_with_evidence, retrieve_evidence


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

    def run(
        self,
        run_id: str,
        workflow: str,
        query: str,
        source_context: dict[str, Any],
        tracer: Any = None,
        max_total_steps: int = 12,
        injected_memory: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        documents = self._read_documents()
        initial_refs = [f"note_ref_{idx}" for idx, _ in enumerate(documents[:2])]
        tool_funcs = {
            "search_notes": self._search_notes,
            "read_verified_note": self._read_verified_note,
            "build_pair_signals": self._build_pair_signals,
            "validate_evidence": self._validate_evidence,
            "validate_schema": self._validate_schema,
        }
        coordinator = Coordinator(
            registry=create_default_registry(),
            retrieval_agent=RetrievalAgent(),
            curator_agent=CuratorAgent(),
            reviewer_agent=ReviewerAgent(),
            tool_funcs=tool_funcs,
        )
        coordinator.max_total_steps = max_total_steps
        event_sink = RuntimeAgentEventSink(tracer) if tracer is not None else None
        result = coordinator.run(
            run_id=run_id,
            workflow=workflow,
            query=query,
            source_context=source_context,
            initial_refs=initial_refs,
            event_sink=event_sink,
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
        return result
