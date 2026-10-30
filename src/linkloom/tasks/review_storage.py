"""Independent append-only review audit, sharing TaskStore's locked transaction."""
from __future__ import annotations

import json
from uuid import uuid4

from linkloom.tasks.models import canonical_json
from linkloom.tasks.store import TaskSchemaError, TaskStore


_TABLES = {
    'task_review_schema_metadata': {'id', 'version'},
    'task_review_source_change': {'sequence', 'workspace_id', 'source_id', 'old_version_id',
                                  'new_version_id', 'old_active', 'new_active'},
    'task_review_event': {'sequence', 'id', 'workspace_id', 'task_id', 'candidate_id', 'action',
                         'review_status', 'candidate_fingerprint', 'candidate_json',
                         'original_values_json', 'final_values_json', 'human_fields_json',
                         'field_sources_json', 'task_event_ids_json', 'reviewer', 'reason', 'reviewed_at'},
}
_JSON_FIELDS = ('candidate', 'original_values', 'final_values', 'human_fields', 'field_sources', 'task_event_ids')
# rebind/invalidate/invalidated are reserved for the already specified 2B.
# Stage 2A exposes none of these operations; reserving them avoids rebuilding a table.
_DDL = (
    '''CREATE TABLE task_review_schema_metadata (
        id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)''',
    '''CREATE TABLE task_review_source_change (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
        source_id TEXT NOT NULL, old_version_id TEXT NOT NULL, new_version_id TEXT NOT NULL,
        old_active INTEGER NOT NULL, new_active INTEGER NOT NULL)''',
    '''CREATE TABLE task_review_event (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL, task_id TEXT, candidate_id TEXT NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('confirm','reject','edit','needs_review','reconfirm','rebind','invalidate')),
        review_status TEXT NOT NULL CHECK(review_status IN ('approved','needs_review','rejected','invalidated')),
        candidate_fingerprint TEXT NOT NULL, candidate_json TEXT NOT NULL,
        original_values_json TEXT NOT NULL, final_values_json TEXT NOT NULL,
        human_fields_json TEXT NOT NULL, field_sources_json TEXT NOT NULL,
        task_event_ids_json TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        FOREIGN KEY(task_id,workspace_id) REFERENCES task(id,workspace_id))''',
    'CREATE INDEX task_review_task_history ON task_review_event(workspace_id,task_id,sequence)',
    'CREATE INDEX task_review_candidate_history ON task_review_event(workspace_id,candidate_id,sequence)',
    'INSERT INTO task_review_schema_metadata(id,version) VALUES(1,1)',
)
_TRIGGERS = {
    f'{table}_append_only_{operation.lower()}': f'''CREATE TRIGGER {table}_append_only_{operation.lower()}
        BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT,'task review records are append-only'); END'''
    for table in ('task_review_event', 'task_review_source_change') for operation in ('UPDATE', 'DELETE')
}
_TRIGGERS['task_review_source_epoch'] = '''CREATE TRIGGER task_review_source_epoch
    AFTER UPDATE OF current_version_id,active ON ll_source
    WHEN NEW.current_version_id IS NOT OLD.current_version_id OR NEW.active IS NOT OLD.active
    BEGIN
        INSERT INTO task_review_source_change(workspace_id,source_id,old_version_id,new_version_id,old_active,new_active)
        VALUES(OLD.workspace_id,OLD.source_id,OLD.current_version_id,NEW.current_version_id,OLD.active,NEW.active);
    END'''


class ReviewStorage:
    """Does not own the connection. Internal methods require store._transaction()."""

    def __init__(self, store: TaskStore):
        self.store = store
        with store._transaction():
            connection = store._connection
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'll_source' not in tables:
                raise TaskSchemaError('initialize the durable source catalogue before task review')
            if not tables.intersection(_TABLES):
                for statement in _DDL:
                    connection.execute(statement)
                for statement in _TRIGGERS.values():
                    connection.execute(statement)
            else:
                if not set(_TABLES).issubset(tables):
                    raise TaskSchemaError('incomplete task review schema')
                for table, expected in _TABLES.items():
                    actual = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
                    if actual != expected:
                        raise TaskSchemaError('unsupported task review table shape')
                version = [tuple(row) for row in connection.execute('SELECT id,version FROM task_review_schema_metadata')]
                if version != [(1, 1)]:
                    raise TaskSchemaError('unsupported task review schema version')
                # Reject missing/replaced history guards; never silently repair an unknown schema.
                stored = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
                normalize = lambda value: ' '.join(value.split()).rstrip(';')
                if any(name not in stored or normalize(stored[name]) != normalize(sql)
                       for name, sql in _TRIGGERS.items()):
                    raise TaskSchemaError('task review history constraints are missing or unsupported')

    @property
    def connection(self):
        return self.store._connection

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        event = dict(row)
        for field in _JSON_FIELDS:
            event[field] = json.loads(event.pop(field + '_json'))
        return event

    def latest(self, workspace_id, *, task_id=None, candidate_id=None):
        field, identifier = ('task_id', task_id) if task_id is not None else ('candidate_id', candidate_id)
        return self._decode(self.connection.execute(
            f'SELECT * FROM task_review_event WHERE workspace_id=? AND {field}=? ORDER BY sequence DESC LIMIT 1',
            (workspace_id, identifier)).fetchone())

    def history(self, workspace_id, task_id):
        return tuple(self._decode(row) for row in self.connection.execute(
            'SELECT * FROM task_review_event WHERE workspace_id=? AND task_id=? ORDER BY sequence',
            (workspace_id, task_id)))

    def append(self, *, workspace_id, task_id, snapshot, action, review_status, original_values,
               final_values, human_fields, field_sources, task_event_ids, reviewer, reason, when):
        columns = ('id', 'workspace_id', 'task_id', 'candidate_id', 'action', 'review_status',
                   'candidate_fingerprint', *[name + '_json' for name in _JSON_FIELDS],
                   'reviewer', 'reason', 'reviewed_at')
        values = ('task_review_' + uuid4().hex, workspace_id, task_id, snapshot.candidate.candidate_id,
                  action, review_status, snapshot.fingerprint,
                  *[canonical_json(value) for value in (snapshot.to_dict(), original_values, final_values,
                                                        human_fields, field_sources, task_event_ids)],
                  reviewer, reason, when.isoformat())
        cursor = self.connection.execute(
            'INSERT INTO task_review_event(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in values) + ')',
            values)
        return self._decode(self.connection.execute('SELECT * FROM task_review_event WHERE sequence=?',
                                                    (cursor.lastrowid,)).fetchone())