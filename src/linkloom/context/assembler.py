"""Deterministic, budgeted assembly of source and memory context."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Mapping, Sequence

from linkloom.decision_memory.models import DecisionMemoryState, DecisionRecord, DecisionStatus
from linkloom.experience.context import ExperienceContextBuilder, render_record
from linkloom.experience.models import ExperienceContextSection, ExperienceSelection
from linkloom.runtime.models import RuntimeState

from .models import normalize_logical_path

if TYPE_CHECKING:
    from linkloom.retrieval_v2.models import RetrievedEvidence


class ContextSourceType(StrEnum):
    QUERY = "query"
    EVIDENCE = "evidence"
    DECISION_MEMORY = "decision_memory"
    RUNTIME_STATE = "runtime_state"
    EXPERIENCE = "experience"


class ContextDropReason(StrEnum):
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    LOW_RANK = "LOW_RANK"
    BUDGET = "BUDGET"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_evidence: int = 5
    max_memory: int = 3
    max_experience: int = 2
    max_chars: int = 12_000
    max_tokens: int = 3_000

    def __post_init__(self) -> None:
        for name in ("max_evidence", "max_memory", "max_experience"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_chars", "max_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class SelectedContextItem:
    item_id: str
    source_type: ContextSourceType
    text: str
    estimated_tokens: int
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DroppedContextItem:
    item_id: str
    source_type: ContextSourceType
    reason: ContextDropReason
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class ContextBundle:
    workspace_id: str
    query: str
    selected: tuple[SelectedContextItem, ...]
    dropped: tuple[DroppedContextItem, ...]
    rendered_text: str
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class _Candidate:
    item_id: str
    source_type: ContextSourceType
    text: str
    source_refs: tuple[str, ...] = ()


_TEMPORAL_QUERY_RE = re.compile(
    r"\b(previous|previously|historical|history|before|earlier|evolution|changed|when)\b"
    r"|之前|以前|历史|過去|过去|演变|變更|变更|何时|什么时候|何時",
    re.IGNORECASE,
)


def _estimate_tokens(text: str) -> int:
    """Return a stable rough estimate (not a provider tokenizer result)."""
    return math.ceil(len(text) / 4) if text else 0


def _render(items: Sequence[SelectedContextItem | _Candidate]) -> str:
    return "\n\n".join(item.text for item in items)


def _path_is_under(path: str, scope_path: str) -> bool:
    normalized_path = normalize_logical_path(path)
    normalized_scope = normalize_logical_path(scope_path)
    return normalized_path == normalized_scope or normalized_path.startswith(
        normalized_scope.rstrip("/") + "/"
    )


class ContextAssembler:
    """Assemble bounded context without allowing memory to displace evidence."""

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        experience_builder: ExperienceContextBuilder | None = None,
    ) -> None:
        self.budget = budget or ContextBudget()
        self._experience_builder = experience_builder

    def assemble(
        self,
        *,
        workspace_id: str,
        query: str,
        retrieved_evidence: Sequence[RetrievedEvidence] = (),
        decision_memory: Sequence[DecisionRecord] = (),
        runtime_state: RuntimeState | None = None,
        accepted_experience: ExperienceSelection | ExperienceContextSection | None = None,
        scope_path: str | None = None,
        temporal_query: bool | None = None,
        decision_memory_states: Mapping[str, str] | None = None,
    ) -> ContextBundle:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        workspace_id = workspace_id.strip()
        query = query.strip()
        if scope_path is not None:
            scope_path = normalize_logical_path(scope_path)
        is_temporal = (
            bool(_TEMPORAL_QUERY_RE.search(query))
            if temporal_query is None
            else temporal_query
        )
        if not isinstance(is_temporal, bool):
            raise ValueError("temporal_query must be a boolean")

        candidates: list[_Candidate] = [
            _Candidate(
                item_id="query",
                source_type=ContextSourceType.QUERY,
                text=f"Question: {query}",
            )
        ]
        dropped: list[DroppedContextItem] = []

        # Re-retrieved documents are already ranked and workspace-filtered by
        # the retriever. Recheck optional metadata/scope before model exposure.
        seen_evidence: set[str] = set()
        evidence_count = 0
        for evidence in sorted(retrieved_evidence, key=lambda item: (item.rank, item.evidence_id)):
            item_id = evidence.evidence_id
            candidate = _evidence_candidate(evidence)
            metadata_workspace = evidence.metadata.get("workspace_id")
            if metadata_workspace is not None and metadata_workspace != workspace_id:
                dropped.append(_dropped(item_id, ContextSourceType.EVIDENCE, ContextDropReason.OUT_OF_SCOPE, candidate.text))
                continue
            if scope_path is not None and not _path_is_under(evidence.logical_path, scope_path):
                dropped.append(_dropped(item_id, ContextSourceType.EVIDENCE, ContextDropReason.OUT_OF_SCOPE, candidate.text))
                continue
            identity = evidence.source_ref or evidence.evidence_id
            if identity in seen_evidence:
                dropped.append(_dropped(item_id, ContextSourceType.EVIDENCE, ContextDropReason.DUPLICATE, candidate.text))
                continue
            seen_evidence.add(identity)
            if evidence_count >= self.budget.max_evidence:
                dropped.append(_dropped(item_id, ContextSourceType.EVIDENCE, ContextDropReason.LOW_RANK, candidate.text))
                continue
            candidates.append(candidate)
            evidence_count += 1

        if runtime_state is not None:
            if not isinstance(runtime_state, RuntimeState):
                raise ValueError("runtime_state must be RuntimeState")
            runtime_text = (
                "Runtime state (operational hint): "
                f"workflow={runtime_state.workflow}; status={runtime_state.status}; "
                f"step={runtime_state.current_step or 'none'}; step_seq={runtime_state.step_seq}"
            )
            candidates.append(
                _Candidate(
                    item_id=runtime_state.run_id,
                    source_type=ContextSourceType.RUNTIME_STATE,
                    text=runtime_text,
                )
            )

        state_map = {key: value.upper() for key, value in (decision_memory_states or {}).items()}
        seen_decisions: set[str] = set()
        ordered_memory = sorted(
            decision_memory,
            key=lambda item: (
                item.status is DecisionStatus.SUPERSEDED,
                -item.valid_from.timestamp(),
                item.decision_id,
            ),
        )
        for decision in ordered_memory:
            item_id = decision.decision_id
            memory_text = _decision_memory_text(decision)
            if item_id in seen_decisions:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.DUPLICATE, memory_text))
                continue
            seen_decisions.add(item_id)
            if decision.workspace_id != workspace_id:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.OUT_OF_SCOPE, memory_text))
                continue
            persisted_state = decision.memory_state or (
                DecisionMemoryState.SUPERSEDED
                if decision.status is DecisionStatus.SUPERSEDED
                else DecisionMemoryState.ACTIVE
            )
            memory_state = state_map.get(item_id, persisted_state.value)
            if persisted_state in {
                DecisionMemoryState.STALE,
                DecisionMemoryState.INVALIDATED,
                DecisionMemoryState.NEEDS_REVALIDATION,
            }:
                # An external hint may make valid memory more restrictive, but
                # it cannot reactivate a record whose persisted provenance failed.
                memory_state = persisted_state.value
            elif (
                decision.status is DecisionStatus.SUPERSEDED
                and memory_state == DecisionMemoryState.ACTIVE.value
            ):
                memory_state = DecisionMemoryState.SUPERSEDED.value
            if memory_state in {"STALE"}:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.STALE, memory_text))
                continue
            if memory_state in {"INVALIDATED", "NEEDS_REVALIDATION", "INVALID_PROVENANCE"}:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.INVALID_PROVENANCE, memory_text))
                continue
            is_historical = decision.status is DecisionStatus.SUPERSEDED or memory_state == "SUPERSEDED"
            if is_historical and not is_temporal:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.OUT_OF_SCOPE, memory_text))
                continue
            if not decision.source_evidence_refs:
                dropped.append(_dropped(item_id, ContextSourceType.DECISION_MEMORY, ContextDropReason.INVALID_PROVENANCE, memory_text))
                continue
            candidates.append(
                _Candidate(
                    item_id=item_id,
                    source_type=ContextSourceType.DECISION_MEMORY,
                    text=memory_text,
                    source_refs=decision.source_evidence_refs,
                )
            )

        experience_candidates, experience_drops = self._experience_candidates(accepted_experience)
        dropped.extend(experience_drops)
        candidates.extend(experience_candidates)

        query_candidate = candidates[0]
        if not _fits((query_candidate,), self.budget):
            raise ValueError("query exceeds context budget")

        selected: list[SelectedContextItem] = []
        selected_source_refs: set[str] = set()
        memory_count = 0
        for candidate in candidates:
            if candidate.source_type is ContextSourceType.DECISION_MEMORY:
                if candidate.source_refs and set(candidate.source_refs).issubset(
                    selected_source_refs
                ):
                    dropped.append(
                        DroppedContextItem(
                            item_id=candidate.item_id,
                            source_type=candidate.source_type,
                            reason=ContextDropReason.DUPLICATE,
                            estimated_tokens=_estimate_tokens(candidate.text),
                        )
                    )
                    continue
                if memory_count >= self.budget.max_memory:
                    dropped.append(
                        DroppedContextItem(
                            item_id=candidate.item_id,
                            source_type=candidate.source_type,
                            reason=ContextDropReason.LOW_RANK,
                            estimated_tokens=_estimate_tokens(candidate.text),
                        )
                    )
                    continue
            proposed = _selected(candidate)
            if _fits((*selected, proposed), self.budget):
                selected.append(proposed)
                if candidate.source_type is ContextSourceType.EVIDENCE:
                    selected_source_refs.update(candidate.source_refs)
                elif candidate.source_type is ContextSourceType.DECISION_MEMORY:
                    memory_count += 1
            else:
                dropped.append(
                    DroppedContextItem(
                        item_id=candidate.item_id,
                        source_type=candidate.source_type,
                        reason=ContextDropReason.BUDGET,
                        estimated_tokens=proposed.estimated_tokens,
                    )
                )

        rendered = _render(selected)
        return ContextBundle(
            workspace_id=workspace_id,
            query=query,
            selected=tuple(selected),
            dropped=tuple(dropped),
            rendered_text=rendered,
            estimated_tokens=_estimate_tokens(rendered),
        )

    def _experience_candidates(
        self,
        accepted_experience: ExperienceSelection | ExperienceContextSection | None,
    ) -> tuple[list[_Candidate], list[DroppedContextItem]]:
        if accepted_experience is None:
            return [], []
        if isinstance(accepted_experience, ExperienceContextSection):
            # Sections carry rendered text but no per-record approval boundary.
            # Require the original selection so the existing builder can recheck
            # status and persisted approval before inclusion.
            ids = accepted_experience.experience_ids or ("experience-context",)
            return [], [
                DroppedContextItem(
                    item_id=experience_id,
                    source_type=ContextSourceType.EXPERIENCE,
                    reason=ContextDropReason.INVALID_PROVENANCE,
                    estimated_tokens=_estimate_tokens(accepted_experience.model_text),
                )
                for experience_id in ids
            ]
        if not isinstance(accepted_experience, ExperienceSelection):
            raise ValueError("accepted_experience must be ExperienceSelection")
        records = accepted_experience.records
        if not records:
            return [], []
        if self._experience_builder is None:
            return [], [
                DroppedContextItem(
                    item_id=record.experience_id,
                    source_type=ContextSourceType.EXPERIENCE,
                    reason=ContextDropReason.INVALID_PROVENANCE,
                    estimated_tokens=_estimate_tokens(render_record(record)),
                )
                for record in records
            ]

        included = records[: self.budget.max_experience]
        excess = records[self.budget.max_experience :]
        drops = [
            DroppedContextItem(
                item_id=record.experience_id,
                source_type=ContextSourceType.EXPERIENCE,
                reason=ContextDropReason.LOW_RANK,
                estimated_tokens=_estimate_tokens(render_record(record)),
            )
            for record in excess
        ]
        approved_ids: set[str] = set()
        try:
            if included:
                subset = ExperienceSelection(
                    records=tuple(included),
                    total_rendered_chars=len(
                        "Relevant prior experience\n"
                        + "\n".join(render_record(record) for record in included)
                    ),
                )
                validated_section = self._experience_builder.build(subset)
                approved_ids = set(validated_section.experience_ids)
        except (TypeError, ValueError):
            drops.extend(
                DroppedContextItem(
                    item_id=record.experience_id,
                    source_type=ContextSourceType.EXPERIENCE,
                    reason=ContextDropReason.INVALID_PROVENANCE,
                    estimated_tokens=_estimate_tokens(render_record(record)),
                )
                for record in included
            )
            return [], drops
        for record in included:
            if record.experience_id not in approved_ids:
                drops.append(
                    DroppedContextItem(
                        item_id=record.experience_id,
                        source_type=ContextSourceType.EXPERIENCE,
                        reason=ContextDropReason.BUDGET,
                        estimated_tokens=_estimate_tokens(render_record(record)),
                    )
                )
        return [
            _Candidate(
                item_id=record.experience_id,
                source_type=ContextSourceType.EXPERIENCE,
                text=render_record(record),
                source_refs=record.source_evidence_refs,
            )
            for record in included
            if record.experience_id in approved_ids
        ], drops


def _evidence_content(evidence: RetrievedEvidence) -> str:
    for key in ("text", "content", "snippet", "excerpt"):
        value = evidence.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return evidence.source_ref


def _evidence_candidate(evidence: RetrievedEvidence) -> _Candidate:
    return _Candidate(
        item_id=evidence.evidence_id,
        source_type=ContextSourceType.EVIDENCE,
        text=(
            f"Source evidence [{evidence.evidence_id}] ({evidence.logical_path}):\n"
            f"{_evidence_content(evidence)}"
        ),
        source_refs=(evidence.source_ref,),
    )


def _decision_memory_text(decision: DecisionRecord) -> str:
    is_historical = decision.status is DecisionStatus.SUPERSEDED
    label = "Historical decision" if is_historical else "Current decision"
    provenance = ", ".join(decision.source_evidence_refs)
    return (
        f"{label} memory hint [{decision.subject_key}]: {decision.value}\n"
        f"Memory provenance refs: {provenance}"
    )


def _dropped(
    item_id: str,
    source_type: ContextSourceType,
    reason: ContextDropReason,
    text: str,
) -> DroppedContextItem:
    return DroppedContextItem(
        item_id=item_id,
        source_type=source_type,
        reason=reason,
        estimated_tokens=_estimate_tokens(text),
    )


def _selected(candidate: _Candidate) -> SelectedContextItem:
    return SelectedContextItem(
        item_id=candidate.item_id,
        source_type=candidate.source_type,
        text=candidate.text,
        estimated_tokens=_estimate_tokens(candidate.text),
        source_refs=candidate.source_refs,
    )


def _fits(items: Sequence[SelectedContextItem | _Candidate], budget: ContextBudget) -> bool:
    rendered = _render(items)
    return len(rendered) <= budget.max_chars and _estimate_tokens(rendered) <= budget.max_tokens


__all__ = [
    "ContextAssembler",
    "ContextBudget",
    "ContextBundle",
    "ContextDropReason",
    "ContextSourceType",
    "DroppedContextItem",
    "SelectedContextItem",
]
