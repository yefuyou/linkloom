"""One DEV campaign followed by one sealed D3 campaign; no result-driven edits."""
from copy import deepcopy
from collections import Counter
from datetime import UTC,datetime,timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter
import argparse,hashlib,json,logging,os,sqlite3,sys,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from run_temporal_graph_dev import CampaignBudget,PriceReference,BudgetExceeded,summarize
from run_graph_continuation import cases as dev_cases,compare,unknown_positive
from temporal_graph_routes import TemporalGraphDevRoutes
from deepseek_eval_transport import adapter_factory,assert_off_peak,OfficialTokenCounter
ROOT=Path('work/graph-contract-repair')
BASELINE=Path('work/graph-mention-final')
CAP_USD=1.55
D3=Path('work/graph-eval-holdout-D3.json')
D3_BYTES=10224
D3_SHA='0ea2b62dba1267f48f40c74a917772f41e4c8edfff4776073a0444f43063dc1d'


def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):
    with Path(p).open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def require(ok,reason):
    if not ok:raise ValueError(reason)


def scan_vocabulary(output,*,extra_terms=(),D3_accessed=False):
    """Use only the prior frozen DEV vocabulary, never open the D3 file."""
    prior=read(BASELINE/'fixture-vocabulary-scan.json');terms=sorted(set(prior['terms'])|set(extra_terms))
    import re
    with zipfile.ZipFile(BASELINE/'source.zip') as z:before={n:z.read(n).decode('utf8') for n in z.namelist() if n.startswith('src/') or n=='.env.example'}
    files=[p for p in Path('src').rglob('*') if p.is_file() and p.suffix in {'.py','.js','.ts','.html','.css'}]+[Path('.env.example')];hits=[]
    for p in files:
        for term in terms:
            pattern=re.compile(r'(?<![A-Za-z0-9_])'+re.escape(term)+r'(?![A-Za-z0-9_])',re.I) if term.isascii() else re.compile(re.escape(term))
            for number,line in enumerate(p.read_text(encoding='utf8').splitlines(),1):
                if pattern.search(line):hits.append(dict(file=p.as_posix(),line=number,term=term,text=line.strip(),present_in_baseline=bool(pattern.search(before.get(p.as_posix(),'')))))
    result=dict(schema='L6-contract-product-vocabulary/v1',D3_accessed=D3_accessed,terms=terms,file_count=len(files),hits=hits,new_hits=[h for h in hits if not h['present_in_baseline']],source_hashes={p.as_posix():sha(p) for p in files},baseline_sha256=sha(BASELINE/'fixture-vocabulary-scan.json'))
    write(output,result);return result


