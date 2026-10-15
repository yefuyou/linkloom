from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import pytest

from linkloom.agents.memory_candidate import (
    AgentDecisionClaimProposal,
    AgentMemoryBuildStatus,
    AgentMemoryResolutionContext,
    AgentRunEvidenceCatalog,
    MemoryCandidateBuilder,
    WorkspaceSubject,
    WorkspaceSubjectRegistry,
)
from linkloom.agents.runtime_evidence import RuntimeEvidenceCatalogAdapter
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.retrieval_v2 import RuntimeRetrievalBackend
from linkloom.schemas import NoteDocument
from linkloom.agents.memory_candidate_persistence import (
    deserialize_candidate,
    serialize_candidate,
)
from linkloom.semantic_ingestion.artifacts import (
    RawArtifact,
    source_episode_id_for,
    source_episode_id_for_source,
)
from linkloom.semantic_ingestion.candidate_models import TemporalResolution
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text


WORKSPACE = "workspace:runtime-evidence"
RUN_ID = "run:2026-10-07:evidence-bridge"
MARKDOWN = "# Supplier decision\nWe selected Acme as the supplier for Launch.\n"


def _document(content: str = MARKDOWN, *, path: str = "decisions/supplier.md") -> NoteDocument:
    return NoteDocument(
        relative_path=path,
        title="Supplier decision",
        content=content,
        headings=[{"level": 1, "text": "Supplier decision", "line": 1}],
        tags=[],
        wikilinks=[],
        size_bytes=len(content.encode("utf-8")),
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        line_count=len(content.splitlines()),
    )


def _passage(document: NoteDocument | None = None) -> dict[str, object]:
    selected = document or _document()
    backend = RuntimeRetrievalBackend(
        workspace_id=WORKSPACE,
        document_provider=lambda: [selected],
        mode="current",
    )
    result = backend.search("supplier", top_k=5)
    return next(item for item in result.evidence if "Acme" in item["quote"])


def _team_result(evidence_ref: str) -> TeamDecisionResult:
    refs = [evidence_ref]
    return TeamDecisionResult(
        decision={"value": "Acme", "status": "approved", "evidence_refs": refs},
        rationale=[{"point": "The source names Acme as supplier.", "evidence_refs": refs}],
        rejected_alternatives=[],
        actions=[],
        unresolved_items=[],
        uncertainty={
            "status": "none",
            "statement": None,
            "unknown_fields": [],
            "evidence_refs": [],
        },
        evidence_refs=refs,
    )


def _build_runtime_candidate(*, content: str = MARKDOWN, registry: SourceReferenceRegistry | None = None):
    document = _document(content)
    passage = _passage(document)
    selected_registry = registry or SourceReferenceRegistry()
    catalog_result = RuntimeEvidenceCatalogAdapter().build(
        run_id=RUN_ID,
        workspace_id=WORKSPACE,
        passages=[passage],
        documents=[document],
        source_registry=selected_registry,
    )
    subject_registry = WorkspaceSubjectRegistry(
        WORKSPACE,
        (WorkspaceSubject.create(WORKSPACE, "Launch"),),
    )
    result = _team_result(str(passage["evidence_id"]))
    context = AgentMemoryResolutionContext(
        WORKSPACE,
        subject_registry,
        AgentDecisionClaimProposal(
            claim_type="DECISION",
            subject_phrase="Launch",
            relation_phrase="supplier",
            evidence_refs=(str(passage["evidence_id"]),),
        ),
    )
    built = MemoryCandidateBuilder().build(
        result,
        workspace_id=WORKSPACE,
        run_id=RUN_ID,
        evidence_catalog=catalog_result.catalog,
        source_registry=selected_registry,
        relation_resolver=FrozenRelationResolver(("supplier",)),
        resolution_context=context,
    )
    return document, passage, selected_registry, catalog_result, built


