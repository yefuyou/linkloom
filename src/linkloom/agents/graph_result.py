"""Receipt-only graph output projection and current-evidence revalidation."""
from copy import deepcopy
from datetime import datetime
from linkloom.temporal_graph.models import digest
from linkloom.temporal_graph.query import graph_query
from linkloom.temporal_graph.wire import unpack_graph_result


def review_graph_result(payload,record,store,*,run_id,task_id,limits=None):
    try:
        expected=dict(run_id=run_id,task_id=task_id,agent_id='graph-worker',call_id=record.call_id)
        if (record.run_id!=run_id or record.task_id!=task_id or record.agent_id!='graph-worker' or
            record.tool_id!='graph_query' or record.status!='completed' or record.error is not None or
            record.result.get('status')!='ok' or unpack_graph_result(record.result.get('value'))!=payload or payload['execution']!=expected):
            raise ValueError('OBSERVED_MATCHING_GRAPH_RECEIPT_REQUIRED')
        args=record.arguments
        if args['workspace']!=payload['workspace_id']: raise ValueError('WORKSPACE_MISMATCH')
        fresh=graph_query(store,args['subgoals'],args['as_of'],args['workspace'],outputs=args['outputs'],limits=limits,
            now=datetime.fromisoformat(payload['query_time']))
        old={k:v for k,v in payload.items() if k not in {'elapsed_ms','execution','query_time'}}
        new={k:v for k,v in fresh.items() if k!='elapsed_ms'}
        if digest(old)!=digest(new): raise ValueError('SOURCE_OR_AUTHORIZATION_CHANGED')
        return dict(status='pass',reason=None)
    except (ValueError,TypeError,KeyError,PermissionError,AttributeError) as error:
        return dict(status='fail',reason=str(error))


def project_graph_result(payload):
    if payload.get('query_profile')=='meeting':return project_meeting_result(payload)
    facts=[]; evidence={}; conflicts={}; goals=[]
    for row in payload['steps']:
        step=next(r for r in payload['plan']['steps'] if r['id']==row['id'])
        goals.append(dict(subgoal_id=row['id'],relation_path=[step['edge_type']]))
        for edge in row['edges']:
            refs=[]
            for item in edge['evidence']:
                ref=item['evidence_id'];refs.append(ref)
                evidence[ref]=dict(evidence_ref=ref,citation_id=ref,source_name=item['source_id'],source_id=item['source_id'],
                  source_version=item['source_version_id'],quote=item['quote'],content_sha256=item['content_sha256'],
                  quote_sha256=item['quote_sha256'],line_start=item['line_start'],line_end=item['line_end'],verified=True)
            if row['status']=='FOUND':
                facts.append(dict(step_id=row['id'],subject=edge['source']['label'],predicate=edge['edge_type'],value=edge['target']['label'],
                  valid_on=row['effective_as_of'],evidence_refs=refs,edge_id=edge['edge_id'],direction=step['direction']))
            elif row['status'] in {'CONFLICT','AMBIGUOUS'}: conflicts.setdefault(row['id'],[]).extend(refs)
    values=[]
    for item in payload['outputs']:
        values.extend(v['label'] if isinstance(v,dict) else str(v) for v in item['values'])
    answer='；'.join(values) if payload['status']=='FOUND' and values else None
    return dict(answer=answer,facts=facts,evidence_level='HUMAN_CONFIRMED_SOURCE',conflicts=conflicts,
      plan=dict(schema_version='graph-plan-view/v1',subgoals=goals),graph_query=deepcopy(payload)),list(evidence.values())


