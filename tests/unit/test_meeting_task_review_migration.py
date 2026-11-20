"""Unit tests for B1-2: migrating MeetingTaskReview writes to formal TaskStore/TaskEvent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from linkloom.semantic_ingestion.artifacts import (
    RawArtifact,
    evidence_ref_for,
    line_range_for,
    message_id_for,
    segment_id_for,
)
from linkloom.semantic_ingestion.meeting_tasks_v3 import TaskExtractionConfig
from linkloom.tasks.models import TaskEventType, TaskStatus
from linkloom.tasks.store import TaskStore
from linkloom.ui.meeting_task_review import MeetingTaskReview


class _FakeAdapter:
    model_id = "fake-model"

    def complete(self, request):
        return None


class _FakeRepo:
    def __init__(self, db_path: Path, source_content: str) -> None:
        self.database_path = str(db_path)
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self.source_content = source_content
        self.review_counts: list[tuple[str, str, str, int]] = []

    def get_source(self, workspace_id: str, source_id: str):
        return {
            "workspace_id": workspace_id,
            "source_id": source_id,
            "name": "meeting-1.md",
            "source_version": "ver-1",
            "active": 1,
        }

    def set_review_required_count(self, workspace_id: str, source_id: str, source_version: str, count: int) -> None:
        self.review_counts.append((workspace_id, source_id, source_version, count))


class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeApp:
    def __init__(self, db_path: Path, source_content: str) -> None:
        self.repository = _FakeRepo(db_path, source_content)
        self._store_lock = _FakeLock()

    def _source_artifact(self, source: dict) -> RawArtifact:
        return RawArtifact(
            workspace_id=source["workspace_id"],
            artifact_id=source["source_id"],
            source_type="timestamped_text",
            content=self.repository.source_content,
        )


class TestMeetingTaskReviewMigration(unittest.TestCase):
    def _seed_pending(
        self,
        review_svc: MeetingTaskReview,
        *,
        candidate_id: str,
        quote: str,
        content: str,
        title: str,
        owner: str,
        deadline: str,
    ) -> None:
        artifact = review_svc.app._source_artifact({"workspace_id": "ws-1", "source_id": "src-1"})
        start = content.index(quote)
        end = start + len(quote)
        line_start, line_end = line_range_for(content, start, end)
        quote_sha256 = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        msg_id = message_id_for(artifact.artifact_version_id, 0)
        seg_id = segment_id_for(artifact.artifact_version_id, msg_id, start, end, quote_sha256)
        ev_ref = evidence_ref_for(seg_id)
        payload = {
            "candidate_id": candidate_id,
            "task": title,
            "deliverable_key": title.lower(),
            "owner_ids": [owner],
            "deadline_text": deadline,
            "reason": "Extracted from meeting",
            "evidence": [
                {
                    "supports": ["work", "owner", "deadline"],
                    "span": {
                        "workspace_id": "ws-1",
                        "artifact_id": "src-1",
                        "artifact_version_id": artifact.artifact_version_id,
                        "source_ref": artifact.source_ref,
                        "content_hash": artifact.content_hash,
                        "ordinal": 0,
                        "message_id": msg_id,
                        "segment_id": seg_id,
                        "evidence_ref": ev_ref,
                        "char_start": start,
                        "char_end": end,
                        "line_start": line_start,
                        "line_end": line_end,
                        "quote": quote,
                        "quote_sha256": quote_sha256,
                    },
                }
            ],
        }
        review_svc.db.execute(
            "INSERT INTO ll_meeting_task_review VALUES (?, ?, ?, ?, ?, 'PENDING_REVIEW', NULL, NULL)",
            ("ws-1", candidate_id, "src-1", "ver-1", json.dumps(payload, ensure_ascii=False)),
        )
        review_svc.db.commit()

    def test_confirm_creates_formal_task_and_merge_appends_events(self) -> None:
        content = (
            "Li: I will ship the payment API by 2026-10-12.\n"
            "Wang: Updated plan: Wang takes over payment API, due 2026-10-16.\n"
        )
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "product.sqlite"
            app = _FakeApp(db_path, content)
            task_store = TaskStore(db_path)
            try:
                svc = MeetingTaskReview(
                    app,
                    _FakeAdapter(),
                    TaskExtractionConfig(task_extraction_mode="meeting_v2"),
                    [],
                    lambda *a, **kw: None,
                    task_store=task_store,
                )
                self._seed_pending(
                    svc,
                    candidate_id="cand-1",
                    quote="Li: I will ship the payment API by 2026-10-12.",
                    content=content,
                    title="Ship payment API",
                    owner="Li",
                    deadline="2026-10-12",
                )
                self._seed_pending(
                    svc,
                    candidate_id="cand-2",
                    quote="Wang: Updated plan: Wang takes over payment API, due 2026-10-16.",
                    content=content,
                    title="Ship payment API",
                    owner="Wang",
                    deadline="2026-10-16",
                )

                created_res = svc.review(
                    "ws-1",
                    "cand-1",
                    {
                        "action": "CONFIRM",
                        "source_version": "ver-1",
                        "reviewer": "pm-1",
                        "note": "Approved initial task",
                    },
                )
                self.assertEqual(created_res["authority"], "CONFIRMED")
                task_id = created_res["task_id"]
                formal_tasks = svc.list_formal_tasks("ws-1")
                self.assertEqual(len(formal_tasks), 1)
                self.assertEqual(formal_tasks[0]["owner"], "Li")
                self.assertEqual(formal_tasks[0]["deadline"], "2026-10-12")
                self.assertEqual(formal_tasks[0]["status"], TaskStatus.OPEN.value)

                merged_res = svc.review(
                    "ws-1",
                    "cand-2",
                    {
                        "action": "MERGE",
                        "target_task_id": task_id,
                        "apply_fields": ["owner", "deadline"],
                        "source_version": "ver-1",
                        "reviewer": "pm-1",
                        "note": "Owner and deadline updated in follow-up",
                    },
                )
                self.assertEqual(merged_res["authority"], "CONFIRMED")
                self.assertEqual(merged_res["task"]["owner"], "Wang")
                self.assertEqual(merged_res["task"]["deadline"], "2026-10-16")

                events = svc.get_formal_task_events("ws-1", task_id)
                event_types = [e["event_type"] for e in events]
                self.assertEqual(
                    event_types,
                    [
                        TaskEventType.CREATED.value,
                        TaskEventType.OWNER_CHANGED.value,
                        TaskEventType.DEADLINE_CHANGED.value,
                    ],
                )
            finally:
                task_store.close()
                app.repository._connection.close()

    def test_reject_records_rejection_without_creating_formal_task(self) -> None:
        content = "Someone suggested maybe rewriting the whole portal.\n"
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "product.sqlite"
            app = _FakeApp(db_path, content)
            task_store = TaskStore(db_path)
            try:
                svc = MeetingTaskReview(
                    app,
                    _FakeAdapter(),
                    TaskExtractionConfig(task_extraction_mode="meeting_v2"),
                    [],
                    lambda *a, **kw: None,
                    task_store=task_store,
                )
                self._seed_pending(
                    svc,
                    candidate_id="cand-rej",
                    quote="Someone suggested maybe rewriting the whole portal.",
                    content=content,
                    title="Rewrite portal",
                    owner="Alex",
                    deadline="2026-11-01",
                )
                res = svc.review(
                    "ws-1",
                    "cand-rej",
                    {
                        "action": "REJECT",
                        "source_version": "ver-1",
                        "reviewer": "pm-1",
                        "note": "Only a passing suggestion",
                    },
                )
                self.assertEqual(res, {"status": "REJECTED_BY_HUMAN", "authority": "REJECTED"})
                self.assertEqual(svc.list_formal_tasks("ws-1"), [])
            finally:
                task_store.close()
                app.repository._connection.close()

    def test_legacy_update_failure_after_formal_write_recovers_from_receipt(self) -> None:
        # TaskStore and ll_meeting_task_review commit on different connections; inject a
        # failure between them and check that the receipt makes recovery idempotent.
        content = "Li: I will ship the payment API by 2026-10-12.\nSomeone suggested rewriting the portal.\n"

        class _FailingLegacyUpdate:
            def __init__(self, connection):
                self._connection, self.armed = connection, True

            def execute(self, sql, *args):
                if self.armed and sql.startswith("UPDATE ll_meeting_task_review"):
                    self.armed = False
                    raise sqlite3.OperationalError("database is locked")
                return self._connection.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._connection, name)

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "product.sqlite"
            app = _FakeApp(db_path, content)
            task_store = TaskStore(db_path)
            try:
                svc = MeetingTaskReview(
                    app,
                    _FakeAdapter(),
                    TaskExtractionConfig(task_extraction_mode="meeting_v2"),
                    [],
                    lambda *a, **kw: None,
                    task_store=task_store,
                )
                self._seed_pending(svc, candidate_id="cand-1", quote="Li: I will ship the payment API by 2026-10-12.",
                                   content=content, title="Ship payment API", owner="Li", deadline="2026-10-12")
                self._seed_pending(svc, candidate_id="cand-rej", quote="Someone suggested rewriting the portal.",
                                   content=content, title="Rewrite portal", owner="Alex", deadline="2026-11-01")
                legacy = svc.db
                confirm = {"action": "CONFIRM", "source_version": "ver-1", "reviewer": "pm-1", "note": "Approved"}
                reject = {"action": "REJECT", "source_version": "ver-1", "reviewer": "pm-1", "note": "Only a suggestion"}

                svc.db = _FailingLegacyUpdate(legacy)
                with self.assertRaises(sqlite3.OperationalError):
                    svc.review("ws-1", "cand-1", confirm)
                svc.db = legacy
                self.assertEqual(len(svc.list_formal_tasks("ws-1")), 1)

                # The formal receipt hides the candidate from the queue even before a retry.
                self.assertEqual([item["candidate_id"] for item in svc.pending("ws-1")], ["cand-rej"])
                status = legacy.execute("SELECT status,reviewer,note FROM ll_meeting_task_review WHERE candidate_id='cand-1'").fetchone()
                self.assertEqual(tuple(status), ("CONFIRMED_BY_HUMAN", "pm-1", "Approved"))

                # A retry neither creates a second task nor reports a false conflict.
                svc.db = _FailingLegacyUpdate(legacy)
                with self.assertRaises(sqlite3.OperationalError):
                    svc.review("ws-1", "cand-rej", reject)
                svc.db = legacy
                retried = svc.review("ws-1", "cand-rej", reject)
                self.assertEqual(retried, {"status": "REJECTED_BY_HUMAN", "authority": "REJECTED"})
                self.assertEqual(svc.pending("ws-1"), [])
                self.assertEqual(len(svc.list_formal_tasks("ws-1")), 1)
                self.assertEqual(app.repository.review_counts[-1], ("ws-1", "src-1", "ver-1", 0))
                with self.assertRaisesRegex(Exception, "already reviewed"):
                    svc.review("ws-1", "cand-1", confirm)
            finally:
                task_store.close()
                app.repository._connection.close()


if __name__ == "__main__":
    unittest.main()