def test_runtime_passage_becomes_verified_binding_without_rewriting_its_id():
    document, passage, registry, adapted, built = _build_runtime_candidate()

    evidence_ref = str(passage["evidence_id"])
    assert evidence_ref.startswith("ev_v2_")
    binding = adapted.catalog.get(evidence_ref).binding
    assert binding.evidence_ref == evidence_ref
    assert binding.workspace_id == WORKSPACE
    assert binding.source_locator == document.relative_path
    assert binding.source_version == document.content_sha256
    assert binding.content_hash == document.content_sha256
    assert binding.quote == passage["quote"]
    assert binding.verify_source(document.content)
    assert registry.has_episode(WORKSPACE, binding.source_episode_id)
    assert registry.has_evidence(WORKSPACE, evidence_ref)
    assert registry.evidence_hash(WORKSPACE, evidence_ref) == document.content_sha256
    assert registry.evidence_version(WORKSPACE, evidence_ref) == binding.source_version
    assert built.candidate is not None
    assert built.candidate.evidence[0].evidence_ref == evidence_ref
    assert built.candidate.evidence[0].provenance.evidence_ref == evidence_ref
    assert built.candidate.evidence[0].provenance.run_id == RUN_ID
    assert document.content[
        built.candidate.evidence[0].provenance.char_start:
        built.candidate.evidence[0].provenance.char_end
    ] == built.candidate.evidence[0].provenance.quote


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda item: item.update(content_sha256="0" * 64), "SOURCE_HASH_MISMATCH"),
        (lambda item: item.update(workspace_id="workspace:other"), "WORKSPACE_MISMATCH"),
        (lambda item: item.update(line_start=99), "PASSAGE_SPAN_MISMATCH"),
    ],
)
def test_runtime_passage_mismatch_is_explicitly_blocked(mutation, reason):
    document = _document()
    passage = _passage(document)
    mutation(passage)

    result = RuntimeEvidenceCatalogAdapter().build(
        run_id=RUN_ID,
        workspace_id=WORKSPACE,
        passages=[passage],
        documents=[document],
        source_registry=SourceReferenceRegistry(),
    )

    assert result.catalog.get(str(passage["evidence_id"])) is None
    assert result.blocked[0].reason_code == reason


def test_runtime_source_version_conflict_blocks_catalog_registration():
    document = _document()
    passage = _passage(document)
    registry = SourceReferenceRegistry()
    registry.register_evidence(
        WORKSPACE,
        str(passage["evidence_id"]),
        content_hash=document.content_sha256,
        source_version="stale-version",
    )

    result = RuntimeEvidenceCatalogAdapter().build(
        run_id=RUN_ID,
        workspace_id=WORKSPACE,
        passages=[passage],
        documents=[document],
        source_registry=registry,
    )

    assert result.catalog.get(str(passage["evidence_id"])) is None
    assert result.blocked[0].reason_code == "SOURCE_VERSION_MISMATCH"
    expected_episode = source_episode_id_for_source(
        WORKSPACE,
        document.relative_path,
        document.content_sha256,
    )
    assert not registry.has_episode(WORKSPACE, expected_episode)


def test_builder_rechecks_registered_source_version():
    document, passage, registry, adapted, _built = _build_runtime_candidate()
    evidence_ref = str(passage["evidence_id"])
    registry.unregister_evidence(WORKSPACE, evidence_ref)
    registry.register_evidence(
        WORKSPACE,
        evidence_ref,
        content_hash=document.content_sha256,
        source_version="stale-version",
    )
    subject_registry = WorkspaceSubjectRegistry(
        WORKSPACE, (WorkspaceSubject.create(WORKSPACE, "Launch"),)
    )
    context = AgentMemoryResolutionContext(
        WORKSPACE,
        subject_registry,
        AgentDecisionClaimProposal(
            claim_type="DECISION",
            subject_phrase="Launch",
            relation_phrase="supplier",
            evidence_refs=(evidence_ref,),
        ),
    )

    blocked = MemoryCandidateBuilder().build(
        _team_result(evidence_ref),
        workspace_id=WORKSPACE,
        run_id=RUN_ID,
        evidence_catalog=adapted.catalog,
        source_registry=registry,
        relation_resolver=FrozenRelationResolver(("supplier",)),
        resolution_context=context,
    )

    assert blocked.status is AgentMemoryBuildStatus.BLOCKED
    assert blocked.candidate is None
    assert blocked.reason_codes[0].value == "SOURCE_VERSION_MISMATCH"


