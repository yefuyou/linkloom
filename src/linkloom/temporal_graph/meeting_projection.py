"""Confirmed meeting/decision/task provenance, projected in the caller's read transaction."""
from datetime import datetime,date
import hashlib,json
from linkloom.temporal_graph.models import Node,Edge,Evidence,digest,parse_time,interval
from linkloom.temporal_graph.projection import _rows,_evidence,task_review_is_current,verified_task_rebind


def _node(workspace,kind,key,label):return Node(workspace,kind,key,label)
def _edge(source,target,kind,left,right,*args,**kwargs):
    # A confirmed business date means its full UTC calendar day. Preserve that
    # declaration in record metadata; normalize only mixed date/timestamp bounds.
    if left is not None and right is not None and type(parse_time(left))!=type(parse_time(right)):
        left=interval(left)[0].isoformat();right=interval(right)[0].isoformat()
    return Edge(source,target,kind,left,right,*args,**kwargs)


def _date(value):
    parsed=parse_time(value)
    return parsed.date().isoformat() if isinstance(parsed,datetime) else parsed.isoformat()


def _reference(db,workspace,reference,authorize):
    if reference.workspace_id!=workspace:raise PermissionError('CROSS_WORKSPACE_EVIDENCE')
    if authorize(workspace,reference.source_id,reference.source_version_id) is not True:raise PermissionError('SOURCE_NOT_AUTHORIZED')
    rows=db.execute("""SELECT evidence_ref FROM ll_source_evidence WHERE workspace_id=? AND source_id=?
        AND artifact_version_id=? AND quote_sha256=? AND line_start=? AND line_end=?""",
        (workspace,reference.source_id,reference.source_version_id,reference.quote_sha256,reference.line_start,reference.line_end)).fetchall()
    if len(rows)!=1:raise ValueError('UNIQUE_VERIFIED_TASK_EVIDENCE_REQUIRED')
    proof=_evidence(db,workspace,rows[0][0],authorize)
    if proof.quote!=reference.quote or proof.content_sha256!=reference.source_sha256:raise ValueError('TASK_REFERENCE_MISMATCH')
    if reference.char_start is not None:
        content=db.execute('SELECT content FROM ll_source_version WHERE workspace_id=? AND source_id=? AND artifact_version_id=?',
            (workspace,reference.source_id,reference.source_version_id)).fetchone()[0]
        if content[reference.char_start:reference.char_end]!=reference.quote:raise ValueError('TASK_CHARACTER_POSITION_MISMATCH')
    return proof


