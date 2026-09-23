"""Workspace-scoped source references accepted by temporal memory writes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from linkloom.decision_memory.models import ActionRecord, DecisionRecord


class SourceReferenceRegistry:
    """Deterministic source catalog used to fail closed before persistence.

    The registry is intentionally explicit: an empty registry accepts no
    episode or evidence reference. Production integrations can populate it
    from the Context Filesystem/index manifest, while fixtures can provide a
    small known catalog without invoking a real model or provider.
    """

    def __init__(
        self,
        *,
        episodes: Mapping[str, Iterable[str]] | None = None,
        evidence: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self._episodes = {
            workspace_id: {item.strip() for item in values if item.strip()}
            for workspace_id, values in (episodes or {}).items()
        }
        self._evidence = {
            workspace_id: {item.strip() for item in values if item.strip()}
            for workspace_id, values in (evidence or {}).items()
        }

    def register_episode(self, workspace_id: str, episode_id: str) -> None:
        self._episodes.setdefault(workspace_id, set()).add(episode_id.strip())

    def register_evidence(self, workspace_id: str, evidence_ref: str) -> None:
        self._evidence.setdefault(workspace_id, set()).add(evidence_ref.strip())

    def has_episode(self, workspace_id: str, episode_id: str) -> bool:
        return episode_id in self._episodes.get(workspace_id, set())

    def has_evidence(self, workspace_id: str, evidence_ref: str) -> bool:
        return evidence_ref in self._evidence.get(workspace_id, set())

    def validate_decision(self, decision: DecisionRecord) -> None:
        if not self.has_episode(decision.workspace_id, decision.source_episode_id):
            raise ValueError(
                "unknown source_episode_id for workspace: "
                f"{decision.source_episode_id}"
            )
        self._validate_evidence(decision.workspace_id, decision.source_evidence_refs)

    def validate_action(self, action: ActionRecord) -> None:
        self._validate_evidence(action.workspace_id, action.source_evidence_refs)

    def _validate_evidence(self, workspace_id: str, evidence_refs: Iterable[str]) -> None:
        for evidence_ref in evidence_refs:
            if not self.has_evidence(workspace_id, evidence_ref):
                raise ValueError(
                    "unknown source evidence reference for workspace: "
                    f"{evidence_ref}"
                )
