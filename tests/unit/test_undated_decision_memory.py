"""Unit tests for confirmed undated decisions (B1-3 / P0-1)."""

from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import (
    DecisionMemoryState,
    DecisionRecord,
    DecisionStatus,
    DecisionTimeBasis,
    TemporalLookupStatus,
    UndatedDecisionState,
    decision_slot_key_for,
)
from linkloom.decision_memory.reconciliation import DecisionReconciler
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore


def _make_store(db_path: Path) -> tuple[TemporalDecisionStore, SourceReferenceRegistry]:
    registry = SourceReferenceRegistry()
    registry.register_episode("ws-alpha", "ep-1")
    registry.register_episode("ws-alpha", "ep-2")
    registry.register_evidence("ws-alpha", "src-1#L1-L2", content_hash="hash-v1")
    registry.register_evidence("ws-alpha", "src-2#L1-L2", content_hash="hash-v2")
    store = TemporalDecisionStore(db_path, source_registry=registry)
    return store, registry


def _seed_dated_decision(
    store: TemporalDecisionStore,
    *,
    decision_id: str = "dec-1",
    value: str = "Birchline",
    valid_from: datetime = datetime(2026, 10, 1, tzinfo=UTC),
) -> DecisionRecord:
    slot_key = decision_slot_key_for("Payment Vendor", "uses vendor")
    assert slot_key is not None
    record = DecisionRecord(
        decision_id=decision_id,
        workspace_id="ws-alpha",
        subject_key=slot_key,
        subject="Payment Vendor",
        relation="uses vendor",
        value=value,
        status=DecisionStatus.CURRENT,
        valid_from=valid_from,
        valid_to=None,
        supersedes_id=None,
        source_episode_id="ep-1",
        source_evidence_refs=("src-1#L1-L2",),
        provenance_run_id="run-1",
    )
    materializer = DecisionMaterializer(store)
    candidate = materializer.propose(
        record,
        team_decision_contract_pass=True,
        grounding_pass=True,
    )
    return materializer.materialize(candidate, approved_by="reviewer-1")


