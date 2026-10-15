"""Answer-blind offline controller exercising the existing production Runtime.

This is a FakeModel demonstration, not a claim of real-model generalization.
Run artifacts (including the existing Tool Ledger) remain under ignored work/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from linkloom.agents.claim_grounding import assess_claim_text, ClaimSupportStatus, verified_subgoal_conflict
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.runtime.task_plan import TaskPlan, verified_plan_subgoal_values, assess_task_plan, verified_dependency_entities
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


class SourceOnlyController:
    """Fixed tool policy; receives only Runtime instructions and tool observations."""
    def __init__(self):
        self.slot = 0
        self.pending = []
        self.reads = {}
        self.values = {}
        self.claims = []
        self.searched = False
        self.started = time.monotonic()

    def __call__(self, request):
        if time.monotonic() - self.started > 30:
            raise ValidationError('Offline investigation deadline exceeded')
        packet = json.loads(request.user_input.split('team_decision_request=', 1)[1])
        plan = TaskPlan.from_dict(packet['task_plan'])
        goal = plan.subgoals[self.slot]
        self.reads.setdefault(goal.subgoal_id, [])
        observation = request.observation
        if observation is not None:
            if observation.status != 'ok':
                raise ValidationError('Offline controller stops on tool failure')
            if observation.tool_id == 'search_notes':
                self.pending = [item['evidence_id'] for item in observation.value]
            elif observation.tool_id == 'read_verified_note':
                self.reads[goal.subgoal_id].append(observation.value)
        def tool(name, arguments):
            return ModelAction.tool(ToolCall(call_id=f'mh_{request.sequence}', tool_id=name, arguments=arguments))
        if self.pending:
            return tool('read_verified_note', {'note_ref': self.pending.pop(0)})
        if not self.searched:
            self.searched = True
            dependencies = {parent: self.values[parent] for parent in goal.depends_on}
            return tool('search_notes', {'query': goal.question,
                'source_context': {**packet['source_context'], 'subgoal_id': goal.subgoal_id,
                                   'dependency_entities': dependencies}, 'limit': 4})
        evidence = {row.subgoal_id: tuple(self.reads.get(row.subgoal_id, [])) for row in plan.subgoals}
        targets = verified_plan_subgoal_values(plan, goal.subgoal_id, evidence)
        if len(targets or ()) == 1:
            target = targets[0]
            literal = next(re.search(re.escape(target), item['quote'], re.I).group()
                           for item in evidence[goal.subgoal_id] if re.search(re.escape(target), item['quote'], re.I))
            self.values[goal.subgoal_id] = literal
            subject = (self.values[goal.depends_on[0]] if goal.depends_on else
                       re.search(r'(?:Who owns |Which project does )(.+?)(?: alias)?[?]$', goal.question)[1])
            predicate = goal.relation_path[0]
            text = {'alias_of_project': f'{subject} is the team alias for {literal}',
                    'owner_of_project': f'{literal} is the accountable owner of {subject}',
                    'reports_to': f'{subject} reports directly to {literal}'}[predicate]
            date = re.search(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', goal.question)
            if date:
                text = f'As of {date.group()} ' + text
            by_ref = {item['evidence_id']: item for item in evidence[goal.subgoal_id]}
            assessment = assess_claim_text(text, list(by_ref), by_ref)
            if assessment.status != ClaimSupportStatus.SUPPORTED:
                raise ValidationError('Source binding does not ground a claim')
            refs = list(dict.fromkeys(ref for claim in assessment.claims for ref in claim.supporting_evidence_refs))
            self.claims.append({'point': text, 'evidence_refs': refs})
            if self.slot + 1 < len(plan.subgoals):
                self.slot += 1
                self.searched = False
                new_goal = plan.subgoals[self.slot]
                self.searched = True
                return tool('search_notes', {'query': new_goal.question, 'source_context': {
                    **packet['source_context'], 'subgoal_id': new_goal.subgoal_id,
                    'dependency_entities': {parent: self.values[parent] for parent in new_goal.depends_on}},
                    'limit': 4})
        complete = len(self.claims) == len(plan.subgoals)
        bound_question = goal.question.replace('{verified_entity}', next(iter(self.values.values()), ''))
        conflict = verified_subgoal_conflict(bound_question, evidence[goal.subgoal_id]) if not complete else False
        missing = [item.subgoal_id for item in plan.subgoals if item.subgoal_id not in self.values]
        unknown_refs = [item['evidence_id'] for item in evidence[goal.subgoal_id]] if not complete else []
        claim_refs = list(dict.fromkeys(ref for claim in self.claims for ref in claim['evidence_refs']))
        refs = list(dict.fromkeys(claim_refs + unknown_refs))
        return ModelAction.final(json.dumps({'schema_version': 'team-decision-result/v1',
            'decision': {'value': '; '.join(item['point'] for item in self.claims) if complete else None,
                         'status': 'approved' if complete else 'insufficient_evidence',
                         'evidence_refs': claim_refs if complete else []},
            'rationale': [] if complete else self.claims, 'actions': [], 'unresolved_items': [],
            'rejected_alternatives': [], 'uncertainty': {
                'status': 'none' if complete else 'conflicting_evidence' if conflict else 'insufficient_evidence',
                'statement': None, 'unknown_fields': missing, 'evidence_refs': unknown_refs},
            'evidence_refs': refs}))


def run_case(public, root):
    """No oracle enters this function, the controller, or the Runtime input."""
    started = time.monotonic()
    plan = plan_relation_investigation(public['query'])
    if plan is None:
        return {'outcome': 'refused', 'reason': 'planning_failed', 'values': [], 'calls': [],
                'sources': [], 'verified_hops': 0, 'model_turns': 0, 'context_bytes': 0,
                'bindings_verified': True, 'citation_valid': True, 'elapsed_ms': 0}
    root = root / ('run_' + uuid4().hex[:12])
    vault = root / 'vault'; vault.mkdir(parents=True)
    for document in public['documents']:
        if document['workspace_id'] != public['workspace_id']:
            continue
        path = Path(document['path'])
        if path.name != document['path'] or path.suffix != '.md':
            raise ValueError('Fixture note path must be one relative Markdown filename')
        (vault / path).write_text(document['content'], encoding='utf-8')
    index = scan_vault(vault, root / 'scan').index_path
    model = FakeModelAdapter([SourceOnlyController()] * 20)
    engine = RuntimeEngine(vault_root=vault, index_path=index, checkpoint_dir=root/'runtime',
        trace_dir=root/'traces', checkpointer=InMemoryCheckpointer(), model=model)
    status = engine.start_multi_agent(RunRequest(request_id='bounded_mh', thread_id='bounded_mh',
        workflow='team_decision', query=public['query'], task_plan=plan.to_dict(),
        max_steps=20, max_provider_requests=20, dry_run=True))
    state = engine.checkpointer.get_latest('bounded_mh')
    records = state.tool_ledger if state else []
    calls, binding_ok, slot = [], True, None
    for record in records:
        row = {'sequence': record.sequence, 'tool': record.tool_id, 'status': record.status}
        if record.tool_id == 'search_notes':
            context = record.arguments['source_context']; slot = context['subgoal_id']
            row.update(hop=slot, dependencies=context.get('dependency_entities', {}))
            prior = [item for item in records if item.sequence < record.sequence]
            assessment = assess_task_plan(plan, prior, run_id=record.run_id, task_id=record.task_id,
                agent_id=record.agent_id, expected_workspace_id=state.source.vault_root_fingerprint)
            try:
                verified_dependency_entities(plan, assessment, slot, row['dependencies'])
            except ValidationError:
                binding_ok = False
        if record.tool_id == 'read_verified_note' and record.status == 'completed':
            value = record.result['value']
            row.update(hop=slot, source=value['relative_path'], evidence_id=value['evidence_id'],
                       content_sha256=value['content_sha256'])
        calls.append(row)
    result_path = root/'runtime'/'results'/status.run_id/'result.json'
    payload = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    final = (payload.get('result') or {}).get('team_decision')
    values, cited = [], []
    if final:
        texts = [final['decision']['value']] if final['decision']['value'] else [x['point'] for x in final['rationale']]
        by_ref = {item['evidence_id']: item for result in model.requests[-1].evidence_context
                  for item in (result.value if isinstance(result.value, list) else [result.value])}
        for text in texts:
            support = assess_claim_text(text, final['evidence_refs'], by_ref)
            values.extend(claim.value for claim in support.claims)
        cited = final['evidence_refs']
    reads = {row['evidence_id']: row['source'] for row in calls if 'evidence_id' in row}
    reason = ('conflicting_evidence' if final and final['uncertainty']['status']=='conflicting_evidence' else
              'evidence_insufficient' if final and not final['decision']['value'] else
              None if final else 'run_interrupted')
    return {'outcome': 'complete' if final and final['decision']['value'] else 'partial' if values else 'refused',
        'reason': reason, 'values': values, 'calls': calls, 'sources': [reads[ref] for ref in cited if ref in reads],
        'citation_valid': all(ref in reads for ref in cited), 'bindings_verified': binding_ok,
        'verified_hops': len(values), 'model_turns': model.call_count,
        'context_bytes': max((len(json.dumps([x.to_dict() for x in request.evidence_context]).encode())
                              for request in model.requests), default=0),
        'elapsed_ms': round((time.monotonic()-started)*1000), 'run_id': status.run_id,
        'artifact_instance': root.name,
        'run_status': status.status, 'error': status.error, 'final': final}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=Path('tests/fixtures/bounded_multihop/dev.json'))
    parser.add_argument('--output',type=Path,default=Path('work/bounded_multihop/dev'))
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    raw=args.dataset.read_bytes(); cases=json.loads(raw)['cases']; rows=[]
    for case in cases:
        public={key:value for key,value in case.items() if key not in {'expected','id','tags'}}
        actual=run_case(public,args.output/case['id'])
        oracle=case['expected']
        passed=(actual['outcome']==oracle['outcome'] and actual['values']==oracle['values']
                and actual['reason']==oracle['reason'] and actual['citation_valid'] and actual['bindings_verified']
                and set(oracle['required_sources']) <= set(actual['sources']))
        rows.append({'id':case['id'],'passed':passed,**actual})
        print(case['id'], 'PASS' if passed else 'FAIL', actual['outcome'],actual['values'],actual['reason'],flush=True)
    report={'dataset_sha256':hashlib.sha256(raw).hexdigest(),'fake_model_only':True,
            'real_provider_requests':0,'passed':sum(row['passed'] for row in rows),'total':len(rows),'cases':rows}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Task outcome accuracy:',report['passed'],'/',report['total'])
    return 0 if report['passed']==report['total'] else 1

if __name__=='__main__':
    raise SystemExit(main())
