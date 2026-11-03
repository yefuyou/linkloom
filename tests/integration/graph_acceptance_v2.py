"""Version 2: expected-only acceptance; multi-task scalars bind per Task without weakening single values.

compile_expected accepts only an expected object. It cannot see questions,
fixtures, provider outputs, or previous system answers. The evaluator is invoked
only after the frozen three-repeat execution has completed.
"""
from copy import deepcopy
from collections import Counter
from datetime import date
import re,unicodedata

DATE=re.compile(r'(?:(?<!\d)\d{4}[-/])?(\d{1,2})\s*(?:[-/]|月)\s*(\d{1,2})\s*日?')
QUOTED=re.compile(r"['‘“]([^'’”]+)['’”]")


def month_day(value):
    values=dates(str(value))
    if len(values)!=1:raise ValueError('ONE_LITERAL_MONTH_DAY_REQUIRED:'+str(value))
    return values[0]


def dates(value):
    result=[]
    for m in DATE.finditer(str(value)):
        month,day=map(int,m.groups())
        try:date(2000,month,day)
        except ValueError:continue
        result.append(f'{month}/{day}')
    return result


def norm(value):
    text=unicodedata.normalize('NFKC',str(value)).casefold()
    def replace(m):
        try:date(2000,int(m[1]),int(m[2]))
        except ValueError:return m[0]
        return f'{int(m[1])}/{int(m[2])}'
    text=DATE.sub(replace,text).replace('这版','本版')
    return ''.join(text.split()).translate(str.maketrans('', '', "'\"‘’“”"))


def tuple_rule(value):
    pieces=[p.strip() for p in value.split('·')]
    if len(pieces) not in {2,3} or any(not p for p in pieces):raise ValueError('EXPLICIT_TASK_TUPLE_REQUIRED')
    result=dict(title=pieces[0])
    if len(pieces)==3:result.update(owner=pieces[1],deadline=month_day(pieces[2]))
    elif dates(pieces[1]):result['deadline']=month_day(pieces[1])
    else:result['owner']=pieces[1]
    return result


def task_change(value):
    parts=re.split('[:：]',value,maxsplit=1)
    if len(parts)!=2:raise ValueError('NAMED_TASK_CHANGE_REQUIRED')
    title,body=parts;rule=dict(title=title.strip());before,after=body.split('→',1)
    if '截止' in before:
        rule.update(before_deadline=month_day(before),after_deadline=month_day(after))
    else:
        rule.update(before_owner=before.strip(),after_owner=re.split('[，,；;]',after,maxsplit=1)[0].strip())
        remaining=re.split('[，,；;]',after,maxsplit=1)
        if len(remaining)==2 and '截止不变' in remaining[1]:rule['same_deadline']=month_day(remaining[1])
        else:raise ValueError('EXPLICIT_CHANGE_FIELDS_REQUIRED')
    return rule


def transition(value):
    old=re.search(r'从\s*('+DATE.pattern+r')',value)
    new=re.search(r'(?:推迟到|改为|改到|变为)\s*('+DATE.pattern+r')',value)
    if old is None or new is None:raise ValueError('EXPLICIT_DECISION_TRANSITION_REQUIRED')
    return dict(before=month_day(old[1]),after=month_day(new[1]))


def history_rule(value):
    before=re.search(r'原定\s*('+DATE.pattern+r')',value)
    after=re.search(r'改为\s*('+DATE.pattern+r')',value)
    owner=re.search(r'负责人([^，,；;]+?)不变',value)
    if before is None or after is None:raise ValueError('EXPLICIT_HISTORY_CHANGE_REQUIRED')
    preceding=dates(value[:after.start()]);rule=dict(before_deadline=month_day(before[1]),after_deadline=month_day(after[1]))
    if len(preceding)<2:raise ValueError('CHANGE_MEETING_DATE_REQUIRED')
    rule['changed_on']=preceding[-1]
    if owner:rule['same_owner']=owner[1].strip()
    return rule


