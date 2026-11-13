"""One-shot A/B/C/D(+optional E) development comparison; never loads held-out data.

Prepare model artifacts before running. Existing output directories are refused.
Only the synthetic scale corpus and explicitly designated retrieval30 dev set are read.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from linkloom.rerank.base import NoopReranker, RerankCandidate, RerankError
from linkloom.retrieval_v2.models import RetrievedEvidence, RetrievalQuery
from linkloom.retrieval_v2.metrics import retrieval_case_metrics
from bench_scale_data import MODEL, SEED, retrieval_fixture, fixture_manifest


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


def save(path, value):
    target = Path(path)
    partial = target.with_name(target.name + '.partial')
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(partial, target)


def memory():
    import psutil
    return {'available_mib': psutil.virtual_memory().available / 2**20,
            'rss_mib': psutil.Process().memory_info().rss / 2**20}


def percentiles(values):
    ordered = sorted(values)
    if not ordered:
        return {'p50_ms': None, 'p95_ms': None}
    def at(q):
        position = (len(ordered)-1)*q
        low, high = math.floor(position), math.ceil(position)
        return ordered[low]+(ordered[high]-ordered[low])*(position-low)
    return {'p50_ms': at(.5), 'p95_ms': at(.95)}


def metric_values(hits, relevant):
    # Document metrics use each source only once, MRR is bounded to the same 20.
    paths = list(dict.fromkeys(hit.source_ref for hit in hits))[:20]
    values = retrieval_case_metrics(paths, set(relevant))
    return {'recall_at_5': values['recall_at_5'], 'mrr_at_20': values['mrr'], 'ndcg_at_5': values['ndcg_at_5']}


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):
            digest.update(block)
    return digest.hexdigest()


def validate_artifacts(models_dir, onnx_path, manifest_path):
    from linkloom.rerank.model_cache import REVISIONS,BASE_MODEL,M3_MODEL
    expected={BASE_MODEL:'ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd',
              M3_MODEL:'d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286'}
    verified={}
    if models_dir:
        for name in (BASE_MODEL,M3_MODEL):
            folder=models_dir/name.rsplit('/',1)[-1]
            manifest=json.loads((folder/'download-manifest.json').read_text(encoding='utf-8'))
            if manifest['model']!=name or manifest['revision']!=REVISIONS[name]:
                raise ValueError('model revision mismatch')
            weights=file_hash(folder/'model.safetensors')
            if weights!=expected[name]:raise ValueError('model weights hash mismatch')
            for item in manifest['files']:
                if item['name']!='model.safetensors' and file_hash(folder/item['name'])!=item['sha256']:
                    raise ValueError('model tokenizer/config hash mismatch')
            verified[name]={'revision':manifest['revision'],'weights_sha256':weights,'config_tokenizer_manifest_checked':True}
    if onnx_path:
        if manifest_path is None:raise ValueError('explicit ONNX requires measurement manifest')
        m=json.loads(manifest_path.read_text(encoding='utf-8'))
        if not (m.get('status')=='complete' and m.get('per_channel') is True and m.get('reduce_range') is True
                and m.get('MatMulConstBOnly') is True and m.get('operators')==['MatMul','Gather']
                and m.get('int8_bytes')==onnx_path.stat().st_size):
            raise ValueError('ONNX measurement manifest/config mismatch')
        verified['onnx_int8']={'sha256':file_hash(onnx_path),'bytes':onnx_path.stat().st_size,
                               'measurement_manifest_sha256':file_hash(manifest_path),'settings_checked':True}
    return verified


def create_run_directory(output):
    # Never overwrite or silently resume the single formal run.
    output.mkdir(parents=True, exist_ok=False)
    (output/'run.lock').write_text(now(),encoding='utf-8')


def read_key(env_file=None):
    for name in ('LINKLOOM_JEV_API_KEY', 'TYPESAFE_API_KEY'):
        value = os.environ.get(name, '').strip()
        if value:
            return value
    if env_file:
        from dotenv import dotenv_values
        values = dotenv_values(env_file)
        for name in ('LINKLOOM_JEV_API_KEY', 'TYPESAFE_API_KEY'):
            value = (values.get(name) or '').strip()
            if value:
                return value
    return None


def restrict_network(allow_jev=False):
    # Audit hooks also cover connects attempted by libraries that cached socket helpers.
    def audit(event, args):
        if event == 'socket.getaddrinfo':
            host = str(args[0])
            if not allow_jev or host != 'api.typesafe.ai':
                raise RuntimeError('network disabled for local development benchmark')
        if event == 'socket.connect' and not allow_jev:
            raise RuntimeError('network disabled for local development benchmark')
    sys.addaudithook(audit)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='8', MKL_NUM_THREADS='8', LINKLOOM_RERANK='off')


def dev_datasets():
    from linkloom.indexing.models import IndexDocument
    for size in (1000, 10000):
        raw, queries = retrieval_fixture(size, 100, SEED)
        # Evaluator-only metadata is deliberately removed from retrieval/model inputs.
        docs = [IndexDocument(**{**row, 'metadata': {}}) for row in raw]
        cases = [{'id':q['id'], 'workspace_id':docs[0].workspace_id, 'query':q['query'],
                  'relevant':[f'synthetic/{rid}.md' for rid in q['relevant_ids']]} for q in queries]
        yield f'scale-{size}', docs, cases, fixture_manifest(raw, queries)
    from linkloom.retrieval_v2.benchmark import FrozenRetrievalBenchmark
    frozen = FrozenRetrievalBenchmark(ROOT)
    assert len(frozen.cases) == 30 and len(frozen.documents) == 36
    cases = [{'id':c.case_id, 'workspace_id':c.workspace_id, 'query':c.query,
              'relevant':sorted(c.relevant_source_refs)} for c in frozen.cases]
    digest = hashlib.sha256(frozen.dataset_path.read_bytes()).hexdigest()
    yield 'retrieval30-dev', list(frozen.documents), cases, {'documents':36,'queries':30,'sha256':digest,'role':'development only'}


def baseline_phase(output, snapshot):
    restrict_network()
    from linkloom.indexing import BM25Index, VectorIndex, SentenceTransformerEmbedder
    from linkloom.retrieval_v2.retrievers import BM25Retriever, DenseRetriever, HybridRetriever
    staged = []
    embedder = SentenceTransformerEmbedder(str(snapshot), local_files_only=True)
    for name, documents, cases, manifest in dev_datasets():
        before = memory()
        start = time.perf_counter()
        lexical = BM25Index(); lexical.build(documents)
        vector = VectorIndex(embedder); vector.build(documents)
        build_ms = (time.perf_counter()-start)*1000
        bm25 = BM25Retriever(lexical)
        hybrid = HybridRetriever(bm25, DenseRetriever(vector), reranker=NoopReranker())
        by_identity = {(d.workspace_id,d.evidence_id):d for d in documents}
        rows = []
        for case in cases:
            query = RetrievalQuery(case['workspace_id'],case['query'],top_k=20)
            start = time.perf_counter(); a = bm25.retrieve(query); a_ms=(time.perf_counter()-start)*1000
            start = time.perf_counter(); b = hybrid.retrieve(query); b_ms=(time.perf_counter()-start)*1000
            rows.append({**case, 'A':{'hits':[asdict(h) for h in a],'metrics':metric_values(a,case['relevant']),'retrieval_ms':a_ms,'rerank_ms':0.0},
                         'B':{'hits':[asdict(h) for h in b],'metrics':metric_values(b,case['relevant']),'retrieval_ms':b_ms,'rerank_ms':0.0},
                         'candidate_texts':[by_identity[(case['workspace_id'],h.evidence_id)].content for h in b]})
        staged.append({'dataset':name,'manifest':manifest,'before':before,'build_ms':build_ms,'cases':rows})
        save(output/'baseline-private.json', staged)
        print(json.dumps({'phase':'baseline','dataset':name,'queries':len(rows),'build_ms':build_ms}),flush=True)
        del vector,lexical,bm25,hybrid,documents
        gc.collect()
    # This intermediate is in ignored work/, never included in the product PR.


def warmup_candidates():
    return [RerankCandidate(RetrievedEvidence('warm-a','warm-a','/warm-a',.03,1,'hybrid','warm-a'),
               'A local retrieval system keeps source documents private and supports offline search.'),
            RerankCandidate(RetrievedEvidence('warm-b','warm-b','/warm-b',.02,2,'hybrid','warm-b'),
               'The administrative team discussed office lighting and chairs this week.')]


def apply_group(staged, group, backend, report, report_path):
    for dataset in staged:
        before=memory()
        report['groups'][group]['datasets'].append({'dataset':dataset['dataset'],'before':before,'cases':[]})
        target=report['groups'][group]['datasets'][-1]
        for case in dataset['cases']:
            values=[RetrievedEvidence(**h) for h in case['B']['hits']]
            candidates=[RerankCandidate(h,text) for h,text in zip(values,case['candidate_texts'])]
            started=time.perf_counter()
            try:
                ranked=backend.rerank(case['query'],candidates)
                if len(ranked)!=len(values) or {h.evidence_id for h in ranked}!={h.evidence_id for h in values}:
                    raise RerankError('invalid_scores')
                if any(h.rerank_score is None or not math.isfinite(h.rerank_score) for h in ranked):
                    raise RerankError('unscored_result')
                elapsed=(time.perf_counter()-started)*1000
                trace=dict(getattr(backend,'last_trace',{}))
                target['cases'].append({'case_id':case['id'],'metrics':metric_values(ranked,case['relevant']),
                    'hits':[asdict(h) for h in ranked], 'rerank_ms':elapsed,'status':'measured',
                    'trace':trace,'candidate_ids':[h.evidence_id for h in values]})
            except Exception as error:
                reason=getattr(error,'reason',type(error).__name__)
                target['cases'].append({'case_id':case['id'],'status':'invalid','reason':reason,'rerank_ms':(time.perf_counter()-started)*1000})
                report['groups'][group]['status']='invalid'
                if group=='E' and reason in {'budget_exhausted','accounting_uncertain','usage_unavailable'}:
                    report['groups'][group]['reason']=reason
                    if hasattr(backend,'accounting'):report['groups'][group]['accounting']=backend.accounting()
                    measured=[r for r in target['cases'] if r['status']=='measured']
                    target['metrics']={m:sum(r['metrics'][m] for r in measured)/len(measured) if measured else None for m in ('recall_at_5','mrr_at_20','ndcg_at_5')}
                    target['latency']=percentiles([r['rerank_ms'] for r in measured])
                    target['invalid_queries']=len(target['cases'])-len(measured)
                    target['unmeasured_queries']=len(dataset['cases'])-len(target['cases'])
                    save(report_path,report)
                    return
            if hasattr(backend,'accounting'):
                report['groups'][group]['accounting']=backend.accounting()
            save(report_path,report)
        measured=[r for r in target['cases'] if r['status']=='measured']
        target['metrics']={key:sum(r['metrics'][key] for r in measured)/len(measured) if measured else None for key in ('recall_at_5','mrr_at_20','ndcg_at_5')}
        target['latency']=percentiles([r['rerank_ms'] for r in measured])
        target['invalid_queries']=len(target['cases'])-len(measured)
        print(json.dumps({'phase':group,'dataset':dataset['dataset'],'metrics':target['metrics'],'latency':target['latency'],'invalid_queries':target['invalid_queries']}),flush=True)
        save(report_path,report)


def analysis_rows(staged, report):
    details=[]
    for dataset in staged:
        for case in dataset['cases']:
            a,b=case['A']['metrics'],case['B']['metrics']
            declines=[metric for metric in a if b[metric]<a[metric]-1e-7]
            if not declines:
                continue
            entry={'dataset':dataset['dataset'],'case_id':case['id'],'declined_metrics':declines,'A':a,'B':b,'reranked':{}}
            relevant=set(case['relevant'])
            pool={h['source_ref'] for h in case['B']['hits']}
            entry['rrf20_recall_ceiling']=len(relevant&pool)/len(relevant)
            for group in ('C','D','E'):
                found=next((row for ds in report['groups'].get(group,{}).get('datasets',[]) if ds['dataset']==dataset['dataset'] for row in ds['cases'] if row['case_id']==case['id']),None)
                if not found or found['status']!='measured':
                    entry['reranked'][group]={'status':'not_measured'};continue
                missing=[m for m in declines if found['metrics'][m]<a[m]-1e-7]
                truncated=any(d.get('truncated') for d in found.get('trace',{}).get('input_details',[]))
                if not missing:reason='recovered_all_declined_metrics'
                elif not relevant.issubset(pool):reason='candidate_pool_missing_relevant_sources'
                elif truncated:reason='ranking_error_or_window_truncation_possible'
                else:reason='ranking_error_with_relevant_sources_available'
                entry['reranked'][group]={'metrics':found['metrics'],'restored_to_A':not missing,'still_below_A':missing,'reason':reason,'reason_note':'Model/window attribution is a hypothesis unless missing source IDs establish the recall ceiling.'}
            details.append(entry)
    return details


def markdown(report):
    lines=['# L5-1 rerank development comparison','',f"Run: {report['started_at']} · commit: `{report['commit']}`",'',
           'Synthetic development data only. Retrieval30 is explicitly used as development data. No held-out acceptance set was evaluated.',
           'One formal pass; A/B retrieve once and C/D/E reuse the exact RRF top-20 per query. No labels or answers enter model text.',
           'MRR@20 uses the first relevant source in the retained 20; Recall@5 and binary-gain nDCG@5 use the first five. Macro averages; linear latency percentiles.',
           'Rerank latency includes text/token preparation, IPC and scoring; offline preparation and synthetic warmup are excluded. A/B rerank time is zero.', '',
           '| Dataset | Group | Recall@5 | MRR@20 | nDCG@5 | rerank p50 ms | p95 ms | Invalid |',
           '|---|---|---:|---:|---:|---:|---:|---:|']
    for group,info in report['groups'].items():
        if not info['datasets']:
            lines.append(f"| — | {group}: {info['status']} | — | — | — | — | — | — |")
        for ds in info['datasets']:
            m=ds['metrics'];latency=ds['latency']
            def fmt(x):return '—' if x is None else f'{x:.4f}'
            lines.append(f"| {ds['dataset']} | {group} | {fmt(m['recall_at_5'])} | {fmt(m['mrr_at_20'])} | {fmt(m['ndcg_at_5'])} | {fmt(latency['p50_ms'])} | {fmt(latency['p95_ms'])} | {ds.get('invalid_queries',0)} |")
    lines+=['','## Local preparation and Jev accounting','']
    for g in ('C','D'):
        lines.append(f"- {g}: `{json.dumps(report['groups'][g].get('preparation',{}),ensure_ascii=False)}`")
    e_status='未运行（本机未配置 Jev key）' if report['groups']['E']['status']=='not_run_no_key' else report['groups']['E']['status']
    lines.append(f"- E: {e_status}; `{json.dumps(report['groups']['E'].get('accounting',{}),ensure_ascii=False)}`")
    lines+=['','INT8 settings: QInt8, per_channel=true, reduce_range=true (7-bit weight range in INT8 storage), MatMul/Gather, MatMulConstBOnly=true, opset 17. CPU threads=8, batch=8, pair limit=256 tokens.',
            'The local base p95 target is 3000 ms for at most 20 candidates; per-dataset measured values above establish whether it was met.', '',
            '## Every B < A development case','', '| Dataset / case | Declines | C restored | D restored | E restored |', '|---|---|---|---|---|']
    for row in report['analysis']:
        def state(g):
            v=row['reranked'][g]
            return 'not measured' if v.get('status')=='not_measured' else ('yes' if v['restored_to_A'] else 'no: '+','.join(v['still_below_A'])+'; '+v['reason'])
        lines.append(f"| {row['dataset']} / {row['case_id']} | {','.join(row['declined_metrics'])} | {state('C')} | {state('D')} | {state('E')} |")
    lines+=['','Each row, including drm-002, has its A/B/C/D/E metrics, source ranks, recall ceiling and remaining failure reasons in results.json. Score scales are never compared across BM25, RRF and rerankers.',
            'Window omission vs semantic confusion is marked as a hypothesis. Ranking agreement or development gains are not held-out task accuracy.', '',
            '## Failures and regressions relative to B','']
    for group,info in report['groups'].items():
        if group not in {'C','D','E'}:continue
        lines.append(f"### {group}")
        for ds in info['datasets']:
            original=next(d for d in report['baseline'] if d['dataset']==ds['dataset'])
            by_id={c['case_id']:c for c in original['cases']}
            for row in ds['cases']:
                if row['status']!='measured':
                    lines.append(f"- {ds['dataset']}/{row['case_id']}: backend invalid: {row.get('reason')}");continue
                lower=[m for m,v in row['metrics'].items() if v<by_id[row['case_id']]['B']['metrics'][m]-1e-7]
                if row['metrics']['recall_at_5']<1 or lower:
                    baseline_case=by_id[row['case_id']]
                    relevant=set(baseline_case['relevant'])
                    pool={h['source_ref'] for h in baseline_case['B']['hits']}
                    if not relevant.issubset(pool):reason='candidate recall limit: relevant source absent from RRF20'
                    elif any(d.get('truncated') for d in row.get('trace',{}).get('input_details',[])):reason='relevant sources present; reranker ordering/window omission are possible causes'
                    else:reason='relevant sources present; reranker ordering fails to prioritize all gold evidence'
                    lines.append(f"- {ds['dataset']}/{row['case_id']}: Recall@5={row['metrics']['recall_at_5']:.4f}; below B: {','.join(lower) or 'none'}; {reason}.")
    lines+=['', '[Raw measurements and model/data manifests](results.json)', '']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir',type=Path)
    parser.add_argument('--base-onnx',type=Path)
    parser.add_argument('--base-onnx-manifest',type=Path)
    parser.add_argument('--embedding-snapshot',type=Path)
    parser.add_argument('--jev-key-env-file',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--baseline-worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.baseline_worker:
        baseline_phase(args.baseline_worker,args.embedding_snapshot);return
    import psutil
    from huggingface_hub import snapshot_download
    from linkloom.rerank.local import LocalCrossEncoder
    from linkloom.rerank.model_cache import BASE_MODEL,M3_MODEL,REVISIONS,EXPORT_SETTINGS
    snapshot=args.embedding_snapshot or Path(snapshot_download(MODEL,local_files_only=True))
    if not snapshot.is_dir():raise ValueError('embedding snapshot missing')
    if args.models_dir and any(not (args.models_dir/slug/'config.json').is_file() for slug in ('bge-reranker-base','bge-reranker-v2-m3')):
        raise ValueError('local model configuration missing')
    if args.base_onnx and not args.base_onnx.is_file():raise ValueError('base ONNX missing')
    artifacts=validate_artifacts(args.models_dir,args.base_onnx,args.base_onnx_manifest)
    key=read_key(args.jev_key_env_file)
    output=args.output or ROOT/'benchmarks'/'rerank'/datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    create_run_directory(output)
    private=ROOT/'work'/('rerank-'+output.name);private.mkdir(parents=True,exist_ok=False)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    versions={n:importlib.metadata.version(n) for n in ('torch','transformers','onnxruntime','onnx','sentence-transformers','numpy')}
    report={'started_at':now(),'commit':commit,'source_state':'source fingerprints below identify the evaluated implementation; commit is invocation HEAD','status':'running','seed':SEED,'settings':{'max_candidates':20,'max_pair_tokens':256,'query_tokens':64,'batch_size':8,'threads':8,'measured_passes':1,'quantization':EXPORT_SETTINGS},
            'embedding':{'model':MODEL,'revision':snapshot.name},'model_revisions':REVISIONS,'packages':versions,'source_sha256':{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__).resolve(),*sorted((ROOT/'src/linkloom/rerank').glob('*.py')),*(ROOT/p for p in ('src/linkloom/retrieval_v2/retrievers.py','src/linkloom/retrieval_v2/models.py','src/linkloom/retrieval_v2/runtime_backend.py','src/linkloom/agents/runtime_adapter.py'))]},'verified_artifacts':artifacts,'groups':{},'baseline':[],'analysis':[]}
    path=output/'results.json';save(path,report)
    try:
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'--baseline-worker',str(private),'--embedding-snapshot',str(snapshot)]
        subprocess.run(command,cwd=ROOT,check=True)
        staged=json.loads((private/'baseline-private.json').read_text(encoding='utf-8'))
        for group in ('A','B'):
            report['groups'][group]={'status':'measured','datasets':[]}
            for ds in staged:
                cases=[{'case_id':c['id'],**c[group]} for c in ds['cases']]
                report['groups'][group]['datasets'].append({'dataset':ds['dataset'],'metrics':{m:sum(c['metrics'][m] for c in cases)/len(cases) for m in ('recall_at_5','mrr_at_20','ndcg_at_5')},'latency':percentiles([0.]*len(cases)),'cases':cases,'before':ds['before'],'invalid_queries':0})
        report['baseline']=[{'dataset':ds['dataset'],'manifest':ds['manifest'],'cases':[{'case_id':c['id'],'relevant':c['relevant'],'A':c['A'],'B':c['B']} for c in ds['cases']]} for ds in staged]
        save(path,report)
        restrict_network(allow_jev=bool(key))
        for group,model,engine in (('C',BASE_MODEL,'onnx'),('D',M3_MODEL,'torch')):
            model_dir=str(args.models_dir/model.rsplit('/',1)[-1]) if args.models_dir else None
            backend=LocalCrossEncoder(model,model_dir=model_dir,onnx_path=str(args.base_onnx) if group=='C' and args.base_onnx else None,device='cpu',engine=engine,timeout_s=120)
            report['groups'][group]={'status':'measured','datasets':[],'before_prepare':memory()}
            try:
                report['groups'][group]['preparation']=backend.prepare(600)
                backend.rerank('Why use private local retrieval?',warmup_candidates())
                apply_group(staged,group,backend,report,path)
            finally:backend.close()
        report['groups']['E']={'status':'not_run_no_key','reason':'未运行：本机未配置 Jev key','datasets':[],
            'accounting':{'request_count':0,'input_tokens':0,'cost_usd':0.,'upper_bound_cost_usd':0.,'budget_usd':1.}}
        if key:
            from linkloom.rerank.jev import JevReranker
            from linkloom.rerank.model_cache import load_tokenizer
            from linkloom.rerank.local import prepare_texts
            tokenizer=load_tokenizer(BASE_MODEL,str(args.models_dir/'bge-reranker-base') if args.models_dir else None)
            def prepare(query,candidates):
                bounded,texts,_=prepare_texts(query,[c.text for c in candidates],tokenizer)
                return bounded,texts
            backend=JevReranker(key,model='jev-1.14.0',budget_usd=1.,prepare=prepare)
            report['groups']['E'].update(status='measured',reason=None)
            try:apply_group(staged,'E',backend,report,path)
            finally:
                report['groups']['E']['accounting']=backend.accounting();backend.close()
            assert report['groups']['E']['accounting']['upper_bound_cost_usd']<=1.
        report['analysis']=analysis_rows(staged,report)
        frozen_path=ROOT/'benchmarks/datasets/m1_team_decision_eval_seed/dataset.jsonl'
        frozen_manifest=next(ds['manifest'] for ds in staged if ds['dataset']=='retrieval30-dev')
        report['frozen_dev_unchanged']=hashlib.sha256(frozen_path.read_bytes()).hexdigest()==frozen_manifest['sha256']
        if not report['frozen_dev_unchanged']:raise RuntimeError('development data changed during run')
        report['status']='complete' if all(report['groups'][g]['status']=='measured' for g in ('A','B','C','D')) else 'invalid'
    except Exception as error:
        report['status']='failed';report['failure_type']=type(error).__name__
        report['failure_reason']=getattr(error,'reason','benchmark_failed')
        raise
    finally:
        report['finished_at']=now();save(path,report)
        (output/'results.md').write_text(markdown(report) if all(g in report['groups'] for g in ('C','D','E')) else '# Incomplete development run\n\nSee results.json; no automatic rerun.\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'output':str(output)}),flush=True)


if __name__=='__main__':main()
