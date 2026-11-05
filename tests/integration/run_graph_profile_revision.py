"""Three-repeat graph treatment revision with unchanged frozen legacy controls.

The original full matrix and its archived source remain available. This treatment
changes only graph planning schema/metadata. No template/legacy code, model or
legacy request options change. Reuse is declared, never represented as fresh calls.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from time import perf_counter
import argparse,ast,hashlib,json,zipfile
from run_temporal_graph_dev import FrozenMeetingDevInputs,CampaignBudget,PriceReference,BudgetExceeded,_json,_dump
from temporal_graph_routes import TemporalGraphDevRoutes
from deepseek_eval_transport import adapter_factory,assert_off_peak
from graph_eval_date_score import rescore

ALLOWED_GRAPH_CHANGES={'src/linkloom/temporal_graph/planning.py','src/linkloom/ui/graph_investigation.py',
    'src/linkloom/temporal_graph/projection.py','src/linkloom/temporal_graph/meeting_query.py','src/linkloom/temporal_graph/meeting_projection.py',
    'src/linkloom/tools/graph.py','src/linkloom/agents/graph_result.py',
    'src/linkloom/semantic_ingestion/task_relation_question_planner.py','tests/integration/temporal_graph_routes.py','work/graph-eval-protocol.md'}
GRAPH_ONLY_METHODS={'src/linkloom/semantic_ingestion/task_relation_question_planner.py':('InvestigationPlanner','plan_graph'),
    'tests/integration/temporal_graph_routes.py':('TemporalGraphDevRoutes','graph_only')}


def without_graph_method(raw,path):
    tree=ast.parse(raw.decode('utf-8-sig'));class_name,method=GRAPH_ONLY_METHODS[path];removed=0
    for node in tree.body:
        if isinstance(node,ast.ClassDef) and node.name==class_name:
            previous=len(node.body);node.body=[item for item in node.body if not isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef)) or item.name!=method]
            removed+=previous-len(node.body)
    if removed!=1:raise ValueError('EXACT_GRAPH_METHOD_REQUIRED')
    return ast.dump(tree,include_attributes=False)


class RevisionInputs(FrozenMeetingDevInputs):
    def verify(self):
        result=super().verify();config=_json(self.dependencies['configuration'])
        for path,expected in config['additional_frozen_files'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected:raise ValueError('REVISION_DEPENDENCY_CHANGED')
        return result


def verify_control_reuse(config):
    # Whole baseline source is hash-verified before accepting unchanged controls.
    baseline=_json(config['baseline_runtime_freeze'])
    current_root=Path(__file__).resolve().parents[2];changed=[]
    with zipfile.ZipFile(config['baseline_source_archive']) as archive:
        for key,saved in baseline['dependencies'].items():
            if not key.startswith('code:'):continue
            relative=key[5:];raw=archive.read(relative)
            if hashlib.sha256(raw).hexdigest()!=saved['sha256']:raise ValueError('BASELINE_ARCHIVE_CHANGED')
            current=(current_root/relative).read_bytes()
            if hashlib.sha256(current).hexdigest()!=saved['sha256']:
                if relative in GRAPH_ONLY_METHODS and without_graph_method(raw,relative)!=without_graph_method(current,relative):
                    raise ValueError('LEGACY_CONTROL_CODE_CHANGED:'+relative)
                changed.append(relative)
    if set(changed)!=ALLOWED_GRAPH_CHANGES:raise ValueError('LEGACY_CONTROL_CODE_CHANGED:'+str(changed))
    original=_json(config['baseline_configuration'])
    for key in original:
        if key=='input_token_caps':
            # Only the graph reservation changes to match legacy; legacy stays exact.
            if set(config[key])!=set(original[key]) or config[key]['legacy_only']!=original[key]['legacy_only']:
                raise ValueError('LEGACY_CONTROL_CONFIGURATION_CHANGED:'+key)
        elif key=='pricing_valid_until_utc' and config[key]!=original[key]:
            # Expiry is verified price metadata, not a generation setting.
            # The transport still independently refuses every peak-hour call.
            checked=datetime.fromisoformat(config['pricing_rechecked_at_utc']);until=datetime.fromisoformat(config[key])
            if checked.utcoffset() is None or until.utcoffset() is None or not 0<(until-checked).total_seconds()<=3600:
                raise ValueError('INVALID_RECHECKED_PRICE_WINDOW')
        elif config[key]!=original[key]:raise ValueError('LEGACY_CONTROL_CONFIGURATION_CHANGED:'+key)
    rows=_json(config['baseline_public_executions'])['rows']
    if len(rows)!=243 or any('score' in row for row in rows):raise ValueError('GOLD_BLIND_PUBLIC_BASELINE_REQUIRED')
    controls=[deepcopy(row) for row in rows if row['route']=='legacy_only']
    if len(controls)!=159:raise ValueError('FULL_LEGACY_CONTROLS_REQUIRED')
    return controls,changed


def run(inputs,output,budget_path):
    inputs.verify();config=_json(inputs.dependencies['configuration']);controls,changed=verify_control_reuse(config)
    previous=[_json(path) for path in config['previous_budget_ledgers']]
    previous_reserved=sum(row['reserved_usd'] for row in previous)
    price=PriceReference(config['model_id'],config['input_usd_per_million'],config['output_usd_per_million'],config['price_checked_on'],config['official_price_url'])
    budget=CampaignBudget(budget_path,price,validate_inputs=inputs.verify,max_usd=1-previous_reserved)
    cases=[row for row in inputs.public_cases() if row[1] in {'D','B_MERIDIAN'}]
    if len(cases)!=28:raise ValueError('FULL_GRAPH_TREATMENT_REQUIRED')
    graph_calls=config.get('graph_max_provider_calls',1)
    if type(graph_calls) is not int or not 1<=graph_calls<=2:raise ValueError('GRAPH_CALL_CAP_REQUIRED')
    cap=budget.preflight(84*graph_calls*config['input_token_caps']['graph_only'],84*graph_calls*config['max_output_tokens'])
    output=Path(output);output.mkdir(exist_ok=False);calls_dir=output/'provider-calls';calls_dir.mkdir()
    sequence=[0]
    def factory(configuration):
        inner=adapter_factory(configuration)
        class Capture:
            capability=inner.capability
            def complete(self,request):
                sequence[0]+=1;response=inner.complete(request)
                with (calls_dir/f'{sequence[0]:03}.json').open('x',encoding='utf8') as stream:
                    json.dump(dict(request=request.to_dict(),response=response.to_dict()),stream,ensure_ascii=False,indent=2)
                return response
        return Capture()
    routes=TemporalGraphDevRoutes(inputs,budget,factory,output/'routes',preflight=False)
    rows=[];failure=None
    for repeat in range(1,4):
        for key,group,public in cases:
            before=budget.reserved;started=perf_counter();first=len(budget.rows)
            try:
                inputs.verify();assert_off_peak(config);actual=routes.graph_only(deepcopy(public));inputs.verify()
                if actual['status']=='PLANNING_FAILED' and actual.get('reason') in {'GRAPH_TOOL_FAILED','GRAPH_CONTEXT_INVALID','GRAPH_FINAL_NOT_VERIFIED'}:
                    # The original adapter used one umbrella status. Preserve
                    # the actual Host failure category in this revised report.
                    actual['status']='BLOCKED'
            except BudgetExceeded as error:failure=str(error);break
            except ValueError as error:
                # Frozen-state/price errors end the campaign and still report.
                failure=str(error);break
            except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
            calls=budget.rows[first:]
            rows.append(dict(id=key,group=group,route='graph_only',repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,
                cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(c['actual_usd'] or 0 for c in calls),unknown_usage_calls=sum(c['actual_usd'] is None for c in calls)))
            _dump(output/'public-executions.json',dict(rows=rows,failure=failure))
            print(json.dumps(dict(id=key,repeat=repeat,status=actual['status'],new_reserved_usd=round(budget.reserved,6),all_reserved_usd=round(previous_reserved+budget.reserved,6))),flush=True)
        if failure:break
    for row in controls:row['execution_source']='REUSED_FROZEN_LEGACY_CONTROL'
    for row in rows:row['execution_source']='FRESH_GRAPH_SCHEMA_REVISION'
    combined=controls+rows
    all_ledgers=previous+[_json(budget_path)]
    exclusions=[dict(id=key,group=group,route='graph_only',reason='数据不满足图准入（缺生效时间）',accuracy_denominator=False)
        for key,group,public in inputs.public_cases() if group in {'A_BOUNDED','B_BOUNDED'}]
    skeleton=dict(schema='temporal-graph-meeting-dev-report/v1',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,split='dev',
        authoring='外部起草，人工确认',confirmation_label='模拟人工确认',primary_group='D',configuration=config,input_hashes=inputs.verify(),
        planned_executions=243,completed_executions=len(combined),graph_exclusions=exclusions,rows=combined,
        preflight=dict(fresh_graph_executions=84,graph_cost_cap=cap,previous_reserved_usd=previous_reserved,total_cap_usd=1),
        control_reuse=dict(count=159,baseline_public_sha256=config['additional_frozen_files'][config['baseline_public_executions']],graph_only_source_changes=changed,legacy_configuration_unchanged=True),
        cost_reserved_usd=sum(x['reserved_usd'] for x in all_ledgers),
        known_actual_cost_estimate_usd=sum(c['actual_usd'] or 0 for ledger in all_ledgers for c in ledger['calls']),
        unknown_usage_calls=sum(c['actual_usd'] is None for ledger in all_ledgers for c in ledger['calls']))
    _dump(output/'combined-unscored.json',skeleton)
    # Gold/scorer access begins only after all fresh inference ends.
    final=rescore(report_path=output/'combined-unscored.json',dataset=inputs.questions,bindings=inputs.bindings,dataset_freeze=inputs.freeze,
        index=config['confirmation_index'],scorer_freeze=config['scorer_freeze'],final_freeze=config['final_scorer_freeze'],output=output/'scored')
    with (output/'scored'/'report.md').open('a',encoding='utf8') as stream:
        stream.write('\n本轮新执行图路径84次；旧路径159次复用首轮三次冻结对照，未伪称重跑。历轮图配置、诊断及旧路径全部费用都计入总额；表中为本配对行费用，文末总额还包含首轮图和诊断费用。原始首轮报告保留以便同时比较。\n')
    print(json.dumps(dict(status=final['status'],fresh_graph=len(rows),controls_reused=159,all_reserved_usd=final['cost_reserved_usd'])))
    return final


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','dataset_freeze','bindings','public_fixture','confirmed_database','configuration','runtime_freeze'):p.add_argument('--'+name.replace('_','-'),required=True,type=Path)
    p.add_argument('--freeze',action='store_true');p.add_argument('--output',type=Path);p.add_argument('--budget',type=Path)
    a=p.parse_args();inputs=RevisionInputs(a.dataset,a.dataset_freeze,a.bindings,public_fixture=a.public_fixture,confirmed_database=a.confirmed_database,configuration=a.configuration,runtime_freeze=a.runtime_freeze)
    if a.freeze:inputs.create_runtime_freeze();inputs.verify();print('Revision inference frozen');return
    if not a.output or not a.budget:p.error('--output and --budget required')
    run(inputs,a.output,a.budget)

if __name__=='__main__':main()
