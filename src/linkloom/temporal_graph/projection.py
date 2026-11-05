"""Deterministic projection, never extraction. No model, writes or source regex."""
import hashlib,json
from dataclasses import replace
from linkloom.temporal_graph.models import Node,Edge,Evidence,GraphSnapshot


def _rows(db,table,workspace):
    # Internal constant table names only; no model/caller SQL interpolation.
    rows=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE workspace_id=? LIMIT 16385',(workspace,))]
    if len(rows)>16384: raise ValueError('GRAPH_RECORD_LIMIT')
    return rows


def _evidence(db,workspace,ref,authorize):
    row=db.execute("""SELECT e.*,v.content,s.active,s.current_version_id
       FROM ll_source_evidence e JOIN ll_source_version v
       ON e.workspace_id=v.workspace_id AND e.source_id=v.source_id AND e.artifact_version_id=v.artifact_version_id
       JOIN ll_source s ON s.workspace_id=e.workspace_id AND s.source_id=e.source_id
       WHERE e.workspace_id=? AND e.evidence_ref=?""",(workspace,ref)).fetchone()
    if row is None: raise ValueError('VERIFIED_EVIDENCE_REQUIRED')
    row=dict(row)
    if authorize(workspace,row['source_id'],row['artifact_version_id']) is not True:
        raise PermissionError('SOURCE_NOT_AUTHORIZED')
    if not row['active'] or row['current_version_id']!=row['artifact_version_id']:
        raise ValueError('SOURCE_REVALIDATION_REQUIRED')
    if hashlib.sha256(row['content'].encode('utf8')).hexdigest()!=row['content_sha256']:
        raise ValueError('SOURCE_HASH_MISMATCH')
    selected=''.join(row['content'].splitlines(keepends=True)[row['line_start']-1:row['line_end']])
    if not row['quote'] or row['quote'] not in selected: raise ValueError('EVIDENCE_POSITION_MISMATCH')
    return Evidence(workspace,ref,row['source_id'],row['artifact_version_id'],row['content_sha256'],
                    row['quote'],row['quote_sha256'],row['line_start'],row['line_end'])


def _node(workspace,kind,label,scope='',key=None):
    return Node(workspace,kind,key or ' '.join(label.casefold().split()),label,scope)


def _make(workspace,predicate,subject,value,start,end,kind,record_id,confirmation,evidence,*,scope='',
          subject_kind=None,object_kind=None,blocker=False):
    from linkloom.temporal_graph.models import required
    required(subject,'subject');required(value,'object')
    declared={'owner_of_project':('owner_of','project',None),'owner_of_plan':('owner_of','plan',None),
        'owner_of_task':('owner_of','task',None),'alias_of_project':('alias_of',None,'project'),
        'alias_of_plan':('alias_of',None,'plan'),'alias_of_task':('alias_of',None,'task'),
        'supersedes_plan':('supersedes','plan','plan'),'supersedes_task':('supersedes','task','task')}
    if predicate in declared:
        predicate,declared_subject,declared_object=declared[predicate]
        if subject_kind and declared_subject and subject_kind!=declared_subject:raise ValueError('CONFIRMED_ENDPOINT_TYPE_CONFLICT')
        if object_kind and declared_object and object_kind!=declared_object:raise ValueError('CONFIRMED_ENDPOINT_TYPE_CONFLICT')
        subject_kind=declared_subject or subject_kind;object_kind=declared_object or object_kind
    if predicate=='owner_of':
        if subject_kind not in {'project','plan','task'}:raise ValueError('ENDPOINT_TYPE_NOT_CONFIRMED')
        source=_node(workspace,'person',value,scope); target=_node(workspace,subject_kind,subject,scope)
        relation='owner_of'
    elif predicate in {'reports_to','direct_leader_of'}:
        source=_node(workspace,'person',subject,scope); target=_node(workspace,'person',value,scope); relation='reports_to'
    elif predicate=='superseded_by':
        source=_node(workspace,'plan',value,scope); target=_node(workspace,'plan',subject,scope); relation='supersedes'
    elif predicate=='supersedes':
        if subject_kind not in {'plan','task','decision_record'} or object_kind!=subject_kind:raise ValueError('ENDPOINT_TYPE_NOT_CONFIRMED')
        source=_node(workspace,subject_kind or 'plan',subject,scope)
        target=_node(workspace,object_kind or subject_kind or 'plan',value,scope); relation=predicate
    elif predicate=='alias_of':
        if object_kind not in {'project','plan','task'}:raise ValueError('ENDPOINT_TYPE_NOT_CONFIRMED')
        source=_node(workspace,'alias',subject,scope)
        target=_node(workspace,object_kind or 'project',value,scope); relation='alias_of'
    else: raise ValueError('UNSUPPORTED_GRAPH_PREDICATE')
    return Edge(source,target,relation,start,end,kind,record_id,confirmation,evidence,blocker)


