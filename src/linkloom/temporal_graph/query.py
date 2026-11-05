"""Bounded traversal over currently authorized confirmed records, without writes."""
from datetime import UTC,datetime
from time import monotonic
from linkloom.temporal_graph.models import GraphLimits,GraphPlan,EDGE_TYPES,digest,parse_time


def graph_query(store,subgoals,as_of,workspace,*,outputs=None,limits=None,now=None):
    limits=limits or GraphLimits()
    if isinstance(subgoals,list) and any(isinstance(r,dict) and 'operation' in r for r in subgoals):
        from linkloom.temporal_graph.meeting_query import meeting_graph_query
        return meeting_graph_query(store,subgoals,as_of,workspace,outputs=outputs,limits=limits,now=now)
    if not isinstance(subgoals,list) or not subgoals: raise ValueError('SUBGOAL_LIMIT')
    snapshot=store.load_edges(workspace,None,EDGE_TYPES)
    plan=GraphPlan.from_dict(dict(steps=subgoals,outputs=outputs or [dict(step=subgoals[-1]['id'],field='node')],
                                 as_of=as_of),nodes=snapshot.nodes,limits=limits)
    now=(now or datetime.now(UTC)).isoformat(); started=monotonic()
    results=[]; by_id={}; visited={}; used=[]
    if len(snapshot.edges)>limits.max_edges:
        return dict(schema_version='graph-query-result/v1',workspace_id=workspace,status='LIMIT_EXCEEDED',steps=[],outputs=[])
    for step in plan.steps:
        inp=step['input']; parent=by_id.get(inp.get('step'))
        row=dict(id=step['id'],status='UNKNOWN',output_nodes=[],edges=[],candidates=[],reason_code=None)
        if parent is not None and parent['status']!='FOUND':
            row.update(status='BLOCKED',reason_code='PREMISE_NOT_SUPPORTED')
            results.append(row); by_id[step['id']]=row; continue
        node_id=inp.get('node') if parent is None else parent['output_nodes'][0]['node_id']
        ancestors=set(visited.get(inp.get('step'),()))|{node_id}
        date=step['as_of'] or plan.as_of or now
        matches=[]; precision=[]
        for edge in snapshot.edges:
            if edge.edge_type!=step['edge_type']: continue
            source,target=(edge.source,edge.target) if step['direction']=='out' else (edge.target,edge.source)
            if source.node_id!=node_id: continue
            when=edge.time_match(date)
            if when=='PRECISION_MISMATCH': precision.append(edge)
            if when=='VALID': matches.append((target,edge))
        blockers=[e for _,e in matches if e.blocker]
        positive=[(n,e) for n,e in matches if not e.blocker]
        candidates={n.node_id:n for n,_ in positive}
        if blockers:
            row.update(status='CONFLICT',reason_code='CONFIRMED_COUNTER_EVIDENCE',edges=[e.to_dict() for _,e in matches])
        elif precision:
            row.update(status='AMBIGUOUS',reason_code='TIME_PRECISION_REQUIRES_SCOPE',edges=[e.to_dict() for e in precision])
        elif len(candidates)>1:
            row.update(status='AMBIGUOUS',reason_code='MULTIPLE_CANDIDATES',
                       candidates=[n.to_dict() for _,n in sorted(candidates.items())],edges=[e.to_dict() for _,e in positive])
        elif len(candidates)==1:
            target=next(iter(candidates.values()))
            if target.node_id in ancestors:
                row.update(status='LIMIT_EXCEEDED',reason_code='CYCLE_DETECTED')
            else:
                row.update(status='FOUND',output_nodes=[target.to_dict()],edges=[e.to_dict() for _,e in positive])
                visited[step['id']]=ancestors|{target.node_id}
        row['effective_as_of']=date
        used.extend(row['edges']); results.append(row); by_id[step['id']]=row
    evidence_bytes=sum(len(e['quote'].encode('utf8')) for row in used for e in row['evidence'])
    statuses={r['status'] for r in results}
    if evidence_bytes>limits.max_evidence_bytes: status='LIMIT_EXCEEDED'; results=[]
    elif 'LIMIT_EXCEEDED' in statuses: status='LIMIT_EXCEEDED'
    elif 'CONFLICT' in statuses: status='CONFLICT'
    elif 'AMBIGUOUS' in statuses: status='AMBIGUOUS'
    elif statuses=={'FOUND'}: status='FOUND'
    elif 'FOUND' in statuses: status='PARTIAL'
    else: status='UNKNOWN'
    selected=[]
    if results:
        for selector in plan.outputs:
            row=by_id[selector['step']]
            if row['status']!='FOUND': continue
            field=selector['field']
            values=row['output_nodes'] if field=='node' else list(dict.fromkeys(e[field] for e in row['edges']))
            if field!='node' and len(values)!=1:
                status='AMBIGUOUS'; continue
            selected.append(dict(**selector,values=values,evidence_ids=sorted({q['evidence_id'] for e in row['edges'] for q in e['evidence']})))
    return dict(schema_version='graph-query-result/v1',workspace_id=workspace,status=status,steps=results,outputs=selected,
                plan=plan.to_dict(),plan_sha256=digest(plan.to_dict()),elapsed_ms=(monotonic()-started)*1000)
