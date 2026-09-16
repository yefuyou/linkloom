"""Append-only local persistence for reviewed Experience records."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import (
    EXPERIENCE_STATUSES,
    ExperienceProvenance,
    ExperienceRecord,
    ExperienceReviewDecision,
    ExperienceReviewTransition,
)
from .policy import validate_candidate_record, validate_experience_record


Clock = Callable[[], datetime]
_EVENT_FIELDS = frozenset(
    {"event_id", "schema_version", "event_type", "timestamp", "payload"}
)
_EVENT_SCHEMA_VERSION = "experience-store-event/v1"
_EVENT_TYPES = frozenset({"candidate_saved", "review_recorded"})


class ExperienceStoreIntegrityError(ValueError):
    """Raised when an append-only Experience log cannot be trusted."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _event_id(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "event_id"}
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


class ExperienceStore:
    """Persist candidate and explicit-review events with deterministic replay."""

    def __init__(
        self,
        log_path: str | os.PathLike[str],
        *,
        clock: Clock = _utc_now,
        authority=None,
        vault_roots: tuple[str | os.PathLike[str], ...] = (),
    ):
        self.log_path = Path(log_path)
        self._clock = clock
        self._authority = authority
        self._store_identity = hashlib.sha256(str(self.log_path.resolve(strict=False)).encode("utf-8")).hexdigest()
        self._records: dict[str, ExperienceRecord] = {}
        self.events: list[dict[str, Any]] = []
        self._valid_log_bytes: int | None = None
        self._validate_vault_boundary(vault_roots)
        self.reload()

    def _validate_vault_boundary(
        self, vault_roots: tuple[str | os.PathLike[str], ...]
    ) -> None:
        target = self.log_path.resolve(strict=False)
        for raw_root in vault_roots:
            root = Path(raw_root).resolve(strict=False)
            if target == root or target.is_relative_to(root):
                raise ValueError("Experience store must remain outside every Vault root")

    def _timestamp(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat()

    def _new_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = json.loads(_canonical_json({
            "event_id": "",
            "schema_version": _EVENT_SCHEMA_VERSION,
            "event_type": event_type,
            "timestamp": self._timestamp(),
            "payload": payload,
        }))
        event["event_id"] = _event_id(event)
        return event

    def _validate_event(self, event: Any) -> dict[str, Any]:
        if not isinstance(event, dict) or set(event) != _EVENT_FIELDS:
            raise ExperienceStoreIntegrityError("event log contains an invalid event schema")
        if event["schema_version"] != _EVENT_SCHEMA_VERSION:
            raise ExperienceStoreIntegrityError("event log schema_version is invalid")
        if event["event_type"] not in _EVENT_TYPES:
            raise ExperienceStoreIntegrityError("event log event_type is invalid")
        if not isinstance(event["payload"], dict):
            raise ExperienceStoreIntegrityError("event log payload is invalid")
        expected_payload_fields = (
            {"record"}
            if event["event_type"] == "candidate_saved"
            else {"decision"}
        )
        if set(event["payload"]) != expected_payload_fields:
            raise ExperienceStoreIntegrityError(
                "event log payload schema is invalid"
            )
        try:
            timestamp = datetime.fromisoformat(
                event["timestamp"].replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise ExperienceStoreIntegrityError(
                "event log timestamp is invalid"
            ) from exc
        if timestamp.tzinfo is None:
            raise ExperienceStoreIntegrityError("event log timestamp is invalid")
        if event["event_id"] != _event_id(event):
            raise ExperienceStoreIntegrityError("event_id integrity check failed")
        return event

    def _append(self, event: dict[str, Any]) -> None:
        directory = self.log_path.resolve(strict=False).parent
        directory.mkdir(parents=True, exist_ok=True)
        if self._valid_log_bytes is not None and self.log_path.exists():
            with self.log_path.open("r+b") as handle:
                handle.truncate(self._valid_log_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            self._valid_log_bytes = None
        line = _canonical_json(event) + "\n"
        descriptor = os.open(
            self.log_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._apply(event)
        self.events.append(event)

    def _apply(self, event: dict[str, Any]) -> None:
        if event["event_type"] == "candidate_saved":
            record = ExperienceRecord.from_dict(event["payload"]["record"])
            validate_candidate_record(record, authority=self._authority)
            existing = self._records.get(record.experience_id)
            if existing is not None:
                if existing.identity_payload() != record.identity_payload():
                    raise ExperienceStoreIntegrityError(
                        "experience_id collision with different payload"
                    )
                return
            self._records[record.experience_id] = record
            return

        decision = ExperienceReviewDecision.from_dict(event["payload"]["decision"])
        record = self._records.get(decision.experience_id)
        if record is None:
            raise ExperienceStoreIntegrityError("review references an unknown record")
        if record.status != "candidate":
            raise ExperienceStoreIntegrityError(
                "review requires a current candidate record"
            )
        validate_candidate_record(record, authority=self._authority)
        candidate_event = next((e for e in self.events
                                if e["event_type"] == "candidate_saved"
                                and e["payload"]["record"]["experience_id"] == record.experience_id), None)
        if candidate_event is None:
            raise ExperienceStoreIntegrityError("review has no persisted original candidate event")
        self._records[record.experience_id] = replace(
            record, status=decision.decision,
            review_transition=ExperienceReviewTransition(
                record.experience_id, candidate_event["event_id"], decision,
                event["event_id"], self._store_identity,
            ),
        )

    def reload(self) -> None:
        self._records.clear()
        self.events.clear()
        self._valid_log_bytes = None
        if not self.log_path.exists():
            return
        raw = self.log_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        consumed = 0
        for index, raw_line in enumerate(lines):
            line_start = consumed
            consumed += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if index == len(lines) - 1:
                    self._valid_log_bytes = line_start
                    continue
                raise ExperienceStoreIntegrityError(
                    "event log contains invalid JSON before the final line"
                ) from exc
            try:
                validated = self._validate_event(event)
                self._apply(validated)
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ExperienceStoreIntegrityError):
                    raise
                raise ExperienceStoreIntegrityError("event log is invalid") from exc
            self.events.append(validated)

    def save(self, record: ExperienceRecord) -> ExperienceRecord:
        if not isinstance(record, ExperienceRecord):
            raise ValueError("record must be an ExperienceRecord")
        validate_candidate_record(record, authority=self._authority)
        existing = self._records.get(record.experience_id)
        if existing is not None:
            if existing.identity_payload() != record.identity_payload():
                raise ExperienceStoreIntegrityError(
                    "experience_id collision with different payload"
                )
            return existing
        event = self._new_event("candidate_saved", {"record": record.to_dict()})
        self._append(event)
        return self._records[record.experience_id]

    def get(self, experience_id: str) -> ExperienceRecord | None:
        record = self._records.get(experience_id)
        if record is not None:
            validate_experience_record(record, authority=self._authority)
            if record.status != "candidate":
                self.validate_reviewed_record(record)
        return record

    def list(self, status: str | None = None) -> tuple[ExperienceRecord, ...]:
        if status is not None and status not in EXPERIENCE_STATUSES:
            raise ValueError("status must be candidate, accepted, or rejected")
        records = (
            item
            for item in self._records.values()
            if status is None or item.status == status
        )
        result = tuple(sorted(records, key=lambda item: item.experience_id))
        for record in result:
            validate_experience_record(record, authority=self._authority)
            if record.status != "candidate":
                self.validate_reviewed_record(record)
        return result

    def record_review(
        self, decision: ExperienceReviewDecision
    ) -> ExperienceRecord:
        if not isinstance(decision, ExperienceReviewDecision):
            raise ValueError("decision must be ExperienceReviewDecision")
        record = self._records.get(decision.experience_id)
        if record is None:
            raise ValueError("candidate record not found")
        if record.status != "candidate":
            raise ValueError("review requires a current candidate record")
        validate_candidate_record(record, authority=self._authority)
        persisted = ExperienceStore(self.log_path, authority=self._authority, clock=self._clock)._records.get(record.experience_id)
        if persisted != record:
            raise ExperienceStoreIntegrityError("review requires persisted original candidate identity")
        # Reparse even a frozen decision supplied by a caller before writing it.
        ExperienceReviewDecision.from_dict(decision.to_dict())
        event = self._new_event(
            "review_recorded", {"decision": decision.to_dict()}
        )
        self._append(event)
        return self._records[decision.experience_id]

    def validate_reviewed_record(self, record: ExperienceRecord) -> ExperienceRecord:
        """Re-resolve durable candidate + explicit decision; caller metadata is not proof."""
        validate_experience_record(record, authority=self._authority)
        if record.status == "candidate" or record.review_transition is None:
            raise ValueError("record has no persisted review transition")
        if record.review_transition.store_identity_sha256 != self._store_identity:
            raise ExperienceStoreIntegrityError("review store identity mismatch")
        # Replay uses _apply without public read APIs, so this does not recurse.
        replay = ExperienceStore(self.log_path, authority=self._authority, clock=self._clock)
        persisted = replay._records.get(record.experience_id)
        if persisted != record:
            raise ExperienceStoreIntegrityError("review transition differs from persisted candidate/review events")
        return record

    def provenance(
        self, experience_id: str
    ) -> tuple[ExperienceProvenance, ...]:
        record = self.get(experience_id)
        if record is None:
            raise ValueError("Experience record not found")
        return record.provenance


__all__ = ["ExperienceStore", "ExperienceStoreIntegrityError"]
