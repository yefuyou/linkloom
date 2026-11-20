"""Opt-in, source-bound meeting task inbox backed by formal TaskStore / TaskEvent."""
from dataclasses import asdict
from datetime import date
import hashlib
import json

from linkloom.semantic_ingestion.artifacts import EvidenceSpan
from linkloom.semantic_ingestion.meeting_tasks_v2 import digest
from linkloom.semantic_ingestion.task_extraction import build_meeting_extractor
from linkloom.tasks.models import EvidenceReference, TaskCandidateInput
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskStore


def _line_bounds(text: str, char_start: int, char_end: int) -> tuple[int, int]:
    line_start = text.count("\n", 0, char_start) + 1
    tail = max(char_start, char_end - 1)
    line_end = text.count("\n", 0, tail) + 1
    return line_start, max(line_start, line_end)


def _iso_deadline(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if date.fromisoformat(text).isoformat() == text:
            return text
    except ValueError:
        return None
    return None


class MeetingTaskReview:
    def __init__(self, app, adapter, config, speakers, request_guard, *, task_store: TaskStore | None = None):
        self.app, self.config = app, config
        # Copy trusted Host metadata; source text and HTTP payloads cannot edit it.
        self.speakers = json.loads(json.dumps(speakers))
        self.extractor = build_meeting_extractor(adapter, model_id=adapter.model_id,
            config=config, request_guard=request_guard)
        self.db = app.repository._connection
        db_path = getattr(app.repository, "database_path", ":memory:")
        self.task_store = task_store or TaskStore(db_path)
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
        return [item for row in rows
                if self._recover_from_receipt(row) is None and (item := self._current(row)) is not None]

    def _to_candidate_input(self, row, candidate: dict, artifact) -> TaskCandidateInput:
        source_sha256 = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
        evidence_refs: list[EvidenceReference] = []
        for idx, item in enumerate(candidate.get("evidence", [])):
            span = EvidenceSpan(**item["span"])
            quote = artifact.content[span.char_start : span.char_end]
            line_start, line_end = _line_bounds(artifact.content, span.char_start, span.char_end)
            evidence_refs.append(
                EvidenceReference(
                    workspace_id=row["workspace_id"],
                    source_id=row["source_id"],
                    source_version_id=row["source_version"],
                    quote=quote,
                    quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                    message_id=span.message_id or f"span-{idx}",
                    line_start=line_start,
                    line_end=line_end,
                    char_start=span.char_start,
                    char_end=span.char_end,
                    source_sha256=source_sha256,
                    meeting_time=None,
                )
            )
        owners = candidate.get("owner_ids") or []
        owner = str(owners[0]).strip() if owners and str(owners[0]).strip() else None
        raw_deadline = candidate.get("deadline_text") or candidate.get("deadline")
        if isinstance(raw_deadline, dict):
            raw_deadline = raw_deadline.get("resolved_at") or raw_deadline.get("raw_text")
        deadline_text = str(raw_deadline).strip() if isinstance(raw_deadline, str) and raw_deadline.strip() else None
        deadline = _iso_deadline(deadline_text)
        title = str(candidate.get("task") or candidate.get("deliverable_key") or row["candidate_id"]).strip()
        return TaskCandidateInput(
            candidate_id=row["candidate_id"],
            workspace_id=row["workspace_id"],
            title=title,
            evidence=tuple(evidence_refs),
            owner=owner,
            deadline=deadline,
            deadline_text=deadline_text,
            source_fingerprint=source_sha256,
        )

    def review(self, workspace_id, candidate_id, payload):
        from linkloom.ui.product import ProductApplicationError
        row = self.db.execute("SELECT * FROM ll_meeting_task_review WHERE workspace_id=? AND candidate_id=?",
                              (workspace_id, candidate_id)).fetchone()
        current = self._current(row) if row is not None else None
        if row is None or not current:
            raise ProductApplicationError('MEETING_TASK_STALE', 'Refresh the current source before reviewing this candidate.', status=409)
        action, reviewer, note = payload.get('action'), payload.get('reviewer'), payload.get('note')
        if (action not in {'CONFIRM', 'CREATE', 'MERGE', 'REJECT'} or payload.get('source_version') != row['source_version']
            or not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 160
            or not isinstance(note, str) or not note.strip() or len(note) > 4096):
            raise ProductApplicationError('INVALID_MEETING_TASK_REVIEW', 'Provide an action, current source version, reviewer and note.')
        recovered = self._recover_from_receipt(row)
        if recovered is not None:
            return recovered
        if row['status'] != 'PENDING_REVIEW':
            raise ProductApplicationError('MEETING_TASK_ALREADY_REVIEWED', 'This candidate was already reviewed.', status=409)

        source = self.app.repository.get_source(row['workspace_id'], row['source_id'])
        artifact = self.app._source_artifact(source)
        candidate_input = self._to_candidate_input(row, current, artifact)
        target_task_id = payload.get('target_task_id')
        apply_fields = tuple(payload.get('apply_fields') or ('owner', 'deadline', 'title'))

        try:
            if action == 'REJECT':
                self.task_store.reject_candidate(
                    workspace_id,
                    candidate_input,
                    reviewer=reviewer.strip(),
                    reason=note.strip(),
                )
                status = 'REJECTED_BY_HUMAN'
                result_payload = {'status': status, 'authority': 'REJECTED'}
            elif action == 'MERGE' or (action == 'CONFIRM' and target_task_id):
                if not isinstance(target_task_id, str) or not target_task_id.strip():
                    raise ProductApplicationError('INVALID_MEETING_TASK_REVIEW', 'Provide target_task_id when merging into an existing task.')
                task = self.task_store.merge_candidate(
                    workspace_id,
                    target_task_id.strip(),
                    candidate_input,
                    apply_fields=apply_fields,
                    reviewer=reviewer.strip(),
                    reason=note.strip(),
                )
                status = 'CONFIRMED_BY_HUMAN'
                result_payload = {'status': status, 'authority': 'CONFIRMED', 'task_id': task.id, 'task': task.to_dict()}
            else:
                task = self.task_store.create_from_candidate(
                    workspace_id,
                    candidate_input,
                    reviewer=reviewer.strip(),
                    reason=note.strip(),
                )
                status = 'CONFIRMED_BY_HUMAN'
                result_payload = {'status': status, 'authority': 'CONFIRMED', 'task_id': task.id, 'task': task.to_dict()}
        except TaskNotFoundError as exc:
            raise ProductApplicationError('TASK_NOT_FOUND', str(exc), status=404) from exc
        except CandidateAlreadyReviewedError as exc:
            raise ProductApplicationError('MEETING_TASK_ALREADY_REVIEWED', str(exc), status=409) from exc
        except ValueError as exc:
            raise ProductApplicationError('INVALID_MEETING_TASK_REVIEW', str(exc), status=400) from exc

        self._mark_reviewed(row, status, reviewer.strip(), note.strip())
        return result_payload

    def _mark_reviewed(self, row, status, reviewer, note):
        self.db.execute("UPDATE ll_meeting_task_review SET status=?,reviewer=?,note=? WHERE workspace_id=? AND candidate_id=?",
            (status, reviewer, note, row['workspace_id'], row['candidate_id']))
        self.db.commit()
        count = self.db.execute("SELECT COUNT(*) FROM ll_meeting_task_review WHERE workspace_id=? AND source_id=? AND source_version=? AND status='PENDING_REVIEW'",
            (row['workspace_id'],row['source_id'],row['source_version'])).fetchone()[0]
        self.app.repository.set_review_required_count(row['workspace_id'],row['source_id'],row['source_version'],count)

    def _recover_from_receipt(self, row):
        """Finish a review whose TaskStore receipt committed but whose queue update did not.

        TaskStore and this queue use separate SQLite connections. The receipt is the
        authoritative record, so the queue row adopts it instead of reviewing again.
        """
        if row['status'] != 'PENDING_REVIEW':
            return None
        link = self.task_store.get_link(row['workspace_id'], row['candidate_id'])
        if link is None:
            return None
        if link.task_id is None:
            status, result = 'REJECTED_BY_HUMAN', {'status': 'REJECTED_BY_HUMAN', 'authority': 'REJECTED'}
        else:
            task = self.task_store.get_task(row['workspace_id'], link.task_id)
            status = 'CONFIRMED_BY_HUMAN'
            result = {'status': status, 'authority': 'CONFIRMED', 'task_id': link.task_id,
                      'task': task.to_dict() if task else None}
        self._mark_reviewed(row, status, link.reviewer, link.reason)
        return result

    def list_formal_tasks(self, workspace_id: str):
        return [task.to_dict() for task in self.task_store.list_tasks(workspace_id)]

    def get_formal_task_events(self, workspace_id: str, task_id: str):
        return [event.to_dict() for event in self.task_store.get_events(workspace_id, task_id)]
