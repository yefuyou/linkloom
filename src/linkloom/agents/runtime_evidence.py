"""Adapt Runtime's observed Markdown passages into verified Agent evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from linkloom.agents.memory_candidate import AgentRunEvidence, AgentRunEvidenceCatalog
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.schemas import NoteDocument
from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceBlock:
    evidence_ref: str | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceCatalogResult:
    catalog: AgentRunEvidenceCatalog
    blocked: tuple[RuntimeEvidenceBlock, ...]


class RuntimeEvidenceCatalogAdapter:
    """Verify observed Runtime passages and populate the shared source registry.

    This is an offline boundary only. It consumes the Runtime's already observed
    passage records and current ``NoteDocument`` snapshots; it does not invoke
    retrieval, a provider, or candidate capture.
    """

    def build(
        self,
        *,
        run_id: str,
        workspace_id: str,
        passages: Iterable[Mapping[str, object]],
        documents: Iterable[NoteDocument],
        source_registry: SourceReferenceRegistry,
    ) -> RuntimeEvidenceCatalogResult:
        if not isinstance(source_registry, SourceReferenceRegistry):
            raise TypeError("source_registry must be SourceReferenceRegistry")

        documents_by_path: dict[str, NoteDocument] = {}
        ambiguous_paths: set[str] = set()
        for document in documents:
            if not isinstance(document, NoteDocument):
                raise TypeError("documents must contain NoteDocument values")
            previous = documents_by_path.get(document.relative_path)
            if previous is not None and previous != document:
                ambiguous_paths.add(document.relative_path)
            else:
                documents_by_path[document.relative_path] = document

        verified: dict[str, tuple[VerifiedEvidenceBinding, NoteDocument]] = {}
        blocked: list[RuntimeEvidenceBlock] = []
        conflicted_refs: set[str] = set()
        for passage in passages:
            if not isinstance(passage, Mapping):
                blocked.append(RuntimeEvidenceBlock(None, "PASSAGE_FIELDS_MISSING"))
                continue
            raw_ref = passage.get("evidence_id")
            evidence_ref = raw_ref.strip() if isinstance(raw_ref, str) and raw_ref.strip() else None
            raw_path = passage.get("relative_path")
            path = raw_path.strip() if isinstance(raw_path, str) and raw_path.strip() else None
            if path is None:
                blocked.append(RuntimeEvidenceBlock(evidence_ref, "PASSAGE_FIELDS_MISSING"))
                continue
            if path in ambiguous_paths:
                blocked.append(RuntimeEvidenceBlock(evidence_ref, "SOURCE_IDENTITY_AMBIGUOUS"))
                continue
            document = documents_by_path.get(path)
            if document is None:
                blocked.append(RuntimeEvidenceBlock(evidence_ref, "SOURCE_NOT_FOUND"))
                continue
            try:
                binding = VerifiedEvidenceBinding.from_runtime_passage(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    document=document,
                    passage=passage,
                )
            except (TypeError, ValueError) as error:
                code = str(error)
                if code not in {
                    "WORKSPACE_MISMATCH",
                    "SOURCE_IDENTITY_MISMATCH",
                    "PASSAGE_NOT_VERIFIED",
                    "SOURCE_HASH_MISMATCH",
                    "PASSAGE_HASH_MISMATCH",
                    "PASSAGE_SPAN_MISMATCH",
                    "PASSAGE_IDENTITY_MISMATCH",
                    "PASSAGE_FIELDS_MISSING",
                }:
                    code = "PASSAGE_INVALID"
                blocked.append(RuntimeEvidenceBlock(evidence_ref, code))
                continue

            previous = verified.get(binding.evidence_ref)
            if binding.evidence_ref in conflicted_refs:
                continue
            if previous is not None and previous[0] != binding:
                verified.pop(binding.evidence_ref, None)
                conflicted_refs.add(binding.evidence_ref)
                blocked.append(RuntimeEvidenceBlock(binding.evidence_ref, "EVIDENCE_REF_CONFLICT"))
                continue
            verified[binding.evidence_ref] = (binding, document)

        accepted: list[tuple[VerifiedEvidenceBinding, NoteDocument]] = []
        for evidence_ref in sorted(verified):
            binding, document = verified[evidence_ref]
            prior_hash = source_registry.evidence_hash(workspace_id, evidence_ref)
            if prior_hash is not None and prior_hash != binding.content_hash:
                blocked.append(RuntimeEvidenceBlock(evidence_ref, "SOURCE_HASH_MISMATCH"))
                continue
            prior_version = source_registry.evidence_version(workspace_id, evidence_ref)
            if prior_version is not None and prior_version != binding.source_version:
                blocked.append(RuntimeEvidenceBlock(evidence_ref, "SOURCE_VERSION_MISMATCH"))
                continue
            accepted.append((binding, document))

        # All conflicts are checked before the registry is mutated, so a blocked
        # item cannot leave a partial episode/evidence entry behind.
        for binding, _document in accepted:
            source_registry.register_episode(workspace_id, binding.source_episode_id)
            source_registry.register_evidence(
                workspace_id,
                binding.evidence_ref,
                content_hash=binding.content_hash,
                source_version=binding.source_version,
            )

        catalog = AgentRunEvidenceCatalog(
            run_id,
            (
                AgentRunEvidence(binding, observed_content=document.content)
                for binding, document in accepted
            ),
        )
        return RuntimeEvidenceCatalogResult(catalog, tuple(blocked))
