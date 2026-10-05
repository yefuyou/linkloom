"""Callable consistency reconciliation for temporal decision memory."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from linkloom.decision_memory.models import DecisionMemoryState, DecisionStatus
from linkloom.decision_memory.store import TemporalDecisionStore


@dataclass(frozen=True, slots=True)
class DecisionReconciliationIssue:
    code: str
    workspace_id: str
    resource_id: str
    detail: str
    evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionStateTransition:
    entity_type: str
    entity_id: str
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class DecisionReconciliationReport:
    decisions_scanned: int
    candidates_scanned: int
    actions_scanned: int
    issues: tuple[DecisionReconciliationIssue, ...]
    state_transitions: tuple[DecisionStateTransition, ...]

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class DecisionReconciler:
    """Compare memory provenance and relational integrity with a source snapshot."""

    def __init__(self, store: TemporalDecisionStore) -> None:
        self.store = store

    def reconcile(
        self,
        source_inventory: Mapping[str, Mapping[str, str | None] | Iterable[str]],
    ) -> DecisionReconciliationReport:
        inventory = _normalize_inventory(source_inventory)
        connection = self.store._connection
        issues: list[DecisionReconciliationIssue] = []
        transitions: list[DecisionStateTransition] = []
        decision_rows = connection.execute(
            "SELECT * FROM decision ORDER BY workspace_id, subject_key, valid_from, decision_id"
        ).fetchall()
        candidate_rows = connection.execute(
            "SELECT * FROM decision_candidate ORDER BY workspace_id, subject_key, created_at, candidate_id"
        ).fetchall()
        action_rows = connection.execute(
            "SELECT * FROM action ORDER BY workspace_id, action_id"
        ).fetchall()

        with connection:
            active_rows = [
                row
                for row in decision_rows
                if row["status"] == DecisionStatus.CURRENT.value and row["valid_to"] is None
            ]
            current_counts = Counter(
                (str(row["workspace_id"]), str(row["subject_key"]))
                for row in active_rows
                if row["memory_state"] == DecisionMemoryState.ACTIVE.value
            )
            for (workspace_id, subject_key), count in sorted(current_counts.items()):
                if count > 1:
                    issues.append(
                        DecisionReconciliationIssue(
                            code="duplicate_current_truth",
                            workspace_id=workspace_id,
                            resource_id=subject_key,
                            detail=f"{count} active current decisions share this workspace and subject",
                        )
                    )

            decisions_by_id = {str(row["decision_id"]): row for row in decision_rows}
            for row in decision_rows:
                supersedes_id = row["supersedes_id"]
                if supersedes_id is None:
                    continue
                parent = decisions_by_id.get(str(supersedes_id))
                broken = (
                    parent is None
                    or str(parent["workspace_id"]) != str(row["workspace_id"])
                    or str(parent["subject_key"]) != str(row["subject_key"])
                    or str(parent["valid_from"]) >= str(row["valid_from"])
                    or parent["valid_to"] != row["valid_from"]
                )
                if broken:
                    issues.append(
                        DecisionReconciliationIssue(
                            code="broken_supersession_chain",
                            workspace_id=str(row["workspace_id"]),
                            resource_id=str(row["decision_id"]),
                            detail=f"supersession target {supersedes_id!r} is missing or temporally inconsistent",
                        )
                    )

            for row in decision_rows:
                refs = connection.execute(
                    "SELECT evidence_ref, source_hash FROM decision_evidence "
                    "WHERE decision_id = ? ORDER BY ordinal, evidence_ref",
                    (row["decision_id"],),
                ).fetchall()
                source_state, source_issues = _source_state(
                    workspace_id=str(row["workspace_id"]),
                    resource_id=str(row["decision_id"]),
                    refs=refs,
                    inventory=inventory,
                )
                issues.extend(source_issues)
                desired = (
                    DecisionMemoryState.SUPERSEDED
                    if source_state is DecisionMemoryState.ACTIVE
                    and row["status"] == DecisionStatus.SUPERSEDED.value
                    else source_state
                )
                if not self.store._source_registry.has_episode(
                    str(row["workspace_id"]), str(row["source_episode_id"])
                ):
                    issues.append(
                        DecisionReconciliationIssue(
                            code="invalid_provenance",
                            workspace_id=str(row["workspace_id"]),
                            resource_id=str(row["decision_id"]),
                            detail="source episode is not registered for this workspace",
                        )
                    )
                    desired = DecisionMemoryState.INVALIDATED
                current_state = DecisionMemoryState(str(row["memory_state"]))
                if desired is not current_state:
                    self.store._set_decision_memory_state(str(row["decision_id"]), desired)
                    transitions.append(
                        DecisionStateTransition(
                            entity_type="decision",
                            entity_id=str(row["decision_id"]),
                            from_state=current_state.value,
                            to_state=desired.value,
                        )
                    )
                    connection.execute(
                        "UPDATE decision_candidate SET status = ?, outcome = ? "
                        "WHERE decision_id = ?",
                        (desired.value, desired.value, row["decision_id"]),
                    )

            for row in candidate_rows:
                if row["status"] != DecisionMemoryState.CANDIDATE.value:
                    continue
                refs = json.loads(str(row["source_evidence_refs"]))
                source_hashes = json.loads(str(row["source_hashes"]))
                evidence_rows = [
                    {"evidence_ref": ref, "source_hash": source_hashes.get(ref)}
                    for ref in refs
                ]
                desired, source_issues = _source_state(
                    workspace_id=str(row["workspace_id"]),
                    resource_id=str(row["candidate_id"]),
                    refs=evidence_rows,
                    inventory=inventory,
                )
                issues.extend(source_issues)
                if desired is not DecisionMemoryState.ACTIVE:
                    connection.execute(
                        "UPDATE decision_candidate SET status = ?, outcome = ? "
                        "WHERE candidate_id = ?",
                        (desired.value, desired.value, row["candidate_id"]),
                    )
                    transitions.append(
                        DecisionStateTransition(
                            entity_type="candidate",
                            entity_id=str(row["candidate_id"]),
                            from_state=DecisionMemoryState.CANDIDATE.value,
                            to_state=desired.value,
                        )
                    )

            for row in action_rows:
                parent = decisions_by_id.get(str(row["source_decision_id"]))
                if (
                    parent is None
                    or str(parent["workspace_id"]) != str(row["workspace_id"])
                ):
                    issues.append(
                        DecisionReconciliationIssue(
                            code="orphan_action",
                            workspace_id=str(row["workspace_id"]),
                            resource_id=str(row["action_id"]),
                            detail=f"source decision {row['source_decision_id']!r} is missing or belongs to another workspace",
                        )
                    )
                    continue
                action_refs = connection.execute(
                    "SELECT evidence_ref, source_hash FROM action_evidence "
                    "WHERE action_id = ? ORDER BY ordinal, evidence_ref",
                    (row["action_id"],),
                ).fetchall()
                _, action_source_issues = _source_state(
                    workspace_id=str(row["workspace_id"]),
                    resource_id=str(row["action_id"]),
                    refs=action_refs,
                    inventory=inventory,
                )
                issues.extend(
                    DecisionReconciliationIssue(
                        code=(
                            "stale_action_evidence"
                            if item.code == "missing_source_reference"
                            else item.code
                        ),
                        workspace_id=item.workspace_id,
                        resource_id=item.resource_id,
                        detail=item.detail,
                        evidence_ref=item.evidence_ref,
                    )
                    for item in action_source_issues
                )

        issues.sort(
            key=lambda issue: (
                issue.code,
                issue.workspace_id,
                issue.resource_id,
                issue.evidence_ref or "",
            )
        )
        transitions.sort(key=lambda item: (item.entity_type, item.entity_id))
        return DecisionReconciliationReport(
            decisions_scanned=len(decision_rows),
            candidates_scanned=len(candidate_rows),
            actions_scanned=len(action_rows),
            issues=tuple(issues),
            state_transitions=tuple(transitions),
        )


def _normalize_inventory(
    source_inventory: Mapping[str, Mapping[str, str | None] | Iterable[str]],
) -> dict[str, dict[str, str | None]]:
    if not isinstance(source_inventory, Mapping):
        raise ValueError("source_inventory must be workspace-scoped mapping data")
    result: dict[str, dict[str, str | None]] = {}
    for workspace_id, items in source_inventory.items():
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("source_inventory workspace ids must be non-empty strings")
        if isinstance(items, Mapping):
            normalized = {}
            for evidence_ref, digest in items.items():
                if not isinstance(evidence_ref, str) or not evidence_ref.strip():
                    raise ValueError("source_inventory evidence refs must be non-empty strings")
                if digest is not None and not isinstance(digest, str):
                    raise ValueError("source_inventory hashes must be strings or None")
                normalized[evidence_ref] = digest
        else:
            if isinstance(items, (str, bytes)) or not isinstance(items, Iterable):
                raise ValueError("source_inventory entries must be mappings or iterables of refs")
            normalized = {str(ref): None for ref in items}
        result[workspace_id] = normalized
    return result


def _source_state(
    *,
    workspace_id: str,
    resource_id: str,
    refs,
    inventory: Mapping[str, Mapping[str, str | None]],
) -> tuple[DecisionMemoryState, list[DecisionReconciliationIssue]]:
    workspace_inventory = inventory.get(workspace_id, {})
    if not refs:
        return DecisionMemoryState.STALE, [
            DecisionReconciliationIssue(
                code="missing_source_reference",
                workspace_id=workspace_id,
                resource_id=resource_id,
                detail="memory record has no source evidence references",
            )
        ]
    valid = 0
    missing = 0
    changed = 0
    unknown_hash = 0
    issues: list[DecisionReconciliationIssue] = []
    for row in refs:
        evidence_ref = str(row["evidence_ref"])
        if evidence_ref not in workspace_inventory:
            missing += 1
            issues.append(
                DecisionReconciliationIssue(
                    code="missing_source_reference",
                    workspace_id=workspace_id,
                    resource_id=resource_id,
                    detail="source evidence reference is absent from the source inventory",
                    evidence_ref=evidence_ref,
                )
            )
            continue
        original_hash = row["source_hash"]
        current_hash = workspace_inventory[evidence_ref]
        if original_hash is not None and current_hash is None:
            unknown_hash += 1
            continue
        if original_hash is not None and current_hash != original_hash:
            changed += 1
            issues.append(
                DecisionReconciliationIssue(
                    code="changed_source_reference",
                    workspace_id=workspace_id,
                    resource_id=resource_id,
                    detail="source evidence content hash differs from its materialization snapshot",
                    evidence_ref=evidence_ref,
                )
            )
            continue
        valid += 1
    if missing == 0 and changed == 0 and unknown_hash == 0:
        return DecisionMemoryState.ACTIVE, issues
    if valid > 0:
        return DecisionMemoryState.NEEDS_REVALIDATION, issues
    if changed > 0:
        return DecisionMemoryState.STALE, issues
    if unknown_hash > 0:
        return DecisionMemoryState.NEEDS_REVALIDATION, issues
    return DecisionMemoryState.STALE, issues


__all__ = [
    "DecisionReconciliationIssue",
    "DecisionReconciliationReport",
    "DecisionReconciler",
    "DecisionStateTransition",
]