def project_meeting_result(payload):
    import json
    evidence={};facts=[]
    for row in payload['steps']:
        for item in row.get('evidence',[]):
            ref=item['evidence_id']
            evidence[ref]=dict(evidence_ref=ref,citation_id=ref,source_name=item['source_id'],source_id=item['source_id'],
                source_version=item['source_version_id'],quote=item['quote'],content_sha256=item['content_sha256'],
                quote_sha256=item['quote_sha256'],line_start=item['line_start'],line_end=item['line_end'],verified=True)
        if row['status']=='FOUND':
            for edge in row['edges']:facts.append(dict(step_id=row['id'],subject=edge['source']['label'],predicate=edge['edge_type'],
                value=edge['target']['label'],valid_on=row['effective_as_of'],evidence_refs=[e['evidence_id'] for e in edge['evidence']],edge_id=edge['edge_id']))
    selected=[dict(step=r['step'],field=r['field'],values=r['values']) for r in payload['outputs']]
    def is_human_context(state):return bool(state.get('human_context_fields') or state.get('status_basis')=='HUMAN_TASK_EVENT')
    human_context=(any(is_human_context(r.get('current',{})) for row in payload['steps'] for r in row.get('records',[])) or
        any(is_human_context(c) for row in payload['steps'] for c in row.get('candidates',[])))
    def conflict_text():
        labels=[]
        plan={s['id']:s for s in payload['plan']['steps']}
        for row in payload['steps']:
            if row['status']!='CONFLICT':continue
            labels.extend('对象：'+n['label'] for n in row['output_nodes'])
            for edge in row['edges']:
                side='target' if plan[row['id']].get('direction')=='out' else 'source'
                labels.append(('反证：' if edge['blocker'] else '候选：')+edge[side]['label'])
            for candidate in row.get('candidates',[]):
                fields=candidate.get('fields',{})
                deadline=fields.get('deadline') or {}
                if isinstance(deadline,dict):deadline=deadline.get('deadline') or deadline.get('deadline_text')
                labels.append('候选：'+' · '.join(str(v) for v in [fields.get('title') or candidate.get('value'),fields.get('owner'),deadline] if v)+('（含人工修改；原文仅作上下文）' if is_human_context(candidate) else ''))
        return '已确认记录存在冲突：'+('；'.join(dict.fromkeys(labels)) if labels else '有效时间或字段无法唯一确定')+'。请明确对象或更细的时间。'
    def missing_text():
        names={'task':'任务','decision_record':'决定','meeting':'会议','person':'人员'}
        rows=[r for r in payload['steps'] if r.get('requested_entity')]
        text='；'.join("未找到已确认的'"+r['requested_entity']['text']+"'" if r.get('requested_mention') else '未找到已确认的'+names.get(r['requested_entity']['kind'],'对象')+'“'+r['requested_entity']['text']+'”' for r in rows) or '未找到已确认的记录'
        if any(r.get('unconfirmed_context',{}).get('count',0)>0 for r in rows):text+='；当前可用来源中仅为提议、未确认，不作为正式任务或决定'
        return text+'。'
    def clarification_text():
        rows=[r for r in payload['steps'] if r['status']=='AMBIGUOUS']
        if not any(r.get('reason_code')=='CONFIRMED_CANDIDATES_NEED_CLARIFICATION' for r in rows):
            return '存在多个已确认候选，请明确所指任务：'+'；'.join(n['label'] for r in rows for n in r['output_nodes'])
        labels=[]
        for row in rows:
            confirmed={r['node_id']:r for r in row.get('records',[])}
            for node in row['output_nodes']:
                record=confirmed.get(node['node_id'],{})
                fields=record.get('current',{}).get('fields',{})
                title=fields.get('title') or record.get('value') or node['label']
                labels.append(title+' · '+fields['owner'] if fields.get('owner') else title)
        return '请确认你指的是哪一项。已确认候选：'+'；'.join(labels)
    answer=(render_meeting_outputs(payload) if payload['status']=='FOUND' else
        conflict_text() if payload['status']=='CONFLICT' else
        clarification_text() if payload['status']=='AMBIGUOUS' else
        missing_text() if payload['status']=='NOT_FOUND' else '该时间点没有有效的已确认记录。' if payload['status']=='NO_VALID_RECORD_AT_TIME' else '本地实体检索不可用，暂时无法核实所指对象。' if payload['status']=='BLOCKED' and any(r.get('reason_code')=='LOCAL_ENTITY_RETRIEVAL_UNAVAILABLE' for r in payload['steps']) else None)
    return dict(answer=answer,facts=facts,evidence_level='HUMAN_REVIEW_WITH_SOURCE_CONTEXT' if human_context else 'UNCONFIRMED_PROPOSAL_CONTEXT' if any(r.get('unconfirmed_context',{}).get('count',0)>0 for r in payload['steps']) else 'HUMAN_CONFIRMED_SOURCE',conflicts={},
        plan=dict(schema_version='meeting-graph-plan-view/v1',subgoals=[dict(subgoal_id=s['id'],relation_path=[s.get('edge_type',s['operation'])])
            for s in payload['plan']['steps']]),graph_query=deepcopy(payload),structured_outputs=selected),list(evidence.values())


