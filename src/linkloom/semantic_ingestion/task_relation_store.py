"""Explicit-path SQLite writer for immutable supporting relation records.

No source corpus, task master table, Decision Memory migration or default path.
Only this class creates a sidecar. Queries live in task_relation_query.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from linkloom.semantic_ingestion.task_relation_lifecycle import (
    RelationEvent, SCHEMA_VERSION, encoded, make_recipe, read_recipe,
    replacement_scope, validate_event_chain, workspace_of,
)
from linkloom.semantic_ingestion.task_relation_models import RelationAssessment, canonical_hash
from linkloom.semantic_ingestion.task_revision_comparison import RevisionComparison


def assessment_record(assessment, recipe):
    payload=assessment.to_dict()
    RelationAssessment.from_dict(payload)
    read_recipe(assessment,recipe)
    workspace=workspace_of(assessment)
    values=dict(workspace_id=workspace,assessment_id=assessment.assessment_id,predicate=assessment.predicate,
        subject_json=encoded(payload['subject']),object_json=encoded(payload['object']),
        payload_json=encoded(payload),payload_hash=canonical_hash(payload),
        recipe_json=encoded(recipe),recipe_hash=canonical_hash(recipe))
    return {**values,'record_hash':canonical_hash(values)}


def decode_assessment(row):
    payload=json.loads(row['payload_json'])
    assessment=RelationAssessment.from_dict(payload)
    recipe=json.loads(row['recipe_json'])
    if assessment_record(assessment,recipe)!=dict(row):
        raise ValueError('stored assessment integrity mismatch')
    return assessment,recipe


def decode_event(row):
    data=json.loads(row['event_json'])
    result=RelationEvent.from_dict(data)
    if (row['event_id']!=result.event_id or row['workspace_id']!=result.workspace_id
        or row['assessment_id']!=result.assessment_id or row['event_hash']!=canonical_hash(data)):
        raise ValueError('stored event integrity mismatch')
    return result


def decode_comparison(row):
    data=json.loads(row['payload_json'])
    result=RevisionComparison.from_dict(data)
    if (workspace_of(result)!=row['workspace_id'] or result.comparison_id!=row['comparison_id']
        or result.revision_id!=row['revision_id'] or result.same_task_id!=row['same_task_id']
        or canonical_hash(data)!=row['payload_hash']):
        raise ValueError('stored comparison integrity mismatch')
    return result


def semantic_payload(assessment):
    data=assessment.to_dict()
    data.pop('assessed_at')
    for observation in data['observations']:
        if observation is not None:
            observation.pop('recorded_at')
    return data


class TaskRelationStore:
    """Writer ownership is explicit. First-captured payloads are never replaced.

    Contract-valid records may still contain untrusted claims. Publication is
    exclusively the query verifier's job, not an ACTIVE event's implication.
    """
    def __init__(self, path):
        self.path=Path(path).resolve()
        if str(path)==':memory:' or not self.path.parent.is_dir():
            raise ValueError('explicit sidecar file with an existing parent required')
        self._connection=sqlite3.connect(self.path,isolation_level=None)
        self._connection.row_factory=sqlite3.Row
        self._connection.execute('PRAGMA foreign_keys=ON')
        existing={r[0] for r in self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if existing and 'task_relation_metadata' not in existing:
            self.close()
            raise ValueError('refuse to initialize a non-relation database')
        if existing:
            versions=[r[0] for r in self._connection.execute('SELECT schema_version FROM task_relation_metadata')]
            if versions!=[SCHEMA_VERSION]:
                self.close()
                raise ValueError('unsupported sidecar schema')
        else:
            self._initialize()

    def _initialize(self):
        self._connection.executescript('''
            BEGIN IMMEDIATE;
            CREATE TABLE task_relation_metadata(schema_version TEXT NOT NULL);
            CREATE TABLE task_relation_assessment(
                workspace_id TEXT NOT NULL,assessment_id TEXT NOT NULL,predicate TEXT NOT NULL,
                subject_json TEXT NOT NULL,object_json TEXT NOT NULL,payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,recipe_json TEXT NOT NULL,recipe_hash TEXT NOT NULL,record_hash TEXT NOT NULL,
                PRIMARY KEY(workspace_id,assessment_id));
            CREATE TABLE task_relation_event(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,
                workspace_id TEXT NOT NULL,assessment_id TEXT NOT NULL,event_json TEXT NOT NULL,event_hash TEXT NOT NULL,
                FOREIGN KEY(workspace_id,assessment_id) REFERENCES task_relation_assessment(workspace_id,assessment_id));
            CREATE TABLE task_revision_comparison(
                workspace_id TEXT NOT NULL,comparison_id TEXT NOT NULL,revision_id TEXT NOT NULL,same_task_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,PRIMARY KEY(workspace_id,comparison_id),
                FOREIGN KEY(workspace_id,revision_id) REFERENCES task_relation_assessment(workspace_id,assessment_id),
                FOREIGN KEY(workspace_id,same_task_id) REFERENCES task_relation_assessment(workspace_id,assessment_id));
            CREATE INDEX relation_predicate ON task_relation_assessment(workspace_id,predicate);
            CREATE INDEX relation_event_owner ON task_relation_event(workspace_id,assessment_id,sequence);
        ''')
        self._connection.execute('INSERT INTO task_relation_metadata VALUES (?)',(SCHEMA_VERSION,))
        for table in ('task_relation_assessment','task_relation_event','task_revision_comparison','task_relation_metadata'):
            for action in ('UPDATE','DELETE'):
                self._connection.execute(f'''CREATE TRIGGER immutable_{table}_{action.lower()}
                    BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'append-only relation sidecar'); END''')
        self._connection.commit()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def _row(self,workspace,assessment_id):
        return self._connection.execute('SELECT * FROM task_relation_assessment WHERE workspace_id=? AND assessment_id=?',
                                        (workspace,assessment_id)).fetchone()

    def _events(self,workspace,assessment_id):
        rows=self._connection.execute('SELECT * FROM task_relation_event WHERE workspace_id=? AND assessment_id=? ORDER BY sequence',
                                      (workspace,assessment_id)).fetchall()
        events=tuple(decode_event(r) for r in rows)
        validate_event_chain(events,workspace,assessment_id)
        return events

    def last_event(self,workspace_id,assessment_id):
        return self._events(workspace_id,assessment_id)[-1]

    def put_assessment(self,assessment,*,evidence=(),replay_premise_ids=()):
        if not isinstance(assessment,RelationAssessment):
            raise ValueError('expected RelationAssessment')
        recipe=make_recipe(assessment,evidence,replay_premise_ids)
        record=assessment_record(assessment,recipe)
        workspace=record['workspace_id']
        self._connection.execute('BEGIN IMMEDIATE')
        try:
            old=self._row(workspace,assessment.assessment_id)
            if old:
                previous,prior_recipe=decode_assessment(old)
                self._events(workspace,assessment.assessment_id)
                if semantic_payload(previous)!=semantic_payload(assessment) or prior_recipe!=recipe:
                    raise ValueError('immutable assessment or replay recipe collision')
                self._connection.commit()
                return False
            for premise_id in recipe['premise_ids']:
                if premise_id==assessment.assessment_id or self._row(workspace,premise_id) is None:
                    raise ValueError('missing or circular same-workspace premise')
            columns=','.join(record)
            self._connection.execute(f'INSERT INTO task_relation_assessment ({columns}) VALUES ({",".join("?" for _ in record)})',tuple(record.values()))
            initial=RelationEvent.create(workspace_id=workspace,assessment_id=assessment.assessment_id,event_type='ACTIVE',
                reason='IMMUTABLE_RECORD_CAPTURED',actor='system:relation-store',previous_event_id=None,
                recorded_at=assessment.assessed_at)
            self._insert_event(initial)
            self._connection.commit()
            return True
        except Exception:
            self._connection.rollback()
            raise

    def _insert_event(self,event):
        data=event.to_dict()
        self._connection.execute('INSERT INTO task_relation_event(event_id,workspace_id,assessment_id,event_json,event_hash) VALUES (?,?,?,?,?)',
            (event.event_id,event.workspace_id,event.assessment_id,encoded(data),canonical_hash(data)))

    def append_event(self,event):
        if not isinstance(event,RelationEvent):
            raise ValueError('expected lifecycle event')
        RelationEvent.from_dict(event.to_dict())
        self._connection.execute('BEGIN IMMEDIATE')
        try:
            old=self._connection.execute('SELECT * FROM task_relation_event WHERE event_id=?',(event.event_id,)).fetchone()
            if old:
                if decode_event(old)!=event:
                    raise ValueError('event ID collision')
                self._connection.commit()
                return False
            row=self._row(event.workspace_id,event.assessment_id)
            if row is None:
                raise ValueError('assessment not found in event workspace')
            owner,_=decode_assessment(row)
            events=self._events(event.workspace_id,event.assessment_id)
            validate_event_chain((*events,event),event.workspace_id,event.assessment_id)
            if event.replacement_id:
                replacement=self._row(event.workspace_id,event.replacement_id)
                if replacement is None:
                    raise ValueError('replacement assessment missing')
                new,_=decode_assessment(replacement)
                if new.predicate!=owner.predicate or self.last_event(event.workspace_id,event.replacement_id).event_type!='ACTIVE':
                    raise ValueError('replacement must be a live same-predicate assessment')
                if replacement_scope(owner)!=replacement_scope(new):
                    raise ValueError('replacement endpoint lineage mismatch')
            self._insert_event(event)
            self._connection.commit()
            return True
        except Exception:
            self._connection.rollback()
            raise

    def put_comparison(self,comparison):
        if not isinstance(comparison,RevisionComparison):
            raise ValueError('expected RevisionComparison')
        data=comparison.to_dict()
        RevisionComparison.from_dict(data)
        workspace=workspace_of(comparison)
        record=(workspace,comparison.comparison_id,comparison.revision_id,comparison.same_task_id,encoded(data),canonical_hash(data))
        self._connection.execute('BEGIN IMMEDIATE')
        try:
            old=self._connection.execute('SELECT * FROM task_revision_comparison WHERE workspace_id=? AND comparison_id=?',record[:2]).fetchone()
            if old:
                if decode_comparison(old).to_dict()!=data:
                    raise ValueError('immutable comparison collision')
                self._connection.commit()
                return False
            for identity in (comparison.revision_id,comparison.same_task_id):
                if self._row(workspace,identity) is None:
                    raise ValueError('comparison premise missing')
            self._connection.execute('INSERT INTO task_revision_comparison VALUES (?,?,?,?,?,?)',record)
            self._connection.commit()
            return True
        except Exception:
            self._connection.rollback()
            raise