def project_meetings(db,workspace,tables,authorize,limits,confirmed,excluded,current_tasks,task_candidate_source):
    if not {'ll_source','ll_source_version','ll_source_evidence'}<=tables:return [],[],[]
    sources={r['source_id']:r for r in _rows(db,'ll_source',workspace)}
    dates={};nodes={};records={};edges=[]
    def register(source,version,when,evidence):
        if not when or source not in sources:raise ValueError('EXPLICIT_MEETING_IDENTITY_REQUIRED')
        if authorize(workspace,source,version) is not True:raise PermissionError('SOURCE_NOT_AUTHORIZED')
        key=(source,version);day=_date(when)
        dates.setdefault(key,set()).add(day)
        if len(dates[key])!=1:raise ValueError('MEETING_DATE_NOT_UNIQUE')
        node=_node(workspace,'meeting',source,sources[source]['name']);nodes[node.node_id]=node
        prior=records.get(node.node_id)
        records[node.node_id]=dict(node_id=node.node_id,kind='meeting',document_id=source,source_version_id=version,
            meeting_date=day,meeting_time=max(filter(None,[prior.get('meeting_time') if prior else None,when if isinstance(parse_time(when),datetime) else None]),default=None),
            content_sha256=evidence.content_sha256,pending_count=prior['pending_count'] if prior else 0,
            evidence=[evidence.to_dict()])
        return node

    # Date metadata is structural input, never relationship extraction. Whole
    # imported records with conflicting meeting dates stay unclassified.
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    for source in sources.values():
        if not source['active'] or authorize(workspace,source['source_id'],source['current_version_id']) is not True:continue
        row=db.execute('SELECT * FROM ll_source_version WHERE workspace_id=? AND source_id=? AND artifact_version_id=?',
            (workspace,source['source_id'],source['current_version_id'])).fetchone()
        if row is None:continue
        try:
            artifact=RawArtifact(workspace,source['source_id'],'timestamped_text',row['content'],source_uri=row['source_uri'])
            if artifact.content_hash!=row['content_sha256'] or artifact.artifact_version_id!=row['artifact_version_id']:raise ValueError('NEEDS_REVALIDATION')
            segments=parse_source_text(artifact).segments
            days={_date(s.event_time.isoformat()) if s.event_time else s.event_date.isoformat()
                for s in segments if s.event_time is not None or getattr(s,'event_date',None) is not None}
            if len(days)==1 and segments:
                proof=_evidence(db,workspace,segments[0].evidence_span.evidence_ref,authorize)
                register(source['source_id'],row['artifact_version_id'],next(iter(days)),proof)
        except (ValueError,TypeError,KeyError,PermissionError):continue

    semantic_events=_rows(db,'semantic_candidate_event',workspace) if 'semantic_candidate_event' in tables else []
    for decision_id,(record,receipt,evidence,view,approval) in confirmed.items():
        node=_node(workspace,'decision_record',decision_id,record['subject']+': '+record['value']);nodes[node.node_id]=node
        records[node.node_id]=dict(node_id=node.node_id,kind='decision_record',record_id=decision_id,subject_key=record['subject_key'],
            subject=record['subject'],value=record['value'],status=record['memory_state'],valid_from=record['valid_from'],
            valid_to=record['valid_to'],supersedes_id=record['supersedes_id'],confirmation_ref=receipt['receipt_id'],
            evidence=[e.to_dict() for e in evidence])
        try:
            commits=[e for e in semantic_events if e['candidate_id']==receipt['candidate_id'] and
                e['event_type'] in {'MATERIALIZATION_COMMITTED','MATERIALIZATION_DUPLICATE'} and
                json.loads(e['metadata_json']).get('decision_id')==decision_id]
            if len(commits)!=1:raise ValueError('MATERIALIZATION_EVENT_REQUIRED')
            if len({(e.source_id,e.source_version_id) for e in evidence})!=1:raise ValueError('ONE_SOURCE_MEETING_REQUIRED')
            first=evidence[0];meeting_key=(first.source_id,first.source_version_id)
            when=next(iter(dates[meeting_key])) if meeting_key in dates and len(dates[meeting_key])==1 else None
            # Human approval may explicitly confirm source identity/date. This is
            # audit data tied to this candidate, never an inferred import date.
            try:declaration=json.loads(approval['reason']).get('meeting')
            except (ValueError,TypeError):declaration=None
            if declaration:
                if (declaration.get('document_id')!=first.source_id or declaration.get('source_version_id')!=first.source_version_id
                    or declaration.get('content_sha256')!=first.content_sha256):raise ValueError('CONFIRMED_MEETING_SOURCE_MISMATCH')
                when=declaration['date']
            if when is None:
                bound={_date(b.event_time.isoformat()) for b in view.evidence if b.event_time}
                source=view.source_candidate
                if source is not None and source.event_date:bound.add(source.event_date.isoformat())
                if len(bound)==1:when=next(iter(bound))
            meeting=register(first.source_id,first.source_version_id,when,first)
            edges.append(_edge(node,meeting,'decided_in',when,None,'interval',commits[0]['event_id'] if 'event_id' in commits[0] else str(commits[0]['id']),receipt['receipt_id'],evidence))
        except (ValueError,TypeError,KeyError,PermissionError) as error:
            excluded.append(dict(source_record_id=decision_id,reason=str(error)))

    if {'task','task_event','task_review_event','task_observation_link'}<=tables:
        _project_tasks(db,workspace,tables,authorize,limits,confirmed,excluded,register,nodes,records,edges,current_tasks,task_candidate_source)
    # Candidate counts expose no proposal facts and do not turn candidates into nodes.
    reviewed_observations={r['observation_id'] for r in _rows(db,'task_observation_link',workspace)} if 'task_observation_link' in tables else set()
    for candidate in _rows(db,'semantic_candidate',workspace) if 'semantic_candidate' in tables else []:
        if candidate['workflow_state']=='SUPPORTING_ONLY':
            from linkloom.semantic_ingestion.materialization import _candidate_view_from_row
            try:
                original,_,_,_=_candidate_view_from_row(candidate)
                observation=getattr(original,'task_observation',None)
                if observation is None:
                    if getattr(getattr(original,'claim_type',None),'value',None)!='PROPOSAL':continue
                elif observation.observation_id in reviewed_observations:continue
            except (ValueError,TypeError,KeyError):continue
        elif candidate['workflow_state'] not in {'PENDING_REVIEW','CONFLICT','APPROVED'}:continue
        key=(candidate['artifact_id'],candidate['artifact_version_id'])
        if key not in dates or len(dates[key])!=1:continue
        node=_node(workspace,'meeting',key[0],sources[key[0]]['name'])
        if node.node_id in records:records[node.node_id]['pending_count']+=1
    # Conflicting dates invalidate the complete source mapping, regardless of read order.
    invalid={source for (source,_),values in dates.items() if len(values)!=1}
    edges=[e for e in edges if not (e.target.kind=='meeting' and e.target.key in invalid)]
    for source in invalid:
        node=_node(workspace,'meeting',source,sources[source]['name']);nodes.pop(node.node_id,None);records.pop(node.node_id,None)
    if len(edges)>limits.max_edges or len(nodes)>limits.max_nodes:raise ValueError('MEETING_GRAPH_LIMIT')
    return edges,list(nodes.values()),list(records.values())