class Inputs:
    def __init__(self,split,group=None,*,draft=False):
        self.split=split;self.group=group;self.draft=draft;self.marker=ROOT/'runtime-freeze.json';self.marker_sha=sha(self.marker) if self.marker.exists() else None
        self.rules=ROOT/'D3/rules-freeze.json';self.rules_sha=sha(self.rules) if split=='D3' and self.rules.exists() else None
        self.execution_marker=ROOT/'D3/execution-freeze.json';self.execution_sha=sha(self.execution_marker) if split=='D3' and self.execution_marker.exists() else None
        self.dev_result_marker=ROOT/'DEV/results-freeze.json';self.dev_result_sha=sha(self.dev_result_marker) if self.dev_result_marker.exists() else None
        self.dependencies=dict(configuration=(ROOT/'config.json').resolve(),public_fixture=Path('work/graph-opt-r3/public-fixtures.json').resolve() if split=='DEV' else (ROOT/'D3/public-fixture.json').resolve(),confirmed_database=Path('work/graph-holdout-D2/confirmed.sqlite' if group=='D2' else 'work/L6-eval-dev-v1.sqlite').resolve() if split=='DEV' else (ROOT/'D3/confirmed.sqlite').resolve())
    def verify(self,strong_assets=False):
        if self.draft:return {}
        require(sha(self.marker)==self.marker_sha,'BASE_FREEZE_CHANGED');marker=read(self.marker)
        for path,value in marker['hashes'].items():require(sha(path)==value,'FROZEN_CODE_OR_INPUT_CHANGED:'+path)
        for path,expected in marker['asset_stats'].items():
            stat=Path(path).stat();require(dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)==expected,'MODEL_ASSET_STAT_CHANGED')
            if strong_assets:require(sha(path)==marker['asset_hashes'][path],'MODEL_ASSET_HASH_CHANGED')
        for database in marker['databases']+[str(self.dependencies['confirmed_database'])]:require(not any(Path(database+s).exists() for s in ('-wal','-journal')),'SQLITE_SIDE_FILE')
        if self.split=='D3':
            require(sha(self.rules)==self.rules_sha,'D3_RULES_SEAL_CHANGED');rules=read(self.rules)
            require(rules['final_configuration_sha256']==sha(self.dependencies['configuration']),'D3_FINAL_CONFIGURATION_CHANGED')
            require(rules['source_freeze_sha256']==self.marker_sha,'D3_FINAL_SOURCE_CHANGED')
            for path,value in rules['hashes'].items():require(sha(path)==value,'D3_SEALED_INPUT_CHANGED:'+path)
            if self.execution_sha is not None:
                require(sha(self.execution_marker)==self.execution_sha,'D3_EXECUTION_SEAL_CHANGED')
                for path,value in read(self.execution_marker)['hashes'].items():require(sha(path)==value,'D3_EXECUTION_INPUT_CHANGED:'+path)
        if self.dev_result_sha is not None:
            require(sha(self.dev_result_marker)==self.dev_result_sha,'DEV_RESULTS_SEAL_CHANGED')
            for path,value in read(self.dev_result_marker)['hashes'].items():require(sha(path)==value,'DEV_RESULTS_OR_BUDGET_CHANGED:'+path)
        assert_off_peak(read(self.dependencies['configuration']));return marker
    def public_cases(self):
        self.verify()
        if self.split=='DEV':return iter(dev_cases())
        # This file has no expected fields; assertions are loaded only after run.
        return iter(read(ROOT/'D3/public-questions.json')['cases'])


class Routes(TemporalGraphDevRoutes):
    def __init__(self,inputs,budget,factory,output,*,mention_locator=None):
        if inputs.split=='DEV':super().__init__(inputs,budget,factory,output,preflight=False,mention_locator=mention_locator);return
        inputs.verify();self.inputs=inputs;self.budget=budget;self.factory=factory;self.output=Path(output);self.mention_locator=mention_locator
        self.config=read(inputs.dependencies['configuration']);public=read(inputs.dependencies['public_fixture'])
        require(public['split']=='holdout' and public['dataset_sha256']==D3_SHA,'SEALED_HOLDOUT_REQUIRED')
        self.fixtures={c['case_id']:deepcopy(c) for c in public['cases']};require(len(self.fixtures)==len(public['cases']),'DUPLICATE_FIXTURE')
        self.database=inputs.dependencies['confirmed_database'];self.max_input=self.config['max_input_bytes'];self.max_output=self.config['max_output_tokens'];self.legacy_turns=self.config['legacy_max_turns'];self.upper_bound=None


