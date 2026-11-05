"""Frozen external DEV evaluation: three repetitions, isolated Gold, hard $1 cap.

This module never edits question/expected files and never discovers a held-out set.
Routes are Host callables; their inference adapters MUST be wrapped by CampaignBudget.
No live call is performed by importing this module or by --freeze/--verify.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC,datetime,timedelta,timezone
from pathlib import Path
from statistics import mean
from threading import Lock
from time import perf_counter
from urllib.parse import urlparse
import argparse,hashlib,json,math


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def _dump(path,data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8')


class FrozenDevInputs:
    def __init__(self,questions,expected,freeze,*,public_fixture,confirmed_database,configuration):
        self.questions=Path(questions).resolve();self.expected=Path(expected).resolve();self.freeze=Path(freeze).resolve()
        self.dependencies={name:Path(path).resolve() for name,path in dict(public_fixture=public_fixture,
            confirmed_database=confirmed_database,configuration=configuration).items()}

    def _dependencies(self):
        # Prompt/schema live in source. Freeze all production Python so legacy and graph
        # cannot silently diverge after the human-confirmed dataset is frozen.
        root=Path(__file__).resolve().parents[2]
        paths={**self.dependencies}
        for path in sorted((root/'src').rglob('*.py')):
            paths['code:'+path.relative_to(root).as_posix()]=path
        for relative in ('src/linkloom/ui/static/product.js','tests/graph_eval_support.py',
                         'tests/integration/run_temporal_graph_dev.py','tests/integration/temporal_graph_routes.py',
                         'tests/integration/deepseek_eval_transport.py','tests/integration/run_meeting_graph_dev.py',
                         'tests/integration/meeting_graph_scoring.py','tests/meeting_graph_eval_support.py',
                         'tests/integration/build_temporal_graph_dev.py','work/graph-eval-protocol.md'):
            path=root/relative
            if path.exists():paths['code:'+relative]=path
        config=_json(self.dependencies['configuration'])
        for name in ('confirmation_index',):
            if config.get(name):paths['configuration:'+name]=Path(config[name]).resolve()
        if config.get('tokenizer_directory'):
            for name in ('source.json','tokenizer.json','encoding.py'):
                paths['tokenizer:'+name]=(Path(config['tokenizer_directory'])/name).resolve()
        database=self.dependencies['confirmed_database']
        if any(Path(str(database)+suffix).exists() for suffix in ('-wal','-journal')):
            raise ValueError('CONFIRMED_DATABASE_MUST_BE_CLOSED_AND_CHECKPOINTED')
        return {name:dict(path=str(path),sha256=_sha(path)) for name,path in sorted(paths.items())}

    def create(self):
        if self.freeze.exists():raise ValueError('FREEZE_ALREADY_EXISTS')
        questions=_json(self.questions);expected=_json(self.expected)
        if questions.get('split')!='dev' or expected.get('split')!='dev':raise ValueError('DEV_ONLY')
        if questions.get('authoring')!='外部起草，人工确认':raise ValueError('EXTERNAL_HUMAN_CONFIRMATION_REQUIRED')
        ids=[q['id'] for q in questions['cases']]
        if len(ids)!=len(set(ids)) or not ids:raise ValueError('DUPLICATE_OR_EMPTY_QUESTIONS')
        if set(expected['cases'])!=set(ids):raise ValueError('EXPECTED_ID_SET_MISMATCH')
        groups={q['group'] for q in questions['cases']}
        if groups!={'A','B','C'}:raise ValueError('THREE_GROUPS_REQUIRED')
        mothers={q['id']:q for q in questions['cases'] if q['group']=='A'}
        templates={f'T{i:02d}' for i in range(1,14)}
        if not templates<={q.get('template_id') for q in mothers.values()}:raise ValueError('ALL_THIRTEEN_TEMPLATES_REQUIRED')
        for template in templates:
            rewrites=[q for q in questions['cases'] if q['group']=='B' and q.get('template_id')==template]
            if len({q['question'] for q in rewrites})<2:raise ValueError('TWO_DISTINCT_REWRITES_PER_TEMPLATE_REQUIRED')
            for rewrite in rewrites:
                mother=mothers.get(rewrite.get('parent_id'))
                if mother is None or mother.get('template_id')!=template:raise ValueError('REWRITE_PARENT_REQUIRED')
                if rewrite['question']==mother['question'] or any(rewrite.get(k)!=mother.get(k) for k in ('fixture_id','workspace_id','as_of')):
                    raise ValueError('REWRITE_CHANGED_INFORMATION_SCOPE')
                if expected['cases'][rewrite['id']]!=expected['cases'][mother['id']]:raise ValueError('REWRITE_EXPECTED_MUST_MATCH_PARENT')
        if len([q for q in questions['cases'] if q['group']=='C'])<12:raise ValueError('NEW_COMBINATION_BOUNDARY_CASES_REQUIRED')
        # Files are hashed as supplied, never reformatted or repaired.
        data=dict(schema='graph-eval-freeze/v2',questions=dict(path=str(self.questions),sha256=_sha(self.questions)),
                  expected=dict(path=str(self.expected),sha256=_sha(self.expected)),dependencies=self._dependencies())
        with self.freeze.open('x',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        return data

    def verify(self):
        data=_json(self.freeze)
        if data.get('schema')!='graph-eval-freeze/v2':raise ValueError('INVALID_FREEZE')
        if data.get('dependencies')!=self._dependencies():raise ValueError('FROZEN_DEPENDENCY_CHANGED')
        for key,path in [('questions',self.questions),('expected',self.expected)]:
            if str(path)!=data[key]['path'] or _sha(path)!=data[key]['sha256']:raise ValueError('FROZEN_INPUT_CHANGED')
        return data

    def public_cases(self):
        self.verify();data=_json(self.questions)
        if data['split']!='dev':raise ValueError('DEV_ONLY')
        for question in data['cases']:
            public={k:v for k,v in question.items() if k in {'question','fixture_id','workspace_id','as_of'}}
            if not isinstance(public.get('question'),str) or not public['question'].strip():raise ValueError('INVALID_QUESTION')
            yield question['id'],question['group'],public


class BudgetExceeded(RuntimeError):pass

@dataclass(frozen=True)
class PriceReference:
    model_id: str
    input_usd_per_million: float
    output_usd_per_million: float
    checked_on: str
    official_url: str

    def validate(self):
        today=datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        if self.checked_on!=today:raise ValueError('CURRENT_OFFICIAL_PRICE_VERIFICATION_REQUIRED')
        if urlparse(self.official_url).hostname not in {'ai.google.dev','developers.googleblog.com','platform.openai.com','openai.com','api-docs.deepseek.com'}:
            raise ValueError('OFFICIAL_PRICE_SOURCE_REQUIRED')
        if not self.model_id or any(type(v) not in {int,float} or not math.isfinite(v) or v<=0 for v in
            (self.input_usd_per_million,self.output_usd_per_million)):raise ValueError('INVALID_PRICE')


class CampaignBudget:
    """Reserve before calls; uncertain failures retain full reservation; no retries."""
    def __init__(self,path,price,*,validate_inputs,max_usd=1.0):
        if type(max_usd) not in {int,float} or not 0<max_usd<=1:raise ValueError('MAX_COST_MUST_NOT_EXCEED_ONE_DOLLAR')
        price.validate();self.path=Path(path);self.price=price;self.validate_inputs=validate_inputs
        self.max_usd=max_usd;self.lock=Lock();self.rows=[];self.reserved=0.0;self.preflight_done=False;self.stopped=False;self.exhaustions=0
        # A campaign file cannot be silently reset or reused after an interrupted paid run.
        with self.path.open('x',encoding='utf8') as stream:json.dump(dict(reserved_usd=0,calls=[]),stream)

    def _save(self):_dump(self.path,dict(max_usd=self.max_usd,reserved_usd=self.reserved,calls=self.rows))

    def preflight(self,total_input_cap,total_output_cap):
        estimate=(total_input_cap*self.price.input_usd_per_million+total_output_cap*self.price.output_usd_per_million)/1_000_000
        if estimate>self.max_usd*.8:raise BudgetExceeded('MATRIX_UPPER_BOUND_EXCEEDS_80_PERCENT')
        self.preflight_done=True
        return estimate

    def wrap(self,adapter,*,max_input_bytes,max_output_tokens,max_input_tokens=None,token_counter=None):
        if (max_input_tokens is None)!=(token_counter is None):raise ValueError('TOKEN_COUNTER_AND_CAP_REQUIRED_TOGETHER')
        if max_input_tokens is not None and (type(max_input_tokens) is not int or max_input_tokens<1 or not callable(token_counter)):
            raise ValueError('INVALID_TOKEN_CAP')
        input_cap=max_input_bytes if max_input_tokens is None else max_input_tokens
        campaign=self
        class Budgeted:
            def __init__(self):
                self.requests=[];self.call_count=0
                self.model_id=campaign.price.model_id
                self.capability=getattr(adapter,'capability',None)
            def complete(self,request):
                if campaign.stopped:raise BudgetExceeded('CAMPAIGN_STOPPED')
                if not campaign.preflight_done:raise BudgetExceeded('MATRIX_PREFLIGHT_REQUIRED')
                campaign.validate_inputs();campaign.price.validate()
                encoded=json.dumps(request.to_dict(),ensure_ascii=False,sort_keys=True).encode()
                output=request.generation_options.max_output_tokens
                if request.model_id!=campaign.price.model_id or len(encoded)+4096>max_input_bytes or output>max_output_tokens:
                    campaign.exhaustions+=1
                    raise BudgetExceeded('REQUEST_EXCEEDS_RESERVED_CONFIGURATION')
                counted_input=token_counter(request) if token_counter else len(encoded)
                if type(counted_input) is not int or counted_input<0 or counted_input>input_cap:
                    campaign.exhaustions+=1
                    raise BudgetExceeded('TOKEN_COUNT_EXCEEDS_RESERVATION')
                cost=(input_cap*campaign.price.input_usd_per_million+max_output_tokens*campaign.price.output_usd_per_million)/1_000_000
                with campaign.lock:
                    if campaign.reserved+cost>campaign.max_usd+1e-12:
                        campaign.exhaustions+=1
                        raise BudgetExceeded('CAMPAIGN_BUDGET_EXHAUSTED')
                    campaign.reserved+=cost
                    row=dict(sequence=len(campaign.rows)+1,reserved_usd=cost,input_token_cap=input_cap,counted_input_tokens=counted_input,request_sha256=hashlib.sha256(encoded).hexdigest(),status='RESERVED',actual_usd=None)
                    campaign.rows.append(row);campaign._save()
                self.requests.append(request);self.call_count+=1
                # Caller supplies a transport with SDK/internal retries disabled.
                try:response=adapter.complete(request)
                except Exception:
                    row['status']='UNCERTAIN';campaign._save();raise
                usage=response.usage
                if usage.input_tokens is not None and usage.output_tokens is not None:
                    total=usage.total_tokens
                    extra=max(0,(total or 0)-usage.input_tokens-usage.output_tokens)
                    billed_output=usage.output_tokens+extra
                    row['actual_usd']=(usage.input_tokens*campaign.price.input_usd_per_million+billed_output*campaign.price.output_usd_per_million)/1_000_000
                    row['input_tokens']=usage.input_tokens;row['output_tokens']=usage.output_tokens
                    row['additional_output_tokens']=extra;row['total_tokens']=total
                    if usage.input_tokens>input_cap or billed_output>max_output_tokens or (total or 0)>input_cap+max_output_tokens:
                        campaign.stopped=True
                        campaign.reserved+=max(0,row['actual_usd']-row['reserved_usd'])
                        row['status']='USAGE_EXCEEDS_RESERVATION';campaign._save();raise BudgetExceeded('PROVIDER_EXCEEDS_RESERVATION')
                else:
                    row['usage_uncertain']=True
                row['status']='COMPLETED';campaign._save();campaign.validate_inputs()
                return response
        return Budgeted()


def _score(actual,expected):
    if not isinstance(actual.get('relations'),list):raise ValueError('STRUCTURED_RELATION_METRICS_REQUIRED')
    key=lambda r:json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    observed={key(r) for r in actual['relations']};correct={key(r) for r in expected.get('relations',[])}
    error_relations=len(observed-correct)
    values_ok=actual.get('values',[])==expected.get('values',[])
    correct_answer=(actual['status']==expected['status'] and values_ok and error_relations==0
        and observed==correct
        and set(expected.get('required_evidence',[]))<=set(actual.get('evidence',[])))
    return dict(correct=correct_answer,error_relations=error_relations,
                refused=actual['status']!='FOUND',answerable=expected['status']=='FOUND')


def summarize(rows):
    grouped=defaultdict(list)
    for row in rows:grouped[(row['group'],row['route'],row['repeat'])].append(row)
    per_run=[]
    for (group,route,repeat),items in sorted(grouped.items()):
        latencies=sorted(r['elapsed_ms'] for r in items);answerable=[r for r in items if r['score']['answerable']]
        per_run.append(dict(group=group,route=route,repeat=repeat,N=len(items),accuracy=mean(r['score']['correct'] for r in items),
            answerable_accuracy=mean(r['score']['correct'] for r in answerable) if answerable else None,
            refusal_rate=mean(r['score']['refused'] for r in items),error_relations=sum(r['score']['error_relations'] for r in items),
            mean_ms=mean(latencies),p50_ms=latencies[(len(latencies)-1)//2],p95_ms=latencies[max(0,math.ceil(len(latencies)*.95)-1)],
            cost_usd=sum(r['cost_upper_usd'] for r in items),
            known_actual_cost_usd=sum(r.get('known_actual_cost_usd',0) for r in items),
            unknown_usage_calls=sum(r.get('unknown_usage_calls',0) for r in items)))
    aggregate=[]
    for group,route in sorted({(r['group'],r['route']) for r in per_run}):
        runs=[r for r in per_run if (r['group'],r['route'])==(group,route)]
        metrics={}
        for key in ['accuracy','answerable_accuracy','refusal_rate','error_relations','mean_ms','p50_ms','p95_ms','cost_usd','known_actual_cost_usd','unknown_usage_calls']:
            values=[r[key] for r in runs if r[key] is not None]
            metrics[key]=dict(mean=mean(values),min=min(values),max=max(values)) if values else None
        aggregate.append(dict(group=group,route=route,repetitions=len(runs),metrics=metrics))
    return dict(per_run=per_run,aggregate=aggregate)


def run_matrix(inputs,routes,output,*,budget=None,config=None):
    """Execute public rows first; load Gold only after public execution completes."""
    if set(routes)!={'legacy_only','graph_only'}:raise ValueError('EXACT_TWO_ROUTES_REQUIRED')
    cases=list(inputs.public_cases());rows=[];failure=None
    frozen_configuration=_json(inputs.dependencies['configuration'])
    if config is not None and config!=frozen_configuration:raise ValueError('FROZEN_CONFIGURATION_MISMATCH')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for repeat in range(1,4):
        # Frozen balanced order; no result-dependent selection or reruns.
        for case_id,group,public in cases:
            names=['legacy_only','graph_only'] if repeat%2 else ['graph_only','legacy_only']
            for name in names:
                inputs.verify();before=budget.reserved if budget else 0;started=perf_counter()
                try:actual=routes[name](deepcopy(public))
                except BudgetExceeded:
                    failure='INCOMPLETE_BUDGET';break
                except Exception as error:
                    actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                inputs.verify()
                rows.append(dict(id=case_id,group=group,route=name,repeat=repeat,actual=actual,
                  elapsed_ms=(perf_counter()-started)*1000,cost_upper_usd=(budget.reserved-before) if budget else 0))
            if failure:break
        if failure:break
    _dump(output/'public-executions.json',dict(rows=rows,failure=failure))
    inputs.verify();expected=_json(inputs.expected)['cases']
    for row in rows:row['score']=_score(row['actual'],expected[row['id']])
    report=dict(schema='temporal-graph-dev-report/v1',status=failure or 'COMPLETE',
      split='dev',authoring='外部起草，人工确认',confirmation_label='模拟人工确认',
      input_hashes=inputs.verify(),configuration=frozen_configuration,planned_executions=len(cases)*2*3,
      completed_executions=len(rows),cost_reserved_usd=budget.reserved if budget else 0,
      known_actual_cost_estimate_usd=sum(r['actual_usd'] for r in budget.rows if r['actual_usd'] is not None) if budget else 0,
      unknown_usage_calls=sum(r['actual_usd'] is None for r in budget.rows) if budget else 0,
      **summarize(rows),rows=rows)
    _dump(output/'report.json',report);inputs.verify();return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--questions',required=True,type=Path);parser.add_argument('--expected',required=True,type=Path)
    parser.add_argument('--freeze-file',required=True,type=Path)
    parser.add_argument('--public-fixture',required=True,type=Path)
    parser.add_argument('--confirmed-database',required=True,type=Path)
    parser.add_argument('--configuration',required=True,type=Path)
    parser.add_argument('--freeze',action='store_true');parser.add_argument('--verify',action='store_true')
    args=parser.parse_args();inputs=FrozenDevInputs(args.questions,args.expected,args.freeze_file,
        public_fixture=args.public_fixture,confirmed_database=args.confirmed_database,configuration=args.configuration)
    if args.freeze:print(json.dumps(inputs.create(),ensure_ascii=False,indent=2))
    elif args.verify:print(json.dumps(inputs.verify(),ensure_ascii=False,indent=2))
    else:parser.error('Choose --freeze or --verify; live routes require Host composition with CampaignBudget.')

if __name__=='__main__':main()


class FrozenMeetingDevInputs:
    """External combined B/D dataset. Gold is exposed only to the scorer.

    Original dataset freezes are append-only by user instruction; runtime inputs
    and code are frozen separately once implementation/configuration is final.
    """
    def __init__(self,dataset,freeze,bindings,*,public_fixture,confirmed_database,configuration,runtime_freeze):
        self.questions=Path(dataset).resolve();self.expected=self.questions
        self.freeze=Path(freeze).resolve();self.bindings=Path(bindings).resolve()
        self.runtime_freeze=Path(runtime_freeze).resolve()
        self.dependencies={name:Path(path).resolve() for name,path in dict(public_fixture=public_fixture,
            confirmed_database=confirmed_database,configuration=configuration).items()}

    def verify_dataset(self):
        data=_json(self.freeze)
        if data.get('schema')!='graph-eval-dataset-freeze/v1':raise ValueError('INVALID_DATASET_FREEZE')
        for key,path in [('questions',self.questions),('fixture_bindings',self.bindings)]:
            if data[key]['sha256']!=_sha(path) or data[key]['path']!=str(path):raise ValueError('FROZEN_INPUT_CHANGED')
        source=Path(__file__).resolve().parents[2]/'tests/fixtures/bounded_multihop/dev.json'
        if _sha(source)!=_json(self.bindings)['source_fixture_sha256']:raise ValueError('BOUND_DEV_FIXTURE_CHANGED')
        return data

    def create_runtime_freeze(self):
        self.verify_dataset()
        frozen=dict(schema='graph-eval-meeting-runtime-freeze/v1',dataset=self.verify_dataset(),
            dependencies=FrozenDevInputs._dependencies(self))
        with self.runtime_freeze.open('x',encoding='utf8') as stream:json.dump(frozen,stream,ensure_ascii=False,indent=2)
        return frozen

    def verify(self):
        data=_json(self.runtime_freeze)
        if data['dataset']!=self.verify_dataset() or data['dependencies']!=FrozenDevInputs._dependencies(self):
            raise ValueError('FROZEN_DEPENDENCY_CHANGED')
        return data

    def public_cases(self):
        self.verify();data=_json(self.questions);bindings=_json(self.bindings)
        bound={r['question_id']:r['case_id'] for r in bindings['bindings']}
        if len(bound)!=len(bindings['bindings']):raise ValueError('DUPLICATE_FIXTURE_BINDING')
        if _sha(Path(__file__).resolve().parents[2]/'tests/fixtures/bounded_multihop/dev.json')!=bindings['source_fixture_sha256']:
            raise ValueError('BOUND_DEV_FIXTURE_CHANGED')
        for row in data['questions']:
            public=dict(question=row['question'],fixture_id=bound[row['id']] if row['fixture']=='bounded_dev' else row['fixture'],
                as_of=row.get('as_of'))
            group='D' if row['group']=='D_real_meeting_questions' else ('B_MERIDIAN' if row['fixture']=='meridian' else 'B_BOUNDED') if row['group']=='B_rephrase' else None
            if group is None:raise ValueError('UNRECOGNIZED_EXTERNAL_GROUP:'+row['id'])
            yield row['id'],group,public
        for row in _json(Path(__file__).resolve().parents[2]/'tests/fixtures/bounded_multihop/dev.json')['cases']:
            yield row['id'],'A_BOUNDED',dict(question=row['query'],fixture_id=row['id'],workspace_id=row['workspace_id'],as_of=row.get('as_of'))

    def expected_cases(self):
        self.verify()
        result={r['id']:r['expected'] for r in _json(self.questions)['questions']}
        for row in _json(Path(__file__).resolve().parents[2]/'tests/fixtures/bounded_multihop/dev.json')['cases']:result[row['id']]=row['expected']
        return result
