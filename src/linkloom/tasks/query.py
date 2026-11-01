"""Bounded product projections over the existing human task review services."""
from __future__ import annotations

import json

from linkloom.tasks.models import TaskStatus, required_text
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, ReviewConflictError
from linkloom.tasks.store import _ANY_OWNER


class TaskQueryError(ReviewConflictError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def pagination(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('limit must be an integer between 1 and 100')
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ValueError('offset must be a nonnegative SQLite integer')
    return limit, offset


def page(items, limit, offset, *, has_more=None):
    more = len(items) > limit if has_more is None else has_more
    return dict(items=list(items[:limit]), page=dict(limit=limit, offset=offset,
        next_offset=offset + limit if more else None, has_more=more))


class TaskQueries:
    def __init__(self, review):
        self.review = review
        self.store = review.store
        self.connection = review.storage.connection

    def require_workspace(self, workspace_id):
        workspace = required_text(workspace_id, 'workspace_id')
        with self.store._lock:
            if self.connection.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?', (workspace,)).fetchone() is None:
                raise TaskQueryError('WORKSPACE_NOT_FOUND', 'workspace is unavailable')
        return workspace

    def require_merge(self, workspace_id, task_id, merge_id):
        workspace = self.require_workspace(workspace_id)
        with self.store._lock:
            self.store._require_task(workspace, required_text(task_id, 'task_id'))
            row = self.connection.execute("""SELECT id FROM task_merge_operation
                WHERE workspace_id=? AND task_id=? AND id=? AND operation='merge'""",
                (workspace, task_id, required_text(merge_id, 'merge_id'))).fetchone()
            if row is None:
                raise TaskQueryError('RESOURCE_NOT_FOUND', 'merge is unavailable for this task and workspace')
        return merge_id

    def source_summary(self, workspace, evidence):
        sources = {}
        for reference in evidence:
            value = reference.to_dict() if hasattr(reference, 'to_dict') else reference
            key = (value['source_id'], value['source_version_id'], value.get('meeting_time'))
            if key not in sources:
                row = self.connection.execute('''SELECT source_name FROM ll_source_version
                    WHERE workspace_id=? AND source_id=? AND artifact_version_id=?''',
                    (workspace, key[0], key[1])).fetchone()
                sources[key] = dict(source_id=key[0], source_version_id=key[1],
                    source_name=row['source_name'] if row else None, meeting_time=key[2])
        return list(sources.values())

    def candidate_item(self, item):
        result = {key: value for key, value in item.items()
                  if key not in ('candidate', 'original_payload', 'context_token')}
        candidate = dict(item['candidate'])
        evidence = candidate.pop('evidence')
        result.update(candidate=candidate, candidate_id=candidate['candidate_id'],
            candidate_fingerprint=item['fingerprint'], evidence_count=len(evidence),
            source_summary=self.source_summary(candidate['workspace_id'], evidence),
            confidence=candidate.get('confidence'), confidence_reason=candidate.get('confidence_reason'))
        return result

    def list_candidates_page(self, workspace_id, *, state='pending', limit=20, offset=0):
        limit, offset = pagination(limit, offset)
        if state not in ('pending', 'processed', 'all'):
            raise ValueError('state must be pending, processed or all')
        workspace = self.require_workspace(workspace_id)
        with self.review.request_scope(), self.store._lock:
            # The source contract verifies complete immutable batches and conflict groups.
            # Enumerate once in this transaction; filtering and paging remain in the service.
            values = self.review.list_candidates(workspace, include_reviewed=state != 'pending')
            if state == 'processed':
                values = tuple(item for item in values if item['reviewed'])
            values = sorted(values, key=lambda item: (item['candidate']['candidate_id'], item['fingerprint'], not item['is_current']))
            selected = values[offset:offset + limit + 1]
            return page([self.candidate_item(item) for item in selected], limit, offset)

    def candidate_evidence_page(self, workspace_id, candidate_id, *, candidate_fingerprint, limit=20, offset=0):
        limit, offset = pagination(limit, offset)
        workspace = self.require_workspace(workspace_id)
        identifier = required_text(candidate_id, 'candidate_id')
        fingerprint = required_text(candidate_fingerprint, 'candidate_fingerprint')
        with self.review.request_scope(), self.store._transaction():
            row = self.connection.execute('''SELECT candidate_json FROM task_review_event
                WHERE workspace_id=? AND candidate_id=? AND candidate_fingerprint=?
                ORDER BY sequence DESC LIMIT 1''', (workspace, identifier, fingerprint)).fetchone()
            historical = None
            if row is not None:
                try:
                    raw = json.loads(row['candidate_json'])
                    historical = CandidateSnapshot.from_dict(raw) if raw is not None else None
                    if historical and (historical.candidate.workspace_id != workspace
                        or historical.candidate.candidate_id != identifier or historical.fingerprint != fingerprint):
                        raise ValueError('review snapshot does not match the requested candidate')
                except (ValueError, TypeError, KeyError) as error:
                    raise TaskQueryError('SOURCE_INVALID', 'saved candidate evidence failed verification') from error
            try:
                values = self.review._candidates(workspace)
            except CandidateUnavailableError:
                if historical is None:
                    raise
                values = ()
            current = next((item for item in values if item.candidate.candidate_id == identifier and item.fingerprint == fingerprint), None)
            selected = current or historical
            if selected is None:
                self.review._select(values, identifier, fingerprint)
                raise AssertionError('unreachable candidate selection')
            evidence = selected.candidate.evidence
            result = page(self.review.merges._evidence_view(workspace, evidence[offset:offset + limit + 1]), limit, offset)
            result.update(candidate_id=identifier, candidate_fingerprint=fingerprint, is_current=current is not None,
                          evidence_basis='current_source' if current is not None else 'review_snapshot')
            return result

    def summary(self, task, latest):
        binding = latest.get('binding_details') if latest else None
        machine = binding['source_proof'].get('machine_validation') if binding else None
        declarations = binding['attestation'] if binding else None
        evidence = latest['candidate']['candidate']['evidence'] if latest and latest['candidate'] else ()
        binding_sources = self.source_summary(task.workspace_id, evidence)
        snapshots = {latest['candidate_fingerprint']: latest['candidate']} if latest and latest['candidate'] else {}
        field_summary = {}
        combined = {}
        for field, origin in (latest['field_sources'].items() if latest else ()):
            fingerprint = origin.get('candidate_fingerprint')
            if fingerprint not in snapshots:
                row = self.connection.execute('''SELECT candidate_json FROM task_review_event
                    WHERE workspace_id=? AND task_id=? AND candidate_fingerprint=?
                    ORDER BY sequence DESC LIMIT 1''', (task.workspace_id, task.id, fingerprint)).fetchone()
                snapshots[fingerprint] = json.loads(row['candidate_json']) if row else None
            raw = snapshots[fingerprint]
            references = ()
            if raw is not None:
                try:
                    snapshot = CandidateSnapshot.from_dict(raw)
                    if snapshot.candidate.workspace_id != task.workspace_id or snapshot.fingerprint != fingerprint:
                        raise ValueError('field source snapshot does not match the task review')
                    references = snapshot.candidate.evidence
                except (ValueError, TypeError, KeyError) as error:
                    raise TaskQueryError('SOURCE_INVALID', 'saved field provenance failed verification') from error
            sources = self.source_summary(task.workspace_id, references)
            field_summary[field] = dict(origin=origin['origin'], evidence_role=origin['evidence_role'], sources=sources)
            for source in sources:
                combined[(source['source_id'], source['source_version_id'], source['meeting_time'])] = source
        result = dict(task=task.to_dict(), review_status=latest['review_status'] if latest else 'needs_review',
            review_revision=str(latest['sequence']) if latest else None,
            candidate_id=latest['candidate_id'] if latest else None,
            candidate_fingerprint=latest['candidate_fingerprint'] if latest else None,
            field_sources=latest['field_sources'] if latest else {},
            machine_validation=machine, human_declarations=declarations,
            binding_source_summary=binding_sources, field_source_summary=field_summary,
            source_summary=list(combined.values()) if field_summary else binding_sources)
        merge = self.connection.execute('''SELECT id,operation,human_declaration_json,source_proof_json
            FROM task_merge_operation WHERE workspace_id=? AND task_id=? ORDER BY sequence DESC LIMIT 1''',
            (task.workspace_id, task.id)).fetchone()
        if merge:
            merge_declarations = json.loads(merge['human_declaration_json'])
            result.update(merge_human_declarations=merge_declarations,
                merge_machine_validation={key: value for key, value in json.loads(merge['source_proof_json']).items()
                                          if key != 'task_event_tail_before'})
            if declarations is None:
                result['human_declarations'] = merge_declarations
        return result

    def task_summary(self, workspace_id, task_id):
        workspace = self.require_workspace(workspace_id)
        with self.review.request_scope(), self.store._transaction():
            task = self.store._require_task(workspace, required_text(task_id, 'task_id'))
            latest = self.review._refresh(self.review.storage.latest(workspace, task_id=task.id), self.store._when(None))
            return self.summary(task, latest)

    def list_tasks_page(self, workspace_id, *, owner=_ANY_OWNER, status=None, limit=20, offset=0):
        limit, offset = pagination(limit, offset)
        workspace = self.require_workspace(workspace_id)
        if status is not None:
            TaskStatus(status)
        with self.review.request_scope(), self.store._transaction():
            tasks = self.store.list_tasks(workspace, owner=owner, status=status, limit=limit + 1, offset=offset)
            when = self.store._when(None)
            items = [self.summary(task, self.review._refresh(self.review.storage.latest(workspace, task_id=task.id), when))
                     for task in tasks[:limit]]
            return page(items, limit, offset, has_more=len(tasks) > limit)

    @staticmethod
    def bounded_event(event):
        result = {key: value for key, value in event.items() if key not in ('evidence', 'meeting_contexts')}
        before, after = event['before_evidence'], event['after_evidence']
        sides = [[], []]
        for index in range(max(len(before), len(after))):
            for side, evidence in enumerate((before, after)):
                if index < len(evidence) and sum(map(len, sides)) < 5:
                    sides[side].append(evidence[index])
            if sum(map(len, sides)) == 5:
                break
        result.update(before_evidence=sides[0], after_evidence=sides[1],
            before_evidence_count=len(before), after_evidence_count=len(after),
            evidence_truncated=len(before) + len(after) > 5)
        return result

    def task_events_page(self, workspace_id, task_id, *, limit=20, offset=0):
        limit, offset = pagination(limit, offset)
        workspace = self.require_workspace(workspace_id)
        with self.review.request_scope(), self.store._transaction():
            task = self.store._require_task(workspace, required_text(task_id, 'task_id'))
            events = self.store.get_events(workspace, task.id, limit=limit + 1, offset=offset)
            history = self.review.storage.history(workspace, task.id)
            audits = {identifier: audit for audit in history for identifier in audit['task_event_ids']}
            projected = []
            for event in events[:limit]:
                item = event.to_dict()
                audit = audits.get(event.id)
                field = event.event_type.value.removesuffix('_changed')
                human = bool(audit and (audit['human_fields'] if field == 'created'
                    else audit['field_sources'].get(field, {}).get('origin') == 'human'))
                item.update(evidence_role='context' if human else 'source' if event.evidence else 'manual',
                            field_sources=audit['field_sources'] if audit else {},
                            review_revision=str(audit['sequence']) if audit else None)
                projected.append(item)
            result = dict(events=projected, review_history=history)
            self.review.merges.enrich_view(task, result)
            return page([self.bounded_event(item) for item in result['events']], limit, offset,
                        has_more=len(events) > limit)