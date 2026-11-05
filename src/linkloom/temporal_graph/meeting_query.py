"""Typed single/list/inspection/comparison operations on one read-only graph snapshot."""
from dataclasses import dataclass
from datetime import UTC,datetime,date
from copy import deepcopy
from linkloom.temporal_graph.dates import parse_date_literal,date_matches
from time import monotonic
import logging
from linkloom.temporal_graph.plan_contracts import LIST_RELATIONS,result_kinds
from linkloom.temporal_graph.models import EDGE_TYPES,GraphLimits,digest,parse_time,interval,required,Edge

FIELDS={'node','record','owner','deadline','status','history','comparison','meeting_date','pending_count','valid_from','valid_to'}


@dataclass(frozen=True)
class MeetingGraphPlan:
    steps:tuple
    outputs:tuple
    as_of:str|None=None

    @classmethod
    def from_dict(cls,data,*,nodes,limits=None):
        limits=limits or GraphLimits()
        if set(data)!={'steps','outputs','as_of'}:raise ValueError('INVALID_MEETING_PLAN')
        parse_time(data['as_of']);known={n.node_id:n for n in nodes};prior={};depth={};children={};signatures={};relation_count=0
        inferred={};outputs=deepcopy(data['outputs'])
        if not isinstance(data['steps'],list) or not 1<=len(data['steps'])<=limits.max_subgoals:raise ValueError('SUBGOAL_LIMIT')
        for original in data['steps']:
            row=deepcopy(original)
            base={'id','operation','input','as_of'}
            op=row.get('operation');extra={'edge_type','direction','cardinality','selection'} if op=='traverse' else ({'other_step','domain'} if 'other_step' in row else {'other','domain'}) if op=='compare' else set()
            if op=='compare' and ('previous_anchor' in row or 'baseline_scope' in row):
                if row.get('baseline_scope')!='workspace' or row.get('previous_anchor') not in {row['input'].get('node'),row.get('other')}:raise ValueError('INVALID_PREVIOUS_MEETING_REFERENCE')
                extra=extra|{'previous_anchor','baseline_scope'}
            if op=='traverse' and 'target_kind' in row:
                if row['target_kind'] not in {'person','task','project','plan','decision_record','meeting','alias','task_observation'}:raise ValueError('INVALID_TARGET_KIND')
                extra=extra|{'target_kind'}
            if op not in {'traverse','inspect','inspect_history','compare','resolve'} or set(row)!=base|extra:raise ValueError('INVALID_MEETING_STEP')
            key=required(row['id'],'step_id',64)
            if not key.isidentifier() or key in prior:raise ValueError('INVALID_STEP_ID')
            parse_time(row['as_of']);inp=row['input']
            if not isinstance(inp,dict) or len(inp)!=1:raise ValueError('INVALID_STEP_INPUT')
            if set(inp)=={'entity_mention'} and op=='resolve':
                from linkloom.temporal_graph.entity_locator import validate_definition
                validate_definition(inp['entity_mention']);depth[key]=0
            elif set(inp)=={'entity'} and op=='resolve':
                entity=inp['entity']
                if not isinstance(entity,dict) or set(entity)!={'text','kind'} or entity['kind'] not in {'task','decision_record','meeting','person','project','plan','alias','task_observation'}:raise ValueError('INVALID_UNMATCHED_ENTITY')
                required(entity['text'],'entity_text',160);depth[key]=0
            elif set(inp)=={'candidates'} and op=='resolve':
                if not isinstance(inp['candidates'],list) or len(inp['candidates'])>limits.max_fanout or len(set(inp['candidates']))!=len(inp['candidates']) or any(n not in known for n in inp['candidates']):raise ValueError('INVALID_ROOT_CANDIDATES')
                for node_id in inp['candidates']:
                    chosen=known[node_id]
                    peers={n.node_id for n in nodes if n.kind==chosen.kind and n.label.casefold()==chosen.label.casefold()}
                    if not peers<=set(inp['candidates']):raise ValueError('INCOMPLETE_AMBIGUOUS_ROOT_CANDIDATES')
                depth[key]=0
            elif set(inp)=={'meeting_date'} and op=='resolve':
                parse_date_literal(inp['meeting_date']);depth[key]=0
            elif set(inp)=={'node'}:
                if inp['node'] not in known:raise ValueError('UNKNOWN_NODE')
                root=known[inp['node']]
                if sum(n.kind==root.kind and n.label.casefold()==root.label.casefold() for n in nodes)>1:raise ValueError('AMBIGUOUS_ROOT_ENTITY')
                depth[key]=int(op=='traverse')
            elif set(inp)=={'step'} and inp['step'] in prior:depth[key]=depth[inp['step']]+int(op=='traverse')
            else:raise ValueError('PRIOR_DEPENDENCY_REQUIRED')
            if depth[key]>limits.max_hops:raise ValueError('HOP_LIMIT_EXCEEDED')
            if op=='traverse' and (row['edge_type'] not in EDGE_TYPES or row['direction'] not in {'in','out'} or
                row['cardinality'] not in {'single','list'} or row['selection'] not in {'all','latest'}):raise ValueError('INVALID_TRAVERSAL')
            if op=='traverse':
                if row['cardinality']=='list' and (row['edge_type'],row['direction']) not in LIST_RELATIONS:
                    row['cardinality']='single'
                    logging.getLogger(__name__).warning('GRAPH_CONTRACT_NORMALIZED step=%s cardinality=list->single relation=%s/%s',key,row['edge_type'],row['direction'])
                if row['selection']=='latest' and (row['edge_type'],row['direction'],row['cardinality'])!=('assigned_in','out','single'):
                    raise ValueError('LATEST_ONLY_FOR_CURRENT_TASK_ASSIGNMENT')
            incoming={known[inp['node']].kind} if 'node' in inp else inferred.get(inp.get('step'),set())
            if 'candidates' in inp:incoming={known[n].kind for n in inp['candidates']}
            if 'meeting_date' in inp:incoming={'meeting'}
            if 'entity' in inp:incoming={inp['entity']['kind']}
            if 'entity_mention' in inp:incoming={inp['entity_mention']['kind']}
            if op=='inspect_history':
                if incoming=={'task'}:
                    # Existing Task inspection already projects bounded assignment
                    # states and event evidence. No Decision traversal is involved.
                    row['operation']=op='inspect'
                    for field in ('record','history'):
                        selector=dict(step=key,field=field)
                        if selector not in outputs:outputs.append(selector)
                    logging.getLogger(__name__).warning('GRAPH_CONTRACT_NORMALIZED step=%s operation=inspect_history->inspect+history kind=task',key)
                elif incoming!={'decision_record'}:
                    raise ValueError('HISTORY_REQUIRES_TASK_OR_DECISION: use inspect with history for a confirmed Task, or inspect_history for a Decision')
            inferred[key]=result_kinds(row,incoming)
            if op=='compare':
                if 'other_step' in row:
                    if set(inp)!={'step'} or inp['step'] not in prior or row['other_step'] not in prior or row['domain'] not in {'decisions','tasks'}:raise ValueError('TWO_MEETINGS_REQUIRED')
                    children[row['other_step']]=children.get(row['other_step'],0)+1
                    if children[row['other_step']]>limits.max_fanout:raise ValueError('FANOUT_LIMIT_EXCEEDED')
                elif 'node' not in inp or root.kind!='meeting' or row['other'] not in known or known[row['other']].kind!='meeting' or row['domain'] not in {'decisions','tasks'}:raise ValueError('TWO_MEETINGS_REQUIRED')
            if 'step' in inp:
                children[inp['step']]=children.get(inp['step'],0)+1
                if children[inp['step']]>limits.max_fanout:raise ValueError('FANOUT_LIMIT_EXCEEDED')
            relation_count+=int(op in {'traverse','compare','inspect_history'})
            if relation_count>limits.max_relation_queries:raise ValueError('RELATION_QUERY_LIMIT')
            signature=digest({k:v for k,v in row.items() if k!='id'}|{'input':{'signature':signatures[inp['step']]} if 'step' in inp else inp})
            if signature in signatures.values():
                if original['operation']=='inspect_history' and op=='inspect':
                    raise ValueError('TASK_HISTORY_DUPLICATES_INSPECTION: emit one inspect with record and history outputs')
                raise ValueError('DUPLICATE_STEP')
            signatures[key]=signature
            prior[key]=row
        if not isinstance(outputs,list) or not outputs:raise ValueError('OUTPUTS_REQUIRED')
        if len(outputs)>limits.max_subgoals*3:raise ValueError('OUTPUT_LIMIT')
        for row in outputs:
            if set(row)!={'step','field'} or row['step'] not in prior or row['field'] not in FIELDS:raise ValueError('INVALID_OUTPUT_SELECTOR')
        return cls(tuple(prior.values()),tuple(outputs),data['as_of'])

    def to_dict(self):return dict(steps=list(self.steps),outputs=list(self.outputs),as_of=self.as_of)


