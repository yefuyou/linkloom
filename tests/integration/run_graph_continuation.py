"""Frozen development rounds after the user explicitly promoted D2 to DEV."""
from copy import deepcopy
from collections import defaultdict
from datetime import datetime,UTC,timedelta
from pathlib import Path
from time import perf_counter
import hashlib,json,logging,os,zipfile
from run_temporal_graph_dev import CampaignBudget,PriceReference,BudgetExceeded,summarize
from temporal_graph_routes import TemporalGraphDevRoutes
from deepseek_eval_transport import adapter_factory,assert_off_peak

ROOT=Path('work/graph-opt-r3')
INITIAL_LEDGERS=['work/graph-opt/budget-r1.json','work/graph-opt/budget-r2.json','work/graph-holdout-D2/budget.json']

def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):
    with Path(p).open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')


def cases():
    data=read('work/graph-eval-questions.json')
    result=[(q['id'],'D' if q['group']=='D_real_meeting_questions' else 'B_MERIDIAN',dict(question=q['question'],fixture_id=q['fixture'],as_of=q.get('as_of'))) for q in data['questions'] if q['fixture'] in {'graph-dev-meetings','meridian'}]
    result += [(q['id'],'D2',dict(question=q['question'],fixture_id=q['fixture'],as_of=q.get('as_of'))) for q in read('work/graph-eval-holdout-D2.json')['questions']]
    if len(result)!=40 or len({r[0] for r in result})!=40:raise ValueError('FULL_40_CASE_MATRIX_REQUIRED')
    return sorted(result,key=lambda r:({'D':0,'D2':1,'B_MERIDIAN':2}[r[1]],r[0]))


class Inputs:
    def __init__(self,number,group):
        self.marker=ROOT/f'runtime-r{number}.json';self.marker_sha=sha(self.marker)
        self.dependencies=dict(configuration=(ROOT/f'config-r{number}.json').resolve(),public_fixture=(ROOT/'public-fixtures.json').resolve(),confirmed_database=Path('work/graph-holdout-D2/confirmed.sqlite' if group=='D2' else 'work/L6-eval-dev-v1.sqlite').resolve())
    def verify(self):
        if sha(self.marker)!=self.marker_sha:raise ValueError('ROUND_FREEZE_CHANGED')
        marker=read(self.marker)
        for path,expected in marker['hashes'].items():
            if sha(path)!=expected:raise ValueError('ROUND_INPUT_CHANGED:'+path)
        for database in marker['databases']:
            if any(Path(database+suffix).exists() for suffix in ('-wal','-journal')):raise ValueError('SQLITE_SIDE_FILE_PRESENT')
        assert_off_peak(read(self.dependencies['configuration']))
        return marker
    def public_cases(self):self.verify();return iter(cases())


