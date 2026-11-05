"""One product-isolated D3 exception; no changes to frozen product/scorer/DEV."""
from copy import deepcopy
from datetime import UTC,datetime,timedelta
from pathlib import Path
from time import perf_counter
import argparse,json,logging,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import run_graph_contract_acceptance as frozen
from run_temporal_graph_dev import CampaignBudget,PriceReference,BudgetExceeded,summarize
from deepseek_eval_transport import adapter_factory,OfficialTokenCounter,assert_off_peak
from graph_acceptance_extensions import compile_expected,AcceptanceEvaluator
ROOT=frozen.ROOT;H=ROOT/'D3'
ALLOWED_CONTEXT_FIELDS={'query_time_fixed','price_checked_on','pricing_rechecked_at_utc','pricing_valid_until_utc'}
read,sha,write,save,require=frozen.read,frozen.sha,frozen.write,frozen.save,frozen.require


def check_baseline(strong=False):
    marker=read(ROOT/'runtime-freeze.json')
    for path,value in marker['hashes'].items():require(sha(path)==value,'ORIGINAL_FROZEN_INPUT_CHANGED:'+path)
    for path,expected in marker['asset_stats'].items():
        stat=Path(path).stat();require(dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)==expected,'LOCAL_ASSET_CHANGED')
        if strong:require(sha(path)==marker['asset_hashes'][path],'LOCAL_ASSET_HASH_CHANGED')
    for path,value in read(ROOT/'DEV/results-freeze.json')['hashes'].items():require(sha(path)==value,'DEV_RESULT_OR_BUDGET_CHANGED')
    for path,value in read('work/graph-opt-r3/scorer-v2-freeze.json')['hashes'].items():require(sha(path)==value,'ORIGINAL_SCORER_CHANGED')
    require(sha(frozen.D3)==frozen.D3_SHA and frozen.D3.stat().st_size==frozen.D3_BYTES,'EXTERNAL_D3_CHANGED')
    return marker


class Inputs(frozen.Inputs):
    def __init__(self):
        super().__init__('D3','D3');self.dependencies['configuration']=(H/'runtime-config.json').resolve()
    def verify(self,strong_assets=False):
        value=super().verify(strong_assets)
        original=read(ROOT/'config.json');actual=read(self.dependencies['configuration'])
        require(set(actual)==set(original),'CONFIGURATION_FIELDS_CHANGED')
        require(all(actual[k]==original[k] for k in original if k not in ALLOWED_CONTEXT_FIELDS),'MODEL_OR_LOCATOR_PARAMETERS_CHANGED')
        context=read(H/'input-context.json');require(actual['query_time_fixed']==context['query_time_fixed'],'D3_CONTEXT_CHANGED')
        return value


def token_preflight():
    config=read(H/'runtime-config.json');counter=OfficialTokenCounter(config['tokenizer_directory']);requests=[]
    class NeverCall:
        def complete(self,request):
            payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1]);require(set(payload)=={'question','workspace_id','as_of','directory','max_hops','query_time'},'PUBLIC_PAYLOAD_ONLY')
            requests.append(dict(tokens=counter(request),bytes=len(json.dumps(request.to_dict(),ensure_ascii=False).encode()),question=payload['question']));raise RuntimeError('LOCAL_TOKEN_COUNT_NO_PROVIDER')
    class NoCost:
        price=type('Price',(),dict(model_id=config['model_id']))();stopped=False;exhaustions=0
        def wrap(self,adapter,**kwargs):return adapter
    inputs=Inputs();routes=frozen.Routes(inputs,NoCost(),lambda cfg:NeverCall(),H/'token-preflight-routes')
    for identifier,group,public in inputs.public_cases():
        before=len(requests);routes.graph_only(deepcopy(public));require(len(requests)==before+1,'MISSING_REQUEST:'+identifier);requests[-1].update(id=identifier)
    result=dict(no_provider_calls=True,max_tokens=max(r['tokens'] for r in requests),max_bytes=max(r['bytes'] for r in requests),rows=requests)
    require(result['max_tokens']<=6144 and result['max_bytes']+1024<=30720,'INPUT_CAP_EXCEEDED');write(H/'token-preflight.json',result);return result