def token_preflight(split,*,draft=False):
    config=read(ROOT/'config.json');counter=OfficialTokenCounter(config['tokenizer_directory']);requests=[]
    class NeverCall:
        def complete(self,request):
            payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1]);require(set(payload)=={'question','workspace_id','as_of','directory','max_hops','query_time'},'PUBLIC_PAYLOAD_ONLY')
            requests.append(dict(tokens=counter(request),bytes=len(json.dumps(request.to_dict(),ensure_ascii=False).encode()),question=payload['question']));raise RuntimeError('NO_MODEL_CALL_LOCAL_TOKEN_COUNT_ONLY')
    class NoCost:
        price=type('Price',(),dict(model_id=config['model_id']))();stopped=False;exhaustions=0
        def wrap(self,adapter,**kw):return adapter
    inputs={g:Inputs(split,g,draft=draft) for g in (('D','D2') if split=='DEV' else ('D3',))}
    routes={g:Routes(i,NoCost(),lambda cfg:NeverCall(),ROOT/'token-preflight-routes') for g,i in inputs.items()}
    selected=list(dev_cases()) if split=='DEV' else read(ROOT/'D3/public-questions.json')['cases']
    for identifier,group,public in selected:
        before=len(requests);routes['D2' if group=='D2' else 'D' if split=='DEV' else 'D3'].graph_only(deepcopy(public))
        require(len(requests)==before+1,'MISSING_PREFLIGHT_REQUEST:'+identifier);requests[-1].update(id=identifier,group=group)
    result=dict(no_model_calls=True,max_tokens=max(x['tokens'] for x in requests),max_bytes=max(x['bytes'] for x in requests),rows=requests)
    require(result['max_tokens']<=6144 and result['max_bytes']+1024<=30720,'PLANNING_INPUT_CAP_EXCEEDED')
    write(ROOT/('token-preflight-dev.json' if split=='DEV' else 'D3/token-preflight.json'),result);return result


def prepare_dev():
    require(not (ROOT/'config.json').exists() and not (ROOT/'DEV').exists(),'ONE_DEV_CAMPAIGN_ONLY')
    config=deepcopy(read(BASELINE/'config.json'));now=datetime.now(UTC)
    # Billing/split metadata changes only. Identity thresholds, Host clock,
    # planner limits, model and all generation settings are byte-equivalent.
    config.update(stage_cap_usd=CAP_USD,stage_budget_increase_usd=0,previous_budget_ledgers=[],contract_repair=True,max_new_rounds=1,D3_accessed=False,price_checked_on=now.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date().isoformat(),pricing_rechecked_at_utc=now.isoformat(),pricing_valid_until_utc=(now+timedelta(hours=2)).isoformat())
    for key in ('locator','model_id','model_version_official','effective_generation_parameters','temperature','max_output_tokens','graph_limits','graph_alignment_review','graph_max_provider_calls','graph_mention_grounding','query_time_fixed'):
        require(config[key]==read(BASELINE/'config.json')[key],'PARAMETER_CHANGE:'+key)
    assert_off_peak(config);write(ROOT/'config.json',config);scan_vocabulary(ROOT/'fixture-vocabulary-scan.json');return token_preflight('DEV',draft=True)