def _resolve_explicit_identities(edges,entries,excluded):
    # Build the full map before emitting any edge; receipt order is irrelevant.
    def label_key(node):return (node.workspace_id,node.kind,node.scope,' '.join(node.label.casefold().split()))
    identities={}
    for node,values in entries:identities.setdefault(label_key(node),set()).update(values)
    result=[]
    for edge in edges:
        nodes=[];ambiguous=False
        for node in (edge.source,edge.target):
            options=identities.get(label_key(node),set())
            if len(options)>1:
                ambiguous=True;break
            nodes.append(replace(node,key=next(iter(options))) if options else node)
        if ambiguous:
            excluded.append(dict(source_record_id=edge.source_record_id,reason='ENDPOINT_IDENTITY_NOT_UNIQUE'))
        else:result.append(replace(edge,source=nodes[0],target=nodes[1]))
    return result


def _confirmed_decisions(db,workspace,tables,authorize,limits,excluded,confirmed=None,identity_output=None):
    required={'decision','decision_evidence','semantic_candidate','semantic_candidate_event','semantic_materialization_receipt',
              'll_source','ll_source_version','ll_source_evidence'}
    if not required<=tables: return []
    from linkloom.semantic_ingestion.materialization import _candidate_view_from_row,_apply_view_review_resolution,CandidateReviewResolution
    candidates={r['candidate_id']:r for r in _rows(db,'semantic_candidate',workspace)}
    receipts=_rows(db,'semantic_materialization_receipt',workspace); events=_rows(db,'semantic_candidate_event',workspace)
    decisions={r['decision_id']:r for r in _rows(db,'decision',workspace)}; out=[];validated={};identity_entries=[]
    for receipt in receipts:
        record=decisions.get(receipt['decision_id']); row=candidates.get(receipt['candidate_id'])
        if not record or not row or receipt['authorization_type']!='HUMAN_APPROVAL': continue
        if record['memory_state'] not in {'ACTIVE','SUPERSEDED'} or row['workflow_state'] not in {'MATERIALIZED','DUPLICATE'}: continue
        if row['payload_version']=='graph-eval-candidate/v1': continue
        if authorize(workspace,row['artifact_id'],row['artifact_version_id']) is not True: continue
        try:
            approvals=[e for e in events if e['candidate_id']==row['candidate_id'] and e['event_type']=='HUMAN_APPROVAL'
                and e['actor_type']=='HUMAN' and e['actor_id']==receipt['authorization_id']
                and e['event_at']==receipt['authorized_at'] and e['candidate_fingerprint']==receipt['candidate_fingerprint']]
            if len(approvals)!=1: raise ValueError('EXACT_HUMAN_APPROVAL_REQUIRED')
            candidate,view,encoded,fp=_candidate_view_from_row(row)
            if encoded!=row['payload_json'] or fp!=row['candidate_fingerprint'] or fp!=receipt['candidate_fingerprint']:
                raise ValueError('CANDIDATE_INTEGRITY_MISMATCH')
            metadata=json.loads(approvals[0]['metadata_json']); resolution=metadata.get('review_resolution')
            if resolution is not None:
                resolution=CandidateReviewResolution.from_dict(resolution)
                if resolution.fingerprint!=receipt.get('review_resolution_fingerprint'): raise ValueError('REVIEW_RESOLUTION_MISMATCH')
                view=_apply_view_review_resolution(view,resolution)
            if not view.provenance_valid or not view.temporal_resolved or not view.subject_resolved or not view.relation_resolved or view.relation!=record['relation'] or view.value!=record['value'] or view.subject!=record['subject']:
                raise ValueError('CONFIRMED_RECORD_MISMATCH')
            from linkloom.semantic_ingestion.materialization import _as_utc
            from datetime import datetime,date
            if (_as_utc(view.valid_from)!=datetime.fromisoformat(record['valid_from']) or
                datetime.fromisoformat(record['valid_from'])!=datetime.fromisoformat(receipt['effective_at'])):
                raise ValueError('CONFIRMED_VALID_TIME_MISMATCH')
            if view.valid_to is not None and _as_utc(view.valid_to)!=datetime.fromisoformat(record['valid_to']):
                raise ValueError('CONFIRMED_VALID_TO_MISMATCH')

            refs=[r[0] for r in db.execute('SELECT evidence_ref FROM decision_evidence WHERE decision_id=? ORDER BY ordinal',(record['decision_id'],))]
            if not refs or set(refs)!=set(view.decision_evidence_refs): raise ValueError('EXACT_EVIDENCE_SET_REQUIRED')
            evidence=tuple(_evidence(db,workspace,ref,authorize) for ref in refs)
            bindings={b.evidence_ref:b for b in view.evidence}
            for item in evidence:
                binding=bindings[item.evidence_id]
                if (item.source_id!=binding.source_identity or item.source_version_id!=binding.source_version or
                    item.content_sha256!=binding.content_hash or item.quote!=binding.quote or
                    item.quote_sha256!=binding.quote_sha256 or item.line_start!=binding.line_start or item.line_end!=binding.line_end):
                    raise ValueError('EXACT_CANDIDATE_EVIDENCE_BINDING_REQUIRED')
            if receipt['supersedes_id']!=record['supersedes_id']:raise ValueError('RECEIPT_LINEAGE_MISMATCH')
            validated[record['decision_id']]=(record,receipt,evidence)
            if confirmed is not None:confirmed[record['decision_id']]=(record,receipt,evidence,view,approvals[0])
            predicate=record['relation']; blocked=predicate.startswith('not_'); predicate=predicate.removeprefix('not_')
            temporal_kind='interval';edge_from=record['valid_from'];edge_to=record['valid_to']
            simulated_scope=None
            if approvals[0]['actor_id']=='graph-eval-simulated-human':
                audit=json.loads(approvals[0]['reason'])
                original=audit['original_candidate']
                if (audit.get('confirmation_label')!='模拟人工确认' or original['subject']!=view.subject or
                    original['relation']!=view.relation or original['value']!=view.value or not original.get('valid_on') or
                    _as_utc(date.fromisoformat(original['valid_on']))!=_as_utc(view.valid_from)):
                    raise ValueError('SIMULATED_CONFIRMATION_CHANGED_CANDIDATE')
                simulated_scope=audit['graph_scope']
                temporal_kind='snapshot' if original.get('temporal_kind')=='snapshot' else 'interval'
                edge_from=original['valid_on'];edge_to=original.get('valid_to')
            options=simulated_scope if simulated_scope is not None else metadata.get('graph_scope',{})
            if not isinstance(options,dict): raise ValueError('INVALID_CONFIRMED_SCOPE')
            out.append(_make(workspace,predicate,record['subject'],record['value'],edge_from,edge_to,
                temporal_kind,record['decision_id'],receipt['receipt_id'],evidence,scope=options.get('business_scope',''),
                subject_kind=options.get('subject_kind'),object_kind=options.get('object_kind'),blocker=blocked))
            agent=view.agent_candidate
            if agent is not None:
                subject_node=out[-1].target if out[-1].edge_type=='owner_of' or predicate=='superseded_by' else out[-1].source
                chosen=resolution.subject_id if resolution is not None and resolution.subject_id else agent.subject_id
                if chosen:
                    identities={entry.subject_id for entry in agent.subject_registry_snapshot
                        if ' '.join(entry.display_label.casefold().split())==' '.join(view.subject.casefold().split())}
                    identities.add(chosen)
                    identity_entries.append((subject_node,identities))
        except (ValueError,TypeError,KeyError,StopIteration) as error:
            excluded.append(dict(source_record_id=receipt['candidate_id'],reason=str(error)))
        except PermissionError: continue
        if len(out)>limits.max_edges: raise ValueError('GRAPH_EDGE_LIMIT')
    # Record lineage is independent of the business-predicate vocabulary. Both
    # endpoints must pass the current authorization/approval/evidence checks.
    for record,receipt,evidence in validated.values():
        previous=validated.get(record['supersedes_id'])
        if previous and not receipt.get('correction_of_decision_id'):
            prior,_,prior_evidence=previous
            if (prior['subject_key']!=record['subject_key'] or prior['relation']!=record['relation'] or
                prior['valid_to']!=record['valid_from']):continue
            left=_node(workspace,'decision_record',record['decision_id'],key=record['decision_id'])
            right=_node(workspace,'decision_record',prior['decision_id'],key=prior['decision_id'])
            proof=tuple({e.evidence_id:e for e in (*prior_evidence,*evidence)}.values())
            out.append(Edge(left,right,'supersedes',record['valid_from'],None,'interval',record['decision_id'],receipt['receipt_id'],proof))
            if len(out)>limits.max_edges:raise ValueError('GRAPH_EDGE_LIMIT')
    if identity_output is not None:identity_output.extend(identity_entries)
    return _resolve_explicit_identities(out,identity_entries,excluded)