def prepare():
    check_baseline(True)
    require(not (H/'run-started.json').exists() and not (ROOT/'budget-D3.json').exists(),'D3_ALREADY_STARTED')
    require(not (H/'rules-freeze.json').exists(),'D3_RULES_ALREADY_FROZEN')
    data=read(frozen.D3);clarifications=read(H/'author-clarifications.json');original=read(ROOT/'config.json');config=deepcopy(original);now=datetime.now(UTC)
    business_date=data['meta']['query_time_fixed'];require(__import__('datetime').date.fromisoformat(business_date).isoformat()==business_date,'EXPLICIT_D3_CONTEXT_REQUIRED')
    config.update(query_time_fixed=business_date,price_checked_on=now.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date().isoformat(),pricing_rechecked_at_utc=now.isoformat(),pricing_valid_until_utc=(now+timedelta(minutes=55)).isoformat())
    require(all(config[k]==original[k] for k in original if k not in ALLOWED_CONTEXT_FIELDS),'PARAMETERS_CHANGED');assert_off_peak(config)
    index=deepcopy(read(H/'confirmation-index.json'));index['text_transition_bindings']=deepcopy(clarifications['text_bindings']);index['decision_receipts']={r['external_id']:r['receipt_id'] for r in index['audit'] if r['kind']=='decision'}
    from linkloom.temporal_graph.store import SQLiteGraphStore
    from tests.meeting_graph_eval_support import task_candidate_source
    store=SQLiteGraphStore(H/'confirmed.sqlite',authorized_workspace=index['workspace_id'],authorize_source=lambda ws,s,v:ws==index['workspace_id'],task_candidate_source=task_candidate_source())
    snapshot=store.load_edges(index['workspace_id']);records={r['record_id']:r for r in snapshot.records if r['kind']=='decision_record'}
    index['decision_records']={key:{k:deepcopy(records[identifier].get(k)) for k in ('record_id','subject_key','valid_from','valid_to','supersedes_id','status')} for key,identifier in index['decisions'].items()}
    fixture=next(iter(data['fixtures'].values()));index['meeting_dates']={node.node_id:meeting['date'] for node in snapshot.nodes if node.kind=='meeting' for meeting in fixture['meetings'] if node.key==index['sources'][meeting['id']]['source_id']}
    require(len(index['decision_records'])==len(fixture['confirmed_decisions']) and len(index['meeting_dates'])==len(fixture['meetings']),'INCOMPLETE_NATIVE_INDEX')
    assertions=dict(schema='D3-exception-assertions/v1',source='expected plus author pre-execution clarifications only',rows=[dict(id=q['id'],assertions=compile_expected(q['expected'],bindings=clarifications['text_bindings'],overrides=clarifications['overrides'].get(q['id'],[]))) for q in data['questions']])
    write(H/'runtime-config.json',config);write(H/'adapter-confirmation-index.json',index)
    write(H/'input-context.json',dict(query_time_fixed=business_date,source='unchanged D3 JSON meta.query_time_fixed',dataset_sha256=frozen.D3_SHA,model_parameters_unchanged=True,allowed_metadata_differences={k:dict(before=original[k],after=config[k]) for k in original if original[k]!=config[k]}))
    write(H/'price-quote.json',dict(checked_at_utc=now.isoformat(),valid_until_utc=config['pricing_valid_until_utc'],model=config['model_id'],official_version=config['model_version_official'],official_url=config['official_price_url'],input_cache_miss_usd_per_million=.15,output_usd_per_million=.6,off_peak_guard_every_request=True))
    write(H/'assertions.json',assertions)
    lines=['# D3 冻结断言：expected及执行前出题人澄清','','|题号|断言类型|具体断言|','|---|---|---|']
    for row in assertions['rows']:
        for rule in row['assertions']:lines.append('|'+row['id']+'|'+rule['op']+'|'+json.dumps(rule,ensure_ascii=False).replace('|','\\|')+'|')
    (H/'assertions.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    extra=[Path(__file__),Path('tests/integration/graph_acceptance_extensions.py'),Path('tests/unit/test_graph_acceptance_extensions.py'),Path('tests/unit/test_graph_D3_exception_controls.py')]
    files=[frozen.D3,Path('work/graph-eval-holdout-D3-freeze.json'),ROOT/'runtime-freeze.json',ROOT/'DEV/results-freeze.json',ROOT/'DEV/report.json',ROOT/'budget-dev.json',ROOT/'config.json']+[H/p for p in ['author-clarifications.json','runtime-config.json','input-context.json','price-quote.json','assertions.json','assertions.md','adapter-confirmation-index.json','confirmation-index.json','public-fixture.json','public-questions.json','confirmed.sqlite','fixture-vocabulary-scan.json']]+[Path('tests/integration/holdout_graph_acceptance.py'),Path('tests/integration/graph_acceptance_v2.py')]+extra
    seal=dict(schema='D3-isolated-adapter-rules/v1',frozen_at_utc=now.isoformat(),frozen_before_execution=True,author_clarifications_before_execution=True,final_configuration_sha256=sha(H/'runtime-config.json'),source_freeze_sha256=sha(ROOT/'runtime-freeze.json'),original_scorer_sha256=sha('tests/integration/graph_acceptance_v2.py'),hashes={str(p.resolve()):sha(p) for p in files})
    write(H/'rules-freeze.json',seal);preflight=token_preflight()
    write(H/'execution-freeze.json',dict(schema='D3-isolated-execution-freeze/v1',frozen_at_utc=datetime.now(UTC).isoformat(),hashes={str(p.resolve()):sha(p) for p in [H/'rules-freeze.json',H/'token-preflight.json']}))
    Inputs().verify(True);return dict(questions=len(assertions['rows']),rules_sha256=sha(H/'rules-freeze.json'),execution_sha256=sha(H/'execution-freeze.json'),assertions_sha256=sha(H/'assertions.json'),assertion_table_sha256=sha(H/'assertions.md'),adapter_sha256=sha(extra[1]),test_sha256=sha(extra[2]),query_time_fixed=business_date,max_input_tokens=preflight['max_tokens'],provider_calls=0)


def execute():
    require((H/'execution-freeze.json').exists(),'EXECUTION_FREEZE_REQUIRED');inputs=Inputs();inputs.verify(True);config=read(inputs.dependencies['configuration']);cases=list(inputs.public_cases());previous=read(ROOT/'budget-dev.json')['reserved_usd']
    from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
    hits=sum(int(any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in [p['question']]+(['As of '+p['as_of']+', '+p['question']] if p.get('as_of') else []))) for i,g,p in cases)
    limits=frozen.matrix_budget(len(cases),hits,config,previous);key=os.environ.get('DEEPSEEK_API_KEY','');require(bool(key),'CONFIGURED_KEY_UNAVAILABLE')
    require(not (H/'run-started.json').exists() and not (ROOT/'budget-D3.json').exists(),'ONE_D3_ONLY_NO_RETRY')
    write(H/'preflight.json',dict(combined_cap_usd=1.55,previous_reserved_usd=previous,remaining_before_usd=limits['remaining'],full_upper_usd=limits['upper'],max_calls=limits['max_calls'],legacy_template_hits=hits))
    write(H/'run-started.json',dict(started_at_utc=datetime.now(UTC).isoformat(),rules_sha256=sha(H/'rules-freeze.json'),execution_sha256=sha(H/'execution-freeze.json'),configuration_sha256=sha(H/'runtime-config.json')))
    budget=CampaignBudget(ROOT/'budget-D3.json',PriceReference(config['model_id'],.15,.6,config['price_checked_on'],config['official_price_url']),validate_inputs=inputs.verify,max_usd=limits['allocation']);budget.preflight(limits['max_calls']*6144,limits['max_calls']*1024)
    os.environ.pop('OPENAI_LOG',None);logging.getLogger('openai').setLevel(logging.WARNING);logging.getLogger('httpx').setLevel(logging.WARNING)
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
    from linkloom.temporal_graph.entity_locator import LocalMentionLocator,LocatorConfig
    from linkloom.indexing import SentenceTransformerEmbedder
    local=config['local_models'];locator=LocalMentionLocator(LocatorConfig(**config['locator']),embedder=SentenceTransformerEmbedder(local['embedding_model_dir'],local_files_only=True),model_dir=local['rerank_model_dir'],onnx_path=local['rerank_onnx_path'])
    warm=perf_counter();locator.prepare();write(H/'local-warmup.json',dict(elapsed_ms=(perf_counter()-warm)*1000,excluded_from_query_latency=True));inputs.verify(True)
    calls_dir=H/'provider-calls';calls_dir.mkdir(exist_ok=False);sequence=[0]
    def factory(cfg):
        inner=adapter_factory(cfg)
        class Capture:
            capability=inner.capability
            def complete(self,request):
                require(sequence[0]<limits['max_calls'],'D3_CALL_LIMIT')
                if request.task_id=='graph-investigation':
                    payload=json.loads(request.user_input.rsplit('\nINPUT_JSON\n',1)[1]);require(set(payload)=={'question','workspace_id','as_of','directory','max_hops','query_time'},'PUBLIC_PAYLOAD_ONLY');require(all(set(n)<={'kind','label','meeting_date','valid_from','valid_to','status'} for n in payload['directory']),'NO_DIRECTORY_HANDLES')
                sequence[0]+=1;started=datetime.now(UTC).isoformat();response=inner.complete(request);value=json.dumps(dict(request_started_at_utc=started,request_completed_at_utc=datetime.now(UTC).isoformat(),request=request.to_dict(),response=response.to_dict()),ensure_ascii=False,indent=2);require(key not in value,'SECRET_LITERAL_REFUSED')
                with (calls_dir/f'{sequence[0]:03}.json').open('x',encoding='utf8') as f:f.write(value)
                return response
        return Capture()
    routes=frozen.Routes(inputs,budget,factory,H/'routes',mention_locator=locator);rows=[];failure=None
    logger=logging.getLogger('linkloom.temporal_graph.meeting_query');handler=logging.FileHandler(H/'contract-normalizations.log',encoding='utf8');handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler);previous_propagate=logger.propagate;logger.propagate=False
    print(json.dumps(dict(split='D3',remaining_before_usd=limits['remaining'],full_upper_usd=limits['upper'],max_calls=limits['max_calls'])),flush=True)
    try:
        for repeat in [1,2,3]:
            for identifier,group,public in cases:
                for route in (('legacy_only','graph_only') if repeat%2 else ('graph_only','legacy_only')):
                    inputs.verify();before=budget.reserved;offset=len(budget.rows);started=perf_counter()
                    try:actual=routes.routes()[route](deepcopy(public))
                    except BudgetExceeded as error:failure=str(error);break
                    except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                    inputs.verify();paid=budget.rows[offset:];rows.append(dict(id=identifier,group=group,route=route,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(c['actual_usd'] or 0 for c in paid),unknown_usage_calls=sum(c['actual_usd'] is None for c in paid)));save(H/'unscored.json',dict(rows=rows,failure=failure))
                    if route=='graph_only':print(json.dumps(dict(id=identifier,repeat=repeat,status=actual['status'],combined_reserved_usd=previous+budget.reserved)),flush=True)
                if failure:break
            if failure:break
        inputs.verify(True)
    finally:locator.close();logger.removeHandler(handler);logger.propagate=previous_propagate;handler.close()
    # The adapter is never handed any system output before all repeats finish.
    data=read(frozen.D3);scorer=AcceptanceEvaluator(next(iter(data['fixtures'].values())),read(H/'adapter-confirmation-index.json'));assertions={r['id']:r['assertions'] for r in read(H/'assertions.json')['rows']};expected={q['id']:q['expected'] for q in data['questions']}
    for row in rows:row['score']=scorer(row['actual'],assertions[row['id']]);row['unknown_positive_answer']=frozen.unknown_positive(row['actual'],expected[row['id']])
    graph=[r for r in rows if r['route']=='graph_only'];report=dict(schema='D3-isolated-acceptance/v1',split='D3',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,runs=1,repetitions=3,rows=rows,**summarize(rows),provider_calls=sequence[0],error_relations_total=sum(r['score']['error_relations'] for r in graph),unknown_positive_answers=sum(r['unknown_positive_answer'] for r in graph),configuration_sha256=sha(H/'runtime-config.json'),source_freeze_sha256=sha(ROOT/'runtime-freeze.json'),rules_freeze_sha256=sha(H/'rules-freeze.json'),dataset_sha256=frozen.D3_SHA,model_parameters_unchanged=True,query_time_fixed=config['query_time_fixed'],query_time_source='D3 meta',combined_cap_usd=1.55,combined_reserved_usd=previous+budget.reserved,remaining_usd=1.55-previous-budget.reserved,new_reserved_usd=budget.reserved,new_known_usage_estimate_usd=sum(c['actual_usd'] or 0 for c in budget.rows),unknown_usage_calls=sum(c['actual_usd'] is None for c in budget.rows),no_retries=True,no_post_result_changes=True,stop_reason='FINAL_D3_STOP')
    write(H/'report.json',report)
    files=[H/'report.json',ROOT/'budget-D3.json',H/'unscored.json',H/'contract-normalizations.log',H/'run-started.json',H/'preflight.json']+sorted(calls_dir.glob('*.json'));write(H/'results-freeze.json',dict(schema='D3-result-freeze/v1',hashes={str(p.resolve()):sha(p) for p in files}));inputs.verify(True)
    return {k:v for k,v in report.items() if k not in {'rows','per_run','aggregate'}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','execute']);args=parser.parse_args();print(json.dumps(prepare() if args.action=='prepare' else execute(),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
