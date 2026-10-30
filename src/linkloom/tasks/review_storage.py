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


_BINDING_COLUMNS = {'review_event_id', 'workspace_id', 'old_candidate_fingerprint',
    'new_candidate_fingerprint', 'source_proof_json', 'field_choices_json', 'legacy_basis_json', 'attestation_json',
    'operation', 'target_review_event_id'}
_BINDING_DDL = (
    'CREATE UNIQUE INDEX task_review_id_workspace ON task_review_event(id,workspace_id)',
    """CREATE TABLE task_review_binding_detail (
        review_event_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK(operation IN ('rebind','invalidate','undo_invalidate')),
        target_review_event_id TEXT,
        old_candidate_fingerprint TEXT NOT NULL, new_candidate_fingerprint TEXT,
        source_proof_json TEXT NOT NULL, field_choices_json TEXT NOT NULL,
        legacy_basis_json TEXT NOT NULL, attestation_json TEXT NOT NULL,
        FOREIGN KEY(review_event_id,workspace_id) REFERENCES task_review_event(id,workspace_id),
        FOREIGN KEY(target_review_event_id,workspace_id) REFERENCES task_review_event(id,workspace_id))""",
)
_BINDING_TRIGGERS = {
    f'task_review_binding_detail_append_only_{operation.lower()}':
    f"""CREATE TRIGGER task_review_binding_detail_append_only_{operation.lower()}
        BEFORE {operation} ON task_review_binding_detail
        BEGIN SELECT RAISE(ABORT,'task binding details are append-only'); END"""
    for operation in ('UPDATE', 'DELETE')
}


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
            self._initialize_binding_details()

    def _initialize_binding_details(self):
        # Pure additive extension: both original task/review metadata remain v1.
        connection = self.connection
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_review_binding_detail'").fetchone()
        if not exists:
            for statement in (*_BINDING_DDL, *_BINDING_TRIGGERS.values()):
                connection.execute(statement)
        else:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(task_review_binding_detail)')}
            if columns != _BINDING_COLUMNS:
                raise TaskSchemaError('unsupported task binding detail schema')
            triggers = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            normalize = lambda value: ' '.join(value.split()).rstrip(';')
            if any(name not in triggers or normalize(triggers[name]) != normalize(sql)
                   for name, sql in _BINDING_TRIGGERS.items()):
                raise TaskSchemaError('task binding detail guards are missing or unsupported')
            index = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='task_review_id_workspace'").fetchone()
            if index is None or normalize(index[0]) != normalize(_BINDING_DDL[0]):
                raise TaskSchemaError('task binding workspace index is missing or unsupported')

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
        return self._with_details(self.connection.execute(
            f'SELECT * FROM task_review_event WHERE workspace_id=? AND {field}=? ORDER BY sequence DESC LIMIT 1',
            (workspace_id, identifier)).fetchone())

    def history(self, workspace_id, task_id):
        return tuple(self._with_details(row) for row in self.connection.execute(
            'SELECT * FROM task_review_event WHERE workspace_id=? AND task_id=? ORDER BY sequence',
            (workspace_id, task_id)))

    def append(self, *, workspace_id, task_id, snapshot, action, review_status, original_values,
               final_values, human_fields, field_sources, task_event_ids, reviewer, reason, when,
               legacy_candidate_id=None, legacy_fingerprint=None):
        columns = ('id', 'workspace_id', 'task_id', 'candidate_id', 'action', 'review_status',
                   'candidate_fingerprint', *[name + '_json' for name in _JSON_FIELDS],
                   'reviewer', 'reason', 'reviewed_at')
        if snapshot is None and (action not in ('invalidate', 'needs_review') or not legacy_candidate_id or not legacy_fingerprint):
            raise ValueError('only explicit legacy invalidation/undo may have an unknown candidate snapshot')
        values = ('task_review_' + uuid4().hex, workspace_id, task_id,
                  snapshot.candidate.candidate_id if snapshot is not None else legacy_candidate_id,
                  action, review_status, snapshot.fingerprint if snapshot is not None else legacy_fingerprint,
                  *[canonical_json(value) for value in (snapshot.to_dict() if snapshot is not None else None, original_values, final_values,
                                                        human_fields, field_sources, task_event_ids)],
                  reviewer, reason, when.isoformat())
        cursor = self.connection.execute(
            'INSERT INTO task_review_event(' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in values) + ')',
            values)
        return self._decode(self.connection.execute('SELECT * FROM task_review_event WHERE sequence=?',
                                                    (cursor.lastrowid,)).fetchone())

    def _with_details(self, row):
        event = self._decode(row)
        if event is None:
            return None
        detail = self.connection.execute("""SELECT * FROM task_review_binding_detail
            WHERE workspace_id=? AND review_event_id=?""", (event['workspace_id'], event['id'])).fetchone()
        event['binding_details'] = None
        if detail:
            event['binding_details'] = dict(detail)
            for key in ('source_proof', 'field_choices', 'legacy_basis', 'attestation'):
                event['binding_details'][key] = json.loads(event['binding_details'].pop(key + '_json'))
        return event

    def rejected(self, workspace_id):
        return tuple(self._with_details(row) for row in self.connection.execute(
            "SELECT * FROM task_review_event WHERE workspace_id=? AND action='reject' ORDER BY sequence", (workspace_id,)))

    def append_binding_details(self, audit, *, old_fingerprint, new_fingerprint, source_proof,
                               field_choices, legacy_basis=None, attestation=None, operation='rebind', target_review_event_id=None):
        self.connection.execute("""INSERT INTO task_review_binding_detail(review_event_id,workspace_id,
            old_candidate_fingerprint,new_candidate_fingerprint,source_proof_json,field_choices_json,
            legacy_basis_json,attestation_json,operation,target_review_event_id) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (audit['id'], audit['workspace_id'], old_fingerprint, new_fingerprint,
             canonical_json(source_proof), canonical_json(field_choices), canonical_json(legacy_basis), canonical_json(attestation),
             operation, target_review_event_id))
