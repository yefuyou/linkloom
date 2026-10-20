"""Generate proposals only from current durably accepted Experience sources."""
from datetime import datetime, timezone

from linkloom.experience.store import ExperienceStore

from .models import ExperienceApprovalReference, StrategyCandidate, canonical_hash
from .policy import validate_record


def approval_reference(record, store):
    if type(store) is not ExperienceStore or record.status != "accepted":
        raise ValueError("source must be accepted in the configured Experience Store")
    store.validate_reviewed_record(record)
    transition = record.review_transition
    return ExperienceApprovalReference(record.experience_id, transition.candidate_event_id,
                                       transition.review_event_id, canonical_hash(transition.review_decision.to_dict()),
                                       transition.store_identity_sha256)


def validate_sources(candidate, store):
    validate_record(candidate)
    if type(store) is not ExperienceStore:
        raise ValueError("configured Experience Store required")
    for reference in candidate.source_review_receipts:
        record = store.get(reference.experience_id)
        if record is None or record.generation_rule_id != "explicit_rejection_requires_evidence/v1":
            raise ValueError("unknown or insufficient source Experience")
        if approval_reference(record, store) != reference:
            raise ValueError("source approval differs from current persisted receipt")


class StrategyGenerator:
    def __init__(self, source_store, *, clock=lambda: datetime.now(timezone.utc)):
        if type(source_store) is not ExperienceStore:
            raise ValueError("configured Experience Store required")
        self._source_store = source_store
        self._clock = clock

    def generate(self, experience_ids=None):
        records = self._source_store.list() if experience_ids is None else tuple(
            self._source_store.get(identity) for identity in experience_ids)
        if any(record is None for record in records):
            raise ValueError("orphan Experience identity")
        result = {}
        for record in records:
            if record.status != "accepted" or record.generation_rule_id != "explicit_rejection_requires_evidence/v1":
                continue
            reference = approval_reference(record, self._source_store)
            now = self._clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise ValueError("clock must return a timezone-aware datetime")
            candidate = StrategyCandidate.create(source_review_receipts=(reference,), created_at=now.astimezone(timezone.utc).isoformat())
            validate_sources(candidate, self._source_store)
            result[candidate.strategy_id] = candidate
        return tuple(result[key] for key in sorted(result))
