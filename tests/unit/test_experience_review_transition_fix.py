"""Focused Gate 4 governance and hard-boundary regressions."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from linkloom.experience.models import (
    ExperienceContextSection,
    ExperienceRecord,
    ExperienceReviewDecision,
    ExperienceReviewTransition,
    ExperienceSelection,
)
from linkloom.experience.store import ExperienceStore, ExperienceStoreIntegrityError
from tests.experience_authority_support import fixture_authority, fixture_candidate


def _decision(record: ExperienceRecord, decision: str = "accepted") -> ExperienceReviewDecision:
    return ExperienceReviewDecision(
        schema_version="experience-review/v1",
        experience_id=record.experience_id,
        decision=decision,
        actor="reviewer:gate4",
        source="independent_reviewer",
        evidence_ref=f"review:gate4:{decision}",
        reviewed_at="2026-09-16T04:00:00+00:00",
    )


def _clock() -> datetime:
    return datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)


def test_record_create_and_constructor_cannot_bypass_review() -> None:
    authority = fixture_authority()
    candidate = fixture_candidate(authority)

    with pytest.raises(ValueError, match="candidate|review"):
        ExperienceRecord.create(**(candidate.creation_payload() | {"status": "accepted"}))
    with pytest.raises(ValueError, match="candidate|review"):
        ExperienceRecord.create(**(candidate.creation_payload() | {"status": "rejected"}))
    with pytest.raises(ValueError, match="review"):
        replace(candidate, status="accepted")
    with pytest.raises(ValueError, match="review"):
        replace(candidate, status="rejected")


def test_review_transition_is_audited_and_survives_reload(tmp_path) -> None:
    authority = fixture_authority()
    store = ExperienceStore(
        tmp_path / "experience.jsonl", authority=authority, clock=_clock
    )
    candidate = store.save(fixture_candidate(authority))
    decision = _decision(candidate)

    accepted = store.record_review(decision)

    assert accepted.status == "accepted"
    transition = accepted.review_transition
    assert isinstance(transition, ExperienceReviewTransition)
    assert transition.candidate_experience_id == candidate.experience_id
    assert transition.candidate_event_id == store.events[0]["event_id"]
    assert transition.review_decision == decision
    assert transition.review_event_id == store.events[1]["event_id"]
    assert len(transition.store_identity_sha256) == 64
    assert store.validate_reviewed_record(accepted) == accepted

    reloaded = ExperienceStore(
        store.log_path, authority=authority, clock=_clock
    )
    assert reloaded.get(candidate.experience_id) == accepted
    assert reloaded.validate_reviewed_record(accepted) == accepted


def test_candidate_without_review_and_rejected_review_are_not_accepted(tmp_path) -> None:
    authority = fixture_authority()
    candidate_store = ExperienceStore(
        tmp_path / "candidate.jsonl", authority=authority, clock=_clock
    )
    candidate = candidate_store.save(fixture_candidate(authority))
    assert candidate.status == "candidate"
    assert candidate.review_transition is None

    rejected_store = ExperienceStore(
        tmp_path / "rejected.jsonl", authority=authority, clock=_clock
    )
    rejected = rejected_store.record_review(
        _decision(rejected_store.save(fixture_candidate(authority)), "rejected")
    )
    assert rejected.status == "rejected"
    assert rejected.review_transition is not None
    assert rejected_store.list(status="accepted") == ()
    assert rejected_store.validate_reviewed_record(rejected) == rejected


def test_fabricated_or_tampered_review_metadata_fails_fresh_replay(tmp_path) -> None:
    authority = fixture_authority()
    store = ExperienceStore(
        tmp_path / "experience.jsonl", authority=authority, clock=_clock
    )
    candidate = store.save(fixture_candidate(authority))
    accepted = store.record_review(_decision(candidate))
    transition = accepted.review_transition
    assert transition is not None

    forged = replace(
        accepted,
        review_transition=replace(
            transition,
            candidate_event_id="f" * 64,
            store_identity_sha256="e" * 64,
        ),
    )
    with pytest.raises((ValueError, ExperienceStoreIntegrityError), match="review|event|provenance|identity"):
        store.validate_reviewed_record(forged)

    object.__setattr__(store._records[accepted.experience_id], "review_transition", forged.review_transition)
    with pytest.raises((ValueError, ExperienceStoreIntegrityError), match="review|event|provenance|identity"):
        store.get(accepted.experience_id)


def test_selection_and_context_reject_more_than_five_records() -> None:
    authority = fixture_authority()
    candidate = fixture_candidate(authority)
    records = (candidate,) * 6

    with pytest.raises(ValueError, match="top.k|bound"):
        ExperienceSelection(records=records, total_rendered_chars=0)
    with pytest.raises(ValueError, match="top.k|bound"):
        ExperienceContextSection(
            model_text="context",
            experience_ids=tuple(f"{index:064x}" for index in range(6)),
            provenance=(candidate.provenance[0],),
        )
