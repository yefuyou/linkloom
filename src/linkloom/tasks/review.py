"""Human task review, independent of extractors, HTTP and decision materialization."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from linkloom.tasks.models import (ObservationLink, ReviewAction, Task, TaskEventType, TaskStatus,
                                  deadline_value, optional_text, required_text)
from linkloom.tasks.review_models import (CandidateSnapshot, CandidateSource, CandidateUnavailableError,
                                         ReviewConflictError, digest, identity, task_fields)
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
    is no automatic task matching or general merge API. Rebinding requires
    explicit human choices and a verified newer revision of the same meeting.
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
        if latest is None or latest['review_status'] in ('needs_review', 'invalidated'):
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
                field = event.event_type.value.removesuffix('_changed')
                role = ('context' if audit['human_fields'] else 'source') if field == 'created' else (
                    'context' if audit['field_sources'].get(field, {}).get('origin') == 'human' else 'source')
                item.update(field_sources=audit['field_sources'],
                            evidence_role=role,
                            review_revision=str(audit['sequence']))
            else:
                item.update(evidence_role='manual' if not event.evidence else 'source', field_sources={})
            events.append(item)
        binding = history[-1].get('binding_details') if history else None
        machine = binding['source_proof'].get('machine_validation') if binding else None
        declarations = binding['attestation'] if binding else None
        return dict(machine_validation=machine, human_declarations=declarations,
            task=task.to_dict(), review_status=latest['review_status'] if latest else 'needs_review',
            review_revision=str(latest['sequence']) if latest else None,
            candidate_fingerprint=latest['candidate_fingerprint'] if latest else None,
            field_sources=latest['field_sources'] if latest else {},
            original_candidate=latest['candidate'] if latest else None, review_history=history, events=events)

    def list_candidates(self, workspace_id, *, include_reviewed=False):
        workspace = required_text(workspace_id, 'workspace_id')
        if type(include_reviewed) is not bool:
            raise ValueError('include_reviewed must be boolean')
        with self.store._transaction():
            result, current_keys = [], set()
            for snapshot in self._candidates(workspace):
                identifier = snapshot.candidate.candidate_id
                current_keys.add((identifier, snapshot.fingerprint))
                link = self.store.get_link(workspace, identifier)
                latest = None
                if link is not None and link.task_id is not None:
                    latest = self._refresh(self.storage.latest(workspace, task_id=link.task_id), self.store._when(None))
                    if latest is not None and latest['candidate_id'] != identifier:
                        latest = None  # A historical link is not the task's current binding.
                needs_review = latest is not None and latest['review_status'] == 'needs_review'
                rejected = self.storage.latest(workspace, candidate_id=identifier) if link is not None and link.task_id is None else None
                if link is None or include_reviewed or needs_review:
                    item = snapshot.to_dict()
                    item['low_confidence'] = snapshot.candidate.confidence == 'LOW'
                    exact_rejection = rejected if rejected and rejected['candidate_fingerprint'] == snapshot.fingerprint else None
                    item.update(reviewed=link is not None, is_current=True,
                                is_current_binding=bool(latest and latest['review_status'] == 'approved'
                                                        and latest['candidate_fingerprint'] == snapshot.fingerprint),
                                review_status=latest['review_status'] if latest else None,
                                review_revision=str(latest['sequence']) if latest else None,
                                task_id=link.task_id if link else None)
                    if exact_rejection:
                        item.update(review_status='rejected', reviewer=exact_rejection['reviewer'],
                                    reason=exact_rejection['reason'], reviewed_at=exact_rejection['reviewed_at'],
                                    processed_action='reject', review_revision=str(exact_rejection['sequence']))
                    result.append(item)
            if include_reviewed:
                for audit in self.storage.rejected(workspace):
                    key = (audit['candidate_id'], audit['candidate_fingerprint'])
                    if key in current_keys:
                        continue
                    item = dict(audit['candidate'])
                    item['low_confidence'] = item['candidate'].get('confidence') == 'LOW'
                    item.update(reviewed=True, is_current=False, is_current_binding=False,
                        review_status='rejected', reviewer=audit['reviewer'], reason=audit['reason'],
                        reviewed_at=audit['reviewed_at'], processed_action='reject',
                        review_revision=str(audit['sequence']), task_id=None)
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
                if (latest is None or latest['candidate_id'] != identifier
                    or latest['review_status'] != 'needs_review'):
                    raise CandidateAlreadyReviewedError('candidate has already been confirmed')
                if latest['candidate'] is None:
                    raise ReviewConflictError('unknown candidate basis requires the controlled legacy review exit with explicit field choices')
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

    def _binding_basis(self, workspace, task, latest):
        if latest is not None and latest['review_status'] != 'needs_review':
            raise ReviewConflictError('binding operation requires needs_review; invalidated must be explicitly undone')
        if latest is not None and latest['candidate'] is not None:
            snapshot = CandidateSnapshot.from_dict(latest['candidate'])
            return dict(snapshot=snapshot, evidence=snapshot.candidate.evidence,
                meeting_time=snapshot.candidate.meeting_time, candidate_id=latest['candidate_id'],
                fingerprint=latest['candidate_fingerprint'], revision=str(latest['sequence']),
                field_sources=latest['field_sources'], legacy_basis=None)
        links = self.storage.connection.execute('''SELECT observation_id,review_action FROM task_observation_link
            WHERE workspace_id=? AND task_id=?''', (workspace, task.id)).fetchall()
        events = self.store.get_events(workspace, task.id)
        if (len(links) != 1 or links[0]['review_action'] != 'create' or not events
            or events[0].event_type is not TaskEventType.CREATED
            or any(event.event_type is not TaskEventType.STATUS_CHANGED for event in events[1:])
            or self.store.replay_task(workspace, task.id) != task):
            raise ReviewConflictError('legacy task needs one create link, replayable history and no field edits or merge chain')
        link = self.store.get_link(workspace, links[0]['observation_id'])
        basis = dict(link=link.to_dict(), created_event=events[0].to_dict(), candidate_snapshot='unknown')
        revision = str(latest['sequence']) if latest else 'legacy:' + digest([task.to_dict(), [event.to_dict() for event in events], basis])
        return dict(snapshot=None, evidence=events[0].evidence, meeting_time=events[0].meeting_time,
            candidate_id=link.observation_id, fingerprint=link.candidate_fingerprint,
            revision=revision, field_sources={}, legacy_basis=basis)

    @staticmethod
    def _single_source(workspace, evidence, meeting_time, *, require_meeting):
        if not evidence or any(reference.workspace_id != workspace for reference in evidence):
            raise ReviewConflictError('binding needs non-empty evidence from this workspace')
        identities = {(reference.source_id, reference.source_version_id) for reference in evidence}
        if len(identities) != 1:
            raise ReviewConflictError('multiple evidence sources or versions cannot establish one binding revision')
        if require_meeting and (meeting_time is None or any(reference.meeting_time != meeting_time for reference in evidence)):
            raise ReviewConflictError('rebinding needs consistent non-empty original meeting times')
        return next(iter(identities))

    def _version_proof(self, workspace, source_id, version_id, evidence, *, current):
        from linkloom.tasks.review_models import SourceVersionProof
        reader = getattr(self.source, 'version_proof', None)
        if not callable(reader):
            raise CandidateUnavailableError('this source adapter cannot verify immutable source versions')
        proof = reader(self.storage.connection, workspace, source_id, version_id,
                       evidence=evidence, require_current=current)
        if (not isinstance(proof, SourceVersionProof) or proof.workspace_id != workspace
            or proof.source_id != source_id or proof.source_version_id != version_id
            or proof.evidence_fingerprint != digest([value.to_dict() for value in evidence])
            or any(value.source_sha256 != proof.content_sha256 for value in evidence)
            or (current and not proof.is_current)):
            raise CandidateUnavailableError('source adapter returned an incomplete or unrelated version proof')
        return proof

    def _prepare_binding(self, workspace, task_id, when):
        task = self.store._require_task(workspace, required_text(task_id, 'task_id'))
        latest = self._refresh(self.storage.latest(workspace, task_id=task.id), when)
        basis = self._binding_basis(workspace, task, latest)
        return task, latest, basis

    def _prepare_rebind(self, workspace, task_id, candidate_id, fingerprint, when):
        task, latest, basis = self._prepare_binding(workspace, task_id, when)
        if when < task.updated_at or (latest and when < datetime.fromisoformat(latest['reviewed_at'])):
            raise ValueError('binding review timestamp precedes the task or latest review')
        snapshot = self._current(workspace, required_text(candidate_id, 'candidate_id'), fingerprint)
        self.store._unprocessed(snapshot.candidate)
        old_source, old_version = self._single_source(workspace, basis['evidence'], basis['meeting_time'], require_meeting=True)
        new_source, new_version = self._single_source(workspace, snapshot.candidate.evidence,
                                                     snapshot.candidate.meeting_time, require_meeting=True)
        if old_source != new_source or basis['meeting_time'] != snapshot.candidate.meeting_time:
            raise ReviewConflictError('rebinding is limited to a revision of the same source and original meeting time')
        old_proof = self._version_proof(workspace, old_source, old_version, basis['evidence'], current=False)
        new_proof = self._version_proof(workspace, new_source, new_version, snapshot.candidate.evidence, current=True)
        if (new_proof.version_sequence <= old_proof.version_sequence
            or basis['meeting_time'] not in old_proof.meeting_times
            or snapshot.candidate.meeting_time not in new_proof.meeting_times):
            raise ReviewConflictError('new candidate must have a strictly newer verified source catalogue entry')
        current, proposed = task_fields(task), task_fields(snapshot.candidate)
        differences = {field: dict(current=current[field], candidate=proposed[field])
                       for field in current if current[field] != proposed[field]}
        value = dict(task=task.to_dict(), current_values=current, candidate_values=proposed,
            differences=differences, required_choices=list(current) if basis['legacy_basis'] else list(differences),
            old_candidate_fingerprint=basis['fingerprint'], new_candidate_fingerprint=snapshot.fingerprint,
            review_revision=basis['revision'], new_candidate=snapshot.to_dict(),
            source_proof=dict(old=old_proof.to_dict(), new=new_proof.to_dict()), legacy_basis=basis['legacy_basis'])
        value['preview_token'] = digest(value)
        return task, basis, snapshot, value

    def preview_rebind(self, workspace_id, task_id, candidate_id, *, expected_candidate_fingerprint, now=None):
        workspace = required_text(workspace_id, 'workspace_id')
        with self.store._transaction():
            return self._prepare_rebind(workspace, task_id, candidate_id, expected_candidate_fingerprint,
                                        self.store._when(now))[3]

    def _resolve_choices(self, task, basis, snapshot, preview, choices, reviewer, reason, when):
        if (not isinstance(choices, dict) or set(choices) - {'title', 'owner', 'deadline'}
            or not set(preview['required_choices']).issubset(choices)):
            raise ValueError('every differing field (and every legacy field) needs an explicit choice')
        values, origins, human_fields = {}, dict(basis['field_sources']), []
        for field, decision in choices.items():
            if not isinstance(decision, dict) or decision.get('choice') not in ('current', 'candidate', 'manual'):
                raise ValueError('field choice must be current, candidate or manual')
            choice = decision['choice']
            if set(decision) != ({'choice', 'value'} if choice == 'manual' else {'choice'}):
                raise ValueError('only manual choices must supply exactly one final value')
            value = decision['value'] if choice == 'manual' else preview[choice + '_values'][field]
            if field == 'deadline':
                if not isinstance(value, dict) or set(value) != {'deadline', 'deadline_text'}:
                    raise ValueError('deadline choice must include deadline and deadline_text together')
                values.update(value)
            else:
                values[field] = value
            human = choice == 'manual' or (choice == 'current' and
                (basis['legacy_basis'] is not None or preview['current_values'][field] != preview['candidate_values'][field]))
            if choice == 'candidate' or human:
                origins[field] = self._origins(snapshot, [field] if human else [], reviewer, reason, when)[field]
            if human:
                human_fields.append(field)
        values, _ = _patch(values)
        return replace(task, **values), origins, human_fields

    def rebind(self, workspace_id, task_id, candidate_id, *, expected_candidate_fingerprint,
               expected_review_revision, expected_preview_token, choices, reviewer, reason, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        when = self.store._when(now)
        with self.store._transaction():
            task, basis, snapshot, preview = self._prepare_rebind(workspace, task_id, candidate_id,
                                                                expected_candidate_fingerprint, when)
            if expected_review_revision != preview['review_revision'] or expected_preview_token != preview['preview_token']:
                raise ReviewConflictError('binding preview or task review revision changed')
            if when < task.updated_at:
                raise ValueError('review timestamp precedes the current task update')
            final, origins, human_fields = self._resolve_choices(task, basis, snapshot, preview, choices, reviewer, reason, when)
            before, after = task_fields(task), task_fields(final)
            changed = [field for field in ('owner', 'deadline', 'title') if before[field] != after[field]]
            events = []
            if changed:
                final = replace(final, updated_at=when)
                self.store._write_task(final)
                for field in changed:
                    source_value = origins[field]['origin'] == 'source'
                    event = self.store._append_event(final, TaskEventType(field + '_changed'), before[field], after[field],
                        evidence=snapshot.candidate.evidence, meeting_time=snapshot.candidate.meeting_time if source_value else None,
                        actor=reviewer, reason=reason, now=when)
                    events.append(event.id)
            self._link(snapshot, task.id, ReviewAction.MERGE, reviewer, reason, when)
            audit = self.storage.append(workspace_id=workspace, task_id=task.id, snapshot=snapshot,
                action='rebind', review_status='approved', original_values=before, final_values=after,
                human_fields=human_fields, field_sources=origins, task_event_ids=events,
                reviewer=reviewer, reason=reason, when=when)
            self.storage.append_binding_details(audit, old_fingerprint=basis['fingerprint'], new_fingerprint=snapshot.fingerprint,
                source_proof=preview['source_proof'], field_choices=choices, legacy_basis=basis['legacy_basis'])
            return self._view(final, audit)
    def _prepare_invalidation(self, workspace, task_id, source_version_id, when):
        task, latest, basis = self._prepare_binding(workspace, task_id, when)
        if when < task.updated_at or (latest and when < datetime.fromisoformat(latest['reviewed_at'])):
            raise ValueError('binding review timestamp precedes the task or latest review')
        source_id, old_version = self._single_source(workspace, basis['evidence'], basis['meeting_time'], require_meeting=False)
        old_proof = self._version_proof(workspace, source_id, old_version, basis['evidence'], current=False)
        new_proof = self._version_proof(workspace, source_id, required_text(source_version_id, 'source_version_id'), (), current=True)
        if new_proof.version_sequence <= old_proof.version_sequence:
            raise ReviewConflictError('invalidation requires a strictly newer current version of the same source')
        proofs = dict(old=old_proof.to_dict(), new=new_proof.to_dict())
        machine = dict(verified_properties=['same_source', 'newer_version', 'current_active', 'original_integrity'],
            source_proof=proofs, checked_at=when.isoformat(), same_meeting='not_machine_verified',
            task_absence='not_machine_verified',
            matching_original_time_present=basis['meeting_time'] is not None and basis['meeting_time'] in new_proof.meeting_times)
        value = dict(task=task.to_dict(), old_candidate_fingerprint=basis['fingerprint'],
            review_revision=basis['revision'], source_version_id=source_version_id,
            source_proof=proofs, legacy_basis=basis['legacy_basis'], machine_validation=machine,
            human_declarations_required=['same_meeting', 'task_absent'])
        # checked_at is an observation time, not an input identity or source version.
        value['preview_token'] = digest({key: item for key, item in value.items() if key != 'machine_validation'})
        return task, basis, value

    def preview_invalidation(self, workspace_id, task_id, source_version_id, *, now=None):
        workspace = required_text(workspace_id, 'workspace_id')
        with self.store._transaction():
            return self._prepare_invalidation(workspace, task_id, source_version_id, self.store._when(now))[2]

    def invalidate(self, workspace_id, task_id, source_version_id, *, expected_review_revision,
                   expected_preview_token, attest_same_meeting, attest_task_absent, reviewer, reason, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        if attest_same_meeting is not True or attest_task_absent is not True:
            raise ValueError('same meeting and task absence both require explicit human declarations')
        when = self.store._when(now)
        with self.store._transaction():
            task, basis, preview = self._prepare_invalidation(workspace, task_id, source_version_id, when)
            if expected_review_revision != preview['review_revision'] or expected_preview_token != preview['preview_token']:
                raise ReviewConflictError('invalidation preview or current task revision changed')
            declaration = dict(origin='human_statement', same_meeting=True, task_absent=True,
                               reviewer=reviewer, reason=reason, reviewed_at=when.isoformat())
            audit = self.storage.append(workspace_id=workspace, task_id=task.id, snapshot=basis['snapshot'],
                action='invalidate', review_status='invalidated', original_values=task_fields(task),
                final_values=task_fields(task), human_fields=[], field_sources=basis['field_sources'], task_event_ids=[],
                reviewer=reviewer, reason=reason, when=when,
                legacy_candidate_id=basis['candidate_id'], legacy_fingerprint=basis['fingerprint'])
            self.storage.append_binding_details(audit, old_fingerprint=basis['fingerprint'], new_fingerprint=None,
                source_proof=dict(**preview['source_proof'], machine_validation=preview['machine_validation']),
                field_choices={}, legacy_basis=basis['legacy_basis'], attestation=declaration, operation='invalidate')
            return self._view(task, audit)

    def undo_invalidation(self, workspace_id, task_id, *, target_review_event_id, expected_review_revision,
                          reviewer, reason, now=None):
        workspace, reviewer, reason = identity(workspace_id, reviewer, reason)
        target = required_text(target_review_event_id, 'target_review_event_id')
        with self.store._transaction():
            task = self.store._require_task(workspace, required_text(task_id, 'task_id'))
            latest = self.storage.latest(workspace, task_id=task.id)
            if (latest is None or latest['review_status'] != 'invalidated' or latest['action'] != 'invalidate'
                or latest['id'] != target or str(latest['sequence']) != expected_review_revision):
                raise ReviewConflictError('undo requires the exact current invalidation event and review revision')
            snapshot = CandidateSnapshot.from_dict(latest['candidate']) if latest['candidate'] is not None else None
            when = self.store._when(now)
            if when < task.updated_at or when < datetime.fromisoformat(latest['reviewed_at']):
                raise ValueError('undo timestamp precedes the task or latest review')
            audit = self.storage.append(workspace_id=workspace, task_id=task.id, snapshot=snapshot,
                action='needs_review', review_status='needs_review', original_values=task_fields(task),
                final_values=task_fields(task), human_fields=[], field_sources=latest['field_sources'], task_event_ids=[],
                reviewer=reviewer, reason=reason, when=when,
                legacy_candidate_id=latest['candidate_id'], legacy_fingerprint=latest['candidate_fingerprint'])
            prior = latest['binding_details']
            self.storage.append_binding_details(audit, old_fingerprint=latest['candidate_fingerprint'], new_fingerprint=None,
                source_proof=dict(previous_validation=prior['source_proof'], reused_from_review_event_id=target),
                field_choices={}, legacy_basis=prior['legacy_basis'],
                attestation=dict(origin='human_statement', withdraws_invalidation=True,
                                 reviewer=reviewer, reason=reason, reviewed_at=when.isoformat()),
                operation='undo_invalidate', target_review_event_id=target)
            return self._view(task, audit)