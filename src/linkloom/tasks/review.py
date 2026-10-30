"""Human task review, independent of extractors, HTTP and decision materialization."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from linkloom.tasks.models import (ObservationLink, ReviewAction, Task, TaskEventType, TaskStatus,
                                  deadline_value, optional_text, required_text)
from linkloom.tasks.review_models import (CandidateSnapshot, CandidateSource, CandidateUnavailableError,
                                         ReviewConflictError, identity, task_fields)
from linkloom.tasks.review_storage import ReviewStorage
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskStore


def _patch(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {'title', 'owner', 'deadline', 'deadline_text'}:
        raise ValueError('patch may only contain title, owner, deadline and deadline_text')
    if ('deadline' in value) != ('deadline_text' in value):
        raise ValueError('deadline and deadline_text must be supplied together')
    result = {}
    for field, item in value.items():
        if field == 'title':
            result[field] = required_text(item, field)
        elif field == 'deadline':
            result[field] = deadline_value(item)
        else:
            result[field] = optional_text(item, field)
    return result, [field for field in ('title', 'owner', 'deadline') if field in result]


class TaskReviewService:
    """Every read/verification/write uses TaskStore's one BEGIN IMMEDIATE scope.

    Querying may append one needs_review audit when an authorization changes.
    Task business status and historical snapshots are never rolled back. There
    is no task matching, merge or cross-candidate rebinding API in this service.
    """

    def __init__(self, store: TaskStore, source: CandidateSource):
        self.store, self.source = store, source
        self.storage = ReviewStorage(store)

    def _candidates(self, workspace):
        values = self.source.list_candidates(self.storage.connection, workspace)
        if any(not isinstance(value, CandidateSnapshot) or value.candidate.workspace_id != workspace
               for value in values):
            raise CandidateUnavailableError('source returned another workspace or an invalid input')
        return values

    @staticmethod
    def _select(values, candidate_id, fingerprint=None):
        matches = [value for value in values if value.candidate.candidate_id == candidate_id
                   and (fingerprint is None or value.fingerprint == fingerprint)]
        if len(matches) != 1:
            raise ReviewConflictError('candidate changed, is unavailable, or needs an exact variant fingerprint')
        return matches[0]

    def _current(self, workspace, candidate_id, fingerprint):
        return self._select(self._candidates(workspace), candidate_id, required_text(fingerprint, 'expected_candidate_fingerprint'))

    def _refresh(self, latest, now):
        if latest is None or latest['review_status'] == 'needs_review':
            return latest
        snapshot = CandidateSnapshot.from_dict(latest['candidate'])
        try:
            self._current(latest['workspace_id'], latest['candidate_id'], latest['candidate_fingerprint'])
            return latest
        except (ReviewConflictError, CandidateUnavailableError):
            # This transition latches: a later A -> B -> A never restores approval.
            when = max(now, datetime.fromisoformat(latest['reviewed_at']))
            return self.storage.append(workspace_id=latest['workspace_id'], task_id=latest['task_id'],
                snapshot=snapshot, action='needs_review', review_status='needs_review',
                original_values=latest['final_values'], final_values=latest['final_values'], human_fields=[],
                field_sources=latest['field_sources'], task_event_ids=[], reviewer='system:source-validation',
                reason='Candidate, conflict context or source authorization changed; explicit review required.', when=when)

    @staticmethod
    def _origins(snapshot, fields, reviewer, reason, when, previous=None):
        result = dict(previous or {})
        for field in ('title', 'owner', 'deadline'):
            if previous is not None and field not in fields:
                continue
            result[field] = dict(origin='human' if field in fields else 'source',
                candidate_fingerprint=snapshot.fingerprint, reviewer=reviewer, reason=reason,
                reviewed_at=when.isoformat(), evidence_role='context' if field in fields else 'source')
        return result

    def _link(self, snapshot, task_id, action, reviewer, reason, when):
        candidate = snapshot.candidate
        receipt = ObservationLink(candidate.candidate_id, candidate.workspace_id, task_id, action,
                                  reviewer, reason, when, snapshot.fingerprint)
        self.storage.connection.execute('''INSERT INTO task_observation_link(observation_id,workspace_id,task_id,
            review_action,reviewer,reason,reviewed_at,candidate_fingerprint) VALUES(?,?,?,?,?,?,?,?)''',
            (receipt.observation_id, receipt.workspace_id, receipt.task_id, receipt.review_action.value,
             receipt.reviewer, receipt.reason, receipt.reviewed_at.isoformat(), receipt.candidate_fingerprint))

    def _change_fields(self, task, patch, snapshot, reviewer, reason, when):
        if when < task.updated_at:
            raise ValueError('review timestamp precedes the current task update')
        final = replace(task, **patch)
        before, after = task_fields(task), task_fields(final)
        changed = [field for field in ('owner', 'deadline', 'title') if before[field] != after[field]]
        events = []
        if changed:
            final = replace(final, updated_at=when)
            self.store._write_task(final)
            for field in changed:
                event = self.store._append_event(final, TaskEventType(field + '_changed'), before[field], after[field],
                    evidence=snapshot.candidate.evidence, meeting_time=None, actor=reviewer, reason=reason, now=when)
                events.append(event.id)
        return final, events

    def _view(self, task, latest):
        history = self.storage.history(task.workspace_id, task.id)
        event_audits = {identifier: audit for audit in history for identifier in audit['task_event_ids']}
        events = []
        for event in self.store.get_events(task.workspace_id, task.id):
            item = event.to_dict()
            audit = event_audits.get(event.id)
            if audit:
                item.update(field_sources=audit['field_sources'],
                            evidence_role='context' if audit['human_fields'] else 'source',
                            review_revision=str(audit['sequence']))
            else:
                item.update(evidence_role='manual' if not event.evidence else 'source', field_sources={})
            events.append(item)
        return dict(task=task.to_dict(), review_status=latest['review_status'] if latest else 'needs_review',
            review_revision=str(latest['sequence']) if latest else None,
            candidate_fingerprint=latest['candidate_fingerprint'] if latest else None,
            field_sources=latest['field_sources'] if latest else {},
            original_candidate=latest['candidate'] if latest else None, review_history=history, events=events)

    def list_candidates(self, workspace_id, *, include_reviewed=False):
        workspace = required_text(workspace_id, 'workspace_id')
        if type(include_reviewed) is not bool:
            raise ValueError('include_reviewed must be boolean')
        with self.store._transaction():
            result = []
            for snapshot in self._candidates(workspace):
                link = self.store.get_link(workspace, snapshot.candidate.candidate_id)
                latest = None
                if link is not None and link.task_id is not None:
                    latest = self._refresh(self.storage.latest(workspace, task_id=link.task_id), self.store._when(None))
                needs_review = latest is not None and latest['review_status'] == 'needs_review'
                if link is None or include_reviewed or needs_review:
                    item = snapshot.to_dict()
                    item.update(reviewed=link is not None,
                                review_status=latest['review_status'] if latest else None,
                                review_revision=str(latest['sequence']) if latest else None,
                                task_id=link.task_id if link else None)
                    result.append(item)
            return tuple(result)

    def preview_candidate(self, workspace_id, candidate_id, *, expected_candidate_fingerprint=None):
        workspace, identifier = required_text(workspace_id, 'workspace_id'), required_text(candidate_id, 'candidate_id')
        with self.store._transaction():
            return self._select(self._candidates(workspace), identifier, expected_candidate_fingerprint).to_dict()

    def confirm(self, workspace_id, candidate_id, *, expected_candidate_fingerprint, reviewer, reason,
                patch=None, expected_review_revision=None, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        identifier = required_text(candidate_id, 'candidate_id')
        patch, human_fields = _patch(patch)
        when, conflict = self.store._when(now), None
        with self.store._transaction():
            snapshot = self._current(workspace, identifier, expected_candidate_fingerprint)
            link = self.store.get_link(workspace, identifier)
            if link is None:
                self.store._unprocessed(snapshot.candidate)
                candidate = snapshot.candidate
                values = dict(title=candidate.title, owner=candidate.owner, deadline=candidate.deadline,
                              deadline_text=candidate.deadline_text)
                values.update(patch)
                task = Task('task_' + uuid4().hex, workspace, **values, status=TaskStatus.OPEN,
                            source_decision_id=candidate.source_decision_id, created_at=when, updated_at=when)
                self.store._write_task(task, create=True)
                created = self.store._append_event(task, TaskEventType.CREATED, None, task.to_dict(),
                    evidence=candidate.evidence, meeting_time=candidate.meeting_time, actor=reviewer, reason=reason, now=when)
                self._link(snapshot, task.id, ReviewAction.CREATE, reviewer, reason, when)
                latest = self.storage.append(workspace_id=workspace, task_id=task.id, snapshot=snapshot,
                    action='confirm', review_status='approved', original_values=task_fields(candidate),
                    final_values=task_fields(task), human_fields=human_fields,
                    field_sources=self._origins(snapshot, human_fields, reviewer, reason, when),
                    task_event_ids=[created.id], reviewer=reviewer, reason=reason, when=when)
            else:
                if link.task_id is None:
                    raise CandidateAlreadyReviewedError('candidate was rejected')
                task = self.store._require_task(workspace, link.task_id)
                latest = self._refresh(self.storage.latest(workspace, task_id=task.id), when)
                if latest is None or latest['review_status'] != 'needs_review':
                    raise CandidateAlreadyReviewedError('candidate has already been confirmed')
                if expected_review_revision != str(latest['sequence']):
                    conflict = ReviewConflictError('explicit re-review requires the current review revision')
                else:
                    original = task_fields(task)
                    task, events = self._change_fields(task, patch, snapshot, reviewer, reason, when)
                    latest = self.storage.append(workspace_id=workspace, task_id=task.id, snapshot=snapshot,
                        action='reconfirm', review_status='approved', original_values=original,
                        final_values=task_fields(task), human_fields=human_fields,
                        field_sources=self._origins(snapshot, human_fields, reviewer, reason, when, latest['field_sources']),
                        task_event_ids=events, reviewer=reviewer, reason=reason, when=when)
            if conflict is None:
                result = self._view(task, latest)
        if conflict:
            raise conflict
        return result

    def reject(self, workspace_id, candidate_id, *, expected_candidate_fingerprint, reviewer, reason, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        identifier, when = required_text(candidate_id, 'candidate_id'), self.store._when(now)
        with self.store._transaction():
            snapshot = self._current(workspace, identifier, expected_candidate_fingerprint)
            self.store._unprocessed(snapshot.candidate)
            self._link(snapshot, None, ReviewAction.REJECT, reviewer, reason, when)
            return self.storage.append(workspace_id=workspace, task_id=None, snapshot=snapshot,
                action='reject', review_status='rejected', original_values=task_fields(snapshot.candidate),
                final_values=None, human_fields=[], field_sources={}, task_event_ids=[],
                reviewer=reviewer, reason=reason, when=when)

    def edit_fields(self, workspace_id, task_id, *, patch, expected_candidate_fingerprint,
                    expected_review_revision, reviewer, reason, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        task_id = required_text(task_id, 'task_id')
        expected_candidate_fingerprint = required_text(expected_candidate_fingerprint, 'expected_candidate_fingerprint')
        patch, human_fields = _patch(patch)
        when, conflict = self.store._when(now), None
        with self.store._transaction():
            task = self.store._require_task(workspace, task_id)
            latest = self._refresh(self.storage.latest(workspace, task_id=task_id), when)
            if (latest is None or latest['review_status'] != 'approved'
                or latest['candidate_fingerprint'] != expected_candidate_fingerprint
                or str(latest['sequence']) != expected_review_revision):
                conflict = ReviewConflictError('task needs current source approval and a fresh review revision')
            else:
                snapshot = CandidateSnapshot.from_dict(latest['candidate'])
                original = task_fields(task)
                task, events = self._change_fields(task, patch, snapshot, reviewer, reason, when)
                latest = self.storage.append(workspace_id=workspace, task_id=task_id, snapshot=snapshot,
                    action='edit', review_status='approved', original_values=original, final_values=task_fields(task),
                    human_fields=human_fields,
                    field_sources=self._origins(snapshot, human_fields, reviewer, reason, when, latest['field_sources']),
                    task_event_ids=events, reviewer=reviewer, reason=reason, when=when)
                result = self._view(task, latest)
        if conflict:
            raise conflict
        return result

    def get_task(self, workspace_id, task_id, *, now=None):
        workspace, task_id = required_text(workspace_id, 'workspace_id'), required_text(task_id, 'task_id')
        with self.store._transaction():
            task = self.store._require_task(workspace, task_id)
            latest = self._refresh(self.storage.latest(workspace, task_id=task_id), self.store._when(now))
            return self._view(task, latest)

    def list_tasks(self, workspace_id, **filters):
        workspace = required_text(workspace_id, 'workspace_id')
        with self.store._transaction():
            tasks = self.store.list_tasks(workspace, **filters)
            when = self.store._when(None)
            return tuple(self._view(task, self._refresh(self.storage.latest(workspace, task_id=task.id), when))
                         for task in tasks)