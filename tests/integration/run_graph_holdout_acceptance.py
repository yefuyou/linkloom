"""One sealed holdout campaign. Product source and model configuration stay frozen."""
from copy import deepcopy
from pathlib import Path
from datetime import UTC,datetime,timedelta
from time import perf_counter
import hashlib,json,logging,os,sqlite3
from temporal_graph_routes import TemporalGraphDevRoutes
from run_temporal_graph_dev import CampaignBudget,PriceReference,BudgetExceeded,summarize


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    with Path(path).open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def save(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')


class LockedInputs:
    def __init__(self,root,*,require_runtime=False):
        self.require_runtime=require_runtime;self.seal_sha256=None;self.runtime_sha256=None
        self.root=Path(root);self.seal=self.root/'rules-freeze.json';self.runtime=self.root/'runtime-freeze.json'
        self.dependencies=dict(configuration=Path('work/graph-opt/config-r2-final.json').resolve(),public_fixture=(self.root/'public-fixture.json').resolve(),confirmed_database=(self.root/'confirmed.sqlite').resolve())
    def verify(self):
        if self.seal_sha256 is None:self.seal_sha256=sha(self.seal)
        if sha(self.seal)!=self.seal_sha256:raise ValueError('ACCEPTANCE_SEAL_CHANGED')
        seal=read(self.seal)
        for name,value in seal['hashes'].items():
            if sha(name)!=value:raise ValueError('SEALED_ACCEPTANCE_INPUT_CHANGED:'+name)
        final=read('work/graph-opt/runtime-r2-final.json')
        for key,entry in final['dependencies'].items():
            if key.startswith('code:') and sha(entry['path'])!=entry['sha256']:raise ValueError('FINAL_CODE_CHANGED:'+key)
        if sha(self.dependencies['configuration'])!=seal['final_configuration_sha256']:raise ValueError('FINAL_PARAMETERS_CHANGED')
        config=read(self.dependencies['configuration'])
        if config['graph_alignment_review'] is not False or config['graph_max_provider_calls']!=1:raise ValueError('FINAL_SINGLE_CALL_CONFIG_REQUIRED')
        quote=seal['price_quote']
        if datetime.now(UTC)>=datetime.fromisoformat(quote['valid_until_utc']):raise ValueError('PRICE_QUOTE_EXPIRED')
        database=self.dependencies['confirmed_database']
        if any(Path(str(database)+suffix).exists() for suffix in ('-wal','-journal')):raise ValueError('SQLITE_SIDE_FILE_PRESENT')
        if self.require_runtime and not self.runtime.exists():raise ValueError('HOLDOUT_RUNTIME_FREEZE_REQUIRED')
        if self.runtime.exists():
            if self.runtime_sha256 is None:self.runtime_sha256=sha(self.runtime)
            if sha(self.runtime)!=self.runtime_sha256:raise ValueError('HOLDOUT_RUNTIME_SEAL_CHANGED')
            for name,value in read(self.runtime)['hashes'].items():
                if sha(name)!=value:raise ValueError('HOLDOUT_FIXTURE_OR_RUNTIME_CHANGED:'+name)
        return seal
    def public_cases(self):
        self.verify();dataset=read(self.root/'dataset-path.json')['path'];data=read(dataset)
        return [(q['id'],'D2',dict(question=q['question'],fixture_id=q['fixture'],as_of=q.get('as_of'))) for q in data['questions']]


class HoldoutRoutes(TemporalGraphDevRoutes):
    """Change only the dataset boundary; inherit both frozen route implementations."""
    def __init__(self,inputs,budget,factory,output):
        inputs.verify();self.inputs=inputs;self.budget=budget;self.factory=factory;self.output=Path(output)
        self.config=read(inputs.dependencies['configuration']);fixture=read(inputs.dependencies['public_fixture'])
        if fixture.get('split')!='holdout' or fixture.get('dataset_sha256')!=read(inputs.root/'rules-freeze.json')['dataset_sha256']:raise ValueError('SEALED_HOLDOUT_REQUIRED')
        self.fixtures={c['case_id']:deepcopy(c) for c in fixture['cases']}
        if len(self.fixtures)!=len(fixture['cases']):raise ValueError('DUPLICATE_FIXTURE_ID')
        self.database=inputs.dependencies['confirmed_database'];self.max_input=self.config['max_input_bytes'];self.max_output=self.config['max_output_tokens'];self.legacy_turns=self.config['legacy_max_turns']
        if (type(self.max_input) is not int or not 8192<=self.max_input<=262144 or type(self.max_output) is not int or not 1<=self.max_output<=4096 or
            type(self.legacy_turns) is not int or not 1<=self.legacy_turns<=20 or self.config.get('retry_attempts')!=1 or self.config.get('retrieval_mode')!='bm25' or self.config['model_id']!=budget.price.model_id):raise ValueError('INVALID_FINAL_CONFIGURATION')
        self.upper_bound=None


def prepare(root,dataset):
    root=Path(root);inputs=LockedInputs(root);inputs.verify()
    if (root/'run-started.json').exists():raise ValueError('ONE_HOLDOUT_EVALUATION_ONLY')
    # No question/expected/system answer enters the existing confirmer.
    from tests.meeting_graph_eval_support import build_meetings,no_gold
    data=read(dataset);fixtures=deepcopy(data['fixtures']);no_gold(fixtures)
    if len(fixtures)!=1:raise ValueError('ONE_HOLDOUT_FIXTURE_REQUIRED')
    key,fixture=next(iter(fixtures.items()))
    database=inputs.dependencies['confirmed_database']
    if database.exists():raise ValueError('NEW_HOLDOUT_DATABASE_REQUIRED')
    index=build_meetings(fixture,database,recorded_at='2026-11-03T00:00:00+08:00')
    index['sources']={};index['decision_receipts']={r['external_id']:r['receipt_id'] for r in index['audit'] if r['kind']=='decision'}
    db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        for meeting in fixture['meetings']:
            rows=db.execute('SELECT source_id,current_version_id FROM ll_source WHERE workspace_id=? AND name=?',(index['workspace_id'],meeting['name'])).fetchall()
            if len(rows)!=1:raise ValueError('UNIQUE_MEETING_SOURCE_REQUIRED')
            row=rows[0];content=db.execute('SELECT content FROM ll_source_version WHERE workspace_id=? AND artifact_version_id=?',(index['workspace_id'],row['current_version_id'])).fetchone()[0]
            if content!='\n'.join(meeting['lines']):raise ValueError('HOLDOUT_SOURCE_TEXT_CHANGED')
            index['sources'][meeting['id']]=dict(source_id=row['source_id'],source_version_id=row['current_version_id'])
        index['task_events']={r['id']:r['task_id'] for r in db.execute('SELECT id,task_id FROM task_event WHERE workspace_id=?',(index['workspace_id'],))}
    finally:db.close()
    public=dict(split='holdout',dataset_sha256=sha(dataset),confirmation_label='模拟人工确认',cases=[dict(case_id=key,workspace_id=fixture['workspace'],confirmed_workspace_id=index['workspace_id'],documents=[dict(workspace_id=fixture['workspace'],path=m['id']+'.md',content='\n'.join(m['lines'])) for m in fixture['meetings']])])
    write(root/'confirmation-index.json',index);write(inputs.dependencies['public_fixture'],public)
    write(root/'runtime-freeze.json',dict(schema='holdout-runtime/v1',hashes={str(p.resolve()):sha(p) for p in [database,inputs.dependencies['public_fixture'],root/'confirmation-index.json']}))
    inputs.verify();return dict(workspace=index['workspace_id'],decisions=len(index['decisions']),tasks=len(index['tasks']),confirmation='模拟人工确认')


def execute(root):
    root=Path(root);inputs=LockedInputs(root,require_runtime=True);seal=inputs.verify();config=read(inputs.dependencies['configuration']);cases=inputs.public_cases()
    if (root/'run-started.json').exists() or (root/'budget.json').exists():raise ValueError('ONE_HOLDOUT_EVALUATION_ONLY')
    from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
    legacy=0
    for _,_,public in cases:
        candidates=[public['question']]
        if public.get('as_of'):candidates.append('As of '+public['as_of']+', '+public['question'])
        if any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in candidates):legacy+=3*config['legacy_max_turns']
    graph=len(cases)*3;call_cap=legacy+graph;quote=seal['price_quote'];cap=(call_cap*8192*quote['input_upper_per_million']+call_cap*1024*quote['output_upper_per_million'])/1000000
    old=[read('work/graph-opt/budget-r1.json'),read('work/graph-opt/budget-r2.json')];previous=sum(x['reserved_usd'] for x in old);remaining=1-previous
    if cap>remaining*.8:raise BudgetExceeded('COMPLETE_HOLDOUT_MATRIX_EXCEEDS_BUDGET_PREFLIGHT')
    preflight=dict(questions=len(cases),executions=len(cases)*6,graph_calls_max=graph,legacy_calls_max=legacy,cost_upper_usd=cap,stage_remaining_before=remaining,previous_stage_reserved=previous,price_valuation='Official peak/cache-miss upper bound, even for off-peak calls',configuration_unchanged=True)
    key=os.environ.get('DEEPSEEK_API_KEY','')
    if not key:raise RuntimeError('CONFIGURED_API_KEY_UNAVAILABLE')
    write(root/'preflight.json',preflight)
    write(root/'run-started.json',dict(started_at_utc=datetime.now(UTC).isoformat(),one_shot=True,dataset_sha256=seal['dataset_sha256'],assertions_sha256=seal['assertions_sha256']))
    price=PriceReference(config['model_id'],quote['input_upper_per_million'],quote['output_upper_per_million'],quote['checked_on'],quote['official_url'])
    budget=CampaignBudget(root/'budget.json',price,validate_inputs=inputs.verify,max_usd=remaining);budget.preflight(call_cap*8192,call_cap*1024)
    from openai import OpenAI
    from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
    os.environ.pop('OPENAI_LOG',None);logging.getLogger('openai').setLevel(logging.WARNING);logging.getLogger('httpx').setLevel(logging.WARNING)
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',max_retries=0,timeout=20)
    calls_dir=root/'provider-calls';calls_dir.mkdir();sequence=[0]
    class OneAttempt:
        def create_chat_completion(self,**payload):
            inputs.verify();thinking=payload.pop('thinking')
            if payload.get('model')!=config['model_id'] or payload.get('max_tokens')!=1024 or payload.get('temperature')!=0 or thinking!={'type':'disabled'}:raise ValueError('MODEL_PARAMETERS_CHANGED')
            return client.chat.completions.create(**payload,extra_body={'thinking':thinking})
    def factory(configuration):
        inner=DeepSeekProviderAdapter(OneAttempt(),model_id=configuration['model_id'])
        class Capture:
            capability=inner.capability
            def complete(self,request):
                sequence[0]+=1;number=sequence[0];started=datetime.now(UTC).isoformat()
                response=inner.complete(request)
                item=dict(request_started_at_utc=started,request_completed_at_utc=datetime.now(UTC).isoformat(),request=request.to_dict(),response=response.to_dict())
                encoded=json.dumps(item,ensure_ascii=False,indent=2)
                if key in encoded:raise ValueError('SECRET_LITERAL_IN_CAPTURE_REFUSED')
                with (calls_dir/f'{number:03}.json').open('x',encoding='utf8') as f:f.write(encoded)
                return response
        return Capture()
    routes=HoldoutRoutes(inputs,budget,factory,root/'routes');rows=[];failure=None
    for repeat in (1,2,3):
        for identifier,group,public in cases:
            order=('legacy_only','graph_only') if repeat%2 else ('graph_only','legacy_only')
            for route in order:
                inputs.verify();before=budget.reserved;first=len(budget.rows);started=perf_counter()
                try:actual=routes.routes()[route](deepcopy(public))
                except BudgetExceeded as error:failure=str(error);break
                except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                inputs.verify();calls=budget.rows[first:]
                rows.append(dict(id=identifier,group=group,route=route,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(c['actual_usd'] or 0 for c in calls),unknown_usage_calls=sum(c['actual_usd'] is None for c in calls)))
                save(root/'unscored-executions.json',dict(rows=rows,failure=failure))
                print(json.dumps(dict(id=identifier,route=route,repeat=repeat,status=actual['status'],holdout_reserved_usd=round(budget.reserved,6))),flush=True)
            if failure:break
        if failure:break
    # Only now may the evaluator receive any real system outputs.
    inputs.verify()
    from holdout_graph_acceptance import AcceptanceEvaluator
    data=read(read(root/'dataset-path.json')['path']);assertions={r['id']:r['assertions'] for r in read(root/'assertions.json')['rows']};fixture=next(iter(data['fixtures'].values()))
    scorer=AcceptanceEvaluator(fixture,read(root/'confirmation-index.json'))
    for row in rows:
        try:row['score']=scorer(row['actual'],assertions[row['id']])
        except (ValueError,KeyError,TypeError,IndexError) as error:
            row['score']=dict(correct=False,checks={'invalid_output_shape_or_evaluation_error':False},error_relations=0,refused=row['actual']['status']!='FOUND',answerable=next(r['expected'] for r in assertions[row['id']] if r['op']=='status')=='FOUND',evaluation_error_type=type(error).__name__)
    report=dict(schema='holdout-acceptance-report/v1',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,split='holdout',runs=1,repetitions=3,configuration_path=str(inputs.dependencies['configuration']),configuration_sha256=sha(inputs.dependencies['configuration']),seal=seal,preflight=preflight,rows=rows,**summarize(rows),holdout_reserved_usd=budget.reserved,stage_reserved_usd=previous+budget.reserved,stage_remaining_usd=1-previous-budget.reserved,known_usage_valuation_upper_usd=sum(c['actual_usd'] or 0 for c in budget.rows),unknown_usage_calls=sum(c['actual_usd'] is None for c in budget.rows),no_post_result_changes=True)
    save(root/'report.json',report);inputs.verify()
    return report
