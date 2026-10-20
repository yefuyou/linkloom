import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from linkloom.experience.models import (
    ExperienceApplicability,
    ExperienceProvenance,
    ExperienceRecord,
    ExperienceReviewDecision,
)
from linkloom.experience.store import (
    ExperienceStore,
    ExperienceStoreIntegrityError,
)
from linkloom.experience.policy import ExperiencePolicyViolation


from functools import partial
from tests.experience_authority_support import fixture_authority, fixture_candidate

AUTHORITY = fixture_authority()
ExperienceStore = partial(ExperienceStore, authority=AUTHORITY)


def candidate(*, created_at: str = "2026-09-16T01:00:00+00:00") -> ExperienceRecord:
    return fixture_candidate(AUTHORITY, created_at=created_at)


def review(record: ExperienceRecord, decision: str = "accepted") -> ExperienceReviewDecision:
    return ExperienceReviewDecision(
        schema_version="experience-review/v1",
        experience_id=record.experience_id,
        decision=decision,
        actor="reviewer:gate4",
        source="independent_reviewer",
        evidence_ref=f"review:gate4:{decision}",
        reviewed_at="2026-09-16T02:00:00+00:00",
    )


def clock() -> datetime:
    return datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)


def test_save_get_list_provenance_and_reload_are_append_only(tmp_path: Path) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    record = candidate()

    saved = store.save(record)

    assert saved == record
    assert store.get(record.experience_id) == record
    assert store.list() == (record,)
    assert store.list(status="candidate") == (record,)
    assert store.provenance(record.experience_id) == record.provenance
    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 1

    reloaded = ExperienceStore(log_path, clock=clock)
    assert reloaded.get(record.experience_id) == record
    assert reloaded.events == store.events


def test_identical_candidate_save_is_idempotent_even_if_time_differs(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    first = candidate(created_at="2026-09-16T01:00:00+00:00")
    later = candidate(created_at="2026-09-17T01:00:00+00:00")

    assert first.experience_id == later.experience_id
    assert store.save(first) == first
    assert store.save(later) == first
    assert len(store.events) == 1


def test_explicit_review_is_required_for_terminal_status_and_replays(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    record = store.save(candidate())

    accepted = store.record_review(review(record))

    assert accepted.status == "accepted"
    assert store.list(status="candidate") == ()
    assert store.list(status="accepted") == (accepted,)
    assert store.provenance(record.experience_id) == record.provenance
    assert ExperienceStore(log_path, clock=clock).get(record.experience_id) == accepted

    with pytest.raises(ValueError, match="candidate"):
        store.record_review(review(record, decision="rejected"))


def test_rejected_record_remains_auditable(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path / "experience.jsonl", clock=clock)
    record = store.save(candidate())

    rejected = store.record_review(review(record, decision="rejected"))

    assert rejected.status == "rejected"
    assert store.get(record.experience_id) == rejected
    assert store.list(status="rejected") == (rejected,)
    assert len(store.events) == 2


def test_save_rejects_non_candidate_unregistered_and_identity_collision(
    tmp_path: Path,
) -> None:
    store = ExperienceStore(tmp_path / "experience.jsonl", clock=clock)
    record = candidate()
    with pytest.raises(ValueError, match="candidate"):
        store.save(replace(record, status="accepted"))

    tampered = candidate()
    object.__setattr__(tampered, "reusable_lesson", "tampered payload")
    store.save(record)
    with pytest.raises(ValueError, match="provenance|identity"):
        store.save(tampered)


def test_store_revalidates_source_evidence_resolution_before_persisting(
    tmp_path: Path,
) -> None:
    store = ExperienceStore(tmp_path / "experience.jsonl", clock=clock)
    record = candidate()
    object.__setattr__(
        record.provenance[0],
        "available_source_evidence_refs",
        (record.source_evidence_refs[0],),
    )

    with pytest.raises(ExperiencePolicyViolation, match="provenance|identity"):
        store.save(record)

    assert not store.log_path.exists()


def test_reload_ignores_only_a_torn_final_line(tmp_path: Path) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    record = store.save(candidate())
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_id":"torn"')

    reloaded = ExperienceStore(log_path, clock=clock)
    assert reloaded.get(record.experience_id) == record

    valid_line = log_path.read_text(encoding="utf-8").splitlines()[0]
    log_path.write_text(
        valid_line + "\n" + "{bad-json}\n" + valid_line + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ExperienceStoreIntegrityError, match="event log"):
        ExperienceStore(log_path, clock=clock)


def test_event_integrity_is_verified_on_reload(tmp_path: Path) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    store.save(candidate())
    event = json.loads(log_path.read_text(encoding="utf-8"))
    event["event_id"] = "f" * 64
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ExperienceStoreIntegrityError, match="event_id"):
        ExperienceStore(log_path, clock=clock)


def test_event_payload_schema_is_closed_on_reload(tmp_path: Path) -> None:
    log_path = tmp_path / "experience.jsonl"
    store = ExperienceStore(log_path, clock=clock)
    store.save(candidate())
    event = json.loads(log_path.read_text(encoding="utf-8"))
    event["payload"]["ignored_extra"] = "not allowed"
    unsigned = {key: value for key, value in event.items() if key != "event_id"}
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    event["event_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ExperienceStoreIntegrityError, match="payload"):
        ExperienceStore(log_path, clock=clock)


def test_store_refuses_log_inside_declared_vault_root(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    with pytest.raises(ValueError, match="Vault"):
        ExperienceStore(
            vault_root / "experience.jsonl",
            clock=clock,
            vault_roots=(vault_root,),
        )