class TestUndatedDecisionMemory(unittest.TestCase):
    def test_confirm_undated_and_conservative_lookup(self) -> None:
        """E11 + E13: Confirmed undated decision never impersonates dated AS_OF/CURRENT."""
        with TemporaryDirectory() as tmp:
            store, _ = _make_store(Path(tmp) / "decisions.sqlite")
            try:
                self.assertEqual(store.schema_version, 3)
                dated = _seed_dated_decision(store)

                undated, receipt = store.confirm_undated_decision(
                    workspace_id="ws-alpha",
                    subject="Payment Vendor",
                    relation="uses vendor",
                    value="Wrenwell",
                    source_episode_id="ep-2",
                    source_evidence_refs=("src-2#L1-L2",),
                    provenance_run_id="run-2",
                    authorization_id="reviewer-1",
                    authorization_reason="Confirmed content from meeting notes; effective date unknown",
                )
                self.assertEqual(undated.memory_state, UndatedDecisionState.ACTIVE)
                self.assertEqual(undated.time_basis, DecisionTimeBasis.UNKNOWN)
                self.assertEqual(receipt.undated_decision_id, undated.undated_decision_id)

                # Receipts are append-only / immutable via SQLite triggers.
                with self.assertRaises(sqlite3.IntegrityError):
                    store._connection.execute(
                        "UPDATE undated_decision_receipt SET authorization_reason = 'tampered' WHERE receipt_id = ?",
                        (receipt.receipt_id,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    store._connection.execute(
                        "DELETE FROM undated_decision_receipt WHERE receipt_id = ?",
                        (receipt.receipt_id,),
                    )

                # Legacy lookup() stays untouched and only sees the dated decision.
                legacy = store.lookup(
                    "ws-alpha",
                    "Payment Vendor",
                    "uses vendor",
                    as_of=datetime(2026, 10, 7, tzinfo=UTC),
                )
                self.assertEqual(legacy.status, TemporalLookupStatus.FOUND)
                self.assertEqual([r.decision_id for r in legacy.records], [dated.decision_id])

                # AS_OF mode returns dated result plus undated note, without claiming CURRENT uncertainty.
                as_of_view = store.lookup_decision_state(
                    "ws-alpha",
                    "Payment Vendor",
                    "uses vendor",
                    as_of=datetime(2026, 10, 7, tzinfo=UTC),
                    mode="AS_OF",
                )
                self.assertEqual(as_of_view.temporal_result.status, TemporalLookupStatus.FOUND)
                self.assertEqual(len(as_of_view.undated_decisions), 1)
                self.assertFalse(as_of_view.current_uncertain_due_to_undated)

                # CURRENT mode flags uncertainty instead of guessing ordering from ingestion time.
                current_view = store.lookup_decision_state(
                    "ws-alpha",
                    "Payment Vendor",
                    "uses vendor",
                    as_of=datetime(2026, 10, 20, tzinfo=UTC),
                    mode="CURRENT",
                )
                self.assertTrue(current_view.current_uncertain_due_to_undated)
                self.assertEqual(current_view.undated_decisions[0].value, "Wrenwell")
            finally:
                store.close()

    def test_promote_undated_to_dated_atomically_preserves_receipt_and_supersedes(self) -> None:
        """E12: Promoting an undated decision atomically creates a dated decision and supersedes predecessor."""
        with TemporaryDirectory() as tmp:
            store, _ = _make_store(Path(tmp) / "decisions.sqlite")
            try:
                dated = _seed_dated_decision(store, valid_from=datetime(2026, 10, 1, tzinfo=UTC))
                undated, initial_receipt = store.confirm_undated_decision(
                    workspace_id="ws-alpha",
                    subject="Payment Vendor",
                    relation="uses vendor",
                    value="Wrenwell",
                    source_episode_id="ep-2",
                    source_evidence_refs=("src-2#L1-L2",),
                    provenance_run_id="run-2",
                    authorization_id="reviewer-1",
                    authorization_reason="Confirmed switch to Wrenwell, date pending",
                    supersedes_decision_id=dated.decision_id,
                )

                # Invalid earlier date must fail cleanly and roll back without altering undated or dated rows.
                with self.assertRaises(ValueError):
                    store.promote_undated_decision_to_dated(
                        workspace_id="ws-alpha",
                        undated_decision_id=undated.undated_decision_id,
                        valid_from=datetime(2026, 9, 25, tzinfo=UTC),
                        time_basis=DecisionTimeBasis.USER_SELECTED,
                        authorization_id="reviewer-1",
                        authorization_reason="Backdated conflict",
                    )
                still_active = store.get_undated_decision("ws-alpha", undated.undated_decision_id)
                assert still_active is not None
                self.assertEqual(still_active.memory_state, UndatedDecisionState.ACTIVE)

                # Valid promotion with USER_TODAY closes Birchline and activates Wrenwell.
                promoted_dated, updated_undated, promo_receipt = store.promote_undated_decision_to_dated(
                    workspace_id="ws-alpha",
                    undated_decision_id=undated.undated_decision_id,
                    valid_from=datetime(2026, 10, 8, tzinfo=UTC),
                    time_basis=DecisionTimeBasis.USER_TODAY,
                    authorization_id="reviewer-1",
                    authorization_reason="Reviewer clicked Fill Today",
                )
                self.assertEqual(promoted_dated.value, "Wrenwell")
                self.assertEqual(promoted_dated.supersedes_id, dated.decision_id)
                self.assertEqual(updated_undated.memory_state, UndatedDecisionState.PROMOTED_TO_DATED)
                self.assertEqual(updated_undated.promoted_decision_id, promoted_dated.decision_id)

                # Original confirmation receipt is preserved alongside promotion receipt.
                receipts = store.get_undated_decision_receipts("ws-alpha", undated.undated_decision_id)
                self.assertEqual(len(receipts), 2)
                self.assertEqual(receipts[0].receipt_id, initial_receipt.receipt_id)
                self.assertEqual(receipts[1].receipt_id, promo_receipt.receipt_id)
                self.assertIn("USER_TODAY", receipts[1].authorization_reason)

                # Historical AS_OF Oct 7 returns Birchline; CURRENT Oct 10 returns Wrenwell with no undated uncertainty.
                oct7 = store.lookup_decision_state(
                    "ws-alpha",
                    "Payment Vendor",
                    "uses vendor",
                    as_of=datetime(2026, 10, 7, tzinfo=UTC),
                    mode="AS_OF",
                )
                self.assertEqual(oct7.temporal_result.records[0].value, "Birchline")
                self.assertEqual(oct7.undated_decisions, ())

                oct10 = store.lookup_decision_state(
                    "ws-alpha",
                    "Payment Vendor",
                    "uses vendor",
                    as_of=datetime(2026, 10, 10, tzinfo=UTC),
                    mode="CURRENT",
                )
                self.assertEqual(oct10.temporal_result.records[0].value, "Wrenwell")
                self.assertFalse(oct10.current_uncertain_due_to_undated)

                # Double promotion is rejected.
                with self.assertRaises(ValueError):
                    store.promote_undated_decision_to_dated(
                        workspace_id="ws-alpha",
                        undated_decision_id=undated.undated_decision_id,
                        valid_from=datetime(2026, 10, 12, tzinfo=UTC),
                        time_basis=DecisionTimeBasis.USER_SELECTED,
                        authorization_id="reviewer-1",
                        authorization_reason="Duplicate promotion",
                    )
            finally:
                store.close()

    def test_reconciliation_and_revocation_for_undated_decisions(self) -> None:
        """E22: Source hash changes mark undated STALE; revocation is terminal and never resurrects old state."""
        with TemporaryDirectory() as tmp:
            store, _ = _make_store(Path(tmp) / "decisions.sqlite")
            try:
                undated, _ = store.confirm_undated_decision(
                    workspace_id="ws-alpha",
                    subject="Payment Vendor",
                    relation="uses vendor",
                    value="Wrenwell",
                    source_episode_id="ep-2",
                    source_evidence_refs=("src-2#L1-L2",),
                    provenance_run_id="run-2",
                    authorization_id="reviewer-1",
                    authorization_reason="Confirmed content",
                )
                reconciler = DecisionReconciler(store)

                # Changed hash transitions ACTIVE -> STALE.
                report = reconciler.reconcile({"ws-alpha": {"src-2#L1-L2": "hash-v2-edited"}})
                self.assertEqual(report.undated_decisions_scanned, 1)
                stale = store.get_undated_decision("ws-alpha", undated.undated_decision_id)
                assert stale is not None
                self.assertEqual(stale.memory_state, UndatedDecisionState.STALE)

                # Revoking transitions to terminal REVOKED; restoring original hash must NOT resurrect it.
                revoked = store.revoke_undated_decision("ws-alpha", undated.undated_decision_id)
                self.assertEqual(revoked.memory_state, UndatedDecisionState.REVOKED)
                reconciler.reconcile({"ws-alpha": {"src-2#L1-L2": "hash-v2"}})
                after = store.get_undated_decision("ws-alpha", undated.undated_decision_id)
                assert after is not None
                self.assertEqual(after.memory_state, UndatedDecisionState.REVOKED)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
