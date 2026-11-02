"""Deterministic contracts for rerank order, immutable evidence and outbound control."""
from dataclasses import replace
import logging
import math

import pytest

from linkloom.retrieval_v2.models import RetrievedEvidence
from linkloom.rerank.base import NoopReranker, RerankCandidate, RerankError, score_candidates
from linkloom.rerank.pipeline import RerankPipeline, build_reranker_from_env
import linkloom.rerank.pipeline as pipeline_module


def candidates(count=3):
    return [RerankCandidate(RetrievedEvidence(f'e{i}', f'source-{i}', f'/source-{i}', 1/(60+i+1), i+1, 'hybrid', f'resource-{i}', {'content_sha256':f'hash-{i}', 'nested':{'safe':True}}), f'original passage {i}') for i in range(count)]


class Scores:
    def __init__(self, values): self.values, self.calls = values, 0
    def rerank(self, query, values):
        self.calls += 1
        return score_candidates(values, self.values)


def test_score_candidates_creates_new_order_without_replacing_original_scores():
    values=candidates()
    old=[(c.evidence.score,c.evidence.rank,dict(c.evidence.metadata)) for c in values]
    result=score_candidates(values,[0.2,0.9,0.9])
    assert [r.evidence_id for r in result]==['e1','e2','e0']
    assert [r.rank for r in result]==[1,2,3]
    assert [r.rerank_score for r in result]==[0.9,0.9,0.2]
    assert result[0].score==values[1].evidence.score
    assert result[0].metadata['rrf_rank']==2
    assert [(c.evidence.score,c.evidence.rank,dict(c.evidence.metadata)) for c in values]==old
    assert all(r.metadata is not values[int(r.evidence_id[1:])].evidence.metadata for r in result)


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,'0.5',None])
def test_score_candidates_rejects_nonfinite_or_untyped_scores(bad):
    with pytest.raises(RerankError,match='invalid_scores'):
        score_candidates(candidates(1),[bad])


def test_score_candidates_rejects_incomplete_and_duplicate_candidates():
    values=candidates(2)
    with pytest.raises(RerankError): score_candidates(values,[1.0])
    with pytest.raises(RerankError): score_candidates([values[0],values[0]],[1.,2.])


def test_noop_retains_original_objects_order_and_scores():
    values=candidates()
    assert NoopReranker.enabled is False
    assert NoopReranker().rerank('q',values)==[v.evidence for v in values]
    assert all(v.evidence.rerank_score is None for v in values)


@pytest.mark.parametrize('mode',['auto','local'])
def test_local_modes_never_call_jev_even_when_local_fails_and_key_exists(monkeypatch,mode):
    class Broken:
        def rerank(self,*args): raise RerankError('model_load_failed')
    class Forbidden:
        def rerank(self,*args): pytest.fail('local mode attempted outbound reranking')
    monkeypatch.setenv('LINKLOOM_JEV_API_KEY','never-use-this-key')
    values=candidates()
    pipeline=RerankPipeline(mode,local=Broken(),jev=Forbidden())
    assert pipeline.rerank('q',values)==[c.evidence for c in values]
    assert [a['backend'] for a in pipeline.last_trace['attempts']]==['local','noop']
    assert pipeline.last_trace['reason']=='model_load_failed'


@pytest.mark.parametrize('query,values',[('',candidates()),('   ',candidates()),('q',[])])
def test_empty_inputs_do_not_invoke_backend(query,values):
    backend=Scores([1.,2.,3.]);pipeline=RerankPipeline('auto',local=backend)
    assert pipeline.rerank(query,values)==[v.evidence for v in values]
    assert backend.calls==0 and pipeline.last_trace['status']=='empty_input'


def test_off_does_not_read_keys_or_construct_backend(monkeypatch):
    class Forbidden:
        def rerank(self,*args): pytest.fail('off invoked model')
    monkeypatch.setenv('LINKLOOM_RERANK','off')
    pipeline=build_reranker_from_env()
    pipeline._local=pipeline._jev=Forbidden()
    values=candidates()
    assert pipeline.rerank('q',values)==[v.evidence for v in values]
    assert not pipeline.enabled and pipeline.last_trace['attempts']==[]


def test_local_missing_dependency_warns_once_without_source_or_key(caplog,monkeypatch):
    class Broken:
        def rerank(self,*args): raise ImportError('SENSITIVE QUERY API KEY')
    monkeypatch.setattr(pipeline_module,'_WARNED',set())
    caplog.set_level(logging.INFO)
    values=candidates()
    for _ in range(2): RerankPipeline('local',local=Broken()).rerank('SENSITIVE QUERY',values)
    assert sum('install linkloom[rerank]' in r.message for r in caplog.records)==1
    assert 'SENSITIVE' not in caplog.text


def test_jev_startup_notice_once_and_local_never_warns_about_outbound(caplog,monkeypatch):
    monkeypatch.setattr(pipeline_module,'_WARNED',set())
    caplog.set_level(logging.WARNING)
    RerankPipeline('auto');RerankPipeline('local');RerankPipeline('off')
    assert not caplog.records
    RerankPipeline('jev');RerankPipeline('jev')
    assert sum('Only jev mode sends data externally' in r.message for r in caplog.records)==1


def test_pipeline_records_success_with_consistent_backend_and_rank():
    values=candidates()
    pipeline=RerankPipeline('auto',local=Scores([0.,3.,1.]))
    result=pipeline.rerank('q',values)
    assert [r.evidence_id for r in result]==['e1','e2','e0']
    assert pipeline.last_trace['backend']=='local' and pipeline.last_trace['status']=='success'
    assert pipeline.last_trace['elapsed_ms']>=0


def test_pipeline_does_not_swallow_user_cancellation():
    class Cancelled:
        def rerank(self,*args): raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt): RerankPipeline('local',local=Cancelled()).rerank('q',candidates())


def test_invalid_mode_is_safe_off_and_recorded():
    pipeline=RerankPipeline('invalid')
    assert not pipeline.enabled
    assert pipeline.rerank('q',candidates())==[v.evidence for v in candidates()]
    assert pipeline.last_trace['reason']=='invalid_mode'
