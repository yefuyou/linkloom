from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from linkloom.context.assembler import (
    ContextAssembler,
    ContextBudget,
    ContextDropReason,
    ContextSourceType,
)
from linkloom.decision_memory.models import DecisionMemoryState, DecisionRecord, DecisionStatus
from linkloom.experience.context import ExperienceContextBuilder, render_records
from linkloom.experience.models import ExperienceSelection
from linkloom.experience.store import ExperienceStore
from linkloom.retrieval_v2.models import RetrievedEvidence
from linkloom.runtime.models import RuntimeState, SourceContext
from tests.experience_authority_support import fixture_authority, fixture_reviewed


def _evidence(
    evidence_id: str,
    rank: int,
    *,
    path: str = "/projects/borealis/notes.md",
    workspace_id: str = "borealis",
    source_ref: str | None = None,
) -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=evidence_id,
        source_ref=source_ref or f"source:{evidence_id}",
        logical_path=path,
        score=1.0 / rank,
        rank=rank,
        retrieval_channel="hybrid",
        resource_id=f"resource-{evidence_id}",
        metadata={"workspace_id": workspace_id, "text": f"Evidence {evidence_id}"},
    )


def _decision(
    decision_id: str,
    value: str,
    *,
    workspace_id: str = "borealis",
    status: DecisionStatus = DecisionStatus.CURRENT,
    evidence: tuple[str, ...] = ("source:decision",),
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        workspace_id=workspace_id,
        subject_key="supplier",
        value=value,
        status=status,
        valid_from=datetime(2026, 5, 1, tzinfo=UTC),
        valid_to=(datetime(2026, 6, 1, tzinfo=UTC) if status is DecisionStatus.SUPERSEDED else None),
        supersedes_id=None,
        source_episode_id=f"episode:{decision_id}",
        source_evidence_refs=evidence,
        provenance_run_id=f"run:{decision_id}",
    )


def test_context_assembler_applies_caps_and_evidence_priority() -> None:
    assembler = ContextAssembler(
        budget=ContextBudget(max_evidence=2, max_memory=1, max_experience=0)
    )
    bundle = assembler.assemble(
        workspace_id="borealis",
        query="supplier decision",
        retrieved_evidence=(_evidence("e1", 1), _evidence("e2", 2), _evidence("e3", 3)),
        decision_memory=(_decision("d1", "Supplier A"),),
    )

    assert [item.source_type for item in bundle.selected] == [
        ContextSourceType.QUERY,
        ContextSourceType.EVIDENCE,
        ContextSourceType.EVIDENCE,
        ContextSourceType.DECISION_MEMORY,
    ]
    assert [item.item_id for item in bundle.selected if item.source_type is ContextSourceType.EVIDENCE] == [
        "e1",
        "e2",
    ]
    assert [(item.item_id, item.reason) for item in bundle.dropped] == [
        ("e3", ContextDropReason.LOW_RANK)
    ]
    assert bundle.estimated_tokens >= sum(item.estimated_tokens for item in bundle.selected)


def test_context_assembler_deduplicates_and_rejects_out_of_scope_items() -> None:
    assembler = ContextAssembler()
    bundle = assembler.assemble(
        workspace_id="borealis",
        query="supplier",
        scope_path="/projects/borealis",
        retrieved_evidence=(
            _evidence("same", 1),
            _evidence("same", 2),
            _evidence("outside", 3, path="/projects/other/secret.md"),
            _evidence("foreign", 4, workspace_id="atlas"),
        ),
        decision_memory=(
            _decision("wrong-workspace", "Supplier X", workspace_id="atlas"),
        ),
    )

    reasons = {(item.item_id, item.reason) for item in bundle.dropped}
    assert ("same", ContextDropReason.DUPLICATE) in reasons
    assert ("outside", ContextDropReason.OUT_OF_SCOPE) in reasons
    assert ("foreign", ContextDropReason.OUT_OF_SCOPE) in reasons
    assert ("wrong-workspace", ContextDropReason.OUT_OF_SCOPE) in reasons
    assert all(item.item_id != "wrong-workspace" for item in bundle.selected)


def test_context_assembler_drops_memory_when_its_source_evidence_is_already_selected() -> None:
    assembler = ContextAssembler()
    bundle = assembler.assemble(
        workspace_id="borealis",
        query="supplier decision",
        retrieved_evidence=(_evidence("decision-source", 1, source_ref="source:decision"),),
        decision_memory=(_decision("d1", "Supplier A"),),
    )

    assert any(item.source_type is ContextSourceType.EVIDENCE for item in bundle.selected)
    assert not any(item.source_type is ContextSourceType.DECISION_MEMORY for item in bundle.selected)
    assert ("d1", ContextDropReason.DUPLICATE) in {
        (item.item_id, item.reason) for item in bundle.dropped
    }


def test_non_temporal_query_excludes_historical_memory_but_temporal_query_can_include_it() -> None:
    historical = _decision("old", "Supplier A", status=DecisionStatus.SUPERSEDED)
    current = _decision("new", "Supplier B")
    assembler = ContextAssembler()

    current_bundle = assembler.assemble(
        workspace_id="borealis",
        query="current supplier decision",
        decision_memory=(historical, current),
    )
    temporal_bundle = assembler.assemble(
        workspace_id="borealis",
        query="what did we decide before?",
        decision_memory=(historical, current),
    )

    current_ids = {item.item_id for item in current_bundle.selected}
    temporal_ids = {item.item_id for item in temporal_bundle.selected}
    assert "old" not in current_ids
    assert "old" in temporal_ids
    assert ("old", ContextDropReason.OUT_OF_SCOPE) in {
        (item.item_id, item.reason) for item in current_bundle.dropped
    }


