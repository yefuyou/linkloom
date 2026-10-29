"""Additive SQLite storage for task snapshots, events and candidate receipts.

This layer persists explicit caller choices. It does not verify current source
permissions, infer task identity, invoke an extractor or approve a decision.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from linkloom.tasks.models import (
    EvidenceReference, ObservationLink, ReviewAction, Task, TaskCandidateInput,
    TaskEvent, TaskEventType, TaskStatus, canonical_json, required_text, utc_time,
)


class TaskNotFoundError(LookupError):
    pass


class CandidateAlreadyReviewedError(ValueError):
    pass


class TaskSchemaError(RuntimeError):
    pass


_ANY_OWNER = object()
_TABLE_COLUMNS = {
    'task_schema_metadata': {'id', 'version'},
    'task': {'id', 'workspace_id', 'title', 'owner', 'deadline', 'deadline_text', 'status',
             'source_decision_id', 'created_at', 'updated_at'},
    'task_event': {'sequence', 'id', 'task_id', 'workspace_id', 'event_type', 'old_value', 'new_value',
                   'source_version_id', 'evidence_json', 'meeting_time', 'actor', 'reason', 'created_at'},
    'task_observation_link': {'observation_id', 'workspace_id', 'task_id', 'review_action', 'reviewer',
                              'reason', 'reviewed_at', 'candidate_fingerprint'},
}
_SCHEMA = (
    """CREATE TABLE task_schema_metadata (
        id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL
    )""",
    """CREATE TABLE task (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
        owner TEXT, deadline TEXT, deadline_text TEXT,
        status TEXT NOT NULL CHECK (status IN ('open','in_progress','blocked','done','cancelled')),
        source_decision_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(id, workspace_id)
    )""",
    """CREATE TABLE task_event (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN
            ('created','owner_changed','deadline_changed','status_changed','title_changed')),
        old_value TEXT NOT NULL, new_value TEXT NOT NULL, source_version_id TEXT,
        evidence_json TEXT NOT NULL, meeting_time TEXT, actor TEXT NOT NULL,
        reason TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(task_id,workspace_id) REFERENCES task(id,workspace_id)
    )""",
    """CREATE TABLE task_observation_link (
        observation_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, task_id TEXT,
        review_action TEXT NOT NULL CHECK (review_action IN ('create','merge','reject')),
        reviewer TEXT NOT NULL, reason TEXT NOT NULL, reviewed_at TEXT NOT NULL,
        candidate_fingerprint TEXT NOT NULL,
        CHECK ((review_action='reject' AND task_id IS NULL) OR
               (review_action IN ('create','merge') AND task_id IS NOT NULL)),
        FOREIGN KEY(task_id,workspace_id) REFERENCES task(id,workspace_id)
    )""",
    'CREATE INDEX task_workspace_owner_status ON task(workspace_id,owner,status)',
    'CREATE INDEX task_event_history ON task_event(workspace_id,task_id,sequence)',
    'CREATE INDEX task_review_workspace ON task_observation_link(workspace_id,reviewed_at)',
    """CREATE TRIGGER task_event_append_only_update BEFORE UPDATE ON task_event
        BEGIN SELECT RAISE(ABORT,'task events are append-only'); END""",
    """CREATE TRIGGER task_event_append_only_delete BEFORE DELETE ON task_event
        BEGIN SELECT RAISE(ABORT,'task events are append-only'); END""",
    'INSERT INTO task_schema_metadata(id,version) VALUES (1,1)',
)


class TaskStore:
    """One serialized connection, usable by a ThreadingHTTPServer.

    Only task-owned tables are added, including when this is an existing product
    database. The existing SQLite user_version and unrelated tables are untouched.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ':memory:':
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.database_path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA foreign_keys=ON')
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def _initialize(self):
        # Serialize the schema read with creation across distinct connections,
        # not merely across threads using this instance. execute() preserves
        # the write transaction; executescript() would implicitly commit it.
        with self._transaction():
            tables = {row[0] for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            owned = set(_TABLE_COLUMNS)
            if not tables.intersection(owned):
                for statement in _SCHEMA:
                    self._connection.execute(statement)
            else:
                if not owned.issubset(tables):
                    raise TaskSchemaError('refuse incomplete or unknown task table schema')
                for table, columns in _TABLE_COLUMNS.items():
                    actual = {row['name'] for row in self._connection.execute(f'PRAGMA table_info({table})')}
                    if actual != columns:
                        raise TaskSchemaError('unsupported task table shape')
                versions = [tuple(row) for row in self._connection.execute('SELECT id,version FROM task_schema_metadata')]
                if versions != [(1, 1)]:
                    raise TaskSchemaError('unsupported task schema version')
                triggers = {row[0] for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
                if not {'task_event_append_only_update', 'task_event_append_only_delete'}.issubset(triggers):
                    raise TaskSchemaError('task history append-only constraints are missing')

    def close(self):
        with self._lock:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._connection.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    @staticmethod
    def _when(now):
        return utc_time(now if now is not None else datetime.now(UTC), 'now')

    @staticmethod
    def _candidate(workspace_id, candidate):
        workspace = required_text(workspace_id, 'workspace_id')
        if not isinstance(candidate, TaskCandidateInput) or candidate.workspace_id != workspace:
            raise ValueError('candidate is unavailable in this workspace')
        return workspace

    def _unprocessed(self, candidate):
        if self._connection.execute('SELECT 1 FROM task_observation_link WHERE observation_id=?',
                                    (candidate.candidate_id,)).fetchone():
            raise CandidateAlreadyReviewedError('candidate has already been processed')

    def _require_task(self, workspace_id, task_id):
        row = self._connection.execute('SELECT * FROM task WHERE workspace_id=? AND id=?',
                                       (workspace_id, task_id)).fetchone()
        if row is None:
            raise TaskNotFoundError('task is unavailable in this workspace')
        return Task.from_dict(dict(row))

    def _write_task(self, task, *, create=False):
        data = task.to_dict()
        if create:
            self._connection.execute('''INSERT INTO task(id,workspace_id,title,owner,deadline,deadline_text,
                status,source_decision_id,created_at,updated_at) VALUES
                (:id,:workspace_id,:title,:owner,:deadline,:deadline_text,:status,:source_decision_id,:created_at,:updated_at)''', data)
        else:
            self._connection.execute('''UPDATE task SET title=:title,owner=:owner,deadline=:deadline,
                deadline_text=:deadline_text,status=:status,updated_at=:updated_at
                WHERE id=:id AND workspace_id=:workspace_id''', data)

    def _append_event(self, task, event_type, old_value, new_value, *, evidence, meeting_time, actor, reason, now):
        versions = {item.source_version_id for item in evidence}
        version = next(iter(versions)) if len(versions) == 1 else None
        event_id = 'task_event_' + uuid4().hex
        cursor = self._connection.execute('''INSERT INTO task_event(id,task_id,workspace_id,event_type,
            old_value,new_value,source_version_id,evidence_json,meeting_time,actor,reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (event_id, task.id, task.workspace_id, event_type, canonical_json(old_value), canonical_json(new_value),
             version, canonical_json([item.to_dict() for item in evidence]),
             meeting_time.isoformat() if meeting_time else None, actor, reason, now.isoformat()))
        # Validate what will be published before committing the operation.
        return TaskEvent(cursor.lastrowid, event_id, task.id, task.workspace_id, event_type,
                         old_value, new_value, version, evidence, meeting_time, actor, reason, now)

    def _link(self, candidate, task_id, action, reviewer, reason, when):
        link = ObservationLink(candidate.candidate_id, candidate.workspace_id, task_id, action,
                               reviewer, reason, when, candidate.fingerprint)
        self._connection.execute('''INSERT INTO task_observation_link(observation_id,workspace_id,task_id,
            review_action,reviewer,reason,reviewed_at,candidate_fingerprint) VALUES (?,?,?,?,?,?,?,?)''',
            (link.observation_id, link.workspace_id, link.task_id, link.review_action.value,
             link.reviewer, link.reason, link.reviewed_at.isoformat(), link.candidate_fingerprint))
        return link

    def create_from_candidate(self, workspace_id, candidate, *, reviewer, reason, now=None) -> Task:
        workspace = self._candidate(workspace_id, candidate)
        reviewer, reason = required_text(reviewer, 'reviewer'), required_text(reason, 'reason')
        when = self._when(now)
        with self._transaction():
            self._unprocessed(candidate)
            task = Task('task_' + uuid4().hex, workspace, candidate.title, candidate.owner,
                        candidate.deadline, candidate.deadline_text, TaskStatus.OPEN,
                        candidate.source_decision_id, when, when)
            self._write_task(task, create=True)
            self._append_event(task, TaskEventType.CREATED, None, task.to_dict(), evidence=candidate.evidence,
                               meeting_time=candidate.meeting_time, actor=reviewer, reason=reason, now=when)
            self._link(candidate, task.id, ReviewAction.CREATE, reviewer, reason, when)
        return task

    def merge_candidate(self, workspace_id, task_id, candidate, *, apply_fields, reviewer, reason, now=None) -> Task:
        workspace = self._candidate(workspace_id, candidate)
        task_id = required_text(task_id, 'task_id')
        reviewer, reason = required_text(reviewer, 'reviewer'), required_text(reason, 'reason')
        when = self._when(now)
        if (not isinstance(apply_fields, (list, tuple)) or any(
                field not in ('owner', 'deadline', 'title') for field in apply_fields)
                or len(set(apply_fields)) != len(apply_fields)):
            raise ValueError('apply_fields must select owner, deadline or title once each')
        with self._transaction():
            self._unprocessed(candidate)
            task = self._require_task(workspace, task_id)
            if when < task.updated_at:
                raise ValueError('review timestamp precedes the current task update')
            changes = []
            values = {}
            for field in ('owner', 'deadline', 'title'):
                if field not in apply_fields:
                    continue
                if field == 'deadline':
                    old = dict(deadline=task.deadline, deadline_text=task.deadline_text)
                    new = dict(deadline=candidate.deadline, deadline_text=candidate.deadline_text)
                    values.update(new)
                else:
                    old, new = getattr(task, field), getattr(candidate, field)
                    values[field] = new
                if old != new:
                    changes.append((TaskEventType(field + '_changed'), old, new))
            if changes:
                task = replace(task, **values, updated_at=when)
                self._write_task(task)
                for event_type, old, new in changes:
                    self._append_event(task, event_type, old, new, evidence=candidate.evidence,
                                       meeting_time=candidate.meeting_time, actor=reviewer, reason=reason, now=when)
            self._link(candidate, task.id, ReviewAction.MERGE, reviewer, reason, when)
        return task

    def reject_candidate(self, workspace_id, candidate, *, reviewer, reason, now=None) -> ObservationLink:
        self._candidate(workspace_id, candidate)
        reviewer, reason = required_text(reviewer, 'reviewer'), required_text(reason, 'reason')
        with self._transaction():
            self._unprocessed(candidate)
            return self._link(candidate, None, ReviewAction.REJECT, reviewer, reason, self._when(now))

    def set_status(self, workspace_id, task_id, status, *, actor, reason, now=None) -> Task:
        workspace = required_text(workspace_id, 'workspace_id')
        task_id = required_text(task_id, 'task_id')
        actor, reason = required_text(actor, 'actor'), required_text(reason, 'reason')
        status, when = TaskStatus(status), self._when(now)
        with self._transaction():
            task = self._require_task(workspace, task_id)
            if when < task.updated_at:
                raise ValueError('status timestamp precedes the current task update')
            if status != task.status:
                previous = task.status.value
                task = replace(task, status=status, updated_at=when)
                self._write_task(task)
                self._append_event(task, TaskEventType.STATUS_CHANGED, previous, status.value,
                                   evidence=(), meeting_time=None, actor=actor, reason=reason, now=when)
        return task

    def get_task(self, workspace_id, task_id) -> Task | None:
        workspace = required_text(workspace_id, 'workspace_id')
        task_id = required_text(task_id, 'task_id')
        with self._lock:
            row = self._connection.execute('SELECT * FROM task WHERE workspace_id=? AND id=?',
                                           (workspace, task_id)).fetchone()
            return Task.from_dict(dict(row)) if row else None

    def list_tasks(self, workspace_id, *, owner=_ANY_OWNER, status=None) -> tuple[Task, ...]:
        """Omit owner for all tasks; owner=None selects only unassigned tasks."""
        workspace = required_text(workspace_id, 'workspace_id')
        filters, arguments = ['workspace_id=?'], [workspace]
        if owner is None:
            filters.append('owner IS NULL')
        elif owner is not _ANY_OWNER:
            filters.append('owner=?')
            arguments.append(required_text(owner, 'owner'))
        if status is not None:
            filters.append('status=?')
            arguments.append(TaskStatus(status).value)
        with self._lock:
            rows = self._connection.execute('SELECT * FROM task WHERE ' + ' AND '.join(filters)
                                            + ' ORDER BY created_at,id', arguments).fetchall()
            return tuple(Task.from_dict(dict(row)) for row in rows)

    def get_events(self, workspace_id, task_id) -> tuple[TaskEvent, ...]:
        workspace = required_text(workspace_id, 'workspace_id')
        task_id = required_text(task_id, 'task_id')
        with self._lock:
            rows = self._connection.execute('SELECT * FROM task_event WHERE workspace_id=? AND task_id=? '
                                           'ORDER BY sequence', (workspace, task_id)).fetchall()
            events = []
            for row in rows:
                data = dict(row)
                data['event_type'] = TaskEventType(data['event_type'])
                data['old_value'], data['new_value'] = json.loads(data['old_value']), json.loads(data['new_value'])
                raw_evidence = json.loads(data.pop('evidence_json'))
                if not isinstance(raw_evidence, list):
                    raise ValueError('stored event evidence must be a JSON list')
                data['evidence'] = tuple(EvidenceReference.from_dict(item) for item in raw_evidence)
                data['created_at'] = datetime.fromisoformat(data['created_at'])
                data['meeting_time'] = datetime.fromisoformat(data['meeting_time']) if data['meeting_time'] else None
                events.append(TaskEvent(**data))
            return tuple(events)

    def get_link(self, workspace_id, observation_id) -> ObservationLink | None:
        workspace = required_text(workspace_id, 'workspace_id')
        observation_id = required_text(observation_id, 'observation_id')
        with self._lock:
            row = self._connection.execute('SELECT * FROM task_observation_link WHERE workspace_id=? AND observation_id=?',
                                           (workspace, observation_id)).fetchone()
            if row is None:
                return None
            data = dict(row)
            data['reviewed_at'] = datetime.fromisoformat(data['reviewed_at'])
            return ObservationLink(**data)

    def replay_task(self, workspace_id, task_id) -> Task:
        """Reconstruct a task solely from its ordered immutable events."""
        events = self.get_events(workspace_id, task_id)
        if not events:
            raise TaskNotFoundError('task history is unavailable in this workspace')
        if events[0].event_type is not TaskEventType.CREATED:
            raise ValueError('task history must begin with created')
        task = Task.from_dict(events[0].new_value)
        for event in events[1:]:
            if event.event_type is TaskEventType.CREATED:
                raise ValueError('task history cannot contain another created event')
            if event.event_type is TaskEventType.DEADLINE_CHANGED:
                current = dict(deadline=task.deadline, deadline_text=task.deadline_text)
                changes = dict(event.new_value)
            else:
                field = event.event_type.value.removesuffix('_changed')
                current = getattr(task, field)
                if isinstance(current, TaskStatus):
                    current = current.value
                changes = {field: event.new_value}
            if current != event.old_value or event.created_at < task.updated_at:
                raise ValueError('event history does not continue the preceding snapshot')
            task = replace(task, **changes, updated_at=event.created_at)
        return task