def _valid(record,when):
    if record['kind']=='meeting':return interval(record['meeting_date'])[0]<=interval(when)[0]
    if record['kind']=='decision_record':
        left,right=interval(when);start,_=interval(record['valid_from']);end=interval(record['valid_to'])[0] if record['valid_to'] else None
        return (start<right and (end is None or end>left)) if type(parse_time(when)) is date else start<=left and (end is None or left<end)
    return bool(_task_states(record,when))


def _task_states(record,when):
    left,right=interval(when);parsed=parse_time(when);out=[]
    for state in record.get('states',[]):
        start,_=interval(state['valid_from']);end=interval(state['valid_to'])[0] if state['valid_to'] else None
        if start<=left and (end is None or left<end):out.append(state)
        elif type(parsed) is date and start<right and (end is None or end>left):
            # A date crossing a confirmed intraday transition is not midnight.
            out.append(dict(state,time_precision_ambiguous=True))
    return out


def _proof(record):
    if record.get('kind')!='task':return record.get('evidence',[])
    evidence=[e for s in record['states'] for e in s['evidence']]
    if record.get('source_decision_warning'):evidence+=record.get('source_decision',{}).get('evidence',[])
    return evidence


def _selected(record,when):
    if record['kind']!='task':
        selected=deepcopy(record)
        if record['kind']=='decision_record' and type(parse_time(when)) is date:
            left,right=interval(when);start=interval(record['valid_from'])[0];end=interval(record['valid_to'])[0] if record.get('valid_to') else None
            if left<start<right or end is not None and left<end<right:return None
        if record['kind']=='decision_record' and record.get('valid_to') and interval(when)[0]<interval(record['valid_to'])[0]:
            selected['status']='ACTIVE'
        return selected
    states=_task_states(record,when)
    if len(states)!=1 or states[0].get('time_precision_ambiguous'):return None
    selected=deepcopy(record);selected['current']=deepcopy(states[0])
    cutoff=interval(when)[0]
    selected['states']=[deepcopy(s) for s in record['states'] if interval(s['valid_from'])[0]<=cutoff]
    selected['status']=states[0]['status']
    parent=selected.get('source_decision')
    if parent and parent.get('replacement') and interval(parent['replacement']['valid_from'])[0]<=interval(when)[0]:
        parent['evidence']+=parent.get('replacement_evidence',[])
        parent.pop('replacement_evidence',None)
        cancellation=any(s.get('status')=='cancelled' for s in selected['states'])
        selected['source_decision_warning']='来源决定“'+parent['value']+'”已被“'+parent['replacement']['value']+'”替代（'+parent['replacement']['valid_from']+'）；'+('该任务存在取消记录，按对应任务事件判断' if cancellation else '该任务本身没有取消记录；来源决定的替代不等于任务已取消；是否调整或取消需人工确认')
    elif parent:
        parent['replacement']=None;parent.pop('replacement_evidence',None)
    return selected


