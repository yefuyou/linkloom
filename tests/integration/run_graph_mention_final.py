"""One authorized DEV matrix after the mention/Host-retrieval design change."""
from copy import deepcopy
from datetime import UTC,datetime,timedelta
from pathlib import Path
from time import perf_counter
import hashlib,json,logging,os,sys,zipfile
from run_temporal_graph_dev import CampaignBudget,PriceReference,BudgetExceeded,summarize
from run_graph_continuation import cases,compare,unknown_positive
from temporal_graph_routes import TemporalGraphDevRoutes
from deepseek_eval_transport import adapter_factory,assert_off_peak,OfficialTokenCounter
ROOT=Path('work/graph-mention-final')
LEDGERS=['work/graph-opt/budget-r1.json','work/graph-opt/budget-r2.json','work/graph-holdout-D2/budget.json','work/graph-opt-r3/budget-r1.json','work/graph-opt-r3/budget-r2.json']

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,value):
    with Path(p).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def save(p,value):Path(p).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')

def configuration():
    previous=read('work/graph-opt-r3/config-r2.json');config=deepcopy(previous);now=datetime.now(UTC)
    config.update(graph_mention_grounding=True,graph_grounding_required=True,input_token_caps={'graph_only':6144,'legacy_only':6144},stage_cap_usd=1.6,stage_budget_increase_usd=.6,previous_budget_ledgers=LEDGERS,locator=read(ROOT/'calibration-result-v2.json')['selected']['config'],local_models=read(ROOT/'local-models.json'),max_new_rounds=1,continuation_round=3,global_optimization_round=5,D2_split='dev',D3_accessed=False,price_checked_on=now.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date().isoformat(),pricing_valid_until_utc=(now+timedelta(minutes=55)).isoformat(),pricing_rechecked_at_utc=now.isoformat(),design_change='model literal mentions, Host hybrid retrieval plus local rerank; no model root node IDs')
    return config


class Inputs:
    def __init__(self,group,*,draft=False):
        self.group=group;self.marker=ROOT/'runtime-freeze.json';self.marker_sha=sha(self.marker) if not draft else None
        self.dependencies=dict(configuration=(ROOT/('config-token-preflight.json' if draft else 'config.json')).resolve(),public_fixture=Path('work/graph-opt-r3/public-fixtures.json').resolve(),confirmed_database=Path('work/graph-holdout-D2/confirmed.sqlite' if group=='D2' else 'work/L6-eval-dev-v1.sqlite').resolve());self.draft=draft
    def verify(self,strong_assets=False):
        if self.draft:return {}
        if sha(self.marker)!=self.marker_sha:raise ValueError('ROUND_FREEZE_CHANGED')
        marker=read(self.marker)
        for path,expected in marker['hashes'].items():
            if sha(path)!=expected:raise ValueError('ROUND_INPUT_CHANGED:'+path)
        for path,expected in marker['asset_stats'].items():
            stat=Path(path).stat()
            if dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)!=expected:raise ValueError('LOCAL_MODEL_ASSET_CHANGED')
            if strong_assets and sha(path)!=marker['asset_hashes'][path]:raise ValueError('LOCAL_MODEL_ASSET_HASH_CHANGED')
        for database in marker['databases']:
            if any(Path(database+suffix).exists() for suffix in ('-wal','-journal')):raise ValueError('SQLITE_SIDE_FILE_PRESENT')
        assert_off_peak(read(self.dependencies['configuration']));return marker
    def public_cases(self):self.verify();return iter(cases())


