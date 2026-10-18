"""Public-input runner seam, with a single fixed Dev Scripted demonstration.

No scorer/Gold imports, dynamic provider loading, databases, or network calls.
"""
from copy import deepcopy
import time

from .fixtures import digest, load_inputs, proof, source_index


class PublicTools:
    """Read-only fixture tools. Trace is recorded by executor, not adapter."""

    def __init__(self, case):
        self._case = deepcopy(case)
        self.receipts = []

    def _record(self, tool, arguments, result, status='OK'):
        self.receipts.append(dict(seq=len(self.receipts)+1,tool=tool,arguments=arguments,
                                  result_sha256=digest(result),status=status))
        return deepcopy(result)

    def list_sources(self):
        artifacts,_,current = source_index(self._case)
        result = [dict(doc_id=s['doc_id'],artifact_id=s['artifact_id'],current=current[s['doc_id']],
                       artifact_version_id=artifacts[s['doc_id']].artifact_version_id,
                       content_hash=artifacts[s['doc_id']].content_hash) for s in self._case['sources']]
        return self._record('list_sources',{},result)

    def read_source(self, doc_id):
        source = next((s for s in self._case['sources'] if s['doc_id']==doc_id),None)
        if source is None:
            self._record('read_source',dict(doc_id=doc_id),dict(error='SOURCE_NOT_AVAILABLE'),'ERROR')
            raise KeyError('SOURCE_NOT_AVAILABLE')
        _,spans,_ = source_index(self._case)
        evidence = [proof(self._case,ref) for ref in spans if ref.rsplit(':',1)[0]==doc_id]
        return self._record('read_source',dict(doc_id=doc_id),dict(**source,evidence=evidence))


def scripted_d01(request, tools):
    """Hard-coded wiring demonstration, not task discovery or a model result."""
    if request['case_id'] != 'D01':
        raise ValueError('Scripted demonstration is only defined for D01')
    sources = tools.list_sources()
    source = tools.read_source(sources[0]['doc_id'])
    evidence = source['evidence']
    return dict(tasks=[dict(ref='a:1',task='回调接入',owner='许澄',project='青岚项目',
        deadline=dict(value='2026-11-12',precision='DAY',role='TASK_DEADLINE',bound='BEFORE'),
        evidence=evidence)],relations=[],revisions=[],answer_status='COMPLETE',
        abstentions=[],answer_evidence=evidence)


def execute(case, adapter, *, adapter_kind='UNAVAILABLE'):
    start = time.perf_counter()
    tools = PublicTools(case)
    execution = dict(target_action='analyze_task_impact',target_invocations=0,tool_receipts=[],
                     model_calls=0 if adapter_kind in {'SCRIPTED','TEST'} else None,
                     input_tokens=None,output_tokens=None,cost_usd=None,wall_ms=None)
    record = dict(schema='m3-run/v1',case_id=case['case_id'],input_sha256=digest(case),
                  adapter_kind=adapter_kind,status='NOT_RUN',answer=None,execution=execution,
                  failure_stage='capability_gap',error_type=None)
    if adapter is not None:
        request = {k:case[k] for k in ('case_id','workspace_id','snapshot','question')}
        execution['target_invocations'] = 1
        try:
            record['answer'] = adapter(deepcopy(request),tools)
            record.update(status='EXECUTED',failure_stage=None)
        except Exception as error:
            # Keep trace and exception type; never persist environment/secrets.
            record.update(status='RUN_ERROR',failure_stage='agent_execution',error_type=type(error).__name__)
    execution['tool_receipts'] = deepcopy(tools.receipts)
    execution['wall_ms'] = round((time.perf_counter()-start)*1000,3)
    return record


def run_dev(case_ids=None, mode='UNAVAILABLE'):
    cases = load_inputs()
    by_id = {c['case_id']:c for c in cases}
    selected = list(by_id) if case_ids is None else case_ids
    if len(set(selected)) != len(selected) or not selected or any(i not in by_id for i in selected):
        raise ValueError('only unique known Dev IDs can execute; holdout is reserved')
    if mode not in {'UNAVAILABLE','SCRIPTED'}:
        raise ValueError('no real model adapter is enabled in this round')
    if mode=='SCRIPTED' and selected!=['D01']:
        raise ValueError('Scripted demonstration requires --case D01')
    adapter = scripted_d01 if mode=='SCRIPTED' else None
    return [execute(by_id[i],adapter,adapter_kind=mode) for i in selected]
