"""Expected-only acceptance extensions; original scorer remains byte-identical.

No question text, live outputs or fixture entities enter the assertion compiler.
Record aliases/IDs are explicit author-supplied assertion data, never code cases.
"""
from copy import deepcopy
from datetime import date
from graph_acceptance_v2 import AcceptanceEvaluator as FrozenEvaluator
from holdout_graph_acceptance import compile_expected as frozen_compile,history_rule,transition,QUOTED
from graph_acceptance_v2 import norm,month_day,walk
from linkloom.temporal_graph.models import interval,parse_time


def text_rule(before,after,bindings):
    matches=[b for b in bindings if norm(before) in {norm(v) for v in b['before_aliases']} and norm(after) in {norm(v) for v in b['after_aliases']}]
    if len(matches)!=1:raise ValueError('EXPLICIT_UNIQUE_TEXT_TRANSITION_BINDING_REQUIRED')
    binding=matches[0]
    return dict(op='decision_text_comparison',expected=dict(before=before,after=after,before_id=binding['before_id'],after_id=binding['after_id'],require_confirmed_supersedes_chain=True,require_same_subject_key=True,require_both_evidence=True))


def compile_expected(expected,*,bindings=(),overrides=()):
    """Only expected plus pre-execution author clarifications; never an output."""
    source=deepcopy(expected);extra=None
    if 'decision_changes' in source:
        value=source['decision_changes']
        try:transition(value)
        except ValueError:
            phrases=QUOTED.findall(value)
            if len(phrases)!=2 or '由' not in value or '改为' not in value:raise ValueError('EXPLICIT_TEXT_TRANSITION_REQUIRED')
            extra=text_rule(*phrases,bindings);source.pop('decision_changes')
    if 'changes' in source:
        try:history_rule(source['changes'])
        except ValueError:
            if source['changes'].count('顺延到')!=1:raise
            source['changes']=source['changes'].replace('顺延到','改为')
            history_rule(source['changes'])
    rules=frozen_compile(source)
    if extra:rules.insert(len(rules)-1,extra)
    for override in overrides:
        positions=[i for i,r in enumerate(rules) if r['op']==override['replace_op']]
        if len(positions)!=1:raise ValueError('UNIQUE_AUTHOR_OVERRIDE_TARGET_REQUIRED')
        replacement=deepcopy(override['replacement'])
        if (override['replace_op'],replacement['op']) not in {('answer','decision_text_comparison'),('reason_text','reason_text')}:raise ValueError('UNAUTHORIZED_OVERRIDE_PAIR')
        if replacement['op']=='decision_text_comparison':
            e=replacement['expected'];compiled=text_rule(e['before'],e['after'],bindings)
            if any(e[k]!=compiled['expected'][k] for k in ('before_id','after_id')):raise ValueError('AUTHOR_TRANSITION_BINDING_CONFLICT')
            for flag in ('require_confirmed_supersedes_chain','require_same_subject_key','require_both_evidence'):
                if e.get(flag) is not True:raise ValueError('STRICT_TRANSITION_FLAGS_REQUIRED')
            if e.get('changed_on'):month_day(e['changed_on'])
        elif replacement['op']=='reason_text':
            if source['status']!='UNKNOWN' or not isinstance(replacement['expected'],list) or not replacement['expected'] or not all(isinstance(x,str) and x for x in replacement['expected']):raise ValueError('EXPLICIT_UNKNOWN_REASON_REQUIRED')
        else:raise ValueError('UNSUPPORTED_AUTHOR_OVERRIDE')
        rules[positions[0]]=replacement
    return rules


def edge_proof(edge):
    """Lossless DTO renaming only; preserve the original edge hash and evidence."""
    proof=deepcopy(edge)
    if proof.get('schema_version')=='graph-edge-proof/v1':return proof
    if 'schema_version' in proof or not all(side in proof for side in ('source','target')):return None
    for side in ('source','target'):
        reference=proof.pop(side)
        if not isinstance(reference,dict) or 'kind' not in reference:return None
        reference['node_type']=reference.pop('kind');proof[side+'_ref']=reference
    return dict(schema_version='graph-edge-proof/v1',**proof)


