"""Opt-in, source-bound meeting task inbox. Review never grants task authority."""
from dataclasses import asdict
import json

from linkloom.semantic_ingestion.artifacts import EvidenceSpan
from linkloom.semantic_ingestion.meeting_tasks_v2 import digest
from linkloom.semantic_ingestion.task_extraction import build_meeting_extractor


class MeetingTaskReview:
    def __init__(self, app, adapter, config, speakers, request_guard):
        self.app, self.config = app, config
        # Copy trusted Host metadata; source text and HTTP payloads cannot edit it.
        self.speakers = json.loads(json.dumps(speakers))
        self.extractor = build_meeting_extractor(adapter, model_id=adapter.model_id,
            config=config, request_guard=request_guard)
        self.db = app.repository._connection
        self.db.execute("""CREATE TABLE IF NOT EXISTS ll_meeting_task_review (
            workspace_id TEXT NOT NULL, candidate_id TEXT NOT NULL, source_id TEXT NOT NULL,
            source_version TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, reviewer TEXT, note TEXT,
            PRIMARY KEY(workspace_id, candidate_id))""")
        self.db.commit()

    def ingest(self, artifact):
        # Recheck before/after EVERY model request, including queued imports and
        # later windows. Deleting/updating a source revokes its old text promptly.
        def current_source():
            with self.app._store_lock:
                source = self.app.repository.get_source(artifact.workspace_id, artifact.artifact_id)
                return self.app._source_artifact(source) if source else None
        self.extractor.current_source = current_source
        batch = self.extractor.extract(artifact, self.speakers)
        with self.app._store_lock:
            source = self.app.repository.get_source(artifact.workspace_id, artifact.artifact_id)
            if not source or source['source_version'] != artifact.artifact_version_id:
                raise ValueError("SOURCE_VERSION_CHANGED")
            for candidate in batch.candidates:
                if not candidate.verify(artifact):
                    raise ValueError("SOURCE_EVIDENCE_INVALID")
                # Model prose may change on retry; it must not duplicate review
                # work or reopen an already confirmed/rejected source-bound item.
                stable_id = 'meeting_review_' + digest([self.config.task_extraction_mode, artifact.artifact_version_id,
                    candidate.owner_ids, ' '.join(candidate.deliverable_key.split()).casefold(),
                    sorted((e.span.char_start,e.span.char_end) for e in candidate.evidence if 'work' in e.supports)])[:32]
                payload = {**asdict(candidate), 'extraction_candidate_id': candidate.candidate_id, 'candidate_id': stable_id}
                previous = self.db.execute(
                    "SELECT payload,status FROM ll_meeting_task_review WHERE workspace_id=? AND candidate_id=?",
                    (artifact.workspace_id, stable_id)).fetchone()
                if previous and previous['status'] == 'PENDING_REVIEW':
                    prior = json.loads(previous['payload'])
                    if prior.get('confidence') == 'LOW' and payload.get('confidence') == 'HIGH':
                        payload['confidence'] = 'LOW'
                        payload['confidence_reason'] = prior['confidence_reason']
                # Refresh validated pending fields on retry, but never promote an
                # earlier LOW or overwrite a human's completed review.
                self.db.execute("""INSERT INTO ll_meeting_task_review VALUES (?,?,?,?,?,'PENDING_REVIEW',NULL,NULL)
                    ON CONFLICT(workspace_id,candidate_id) DO UPDATE SET payload=excluded.payload
                    WHERE ll_meeting_task_review.status='PENDING_REVIEW'""",
                    (artifact.workspace_id, stable_id, artifact.artifact_id,
                     artifact.artifact_version_id, json.dumps(payload, ensure_ascii=False)))
            self.db.commit()
            pending = self.db.execute("SELECT COUNT(*) FROM ll_meeting_task_review WHERE workspace_id=? AND source_id=? AND source_version=? AND status='PENDING_REVIEW'",
                (artifact.workspace_id, artifact.artifact_id, artifact.artifact_version_id)).fetchone()[0]
            self.app.repository.set_ingestion_result(artifact.workspace_id, artifact.artifact_id,
                artifact.artifact_version_id, status="COMPLETE" if batch.coverage_complete else "PARTIAL",
                candidate_count=self.db.execute('SELECT COUNT(*) FROM ll_meeting_task_review WHERE workspace_id=? AND source_id=? AND source_version=?',
                    (artifact.workspace_id, artifact.artifact_id, artifact.artifact_version_id)).fetchone()[0], review_required_count=pending,
                error=None if batch.coverage_complete else "Some meeting candidates failed validation; review source coverage.")
        return batch

    def _current(self, row):
        source = self.app.repository.get_source(row['workspace_id'], row['source_id'])
        if not source or source['source_version'] != row['source_version']:
            return None
        artifact = self.app._source_artifact(source)
        candidate = json.loads(row['payload'])
        if not candidate.get('evidence') or not all(EvidenceSpan(**e['span']).verify(artifact) for e in candidate['evidence']):
            return None
        return {**candidate, 'source_id': row['source_id'], 'source_name': source['name'],
                'source_version': row['source_version'], 'status': row['status'],
                'confidence': candidate.get('confidence', 'HIGH'),
                'confidence_reason': candidate.get('confidence_reason', candidate['reason']),
                'authority': 'UNCONFIRMED', 'review_required': True}

    def pending(self, workspace_id):
        rows = self.db.execute("SELECT * FROM ll_meeting_task_review WHERE workspace_id=? AND status='PENDING_REVIEW' ORDER BY candidate_id", (workspace_id,)).fetchall()
        return [item for row in rows if (item := self._current(row)) is not None]

    def review(self, workspace_id, candidate_id, payload):
        from linkloom.ui.product import ProductApplicationError
        row = self.db.execute("SELECT * FROM ll_meeting_task_review WHERE workspace_id=? AND candidate_id=?",
                              (workspace_id, candidate_id)).fetchone()
        if row is None or not self._current(row):
            raise ProductApplicationError('MEETING_TASK_STALE', 'Refresh the current source before reviewing this candidate.', status=409)
        action, reviewer, note = payload.get('action'), payload.get('reviewer'), payload.get('note')
        if (action not in {'CONFIRM', 'REJECT'} or payload.get('source_version') != row['source_version']
            or not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 160
            or not isinstance(note, str) or not note.strip() or len(note) > 4096):
            raise ProductApplicationError('INVALID_MEETING_TASK_REVIEW', 'Provide an action, current source version, reviewer and note.')
        if row['status'] != 'PENDING_REVIEW':
            raise ProductApplicationError('MEETING_TASK_ALREADY_REVIEWED', 'This candidate was already reviewed.', status=409)
        status = 'CONFIRMED_BY_HUMAN' if action == 'CONFIRM' else 'REJECTED_BY_HUMAN'
        self.db.execute("UPDATE ll_meeting_task_review SET status=?,reviewer=?,note=? WHERE workspace_id=? AND candidate_id=?",
            (status, reviewer.strip(), note.strip(), workspace_id, candidate_id))
        self.db.commit()
        count = self.db.execute("SELECT COUNT(*) FROM ll_meeting_task_review WHERE workspace_id=? AND source_id=? AND source_version=? AND status='PENDING_REVIEW'",
            (workspace_id,row['source_id'],row['source_version'])).fetchone()[0]
        self.app.repository.set_review_required_count(workspace_id,row['source_id'],row['source_version'],count)
        return {'status': status, 'authority': 'UNCONFIRMED'}