def token_preflight():
    config=configuration();write(ROOT/'config-token-preflight.json',config)
    counter=OfficialTokenCounter(config['tokenizer_directory']);requests=[]
    class NeverCall:
        def complete(self,request):
            payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1]);assert 'nodes' not in payload and 'directory' in payload
            assert set(payload)<={'question','workspace_id','as_of','directory','max_hops','query_time'}
            requests.append(dict(tokens=counter(request),bytes=len(json.dumps(request.to_dict(),ensure_ascii=False).encode()),question=payload['question']))
            raise RuntimeError('LOCAL_TOKEN_COUNT_ONLY_NO_MODEL_CALL')
    class NoCost:
        price=type('Price',(),dict(model_id=config['model_id']))();stopped=False;exhaustions=0
        def wrap(self,adapter,**kw):return adapter
    inputs={g:Inputs(g,draft=True) for g in ('D','D2')}
    routes={g:TemporalGraphDevRoutes(i,NoCost(),lambda cfg:NeverCall(),ROOT/'token-preflight-routes',preflight=False) for g,i in inputs.items()}
    for identifier,group,public in cases():
        before=len(requests);routes['D2' if group=='D2' else 'D'].graph_only(deepcopy(public))
        if len(requests)!=before+1:raise ValueError('MISSING_PREFLIGHT_REQUEST:'+identifier)
        requests[-1].update(id=identifier,group=group)
    result=dict(no_model_calls=True,request_count=len(requests),max_tokens=max(r['tokens'] for r in requests),max_bytes=max(r['bytes'] for r in requests),rows=requests)
    if result['max_tokens']>6144 or result['max_bytes']+1024>30720:raise ValueError('PLANNING_INPUT_CAP_EXCEEDED')
    write(ROOT/'token-preflight.json',result);return result