def seal(number):
    if type(number) is not int or not 1<=number<=3:raise ValueError('MAX_THREE_ADDITIONAL_ROUNDS')
    expected={f'budget-r{i}.json' for i in range(1,number)}
    if {p.name for p in ROOT.glob('budget-r*.json')}!=expected:raise ValueError('LEDGER_SEQUENCE_REQUIRED_NO_RERUNS')
    if number>1:
        previous=read(ROOT/f'results-r{number-1}/report.json')
        if previous['status']!='COMPLETE' or previous['target_met']:raise ValueError('STOP_REQUIRED')
    final=read('work/graph-opt/config-r2-final.json')
    names=['model_id','max_input_bytes','max_output_tokens','legacy_max_turns','retry_attempts','retrieval_mode','temperature','graph_limits','query_time_fixed','task_candidate_format','tokenizer_directory','graph_alignment_review','graph_max_provider_calls','model_version_official','endpoint','effective_generation_parameters','historical_metrics_notice']
    config={k:final[k] for k in names};now=datetime.now(UTC)
    token_cap=7680 if number>1 else 7168
    config.update(graph_grounding_required=True,input_token_caps={'graph_only':token_cap,'legacy_only':token_cap},input_usd_per_million=.15,output_usd_per_million=.6,price_checked_on=now.astimezone(__import__('datetime').timezone(timedelta(hours=8))).date().isoformat(),official_price_url='https://api-docs.deepseek.com/quick_start/pricing/',pricing_valid_until_utc=(now+timedelta(minutes=55)).isoformat(),pricing_rechecked_at_utc=now.isoformat(),continuation_round=number,global_optimization_round=number+2,stage_cap_usd=1,previous_budget_ledgers=INITIAL_LEDGERS+[str(ROOT/f'budget-r{i}.json') for i in range(1,number)],scorer_v2_freeze=str(ROOT/'scorer-v2-freeze.json'),D2_split='dev',D3_accessed=False)
    assert_off_peak(config);write(ROOT/f'config-r{number}.json',config)
    # Raw fixtures/questions/expected stay byte-identical; only the public split metadata was relabelled.
    source_files=sorted(Path('src').rglob('*.py'))+sorted(Path('tests/integration').glob('*.py'))+[Path('tests/meeting_graph_eval_support.py'),Path('tests/graph_eval_support.py')]
    frozen=['work/graph-eval-questions.json','work/graph-eval-freeze.json','work/graph-eval-fixture-bindings.json','work/graph-eval-holdout-D2.json','work/graph-eval-holdout-D2-json-freeze.json','work/graph-holdout-D2/assertions.json','work/graph-holdout-D2/confirmation-index.json','work/graph-eval-confirmations-v1.json','work/graph-opt/results-r2/scored/report.json','work/graph-holdout-D2/report.json']
    if number>1:frozen += [str(ROOT/f'results-r{number-1}/report.json'),str(ROOT/f'runtime-r{number-1}.json'),str(ROOT/f'source-r{number-1}.zip')]
    files=source_files+[Path(p) for p in frozen]+[ROOT/'public-fixtures.json',ROOT/'scorer-v2-freeze.json',ROOT/'d2-corrected-baseline.json',ROOT/f'config-r{number}.json']+[Path(p) for p in config['previous_budget_ledgers']]+[Path('work/graph-eval-tokenizer')/p for p in ['source.json','encoding.py','tokenizer.json']]
    databases=[Path('work/L6-eval-dev-v1.sqlite').resolve(),Path('work/graph-holdout-D2/confirmed.sqlite').resolve()];files+=databases
    archive=ROOT/f'source-r{number}.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for p in source_files:z.write(p,p.as_posix())
    files.append(archive)
    marker=dict(schema='L6-continuation-freeze/v1',frozen_at_utc=now.isoformat(),round=number,databases=[str(p) for p in databases],hashes={str(p.resolve()):sha(p) for p in files},protocol=dict(dev_groups=['D','D2','B_MERIDIAN'],repetitions=3,scorer_v2=True,no_expected_in_provider=True,max_new_rounds=3,unknown_fabrication_rule='expected UNKNOWN and (FOUND or nonempty values/relations/selected outputs)',D3_accessed=False))
    write(ROOT/f'runtime-r{number}.json',marker);Inputs(number,'D').verify();return marker


def unknown_positive(actual,expected):
    return expected.get('status')=='UNKNOWN' and bool(actual['status']=='FOUND' or actual.get('values') or actual.get('relations') or any(o.get('values') for o in actual.get('structured_outputs',[])))


def compare(current,previous):
    def group(rows):
        result=defaultdict(list)
        for row in rows:
            if row['route']=='graph_only' and row['group'] in {'D','D2','B_MERIDIAN'}:result[row['id']].append(row)
        if len(result)!=40 or any(sorted(x['repeat'] for x in values)!=[1,2,3] for values in result.values()):raise ValueError('THREE_REPEATS_ALL_40_REQUIRED')
        return {key:dict(correct=sum(x['score']['correct'] for x in values),statuses=[x['actual']['status'] for x in sorted(values,key=lambda x:x['repeat'])],failed=sorted({k for x in values for k,v in x['score']['checks'].items() if not v})) for key,values in result.items()}
    before,after=group(previous),group(current)
    differences=[dict(id=k,before=before[k],after=after[k],delta=after[k]['correct']-before[k]['correct']) for k in sorted(after)]
    return dict(per_case=differences,better=[r['id'] for r in differences if r['delta']>0],worse=[r['id'] for r in differences if r['delta']<0])


