"""Local append-only human promotion history; never decides or auto-promotes."""
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .authority import ComparisonAuthority
from .generation import validate_sources
from .models import HumanStrategyDecision, StrategyCandidate, StrategyReviewReceipt, StrategyReviewTransition, canonical_hash, canonical_json, require_timestamp


class StrategyStore:
    def __init__(self, log_path, *, source_store, comparison_authority, vault_roots=()):
        if type(comparison_authority) is not ComparisonAuthority:
            raise ValueError("configured pinned comparison authority required")
        self.log_path = Path(log_path)
        target = self.log_path.resolve(strict=False)
        if any(target == Path(root).resolve() or target.is_relative_to(Path(root).resolve()) for root in vault_roots):
            raise ValueError("Strategy Store must remain outside every Vault")
        self.source_store = source_store
        self.comparison_authority = comparison_authority
        self.store_identity = canonical_hash(str(target))
        self._records, self._candidate_events, self._events = {}, {}, []
        self._torn_tail = False
        self.reload()

    def _replay(self):
        return StrategyStore(self.log_path, source_store=self.source_store, comparison_authority=self.comparison_authority)

    def _apply(self, event):
        if event["event_type"] == "candidate_saved":
            record = StrategyCandidate.from_dict(event["payload"])
            if record.status != "candidate" or record.strategy_id in self._records:
                raise ValueError("new candidate event must be unique and candidate-only")
            validate_sources(record, self.source_store)
            self._records[record.strategy_id] = record
            self._candidate_events[record.strategy_id] = event["event_id"]
        elif event["event_type"] == "review_recorded":
            receipt = StrategyReviewReceipt.from_dict(event["payload"])
            record = self._records.get(receipt.strategy_id)
            if record is None or record.status != "candidate" or receipt.candidate_event_id != self._candidate_events[record.strategy_id] or receipt.store_identity_sha256 != self.store_identity:
                raise ValueError("review has no matching persisted original candidate/Store")
            validate_sources(record, self.source_store)
            self.comparison_authority.validate(record, receipt.evaluator_result)
            self._records[record.strategy_id] = replace(record, status=receipt.reviewer_decision.decision,
                                                      review_transition=StrategyReviewTransition(receipt, event["event_id"]))
        else:
            raise ValueError("invalid Strategy event type")

    def reload(self):
        self._records, self._candidate_events, self._events = {}, {}, []
        self._torn_tail = False
        if not self.log_path.exists():
            return
        lines = self.log_path.read_bytes().splitlines(keepends=True)
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                if index == len(lines) - 1 and not line.endswith(b"\n"):
                    self._torn_tail = True
                    break  # Retain the incomplete bytes; never remove valid history.
                raise ValueError("corrupt complete Strategy event")
            if not isinstance(event, dict) or set(event) != {"schema_version", "event_id", "previous_event_id", "store_identity", "event_type", "timestamp", "payload"}:
                raise ValueError("invalid closed Strategy event")
            require_timestamp(event["timestamp"])
            if (event["schema_version"] != "strategy-store-event/v1" or event["store_identity"] != self.store_identity
                    or event["previous_event_id"] != (self._events[-1]["event_id"] if self._events else "0" * 64)
                    or event["event_id"] != canonical_hash({k: v for k, v in event.items() if k != "event_id"})):
                raise ValueError("Strategy event chain/identity integrity mismatch")
            self._apply(event)
            self._events.append(event)

    def _append(self, event_type, payload):
        self.reload()
        if self._torn_tail:
            raise ValueError("incomplete tail retained: explicit recovery required before append")
        body = {"schema_version": "strategy-store-event/v1", "event_type": event_type,
                "previous_event_id": self._events[-1]["event_id"] if self._events else "0" * 64,
                "store_identity": self.store_identity, "timestamp": datetime.now(timezone.utc).isoformat(), "payload": payload}
        event = dict(body, event_id=canonical_hash(body))
        # Verify the complete transition before any write; then fsync the event.
        self._apply(event)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        prefix = b"\n" if self.log_path.exists() and self.log_path.stat().st_size and not self.log_path.read_bytes().endswith(b"\n") else b""
        with self.log_path.open("ab") as handle:
            handle.write(prefix + canonical_json(event).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._events.append(event)

    def save(self, record):
        validate_sources(record, self.source_store)
        if record.status != "candidate":
            raise ValueError("save cannot construct/promote terminal Strategy")
        self.reload()
        previous = self._records.get(record.strategy_id)
        if previous is not None:
            if previous.identity_payload() != record.identity_payload():
                raise ValueError("Strategy identity collision")
            return self.get(record.strategy_id)
        self._append("candidate_saved", record.to_dict())
        return self.get(record.strategy_id)

    def record_review(self, decision):
        if type(decision) is not HumanStrategyDecision:
            raise ValueError("explicit human review entrypoint requires a human decision")
        decision = HumanStrategyDecision.from_dict(decision.to_dict())
        self.reload()
        record = self._records.get(decision.strategy_id)
        if record is None or record.status != "candidate":
            raise ValueError("review requires current persisted candidate")
        validate_sources(record, self.source_store)
        summary = self.comparison_authority.resolve(decision.comparison_id)
        self.comparison_authority.validate(record, summary)
        receipt = StrategyReviewReceipt(record.strategy_id, self._candidate_events[record.strategy_id], self.store_identity, summary, decision)
        self._append("review_recorded", receipt.to_dict())
        return self.get(record.strategy_id)

    def get(self, strategy_id):
        record = self._replay()._records.get(strategy_id)
        if record is not None:
            validate_sources(record, self.source_store)
        return record

    def list(self, status=None):
        if status not in {None, "candidate", "accepted", "rejected"}:
            raise ValueError("invalid status query")
        records = self._replay()._records
        return tuple(records[key] for key in sorted(records) if status is None or records[key].status == status)

    def validate_reviewed_record(self, record):
        if record.status == "candidate" or record.review_transition is None:
            raise ValueError("no persisted human review transition")
        if self.get(record.strategy_id) != record:
            raise ValueError("review differs from current persisted original candidate/receipt")
        return record
