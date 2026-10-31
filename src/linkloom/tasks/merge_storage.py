"""Additive append-only merge operations and effective candidate assignments."""
from __future__ import annotations

import json
from uuid import uuid4

from linkloom.tasks.models import canonical_json
from linkloom.tasks.store import TaskSchemaError


_COLUMNS = {
    'task_merge_schema_metadata': {'id', 'version'},
    'task_candidate_assignment': {'sequence', 'id', 'workspace_id', 'candidate_id', 'task_id', 'operation',
        'review_action', 'candidate_fingerprint', 'reviewer', 'reason', 'reviewed_at', 'prior_id'},
    'task_merge_operation': {'sequence', 'id', 'workspace_id', 'task_id', 'operation', 'target_merge_id',
        'parent_merge_id', 'review_event_id', 'candidate_id', 'candidate_fingerprint', 'assignment_id',
        'before_task_json', 'after_task_json', 'before_review_json', 'after_review_json', 'apply_fields_json',
        'source_proof_json', 'human_declaration_json', 'task_event_ids_json', 'created_at'},
}
_DDL = (
    'CREATE TABLE task_merge_schema_metadata(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)',
    'CREATE UNIQUE INDEX task_link_workspace_identity ON task_observation_link(observation_id,workspace_id)',
    """CREATE TABLE task_candidate_assignment (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL, candidate_id TEXT NOT NULL, task_id TEXT,
        operation TEXT NOT NULL CHECK(operation IN ('claim','release')),
        review_action TEXT NOT NULL CHECK(review_action IN ('create','merge','reject')),
        candidate_fingerprint TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
        reviewed_at TEXT NOT NULL, prior_id TEXT, UNIQUE(id,workspace_id),
        FOREIGN KEY(candidate_id,workspace_id) REFERENCES task_observation_link(observation_id,workspace_id),
        FOREIGN KEY(task_id,workspace_id) REFERENCES task(id,workspace_id),
        FOREIGN KEY(prior_id,workspace_id) REFERENCES task_candidate_assignment(id,workspace_id),
        CHECK((review_action='reject' AND task_id IS NULL) OR (review_action<>'reject' AND task_id IS NOT NULL)),
        CHECK(operation<>'release' OR review_action='merge'))""",
    """CREATE TABLE task_merge_operation (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        workspace_id TEXT NOT NULL, task_id TEXT NOT NULL,
        operation TEXT NOT NULL CHECK(operation IN ('merge','undo_merge')),
        target_merge_id TEXT, parent_merge_id TEXT, review_event_id TEXT NOT NULL UNIQUE,
        candidate_id TEXT NOT NULL, candidate_fingerprint TEXT NOT NULL, assignment_id TEXT NOT NULL,
        before_task_json TEXT NOT NULL, after_task_json TEXT NOT NULL,
        before_review_json TEXT NOT NULL, after_review_json TEXT NOT NULL, apply_fields_json TEXT NOT NULL,
        source_proof_json TEXT NOT NULL, human_declaration_json TEXT NOT NULL, task_event_ids_json TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(id,workspace_id),
        FOREIGN KEY(task_id,workspace_id) REFERENCES task(id,workspace_id),
        FOREIGN KEY(review_event_id,workspace_id) REFERENCES task_review_event(id,workspace_id),
        FOREIGN KEY(assignment_id,workspace_id) REFERENCES task_candidate_assignment(id,workspace_id),
        FOREIGN KEY(target_merge_id,workspace_id) REFERENCES task_merge_operation(id,workspace_id),
        FOREIGN KEY(parent_merge_id,workspace_id) REFERENCES task_merge_operation(id,workspace_id),
        CHECK((operation='merge' AND target_merge_id IS NULL) OR (operation='undo_merge' AND target_merge_id IS NOT NULL)))""",
    'CREATE INDEX task_assignment_candidate ON task_candidate_assignment(candidate_id,sequence)',
    'CREATE INDEX task_merge_history ON task_merge_operation(workspace_id,task_id,sequence)',
    'INSERT INTO task_merge_schema_metadata(id,version) VALUES(1,1)',
)
_TRIGGERS = {
    f'{table}_append_only_{operation.lower()}': f"""CREATE TRIGGER {table}_append_only_{operation.lower()}
        BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'task merge history is append-only'); END"""
    for table in ('task_candidate_assignment','task_merge_operation') for operation in ('UPDATE','DELETE')
}
_JSON_FIELDS = ('before_task','after_task','before_review','after_review','apply_fields',
                'source_proof','human_declaration','task_event_ids')