def test_character_and_token_budgets_bound_the_final_context() -> None:
    assembler = ContextAssembler(
        budget=ContextBudget(
            max_evidence=5,
            max_memory=3,
            max_experience=2,
            max_chars=45,
            max_tokens=11,
        )
    )
    bundle = assembler.assemble(
        workspace_id="borealis",
        query="Q",
        retrieved_evidence=(_evidence("long", 1),),
        decision_memory=(_decision("d1", "A very long supplier value"),),
    )

    assert len(bundle.rendered_text) <= 45
    assert bundle.estimated_tokens <= 11
    assert any(item.reason is ContextDropReason.BUDGET for item in bundle.dropped)
    assert all(item.estimated_tokens >= 0 for item in (*bundle.selected, *bundle.dropped))


def test_query_that_cannot_fit_is_rejected_instead_of_silently_truncated() -> None:
    assembler = ContextAssembler(budget=ContextBudget(max_chars=3, max_tokens=1))

    with pytest.raises(ValueError, match="query exceeds context budget"):
        assembler.assemble(workspace_id="borealis", query="long query")


def test_stale_and_invalidated_memory_is_not_exposed_to_the_model() -> None:
    assembler = ContextAssembler()
    bundle = assembler.assemble(
        workspace_id="borealis",
        query="supplier",
        decision_memory=(_decision("stale", "Supplier A"), _decision("invalid", "Supplier B")),
        decision_memory_states={"stale": "STALE", "invalid": "INVALIDATED"},
    )

    assert not any(item.source_type is ContextSourceType.DECISION_MEMORY for item in bundle.selected)
    assert {item.reason for item in bundle.dropped} == {
        ContextDropReason.STALE,
        ContextDropReason.INVALID_PROVENANCE,
    }


@pytest.mark.parametrize(
    ("memory_state", "drop_reason"),
    (
        (DecisionMemoryState.STALE, ContextDropReason.STALE),
        (DecisionMemoryState.INVALIDATED, ContextDropReason.INVALID_PROVENANCE),
        (DecisionMemoryState.NEEDS_REVALIDATION, ContextDropReason.INVALID_PROVENANCE),
    ),
)
def test_temporal_context_honors_invalid_persisted_state_on_superseded_memory(
    memory_state: DecisionMemoryState,
    drop_reason: ContextDropReason,
) -> None:
    stale_history = _decision("old", "Supplier A", status=DecisionStatus.SUPERSEDED)
    stale_history = replace(stale_history, memory_state=memory_state)

    bundle = ContextAssembler().assemble(
        workspace_id="borealis",
        query="what did we decide before?",
        decision_memory=(stale_history,),
    )

    assert not any(
        item.source_type is ContextSourceType.DECISION_MEMORY
        for item in bundle.selected
    )
    assert ("old", drop_reason) in {
        (item.item_id, item.reason) for item in bundle.dropped
    }


def test_only_persistently_approved_experience_is_assembled_and_capped(tmp_path) -> None:
    authority = fixture_authority()
    store = ExperienceStore(tmp_path / "experience.jsonl", authority=authority)
    records = tuple(
        fixture_reviewed(store, authority, suffix)
        for suffix in ("a1", "b2", "c3")
    )
    selection = ExperienceSelection(records, len(render_records(records)))
    assembler = ContextAssembler(
        budget=ContextBudget(max_evidence=5, max_memory=3, max_experience=2),
        experience_builder=ExperienceContextBuilder(authority=authority, store=store),
    )

    bundle = assembler.assemble(
        workspace_id="borealis",
        query="current supplier",
        accepted_experience=selection,
    )

    selected_ids = [
        item.item_id
        for item in bundle.selected
        if item.source_type is ContextSourceType.EXPERIENCE
    ]
    assert selected_ids == [record.experience_id for record in records[:2]]
    assert [(item.item_id, item.reason) for item in bundle.dropped] == [
        (records[2].experience_id, ContextDropReason.LOW_RANK)
    ]


def test_runtime_context_is_minimal_and_does_not_expose_full_state() -> None:
    runtime_state = RuntimeState(
        schema_version=1,
        run_id="run_context_1",
        thread_id="thread_context_1",
        workflow="ask",
        status="running",
        step_seq=2,
        request_ref="request_context.json",
        source=SourceContext(
            index_path=".artifacts/context/index.json",
            index_sha256="a" * 64,
            vault_root_fingerprint="root_context",
        ),
        intent={"private_note": "must-not-be-rendered"},
    )
    bundle = ContextAssembler().assemble(
        workspace_id="borealis",
        query="supplier",
        runtime_state=runtime_state,
    )

    runtime_item = next(item for item in bundle.selected if item.source_type is ContextSourceType.RUNTIME_STATE)
    assert "workflow=ask" in runtime_item.text
    assert "must-not-be-rendered" not in bundle.rendered_text
