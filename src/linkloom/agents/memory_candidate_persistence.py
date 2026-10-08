"""Versioned persistence for the original, non-authoritative Agent candidate."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json

from linkloom.agents.memory_candidate import (
    AgentActionCandidate,
    AgentEvidenceBinding,
    AgentMemoryCandidate,
    AgentMemoryReason,
    SubjectResolution,
    SubjectResolutionStatus,
    WorkspaceSubject,
)
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.semantic_ingestion.artifacts import EvidenceSpan
from linkloom.semantic_ingestion.evidence import VerifiedEvidenceBinding
from linkloom.semantic_ingestion.candidate_models import (
    ClaimType,
    RelationResolution,
    TemporalBasis,
    TemporalResolution,
)
from linkloom.semantic_ingestion.relations import RelationResolutionResult


_PAYLOAD_VERSION_V1 = "agent-memory-candidate/v1"
_PAYLOAD_VERSION_V2 = "agent-memory-candidate/v2"


def candidate_payload_dict(candidate: AgentMemoryCandidate) -> dict[str, object]:
    if not isinstance(candidate, AgentMemoryCandidate):
        raise TypeError("candidate must be AgentMemoryCandidate")
    return {
        "record_version": _PAYLOAD_VERSION_V2,
        "candidate": {
            "schema_version": candidate.schema_version,
            "candidate_id": candidate.candidate_id,
            "fingerprint": candidate.fingerprint,
            "workspace_id": candidate.workspace_id,
            "provenance_run_id": candidate.provenance_run_id,
            "team_decision_result_sha256": candidate.team_decision_result_sha256,
            "team_decision_result_json": candidate.team_decision_result_json,
            "agent_result_status": candidate.agent_result_status,
            "claim_type": candidate.claim_type.value if candidate.claim_type is not None else None,
            "decision_value": candidate.decision_value,
            "decision_evidence_refs": list(candidate.decision_evidence_refs),
            "evidence": [_evidence_to_dict(item) for item in candidate.evidence],
            "source_episode_ids": list(candidate.source_episode_ids),
            "subject_resolution": {
                "status": candidate.subject_resolution.status.value,
                "proposed_phrase": candidate.subject_resolution.proposed_phrase,
                "subject_id": candidate.subject_resolution.subject_id,
                "display_label": candidate.subject_resolution.display_label,
            },
            "subject_id": candidate.subject_id,
            "subject_label": candidate.subject_label,
            "subject_key": candidate.subject_key,
            "relation_resolution": {
                "status": candidate.relation_resolution.status.value,
                "canonical_relation": candidate.relation_resolution.canonical_relation,
                "source_phrase": candidate.relation_resolution.source_phrase,
            },
            "relation_catalog_fingerprint": candidate.relation_catalog_fingerprint,
            "subject_registry_fingerprint": candidate.subject_registry_fingerprint,
            "subject_registry_snapshot": [
                {
                    "subject_id": item.subject_id,
                    "display_label": item.display_label,
                    "confirmed_aliases": list(item.confirmed_aliases),
                }
                for item in candidate.subject_registry_snapshot
            ],
            "effective_time": _temporal_to_dict(candidate.effective_time),
            "temporal_resolution": candidate.temporal_resolution.value,
            "temporal_basis": candidate.temporal_basis.value,
            "actions": [
                {
                    "description": item.description,
                    "owner": item.owner,
                    "deadline": item.deadline,
                    "status": item.status,
                    "evidence_refs": list(item.evidence_refs),
                    "description_grounded": item.description_grounded,
                    "owner_grounded": item.owner_grounded,
                    "deadline_grounded": item.deadline_grounded,
                    "requires_review": item.requires_review,
                }
                for item in candidate.actions
            ],
            "review_reasons": [item.value for item in candidate.review_reasons],
            "authorization_status": candidate.authorization_status,
            "authority": candidate.authority,
            "candidate_status": candidate.candidate_status,
        },
    }


def serialize_candidate(candidate: AgentMemoryCandidate) -> tuple[str, str]:
    payload = candidate_payload_dict(candidate)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deserialize_candidate(payload_json: str) -> AgentMemoryCandidate:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("stored Agent memory candidate payload is invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"record_version", "candidate"}:
        raise ValueError("stored Agent memory candidate payload has unsupported fields")
    record_version = payload["record_version"]
    if record_version not in {_PAYLOAD_VERSION_V1, _PAYLOAD_VERSION_V2}:
        raise ValueError("stored Agent memory candidate payload version is unsupported")
    data = payload["candidate"]
    expected = {
        "schema_version",
        "candidate_id",
        "fingerprint",
        "workspace_id",
        "provenance_run_id",
        "team_decision_result_sha256",
        "team_decision_result_json",
        "agent_result_status",
        "claim_type",
        "decision_value",
        "decision_evidence_refs",
        "evidence",
        "source_episode_ids",
        "subject_resolution",
        "subject_id",
        "subject_label",
        "subject_key",
        "relation_resolution",
        "relation_catalog_fingerprint",
        "subject_registry_fingerprint",
        "subject_registry_snapshot",
        "effective_time",
        "temporal_resolution",
        "temporal_basis",
        "actions",
        "review_reasons",
        "authorization_status",
        "authority",
        "candidate_status",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("stored AgentMemoryCandidate has missing or unsupported fields")

    result_json = _required_string(data["team_decision_result_json"], "team_decision_result_json")
    try:
        result_dict = json.loads(result_json)
    except json.JSONDecodeError as error:
        raise ValueError("stored TeamDecisionResult snapshot is invalid JSON") from error
    if not isinstance(result_dict, dict):
        raise ValueError("stored TeamDecisionResult snapshot must be an object")
    TeamDecisionResult.from_dict(result_dict)

    raw_subject_resolution = data["subject_resolution"]
    if not isinstance(raw_subject_resolution, dict) or set(raw_subject_resolution) != {
        "status",
        "proposed_phrase",
        "subject_id",
        "display_label",
    }:
        raise ValueError("stored Agent subject resolution is malformed")
    raw_relation_resolution = data["relation_resolution"]
    if not isinstance(raw_relation_resolution, dict) or set(raw_relation_resolution) != {
        "status",
        "canonical_relation",
        "source_phrase",
    }:
        raise ValueError("stored Agent relation resolution is malformed")
    evidence = data["evidence"]
    actions = data["actions"]
    source_episode_ids = data["source_episode_ids"]
    decision_refs = data["decision_evidence_refs"]
    reasons = data["review_reasons"]
    registry_snapshot = data["subject_registry_snapshot"]
    if not isinstance(evidence, list) or not isinstance(actions, list):
        raise ValueError("stored Agent evidence and actions must be lists")
    if not isinstance(source_episode_ids, list) or not isinstance(decision_refs, list):
        raise ValueError("stored Agent evidence references must be lists")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise ValueError("stored Agent review reasons are malformed")
    if not isinstance(registry_snapshot, list):
        raise ValueError("stored Agent subject registry snapshot is malformed")

    return AgentMemoryCandidate(
        schema_version=data["schema_version"],
        candidate_id=data["candidate_id"],
        fingerprint=data["fingerprint"],
        workspace_id=data["workspace_id"],
        provenance_run_id=data["provenance_run_id"],
        team_decision_result_sha256=data["team_decision_result_sha256"],
        team_decision_result_json=result_json,
        agent_result_status=data["agent_result_status"],
        claim_type=ClaimType(data["claim_type"]) if data["claim_type"] is not None else None,
        decision_value=data["decision_value"],
        decision_evidence_refs=tuple(_string_list(decision_refs, "decision_evidence_refs")),
        evidence=tuple(
            _evidence_from_dict(item, general=record_version == _PAYLOAD_VERSION_V2)
            for item in evidence
        ),
        source_episode_ids=tuple(_string_list(source_episode_ids, "source_episode_ids")),
        subject_resolution=SubjectResolution(
            status=SubjectResolutionStatus(raw_subject_resolution["status"]),
            proposed_phrase=raw_subject_resolution["proposed_phrase"],
            subject_id=raw_subject_resolution["subject_id"],
            display_label=raw_subject_resolution["display_label"],
        ),
        subject_id=data["subject_id"],
        subject_label=data["subject_label"],
        subject_key=data["subject_key"],
        relation_resolution=RelationResolutionResult(
            status=RelationResolution(raw_relation_resolution["status"]),
            canonical_relation=raw_relation_resolution["canonical_relation"],
            source_phrase=raw_relation_resolution["source_phrase"],
        ),
        relation_catalog_fingerprint=data["relation_catalog_fingerprint"],
        subject_registry_fingerprint=data["subject_registry_fingerprint"],
        subject_registry_snapshot=tuple(_workspace_subject_from_dict(item) for item in registry_snapshot),
        effective_time=_temporal_from_dict(data["effective_time"]),
        temporal_resolution=TemporalResolution(data["temporal_resolution"]),
        temporal_basis=TemporalBasis(data["temporal_basis"]),
        actions=tuple(_action_from_dict(item) for item in actions),
        review_reasons=tuple(AgentMemoryReason(item) for item in reasons),
        authorization_status=data["authorization_status"],
        authority=data["authority"],
        candidate_status=data["candidate_status"],
    )


def _evidence_to_dict(item: AgentEvidenceBinding) -> dict[str, object]:
    return {
        "provenance": item.provenance.to_dict(),
        "claim_roles": list(item.claim_roles),
    }


def _evidence_from_dict(value: object, *, general: bool) -> AgentEvidenceBinding:
    if general:
        if not isinstance(value, dict) or set(value) != {"provenance", "claim_roles"}:
            raise ValueError("stored generalized Agent evidence binding is malformed")
        claim_roles = _string_list(value["claim_roles"], "claim_roles")
        return AgentEvidenceBinding(
            provenance=VerifiedEvidenceBinding.from_dict(value["provenance"]),
            claim_roles=tuple(claim_roles),
        )

    expected = {
        "evidence_ref",
        "source_episode_id",
        "artifact_id",
        "artifact_version_id",
        "source_ref",
        "content_sha256",
        "provenance",
        "source_speaker",
        "event_time",
        "claim_roles",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("stored Agent evidence binding is malformed")
    provenance = value["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError("stored Agent evidence provenance is malformed")
    claim_roles = _string_list(value["claim_roles"], "claim_roles")
    binding = VerifiedEvidenceBinding.from_legacy_semantic_span(
        source_episode_id=value["source_episode_id"],
        span=EvidenceSpan(**provenance),
        source_speaker=value["source_speaker"],
        event_time=datetime.fromisoformat(value["event_time"]),
    )
    if (
        value["evidence_ref"] != binding.evidence_ref
        or value["artifact_id"] != binding.source_identity
        or value["artifact_version_id"] != binding.source_version
        or value["source_ref"] != binding.source_locator
        or value["content_sha256"] != binding.content_hash
    ):
        raise ValueError("stored legacy Agent evidence fields conflict with its span")
    return AgentEvidenceBinding(provenance=binding, claim_roles=tuple(claim_roles))


def _action_from_dict(value: object) -> AgentActionCandidate:
    expected = {
        "description",
        "owner",
        "deadline",
        "status",
        "evidence_refs",
        "description_grounded",
        "owner_grounded",
        "deadline_grounded",
        "requires_review",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("stored Agent action candidate is malformed")
    return AgentActionCandidate(
        description=value["description"],
        owner=value["owner"],
        deadline=value["deadline"],
        status=value["status"],
        evidence_refs=tuple(_string_list(value["evidence_refs"], "action evidence_refs")),
        description_grounded=_bool(value["description_grounded"], "description_grounded"),
        owner_grounded=_bool(value["owner_grounded"], "owner_grounded"),
        deadline_grounded=_bool(value["deadline_grounded"], "deadline_grounded"),
        requires_review=_bool(value["requires_review"], "requires_review"),
    )


def _workspace_subject_from_dict(value: object) -> WorkspaceSubject:
    if not isinstance(value, dict) or set(value) != {
        "subject_id",
        "display_label",
        "confirmed_aliases",
    }:
        raise ValueError("stored workspace subject snapshot item is malformed")
    aliases = _string_list(value["confirmed_aliases"], "confirmed_aliases")
    return WorkspaceSubject(
        subject_id=value["subject_id"],
        display_label=value["display_label"],
        confirmed_aliases=tuple(aliases),
    )


def _temporal_to_dict(value: date | datetime | None) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    return {"kind": "date", "value": value.isoformat()}


def _temporal_from_dict(value: object) -> date | datetime | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise ValueError("stored Agent effective time is malformed")
    if value["kind"] == "date" and isinstance(value["value"], str):
        return date.fromisoformat(value["value"])
    if value["kind"] == "datetime" and isinstance(value["value"], str):
        return datetime.fromisoformat(value["value"])
    raise ValueError("stored Agent effective time kind is unsupported")


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"stored {field_name} must be a list of text")
    return value


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"stored {field_name} must be non-empty text")
    return value


def _bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"stored {field_name} must be boolean")
    return value