def seal():
    if (ROOT/'config.json').exists() or (ROOT/'results').exists() or (ROOT/'budget.json').exists():raise ValueError('ONE_FINAL_ROUND_NO_RERUN')
    if not read(ROOT/'token-preflight.json')['no_model_calls']:raise ValueError('LOCAL_PREFLIGHT_REQUIRED')
    config=configuration();assert_off_peak(config)
    for p,expected in read('work/graph-opt-r3/scorer-v2-freeze.json')['hashes'].items():
        if sha(p)!=expected:raise ValueError('ORIGINAL_SCORER_FREEZE_CHANGED')
    write(ROOT/'config.json',config)
    source_files=sorted(set(Path('src').rglob('*.py'))|set(Path('tests/integration').glob('*.py'))|{Path('tests/meeting_graph_eval_support.py'),Path('tests/graph_eval_support.py'),Path('tests/unit/test_graph_mentions.py'),Path('tests/unit/test_graph_mention_final_controls.py')}|{Path(p) for p in read(ROOT/'fixture-vocabulary-scan.json')['source_hashes']})
    archive=ROOT/'source.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in source_files:z.write(p,p.as_posix())
    frozen=['work/graph-eval-questions.json','work/graph-eval-freeze.json','work/graph-eval-fixture-bindings.json','work/graph-eval-holdout-D2.json','work/graph-eval-holdout-D2-json-freeze.json','work/graph-holdout-D2/assertions.json','work/graph-holdout-D2/confirmation-index.json','work/graph-eval-confirmations-v1.json','work/graph-opt-r3/results-r2/report.json','work/graph-opt-r3/runtime-r2.json','work/graph-opt-r3/source-r2.zip','work/graph-opt-r3/scorer-v2-freeze.json','work/graph-opt-r3/public-fixtures.json']
    own_files=[ROOT/p for p in ['config.json','local-models.json','local-preflight.json','calibration-mentions.json','calibration-annotations-freeze.json','calibration-local-scores.json','calibration-result.json','calibration-result-v2.json','fixture-vocabulary-scan.json','token-preflight.json','environment.json']]
    files=source_files+[Path(p) for p in frozen+LEDGERS]+own_files+[archive]+[Path('work/graph-eval-tokenizer')/p for p in ['source.json','encoding.py','tokenizer.json']]
    databases=[Path('work/L6-eval-dev-v1.sqlite').resolve(),Path('work/graph-holdout-D2/confirmed.sqlite').resolve()];files+=databases
    resources=config['local_models'];assets=[p for p in Path(resources['embedding_model_dir']).rglob('*') if p.is_file()]+[Path(resources['rerank_onnx_path']),Path(resources['rerank_manifest'])]+[p for p in Path(resources['rerank_model_dir']).iterdir() if p.is_file() and p.suffix in {'.json','.txt','.model'}]
    marker=dict(schema='L6-final-mention-freeze/v1',frozen_at_utc=datetime.now(UTC).isoformat(),hashes={str(p.resolve()):sha(p) for p in files},databases=[str(p) for p in databases],asset_hashes={str(p.resolve()):sha(p) for p in assets},asset_stats={str(p.resolve()):dict(size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in assets},protocol=dict(dev_groups=['D','D2','B_MERIDIAN'],repetitions=3,single_round=True,no_expected_in_provider=True,scorer_v2_unchanged=True,D3_accessed=False,model_assets_full_hash_check='before and after matrix, stat check before every route/request'))
    write(ROOT/'runtime-freeze.json',marker);Inputs('D').verify(strong_assets=True);return marker


def run():
    inputs={g:Inputs(g) for g in ('D','D2')};marker=inputs['D'].verify(strong_assets=True);config=read(inputs['D'].dependencies['configuration'])
    previous_reserved=sum(read(p)['reserved_usd'] for p in LEDGERS);remaining=1.6-previous_reserved;cap=(120*6144*.15+120*1024*.6)/1e6
    if cap>remaining*.8:raise BudgetExceeded('FULL_MATRIX_EXCEEDS_REMAINING_BUDGET')
    from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
    legacy_hits=sum(int(any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in [p['question']]+(['As of '+p['as_of']+', '+p['question']] if p.get('as_of') else []))) for i,g,p in cases())
    if legacy_hits:raise ValueError('LEGACY_CALL_PREFLIGHT_CHANGED')
    key=os.environ.get('DEEPSEEK_API_KEY','')
    if not key:raise ValueError('CONFIGURED_API_KEY_UNAVAILABLE')
    output=ROOT/'results';output.mkdir(exist_ok=False)
    write(output/'preflight.json',dict(stage_cap_usd=1.6,additional_authorized_usd=.6,previous_reserved_usd=previous_reserved,remaining_before_usd=remaining,cost_upper_usd=cap,max_calls=120,graph_executions=120,legacy_executions=120,legacy_template_hits=legacy_hits))
    price=PriceReference(config['model_id'],.15,.6,config['price_checked_on'],config['official_price_url']);budget=CampaignBudget(ROOT/'budget.json',price,validate_inputs=inputs['D'].verify,max_usd=remaining);budget.preflight(120*6144,120*1024)
    os.environ.pop('OPENAI_LOG',None);logging.getLogger('openai').setLevel(logging.WARNING);logging.getLogger('httpx').setLevel(logging.WARNING)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    from linkloom.temporal_graph.entity_locator import LocalMentionLocator,LocatorConfig
    from linkloom.indexing import SentenceTransformerEmbedder
    local=config['local_models'];locator=LocalMentionLocator(LocatorConfig(**config['locator']),embedder=SentenceTransformerEmbedder(local['embedding_model_dir'],local_files_only=True),model_dir=local['rerank_model_dir'],onnx_path=local['rerank_onnx_path'])
    warm_start=perf_counter();locator.prepare();warm_ms=(perf_counter()-warm_start)*1000;inputs['D'].verify(strong_assets=True)
    write(output/'local-warmup.json',dict(elapsed_ms=warm_ms,scope='separate from per-question latency; no paid model calls'))
    calls=output/'provider-calls';calls.mkdir();sequence=[0]
    def factory(configuration):
        inner=adapter_factory(configuration)
        class Capture:
            capability=inner.capability
            def complete(self,request):
                if sequence[0]>=120:raise BudgetExceeded('SINGLE_ROUND_CALL_LIMIT')
                payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1])
                if set(payload)!={'question','workspace_id','as_of','directory','max_hops','query_time'}:raise ValueError('MODEL_PAYLOAD_ALLOWLIST')
                sequence[0]+=1;started=datetime.now(UTC).isoformat();response=inner.complete(request)
                encoded=json.dumps(dict(request_started_at_utc=started,request_completed_at_utc=datetime.now(UTC).isoformat(),request=request.to_dict(),response=response.to_dict()),ensure_ascii=False,indent=2)
                if key in encoded:raise ValueError('SECRET_LITERAL_REFUSED')
                with (calls/f'{sequence[0]:03}.json').open('x',encoding='utf8') as f:f.write(encoded)
                return response
        return Capture()
    routes={g:TemporalGraphDevRoutes(i,budget,factory,output/'routes',preflight=False,mention_locator=locator) for g,i in inputs.items()};rows=[];failure=None
    print(json.dumps(dict(single_round=True,remaining_before_usd=remaining,cost_upper_usd=cap,local_ready=True)),flush=True)
    try:
        for repeat in (1,2,3):
            for identifier,group,public in cases():
                selected=routes['D2' if group=='D2' else 'D']
                for route in (('legacy_only','graph_only') if repeat%2 else ('graph_only','legacy_only')):
                    inputs['D'].verify();first=len(budget.rows);before=budget.reserved;started=perf_counter()
                    try:actual=selected.routes()[route](deepcopy(public))
                    except BudgetExceeded as error:failure=str(error);break
                    except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                    inputs['D'].verify();paid=budget.rows[first:]
                    rows.append(dict(id=identifier,group=group,route=route,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(x['actual_usd'] or 0 for x in paid),unknown_usage_calls=sum(x['actual_usd'] is None for x in paid)))
                    save(output/'unscored.json',dict(rows=rows,failure=failure))
                    if route=='graph_only':print(json.dumps(dict(id=identifier,repeat=repeat,status=actual['status'],stage_reserved=previous_reserved+budget.reserved)),flush=True)
                if failure:break
            if failure:break
        inputs['D'].verify(strong_assets=True)
    finally:locator.close()
    # Frozen scorers and expected fields are read only after the complete run.
    from graph_eval_date_score import DateParityScorer
    from graph_acceptance_v2 import AcceptanceEvaluator
    data=read('work/graph-eval-questions.json');expected={q['id']:q['expected'] for q in data['questions']};scorer=DateParityScorer(data['fixtures'],read('work/graph-eval-confirmations-v1.json'))
    h=read('work/graph-eval-holdout-D2.json');expected.update({q['id']:q['expected'] for q in h['questions']});h_scorer=AcceptanceEvaluator(next(iter(h['fixtures'].values())),read('work/graph-holdout-D2/confirmation-index.json'));assertions={r['id']:r['assertions'] for r in read('work/graph-holdout-D2/assertions.json')['rows']};publics={i:p for i,g,p in cases()};fixtures={f['case_id']:f for f in read(inputs['D'].dependencies['public_fixture'])['cases']}
    for row in rows:
        if row['group']=='D2':row['score']=h_scorer(row['actual'],assertions[row['id']])
        else:
            public=publics[row['id']];fixture=fixtures[public['fixture_id']];documents=[d for d in fixture['documents'] if d['workspace_id']==fixture['workspace_id']]
            row['score']=scorer(row['id'],row['actual'],expected[row['id']],public,documents)
        row['unknown_positive_answer']=unknown_positive(row['actual'],expected[row['id']])
    graph=[r for r in rows if r['route']=='graph_only'];aggregate=summarize(rows)
    metrics={a['group']:a['metrics'] for a in aggregate['aggregate'] if a['route']=='graph_only'}
    fabricated=sum(r['unknown_positive_answer'] for r in graph);errors=sum(r['score']['error_relations'] for r in graph)
    result=dict(schema='L6-final-mention-round/v1',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,round=1,single_authorized_round=True,D2_split='dev',D3_accessed=False,rows=rows,**aggregate,comparison=compare(rows,read('work/graph-opt-r3/results-r2/report.json')['rows']) if not failure else None,stop_reason='USER_FINAL_SINGLE_ROUND',target_met=not failure and len(rows)==240 and all(metrics[g]['accuracy']['mean']>=.75 for g in ('D','D2')) and not errors and not fabricated,unknown_positive_answers=fabricated,error_relations_total=errors,H10_results=[dict(repeat=r['repeat'],status=r['actual']['status'],positive=r['unknown_positive_answer'],correct=r['score']['correct']) for r in graph if r['id']=='H10'],stage_cap_usd=1.6,stage_reserved_usd=previous_reserved+budget.reserved,stage_remaining_usd=remaining-budget.reserved,new_reserved_usd=budget.reserved,new_known_usage_estimate_usd=sum(x['actual_usd'] or 0 for x in budget.rows),unknown_usage_calls=sum(x['actual_usd'] is None for x in budget.rows),provider_calls=sequence[0],configuration=config,runtime_sha256=sha(ROOT/'runtime-freeze.json'),vocabulary_scan_sha256=sha(ROOT/'fixture-vocabulary-scan.json'),scorer_v2_freeze_sha256=sha('work/graph-opt-r3/scorer-v2-freeze.json'))
    write(output/'report.json',result);inputs['D'].verify(strong_assets=True);return result