def run(number):
    inputs={g:Inputs(number,g) for g in ('D','D2')};marker=inputs['D'].verify();config=read(inputs['D'].dependencies['configuration']);public_cases=cases()
    output=ROOT/f'results-r{number}';output.mkdir(exist_ok=False)
    previous_reserved=sum(read(p)['reserved_usd'] for p in config['previous_budget_ledgers']);remaining=1-previous_reserved
    from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query
    legacy_hits=0
    for _,_,public in public_cases:
        questions=[public['question']]+(['As of '+public['as_of']+', '+public['question']] if public.get('as_of') else [])
        legacy_hits+=int(any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in questions))
    max_calls=120+legacy_hits*3*config['legacy_max_turns'];token_cap=config['input_token_caps']['graph_only'];cap=(max_calls*token_cap*.15+max_calls*1024*.6)/1e6
    if cap>remaining*.8:raise BudgetExceeded('FULL_MATRIX_WOULD_EXCEED_REMAINING_BUDGET')
    key=os.environ.get('DEEPSEEK_API_KEY','')
    if not key:raise ValueError('CONFIGURED_API_KEY_UNAVAILABLE')
    write(output/'preflight.json',dict(previous_reserved_usd=previous_reserved,remaining_usd=remaining,max_calls=max_calls,cost_upper_usd=cap,graph_executions=120,legacy_executions=120,legacy_template_hits=legacy_hits))
    print(json.dumps(dict(round=number,remaining_before=remaining,full_matrix_cap=cap,max_calls=max_calls)),flush=True)
    price=PriceReference(config['model_id'],.15,.6,config['price_checked_on'],config['official_price_url'])
    budget=CampaignBudget(ROOT/f'budget-r{number}.json',price,validate_inputs=inputs['D'].verify,max_usd=remaining);budget.preflight(max_calls*token_cap,max_calls*1024)
    os.environ.pop('OPENAI_LOG',None);logging.getLogger('openai').setLevel(logging.WARNING);logging.getLogger('httpx').setLevel(logging.WARNING)
    calls=output/'provider-calls';calls.mkdir();sequence=[0]
    def factory(configuration):
        inner=adapter_factory(configuration)
        class Capture:
            capability=inner.capability
            def complete(self,request):
                sequence[0]+=1;started=datetime.now(UTC).isoformat();response=inner.complete(request)
                payload=json.dumps(dict(request_started_at_utc=started,request_completed_at_utc=datetime.now(UTC).isoformat(),request=request.to_dict(),response=response.to_dict()),ensure_ascii=False,indent=2)
                if key in payload:raise ValueError('SECRET_LITERAL_REFUSED')
                with (calls/f'{sequence[0]:03}.json').open('x',encoding='utf8') as f:f.write(payload)
                return response
        return Capture()
    routes={g:TemporalGraphDevRoutes(value,budget,factory,output/'routes',preflight=False) for g,value in inputs.items()};rows=[];failure=None
    for repeat in (1,2,3):
        for identifier,group,public in public_cases:
            selected=routes['D2' if group=='D2' else 'D']
            for route in (('legacy_only','graph_only') if repeat%2 else ('graph_only','legacy_only')):
                inputs['D'].verify();first=len(budget.rows);before=budget.reserved;started=perf_counter()
                try:actual=selected.routes()[route](deepcopy(public))
                except BudgetExceeded as error:failure=str(error);break
                except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                inputs['D'].verify();paid=budget.rows[first:]
                rows.append(dict(id=identifier,group=group,route=route,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(x['actual_usd'] or 0 for x in paid),unknown_usage_calls=sum(x['actual_usd'] is None for x in paid)))
                save(output/'unscored.json',dict(rows=rows,failure=failure))
                if route=='graph_only':print(json.dumps(dict(id=identifier,group=group,repeat=repeat,status=actual['status'],stage_reserved=round(previous_reserved+budget.reserved,6))),flush=True)
            if failure:break
        if failure:break
    inputs['D'].verify()
    from graph_eval_date_score import DateParityScorer
    from graph_acceptance_v2 import AcceptanceEvaluator
    data=read('work/graph-eval-questions.json');expected={q['id']:q['expected'] for q in data['questions']};scorer=DateParityScorer(data['fixtures'],read('work/graph-eval-confirmations-v1.json'))
    holdout=read('work/graph-eval-holdout-D2.json');h_expected={q['id']:q['expected'] for q in holdout['questions']};expected.update(h_expected)
    h_scorer=AcceptanceEvaluator(next(iter(holdout['fixtures'].values())),read('work/graph-holdout-D2/confirmation-index.json'));assertions={r['id']:r['assertions'] for r in read('work/graph-holdout-D2/assertions.json')['rows']};publics={i:p for i,g,p in public_cases}
    fixtures={c['case_id']:c for c in read(inputs['D'].dependencies['public_fixture'])['cases']}
    for row in rows:
        if row['group']=='D2':row['score']=h_scorer(row['actual'],assertions[row['id']])
        else:
            public=publics[row['id']];fixture=fixtures[public['fixture_id']];documents=[d for d in fixture['documents'] if d['workspace_id']==fixture['workspace_id']]
            row['score']=scorer(row['id'],row['actual'],expected[row['id']],public,documents)
        row['unknown_positive_answer']=unknown_positive(row['actual'],expected[row['id']])
    aggregate=summarize(rows);graph=[x for x in rows if x['route']=='graph_only'];fabricated=sum(x['unknown_positive_answer'] for x in graph)
    metrics={a['group']:a['metrics'] for a in aggregate['aggregate'] if a['route']=='graph_only'}
    target=not failure and len(rows)==240 and all(metrics[g]['accuracy']['mean']>=.75 for g in ('D','D2')) and sum(x['score']['error_relations'] for x in graph)==0 and fabricated==0
    baseline=(read('work/graph-opt/results-r2/scored/report.json')['rows']+read(ROOT/'d2-corrected-baseline.json')['rows']) if number==1 else read(ROOT/f'results-r{number-1}/report.json')['rows']
    comparison=compare(rows,baseline) if not failure else None
    stage_reserved=previous_reserved+budget.reserved;remaining_after=1-stage_reserved
    stop_reason='TARGET_MET' if target else 'MAX_ROUNDS' if number==3 else 'BUDGET_PREFLIGHT' if cap>remaining_after*.8 else None
    result=dict(schema='L6-dev-continuation-report/v1',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,round=number,global_round=number+2,split='dev',D2_promoted_to_dev=True,D3_accessed=False,rows=rows,**aggregate,comparison=comparison,target_met=target,stop_reason=stop_reason,unknown_positive_answers=fabricated,H10_results=[dict(repeat=x['repeat'],status=x['actual']['status'],positive=x['unknown_positive_answer']) for x in graph if x['id']=='H10'],stage_reserved_usd=stage_reserved,stage_remaining_usd=remaining_after,new_reserved_usd=budget.reserved,new_known_usage_estimate_usd=sum(x['actual_usd'] or 0 for x in budget.rows),unknown_usage_calls=sum(x['actual_usd'] is None for x in budget.rows),configuration=config,runtime_sha256=sha(ROOT/f'runtime-r{number}.json'),scorer_v2_sha256=sha(ROOT/'scorer-v2-freeze.json'))
    write(output/'report.json',result);inputs['D'].verify()
    return result
