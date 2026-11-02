"""Failure injection for model output, Jev transport, deadlines and cost caps."""
from dataclasses import replace
import threading

import pytest

from linkloom.rerank.base import RerankError, score_candidates
from linkloom.rerank.pipeline import RerankPipeline
from linkloom.rerank.jev import JevReranker
from test_rerank import candidates


@pytest.mark.parametrize('reason',['missing_dependency','download_failed','cache_invalid','model_load_failed','cuda_unavailable','cuda_oom','inference_timeout','worker_exited'])
def test_local_fault_matrix_falls_back_to_exact_rrf(reason):
    class Broken:
        def rerank(self,*args): raise RerankError(reason)
    values=candidates();pipeline=RerankPipeline('auto',local=Broken())
    assert pipeline.rerank('q',values)==[c.evidence for c in values]
    assert pipeline.last_trace['reason']==reason
    assert [a['backend'] for a in pipeline.last_trace['attempts']]==['local','noop']


@pytest.mark.parametrize('corruption',['missing','duplicate','unknown','changed_score','changed_identity','changed_source_metadata','none_score','nan_score'])
def test_invalid_model_output_is_rejected_as_one_batch(corruption):
    class Bad:
        def rerank(self,query,values):
            result=score_candidates(values,[1.,2.,3.])
            if corruption=='missing':return result[:-1]
            if corruption=='duplicate':return [result[0]]*len(result)
            if corruption=='unknown':result[0]=replace(result[0],evidence_id='foreign')
            if corruption=='changed_score':result[0]=replace(result[0],score=99.)
            if corruption=='changed_identity':result[0]=replace(result[0],source_ref='foreign')
            if corruption=='changed_source_metadata':result[0]=replace(result[0],metadata={})
            if corruption=='none_score':result[0]=replace(result[0],rerank_score=None)
            if corruption=='nan_score':result[0]=replace(result[0],rerank_score=float('nan'))
            return result
    values=candidates();pipeline=RerankPipeline('auto',local=Bad())
    assert pipeline.rerank('q',values)==[c.evidence for c in values]
    assert pipeline.last_trace['reason']=='invalid_scores'


def test_backend_cannot_mutate_original_metadata():
    class Mutating:
        def rerank(self,query,values):
            values[0].evidence.metadata['nested']['safe']=False
            raise RuntimeError('secret query and key')
    values=candidates();pipeline=RerankPipeline('local',local=Mutating())
    assert pipeline.rerank('q',values)==[c.evidence for c in values]
    assert values[0].evidence.metadata['nested']['safe'] is True
    assert pipeline.last_trace['reason']=='inference_failed'


def prepare(query,values):return 'bounded query',[f'bounded {i}' for i in range(len(values))]


def response(payload,**kwargs):
    return {'model':'jev-1.14.0','answers':{key:{'type':'noul','noul':index/10} for index,key in enumerate(payload['questions'])},'usage':{'input_tokens':1234,'output_tokens':12}}


def test_jev_request_shape_score_and_usage_are_explicit():
    requests=[]
    def capture(payload,**kwargs):
        requests.append((payload,kwargs));return response(payload)
    backend=JevReranker('private-key',transport=capture,prepare=prepare,budget_usd=1.)
    result=backend.rerank('raw query',candidates())
    payload,options=requests[0]
    assert payload['state']=={'query':'bounded query','candidates':{'c00':'bounded 0','c01':'bounded 1','c02':'bounded 2'}}
    assert payload['model']=='jev-1.14.0' and len(payload['questions'])==3
    assert all(v['type']=='noul' for v in payload['questions'].values())
    assert options['headers']['Authorization']=='Bearer private-key' and options['timeout']==3.
    assert [r.evidence_id for r in result]==['e2','e1','e0']
    account=backend.accounting()
    assert account['request_count']==1 and account['input_tokens']==1234
    assert account['cost_usd']==pytest.approx(1234*.042/1_000_000)
    assert account['reserved_cost_usd']==0 and not account['accounting_uncertain']


@pytest.mark.parametrize('reason',['http_401','http_403','http_429','http_529','http_500','request_timeout','request_failed','invalid_response'])
def test_jev_fault_matrix_preserves_rrf_and_counts_sent_requests(reason):
    def broken(*args,**kwargs):raise RerankError(reason)
    backend=JevReranker('key',transport=broken,prepare=prepare,budget_usd=1.)
    pipeline=RerankPipeline('jev',jev=backend);values=candidates()
    assert pipeline.rerank('q',values)==[v.evidence for v in values]
    assert pipeline.last_trace['reason']==reason
    assert backend.request_count==1
    assert backend.accounting()['accounting_uncertain']
    assert backend.accounting()['upper_bound_cost_usd']<1.


@pytest.mark.parametrize('bad',['missing','unknown','type','nan','range','partial'])
def test_jev_rejects_incomplete_or_invalid_response_but_records_usage(bad):
    def invalid(payload,**kwargs):
        data=response(payload)
        if bad=='missing':data.pop('answers')
        if bad=='unknown':data['answers']['not_requested']={'type':'noul','noul':.5}
        if bad=='type':data['answers']['c00']['type']='score'
        if bad=='nan':data['answers']['c00']['noul']=float('nan')
        if bad=='range':data['answers']['c00']['noul']=1.1
        if bad=='partial':data['answers'].pop('c01')
        return data
    backend=JevReranker('key',transport=invalid,prepare=prepare,budget_usd=1.)
    with pytest.raises(RerankError,match='invalid_response'):backend.rerank('q',candidates())
    assert backend.input_tokens==1234 and backend.cost_usd>0


def test_jev_budget_is_reserved_before_any_request_and_no_key_is_logged():
    called=[]
    backend=JevReranker('secret',transport=lambda *a,**k:called.append(True),prepare=prepare,budget_usd=.0001)
    with pytest.raises(RerankError,match='budget_exhausted'):backend.rerank('q',candidates())
    assert not called and backend.request_count==0


def test_unknown_usage_blocks_more_budgeted_requests():
    def no_usage(payload,**kwargs):
        value=response(payload);value.pop('usage');return value
    backend=JevReranker('key',transport=no_usage,prepare=prepare,budget_usd=1.)
    with pytest.raises(RerankError,match='usage_unavailable'):backend.rerank('q',candidates())
    with pytest.raises(RerankError,match='accounting_uncertain'):backend.rerank('q',candidates())
    assert backend.request_count==1 and backend.accounting()['upper_bound_cost_usd']<1.


def test_jev_deadline_returns_without_waiting_for_blocked_transport():
    release=threading.Event();entered=threading.Event()
    def blocked(payload,**kwargs):
        entered.set();release.wait(2);return response(payload)
    backend=JevReranker('key',transport=blocked,prepare=prepare,timeout_s=.02,budget_usd=1.)
    try:
        with pytest.raises(RerankError,match='request_timeout'):backend.rerank('q',candidates())
        assert entered.is_set() and not release.is_set()
        assert backend.request_count==1 and backend.accounting()['accounting_uncertain']
    finally:release.set()


def test_explicit_jev_without_key_never_constructs_remote(monkeypatch):
    monkeypatch.delenv('LINKLOOM_JEV_API_KEY',raising=False);monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    values=candidates();pipeline=RerankPipeline('jev')
    assert pipeline.rerank('q',values)==[v.evidence for v in values]
    assert pipeline.last_trace['reason']=='missing_key'
