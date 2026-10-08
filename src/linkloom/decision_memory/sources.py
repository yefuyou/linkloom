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
        evidence_hashes: Mapping[str, Mapping[str, str]] | None = None,
        evidence_versions: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._episodes = {
            workspace_id: {item.strip() for item in values if item.strip()}
            for workspace_id, values in (episodes or {}).items()
        }
        self._evidence = {
            workspace_id: {item.strip() for item in values if item.strip()}
            for workspace_id, values in (evidence or {}).items()
        }
        self._evidence_hashes = {
            workspace_id: {ref.strip(): digest for ref, digest in values.items()}
            for workspace_id, values in (evidence_hashes or {}).items()
        }
        self._evidence_versions = {
            workspace_id: {ref.strip(): version for ref, version in values.items()}
            for workspace_id, values in (evidence_versions or {}).items()
        }

    def register_episode(self, workspace_id: str, episode_id: str) -> None:
        self._episodes.setdefault(workspace_id, set()).add(episode_id.strip())

    def register_evidence(
        self,
        workspace_id: str,
        evidence_ref: str,
        *,
        content_hash: str | None = None,
        source_version: str | None = None,
    ) -> None:
        """Register the current source snapshot for a workspace evidence ref.

        Re-registration is an explicit source update. Persisted candidates and
        decision evidence retain their captured hash/version for audit and are
        invalidated by the store when the current snapshot no longer matches.
        """
        workspace = workspace_id.strip()
        reference = evidence_ref.strip()
        if not workspace or not reference:
            raise ValueError("workspace_id and evidence_ref must be non-empty")
        if source_version is not None and (
            not isinstance(source_version, str) or not source_version.strip()
        ):
            raise ValueError("source_version must be non-empty")
        self._evidence.setdefault(workspace, set()).add(reference)
        if content_hash is not None:
            self._evidence_hashes.setdefault(workspace, {})[reference] = content_hash
        if source_version is not None:
            self._evidence_versions.setdefault(workspace, {})[reference] = source_version

    def unregister_evidence(self, workspace_id: str, evidence_ref: str) -> None:
        self._evidence.get(workspace_id, set()).discard(evidence_ref)
        self._evidence_hashes.get(workspace_id, {}).pop(evidence_ref, None)
        self._evidence_versions.get(workspace_id, {}).pop(evidence_ref, None)

    def unregister_episode(self, workspace_id: str, episode_id: str) -> None:
        self._episodes.get(workspace_id, set()).discard(episode_id)

    def has_episode(self, workspace_id: str, episode_id: str) -> bool:
        return episode_id in self._episodes.get(workspace_id, set())

    def has_workspace(self, workspace_id: str) -> bool:
        return workspace_id in self._episodes or workspace_id in self._evidence

    def has_evidence(self, workspace_id: str, evidence_ref: str) -> bool:
        return evidence_ref in self._evidence.get(workspace_id, set())

    def evidence_hash(self, workspace_id: str, evidence_ref: str) -> str | None:
        return self._evidence_hashes.get(workspace_id, {}).get(evidence_ref)

    def evidence_version(self, workspace_id: str, evidence_ref: str) -> str | None:
        """Return the registered immutable source version for an evidence ref."""
        return self._evidence_versions.get(workspace_id, {}).get(evidence_ref)

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
