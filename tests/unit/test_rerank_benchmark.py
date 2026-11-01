"""Check the one-pass development harness on tiny invented inputs only."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import bench_rerank as bench
from linkloom.retrieval_v2.models import RetrievedEvidence
from linkloom.rerank.base import score_candidates, RerankError

@pytest.fixture(autouse=True)
def invented_memory_snapshot(monkeypatch):
    # Unit tests verify harness bookkeeping; psutil remains a rerank-only dependency.
    monkeypatch.setattr(bench, 'memory', lambda: {'available_mib': 1024., 'rss_mib': 32.})


def hit(i):return RetrievedEvidence(str(i),f's{i}',f'/s{i}',1/(61+i),i+1,'hybrid',str(i))


def test_metrics_are_recall5_binary_ndcg5_and_mrr20():
    scores=bench.metric_values([hit(i) for i in range(25)],{'s6','s24'})
    assert scores['recall_at_5']==0
    assert scores['mrr_at_20']==pytest.approx(round(1/7,4))
    assert scores['ndcg_at_5']==0
    assert bench.metric_values([hit(0),hit(0),hit(1)],{'s1'})['mrr_at_20']==.5


def test_latency_percentiles_use_linear_interpolation_and_empty_values_are_explicit():
    assert bench.percentiles([1,2,3,4])=={'p50_ms':2.5,'p95_ms':pytest.approx(3.85)}
    assert bench.percentiles([])=={'p50_ms':None,'p95_ms':None}


def test_key_lookup_reads_only_explicit_keys_and_empty_means_no_E(monkeypatch,tmp_path):
    for name in ('LINKLOOM_JEV_API_KEY','TYPESAFE_API_KEY'):monkeypatch.delenv(name,raising=False)
    assert bench.read_key() is None
    path=tmp_path/'env';path.write_text('UNRELATED_SECRET=do-not-use\nLINKLOOM_JEV_API_KEY=\n')
    assert bench.read_key(path) is None
    path.write_text('TYPESAFE_API_KEY=synthetic-test-key\n')
    assert bench.read_key(path)=='synthetic-test-key'


def staged_case():
    values=[hit(i) for i in range(3)]
    from dataclasses import asdict
    return [{'dataset':'invented','cases':[{'id':'invented-1','query':'neutral question','relevant':['s2'],
        'A':{'metrics':bench.metric_values(values,{'s2'})},
        'B':{'hits':[asdict(h) for h in values],'metrics':bench.metric_values(values,{'s2'})},
        'candidate_texts':['only raw text a','only raw text b','only raw text c']}]}]


def test_groups_share_exact_B_candidates_and_model_never_receives_labels(tmp_path):
    class Model:
        last_trace={}
        def rerank(self,query,candidates):
            assert query=='neutral question'
            assert [c.text for c in candidates]==['only raw text a','only raw text b','only raw text c']
            assert [c.evidence.evidence_id for c in candidates]==['0','1','2']
            return score_candidates(candidates,[.1,.2,.9])
    staged=staged_case();report={'groups':{'C':{'status':'measured','datasets':[]}}}
    bench.apply_group(staged,'C',Model(),report,tmp_path/'results.json')
    row=report['groups']['C']['datasets'][0]['cases'][0]
    assert row['candidate_ids']==['0','1','2'] and row['hits'][0]['evidence_id']=='2'
    assert row['metrics']['mrr_at_20']==1.
    assert row['hits'][0]['score']==hit(2).score
    assert row['status']=='measured'


def test_backend_error_is_invalid_not_a_noop_success(tmp_path):
    class Broken:
        def rerank(self,*args):raise RerankError('inference_timeout')
    report={'groups':{'D':{'status':'measured','datasets':[]}}}
    bench.apply_group(staged_case(),'D',Broken(),report,tmp_path/'results.json')
    assert report['groups']['D']['status']=='invalid'
    assert report['groups']['D']['datasets'][0]['cases'][0]['status']=='invalid'
    assert report['groups']['D']['datasets'][0]['metrics']['recall_at_5'] is None


def test_e_budget_failure_stops_group_without_more_requests(tmp_path):
    class Budget:
        calls=0
        def rerank(self,*args):self.calls+=1;raise RerankError('budget_exhausted')
        def accounting(self):return {'request_count':0,'cost_usd':0,'upper_bound_cost_usd':0,'budget_usd':1}
    staged=staged_case();staged[0]['cases']*=3
    backend=Budget();report={'groups':{'E':{'status':'measured','datasets':[]}}}
    bench.apply_group(staged,'E',backend,report,tmp_path/'results.json')
    assert backend.calls==1 and report['groups']['E']['status']=='invalid'
    assert report['groups']['E']['accounting']['upper_bound_cost_usd']<=1


def test_budget_stop_finalizes_dataset_for_report_generation(tmp_path):
    class Budget:
        def rerank(self,*args):raise RerankError('budget_exhausted')
        def accounting(self):return {'request_count':0,'cost_usd':0,'upper_bound_cost_usd':0}
    report={'groups':{'E':{'status':'measured','datasets':[]}}}
    staged=staged_case();staged[0]['cases']*=2
    bench.apply_group(staged,'E',Budget(),report,tmp_path/'results.json')
    dataset=report['groups']['E']['datasets'][0]
    assert dataset['metrics']['mrr_at_20'] is None
    assert dataset['latency']['p95_ms'] is None
    assert dataset['unmeasured_queries']==1


def test_explicit_onnx_must_match_measured_quantization_settings(tmp_path):
    target=tmp_path/'model.onnx';target.write_bytes(b'verified-int8')
    manifest=tmp_path/'measurement.json'
    with pytest.raises(ValueError,match='requires measurement manifest'):
        bench.validate_artifacts(None,target,None)
    value={'status':'complete','per_channel':True,'reduce_range':True,'MatMulConstBOnly':True,'operators':['MatMul','Gather'],'int8_bytes':target.stat().st_size}
    manifest.write_text(json.dumps(value))
    checked=bench.validate_artifacts(None,target,manifest)
    assert checked['onnx_int8']['sha256']==bench.file_hash(target)
    value['reduce_range']=False;manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='manifest/config mismatch'):
        bench.validate_artifacts(None,target,manifest)


def test_existing_run_directory_is_refused_without_overwrite(tmp_path):
    destination=tmp_path/'formal'
    bench.create_run_directory(destination)
    marker=(destination/'run.lock').read_text()
    with pytest.raises(FileExistsError):bench.create_run_directory(destination)
    assert (destination/'run.lock').read_text()==marker


def test_report_explicitly_marks_unconfigured_E_as_not_run():
    groups={g:{'status':'measured','datasets':[]} for g in ('A','B','C','D')}
    groups['E']={'status':'not_run_no_key','datasets':[],'accounting':{'request_count':0,'cost_usd':0}}
    text=bench.markdown({'started_at':'synthetic','commit':'abc','groups':groups,'analysis':[],'baseline':[]})
    assert '未运行（本机未配置 Jev key）' in text
    assert '"request_count": 0' in text and '"cost_usd": 0' in text


def test_unscored_noop_output_cannot_be_counted_as_model_measurement(tmp_path):
    class Unscored:
        def rerank(self,query,candidates):return [c.evidence for c in candidates]
    report={'groups':{'C':{'status':'measured','datasets':[]}}}
    bench.apply_group(staged_case(),'C',Unscored(),report,tmp_path/'results.json')
    assert report['groups']['C']['status']=='invalid'
    assert report['groups']['C']['datasets'][0]['cases'][0]['reason']=='unscored_result'