def _source_order(node_id,records,edges):
    evidence=_proof(records[node_id]) if node_id in records else []
    membership=[proof for edge in edges if edge.get('edge_type') in {'decided_in','assigned_in'} and edge['source']['node_id']==node_id and edge['target']['kind']=='meeting' for proof in edge['evidence'] if proof['source_id']==edge['target']['key']]
    if membership:evidence=membership
    return (min((e['line_start'] for e in evidence),default=2147483647),node_id)


def _edge_proof(edge):
    """References are not full business records; retain proof without false kinds."""
    data=edge.to_dict()
    for side in ('source','target'):
        reference=data.pop(side);reference['node_type']=reference.pop('kind')
        data[side+'_ref']=reference
    return dict(schema_version='graph-edge-proof/v1',**data)


def nearest_previous_meeting(records,anchor):
    """Explicit workspace chronological policy; never infer a project/series."""
    current=records.get(anchor)
    if not current or current.get('kind')!='meeting':raise ValueError('MEETING_ANCHOR_REQUIRED')
    start,stop=interval(current.get('meeting_time') or current['meeting_date'])
    before=[];uncertain=[]
    for key,item in records.items():
        if key==anchor or item.get('kind')!='meeting':continue
        left,right=interval(item.get('meeting_time') or item['meeting_date'])
        if right<=start and left<start:before.append((left,right,key))
        elif left==start or left<(stop if stop>start else start):uncertain.append(key)
    if before:
        latest=max(left for left,_,_ in before)
        # A date is a half-open interval, not midnight. Overlapping precision
        # cannot establish which meeting was latest; exact equal instants tie.
        candidates=[key for left,right,key in before if right>latest or left==right==latest]
    else:candidates=[]
    if uncertain:return dict(status='AMBIGUOUS',candidates=sorted(set(candidates+uncertain)),reason='MEETING_ORDER_REQUIRES_TIME')
    if not before:return dict(status='NOT_FOUND',candidates=[],reason='NO_PREVIOUS_MEETING')
    candidates=sorted(candidates)
    return dict(status='FOUND' if len(candidates)==1 else 'AMBIGUOUS',candidates=candidates,reason=None if len(candidates)==1 else 'PREVIOUS_MEETING_NOT_UNIQUE')