def _unmaterialized(db,workspace,tables,authorize,excluded):
    if not {'semantic_candidate','semantic_candidate_event','ll_source_evidence'}<=tables:return
    from linkloom.semantic_ingestion.materialization import _candidate_view_from_row
    events=_rows(db,'semantic_candidate_event',workspace)
    for row in _rows(db,'semantic_candidate',workspace):
        if row['workflow_state']!='APPROVED':continue
        if authorize(workspace,row['artifact_id'],row['artifact_version_id']) is not True:continue
        if not any(e['candidate_id']==row['candidate_id'] and e['event_type']=='HUMAN_APPROVAL' and
                   e['actor_type']=='HUMAN' and e['candidate_fingerprint']==row['candidate_fingerprint'] for e in events):continue
        try:
            _,view,encoded,fp=_candidate_view_from_row(row)
            if encoded!=row['payload_json'] or fp!=row['candidate_fingerprint']:raise ValueError('CANDIDATE_INTEGRITY_MISMATCH')
            for ref in view.decision_evidence_refs:_evidence(db,workspace,ref,authorize)
            excluded.append(dict(source_record_id=row['candidate_id'],reason='TIME_NOT_CONFIRMED' if not view.temporal_resolved else 'MATERIALIZATION_REQUIRED'))
        except PermissionError:continue
        except (ValueError,TypeError,KeyError):continue


