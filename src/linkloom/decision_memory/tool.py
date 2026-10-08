"""Provider-neutral, read-only navigation tool for temporal decision memory."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from linkloom.decision_memory.models import (
    DecisionRecord,
    TemporalLookupResult,
)


class DecisionMemoryReader(Protocol):
    def search(
        self,
        workspace_id: str,
        query: str,
        *,
        as_of: datetime | None = None,
        limit: int = 5,
    ) -> tuple[DecisionRecord, ...]: ...


class DecisionMemoryStructuredReader(Protocol):
    def lookup(
        self,
        workspace_id: str,
        subject: str,
        relation: str,
        *,
        as_of: datetime | None = None,
    ) -> TemporalLookupResult: ...


@dataclass(frozen=True, slots=True)
class MemorySearchObservation:
    workspace_id: str
    memory_hit_count: int
    memory_latency_ms: float


@dataclass(frozen=True, slots=True)
class DecisionMemorySearchResult:
    values: list[dict[str, object]]
    observation: MemorySearchObservation


class DecisionMemorySearchTool:
    """Authorize workspace scope before any memory lookup or model exposure."""

    def __init__(
        self,
        store: DecisionMemoryReader,
        *,
        authorized_workspace_id: str,
    ) -> None:
        if not authorized_workspace_id.strip():
            raise ValueError("authorized_workspace_id must not be empty")
        self.store = store
        self.authorized_workspace_id = authorized_workspace_id

    def search(
        self,
        *,
        workspace: str,
        query: str,
        as_of: str | None,
        limit: int,
    ) -> DecisionMemorySearchResult:
        if workspace != self.authorized_workspace_id:
            raise PermissionError("workspace is not authorized for decision-memory lookup")
        if not query.strip():
            raise ValueError("decision-memory query must not be empty")
        if limit <= 0 or limit > 50:
            raise ValueError("decision-memory limit must be between 1 and 50")
        parsed_as_of = _parse_as_of(as_of)
        started = time.perf_counter()
        records = self.store.search(
            self.authorized_workspace_id,
            query,
            as_of=parsed_as_of,
            limit=limit,
        )
        values = [_record_to_navigation_hint(record) for record in records]
        return DecisionMemorySearchResult(
            values=values,
            observation=MemorySearchObservation(
                workspace_id=self.authorized_workspace_id,
                memory_hit_count=len(values),
                memory_latency_ms=(time.perf_counter() - started) * 1000.0,
            ),
        )

    def lookup(
        self,
        *,
        workspace: str,
        subject: str,
        relation: str,
        as_of: str | None = None,
    ) -> TemporalLookupResult:
        """Use exact structured lookup without changing legacy lexical search."""
        if workspace != self.authorized_workspace_id:
            raise PermissionError("workspace is not authorized for decision-memory lookup")
        parsed_as_of = _parse_as_of(as_of)
        if not callable(getattr(self.store, "lookup", None)):
            raise TypeError("decision-memory store does not support structured lookup")
        reader = cast(DecisionMemoryStructuredReader, self.store)
        return reader.lookup(
            self.authorized_workspace_id,
            subject,
            relation,
            as_of=parsed_as_of,
        )


def _parse_as_of(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("as_of must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return parsed


def _record_to_navigation_hint(record: DecisionRecord) -> dict[str, object]:
    return {
        "decision_id": record.decision_id,
        "workspace_id": record.workspace_id,
        "subject_key": record.subject_key,
        "value": record.value,
        "status": record.status.value,
        "valid_from": record.valid_from.isoformat(),
        "valid_to": record.valid_to.isoformat() if record.valid_to is not None else None,
        "supersedes_id": record.supersedes_id,
        "source_episode_id": record.source_episode_id,
        "source_evidence_refs": list(record.source_evidence_refs),
        "provenance_run_id": record.provenance_run_id,
        "grounding_role": "navigation_only",
    }
