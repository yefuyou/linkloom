"""Explicit human candidate merging and reverse-order compensating undo."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from linkloom.tasks.merge_storage import MergeStorage, assignment_tip, append_assignment
from linkloom.tasks.models import ObservationLink, ReviewAction, Task, TaskEventType, required_text
from linkloom.tasks.review_models import CandidateSnapshot, CandidateUnavailableError, ReviewConflictError, digest, identity, task_fields


_FIELDS = ('title','owner','deadline')


class MergeBlockedError(ReviewConflictError):
    def __init__(self, message, blocking_operations):
        super().__init__(message)
        self.blocking_operations = tuple(blocking_operations)


def _fields(value):
    if (not isinstance(value,(list,tuple)) or any(field not in _FIELDS for field in value)
        or len(set(value)) != len(value)):
        raise ValueError('apply_fields must explicitly select title, owner or deadline once each')
    return [field for field in _FIELDS if field in value]


def _review_state(audit):
    # Reference immutable prior audit, without recursively embedding prior merge details.
    return {key:value for key,value in audit.items() if key not in ('merge_details','binding_details','storage_action')}


def _business(task):
    value = dict(task)
    value.pop('updated_at')
    return value


def _active_stack(history):
    stack = []
    for item in history:
        if item['operation']=='merge':
            expected = stack[-1]['id'] if stack else None
            if item['parent_merge_id'] != expected:
                raise ReviewConflictError('merge operation history has an invalid parent')
            stack.append(item)
        else:
            if not stack or stack[-1]['id'] != item['target_merge_id']:
                raise ReviewConflictError('merge undo history is not a reverse-order stack')
            stack.pop()
    return stack


class TaskMergeService:
    def __init__(self, review):
        self.review = review
        self.store = review.store
        self.storage = MergeStorage(self.store)

    def _proofs(self, snapshot):
        grouped = {}
        for reference in snapshot.candidate.evidence:
            grouped.setdefault((reference.source_id,reference.source_version_id),[]).append(reference)
        return [self.review._version_proof(snapshot.candidate.workspace_id,source,version,tuple(evidence),current=True).to_dict()
                for (source,version),evidence in sorted(grouped.items())]

    def _prepare(self, workspace, task_id, candidate_id, fingerprint, apply_fields, when):
        selected = _fields(apply_fields)
        task = self.store._require_task(workspace,required_text(task_id,'task_id'))
        latest = self.review._refresh(self.review.storage.latest(workspace,task_id=task.id),when)
        if latest is None or latest['review_status'] != 'approved' or latest['candidate'] is None:
            raise ReviewConflictError('merge target requires a current approved task binding')
        if when < task.updated_at or when < datetime.fromisoformat(latest['reviewed_at']):
            raise ValueError('merge timestamp precedes the task or latest review')
        snapshot = self.review._current(workspace,required_text(candidate_id,'candidate_id'),fingerprint)
        self.store._unprocessed(snapshot.candidate)
        proof = self._proofs(snapshot)
        history = self.storage.history(workspace,task.id)
        stack = _active_stack(history)
        current, proposed = task_fields(task),task_fields(snapshot.candidate)
        values = {}
        for field in selected:
            if field=='deadline': values.update(proposed[field])
            else: values[field] = proposed[field]
        final = replace(task,**values)
        events = self.store.get_events(workspace,task.id)
        tip = assignment_tip(self.storage.connection,snapshot.candidate.candidate_id)
        preview = dict(task=task.to_dict(),current_values=current,candidate_values=proposed,
            differences={field:dict(current=current[field],candidate=proposed[field]) for field in _FIELDS if current[field]!=proposed[field]},
            apply_fields=selected,final_values=task_fields(final),field_sources=latest['field_sources'],
            candidate=snapshot.to_dict(),candidate_fingerprint=snapshot.fingerprint,
            review_revision=str(latest['sequence']),task_event_tail=events[-1].to_dict() if events else None,
            candidate_assignment_revision=tip['id'] if tip else None,
            parent_merge_id=stack[-1]['id'] if stack else None,
            source_proof=proof,low_confidence=snapshot.candidate.confidence=='LOW',
            same_task='human_decision_required')
        preview['preview_token'] = digest(preview)
        return task,latest,snapshot,final,preview

    def preview_merge(self, workspace_id, task_id, candidate_id, *, expected_candidate_fingerprint, apply_fields, now=None):
        workspace = required_text(workspace_id,'workspace_id')
        with self.store._transaction():
            return self._prepare(workspace,task_id,candidate_id,expected_candidate_fingerprint,apply_fields,self.store._when(now))[-1]

    def merge(self, workspace_id, task_id, candidate_id, *, expected_candidate_fingerprint, expected_review_revision,
              expected_preview_token, apply_fields, reviewer, reason, now=None):
        workspace,reviewer,reason = identity(workspace_id,reviewer,reason)
        when = self.store._when(now)
        with self.store._transaction():
            task,latest,snapshot,final,preview = self._prepare(workspace,task_id,candidate_id,
                expected_candidate_fingerprint,apply_fields,when)
            if expected_review_revision!=preview['review_revision'] or expected_preview_token!=preview['preview_token']:
                raise ReviewConflictError('merge preview, assignment, source or task review changed')
            before,after = task_fields(task),task_fields(final)
            sources = dict(latest['field_sources'])
            for field in preview['apply_fields']:
                sources[field] = self.review._origins(snapshot,[],reviewer,reason,when)[field]
            self.review._validate_approved(snapshot,sources,task.id)
            changed = [field for field in ('owner','deadline','title') if before[field]!=after[field]]
            event_ids = []
            if changed:
                final = replace(final,updated_at=when)
                self.store._write_task(final)
                for field in changed:
                    event_ids.append(self.store._append_event(final,TaskEventType(field+'_changed'),before[field],after[field],
                        evidence=snapshot.candidate.evidence,meeting_time=snapshot.candidate.meeting_time,
                        actor=reviewer,reason=reason,now=when).id)
            self.review._link(snapshot,final.id,ReviewAction.MERGE,reviewer,reason,when)
            audit = self.review.storage.append(workspace_id=workspace,task_id=task.id,snapshot=snapshot,
                action='rebind',review_status='approved',original_values=before,final_values=after,human_fields=[],
                field_sources=sources,task_event_ids=event_ids,reviewer=reviewer,reason=reason,when=when)
            tip = assignment_tip(self.storage.connection,snapshot.candidate.candidate_id)
            self.storage.append(workspace_id=workspace,task_id=task.id,operation='merge',target_merge_id=None,
                parent_merge_id=preview['parent_merge_id'],audit=audit,snapshot=snapshot,assignment_id=tip['id'],
                before_task=task.to_dict(),after_task=final.to_dict(),before_review=_review_state(latest),
                apply_fields=preview['apply_fields'],source_proof=dict(verified_at=when.isoformat(),evidence=preview['source_proof'],task_event_tail_before=preview['task_event_tail'],
                    same_task='not_machine_verified'),human_declaration=dict(origin='human_statement',same_task=True,
                    reviewer=reviewer,reason=reason,reviewed_at=when.isoformat()),event_ids=event_ids,when=when)
            return self.review._view(final,self.review.storage.latest(workspace,task_id=task.id))

    def _prepare_undo(self, workspace, task_id, merge_id, when):
        task = self.store._require_task(workspace,required_text(task_id,'task_id'))
        identifier = required_text(merge_id,'merge_id')
        history = self.storage.history(workspace,task.id)
        target = next((item for item in history if item['id']==identifier and item['operation']=='merge'),None)
        if target is None: raise ReviewConflictError('merge is unavailable for this task and workspace')
        stack = _active_stack(history)
        if not stack or stack[-1]['id']!=identifier:
            blockers=[dict(operation='merge',id=item['id']) for item in stack if item['sequence']>target['sequence']]
            raise MergeBlockedError('undo requires the most recent active merge; undo later merges first',blockers)
        latest = self.review.storage.latest(workspace,task_id=task.id)
        events = self.store.get_events(workspace,task.id)
        blockers=[]
        last_operation = history[-1]
        allowed_reviews={item['review_event_id'] for item in history if item['sequence']>=target['sequence']}
        for audit in self.review.storage.history(workspace,task.id):
            if audit['sequence'] <= target['after_review']['sequence']: continue
            if audit['id'] in allowed_reviews: continue
            if audit['action']=='needs_review' and audit['reviewer']=='system:source-validation': continue
            blockers.append(dict(operation=audit['action'],id=audit['id'],reviewed_at=audit['reviewed_at']))
        known_events={identifier for item in history if item['sequence']>=target['sequence'] for identifier in item['task_event_ids']}
        before_tail=target['source_proof']['task_event_tail_before']
        before_sequence=before_tail['sequence'] if before_tail else 0
        for event in events:
            if event.sequence>before_sequence and event.id not in known_events:
                blockers.append(dict(operation=event.event_type.value,id=event.id,created_at=event.created_at.isoformat()))
        if (_business(task.to_dict())!=_business(target['after_task'])
            or latest['candidate_fingerprint']!=target['after_review']['candidate_fingerprint']
            or latest['field_sources']!=target['after_review']['field_sources']):
            blockers.append(dict(operation='current_state_changed',id=latest['id']))
        tip=assignment_tip(self.storage.connection,target['candidate_id'])
        if tip is None or tip['operation']!='claim' or tip['id']!=target['assignment_id']:
            blockers.append(dict(operation='candidate_assignment_changed',id=tip['id'] if tip else None))
        if blockers: raise MergeBlockedError('later edits or status changes block undo; restore them before undo',blockers)
        if when < task.updated_at or when < datetime.fromisoformat(latest['reviewed_at']):
            raise ValueError('undo timestamp precedes task or latest review')
        restored=Task.from_dict(target['before_task'])
        desired=replace(task,title=restored.title,owner=restored.owner,deadline=restored.deadline,
                        deadline_text=restored.deadline_text,status=restored.status)
        original=CandidateSnapshot.from_dict(target['before_review']['candidate'])
        proof_status='approved'
        try:
            self.review._validate_dependencies(target['before_review'])
        except (CandidateUnavailableError,ReviewConflictError):
            proof_status='needs_review'
        preview=dict(task=task.to_dict(),merge_id=identifier,review_revision=str(latest['sequence']),
            restored_values=task_fields(desired),restored_field_sources=target['before_review']['field_sources'],
            restored_candidate=original.to_dict(),restored_review_status=proof_status,
            candidate_assignment_revision=tip['id'],operation_tail=last_operation['id'],
            task_event_tail=events[-1].to_dict() if events else None)
        preview['preview_token']=digest(preview)
        return task,latest,target,original,desired,preview

    def preview_undo_merge(self,workspace_id,task_id,merge_id,*,now=None):
        workspace=required_text(workspace_id,'workspace_id')
        with self.store._transaction():
            return self._prepare_undo(workspace,task_id,merge_id,self.store._when(now))[-1]

    def undo_merge(self,workspace_id,task_id,merge_id,*,expected_review_revision,expected_preview_token,
                   reviewer,reason,now=None):
        workspace,reviewer,reason=identity(workspace_id,reviewer,reason)
        when=self.store._when(now)
        with self.store._transaction():
            task,latest,target,snapshot,final,preview=self._prepare_undo(workspace,task_id,merge_id,when)
            if expected_review_revision!=preview['review_revision'] or expected_preview_token!=preview['preview_token']:
                raise ReviewConflictError('undo preview, assignment, source or task review changed')
            if preview['restored_review_status']=='approved':
                self.review._validate_approved(snapshot,target['before_review']['field_sources'],task.id)
            before,after=task_fields(task),task_fields(final)
            event_ids=[]
            changed=[field for field in ('owner','deadline','title') if before[field]!=after[field]]
            if changed:
                final=replace(final,updated_at=when)
                self.store._write_task(final)
                raw_snapshots={row['candidate_fingerprint']:row['candidate'] for row in self.review.storage.history(workspace,task.id)
                               if row['candidate'] is not None}
                def field_evidence(origins, field, fallback):
                    fingerprint=origins.get(field,{}).get('candidate_fingerprint')
                    raw=raw_snapshots.get(fingerprint)
                    return CandidateSnapshot.from_dict(raw).candidate.evidence if raw else fallback
                for field in changed:
                    old_evidence=field_evidence(latest['field_sources'],field,
                        CandidateSnapshot.from_dict(target['after_review']['candidate']).candidate.evidence)
                    new_evidence=field_evidence(target['before_review']['field_sources'],field,snapshot.candidate.evidence)
                    evidence=tuple(dict.fromkeys((*old_evidence,*new_evidence)))
                    event_ids.append(self.store._append_event(final,TaskEventType(field+'_changed'),before[field],after[field],
                        evidence=evidence,meeting_time=None,actor=reviewer,reason=reason,now=when).id)
            old_link=self.store.get_link(workspace,target['candidate_id'])
            released=ObservationLink(old_link.observation_id,workspace,old_link.task_id,old_link.review_action,
                                    reviewer,reason,when,old_link.candidate_fingerprint)
            tip=append_assignment(self.storage.connection,released,'release')
            audit=self.review.storage.append(workspace_id=workspace,task_id=task.id,snapshot=snapshot,
                action='reconfirm' if preview['restored_review_status']=='approved' else 'needs_review',
                review_status=preview['restored_review_status'],original_values=before,final_values=after,human_fields=[],
                field_sources=target['before_review']['field_sources'],task_event_ids=event_ids,
                reviewer=reviewer,reason=reason,when=when)
            self.storage.append(workspace_id=workspace,task_id=task.id,operation='undo_merge',target_merge_id=target['id'],
                parent_merge_id=target['parent_merge_id'],audit=audit,snapshot=CandidateSnapshot.from_dict(target['after_review']['candidate']),
                assignment_id=tip['id'],before_task=task.to_dict(),after_task=final.to_dict(),before_review=_review_state(latest),
                apply_fields=target['apply_fields'],source_proof=dict(restored_review_status=preview['restored_review_status'],
                    restored_binding_from=target['before_review']['id']),human_declaration=dict(origin='human_statement',
                    withdraws_merge=target['id'],reviewer=reviewer,reason=reason,reviewed_at=when.isoformat()),
                event_ids=event_ids,when=when)
            return self.review._view(final,self.review.storage.latest(workspace,task_id=task.id))

    def _evidence_view(self, workspace, evidence):
        result=[]
        for reference in evidence:
            value=reference.to_dict() if hasattr(reference,'to_dict') else dict(reference)
            row=self.storage.connection.execute("""SELECT source_name FROM ll_source_version
                WHERE workspace_id=? AND source_id=? AND artifact_version_id=?""",
                (workspace,value['source_id'],value['source_version_id'])).fetchone()
            value['source_name']=row['source_name'] if row else None
            result.append(value)
        return result

    def enrich_view(self, task, result):
        history=result['review_history']
        snapshots={item['candidate_fingerprint']:item['candidate'] for item in history if item['candidate'] is not None}
        audits={identifier:item for item in history for identifier in item['task_event_ids']}
        prior_by_id={item['id']:history[index-1] if index else None for index,item in enumerate(history)}
        def origin_evidence(origins,field,fallback):
            fingerprint=origins.get(field,{}).get('candidate_fingerprint')
            stored=snapshots.get(fingerprint)
            return self._evidence_view(task.workspace_id,stored['candidate']['evidence'] if stored else fallback)
        timeline=[]
        for event in result['events']:
            audit=audits.get(event['id'])
            detail=audit.get('merge_details') if audit else None
            prior=detail['before_review'] if detail else prior_by_id.get(audit['id']) if audit else None
            field=event['event_type'].removesuffix('_changed')
            before=[] if field=='created' else origin_evidence(prior['field_sources'] if prior else {},field,event['evidence'])
            after=origin_evidence(audit['field_sources'] if audit else {},field,event['evidence'])
            operation=detail['operation'] if detail else audit['action'] if audit else 'manual_status'
            before_source=prior['field_sources'].get(field) if prior and field!='created' else None
            after_source=audit['field_sources'].get(field) if audit and field!='created' else audit['field_sources'] if audit else None
            after_human=(after_source.get('origin')=='human' if field!='created' and after_source else
                         any(value.get('origin')=='human' for value in after_source.values()) if after_source else False)
            event.update(operation=operation,operation_id=detail['id'] if detail else None,
                         before_evidence=before,after_evidence=after,
                         before_field_source=before_source,after_field_source=after_source,
                         before_evidence_role='context' if before_source and before_source.get('origin')=='human' else 'source' if before else None,
                         after_evidence_role='context' if after_human else 'source' if after else None,
                         meeting_contexts=self._evidence_view(task.workspace_id,event['evidence']))
            if operation=='undo_merge':
                event['evidence_role']='context'
            timeline.append(dict(event))
        merges=self.storage.history(task.workspace_id,task.id)
        undone={item['target_merge_id']:item for item in merges if item['operation']=='undo_merge'}
        operations=[]
        for item in merges:
            value=dict(item)
            reversal=undone.get(item['id'])
            value['undone_by']=dict(id=reversal['id'],reviewer=reversal['human_declaration']['reviewer'],
                reason=reversal['human_declaration']['reason'],created_at=reversal['created_at']) if reversal else None
            operations.append(value)
        result.update(timeline=timeline,merge_history=tuple(operations))
        if merges:
            result['human_declarations']=merges[-1]['human_declaration']
            result['merge_machine_validation']=merges[-1]['source_proof']
        return result