def _meetings_exceed_as_of(records,as_of):
    if as_of is None:return False
    cutoff_left,cutoff_right=interval(as_of)
    starts=[interval(r.get('meeting_time') or r['meeting_date'])[0] for r in records]
    return any(t>=cutoff_right for t in starts) if type(parse_time(as_of)) is date else any(t>cutoff_left for t in starts)


def compare_meetings(snapshot,first_id,second_id,domain,*,limits,as_of=None):
    records={r['node_id']:r for r in snapshot.records}
    first,second=records[first_id],records[second_id]
    if _meetings_exceed_as_of((first,second),as_of):
        return dict(status='NO_VALID_RECORD_AT_TIME',reason='COMPARISON_EXCEEDS_AS_OF')
    if first['meeting_date']==second['meeting_date'] and (not first.get('meeting_time') or not second.get('meeting_time') or first['meeting_time']==second['meeting_time']):
        return dict(status='CONFLICT',reason='MEETING_ORDER_REQUIRES_TIME')
    first_time=first.get('meeting_time') or first['meeting_date'];second_time=second.get('meeting_time') or second['meeting_date']
    swapped=interval(first_time)[0]>interval(second_time)[0]
    if swapped:first,second=second,first
    dates=[first.get('meeting_time') or first['meeting_date'],second.get('meeting_time') or second['meeting_date']]
    kind='decision_record' if domain=='decisions' else 'task'
    # Only records belonging to a confirmed meeting are comparable. Snapshot
    # carry-forward retains unchanged decisions/tasks from earlier meetings.
    membership='decided_in' if kind=='decision_record' else 'assigned_in'
    eligible={e.source.node_id for e in snapshot.edges if e.edge_type==membership}
    views=[];unaligned=[]
    for when in dates:
        view={}
        for node_id,record in records.items():
            if record['kind']!=kind or node_id not in eligible or not _valid(record,when):continue
            key=record.get('subject_key') if kind=='decision_record' else record.get('task_id')
            selected=_selected(record,when)
            if not key or selected is None:unaligned.append(dict(node_id=node_id,kind=record['kind'],reason='NO_CONFIRMED_ALIGNMENT_OR_TIME'));continue
            view.setdefault(key,[]).append(selected)
        views.append(view)
    labels={n.node_id:n.label for n in snapshot.nodes if n.kind=='meeting'}
    result=dict(earlier_meeting=first['node_id'],later_meeting=second['node_id'],order_swapped=swapped,
        earlier_meeting_label=labels[first['node_id']],later_meeting_label=labels[second['node_id']],
        added=[],changed=[],cancelled=[],unchanged=[],unaligned=unaligned,
        pending=dict(earlier=first['pending_count'],later=second['pending_count'],label='待审，不计入对比'))
    def replacement_chain(new,old):
        current=new['record_id'];target=old['record_id'];chain=[];visited=set()
        for _ in range(limits.max_hops):
            if current in visited:return []
            visited.add(current)
            found=[e for e in snapshot.edges if e.edge_type=='supersedes' and e.source.key==current and not e.blocker]
            if len(found)!=1:return []
            chain.append(_edge_proof(found[0]));current=found[0].target.key
            if current==target:return chain
        return []
    for key in sorted(set(views[0])|set(views[1])):
        before,after=views[0].get(key,[]),views[1].get(key,[])
        if len(before)>1 or len(after)>1:
            result['unaligned'].append(dict(key=key,before=before,after=after,reason='CONFLICTING_CONFIRMED_IDENTITIES'));continue
        if not before:result['added']+=after;continue
        if not after:result['cancelled']+=before;continue
        a,b=before[0],after[0]
        if kind=='decision_record':
            if a['record_id']==b['record_id']:result['unchanged'].append(b)
            else:
                chain=replacement_chain(b,a)
                if chain:result['changed'].append(dict(before=a,after=b,supersedes_chain=chain))
                else:result['unaligned'].append(dict(key=key,before=a,after=b,reason='CONFIRMED_SUPERSEDES_CHAIN_REQUIRED'))
        else:
            left,right=a['current'],b['current']
            if left['fields']==right['fields'] and left['status']==right['status']:result['unchanged'].append(b)
            elif right['status']=='cancelled':result['cancelled'].append(dict(before=a,after=b,events=right['event_ids']))
            else:result['changed'].append(dict(before=a,after=b,events=right['event_ids']))
    return result