def confirmed_task_business_date(review,candidate):
    """Only an explicit human date and its exact current source witness qualify."""
    from datetime import date
    try:declaration=json.loads(review['reason'])
    except (ValueError,TypeError):return None
    if not isinstance(declaration,dict) or declaration.get('graph_valid_time') is None:return None
    timing=declaration['graph_valid_time']
    if (not isinstance(timing,dict) or set(timing)!={'kind','value'} or timing['kind']!='date' or
        date.fromisoformat(timing['value']).isoformat()!=timing['value'] or candidate.meeting_time is None or
        candidate.meeting_time.date().isoformat()!=timing['value']):raise ValueError('CONFIRMED_TASK_BUSINESS_TIME_MISMATCH')
    meeting=declaration.get('meeting')
    if meeting is not None:
        if not isinstance(meeting,dict) or meeting.get('date')!=timing['value']:raise ValueError('CONFIRMED_BUSINESS_SOURCE_REQUIRED')
        witnesses=[r for r in candidate.evidence if r.source_id==meeting.get('document_id') and r.source_version_id==meeting.get('source_version_id') and r.source_sha256==meeting.get('content_sha256') and r.meeting_time and r.meeting_time.date().isoformat()==timing['value']]
        if not witnesses:raise ValueError('CONFIRMED_BUSINESS_SOURCE_REQUIRED')
    elif len({(r.source_id,r.source_version_id) for r in candidate.evidence})!=1:raise ValueError('UNIQUE_CONFIRMED_BUSINESS_SOURCE_REQUIRED')
    return timing['value']


