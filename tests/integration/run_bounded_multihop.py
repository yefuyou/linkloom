"""Answer-blind offline controller exercising the existing production Runtime.

This is a FakeModel demonstration, not a claim of real-model generalization. The
fake plays the model's part: it reads the question and the verified sources
itself, follows newly read entities with further searches, and proposes Typed
Claims (claim-proposal/v1). The Runtime receives no plan or template; the Host
checks every claim deterministically against the Tool Ledger.
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

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.checkpoint import InMemoryCheckpointer
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall

# The fake model's own reading of the synthetic fixture sentences. This stands in
# for model understanding inside a test; production code has no such table.
_READINGS = (
    (re.compile(r'(?P<o>[^.;\n]+?) is the accountable owner of (?P<s>[^.;\n]+?)(?: for [^.;\n]+)?$'), 'owner_of_project'),
    (re.compile(r'(?P<s>[^.;\n]+?) reports directly to (?P<o>[^.;\n]+?)$'), 'reports_to'),
    (re.compile(r'(?P<s>[^.;\n]+?) is the team alias for (?P<o>[^.;\n]+?)$'), 'alias_of_project'),
)
_WANTS = (
    ('reports_to', re.compile(r'lead|manager|report|直属|领导|汇报|上级', re.I)),
    ('owner_of_project', re.compile(r'\bown|负责', re.I)),
    ('alias_of_project', re.compile(r'alias|behind|别名', re.I)),
)
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _norm(value):
    return ' '.join(str(value).casefold().split())


def read_facts(passage):
    """Facts the fake model understands in one verified read, with exact quotes."""
    facts = []
    for line in passage['quote'].splitlines():
        line = re.sub(r'^\[[^\]]*\]\s*(?:[^:]{1,40}:\s*)?', '', line.strip()).lstrip('# ')
        for sentence in re.split(r'(?<=[.;])\s+', line):
            text = sentence.strip().rstrip('.;')
            date = _DATE.match(text.removeprefix('As of ')) if text.startswith('As of ') else None
            body = text[len('As of ') + 10:].strip() if date else text
            for pattern, predicate in _READINGS:
                match = pattern.match(body)
                if match and text in passage['quote']:
                    facts.append({'subject': match['s'].strip(), 'predicate': predicate,
                                  'object': match['o'].strip(), 'evidence_ids': [passage['evidence_id']],
                                  'quote': text, 'valid_on': date.group() if date else None,
                                  'temporal_kind': 'snapshot' if date else None})
    return facts


class SourceOnlyController:
    """Observation-driven fake model: search, read every hit, follow new entities, Final."""
    def __init__(self):
        self.pending = []
        self.read_ids = set()
        self.facts = []
        self.searched = []

    def __call__(self, request):
        # This injected fake is bounded by the caller's Runtime step/request caps.
        # A host scheduling or snapshot delay is not a model-policy refusal.
        packet = json.loads(request.user_input.split('team_decision_request=', 1)[1])
        observation = request.observation
        if observation is not None:
            if observation.status != 'ok':
                raise ValidationError('Offline controller stops on tool failure')
            if observation.tool_id == 'search_notes':
                self.pending.extend(item['evidence_id'] for item in observation.value
                                    if item['evidence_id'] not in self.read_ids
                                    and item['evidence_id'] not in self.pending)
            elif observation.tool_id == 'read_verified_note':
                self.read_ids.add(observation.value['evidence_id'])
                self.facts.extend(fact for fact in read_facts(observation.value) if fact not in self.facts)
        if getattr(request, 'available_tools', None) == []:
            # Exhaustion closes tools. Remaining search hits stay unread and never enter claims.
            self.pending.clear()
        def tool(name, arguments):
            return ModelAction.tool(ToolCall(call_id=f'mh_{request.sequence}', tool_id=name, arguments=arguments))
        if getattr(request, 'available_tools', None) != []:
            if self.pending:
                return tool('read_verified_note', {'note_ref': self.pending.pop(0)})
            claims, conflicted = self._chain(packet['query'])
            # A disputed entity is never used for a dependent search.
            frontier = [packet['query']] + [claim['object'] for claim in claims if (
                claim['predicate'], _norm(claim['subject']), claim['valid_on']) not in conflicted]
            query = next((item for item in frontier if item not in self.searched), None)
            if query is not None:
                self.searched.append(query)
                return tool('search_notes', {'query': query, 'source_context': packet['source_context'], 'limit': 4})
        return ModelAction.final(json.dumps(self.final(packet['query'])))

    def _chain(self, question):
        """Claims connected to the question, and the (predicate, subject) slots read as conflicting."""
        day = (_DATE.findall(question) or [None])[0]
        facts = [fact for fact in self.facts if day is None or fact['valid_on'] in {None, day}]
        # For a dated question, a fact stated for that day outranks an undated one.
        dated = {(fact['predicate'], _norm(fact['subject'])) for fact in facts if fact['valid_on']}
        facts = [fact for fact in facts if fact['valid_on'] or (fact['predicate'], _norm(fact['subject'])) not in dated]
        known = {_norm(question)}
        chain, conflicted = [], set()
        while True:
            added = [fact for fact in facts if fact not in chain
                     and (any(_norm(fact['subject']) in entity for entity in known))]
            if not added:
                return chain, conflicted
            chain.extend(added)
            for fact in added:
                slot = (fact['predicate'], _norm(fact['subject']), fact['valid_on'])
                if len({_norm(item['object']) for item in chain
                        if (item['predicate'], _norm(item['subject']), item['valid_on']) == slot}) > 1:
                    conflicted.add(slot)
            known |= {_norm(fact['object']) for fact in added
                      if (fact['predicate'], _norm(fact['subject']), fact['valid_on']) not in conflicted}

    def final(self, question):
        claims, conflicted = self._chain(question)
        wanted = [predicate for predicate, pattern in _WANTS if pattern.search(question)]
        if not wanted:
            claims, conflicted = [], set()  # Nothing read answers what was asked.
        resolved = {claim['predicate'] for claim in claims
                    if (claim['predicate'], _norm(claim['subject']), claim['valid_on']) not in conflicted}
        unknown = [predicate for predicate in wanted if predicate not in resolved] or (
            [] if wanted else ['answer'])
        payload = {'schema_version': 'claim-proposal/v1', 'claims': claims}
        if conflicted or unknown:
            payload['uncertainty'] = {
                'status': 'conflicting_evidence' if conflicted else 'insufficient_evidence',
                'unknown_fields': unknown or sorted({slot[0] for slot in conflicted})}
        return payload


def conflict_free_values(claims):
    slots = {}
    for claim in claims:
        slots.setdefault((claim['predicate'], _norm(claim['subject']), claim['valid_on']), set()).add(_norm(claim['object']))
    return [_norm(claim['object']) for claim in claims
            if len(slots[(claim['predicate'], _norm(claim['subject']), claim['valid_on'])]) == 1]


def run_case(public, root):
    """No oracle enters this function, the controller, or the Runtime input."""
    started = time.monotonic()
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
        workflow='team_decision', query=public['query'], final_protocol='claim-proposal/v1',
        max_steps=20, dry_run=True))
    state = engine.checkpointer.get_latest('bounded_mh')
    records = state.tool_ledger if state else []
    calls, binding_ok, hop, quotes = [], True, None, []
    for record in records:
        row = {'sequence': record.sequence, 'tool': record.tool_id, 'status': record.status}
        if record.tool_id == 'search_notes':
            hop = record.arguments['query']
            row.update(hop=hop)
            # A follow-up search must use an entity the fake read before it, never a guess.
            if calls and not any(_norm(hop) in _norm(quote) for quote in quotes):
                binding_ok = False
        if record.tool_id == 'read_verified_note' and record.status == 'completed':
            value = record.result['value']
            quotes.append(value['quote'])
            row.update(hop=hop, source=value['relative_path'], evidence_id=value['evidence_id'],
                       content_sha256=value['content_sha256'])
        calls.append(row)
    result_path = root/'runtime'/'results'/status.run_id/'result.json'
    payload = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    final = (payload.get('result') or {}).get('team_decision')
    claims = (payload.get('result') or {}).get('typed_claims') or [] if final else []
    values = conflict_free_values(claims)
    cited = final['evidence_refs'] if final else []
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
        'run_status': status.status, 'error': status.error, 'final': final, 'typed_claims': claims}


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