def meeting_graph_query(store,subgoals,as_of,workspace,*,outputs,limits=None,now=None):
    limits=limits or GraphLimits();snapshot=store.load_edges(workspace,None,EDGE_TYPES)
    plan=MeetingGraphPlan.from_dict(dict(steps=subgoals,outputs=outputs,as_of=as_of),nodes=snapshot.nodes,limits=limits)
    started=monotonic();now=(now or datetime.now(UTC)).isoformat();by_node={n.node_id:n for n in snapshot.nodes}
    records={r['node_id']:r for r in snapshot.records};results=[];prior={};ancestors={};hop_depth={}
    for step in plan.steps:
        when=step['as_of'] or plan.as_of or now;inp=step['input'];parent=prior.get(inp.get('step'))
        row=dict(id=step['id'],status='FOUND',output_nodes=[],edges=[],records=[],evidence=[],pending_count=0,effective_as_of=when)
        hop_depth[step['id']]=hop_depth.get(inp.get('step'),0)+int(step['operation']=='traverse')
        if hop_depth[step['id']]>limits.max_hops:row.update(status='LIMIT_EXCEEDED',reason_code='HOP_LIMIT_EXCEEDED')
        elif parent is not None and parent['status']!='FOUND':row.update(status='BLOCKED',reason_code='PREMISE_NOT_SUPPORTED')
        elif step['operation']!='resolve' and any('entity_mention' in previous['input'] and prior.get(previous['id'],{}).get('status') not in {None,'FOUND'} for previous in plan.steps):row.update(status='BLOCKED',reason_code='ROOT_MENTION_NOT_SUPPORTED')
        elif step.get('other_step') and prior[step['other_step']]['status']!='FOUND':row.update(status='BLOCKED',reason_code='PREMISE_NOT_SUPPORTED')
        else:
            inputs=([] if 'entity' in inp or 'entity_mention' in inp else [r['node_id'] for r in snapshot.records if r['kind']=='meeting' and date_matches(r['meeting_date'],inp['meeting_date'])] if 'meeting_date' in inp else
                list(inp['candidates']) if 'candidates' in inp else [inp['node']] if 'node' in inp else [n['node_id'] for n in parent['output_nodes']])
            if step['operation']=='resolve':
                if 'entity_mention' in inp:
                    from linkloom.temporal_graph.entity_locator import resolve_mention_row
                    row=resolve_mention_row(store,snapshot,inp['entity_mention'],when,row,by_node,records,limits)
                    results.append(row);prior[row['id']]=row;continue
                if 'entity' in inp:
                    lookup=getattr(store,'unconfirmed_context',None)
                    row['unconfirmed_context']=lookup(workspace,inp['entity']['text']) if callable(lookup) else dict(count=0,evidence=[],candidate_refs=[])
                    row['evidence']+=row['unconfirmed_context']['evidence']
                    row['requested_entity']=deepcopy(inp['entity'])
                had_temporal_record=False
                for node_id in inputs:
                    record=records.get(node_id)
                    if record is None:
                        incident=[e for e in snapshot.edges if node_id in {e.source.node_id,e.target.node_id}]
                        had_temporal_record=had_temporal_record or bool(incident)
                        active=[e for e in incident if e.time_match(when)=='VALID']
                        uncertain=[e for e in incident if e.time_match(when)=='PRECISION_MISMATCH']
                        if uncertain:
                            row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT')
                            row['output_nodes'].append(by_node[node_id].to_dict())
                            row['evidence'] += [w.to_dict() for e in uncertain for w in e.evidence]
                        elif active:
                            row['output_nodes'].append(by_node[node_id].to_dict())
                            row['evidence'] += [w.to_dict() for e in active for w in e.evidence]
                        continue
                    had_temporal_record=True
                    if not _valid(record,when):continue
                    selected=_selected(record,when)
                    if selected is None:
                        row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT')
                        row['output_nodes'].append(by_node[node_id].to_dict())
                        if record['kind']=='task':
                            row.setdefault('candidates',[]).extend(deepcopy(_task_states(record,when)))
                            row['evidence'] += [e for state in _task_states(record,when) for e in state['evidence']]
                        else:row['evidence']+=_proof(record)
                        continue
                    row['output_nodes'].append(by_node[node_id].to_dict());row['records'].append(selected);row['evidence']+=_proof(selected)
                if row['status']!='CONFLICT':
                    row['status']='AMBIGUOUS' if len(row['output_nodes'])>1 else 'FOUND' if row['output_nodes'] else 'NO_VALID_RECORD_AT_TIME' if had_temporal_record else 'NOT_FOUND'
                    row['reason_code']='ROOT_NEEDS_CLARIFICATION' if row['status']=='AMBIGUOUS' else 'NO_CONFIRMED_ROOT' if row['status']=='NOT_FOUND' else None
            elif step['operation']=='compare':
                other_id=step.get('other')
                if 'other_step' in step:
                    other_nodes=prior[step['other_step']]['output_nodes']
                    if len(other_nodes)!=1 or len(inputs)!=1:raise ValueError('TWO_UNIQUE_MEETINGS_REQUIRED')
                    other_id=other_nodes[0]['node_id']
                if len(inputs)!=1 or records.get(inputs[0],{}).get('kind')!='meeting' or records.get(other_id,{}).get('kind')!='meeting':raise ValueError('TWO_MEETINGS_REQUIRED')
                if _meetings_exceed_as_of((records[inputs[0]],records[other_id]),when):
                    row.update(status='NO_VALID_RECORD_AT_TIME',reason_code='COMPARISON_EXCEEDS_AS_OF')
                    results.append(row);prior[row['id']]=row;continue
                if 'previous_anchor' in step:
                    prior_check=nearest_previous_meeting(records,step['previous_anchor'])
                    expected=other_id if inputs[0]==step['previous_anchor'] else inputs[0]
                    if _meetings_exceed_as_of((records[key] for key in prior_check['candidates']),when):
                        row.update(status='NO_VALID_RECORD_AT_TIME',reason_code='COMPARISON_EXCEEDS_AS_OF')
                        results.append(row);prior[row['id']]=row;continue
                    row['baseline_verification']=dict(prior_check,scope='workspace',anchor=step['previous_anchor'],planned_baseline=expected)
                    if prior_check['status']!='FOUND' or prior_check['candidates']!=[expected]:
                        row.update(status=prior_check['status'] if prior_check['status']!='FOUND' else 'CONFLICT',reason_code=prior_check['reason'] or 'PREVIOUS_MEETING_CHANGED')
                        row['output_nodes']=[by_node[key].to_dict() for key in prior_check['candidates']]
                        row['evidence']=[p for key in prior_check['candidates'] for p in _proof(records[key])]
                        results.append(row);prior[row['id']]=row;continue
                row['comparison']=compare_meetings(snapshot,inputs[0],other_id,step['domain'],limits=limits,as_of=when)
                workspace_baseline='previous_anchor' in step or any(prior.get(key,{}).get('root_resolution',{}).get('baseline_scope')=='workspace' for key in (inp.get('step'),step.get('other_step')))
                if workspace_baseline and 'status' not in row['comparison']:row['comparison']['baseline_scope']='workspace'
                if 'status' in row['comparison']:row.update(status=row['comparison']['status'],reason_code=row['comparison']['reason'])
                def collect_proof(value):
                    if isinstance(value,dict):
                        if 'evidence' in value and isinstance(value['evidence'],list):row['evidence'].extend(value['evidence'])
                        for key,item in value.items():
                            if key!='evidence':collect_proof(item)
                    elif isinstance(value,list):
                        for item in value:collect_proof(item)
                collect_proof(row['comparison'])
                row['evidence']+=_proof(records[inputs[0]])+_proof(records[other_id])
            elif step['operation']=='inspect_history':
                # Historical provenance does not require an old value to remain current.
                selected_ids=set(inputs);frontier=set(inputs);history_edges={}
                if any(n not in records or records[n]['kind']!='decision_record' for n in inputs):raise ValueError('DECISION_HISTORY_REQUIRED')
                history_depth=0
                for distance in range(limits.max_hops-hop_depth[step['id']]):
                    following=set()
                    for edge in snapshot.edges:
                        if edge.edge_type!='supersedes' or edge.time_match(when)!='VALID':continue
                        pair={edge.source.node_id,edge.target.node_id}
                        if not pair&frontier:continue
                        history_edges[edge.edge_id]=edge.to_dict()
                        if edge.blocker:row.update(status='CONFLICT',reason_code='CONFIRMED_COUNTER_EVIDENCE')
                        following|=pair-selected_ids
                    if not following:break
                    selected_ids|=following;frontier=following;history_depth=distance+1
                hop_depth[step['id']]+=history_depth
                if any(edge.edge_type=='supersedes' and edge.time_match(when)=='VALID' and {edge.source.node_id,edge.target.node_id}&frontier and not {edge.source.node_id,edge.target.node_id}<=selected_ids for edge in snapshot.edges):row.update(status='LIMIT_EXCEEDED',reason_code='HOP_LIMIT_EXCEEDED')
                if len(selected_ids)>limits.max_fanout:raise ValueError('HISTORY_FANOUT_LIMIT')
                row['edges']=list(history_edges.values())
                for node_id in sorted(selected_ids,key=lambda n:records[n]['valid_from']):
                    record=records[node_id]
                    if interval(record['valid_from'])[0]>interval(when)[0]:continue
                    selected=_selected(record,when)
                    if selected is None:row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT');continue
                    selected['current_view']='decision_history'
                    row['output_nodes'].append(by_node[node_id].to_dict());row['records'].append(selected);row['evidence']+=_proof(selected)
                if not row['records'] and row['status']=='FOUND':row['status']='NO_VALID_RECORD_AT_TIME'
            elif step['operation']=='inspect':
                inherited={r['node_id']:r for r in parent.get('records',[])} if parent and step['as_of'] is None else {}
                for node_id in inputs:
                    previous=inherited.get(node_id)
                    if previous and previous.get('current_view')=='meeting_source_assignment':
                        selected=deepcopy(previous);row['output_nodes'].append(by_node[node_id].to_dict());row['records'].append(selected);row['evidence']+=_proof(selected);continue
                    record=records.get(node_id)
                    if record is None:row.update(status='NOT_FOUND');continue
                    if not _valid(record,when):row.update(status='NO_VALID_RECORD_AT_TIME');continue
                    selected=_selected(record,when)
                    if selected is None:
                        row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT')
                        row['output_nodes'].append(by_node[node_id].to_dict())
                        candidates=_task_states(record,when) if record['kind']=='task' else [record]
                        row.setdefault('candidates',[]).extend(deepcopy(candidates))
                        row['evidence'] += [e for candidate in candidates for e in candidate.get('evidence',[])]
                        continue
                    row['output_nodes'].append(by_node[node_id].to_dict());row['records'].append(selected);row['evidence']+=_proof(selected)
            else:
                matches=[];precision=[];had_record=False
                future_meeting=any(n in records and records[n]['kind']=='meeting' and not _valid(records[n],when) for n in inputs)
                if future_meeting:row.update(status='NO_VALID_RECORD_AT_TIME',reason_code='MEETING_NOT_YET_VALID')
                for edge in ([] if future_meeting else snapshot.edges):
                    if edge.edge_type!=step['edge_type']:continue
                    start,target=(edge.source,edge.target) if step['direction']=='out' else (edge.target,edge.source)
                    if start.node_id not in inputs or step.get('target_kind') and target.kind!=step['target_kind']:continue
                    had_record=True;valid=edge.time_match(when)
                    if valid=='PRECISION_MISMATCH':
                        precision.append(edge);row.update(status='CONFLICT',reason_code='TIME_PRECISION_REQUIRES_SCOPE')
                    elif valid=='VALID':matches.append((target,edge))
                has_blocker=any(e.blocker for _,e in matches)
                if step['selection']=='latest' and matches and not has_blocker:
                    stamp=max(interval(e.valid_from)[0] for _,e in matches if e.valid_from)
                    matches=[(n,e) for n,e in matches if e.valid_from and interval(e.valid_from)[0]==stamp]
                unique={n.node_id:n for n,_ in matches}
                if has_blocker:row.update(status='CONFLICT',reason_code='CONFIRMED_COUNTER_EVIDENCE')
                elif step['cardinality']=='single' and len(unique)>1:row.update(status='CONFLICT',reason_code='MULTIPLE_CONFIRMED_VALUES')
                elif not matches and step['cardinality']=='single' and row['status']=='FOUND':row.update(status='NO_VALID_RECORD_AT_TIME' if had_record else 'NOT_FOUND')
                if len(unique)>limits.max_nodes:row.update(status='LIMIT_EXCEEDED')
                row['edges']=[e.to_dict() for _,e in matches]+[e.to_dict() for e in precision]
                seen=set(inputs)|ancestors.get(inp.get('step'),set())
                if set(unique)&seen:row.update(status='LIMIT_EXCEEDED',reason_code='CYCLE_DETECTED')
                ancestors[step['id']]=seen|set(unique)
                if row['status']=='FOUND':
                    ordered=sorted(unique,key=lambda n:_source_order(n,records,row['edges']))
                    row['output_nodes']=[unique[n].to_dict() for n in ordered]
                    row['records']=[]
                    for node_id in ordered:
                        if node_id not in records:continue
                        record=_selected(records[node_id],when)
                        if record is None:
                            row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT');continue
                        if record['kind']=='task' and step['edge_type']=='assigned_in' and step['direction']=='in':
                            refs={e.confirmation_ref for n,e in matches if n.node_id==node_id}
                            states=[s for s in records[node_id]['states'] if s['confirmation_ref'] in refs and interval(s['valid_from'])[0]<=interval(when)[0]]
                            record['assigned_states']=deepcopy(states)
                            if len(states)==1:
                                record=_selected(records[node_id],states[0]['valid_from'])
                                if record is None:row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT');continue
                                record['assigned_states']=deepcopy(states)
                                record['meeting_context_date']=states[0]['valid_from']
                                record['current']=deepcopy(states[0]);record['status']=states[0]['status'];record['current_view']='meeting_source_assignment'
                                if states[0].get('human_context_fields'):
                                    record['current']['post_review_changes']=deepcopy(states[0]['fields'])
                                    record['current']['fields']=deepcopy(states[0]['source_assignment'])
                                    record['current']['source_view_note']='按会议原安排；审核时的人工改写另有标记'
                                    record['current_view']='meeting_source_assignment'
                                record['states']=[deepcopy(s) for s in records[node_id]['states'] if interval(s['valid_from'])[0]<=interval(states[0]['valid_from'])[0]]
                        row['records'].append(record)
                for node_id in inputs:
                    if node_id in records:row['pending_count']+=records[node_id].get('pending_count',0)
            row['evidence']+= [e for edge in row['edges'] for e in edge['evidence']]
        results.append(row);prior[row['id']]=row
    selected=[]
    for selector in plan.outputs:
        row=prior[selector['step']];field=selector['field'];values=[]
        if row['status']=='FOUND':
            if field=='node':values=row['output_nodes']
            elif field=='record':values=row['records']
            elif field=='comparison':values=[row['comparison']] if 'comparison' in row else []
            elif field in {'valid_from','valid_to'}:
                values=list(dict.fromkeys(e[field] for e in row['edges']))
                if not row['edges']:
                    values=list(dict.fromkeys(r[field] if field in r else r['current'][field] for r in row['records'] if field in r or r.get('kind')=='task' and field in r.get('current',{})))
                if next(s for s in plan.steps if s['id']==row['id']).get('cardinality')=='single' and len(values)>1:row['status']='CONFLICT';values=[]
            elif field=='history':values=[r['states'] for r in row['records'] if r['kind']=='task']
            else:
                for record in row['records']:
                    if field in {'owner','deadline','status'} and record['kind']=='task':
                        current=record if record.get('current_view')=='meeting_source_assignment' else _selected(record,row['effective_as_of'])
                        if current is None:row['status']='CONFLICT';continue
                        value=current['current']['status'] if field=='status' else current['current']['fields'].get(field)
                        if field=='deadline' and isinstance(value,dict):value=value.get('deadline')
                        values.append(value)
                    elif field in record:values.append(record[field])
            selected.append(dict(**selector,values=values,evidence_ids=sorted({e['evidence_id'] for e in row['evidence']})))
    statuses={r['status'] for r in results}
    status='LIMIT_EXCEEDED' if 'LIMIT_EXCEEDED' in statuses else 'CONFLICT' if 'CONFLICT' in statuses else 'AMBIGUOUS' if 'AMBIGUOUS' in statuses else 'FOUND' if statuses=={'FOUND'} else 'PARTIAL' if 'FOUND' in statuses else 'NO_VALID_RECORD_AT_TIME' if 'NO_VALID_RECORD_AT_TIME' in statuses else next(iter(statuses)) if len(statuses)==1 else 'NOT_FOUND'
    root_failures=[prior[s['id']]['status'] for s in plan.steps if 'entity_mention' in s['input'] and prior[s['id']]['status']!='FOUND']
    if root_failures:
        selected=[]
        status=next((code for code in ('LIMIT_EXCEEDED','CONFLICT','AMBIGUOUS','BLOCKED','NO_VALID_RECORD_AT_TIME','NOT_FOUND') if code in root_failures),'NOT_FOUND')
    evidence={e['evidence_id']:e for row in results for e in row['evidence']}
    if sum(len(e['quote'].encode()) for e in evidence.values())>limits.max_evidence_bytes:raise ValueError('GRAPH_EVIDENCE_LIMIT')
    return dict(schema_version='graph-query-result/v1',workspace_id=workspace,status=status,steps=results,outputs=selected,
        plan=plan.to_dict(),plan_sha256=digest(plan.to_dict()),elapsed_ms=(monotonic()-started)*1000,query_profile='meeting')