def verified_task_rebind(db,workspace,review,task_candidate_source):
    """Verify the existing product's immutable same-source revision receipt."""
    from linkloom.tasks.review_models import CandidateSnapshot
    try:
        if review['action']!='rebind' or review['review_status']!='approved':return False
        detail=db.execute('SELECT * FROM task_review_binding_detail WHERE workspace_id=? AND review_event_id=?',(workspace,review['id'])).fetchone()
        if detail is None or detail['operation']!='rebind' or json.loads(detail['legacy_basis_json']) is not None:return False
        new=CandidateSnapshot.from_dict(json.loads(review['candidate_json']))
        if detail['new_candidate_fingerprint']!=new.fingerprint or new.fingerprint!=review['candidate_fingerprint']:return False
        prior=db.execute("SELECT * FROM task_review_event WHERE workspace_id=? AND task_id=? AND candidate_fingerprint=? AND review_status='approved' AND sequence<? ORDER BY sequence DESC LIMIT 1",(workspace,review['task_id'],detail['old_candidate_fingerprint'],review['sequence'])).fetchone()
        if prior is None:return False
        old=CandidateSnapshot.from_dict(json.loads(prior['candidate_json']))
        saved=json.loads(detail['source_proof_json'])
        if set(saved)!={'old','new'}:return False
        for name,snapshot,current in [('old',old,False),('new',new,True)]:
            candidate=snapshot.candidate
            identities={(r.source_id,r.source_version_id) for r in candidate.evidence}
            if candidate.workspace_id!=workspace or len(identities)!=1 or candidate.meeting_time is None:return False
            source,version=next(iter(identities))
            proof=task_candidate_source.version_proof(db,workspace,source,version,evidence=candidate.evidence,require_current=current)
            if proof.to_dict()!=saved[name] or candidate.meeting_time not in proof.meeting_times:return False
        a,b=saved['old'],saved['new']
        if a['source_id']!=b['source_id'] or b['version_sequence']<=a['version_sequence'] or old.candidate.meeting_time!=new.candidate.meeting_time:return False
        choices=json.loads(detail['field_choices_json']);origins=json.loads(review['field_sources_json']);prior_origins=json.loads(prior['field_sources_json'])
        before=json.loads(review['original_values_json']);after=json.loads(review['final_values_json'])
        def fields(candidate):return dict(title=candidate.title,owner=candidate.owner,deadline=dict(deadline=candidate.deadline,deadline_text=candidate.deadline_text))
        proposed=fields(new.candidate)
        if before!=json.loads(prior['final_values_json']):return False
        if set(choices)-set(proposed) or set(origins)!=set(proposed):return False
        for field in proposed:
            choice=choices.get(field)
            if choice is None:
                if before[field]!=proposed[field] or after[field]!=before[field] or origins[field]!=prior_origins[field]:return False
            elif choice=={'choice':'candidate'}:
                if after[field]!=proposed[field] or origins[field].get('candidate_fingerprint')!=new.fingerprint or origins[field].get('origin')!='source':return False
            elif choice=={'choice':'current'}:
                if after[field]!=before[field]:return False
            elif isinstance(choice,dict) and choice.get('choice')=='manual' and set(choice)=={'choice','value'}:
                if after[field]!=choice['value'] or origins[field].get('origin')!='human':return False
            else:return False
            if origins[field].get('candidate_fingerprint') not in {old.fingerprint,new.fingerprint}:return False
        return True
    except (ValueError,TypeError,KeyError,AttributeError):return False