def _project_tasks(db,workspace,tables,authorize,limits,confirmed,excluded,register,nodes,records,edges,current_tasks,task_candidate_source):
    from linkloom.tasks.review_models import CandidateSnapshot
    from linkloom.tasks.models import EvidenceReference
    reviews=_rows(db,'task_review_event',workspace);events=_rows(db,'task_event',workspace)
    links=_rows(db,'task_observation_link',workspace)
    merges=_rows(db,'task_merge_operation',workspace) if 'task_merge_operation' in tables else []
    assignments=_rows(db,'task_candidate_assignment',workspace) if 'task_candidate_assignment' in tables else []
    def source_decision_view(parent_id):
        if parent_id not in confirmed:return None
        record,_,evidence,_,_=confirmed[parent_id]
        successors=[r for r in confirmed.values() if r[0]['supersedes_id']==parent_id and r[1]['supersedes_id']==parent_id
            and r[0]['valid_from']==record['valid_to'] and r[0]['subject_key']==record['subject_key']]
        successor=successors[0] if len(successors)==1 else None
        return dict(record_id=record['decision_id'],value=record['value'],valid_to=record['valid_to'],
            replacement=dict(value=successor[0]['value'],valid_from=successor[0]['valid_from']) if successor else None,
            evidence=[e.to_dict() for e in evidence],replacement_evidence=[e.to_dict() for e in successor[2]] if successor else [])
    for task in _rows(db,'task',workspace):
        history=sorted([r for r in reviews if r['task_id']==task['id']],key=lambda r:r['sequence'])
        if not history:continue
        latest=history[-1]
        if not task_review_is_current(latest,current_tasks,db=db,workspace=workspace,task_candidate_source=task_candidate_source):
            excluded.append(dict(source_record_id=task['id'],reason='NEEDS_REVALIDATION_CURRENT_TASK_BINDING'));continue
        from linkloom.tasks.merge import _active_stack
        from linkloom.tasks.merge_storage import decode_operation
        try:_active_stack([decode_operation(m) for m in sorted(merges,key=lambda r:r['sequence']) if m['task_id']==task['id']])
        except ValueError:
            excluded.append(dict(source_record_id=task['id'],reason='INVALID_MERGE_HISTORY'));continue
        task_events=sorted([e for e in events if e['task_id']==task['id']],key=lambda e:e['sequence'])
        creation=next((e for e in task_events if e['event_type']=='created'),None)
        if creation is None:continue
        initial_status=json.loads(creation['new_value']).get('status')
        if initial_status not in {'open','in_progress','blocked','done','cancelled'}:continue
        status_events=[e for e in task_events if e['event_type']=='status_changed']
        confirmed_status=json.loads(status_events[-1]['new_value']) if status_events else initial_status
        if task['status']!=confirmed_status:continue
        # Invalidated/needs-review records never regain trust from an old approval.
        if latest['review_status'] in {'invalidated','needs_review'}:continue
        node=_node(workspace,'task',task['id'],task['title']);states=[];task_edges=[];created=None
        for audit in history:
            if audit['review_status']!='approved':continue
            try:
                snapshot=CandidateSnapshot.from_dict(json.loads(audit['candidate_json']))
                candidate=snapshot.candidate
                if snapshot.fingerprint not in current_tasks.get(candidate.candidate_id,set()):raise ValueError('CURRENT_TASK_APPROVAL_CONTEXT_REQUIRED')
                if snapshot.fingerprint!=audit['candidate_fingerprint'] or candidate.workspace_id!=workspace:raise ValueError('TASK_APPROVAL_BINDING_MISMATCH')
                evidence=tuple(_reference(db,workspace,r,authorize) for r in candidate.evidence)
                ids=json.loads(audit['task_event_ids_json']);bound_events=[e for e in events if e['id'] in ids]
                if (len(bound_events)!=len(ids) or any(e['task_id']!=task['id'] or e['actor']!=audit['reviewer'] for e in bound_events)):
                    raise ValueError('TASK_EVENT_BINDING_REQUIRED')
                link=next((l for l in links if l['observation_id']==candidate.candidate_id and l['task_id']==task['id'] and l['candidate_fingerprint']==snapshot.fingerprint),None)
                merge=next((m for m in merges if m['review_event_id']==audit['id'] and m['operation']=='merge'),None)
                if link is None:raise ValueError('TASK_REVIEW_RECEIPT_REQUIRED')
                restoration=next((m for m in merges if m['review_event_id']==audit['id'] and m['operation']=='undo_merge'),None)
                if restoration:
                    target=next((m for m in merges if m['id']==restoration['target_merge_id'] and m['operation']=='merge' and m['task_id']==task['id']),None)
                    if target is None:raise ValueError('EXACT_UNDO_TARGET_REQUIRED')
                    prior=json.loads(target['before_review_json']);declaration=json.loads(restoration['human_declaration_json'])
                    release=next((a for a in assignments if a['id']==restoration['assignment_id']),None)
                    if (prior['candidate_fingerprint']!=snapshot.fingerprint or prior['review_status']!='approved' or
                        declaration.get('withdraws_merge')!=target['id'] or declaration.get('origin')!='human_statement' or
                        release is None or release['operation']!='release' or release['prior_id']!=target['assignment_id']):
                        raise ValueError('EXACT_APPROVED_UNDO_RESTORE_REQUIRED')
                rebind=verified_task_rebind(db,workspace,audit,task_candidate_source) if audit['action']=='rebind' else False
                end=None
                if merge:
                    claim=next((a for a in assignments if a['id']==merge['assignment_id']),None)
                    if (merge['candidate_fingerprint']!=snapshot.fingerprint or merge['candidate_id']!=candidate.candidate_id or
                        claim is None or claim['operation']!='claim' or claim['review_action']!='merge' or
                        claim['task_id']!=task['id'] or claim['candidate_fingerprint']!=snapshot.fingerprint):raise ValueError('EXACT_MERGE_ASSIGNMENT_REQUIRED')
                    declaration=json.loads(merge['human_declaration_json'])
                    if not declaration.get('same_task') or declaration.get('origin')!='human_statement':raise ValueError('HUMAN_MERGE_DECLARATION_REQUIRED')
                    undone=next((m for m in merges if m['operation']=='undo_merge' and m['target_merge_id']==merge['id']),None)
                    end=undone['created_at'] if undone else None
                elif restoration is None and not rebind and link['review_action']!='create' and audit['action'] not in {'edit','reconfirm'}:raise ValueError('CONFIRMED_ASSIGNMENT_REQUIRED')
                restoration=next((m for m in merges if m['review_event_id']==audit['id'] and m['operation']=='undo_merge'),None)
                origins=json.loads(audit['field_sources_json'])
                human_fields=[field for field,origin in origins.items() if origin.get('origin')=='human']
                source_assignment=not human_fields and (merge is not None or audit['action']=='confirm' or rebind)
                try:time_declaration=json.loads(audit['reason'])
                except (ValueError,TypeError):time_declaration={}
                declared_time=time_declaration.get('graph_valid_time');declared_meeting=time_declaration.get('meeting')
                business_date=None
                if declared_time is not None:
                    if (not isinstance(declared_time,dict) or set(declared_time)!={'kind','value'} or declared_time['kind']!='date'
                        or date.fromisoformat(declared_time['value']).isoformat()!=declared_time['value'] or candidate.meeting_time is None
                        or candidate.meeting_time.date().isoformat()!=declared_time['value']):raise ValueError('CONFIRMED_TASK_BUSINESS_TIME_MISMATCH')
                    business_date=declared_time['value']
                when=restoration['created_at'] if restoration else candidate.meeting_time.isoformat() if source_assignment and candidate.meeting_time else audit['reviewed_at']
                if business_date and source_assignment and restoration is None:when=business_date
                final=json.loads(audit['final_values_json']);origins=json.loads(audit['field_sources_json'])
                prior_status=[e for e in status_events if e['created_at']<=when]
                effective_status=json.loads(prior_status[-1]['new_value']) if prior_status else initial_status
                state=dict(review_event_id=audit['id'],event_ids=ids,valid_from=when,valid_to=end,fields=final,
                    status=effective_status,evidence=[e.to_dict() for e in evidence],confirmation_ref=audit['id'],
                    field_sources=origins,human_context_fields=human_fields,reviewer=audit['reviewer'],reviewed_at=audit['reviewed_at'],
                    business_valid_from=business_date,time_precision='date' if len(when)==10 else 'datetime',system_recorded_at=audit['reviewed_at'],
                    source_event_time=candidate.meeting_time.isoformat() if candidate.meeting_time else None,business_time_confirmed=source_assignment and business_date is not None,
                    source_assignment=dict(title=candidate.title,owner=candidate.owner,deadline=dict(deadline=candidate.deadline,deadline_text=candidate.deadline_text)))
                states.append(state)
                is_create=any(e['event_type']=='created' for e in bound_events)
                if is_create or rebind:
                    if candidate.source_decision_id!=task['source_decision_id']:raise ValueError('TASK_SOURCE_DECISION_NOT_CONFIRMED')
                    created=(candidate,audit,evidence,when)
                meetings={(r.source_id,r.source_version_id,r.meeting_time.isoformat() if r.meeting_time else None) for r in candidate.evidence}
                assignment=restoration is None and (is_create or merge is not None or any(e['event_type']=='owner_changed' for e in bound_events))
                if not assignment:continue
                if declared_meeting is not None:
                    if business_date is None or declared_meeting.get('date')!=business_date:raise ValueError('EXPLICIT_MEETING_TIME_REQUIRED')
                    selected=[r for r in candidate.evidence if r.source_id==declared_meeting.get('document_id') and
                        r.source_version_id==declared_meeting.get('source_version_id') and r.source_sha256==declared_meeting.get('content_sha256')]
                    if not selected:raise ValueError('CONFIRMED_MEETING_WITNESS_REQUIRED')
                    source,version,meeting_time=selected[0].source_id,selected[0].source_version_id,business_date
                    meeting_proof=next(e for e in evidence if e.source_id==source and e.source_version_id==version)
                elif len(meetings)==1:
                    source,version,meeting_time=next(iter(meetings));meeting_proof=evidence[0]
                else:continue
                if not meeting_time:continue
                # Human field edits may be contextual evidence; do not claim the
                # meeting assigned those edited values.
                if origins.get('owner',{}).get('evidence_role')!='source':continue
                meeting=register(source,version,meeting_time,meeting_proof)
                witness=next((e['id'] for e in bound_events if e['event_type'] in {'created','owner_changed'}),audit['id'])
                task_edges.append(_edge(node,meeting,'assigned_in',when,end,'interval',witness,audit['id'],evidence))
            except (ValueError,TypeError,KeyError,PermissionError) as error:
                excluded.append(dict(source_record_id=audit['id'],reason=str(error)))
        if not states:continue
        approved_final=json.loads(latest['final_values_json'])
        if latest['review_status']=='approved' and (task['title']!=approved_final['title'] or task['owner']!=approved_final['owner'] or
            task['deadline']!=approved_final['deadline']['deadline'] or task['deadline_text']!=approved_final['deadline']['deadline_text']):
            excluded.append(dict(source_record_id=task['id'],reason='CURRENT_TASK_FIELDS_NOT_CONFIRMED'));continue
        # Status is a separate explicit human event; never copy today's status
        # backward into a historical review state.
        for event in status_events:
            eligible=[s for s in states if s['valid_from']<=event['created_at'] and (s['valid_to'] is None or event['created_at']<s['valid_to'])]
            if not eligible:continue
            prior=max(eligible,key=lambda s:s['valid_from'])
            changed=dict(prior,review_event_id=event['id'],event_ids=[event['id']],valid_from=event['created_at'],
                status=json.loads(event['new_value']),confirmation_ref=event['id'],status_basis='HUMAN_TASK_EVENT')
            states.append(changed)
        if latest['review_status']=='rejected':
            end=latest['reviewed_at']
            for state in states:
                if state['valid_to'] is None or state['valid_to']>end:state['valid_to']=end
            task_edges=[_edge(e.source,e.target,e.edge_type,e.valid_from,end,e.valid_time_kind,
                e.source_record_id,e.confirmation_ref,e.evidence) for e in task_edges if e.valid_from<end]
        nodes[node.node_id]=node
        # Close each state when a later confirmed state takes effect. Undo bounds
        # are retained rather than erasing history.
        states.sort(key=lambda r:(interval(r['valid_from'])[0],r['review_event_id']))
        # Equal business dates have no confirmed order. Preserve both values
        # through the next strictly later instant so the query reports CONFLICT.
        starts=sorted({interval(s['valid_from'])[0] for s in states})
        next_start={a:b for a,b in zip(starts,starts[1:])}
        representation={stamp:next(s['valid_from'] for s in states if interval(s['valid_from'])[0]==stamp) for stamp in starts}
        for state in states:
            successor=next_start.get(interval(state['valid_from'])[0])
            if successor is not None and (state['valid_to'] is None or interval(state['valid_to'])[0]>successor):state['valid_to']=representation[successor]
        records[node.node_id]=dict(node_id=node.node_id,kind='task',task_id=task['id'],title=task['title'],
            source_decision_id=task['source_decision_id'],states=states,status=task['status'],review_status=latest['review_status'],
            source_decision=source_decision_view(task['source_decision_id']))
        edges.extend(task_edges)
        ownership=[]
        for state in states:
            owner=state['fields'].get('owner')
            if not owner or not state.get('business_time_confirmed') or state['field_sources'].get('owner',{}).get('origin')!='source':continue
            if ownership and ownership[-1]['owner']==owner and ownership[-1]['valid_to']==state['valid_from']:
                ownership[-1]['valid_to']=state['valid_to']
            else:ownership.append(dict(owner=owner,valid_from=state['valid_from'],valid_to=state['valid_to'],basis=state))
        for item in ownership:
            person=_node(workspace,'person',' '.join(item['owner'].casefold().split()),item['owner'])
            nodes[person.node_id]=person;state=item['basis']
            proof=tuple(Evidence(**e) for e in state['evidence'])
            edges.append(_edge(person,node,'owner_of',item['valid_from'],item['valid_to'],'interval',state['review_event_id'],state['confirmation_ref'],proof))
        if created and task['source_decision_id'] in confirmed:
            candidate,audit,evidence,when=created
            parent=confirmed[task['source_decision_id']]
            decision=_node(workspace,'decision_record',parent[0]['decision_id'],parent[0]['subject']+': '+parent[0]['value'])
            edges.append(_edge(node,decision,'derived_from',when,latest['reviewed_at'] if latest['review_status']=='rejected' else None,'interval',audit['id'],audit['id'],tuple((*evidence,*parent[2]))))