def seal():
    require(not (ROOT/'runtime-freeze.json').exists() and not (ROOT/'DEV').exists(),'ONE_FREEZE_ONLY')
    require(read(ROOT/'token-preflight-dev.json')['no_model_calls'],'PREFLIGHT_REQUIRED')
    files=sorted(set(Path('src').rglob('*.py'))|set(Path('tests').rglob('*.py'))|{Path(p) for p in read(ROOT/'fixture-vocabulary-scan.json')['source_hashes']}|{Path('pyproject.toml')})
    with zipfile.ZipFile(ROOT/'source.zip','x',zipfile.ZIP_DEFLATED) as archive:
        for path in files:archive.write(path,path.as_posix())
    files += [ROOT/p for p in ['source.zip','config.json','token-preflight-dev.json','fixture-vocabulary-scan.json','before-hashes.json']]
    files += [Path(p) for p in ['work/graph-eval-questions.json','work/graph-eval-freeze.json','work/graph-eval-fixture-bindings.json','work/graph-eval-holdout-D2.json','work/graph-eval-holdout-D2-json-freeze.json','work/graph-eval-confirmations-v1.json','work/graph-holdout-D2/assertions.json','work/graph-holdout-D2/confirmation-index.json','work/graph-opt-r3/public-fixtures.json','work/graph-opt-r3/scorer-v2-freeze.json']]
    files += [BASELINE/p for p in ['results/report.json','runtime-freeze.json','source.zip','config.json','calibration-result-v2.json','environment.json']]
    files += [Path('work/graph-eval-tokenizer')/p for p in ['source.json','encoding.py','tokenizer.json']]
    databases=['work/L6-eval-dev-v1.sqlite','work/graph-holdout-D2/confirmed.sqlite'];files += [Path(p) for p in databases]
    old=read(BASELINE/'runtime-freeze.json')
    for p,value in old['asset_hashes'].items():require(sha(p)==value,'LOCAL_MODEL_CHANGED')
    for p,value in read('work/graph-opt-r3/scorer-v2-freeze.json')['hashes'].items():require(sha(p)==value,'FROZEN_SCORER_CHANGED')
    marker=dict(schema='L6-contract-source-freeze/v1',frozen_at_utc=datetime.now(UTC).isoformat(),hashes={str(p.resolve()):sha(p) for p in files},databases=[str(Path(p).resolve()) for p in databases],asset_hashes=old['asset_hashes'],asset_stats=old['asset_stats'],protocol=dict(DEV_then_D3=True,one_each=True,repetitions=3,source_unchanged_after_DEV=True,thresholds_unchanged=True,model_parameters_unchanged=True,D3_not_read_before_DEV=True,combined_cap_usd=CAP_USD))
    write(ROOT/'runtime-freeze.json',marker);Inputs('DEV','D').verify(strong_assets=True);return marker