def render_meeting_outputs(payload):
    def record_text(record):
        if record.get('kind')=='task':
            state=record.get('current')
            if state is None:
                states=record.get('assigned_states') or record.get('states',[])
                state=states[0] if len(states)==1 else {}
            fields=state.get('fields',{})
            deadline=fields.get('deadline') or {}
            if isinstance(deadline,dict):deadline=deadline.get('deadline') or deadline.get('deadline_text') or '截止时间未确认'
            return ' · '.join(str(v) for v in [fields.get('title') or record.get('title'),fields.get('owner') or '负责人未确认',deadline or '截止时间未确认'])+('（'+record['source_decision_warning']+'）' if record.get('source_decision_warning') else '')+('（'+state.get('source_view_note','含人工修改，原文仅作上下文')+'）' if state.get('human_context_fields') else '')
        if record.get('kind')=='decision_record':
            prefix=record['valid_from']+'：' if record.get('current_view')=='decision_history' else ''
            return prefix+str(record.get('value',''))+('（已被替代）' if record.get('status')=='SUPERSEDED' else '')
        return str(record.get('label') or record.get('value') or record.get('meeting_date') or '')
    parts=[];steps={r['id']:r for r in payload['steps']}
    for output in payload['outputs']:
        row=steps[output['step']];values=output['values'];field=output['field']
        if field=='record':
            prefix=('会议时点值：' if any(r.get('current_view')=='meeting_source_assignment' for r in values) else '决定变更历史：' if any(r.get('current_view')=='decision_history' for r in values) else '')
            parts.append(prefix+('；'.join(record_text(r) for r in values) if values else '0 条已确认'))
            if row.get('pending_count'):parts.append(str(row['pending_count'])+' 条待审，未计入已确认结果')
        elif field=='comparison':
            for diff in values:
                if diff.get('earlier_meeting_label') and diff.get('later_meeting_label'):parts.append('会议对比：'+diff['earlier_meeting_label']+' → '+diff['later_meeting_label'])
                if diff.get('baseline_scope')=='workspace':parts.append('基线：当前工作区会议目录中按业务时间确定的最近前会')
                if diff['order_swapped']:parts.append('已按会议日期交换顺序')
                for key,label in [('added','新增'),('changed','变更'),('cancelled','撤销／取消'),('unchanged','未变化')]:
                    texts=[]
                    for item in diff[key]:
                        texts.append(record_text(item['before'])+' → '+record_text(item['after']) if 'before' in item and 'after' in item else record_text(item))
                    parts.append(label+'：'+('；'.join(texts) if texts else '0 条'))
                if diff['unaligned']:parts.append('无法对齐：'+str(len(diff['unaligned']))+' 条，未猜测关联')
                if diff['pending']['earlier'] or diff['pending']['later']:parts.append('待审，不计入对比：前会 '+str(diff['pending']['earlier'])+' 条，后会 '+str(diff['pending']['later'])+' 条')
        elif field=='history':
            for history in values:
                for state in history:parts.append(state['valid_from']+'：'+record_text(dict(kind='task',current=state)))
        else:
            labels={'owner':'负责人','deadline':'截止日期','status':'状态','meeting_date':'会议日期','pending_count':'待审数'}
            for value in values:
                human=any(field in r.get('current',{}).get('human_context_fields',[]) or field=='status' and r.get('current',{}).get('status_basis')=='HUMAN_TASK_EVENT' for r in row.get('records',[]))
                parts.append((labels.get(field,'')+'：' if field in labels else '')+(str(value.get('label','')) if isinstance(value,dict) else str(value))+('（人工修改；原文仅作上下文）' if human else ''))
    warnings={r['source_decision_warning'] for row in payload['steps'] for r in row.get('records',[]) if r.get('source_decision_warning')}
    for warning in sorted(warnings):
        if not any(warning in part for part in parts):parts.append(warning)
    return '。'.join(parts)