def assignment_tip(connection, candidate_id):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_candidate_assignment'").fetchone()
    if not exists:
        return None
    row = connection.execute('SELECT * FROM task_candidate_assignment WHERE candidate_id=? ORDER BY sequence DESC LIMIT 1',
                             (candidate_id,)).fetchone()
    return dict(row) if row is not None else None


def append_assignment(connection, link, operation='claim'):
    prior = assignment_tip(connection, link.observation_id)
    identifier = 'task_assignment_' + uuid4().hex
    connection.execute("""INSERT INTO task_candidate_assignment(id,workspace_id,candidate_id,task_id,operation,
        review_action,candidate_fingerprint,reviewer,reason,reviewed_at,prior_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (identifier, link.workspace_id, link.observation_id, link.task_id, operation, link.review_action.value,
         link.candidate_fingerprint, link.reviewer, link.reason, link.reviewed_at.isoformat(), prior['id'] if prior else None))
    return assignment_tip(connection, link.observation_id)


def decode_operation(row):
    if row is None: return None
    value = dict(row)
    for key in _JSON_FIELDS:
        value[key] = json.loads(value.pop(key + '_json'))
    return value


def review_merge_detail(connection, workspace_id, review_event_id):
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_merge_operation'").fetchone() is None:
        return None
    return decode_operation(connection.execute('SELECT * FROM task_merge_operation WHERE workspace_id=? AND review_event_id=?',
                                               (workspace_id,review_event_id)).fetchone())


class MergeStorage:
    def __init__(self, store):
        self.store = store
        with store._transaction():
            connection = store._connection
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables.intersection(_COLUMNS):
                for statement in (*_DDL,*_TRIGGERS.values()): connection.execute(statement)
            else:
                if not set(_COLUMNS).issubset(tables): raise TaskSchemaError('incomplete task merge schema')
                for table, expected in _COLUMNS.items():
                    if {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')} != expected:
                        raise TaskSchemaError('unsupported task merge table shape')
                if [tuple(row) for row in connection.execute('SELECT id,version FROM task_merge_schema_metadata')] != [(1,1)]:
                    raise TaskSchemaError('unsupported task merge schema version')
                normalize = lambda value: ' '.join(value.split()).rstrip(';')
                guards = dict(connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
                if any(name not in guards or normalize(guards[name]) != normalize(sql) for name,sql in _TRIGGERS.items()):
                    raise TaskSchemaError('task merge append-only guards missing or unsupported')
                for sql in (_DDL[1],_DDL[4],_DDL[5]):
                    name = sql.split(' ON ')[0].split()[-1]
                    found = connection.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",(name,)).fetchone()
                    if found is None or normalize(found[0]) != normalize(sql):
                        raise TaskSchemaError('task merge indexes missing or unsupported')

    @property
    def connection(self): return self.store._connection

    def history(self, workspace, task_id):
        return tuple(decode_operation(row) for row in self.connection.execute(
            'SELECT * FROM task_merge_operation WHERE workspace_id=? AND task_id=? ORDER BY sequence', (workspace,task_id)))

    def append(self, *, workspace_id, task_id, operation, target_merge_id, parent_merge_id, audit, snapshot,
               assignment_id, before_task, after_task, before_review, apply_fields, source_proof,
               human_declaration, event_ids, when):
        identifier = 'task_merge_' + uuid4().hex
        values = (identifier,workspace_id,task_id,operation,target_merge_id,parent_merge_id,audit['id'],
            snapshot.candidate.candidate_id,snapshot.fingerprint,assignment_id,
            *[canonical_json(value) for value in (before_task,after_task,before_review,audit,apply_fields,
                                                source_proof,human_declaration,event_ids)],when.isoformat())
        self.connection.execute("""INSERT INTO task_merge_operation(id,workspace_id,task_id,operation,target_merge_id,
            parent_merge_id,review_event_id,candidate_id,candidate_fingerprint,assignment_id,before_task_json,
            after_task_json,before_review_json,after_review_json,apply_fields_json,source_proof_json,
            human_declaration_json,task_event_ids_json,created_at) VALUES(""" + ','.join('?' for _ in values) + ')', values)
        return decode_operation(self.connection.execute('SELECT * FROM task_merge_operation WHERE id=?',(identifier,)).fetchone())
