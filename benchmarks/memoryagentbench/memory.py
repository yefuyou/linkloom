"""Benchmark-only bridges to LinkLoom's existing retrieval and memory contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import DecisionRecord, DecisionStatus
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore
from linkloom.decision_memory.tool import DecisionMemorySearchTool
from linkloom.indexing.bm25 import BM25Index
from linkloom.indexing.models import IndexDocument

from .adapter import FactConsolidationCase, OrderedFact


_ORDER_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "current",
    "did",
    "do",
    "does",
    "for",
    "how",
    "is",
    "it",
    "name",
    "of",
    "the",
    "to",
    "was",
    "what",
    "which",
    "who",
    "where",
    "with",
}


@dataclass(frozen=True, slots=True)
class TemporalMemoryFixture:
    store: TemporalDecisionStore
    workspace_id: str
    subject_index: BM25Index
    subject_by_resource_id: dict[str, str]
    subject_keys: tuple[str, ...]
    materialized_count: int
    unmapped_fact_ids: tuple[str, ...]
    candidate_lifecycle: tuple["CandidateLifecycleEvent", ...]


@dataclass(frozen=True, slots=True)
class CandidateLifecycleEvent:
    source_fact_id: str
    candidate_id: str
    write_action: str
    candidate_state: str
    decision_id: str
    decision_state: str
    supersedes_id: str | None


@dataclass(frozen=True, slots=True)
class TemporalQueryResult:
    selected_subject_keys: tuple[str, ...]
    values: tuple[dict[str, object], ...]


def build_flat_index(case: FactConsolidationCase) -> BM25Index:
    """Index every source fact as an independent, non-temporal evidence item."""
    index = BM25Index()
    index.build(
        IndexDocument(
            workspace_id=case.workspace_id,
            resource_id=fact.fact_id,
            evidence_id=fact.fact_id,
            logical_path=f"facts/{fact.ordinal:04d}.md",
            content=fact.statement,
            source_ref=fact.fact_id,
            metadata={"ordinal": fact.ordinal, "source": case.source},
        )
        for fact in case.facts
    )
    return index


def build_temporal_memory(
    case: FactConsolidationCase,
    *,
    db_path: str | Path = ":memory:",
) -> TemporalMemoryFixture:
    """Materialize parsed facts through the existing candidate/write/read path.

    FactConsolidation supplies order but no event timestamps. The adapter uses
    an ordinal-derived UTC value only to preserve that order in the product
    temporal schema; it is not presented as a dataset timestamp.
    """
    mapped = [fact for fact in case.facts if fact.subject_key and fact.value]
    registry = SourceReferenceRegistry(
        episodes={
            case.workspace_id: (f"episode:{fact.fact_id}" for fact in mapped)
        },
        evidence={case.workspace_id: (fact.fact_id for fact in mapped)},
    )
    store = TemporalDecisionStore(db_path, source_registry=registry)
    materializer = DecisionMaterializer(store)
    inserted = 0
    lifecycle: list[CandidateLifecycleEvent] = []
    try:
        for fact in mapped:
            valid_from = _ORDER_EPOCH + timedelta(seconds=fact.ordinal)
            existing = store.get_current(case.workspace_id, fact.subject_key or "")
            decision = DecisionRecord(
                decision_id=f"decision:{fact.ordinal}",
                workspace_id=case.workspace_id,
                subject_key=fact.subject_key or "",
                value=fact.value or "",
                status=DecisionStatus.CURRENT,
                valid_from=valid_from,
                valid_to=None,
                supersedes_id=existing.decision_id if existing is not None else None,
                source_episode_id=f"episode:{fact.fact_id}",
                source_evidence_refs=(fact.fact_id,),
                provenance_run_id="memoryagentbench:deterministic-adapter",
            )
            _validate_benchmark_fact_as_grounded_result(fact)
            candidate = materializer.propose(
                decision,
                team_decision_contract_pass=True,
                grounding_pass=True,
            )
            result = materializer.materialize_with_policy(
                candidate,
                approved_by="memoryagentbench:deterministic-offline-adapter",
            )
            if result.action.value in {"ACTIVATE", "SUPERSEDE"}:
                inserted += 1
            if result.decision is None:
                raise RuntimeError("benchmark memory write produced no persisted decision")
            persisted = store.get_decision(case.workspace_id, result.decision.decision_id)
            candidate_state = store.get_candidate_state(candidate.candidate_id)
            if (
                persisted is None
                or persisted.memory_state is None
                or candidate_state is None
            ):
                raise RuntimeError("benchmark candidate lifecycle could not be read back")
            lifecycle.append(
                CandidateLifecycleEvent(
                    source_fact_id=fact.fact_id,
                    candidate_id=candidate.candidate_id,
                    write_action=result.action.value,
                    candidate_state=candidate_state.value,
                    decision_id=persisted.decision_id,
                    decision_state=persisted.memory_state.value,
                    supersedes_id=persisted.supersedes_id,
                )
            )

        subject_keys = tuple(
            sorted(
                {
                    fact.subject_key
                    for fact in mapped
                    if fact.subject_key and store.get_current(case.workspace_id, fact.subject_key)
                }
            )
        )
        subject_by_resource_id = {
            f"subject:{index}": subject_key
            for index, subject_key in enumerate(subject_keys)
        }
        subject_index = BM25Index()
        subject_index.build(
            IndexDocument(
                workspace_id=case.workspace_id,
                resource_id=resource_id,
                evidence_id=resource_id,
                logical_path=f"memory/subjects/{index:04d}.md",
                content=_normalize_search_text(subject_key),
                source_ref=subject_key,
                metadata={"subject_key": subject_key},
            )
            for index, (resource_id, subject_key) in enumerate(subject_by_resource_id.items())
        )
        final_lifecycle: list[CandidateLifecycleEvent] = []
        for event in lifecycle:
            persisted = store.get_decision(case.workspace_id, event.decision_id)
            if persisted is None or persisted.memory_state is None:
                raise RuntimeError("benchmark decision state could not be read back")
            final_lifecycle.append(
                replace(event, decision_state=persisted.memory_state.value)
            )
        return TemporalMemoryFixture(
            store=store,
            workspace_id=case.workspace_id,
            subject_index=subject_index,
            subject_by_resource_id=subject_by_resource_id,
            subject_keys=subject_keys,
            materialized_count=inserted,
            unmapped_fact_ids=tuple(
                fact.fact_id for fact in case.facts if not fact.subject_key or not fact.value
            ),
            candidate_lifecycle=tuple(final_lifecycle),
        )
    except Exception:
        store.close()
        raise


def search_temporal_memory(
    memory: TemporalMemoryFixture,
    question: str,
    *,
    subject_limit: int = 1,
    result_limit: int = 5,
) -> TemporalQueryResult:
    """Use lexical subject navigation, then the product's authorized read tool."""
    ranked_subjects = memory.subject_index.search(
        _normalize_search_text(question),
        workspace_id=memory.workspace_id,
        top_k=max(subject_limit * 3, subject_limit),
    )
    query_terms = _meaningful_terms(question)
    selected: list[str] = []
    for result in ranked_subjects:
        subject_key = memory.subject_by_resource_id.get(result.resource_id)
        if subject_key is None:
            continue
        # A weak single-token coincidence (for example "university") is not
        # enough to expose an unrelated current-state hint.
        if len(query_terms & _meaningful_terms(subject_key)) < 2:
            continue
        selected.append(subject_key)
        if len(selected) >= subject_limit:
            break
    selected_keys = tuple(selected)
    tool = DecisionMemorySearchTool(
        memory.store,
        authorized_workspace_id=memory.workspace_id,
    )
    values: list[dict[str, object]] = []
    seen: set[str] = set()
    for subject_key in selected_keys:
        result = tool.search(
            workspace=memory.workspace_id,
            query=subject_key,
            as_of=None,
            limit=result_limit,
        )
        for value in result.values:
            decision_id = str(value["decision_id"])
            if decision_id not in seen:
                seen.add(decision_id)
                values.append(value)
    return TemporalQueryResult(selected_subject_keys=selected_keys, values=tuple(values))


