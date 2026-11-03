"""Offline scoring only. Never imported by planners/providers/fixture confirmers.

D assertions below transcribe the externally confirmed free-form expected answers
into typed checks before live execution; neither source text nor frozen Gold is
rewritten. Relation error truth is generated from public fixture declarations,
not from the graph projection or its outputs.
"""
from copy import deepcopy
import json,re


def norm(value):return ' '.join(str(value).casefold().split())
def triple(subject,kind,target):return (norm(subject),kind,norm(target))
def walk(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from walk(item)
    elif isinstance(value,list):
        for item in value:yield from walk(item)


class MeetingDevScorer:
    def __init__(self,fixtures,index):
        self.fixtures=deepcopy(fixtures);self.index=deepcopy(index)
        meeting=fixtures['graph-dev-meetings']
        self.decision_ids={v:k for k,v in index['meetings']['decisions'].items()}
        self.task_ids={v:k for k,v in index['meetings']['tasks'].items()}
        self.decisions={d['id']:d for d in meeting['confirmed_decisions']}
        self.tasks={t['id']:t for t in meeting['confirmed_tasks']}
        self.meetings={m['id']:m for m in meeting['meetings']}
        self.truth=set()
        for edge in fixtures['meridian']['confirmed_edges']:self.truth.add(triple(edge['from'],edge['type'],edge['to']))
        labels={}
        for identifier,item in self.decisions.items():
            source,line=item['evidence'].split('#')
            labels[identifier]=meeting['topic']+': '+self.meetings[source]['lines'][int(line)-1]
            self.truth.add(triple(labels[identifier],'decided_in',self.meetings[source]['name']))
        for identifier,item in self.decisions.items():
            if item.get('supersedes'):self.truth.add(triple(labels[identifier],'supersedes',labels[item['supersedes']]))
        for task in self.tasks.values():
            for state in task['history']:
                self.truth.add(triple(state['owner'],'owner_of',task['title']))
                self.truth.add(triple(task['title'],'assigned_in',self.meetings[state['meeting']]['name']))
            if task.get('source_decision'):self.truth.add(triple(task['title'],'derived_from',labels[task['source_decision']]))
        # Bound-dev legacy assertions are still checked against the same public
        # source facts by the existing grounded-claim parser, independently of Gold.
    def relation_errors(self,actual,public):
        observed={triple(r['subject'],r['edge_type'],r['target']) for r in actual['relations']}
        truth=set(self.truth)
        return len(observed-truth)
    def bound_truth(self,actual,documents):
        from linkloom.agents.claim_grounding import _claim_facts
        allowed=set()
        for doc in documents:
            for claim in _claim_facts(doc['content']) or ():
                subject,kind,target=claim.subject,claim.relation,claim.value
                if kind=='owner_of':subject,target=target,subject
                elif kind=='superseded_by':subject,target=target,subject;kind='supersedes'
                elif kind=='direct_leader_of':kind='reports_to'
                if kind in {'owner_of','supersedes','reports_to','alias_of'}:allowed.add(triple(subject,kind,target))
        observed={triple(r['subject'],r['edge_type'],r['target']) for r in actual['relations']}
        return len(observed-allowed)
    def __call__(self,case_id,actual,expected,public,documents):
        if not isinstance(actual.get('relations'),list):raise ValueError('STRUCTURED_RELATION_METRICS_REQUIRED')
        errors=self.bound_truth(actual,documents) if public['fixture_id'].startswith('DEV-') else self.relation_errors(actual,public)
        status=actual['status'];normal='UNKNOWN' if status in {'NOT_FOUND','NO_VALID_RECORD_AT_TIME'} else status
        checks={}
        if case_id.startswith('DEV-'):
            checks['outcome']=actual.get('outcome','refused')==expected['outcome']
            checks['values']=set(map(norm,expected['values']))<=set(actual.get('values',[]))
            checks['evidence']=set(expected['required_sources'])<=set(actual['evidence'])
            if expected.get('reason'):checks['reason']=actual.get('reason')==expected['reason']
            answerable=expected['outcome']=='complete'
        elif case_id.startswith('B'):
            checks['status']=normal==expected['status']
            bag=norm(json.dumps([actual.get('values'),actual.get('answer'),actual.get('relations')],ensure_ascii=False))
            checks['values']=all(norm(value) in bag for value in expected.get('values',{}).values())
            checks['evidence']=bool(actual['evidence']) if expected['status']=='FOUND' else True
            answerable=expected['status']=='FOUND'
        else:
            checks['status']=normal==expected['status'];answerable=expected['status']=='FOUND'
            checks.update(self.d_checks(case_id,actual))
            if answerable:checks['evidence']=bool(actual['evidence'])
        checks['relations']=errors==0
        return dict(correct=all(checks.values()),refused=status!='FOUND',answerable=answerable,error_relations=errors,checks=checks)
    def d_checks(self,key,actual):
        outputs=actual.get('structured_outputs',[]);steps=actual.get('result_steps',[])
        selected=[o for output in outputs for o in output['values']]
        selected_dicts=list(walk(selected))
        selected_records=[r for r in selected_dicts if r.get('kind') in {'decision_record','task'}]
        all_records=[r for row in steps for r in row.get('records',[])]
        def did(record):return self.decision_ids.get(record.get('record_id'))
        def tid(record):return self.task_ids.get(record.get('task_id'))
        def dset(records):return {did(r) for r in records if did(r)}
        def tset(records):return {tid(r) for r in records if tid(r)}
        def tasks(values):
            result=set()
            for record in values:
                if record.get('kind')!='task':continue
                state=record.get('current',{});fields=state.get('fields',{});deadline=fields.get('deadline') or {}
                if isinstance(deadline,dict):deadline=deadline.get('deadline')
                result.add((tid(record),fields.get('owner'),deadline))
            return result
        def exact_tasks(expected):return tasks(selected_records)==set(expected)
        def fields(owner=None,deadline=None):
            values=[v for o in outputs for v in o['values'] if isinstance(v,str)]
            tuples=tasks(selected_records)
            return (owner is None or owner in values or any(t[1]==owner for t in tuples)) and (deadline is None or deadline in values or any(t[2]==deadline for t in tuples))
        def history(owner,deadline,when):
            histories=[s for o in outputs if o['field']=='history' for h in o['values'] for s in h]
            return any(s.get('fields',{}).get('owner')==owner and s['fields'].get('deadline',{}).get('deadline')==deadline and s['valid_from'].startswith(when) for s in histories)
        def diffs():return [v for o in outputs if o['field']=='comparison' for v in o['values']]
        def chain(new,old):
            return any(self.decision_ids.get(e['source']['key'])==new and self.decision_ids.get(e['target']['key'])==old for row in steps for e in row['edges'] if e['edge_type']=='supersedes') or any(self.decision_ids.get(x.get('supersedes_id'))==old and did(x)==new for x in selected_records)
        if key=='D01':return dict(decisions=dset(selected_records)=={'D1','D2'},superseded=any(did(r)=='D1' and r.get('status')=='SUPERSEDED' for r in selected_records))
        if key=='D02':
            required={('T1','李明','2026-10-15'),('T2','王芳','2026-10-12'),('T3','李明','2026-10-12')}
            allowed=set()
            for identifier in {'T1','T2','T3'}:
                for state in self.tasks[identifier]['history']:
                    month,day=map(int,state['deadline'].split('/'))
                    year=self.meetings[state['meeting']]['date'][:4]
                    allowed.add((identifier,state['owner'],f'{year}-{month:02d}-{day:02d}'))
            observed=tasks(selected_records)
            return dict(tasks=required<=observed<=allowed)

        if key=='D03':return dict(comparison=any(dset(d['added'])=={'D4'} and dset(d['unchanged'])=={'D2'} and len(d['changed'])==1 and did(d['changed'][0]['before'])=='D1' and did(d['changed'][0]['after'])=='D3' and bool(d['changed'][0].get('supersedes_chain')) and not d['cancelled'] and not d['unaligned'] for d in diffs()))
        if key=='D04':return dict(comparison=any(tasks(d['added'])=={('T4','周宁','2026-10-20'),('T5','赵敏','2026-10-18')} and len(d['changed'])==1 and tasks([d['changed'][0]['before']])=={('T3','李明','2026-10-12')} and tasks([d['changed'][0]['after']])=={('T3','王芳','2026-10-16')} and bool(d['changed'][0].get('events')) and not d['cancelled'] and not d['unaligned'] for d in diffs()))
        if key=='D05':return dict(current='D3' in dset(selected_records) or any(self.decision_ids.get(r.get('key'))=='D3' for r in selected_dicts),meeting=any(r.get('meeting_date')=='2026-10-10' for r in selected_dicts) or any(r.get('label')==self.meetings['M2']['name'] for r in selected_dicts),replacement=chain('D3','D1'))
        if key=='D06':return dict(tasks=exact_tasks([('T4','周宁','2026-10-20')]))
        if key=='D07':return dict(current=fields('王芳','2026-10-16'),change=history('李明','2026-10-12','2026-10-08') and history('王芳','2026-10-16','2026-10-10'))
        if key=='D08':return dict(current=fields('李明','2026-10-12'))
        if key=='D09':return dict(tasks=exact_tasks([('T2','王芳','2026-10-17'),('T3','王芳','2026-10-16')]))
        if key=='D10':return dict(current=fields(deadline='2026-10-17'),change=history('王芳','2026-10-12','2026-10-08') and history('王芳','2026-10-17','2026-10-14'))
        if key=='D11':return dict(channels=any(did(r)=='D2' and r.get('valid_to') is None for r in selected_records))
        if key=='D12':return dict(decisions=dset(selected_records)=={'D5'},changes=any(tid(r)=='T2' and ('T2','王芳','2026-10-17') in tasks([r]) for r in selected_records),unconfirmed_excluded=all('对账看板' not in str(r) for r in selected_records))
        if key=='D13':return dict(no_positive_facts=not actual['relations'] and not selected,proposal_explained='提议' in (actual.get('answer') or ''))
        if key=='D14':return dict(current=fields('李明','2026-10-15'),warning=any('已被' in r.get('source_decision_warning','') and '确认' in r.get('source_decision_warning','') and r.get('source_decision',{}).get('replacement') for r in selected_records))
        if key=='D15':return dict(candidates=tasks(all_records)=={('T1','李明','2026-10-15'),('T4','周宁','2026-10-20')})
        if key=='D16':return dict(no_positive_facts=not actual['relations'] and not selected)
        raise ValueError('UNKNOWN_CONFIRMED_QUESTION:'+key)