def task_review_is_current(review,current_tasks,*,db=None,workspace=None,task_candidate_source=None):
    from linkloom.tasks.review_models import CandidateSnapshot
    if review is None or review['review_status']!='approved':return False
    try:
        snapshot=CandidateSnapshot.from_dict(json.loads(review['candidate_json']))
        if snapshot.fingerprint not in current_tasks.get(snapshot.candidate.candidate_id,set()):return False
        allowed={fingerprint for values in current_tasks.values() for fingerprint in values}
        origins=json.loads(review['field_sources_json'])
        if all(origin.get('candidate_fingerprint') in allowed for origin in origins.values()):return True
        return db is not None and verified_task_rebind(db,workspace,review,task_candidate_source)
    except (ValueError,TypeError,KeyError):return False


def _tasks(db,workspace,tables,authorize,limits,excluded,current_tasks,task_candidate_source):
    needed={'task','task_review_event','task_merge_operation','task_candidate_assignment','ll_source_evidence'}
    if not needed<=tables: return []
    from linkloom.tasks.review_models import CandidateSnapshot,ReviewConflictError
    from linkloom.tasks.merge import _active_stack
    from linkloom.tasks.merge_storage import decode_operation
    history=[decode_operation(r) for r in sorted(_rows(db,'task_merge_operation',workspace),key=lambda r:r['sequence'])]
    tasks={r['id']:r for r in _rows(db,'task',workspace)}
    reviews=_rows(db,'task_review_event',workspace); assignments=_rows(db,'task_candidate_assignment',workspace)
    latest={}
    for row in sorted(reviews,key=lambda r:r['sequence']): latest[row['task_id']]=row
    out=[]
    # Existing owner fields have no business valid-time field. Never infer it from meeting/review time.
    for task_id,row in latest.items():
        if row['review_status']=='approved' and tasks.get(task_id,{}).get('owner'):
            excluded.append(dict(source_record_id=row['id'],reason='TASK_OWNER_VALID_TIME_NOT_CONFIRMED'))
    for task_id,task in tasks.items():
        if not task_review_is_current(latest.get(task_id),current_tasks,db=db,workspace=workspace,task_candidate_source=task_candidate_source):continue
        task_history=[r for r in history if r['task_id']==task_id]
        try:_active_stack(task_history)  # Validate complete append-only merge/undo lineage.
        except ReviewConflictError:
            excluded.append(dict(source_record_id=task_id,reason='INVALID_MERGE_HISTORY')); continue
        reversals={r['target_merge_id']:r for r in task_history if r['operation']=='undo_merge'}
        stack=[r for r in task_history if r['operation']=='merge']
        for merge in stack:
            try:
                if merge['human_declaration'].get('same_task') is not True or merge['human_declaration'].get('origin')!='human_statement': raise ValueError('HUMAN_IDENTITY_DECLARATION_REQUIRED')
                review=next(r for r in reviews if r['id']==merge['review_event_id'])
                reversal=reversals.get(merge['id'])
                if review['review_status']!='approved' or (reversal is None and latest[task_id]['review_status']!='approved'): raise ValueError('APPROVED_TASK_BINDING_REQUIRED')
                tips=[r for r in assignments if r['candidate_id']==merge['candidate_id']]
                claim=next(r for r in tips if r['id']==merge['assignment_id'])
                if claim['operation']!='claim' or claim['task_id']!=task_id or claim['review_action']!='merge':raise ValueError('EXACT_MERGE_ASSIGNMENT_REQUIRED')
                tip=max(tips,key=lambda r:r['sequence'])
                if reversal is None and tip['id']!=claim['id']:raise ValueError('ACTIVE_ASSIGNMENT_REQUIRED')
                if reversal is not None and not any(r['operation']=='release' and r['prior_id']==claim['id'] for r in tips):
                    raise ValueError('EXACT_MERGE_RELEASE_REQUIRED')
                snapshot=CandidateSnapshot.from_dict(json.loads(review['candidate_json']))
                if snapshot.fingerprint not in current_tasks.get(snapshot.candidate.candidate_id,set()):raise ValueError('CURRENT_TASK_APPROVAL_CONTEXT_REQUIRED')
                if snapshot.fingerprint!=merge['candidate_fingerprint'] or snapshot.candidate.workspace_id!=workspace:
                    raise ValueError('MERGE_CANDIDATE_MISMATCH')
                evidence=[]
                from linkloom.tasks.legacy_adapter import LegacyTaskCandidateSource
                from linkloom.temporal_graph.models import digest
                for reference in snapshot.candidate.evidence:
                    if authorize(workspace,reference.source_id,reference.source_version_id) is not True:
                        raise PermissionError('SOURCE_NOT_AUTHORIZED')
                    proof=task_candidate_source.version_proof(db,workspace,reference.source_id,
                        reference.source_version_id,evidence=(reference,),require_current=True)
                    evidence.append(Evidence(workspace,'task-evidence-'+digest(reference.to_dict())[:32],
                        reference.source_id,reference.source_version_id,proof.content_sha256,
                        reference.quote,reference.quote_sha256,reference.line_start,reference.line_end))
                left=_node(workspace,'task_observation',merge['candidate_id'],key=merge['candidate_id'])
                right=_node(workspace,'task',task['title'],key=task_id)
                business_date=confirmed_task_business_date(review,snapshot.candidate)
                start=business_date or merge['created_at'];end=reversal['created_at'] if reversal else None
                if end is not None and len(start)==10:
                    from linkloom.temporal_graph.models import interval
                    start=interval(start)[0].isoformat();end=interval(end)[0].isoformat()
                out.append(Edge(left,right,'same_task',start,end,'interval',merge['id'],review['id'],
                    tuple(evidence),time_basis='CONFIRMED_VALID_TIME' if business_date else 'HUMAN_IDENTITY_ASSERTION'))
            except (ValueError,KeyError,TypeError,StopIteration) as error:
                excluded.append(dict(source_record_id=merge['id'],reason=str(error)))
            except PermissionError: continue
    return out


