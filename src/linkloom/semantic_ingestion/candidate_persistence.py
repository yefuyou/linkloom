"""Versioned serialization for durable, non-authoritative Step 2 candidates."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json

from linkloom.semantic_ingestion.artifacts import EvidenceSpan
from linkloom.semantic_ingestion.candidate_models import (
    CandidateConfidence,
    CandidateDecisionFact,
    CandidateReasonCode,
    ClaimType,
    EntityResolution,
    ExtractionReceipt,
    RelationResolution,
    TemporalBasis,
    TemporalResolution,
)


_PAYLOAD_VERSION = "candidate-decision-fact/v1"


def candidate_payload_dict(candidate: CandidateDecisionFact) -> dict[str, object]:
    if not isinstance(candidate, CandidateDecisionFact):
        raise TypeError("candidate must be CandidateDecisionFact")
    span = candidate.provenance
    return {
        "record_version": _PAYLOAD_VERSION,
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "workspace_id": candidate.workspace_id,
            "artifact_id": candidate.artifact_id,
            "artifact_version_id": candidate.artifact_version_id,
            "source_episode_id": candidate.source_episode_id,
            "source_ref": candidate.source_ref,
            "content_sha256": candidate.content_sha256,
            "segment_id": candidate.segment_id,
            "message_id": candidate.message_id,
            "evidence_ref": candidate.evidence_ref,
            "provenance": {
                "workspace_id": span.workspace_id,
                "artifact_id": span.artifact_id,
                "artifact_version_id": span.artifact_version_id,
                "source_ref": span.source_ref,
                "content_hash": span.content_hash,
                "ordinal": span.ordinal,
                "message_id": span.message_id,
                "segment_id": span.segment_id,
                "evidence_ref": span.evidence_ref,
                "char_start": span.char_start,
                "char_end": span.char_end,
                "line_start": span.line_start,
                "line_end": span.line_end,
                "quote": span.quote,
                "quote_sha256": span.quote_sha256,
            },
            "claim_type": candidate.claim_type.value,
            "subject": candidate.subject,
            "subject_key": candidate.subject_key,
            "relation": candidate.relation,
            "relation_phrase": candidate.relation_phrase,
            "value": candidate.value,
            "relation_resolution": candidate.relation_resolution.value,
            "entity_resolution": candidate.entity_resolution.value,
            "event_time": candidate.event_time.isoformat(),
            "ingestion_time": candidate.ingestion_time.isoformat(),
            "valid_from": _serialize_temporal(candidate.valid_from),
            "valid_to": _serialize_temporal(candidate.valid_to),
            "temporal_resolution": candidate.temporal_resolution.value,
            "temporal_basis": candidate.temporal_basis.value,
            "confidence": candidate.confidence.to_dict(),
            "extraction_fingerprint": candidate.extraction_fingerprint,
            "extraction_receipt": candidate.extraction_receipt.to_dict(),
            "review_reasons": [reason.value for reason in candidate.review_reasons],
            "candidate_status": candidate.candidate_status,
            "authority": candidate.authority,
        },
    }


def serialize_candidate(candidate: CandidateDecisionFact) -> tuple[str, str]:
    payload = candidate_payload_dict(candidate)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deserialize_candidate(payload_json: str) -> CandidateDecisionFact:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("stored semantic candidate payload is invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"record_version", "candidate"}:
        raise ValueError("stored semantic candidate payload has unsupported fields")
    if payload["record_version"] != _PAYLOAD_VERSION:
        raise ValueError("stored semantic candidate payload version is unsupported")
    data = payload["candidate"]
    expected = {
        "candidate_id",
        "workspace_id",
        "artifact_id",
        "artifact_version_id",
        "source_episode_id",
        "source_ref",
        "content_sha256",
        "segment_id",
        "message_id",
        "evidence_ref",
        "provenance",
        "claim_type",
        "subject",
        "subject_key",
        "relation",
        "relation_phrase",
        "value",
        "relation_resolution",
        "entity_resolution",
        "event_time",
        "ingestion_time",
        "valid_from",
        "valid_to",
        "temporal_resolution",
        "temporal_basis",
        "confidence",
        "extraction_fingerprint",
        "extraction_receipt",
        "review_reasons",
        "candidate_status",
        "authority",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("stored CandidateDecisionFact has missing or unsupported fields")
    raw_span = data["provenance"]
    if not isinstance(raw_span, dict):
        raise ValueError("stored candidate provenance is malformed")
    raw_confidence = data["confidence"]
    if not isinstance(raw_confidence, dict) or set(raw_confidence) != {
        "claim_type",
        "entity",
        "relation",
        "temporal",
        "overall",
    }:
        raise ValueError("stored candidate confidence is malformed")
    raw_reasons = data["review_reasons"]
    if not isinstance(raw_reasons, list) or any(not isinstance(item, str) for item in raw_reasons):
        raise ValueError("stored candidate review reasons are malformed")

    return CandidateDecisionFact(
        candidate_id=data["candidate_id"],
        workspace_id=data["workspace_id"],
        artifact_id=data["artifact_id"],
        artifact_version_id=data["artifact_version_id"],
        source_episode_id=data["source_episode_id"],
        source_ref=data["source_ref"],
        content_sha256=data["content_sha256"],
        segment_id=data["segment_id"],
        message_id=data["message_id"],
        evidence_ref=data["evidence_ref"],
        provenance=EvidenceSpan(**raw_span),
        claim_type=ClaimType(data["claim_type"]),
        subject=data["subject"],
        subject_key=data["subject_key"],
        relation=data["relation"],
        relation_phrase=data["relation_phrase"],
        value=data["value"],
        relation_resolution=RelationResolution(data["relation_resolution"]),
        entity_resolution=EntityResolution(data["entity_resolution"]),
        event_time=datetime.fromisoformat(data["event_time"]),
        ingestion_time=datetime.fromisoformat(data["ingestion_time"]),
        valid_from=_deserialize_temporal(data["valid_from"]),
        valid_to=_deserialize_temporal(data["valid_to"]),
        temporal_resolution=TemporalResolution(data["temporal_resolution"]),
        temporal_basis=TemporalBasis(data["temporal_basis"]),
        confidence=CandidateConfidence(**raw_confidence),
        extraction_fingerprint=data["extraction_fingerprint"],
        extraction_receipt=ExtractionReceipt.from_dict(data["extraction_receipt"]),
        review_reasons=tuple(CandidateReasonCode(item) for item in raw_reasons),
        candidate_status=data["candidate_status"],
        authority=data["authority"],
    )


def _serialize_temporal(value: date | datetime | None) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    return {"kind": "date", "value": value.isoformat()}


def _deserialize_temporal(value: object) -> date | datetime | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "value"}
        or not isinstance(value["value"], str)
    ):
        raise ValueError("stored temporal value is malformed")
    if value["kind"] == "datetime":
        return datetime.fromisoformat(value["value"])
    if value["kind"] == "date":
        return date.fromisoformat(value["value"])
    raise ValueError("stored temporal value has an unsupported kind")