def test_timestamped_semantic_evidence_uses_source_date_not_run_date():
    artifact = RawArtifact(
        workspace_id=WORKSPACE,
        artifact_id="meeting-2026-06-01",
        source_type="timestamped_text",
        content=(
            "[2026-06-01T09:00:00Z] Alice: Effective 2026-06-01, we decided to use "
            "Acme as supplier for Launch."
        ),
    )
    segment = parse_timestamped_text(artifact).segments[0]
    from linkloom.agents.memory_candidate import AgentRunEvidence, AgentRunEvidenceCatalog

    evidence = AgentRunEvidence(artifact, segment)
    registry = SourceReferenceRegistry(
        episodes={WORKSPACE: [source_episode_id_for(artifact)]},
        evidence={WORKSPACE: [evidence.evidence_ref]},
        evidence_hashes={WORKSPACE: {evidence.evidence_ref: artifact.content_hash}},
        evidence_versions={WORKSPACE: {evidence.evidence_ref: artifact.artifact_version_id}},
    )
    subject_registry = WorkspaceSubjectRegistry(
        WORKSPACE, (WorkspaceSubject.create(WORKSPACE, "Launch"),)
    )
    result = _team_result(evidence.evidence_ref)
    context = AgentMemoryResolutionContext(
        WORKSPACE,
        subject_registry,
        AgentDecisionClaimProposal(
            claim_type="DECISION",
            subject_phrase="Launch",
            relation_phrase="supplier",
            evidence_refs=(evidence.evidence_ref,),
        ),
    )

    built = MemoryCandidateBuilder().build(
        result,
        workspace_id=WORKSPACE,
        run_id=RUN_ID,
        evidence_catalog=AgentRunEvidenceCatalog(RUN_ID, [evidence]),
        source_registry=registry,
        relation_resolver=FrozenRelationResolver(("supplier",)),
        resolution_context=context,
    )

    assert built.candidate is not None
    assert built.candidate.effective_time == datetime(2026, 6, 1, tzinfo=UTC).date()
    assert built.candidate.effective_time != datetime(2026, 10, 7, tzinfo=UTC).date()


def test_generic_markdown_without_source_time_is_reviewable_and_creates_no_decision():
    _document_value, passage, registry, _adapted, built = _build_runtime_candidate()

    assert built.status is AgentMemoryBuildStatus.UNRESOLVED
    assert built.candidate is not None
    assert built.candidate.effective_time is None
    assert built.candidate.temporal_resolution is TemporalResolution.UNRESOLVED
    assert built.candidate.temporal_basis.value == "UNRESOLVED"
    assert "TEMPORAL_UNRESOLVED" in {reason.value for reason in built.candidate.review_reasons}
    assert built.candidate.evidence[0].event_time is None
    assert built.candidate.evidence[0].evidence_ref == str(passage["evidence_id"])

    with TemporalDecisionStore(":memory:", source_registry=registry) as store:
        assert store.search(WORKSPACE, "Acme") == ()

    encoded, _fingerprint = serialize_candidate(built.candidate)
    assert '"record_version":"agent-memory-candidate/v2"' in encoded
    reloaded = deserialize_candidate(encoded)
    assert reloaded == built.candidate
    assert reloaded.evidence[0].evidence_ref == str(passage["evidence_id"])


def test_missing_runtime_provenance_blocks_candidate_and_replay_is_deterministic():
    _document_value, passage, _registry, first, first_built = _build_runtime_candidate()
    _document_value, _passage_value, _registry, second, second_built = _build_runtime_candidate()

    assert first.catalog.get(str(passage["evidence_id"])) == second.catalog.get(str(passage["evidence_id"]))
    assert first_built.candidate is not None and second_built.candidate is not None
    assert first_built.candidate.candidate_id == second_built.candidate.candidate_id

    subject_registry = WorkspaceSubjectRegistry(
        WORKSPACE, (WorkspaceSubject.create(WORKSPACE, "Launch"),)
    )
    result = _team_result(str(passage["evidence_id"]))
    blocked = MemoryCandidateBuilder().build(
        result,
        workspace_id=WORKSPACE,
        run_id=RUN_ID,
        evidence_catalog=AgentRunEvidenceCatalog(RUN_ID, []),
        source_registry=SourceReferenceRegistry(),
        relation_resolver=FrozenRelationResolver(("supplier",)),
        resolution_context=AgentMemoryResolutionContext(
            WORKSPACE,
            subject_registry,
            AgentDecisionClaimProposal(
                claim_type="DECISION",
                subject_phrase="Launch",
                relation_phrase="supplier",
                evidence_refs=(str(passage["evidence_id"]),),
            ),
        ),
    )
    assert blocked.status is AgentMemoryBuildStatus.BLOCKED
    assert blocked.candidate is None