def project_records(db,workspace,authorize,limits,task_candidate_source):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'll_workspace' not in tables or db.execute('SELECT 1 FROM ll_workspace WHERE workspace_id=?',(workspace,)).fetchone() is None:
        raise PermissionError('WORKSPACE_NOT_AUTHORIZED')
    excluded=[]
    confirmed={};identity_entries=[]
    edges=_confirmed_decisions(db,workspace,tables,authorize,limits,excluded,confirmed,identity_entries)
    _unmaterialized(db,workspace,tables,authorize,excluded)
    current_tasks={}
    if {'task_review_event','task_review_source_change'}<=tables:
        try:
            for snapshot in task_candidate_source.list_candidates(db,workspace):
                if all(authorize(workspace,r.source_id,r.source_version_id) is True for r in snapshot.candidate.evidence):
                    current_tasks.setdefault(snapshot.candidate.candidate_id,set()).add(snapshot.fingerprint)
        except (ValueError,TypeError,KeyError):
            excluded.append(dict(source_record_id='tasks',reason='CURRENT_TASK_SOURCE_CONTEXT_INVALID'))
    edges+=_tasks(db,workspace,tables,authorize,limits,excluded,current_tasks,task_candidate_source)
    from linkloom.temporal_graph.meeting_projection import project_meetings
    meeting_edges,catalogue,records=project_meetings(db,workspace,tables,authorize,limits,confirmed,excluded,current_tasks,task_candidate_source)
    meeting_edges=_resolve_explicit_identities(meeting_edges,[(n,ids) for n,ids in identity_entries if n.kind=='person'],excluded)
    catalogue=[n for n in catalogue if n.kind!='person']
    edges+=meeting_edges
    by_id={n.node_id:n for n in catalogue}
    edges=[replace(e,source=by_id.get(e.source.node_id,e.source),target=by_id.get(e.target.node_id,e.target)) for e in edges]
    if len(edges)>limits.max_edges: raise ValueError('GRAPH_EDGE_LIMIT')
    if sum(len(e.quote.encode('utf8')) for edge in edges for e in edge.evidence)>limits.max_evidence_bytes:
        raise ValueError('GRAPH_EVIDENCE_LIMIT')
    return GraphSnapshot(workspace,tuple(sorted(edges,key=lambda e:e.edge_id)),tuple(excluded),tuple(catalogue),tuple(records))