class AcceptanceEvaluator(FrozenEvaluator):
    """Adds text transitions; delegates every other assertion to frozen v2."""
    def native_record(self,record,identifier,when=None):
        truth=self.index.get('decision_records',{}).get(identifier)
        if not (truth and self.decision_id(record)==identifier and self.record_valid(record)
            and truth.get('subject_key') and all(record.get(k)==truth.get(k) for k in ('record_id','subject_key','valid_from','valid_to','supersedes_id'))):return False
        status=truth.get('status')
        if when:
            left,right=interval(when);start=interval(truth['valid_from'])[0];end=interval(truth['valid_to'])[0] if truth.get('valid_to') else None
            if type(parse_time(when)) is date and (left<start<right or end is not None and left<end<right):return False
            if end is not None and left<end:status='ACTIVE'
        return record.get('status')==status

    def pair_valid(self,before,after,expected,when=None,before_when=None):
        a,b=expected['before_id'],expected['after_id']
        if not self.native_record(before,a,before_when or when) or not self.native_record(after,b,when):return False
        if before['subject_key']!=after['subject_key']:return False
        if when and any(r['valid_from'][:10]>when[:10] for r in (before,after)):return False
        if expected.get('changed_on'):
            meeting=self.meetings[self.decisions[b]['meeting']]
            if month_day(after['valid_from'])!=month_day(expected['changed_on']) or month_day(meeting['date'])!=month_day(expected['changed_on']):return False
        return (self.witnessed(before.get('evidence',[]),self.decisions[a]['evidence'])
            and self.witnessed(after.get('evidence',[]),self.decisions[b]['evidence']))

    def transition_valid(self,actual,expected,rules):
        if not all(expected.get(k) is True for k in ('require_confirmed_supersedes_chain','require_same_subject_key','require_both_evidence')):return False
        if expected.get('before_id') not in self.decisions or expected.get('after_id') not in self.decisions:return False
        bindings=[b for b in self.index.get('text_transition_bindings',[]) if b['before_id']==expected['before_id'] and b['after_id']==expected['after_id'] and norm(expected['before']) in {norm(v) for v in b['before_aliases']} and norm(expected['after']) in {norm(v) for v in b['after_aliases']}]
        if len(bindings)!=1:return False
        cited=[e for step in actual.get('result_steps',[]) for e in step.get('evidence',[])]
        if not all(self.witnessed(cited,self.decisions[key]['evidence']) for key in (expected['before_id'],expected['after_id'])):return False
        outputs=actual.get('structured_outputs',[]);steps=actual.get('result_steps',[])
        diffs=[d for o in outputs if o['field']=='comparison' for d in o['values']]
        if diffs:
            required={'decision_record'}
            if any(r['op'] in {'task_comparison','exact_added_tasks'} for r in rules):required.add('task')
            if len(diffs)!=len(required) or len({(d.get('earlier_meeting'),d.get('later_meeting')) for d in diffs})!=1:return False
            if any(sum(self.comparison_domain(d)==kind for d in diffs)!=1 for kind in required):return False
            diff=next(d for d in diffs if self.comparison_domain(d)=='decision_record')
            if diff['added'] or diff['cancelled'] or diff['unaligned'] or len(diff['changed'])!=1:return False
            item=diff['changed'][0];a,b=item['before'],item['after']
            dates=self.index.get('meeting_dates',{})
            early,late=dates.get(diff.get('earlier_meeting')),dates.get(diff.get('later_meeting'))
            if not early or not late or early>=late:return False
            if not a.get('valid_from','')[:10]<=early or a.get('valid_to') and early>=a['valid_to'][:10]:return False
            if not b.get('valid_from','')[:10]<=late or b.get('valid_to') and late>=b['valid_to'][:10]:return False
            return self.pair_valid(a,b,expected,late,before_when=early) and self.chain_valid(item.get('supersedes_chain'),a,b)
        # History must expose both authoritative records and its own real edges
        # in one selected step. Do not borrow facts or chains from the fixture.
        record_outputs=[o for o in outputs if o['field']=='record']
        all_selected=[r for o in outputs for r in walk(o['values']) if r.get('kind') in {'task','decision_record'}]
        target={expected['before_id'],expected['after_id']}
        if any(r.get('kind')!='decision_record' or self.decision_id(r) not in self.decisions for r in all_selected):return False
        for output in record_outputs:
            rows=[s for s in steps if s['id']==output['step']]
            if len(rows)!=1:continue
            row=rows[0];selected=[r for r in output['values'] if isinstance(r,dict) and r.get('kind')=='decision_record']
            selected_ids={self.decision_id(r) for r in selected}
            if not target<=selected_ids or len(selected)!=len(selected_ids) or {self.decision_id(r) for r in all_selected}!=selected_ids:continue
            if any(not self.native_record(r,self.decision_id(r),row.get('effective_as_of')) for r in selected):continue
            a=next(r for r in selected if self.decision_id(r)==expected['before_id']);b=next(r for r in selected if self.decision_id(r)==expected['after_id'])
            if not row.get('effective_as_of') or not self.pair_valid(a,b,expected,row['effective_as_of']):continue
            if not any(r==a for r in row.get('records',[])) or not any(r==b for r in row.get('records',[])):continue
            proofs=[p for edge in row.get('edges',[]) if (p:=edge_proof(edge)) is not None and p.get('edge_type')=='supersedes']
            def paths(current,chain,seen):
                if current==a['record_id']:return [chain]
                if len(chain)>=3 or current in seen:return []
                return [path for p in proofs if p.get('source_ref',{}).get('key')==current for path in paths(p.get('target_ref',{}).get('key'),chain+[p],seen|{current})]
            valid=[chain for chain in paths(b['record_id'],[],set()) if self.chain_valid(chain,a,b)]
            if len(valid)==1:
                chain_ids={self.decision_ids.get(p[side]['key']) for p in valid[0] for side in ('source_ref','target_ref')}
                if selected_ids==chain_ids and all(r['subject_key']==a['subject_key'] and r in row.get('records',[]) for r in selected):return True
        return False

    def __call__(self,actual,rules):
        bridge=deepcopy(rules)
        for rule in bridge:
            if rule['op']=='decision_text_comparison':
                # Tell the frozen scorer the expected comparison domain. The
                # placeholder check itself is replaced only by the stricter,
                # independently checked text transition; other checks stay intact.
                rule.update(op='decision_comparison',expected=dict(before='__TEXT_TRANSITION__',after='__TEXT_TRANSITION__'))
        result=super().__call__(actual,bridge);checks={}
        for i,rule in enumerate(rules):
            if rule['op']=='decision_text_comparison':ok=self.transition_valid(actual,rule['expected'],rules)
            else:ok=result['checks'][str(i)+':'+rule['op']]
            checks[str(i)+':'+rule['op']]=bool(ok)
        return dict(result,checks=checks,correct=all(checks.values()))