def _meaningful_terms(text: str) -> set[str]:
    return {
        _normalize_token(token)
        for token in _WORD.findall(text.casefold())
        if token not in _STOP_WORDS and len(_normalize_token(token)) > 1
    }


def _normalize_search_text(text: str) -> str:
    """Normalize morphology symmetrically for benchmark queries and keys.

    This operates only on the benchmark's lexical navigation index. Stored
    facts, predicates, retrieval documents, and product search semantics are
    left untouched.
    """
    return " ".join(_normalize_token(token) for token in _WORD.findall(text.casefold()))


def _normalize_token(token: str) -> str:
    """Apply small, general English suffix rules without relation aliases."""
    if len(token) > 7 and token.endswith("ship"):
        return token[:-4]
    if len(token) > 4 and token.endswith(("ies", "ied")):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("ing"):
        stem = token[:-3]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        if stem.endswith("duc"):
            stem += "e"
        return stem
    if len(token) > 4 and token.endswith("ed"):
        stem = token[:-2]
        if stem.endswith(("at", "it")):
            stem += "e"
        elif len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]
        return stem
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _validate_benchmark_fact_as_grounded_result(fact: OrderedFact) -> None:
    """Exercise the strict product contract before adapter materialization.

    This is a deterministic fixture conversion, not a claim that the dataset
    fact is a user-approved business decision or a model-generated Final.
    """
    if fact.subject_key is None or fact.value is None:
        raise ValueError(f"fact cannot be promoted without a parsed relation: {fact.fact_id}")
    evidence_refs = [fact.fact_id]
    result = TeamDecisionResult(
        decision={
            "value": fact.value,
            "status": "approved",
            "evidence_refs": evidence_refs,
        },
        rationale=[
            {"point": fact.statement, "evidence_refs": evidence_refs},
        ],
        rejected_alternatives=[],
        actions=[],
        unresolved_items=[],
        uncertainty={
            "status": "none",
            "statement": None,
            "unknown_fields": [],
            "evidence_refs": [],
        },
        evidence_refs=evidence_refs,
    )
    TeamDecisionResult.from_grounded_final(
        json.dumps(result.to_dict(), ensure_ascii=False),
        observed_evidence_refs=evidence_refs,
    )