def prepare_d3():
    Inputs('DEV','D').verify(strong_assets=True);dev=read(ROOT/'DEV/report.json');require(dev['status']=='COMPLETE' and len(dev['rows'])==240,'COMPLETE_DEV_REQUIRED')
    require(not (ROOT/'D3').exists(),'ONE_D3_PREPARATION_ONLY');scan=read(ROOT/'pre-D3-vocabulary-scan.json')
    for path,value in scan['source_hashes'].items():require(sha(path)==value,'CODE_CHANGED_AFTER_VOCABULARY_NOTICE')
    # Only here, after the caller reports the scan, may D3 bytes be read.
    raw=D3.read_bytes();require(len(raw)==D3_BYTES and hashlib.sha256(raw).hexdigest()==D3_SHA,'D3_RAW_BYTES_OR_SHA_MISMATCH_STOP')
    write('work/graph-eval-holdout-D3-freeze.json',dict(schema='external-D3-freeze/v1',bytes=len(raw),sha256=D3_SHA,path=str(D3.resolve()),confirmation_label='外部起草，人工确认',frozen_at_utc=datetime.now(UTC).isoformat()))
    data=json.loads(raw);root=ROOT/'D3';root.mkdir();write(root/'dataset-path.json',dict(path=str(D3.resolve())))
    from tests.meeting_graph_eval_support import build_meetings,no_gold
    fixtures=deepcopy(data['fixtures']);no_gold(fixtures);require(len(fixtures)==1,'ONE_HOLDOUT_FIXTURE_REQUIRED');key,fixture=next(iter(fixtures.items()))
    index=build_meetings(fixture,root/'confirmed.sqlite',recorded_at='2026-11-03T00:00:00+08:00');index['sources']={}
    db=sqlite3.connect((root/'confirmed.sqlite').resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        for meeting in fixture['meetings']:
            found=db.execute('SELECT source_id,current_version_id FROM ll_source WHERE workspace_id=? AND name=?',(index['workspace_id'],meeting['name'])).fetchall();require(len(found)==1,'UNIQUE_MEETING_SOURCE_REQUIRED');row=found[0]
            content=db.execute('SELECT content FROM ll_source_version WHERE workspace_id=? AND artifact_version_id=?',(index['workspace_id'],row['current_version_id'])).fetchone()[0];require(content=='\n'.join(meeting['lines']),'SOURCE_BYTES_CHANGED')
            index['sources'][meeting['id']]=dict(source_id=row['source_id'],source_version_id=row['current_version_id'])
        index['task_events']={r['id']:r['task_id'] for r in db.execute('SELECT id,task_id FROM task_event WHERE workspace_id=?',(index['workspace_id'],))}
    finally:db.close()
    public=dict(split='holdout',dataset_sha256=D3_SHA,confirmation_label='模拟人工确认',cases=[dict(case_id=key,workspace_id=fixture['workspace'],confirmed_workspace_id=index['workspace_id'],documents=[dict(workspace_id=fixture['workspace'],path=m['id']+'.md',content='\n'.join(m['lines'])) for m in fixture['meetings']])]);write(root/'public-fixture.json',public);write(root/'confirmation-index.json',index)
    from linkloom.temporal_graph.store import SQLiteGraphStore
    from tests.meeting_graph_eval_support import task_candidate_source
    store=SQLiteGraphStore(root/'confirmed.sqlite',authorized_workspace=index['workspace_id'],authorize_source=lambda ws,s,v:ws==index['workspace_id'],task_candidate_source=task_candidate_source())
    vocabulary={key}|{q['id'] for q in data['questions']}
    for node in store.load_edges(index['workspace_id']).nodes:
        if node.kind=='task_observation':continue
        if len(node.label)>=2:vocabulary.add(node.label)
        if node.kind=='decision_record' and ': ' in node.label:vocabulary.add(node.label.split(': ',1)[0])
    scan_vocabulary(root/'fixture-vocabulary-scan.json',extra_terms=vocabulary,D3_accessed=True)
    qs=[(q['id'],'D3',dict(question=q['question'],fixture_id=q['fixture'],as_of=q.get('as_of'))) for q in data['questions']];require(len({q[0] for q in qs})==len(qs),'DUPLICATE_QUESTION_ID');write(root/'public-questions.json',dict(cases=qs))
    from holdout_graph_acceptance import compile_expected
    assertions=dict(schema='generic-holdout-assertions/v1',source='expected fields only; no system outputs',rows=[dict(id=q['id'],assertions=compile_expected(q['expected'])) for q in data['questions']]);write(root/'assertions.json',assertions)
    table=['# D3 冻结断言（仅由 expected 编译）','','|题号|断言类型|具体断言|','|---|---|---|']
    for row in assertions['rows']:
        for rule in row['assertions']:table.append('|'+row['id']+'|'+rule['op']+'|'+json.dumps(rule,ensure_ascii=False).replace('|','\\|')+'|')
    (root/'assertions.md').write_text('\n'.join(table)+'\n',encoding='utf8')
    sealed=[ROOT/'DEV/results-freeze.json',ROOT/'DEV/report.json',ROOT/'budget-dev.json',D3,Path('work/graph-eval-holdout-D3-freeze.json'),root/'dataset-path.json',root/'assertions.json',root/'assertions.md',root/'fixture-vocabulary-scan.json',root/'public-fixture.json',root/'confirmation-index.json',root/'public-questions.json',root/'confirmed.sqlite',ROOT/'pre-D3-vocabulary-scan.json',Path('tests/integration/holdout_graph_acceptance.py'),Path('tests/integration/graph_acceptance_v2.py')]
    write(root/'rules-freeze.json',dict(schema='D3-rules-freeze/v1',frozen_before_execution=True,assertions_derived_only_from_expected=True,final_configuration_sha256=sha(ROOT/'config.json'),source_freeze_sha256=sha(ROOT/'runtime-freeze.json'),hashes={str(p.resolve()):sha(p) for p in sealed}))
    result=token_preflight('D3')
    write(root/'execution-freeze.json',dict(schema='D3-execution-freeze/v1',frozen_at_utc=datetime.now(UTC).isoformat(),hashes={str(p.resolve()):sha(p) for p in [root/'rules-freeze.json',root/'token-preflight.json']}))
    return result


def score_rows(split,rows):
    if split=='DEV':
        from graph_eval_date_score import DateParityScorer
        from graph_acceptance_v2 import AcceptanceEvaluator
        data=read('work/graph-eval-questions.json');h=read('work/graph-eval-holdout-D2.json');expected={q['id']:q['expected'] for q in data['questions']+h['questions']};scorer=DateParityScorer(data['fixtures'],read('work/graph-eval-confirmations-v1.json'));hs=AcceptanceEvaluator(next(iter(h['fixtures'].values())),read('work/graph-holdout-D2/confirmation-index.json'));assertions={r['id']:r['assertions'] for r in read('work/graph-holdout-D2/assertions.json')['rows']};publics={i:p for i,g,p in dev_cases()};fixtures={f['case_id']:f for f in read('work/graph-opt-r3/public-fixtures.json')['cases']}
        for row in rows:
            if row['group']=='D2':row['score']=hs(row['actual'],assertions[row['id']])
            else:
                public=publics[row['id']];fixture=fixtures[public['fixture_id']];documents=[d for d in fixture['documents'] if d['workspace_id']==fixture['workspace_id']];row['score']=scorer(row['id'],row['actual'],expected[row['id']],public,documents)
            row['unknown_positive_answer']=unknown_positive(row['actual'],expected[row['id']])
    else:
        from graph_acceptance_v2 import AcceptanceEvaluator
        data=read(D3);hs=AcceptanceEvaluator(next(iter(data['fixtures'].values())),read(ROOT/'D3/confirmation-index.json'));assertions={r['id']:r['assertions'] for r in read(ROOT/'D3/assertions.json')['rows']};expected={q['id']:q['expected'] for q in data['questions']}
        for row in rows:row['score']=hs(row['actual'],assertions[row['id']]);row['unknown_positive_answer']=unknown_positive(row['actual'],expected[row['id']])



def matrix_budget(count,legacy_hits,config,previous_reserved):
    require(0<=previous_reserved<CAP_USD,'COMBINED_BUDGET_EXHAUSTED')
    remaining=CAP_USD-previous_reserved;allocation=min(1.0,remaining)
    max_calls=3*(count+legacy_hits*config['legacy_max_turns'])
    upper=(max_calls*6144*.15+max_calls*1024*.6)/1e6
    require(upper<=allocation*.8,'FULL_MATRIX_OVER_BUDGET')
    return dict(remaining=remaining,allocation=allocation,max_calls=max_calls,upper=upper)

def run(split):
    if split=='D3':require((ROOT/'D3/execution-freeze.json').exists(),'D3_EXECUTION_SEAL_REQUIRED')
    config=read(ROOT/'config.json');inputs={g:Inputs(split,g) for g in (('D','D2') if split=='DEV' else ('D3',))};first=next(iter(inputs.values()));first.verify(strong_assets=True)
    cases=list(first.public_cases());count=len(cases);previous=0 if split=='DEV' else read(ROOT/'budget-dev.json')['reserved_usd']
    from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
    hits=sum(int(any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in [p['question']]+(['As of '+p['as_of']+', '+p['question']] if p.get('as_of') else []))) for _,_,p in cases)
    limits=matrix_budget(count,hits,config,previous);remaining,allocation,max_calls,upper=(limits[k] for k in ('remaining','allocation','max_calls','upper'))
    require(split=='D3' or (count==40 and hits==0),'FULL_DEV_MATRIX_OR_LEGACY_PREFLIGHT_CHANGED')
    require(read(ROOT/('token-preflight-dev.json' if split=='DEV' else 'D3/token-preflight.json'))['max_tokens']<=6144,'INPUT_TOKEN_CAP')
    key=os.environ.get('DEEPSEEK_API_KEY','');require(bool(key),'CONFIGURED_KEY_UNAVAILABLE')
    root=ROOT/split;root.mkdir(exist_ok=True);require(not (root/'run-started.json').exists() and not (root/'report.json').exists(),'ONE_RUN_PER_SPLIT_NO_RETRY')
    write(root/'preflight.json',dict(cap_usd=CAP_USD,previous_reserved_usd=previous,remaining_before_usd=remaining,full_upper_usd=upper,local_budget_cap_usd=allocation,max_calls=max_calls,graph_executions=3*count,legacy_executions=3*count,legacy_template_hits=hits));write(root/'run-started.json',dict(started_at_utc=datetime.now(UTC).isoformat(),source_freeze_sha256=sha(ROOT/'runtime-freeze.json'),configuration_sha256=sha(ROOT/'config.json'),D3_rules_sha256=first.rules_sha,D3_execution_sha256=first.execution_sha))
    ledger=ROOT/('budget-dev.json' if split=='DEV' else 'budget-D3.json');require(not ledger.exists(),'EXISTING_LEDGER_NO_RERUN')
    price=PriceReference(config['model_id'],.15,.6,config['price_checked_on'],config['official_price_url']);budget=CampaignBudget(ledger,price,validate_inputs=first.verify,max_usd=allocation);budget.preflight(max_calls*6144,max_calls*1024)
    os.environ.pop('OPENAI_LOG',None);logging.getLogger('openai').setLevel(logging.WARNING);logging.getLogger('httpx').setLevel(logging.WARNING)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
    from linkloom.temporal_graph.entity_locator import LocalMentionLocator,LocatorConfig
    from linkloom.indexing import SentenceTransformerEmbedder
    local=config['local_models'];locator=LocalMentionLocator(LocatorConfig(**config['locator']),embedder=SentenceTransformerEmbedder(local['embedding_model_dir'],local_files_only=True),model_dir=local['rerank_model_dir'],onnx_path=local['rerank_onnx_path']);started=perf_counter();locator.prepare();write(root/'local-warmup.json',dict(elapsed_ms=(perf_counter()-started)*1000,not_in_query_latency=True));first.verify(strong_assets=True)
    calls_dir=root/'provider-calls';calls_dir.mkdir(exist_ok=False);sequence=[0]
    def factory(cfg):
        inner=adapter_factory(cfg)
        class Capture:
            capability=inner.capability
            def complete(self,request):
                require(sequence[0]<max_calls,'CALL_LIMIT')
                if request.task_id=='graph-investigation':
                    payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1]);require(set(payload)=={'question','workspace_id','as_of','directory','max_hops','query_time'},'MODEL_PUBLIC_PAYLOAD_ONLY')
                    require(all(set(n)<={'kind','label','meeting_date','valid_from','valid_to','status'} for n in payload['directory']),'NO_DIRECTORY_HANDLES')
                sequence[0]+=1;start=datetime.now(UTC).isoformat();response=inner.complete(request);encoded=json.dumps(dict(request_started_at_utc=start,request_completed_at_utc=datetime.now(UTC).isoformat(),request=request.to_dict(),response=response.to_dict()),ensure_ascii=False,indent=2);require(key not in encoded,'SECRET_LITERAL_REFUSED')
                with (calls_dir/f'{sequence[0]:03}.json').open('x',encoding='utf8') as f:f.write(encoded)
                return response
        return Capture()
    routes={g:Routes(i,budget,factory,root/'routes',mention_locator=locator) for g,i in inputs.items()};rows=[];failure=None
    logger=logging.getLogger('linkloom.temporal_graph.meeting_query');handler=logging.FileHandler(root/'contract-normalizations.log',encoding='utf8');handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler);propagate=logger.propagate;logger.propagate=False
    print(json.dumps(dict(split=split,remaining_before_usd=remaining,full_upper_usd=upper,max_calls=max_calls)),flush=True)
    try:
        for repeat in [1,2,3]:
            for identifier,group,public in cases:
                selected=routes['D2' if group=='D2' else 'D' if split=='DEV' else 'D3']
                for route in (('legacy_only','graph_only') if repeat%2 else ('graph_only','legacy_only')):
                    first.verify();before=budget.reserved;offset=len(budget.rows);started=perf_counter()
                    try:actual=selected.routes()[route](deepcopy(public))
                    except BudgetExceeded as error:failure=str(error);break
                    except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                    first.verify();paid=budget.rows[offset:];rows.append(dict(id=identifier,group=group,route=route,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(c['actual_usd'] or 0 for c in paid),unknown_usage_calls=sum(c['actual_usd'] is None for c in paid)));save(root/'unscored.json',dict(rows=rows,failure=failure))
                    if route=='graph_only':print(json.dumps(dict(split=split,id=identifier,repeat=repeat,status=actual['status'],combined_reserved_usd=previous+budget.reserved)),flush=True)
                if failure:break
            if failure:break
        first.verify(strong_assets=True)
    finally:locator.close();logger.removeHandler(handler);logger.propagate=propagate;handler.close()
    score_rows(split,rows);graph=[r for r in rows if r['route']=='graph_only'];errors=sum(r['score']['error_relations'] for r in graph);positive=sum(r['unknown_positive_answer'] for r in graph)
    report=dict(schema='L6-contract-acceptance/v1',split=split,status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,runs=1,repetitions=3,rows=rows,**summarize(rows),provider_calls=sequence[0],error_relations_total=errors,unknown_positive_answers=positive,configuration_sha256=sha(ROOT/'config.json'),source_freeze_sha256=sha(ROOT/'runtime-freeze.json'),combined_cap_usd=CAP_USD,combined_reserved_usd=previous+budget.reserved,remaining_usd=CAP_USD-previous-budget.reserved,new_reserved_usd=budget.reserved,new_known_usage_estimate_usd=sum(c['actual_usd'] or 0 for c in budget.rows),unknown_usage_calls=sum(c['actual_usd'] is None for c in budget.rows),normalization_log_sha256=sha(root/'contract-normalizations.log'),D3_accessed=split=='D3',no_retries=True,no_result_driven_changes=True,stop_reason='REPORT_DEV_THEN_D3' if split=='DEV' else 'FINAL_D3_STOP')
    if split=='DEV':report['comparison']=compare(rows,read(BASELINE/'results/report.json')['rows']) if not failure else None
    else:report.update(dataset_sha256=D3_SHA,rules_freeze_sha256=sha(ROOT/'D3/rules-freeze.json'))
    write(root/'report.json',report);first.verify(strong_assets=True)
    result_files=[root/'report.json',ledger,root/'unscored.json',root/'contract-normalizations.log',root/'preflight.json',root/'run-started.json']+sorted(calls_dir.glob('*.json'))
    write(root/'results-freeze.json',dict(schema='contract-results-freeze/v1',hashes={str(p.resolve()):sha(p) for p in result_files},configuration_sha256=sha(ROOT/'config.json'),source_freeze_sha256=sha(ROOT/'runtime-freeze.json')))
    Inputs(split,first.group).verify(strong_assets=True);return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare-dev','token-preflight-dev','seal','run-dev','scan-before-D3','prepare-D3','run-D3']);args=parser.parse_args()
    if args.action=='prepare-dev':result=prepare_dev()
    elif args.action=='token-preflight-dev':
        require(not (ROOT/'runtime-freeze.json').exists() and not (ROOT/'DEV').exists(),'PREFLIGHT_BEFORE_FREEZE_ONLY');result=token_preflight('DEV',draft=True)
    elif args.action=='seal':result=seal()
    elif args.action=='scan-before-D3':
        Inputs('DEV','D').verify();require(read(ROOT/'DEV/report.json')['status']=='COMPLETE','COMPLETE_DEV_REQUIRED');result=scan_vocabulary(ROOT/'pre-D3-vocabulary-scan.json')
    elif args.action=='prepare-D3':result=prepare_d3()
    else:result=run('DEV' if args.action=='run-dev' else 'D3')
    print(json.dumps({k:v for k,v in result.items() if k not in {'rows','hashes','asset_hashes','asset_stats','aggregate','per_run','comparison','source_hashes','hits','terms'}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