def answer_rule(value):
    if len(dates(value))==1 and norm(value)==norm(dates(value)[0]):return dict(kind='date_value',value=month_day(value))
    if value.startswith('改了'):
        if '决定' not in value or '替代' not in value:raise ValueError('EXPLICIT_REPLACEMENT_ANSWER_REQUIRED')
        claim=value.split('决定',1)[1].split('替代',1)[0].strip('，,；; ')
        quoted=QUOTED.findall(value)
        if len(quoted)!=1 or len(dates(value))!=1:raise ValueError('EXPLICIT_REPLACEMENT_TARGET_REQUIRED')
        return dict(kind='replacement_answer',new_claim=claim,old_claim=quoted[0],meeting_date=dates(value)[0])
    return dict(kind='text_value',value=value)


def mention_rule(value):
    tokens=QUOTED.findall(value)+re.findall('[（(]([^）)]+)[）)]',value)+dates(value)
    if '已被' in value and '替代' in value:tokens+=['已被','替代']
    for clause in re.split('[，,；;。]',value):
        if '未被取消' in clause:
            tokens.append(clause.removeprefix('但').replace('本身','').strip())
        if '没有取消记录' in clause:tokens.append('没有取消记录')
    if not tokens:tokens=[value]
    return dict(required_text=list(dict.fromkeys(tokens)),require_source_replacement='已被' in value and '替代' in value)


def compile_expected(expected):
    if not isinstance(expected,dict) or expected.get('status') not in {'FOUND','UNKNOWN','AMBIGUOUS'}:raise ValueError('EXPLICIT_STATUS_REQUIRED')
    rules=[dict(op='status',expected=expected['status'])]
    if expected['status']=='FOUND':rules.append(dict(op='selected_evidence',expected=True))
    if expected['status']=='UNKNOWN':rules.append(dict(op='no_positive_facts',expected=True))
    singles={k:expected[k] for k in ('owner','deadline') if k in expected}
    if singles:
        if 'deadline' in singles:singles['deadline']=month_day(singles['deadline'])
        rules.append(dict(op='single_task_values',expected=singles))
    for key,value in expected.items():
        if key in {'status','owner','deadline'}:continue
        if key=='decisions':
            targets=[]
            for item in value:
                identifier,description=item.split(maxsplit=1);entry=dict(id=identifier,original_claim=description.split('（',1)[0].strip())
                if '已被' in item:
                    suffix=item.split('已被',1)[1];entry['superseded_on']=dates(suffix)[0]
                targets.append(entry)
            rules.append(dict(op='exact_decisions',expected=targets))
        elif key in {'tasks','tasks_as_assigned_in_meeting'}:rules.append(dict(op='exact_tasks',expected=[tuple_rule(x) for x in value]))
        elif key=='candidates':rules.append(dict(op='exact_candidates',expected=[tuple_rule(x) for x in value]))
        elif key=='decision_changes':rules.append(dict(op='decision_comparison',expected=transition(value)))
        elif key=='task_changes':rules.append(dict(op='task_comparison',expected=[task_change(x) for x in value]))
        elif key=='added':rules.append(dict(op='exact_added_tasks',expected=[tuple_rule(x) for x in value]))
        elif key=='answer':
            entry=answer_rule(value);entry['allow_replaced_context']='replaced' in expected or entry['kind']=='replacement_answer'
            rules.append(dict(op='answer',expected=entry))
        elif key=='source':rules.append(dict(op='source_meeting',expected=value))
        elif key=='replaced':rules.append(dict(op='replaced_value',expected=answer_rule(value)))
        elif key=='changes':rules.append(dict(op='task_history',expected=history_rule(value)))
        elif key=='must_mention':rules.append(dict(op='must_mention',expected=mention_rule(value),original=value))
        elif key=='reason':
            if expected['status']=='UNKNOWN':
                rules.append(dict(op='reason_text',expected=['提议'] if '提议' in value else [value],original=value))
            elif expected['status']=='AMBIGUOUS':rules.append(dict(op='no_arbitrary_selection',expected=True,original=value))
            else:raise ValueError('REASON_STATUS_NOT_SUPPORTED')
        else:raise ValueError('UNSUPPORTED_EXPECTED_FIELD:'+key)
    rules.append(dict(op='record_and_relation_consistency',expected=True))
    return rules


def walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)


class AcceptanceEvaluator:
    """Uses fixed assertions; fixture/index data is only independent evidence truth."""
    def __init__(self,fixture,index):
        self.fixture=deepcopy(fixture);self.index=deepcopy(index)
        self.meetings={m['id']:m for m in fixture['meetings']}
        self.decisions={d['id']:d for d in fixture['confirmed_decisions']}
        self.tasks={t['id']:t for t in fixture['confirmed_tasks']}
        self.decision_ids={v:k for k,v in index['decisions'].items()};self.task_ids={v:k for k,v in index['tasks'].items()}
        self.lines={(m['id'],i+1):line for m in fixture['meetings'] for i,line in enumerate(m['lines'])}
        self.truth=set();labels={}
        for key,item in self.decisions.items():
            meeting,line=item['evidence'].split('#');labels[key]=fixture['topic']+': '+self.lines[(meeting,int(line))]
            self.truth.add(self.triple(labels[key],'decided_in',self.meetings[meeting]['name']))
        for key,item in self.decisions.items():
            if item.get('supersedes'):self.truth.add(self.triple(labels[key],'supersedes',labels[item['supersedes']]))
        for task in self.tasks.values():
            for state in task['history']:
                self.truth.add(self.triple(state['owner'],'owner_of',task['title']))
                self.truth.add(self.triple(task['title'],'assigned_in',self.meetings[state['meeting']]['name']))
            if task.get('source_decision'):self.truth.add(self.triple(task['title'],'derived_from',labels[task['source_decision']]))

    @staticmethod
    def triple(subject,kind,target):return (norm(subject),kind,norm(target))
    def decision_id(self,r):return self.decision_ids.get(r.get('record_id'))
    def task_id(self,r):return self.task_ids.get(r.get('task_id'))
    def did_value(self,identifier):
        item=self.decisions[identifier];meeting,line=item['evidence'].split('#');return self.lines[(meeting,int(line))]
    def state_tuple(self,r):
        fields=r.get('current',{}).get('fields',{});deadline=fields.get('deadline') or {}
        if isinstance(deadline,dict):deadline=deadline.get('deadline')
        return dict(title=r.get('title'),owner=fields.get('owner'),deadline=month_day(deadline) if deadline else None)
    def evidence_valid(self,proof):
        import hashlib
        if proof.get('workspace_id')!=self.index['workspace_id']:return False
        quote=proof.get('quote','')
        if hashlib.sha256(quote.encode()).hexdigest()!=proof.get('quote_sha256'):return False
        if proof.get('line_start')!=proof.get('line_end'):return False
        for meeting in self.fixture['meetings']:
            line=proof.get('line_start',0)
            if not 1<=line<=len(meeting['lines']):continue
            content='\n'.join(meeting['lines'])
            source=self.index['sources'][meeting['id']]
            if (meeting['lines'][line-1]==quote and hashlib.sha256(content.encode()).hexdigest()==proof.get('content_sha256')
                and proof.get('source_id')==source['source_id'] and proof.get('source_version_id')==source['source_version_id']):return True
        return False
    def witnessed(self,evidence,reference):
        meeting,line=reference.split('#');quote=self.lines[(meeting,int(line))]
        source=self.index['sources'][meeting]
        return any(e.get('quote')==quote and e.get('source_id')==source['source_id'] and self.evidence_valid(e) for e in evidence)
    def record_valid(self,r):
        if r.get('kind')=='decision_record':
            identifier=self.decision_id(r)
            return identifier in self.decisions and r.get('value')==self.did_value(identifier) and bool(r.get('evidence')) and all(self.evidence_valid(e) for e in r['evidence']) and self.witnessed(r['evidence'],self.decisions[identifier]['evidence'])
        if r.get('kind')=='task':
            identifier=self.task_id(r);given=self.tasks.get(identifier)
            if given is None or r.get('title')!=given['title']:return False
            allowed={(state['owner'],month_day(state['deadline'])) for state in given['history']}
            states=r.get('states',[])+([r['current']] if r.get('current') else [])
            if not states:return False
            for state in states:
                fields=state.get('fields',{});deadline=fields.get('deadline') or {}
                if isinstance(deadline,dict):deadline=deadline.get('deadline')
                if fields.get('title')!=given['title'] or not deadline or (fields.get('owner'),month_day(deadline)) not in allowed:return False
                if not state.get('evidence') or not all(self.evidence_valid(e) for e in state['evidence']):return False
                compatible=[entry for entry in given['history'] if entry['owner']==fields.get('owner') and month_day(entry['deadline'])==month_day(deadline) and state.get('valid_from','')[:10]==self.meetings[entry['meeting']]['date']]
                if not any(self.witnessed(state['evidence'],entry['evidence']) for entry in compatible):return False
            parent=r.get('source_decision');declared=given.get('source_decision')
            if declared:
                if r.get('source_decision_id')!=self.index['decisions'][declared] or not parent:return False
                if parent.get('record_id')!=self.index['decisions'][declared] or parent.get('value')!=self.did_value(declared):return False
                if not self.witnessed(parent.get('evidence',[]),self.decisions[declared]['evidence']):return False
                if parent.get('replacement') and not self.source_replacement_valid(r):return False
            elif parent or r.get('source_decision_id'):return False
            return True
        return True
    def exact_tasks(self,records,rules):
        tasks=[r for r in records if r.get('kind')=='task']
        fields=set().union(*(set(r) for r in rules)) if rules else {'title'}
        observed={tuple((k,norm(self.state_tuple(r).get(k))) for k in sorted(fields)) for r in tasks}
        expected={tuple((k,norm(r.get(k))) for k in sorted(fields)) for r in rules}
        return observed==expected and len({self.task_id(r) for r in tasks})==len(rules)
    def replacement(self,r,old_value):
        identifier=self.decision_id(r)
        if identifier not in self.decisions:return False
        old=self.decisions[identifier].get('supersedes')
        return old is not None and r.get('supersedes_id')==self.index['decisions'][old] and old_value in norm(self.did_value(old))
    def source_replacement_valid(self,r):
        task=self.tasks.get(self.task_id(r));old=task.get('source_decision') if task else None
        parent=r.get('source_decision') or {}
        if old not in self.decisions or parent.get('record_id')!=self.index['decisions'][old]:return False
        successors=[key for key,item in self.decisions.items() if item.get('supersedes')==old]
        if len(successors)!=1:return False
        new=successors[0];replacement=parent.get('replacement') or {};evidence=parent.get('evidence',[])
        return (parent.get('value')==self.did_value(old) and replacement.get('value')==self.did_value(new)
            and (replacement.get('valid_from') or '')[:10]==self.decisions[new]['valid_from']
            and (parent.get('valid_to') or '')[:10]==self.decisions[new]['valid_from']
            and all(self.evidence_valid(e) for e in evidence)
            and self.witnessed(evidence,self.decisions[old]['evidence']) and self.witnessed(evidence,self.decisions[new]['evidence']))
    def chain_valid(self,chain,before,after):
        from linkloom.temporal_graph.models import digest
        if not isinstance(chain,list) or not 1<=len(chain)<=3:return False
        current=after.get('record_id');target=before.get('record_id');seen=set()
        for proof in chain:
            if current in seen:return False
            seen.add(current)
            if proof.get('schema_version')!='graph-edge-proof/v1' or proof.get('edge_type')!='supersedes' or proof.get('blocker'):return False
            source=proof.get('source_ref') or {};end=proof.get('target_ref') or {}
            if source.get('key')!=current or source.get('node_type')!='decision_record' or end.get('node_type')!='decision_record':return False
            new=self.decision_ids.get(source.get('key'));old=self.decision_ids.get(end.get('key'))
            if new not in self.decisions or self.decisions[new].get('supersedes')!=old:return False
            if source.get('workspace_id')!=self.index['workspace_id'] or end.get('workspace_id')!=self.index['workspace_id']:return False
            if proof.get('confirmation_ref')!=self.index['decision_receipts'].get(new) or proof.get('source_record_id')!=current:return False
            if proof.get('valid_from','')[:10]!=self.decisions[new]['valid_from']:return False
            evidence=proof.get('evidence',[])
            if not all(self.evidence_valid(e) for e in evidence) or not self.witnessed(evidence,self.decisions[new]['evidence']) or not self.witnessed(evidence,self.decisions[old]['evidence']):return False
            canonical={k:deepcopy(v) for k,v in proof.items() if k not in {'schema_version','edge_id','source_ref','target_ref'}}
            for name,reference in [('source',source),('target',end)]:
                reference=deepcopy(reference);reference['kind']=reference.pop('node_type');canonical[name]=reference
            if proof.get('edge_id')!='edge_'+digest(canonical)[:32]:return False
            current=end['key']
        return current==target
    def source_matches(self,record,meeting):
        if record.get('kind')=='decision_record':
            item=self.decisions.get(self.decision_id(record))
            return bool(item and item['meeting']==meeting and self.witnessed(record.get('evidence',[]),item['evidence']))
        if record.get('kind')=='task':
            item=self.tasks.get(self.task_id(record));state=record.get('current',{})
            return bool(item and any(entry['meeting']==meeting and state.get('valid_from','')[:10]==self.meetings[meeting]['date'] and self.witnessed(state.get('evidence',[]),entry['evidence']) for entry in item['history']))
        return False
    def comparison_domain(self,diff):
        kinds=set()
        for group in ('added','changed','cancelled','unchanged'):
            for item in diff[group]:
                if 'before' in item:kinds.add(item['before'].get('kind'));kinds.add(item['after'].get('kind'))
                else:kinds.add(item.get('kind'))
        return next(iter(kinds)) if len(kinds)==1 else None
    def task_change_matches(self,item,rule):
        if not item.get('events') or 'before' not in item or 'after' not in item:return False
        if self.task_id(item['before']) is None or self.task_id(item['before'])!=self.task_id(item['after']):return False
        task_id=item['before'].get('task_id')
        if not all(self.index['task_events'].get(event)==task_id for event in item['events']):return False
        a,b=self.state_tuple(item['before']),self.state_tuple(item['after'])
        if a['title']!=rule['title'] or b['title']!=rule['title']:return False
        for side,state in [('before',a),('after',b)]:
            for field in ('owner','deadline'):
                if side+'_'+field in rule and norm(state[field])!=norm(rule[side+'_'+field]):return False
        return 'same_deadline' not in rule or a['deadline']==b['deadline']==rule['same_deadline']

    def __call__(self,actual,rules):
        outputs=actual.get('structured_outputs',[]);steps=actual.get('result_steps',[])
        selected=[v for o in outputs for v in o['values']]
        records=[r for r in walk(selected) if r.get('kind') in {'task','decision_record'}]
        all_records=[r for step in steps for r in step.get('records',[])]
        normal='UNKNOWN' if actual['status'] in {'NOT_FOUND','NO_VALID_RECORD_AT_TIME'} else actual['status']
        observed={self.triple(r['subject'],r['edge_type'],r['target']) for r in actual.get('relations',[])}
        errors=len(observed-self.truth);checks={}
        task_records=[r for r in records if r.get('kind')=='task']
        # List scalars bind to all verified records of their own step, with multiplicity.
        bound_tasks=list(task_records);scalar_bindings_valid=True
        for output in outputs:
            if output['field'] not in {'owner','deadline'}:continue
            rows=[step for step in steps if step['id']==output['step']]
            tasks=[r for row in rows for r in row.get('records',[]) if r.get('kind')=='task']
            identifiers={self.task_id(r) for r in tasks}
            valid=(len(rows)==1 and bool(identifiers) and None not in identifiers and len(identifiers)==len(tasks) and all(self.record_valid(r) for r in tasks))
            values=Counter(norm(self.state_tuple(r).get(output['field'])) for r in tasks)
            chosen=Counter(norm(month_day(v) if output['field']=='deadline' else v) for v in output['values'])
            # An unlabelled scalar cannot contradict the selected snapshot of that Task.
            coherent=all(norm(self.state_tuple(source).get(output['field']))==norm(self.state_tuple(target).get(output['field']))
                for source in tasks for target in task_records if self.task_id(source)==self.task_id(target))
            scalar_bindings_valid=scalar_bindings_valid and valid and values==chosen and bool(chosen) and coherent
            if valid:bound_tasks.extend(tasks)
        decision_records=[r for r in records if r.get('kind')=='decision_record']
        diffs=[d for o in outputs if o['field']=='comparison' for d in o['values']]
        task_diffs=[d for d in diffs if self.comparison_domain(d)=='task'];decision_diffs=[d for d in diffs if self.comparison_domain(d)=='decision_record']
        required_domains=set()
        if any(r['op']=='decision_comparison' for r in rules):required_domains.add('decision_record')
        if any(r['op'] in {'task_comparison','exact_added_tasks'} for r in rules):required_domains.add('task')
        comparisons_bound=(not required_domains or len(diffs)==len(required_domains) and len({(d.get('earlier_meeting'),d.get('later_meeting')) for d in diffs})==1 and all(sum(self.comparison_domain(d)==kind for d in diffs)==1 for kind in required_domains))
        if comparisons_bound and required_domains:
            allowed={r.get('record_id') or r.get('task_id') for d in diffs for r in walk(d) if r.get('kind') in {'task','decision_record'}}
            comparisons_bound=all((r.get('record_id') or r.get('task_id')) in allowed for r in records)
        evidence=[e for step in steps for e in step.get('evidence',[])];valid_evidence=bool(evidence) and all(self.evidence_valid(e) for e in evidence)
        answer=norm(actual.get('answer') or '')
        source_names=norm(' '.join(m['name'] for m in self.fixture['meetings'] if any(e.get('quote') in m['lines'] for e in evidence)))
        histories=[];history_bindings_valid=True
        for output in outputs:
            if output['field']!='history':continue
            owner_rows=[row for row in steps if row['id']==output['step']]
            bound=[r for row in owner_rows for r in row.get('records',[]) if r.get('kind')=='task' and self.record_valid(r)]
            for history in output['values']:
                matches=[r for r in bound if r.get('states')==history]
                identifiers={self.task_id(r) for r in matches}
                valid=(len(owner_rows)==1 and len(identifiers)==1 and None not in identifiers and (not bound_tasks or identifiers=={self.task_id(r) for r in bound_tasks}))
                history_bindings_valid=history_bindings_valid and valid
                if valid:histories.append(history)
        for index,rule in enumerate(rules):
            op=rule['op'];expected=rule['expected'];ok=False
            if op=='status':ok=normal==expected
            elif op=='selected_evidence':ok=valid_evidence and bool(actual.get('evidence'))
            elif op=='no_positive_facts':ok=not observed and not selected
            elif op=='record_and_relation_consistency':ok=errors==0 and scalar_bindings_valid and history_bindings_valid and all(self.record_valid(r) for r in all_records+records)
            elif op=='reason_text':ok=all(norm(t) in answer for t in expected)
            elif op=='no_arbitrary_selection':ok=not selected and not observed
            elif op=='single_task_values':
                ids={self.task_id(r) for r in bound_tasks}
                ok=scalar_bindings_valid and len(ids)==1 and None not in ids and all(all(norm(self.state_tuple(r).get(k))==norm(v) for k,v in expected.items()) for r in bound_tasks)
                ok=ok and valid_evidence
            elif op=='exact_decisions':
                ok={self.decision_id(r) for r in decision_records}=={x['id'] for x in expected} and not task_records
                for entry in expected:ok=ok and any(self.decision_id(r)==entry['id'] and norm(entry['original_claim']) in norm(r.get('value','')) for r in decision_records)
                for entry in expected:
                    if 'superseded_on' in entry:ok=ok and any(self.decision_id(r)==entry['id'] and r.get('status')=='SUPERSEDED' and r.get('valid_to') and month_day(r['valid_to'])==entry['superseded_on'] for r in decision_records)
            elif op=='exact_tasks':ok=self.exact_tasks(task_records,expected) and not any(isinstance(v,dict) and v.get('kind')=='decision_record' for v in selected)
            elif op=='exact_candidates':
                candidates=[r for step in steps if step['status']=='AMBIGUOUS' for r in step.get('records',[]) if r.get('kind')=='task']
                ok=self.exact_tasks(candidates,expected) and all(self.record_valid(r) for r in candidates)
            elif op=='source_meeting':
                meetings=[key for key,m in self.meetings.items() if norm(m['name'])==norm(expected)]
                targets=[r for r in decision_records if r.get('status')=='ACTIVE'] or task_records
                ok=len(meetings)==1 and bool(targets) and all(self.source_matches(r,meetings[0]) for r in targets)
            elif op=='answer':
                active=[r for r in decision_records if r.get('status')=='ACTIVE']
                active_ids={self.decision_id(r) for r in active}
                allowed=set(active_ids)
                if expected.get('allow_replaced_context'):
                    for identifier in list(active_ids):
                        if identifier in self.decisions and self.decisions[identifier].get('supersedes'):allowed.add(self.decisions[identifier]['supersedes'])
                coherent=len(active_ids)==1 and None not in active_ids and {self.decision_id(r) for r in decision_records}<=allowed
                if expected['kind']=='date_value':ok=coherent and all(expected['value'] in dates(r.get('value','')) for r in active) and valid_evidence
                elif expected['kind']=='text_value':ok=norm(expected['value']) in answer and valid_evidence
                else:
                    ok=coherent and all(norm(expected['new_claim']) in norm(r.get('value','')) and self.replacement(r,norm(expected['old_claim'])) for r in active)
                    ok=ok and expected['meeting_date'] in dates(source_names) and valid_evidence
            elif op=='replaced_value':
                value=expected['value']
                ok=any(self.replacement(r,norm(value)) for r in decision_records)
            elif op=='must_mention':
                ok=all(norm(t) in answer for t in expected['required_text'])
                if expected['require_source_replacement']:ok=ok and len({self.task_id(r) for r in task_records})==1 and bool(task_records) and all(self.source_replacement_valid(r) for r in task_records)
            elif op=='task_history':
                ok=False
                for history in histories:
                    for a in history:
                        for b in history:
                            af,bf=a.get('fields',{}),b.get('fields',{})
                            ad,bd=af.get('deadline') or {},bf.get('deadline') or {}
                            if isinstance(ad,dict):ad=ad.get('deadline')
                            if isinstance(bd,dict):bd=bd.get('deadline')
                            if not ad or not bd:continue
                            if month_day(ad)!=expected['before_deadline'] or month_day(bd)!=expected['after_deadline'] or month_day(b['valid_from'])!=expected['changed_on']:continue
                            if 'same_owner' in expected and not af.get('owner')==bf.get('owner')==expected['same_owner']:continue
                            if a['valid_from']>=b['valid_from']:continue
                            ok=True
            elif op=='decision_comparison':
                for diff in decision_diffs:
                    if diff['added'] or diff['cancelled'] or diff['unaligned'] or len(diff['changed'])!=1:continue
                    item=diff['changed'][0];a,b=item['before'],item['after']
                    ok=comparisons_bound and self.chain_valid(item.get('supersedes_chain'),a,b) and expected['before'] in dates(a.get('value','')) and expected['after'] in dates(b.get('value','')) and self.replacement(b,norm(expected['before']))
            elif op=='task_comparison':
                ok=comparisons_bound and any(not diff['cancelled'] and not diff['unaligned'] and len(diff['changed'])==len(expected) and all(any(self.task_change_matches(item,entry) for item in diff['changed']) for entry in expected) for diff in task_diffs)
            elif op=='exact_added_tasks':ok=comparisons_bound and any(self.exact_tasks(diff['added'],expected) for diff in task_diffs)
            else:raise ValueError('UNKNOWN_FROZEN_ASSERTION:'+op)
            checks[str(index)+':'+op]=bool(ok)
        return dict(correct=all(checks.values()),checks=checks,error_relations=errors,refused=actual['status']!='FOUND',answerable=next(r['expected'] for r in rules if r['op']=='status')=='FOUND')
