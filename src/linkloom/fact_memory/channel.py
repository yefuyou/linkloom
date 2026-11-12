"""Fact admission: its own checks, never a relaxed decision/task/relation rule.

A FACT candidate reaches this channel only after the unchanged decision
policy refused it. The channel admits it when, and only when:

* the candidate's evidence span still verifies against the current version
  of an active source, and the value is located literally within
  one full sentence of that quote (see ``fact_memory.evidence``);
* the subject lands on an entity (first person -> the message's speaker,
  the speaker's own label, or a mention inside the quote);
* the host-normalized attribute key is non-empty;
* the source gives the fact a time (message timestamp or document date).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
import json

from linkloom.fact_memory.evidence import verify_value
from linkloom.fact_memory.normalize import locate_value, attribute_key, normalize_value, resolve_subject, value_kind
from linkloom.fact_memory.store import FactRecord, FactStore, fact_id_for, slot_key_for
from linkloom.semantic_ingestion.candidate_models import CandidateDecisionFact, ClaimType


@dataclass(frozen=True, slots=True)
class FactAdmission:
    record: FactRecord | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactSource:
    """What the host knows about the candidate's source, read by the caller."""

    active_current: bool
    verified: bool
    speaker: str | None


def assess(candidate: CandidateDecisionFact, source: FactSource, *, subject: str | None = None,
           attribute: str | None = None) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    """Return the record fields or every reason the fact cannot be admitted."""
    reasons: list[str] = []
    if candidate.claim_type is not ClaimType.FACT:
        return None, ("FACT_CLAIM_TYPE_REQUIRED",)
    quote = candidate.provenance.quote
    if not source.active_current or not source.verified or not quote:
        reasons.append("FACT_EVIDENCE_INVALID")
    evidence = verify_value(candidate.value, quote)
    if evidence.reason:
        reasons.append(evidence.reason)
    details = candidate.fact_details
    if details is not None and details.event_time_text is not None and (
            evidence.sentence is None or locate_value(details.event_time_text, evidence.sentence) is None):
        reasons.append("FACT_EVENT_TIME_NOT_IN_SOURCE")
    resolved = resolve_subject(subject if subject is not None else candidate.subject, quote, source.speaker)
    if resolved.reason:
        reasons.append("FACT_" + resolved.reason)
    attribute_name = attribute or candidate.relation_phrase or candidate.relation
    key = attribute_key(attribute_name, subject_words=tuple(
        word for word in (candidate.subject, source.speaker, subject) if word))
    if not key.key:
        reasons.append("FACT_ATTRIBUTE_KEY_EMPTY")
    effective = _effective_at(candidate)
    if effective is None:
        reasons.append("FACT_SOURCE_TIME_MISSING")
    if reasons:
        return None, tuple(dict.fromkeys(reasons))
    # The value was located in the source; its normalized display form is not evidence.
    value_text = " ".join(candidate.value.split())
    if quote[evidence.start:evidence.end] != evidence.sentence:
        return None, ("FACT_EVIDENCE_INVALID",)
    return {
        "subject_id": resolved.subject_id, "subject_label": resolved.label, "subject_basis": resolved.basis,
        "attribute_name": attribute_name.strip(), "attribute_key": key.key, "mergeable": key.mergeable,
        "value_text": value_text, "value_normalized": normalize_value(value_text),
        "value_kind": value_kind(normalize_value(value_text)), "effective_at": effective,
        "evidence_sentence": evidence.sentence, "evidence_start": evidence.start, "evidence_end": evidence.end,
        "key_content": json.dumps(evidence.keys, ensure_ascii=False), "quote": quote,
        "fact_details_json": json.dumps(details.to_dict() if details else {}, ensure_ascii=False),
    }, ()


def admit(store: FactStore, candidate: CandidateDecisionFact, source: FactSource, *, reviewer_id: str,
          reason: str, now: datetime | None = None, subject: str | None = None,
          attribute: str | None = None) -> FactAdmission:
    existing = store.by_candidate(candidate.workspace_id, candidate.candidate_id)
    if existing is not None:
        return FactAdmission(existing, ())
    fields, reasons = assess(candidate, source, subject=subject, attribute=attribute)
    if fields is None:
        return FactAdmission(None, reasons)
    record = FactRecord(
        fact_id=fact_id_for(candidate.workspace_id, candidate.candidate_id),
        workspace_id=candidate.workspace_id,
        slot_key=slot_key_for(candidate.workspace_id, str(fields["subject_id"]), str(fields["attribute_key"]),
                              bool(fields["mergeable"]), candidate.candidate_id),
        candidate_id=candidate.candidate_id, artifact_id=candidate.artifact_id,
        artifact_version_id=candidate.artifact_version_id, evidence_ref=candidate.evidence_ref,
        reviewer_id=reviewer_id, review_reason=reason,
        recorded_at=(now or datetime.now(UTC)).astimezone(UTC), **fields)
    return FactAdmission(store.add(record), ())


def _effective_at(candidate: CandidateDecisionFact) -> datetime | None:
    if candidate.event_time is not None:
        return candidate.event_time.astimezone(UTC)
    day: date | None = candidate.event_date or candidate.document_date
    return datetime.combine(day, time.min, tzinfo=UTC) if day is not None else None
