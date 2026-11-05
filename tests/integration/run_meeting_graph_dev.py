"""Frozen latest L6 DEV scope: D/B-Meridian dual, bounded legacy regression."""
from copy import deepcopy
from pathlib import Path
from time import perf_counter
import argparse,json
from run_temporal_graph_dev import (FrozenMeetingDevInputs,CampaignBudget,PriceReference,BudgetExceeded,_json,_dump,summarize)
from temporal_graph_routes import TemporalGraphDevRoutes
from deepseek_eval_transport import adapter_factory,assert_off_peak
from linkloom.runtime.team_decision_planner import plan_relation_investigation,plan_team_decision_query

EXCLUSION='数据不满足图准入（缺生效时间）'


def route_names(group,repeat):
    if group in {'A_BOUNDED','B_BOUNDED'}:return ['legacy_only']
    if group not in {'D','B_MERIDIAN'}:raise ValueError('UNKNOWN_SCOPE')
    return ['legacy_only','graph_only'] if repeat%2 else ['graph_only','legacy_only']


def preflight(inputs,budget,config):
    cases=list(inputs.public_cases());legacy=0;graph=0
    for _,group,public in cases:
        question=public['question']
        # This is a conservative superset of the legacy compiler calls made by
        # the route; explicit date prefix can remove hits, never grant more calls.
        candidates=[question]
        if public.get('as_of'):candidates.append('As of '+public['as_of']+', '+question)
        if any(plan_relation_investigation(q) or plan_team_decision_query(q) for q in candidates):legacy+=3*config['legacy_max_turns']
        if group in {'D','B_MERIDIAN'}:graph+=3
    total_in=legacy*config['input_token_caps']['legacy_only']+graph*config['input_token_caps']['graph_only']
    maximum=budget.preflight(total_in,(legacy+graph)*config['max_output_tokens'])
    return dict(cases=len(cases),executions=sum(len(route_names(g,r)) for _,g,_ in cases for r in range(1,4)),legacy_call_cap=legacy,graph_call_cap=graph,input_token_cap=total_in,cost_upper_usd=maximum)


def run(inputs,budget,output,*,factory=adapter_factory):
    inputs.verify();config=_json(inputs.dependencies['configuration']);assert_off_peak(config)
    ceiling=preflight(inputs,budget,config)
    output=Path(output);output.mkdir(exist_ok=False)
    _dump(output/'preflight.json',ceiling)
    routes=TemporalGraphDevRoutes(inputs,budget,factory,output/'routes',preflight=False)
    cases=list(inputs.public_cases());rows=[];exclusions=[];failure=None
    for key,group,_ in cases:
        if group in {'A_BOUNDED','B_BOUNDED'}:exclusions.append(dict(id=key,group=group,route='graph_only',reason=EXCLUSION,accuracy_denominator=False))
    for repeat in range(1,4):
        for key,group,public in cases:
            for name in route_names(group,repeat):
                inputs.verify();assert_off_peak(config);before=budget.reserved;started=perf_counter();first_call=len(budget.rows)
                try:actual=routes.routes()[name](deepcopy(public))
                except BudgetExceeded as error:failure=str(error);break
                except Exception as error:actual=dict(status='RUNTIME_FAILURE',values=[],relations=[],evidence=[],failure_type=type(error).__name__)
                inputs.verify()
                calls=budget.rows[first_call:]
                row=dict(id=key,group=group,route=name,repeat=repeat,actual=actual,elapsed_ms=(perf_counter()-started)*1000,
                    cost_upper_usd=budget.reserved-before,known_actual_cost_usd=sum(c['actual_usd'] or 0 for c in calls),unknown_usage_calls=sum(c['actual_usd'] is None for c in calls))
                rows.append(row)
                _dump(output/'public-executions.json',dict(rows=rows,failure=failure))
                print(json.dumps(dict(id=key,group=group,route=name,repeat=repeat,status=actual['status'],calls=len(calls),reserved_usd=round(budget.reserved,6))),flush=True)
            if failure:break
        if failure:break
    _dump(output/'public-executions.json',dict(rows=rows,failure=failure))
    # Only after every public execution has ended may the scorer receive Gold.
    inputs.verify()
    from meeting_graph_scoring import MeetingDevScorer
    expected=inputs.expected_cases();dataset=_json(inputs.questions)
    scorer=MeetingDevScorer(dataset['fixtures'],_json(Path(config['confirmation_index'])))
    public_by_id={key:public for key,_,public in cases}
    for row in rows:
        public=public_by_id[row['id']];fixture=routes.fixtures[public['fixture_id']]
        docs=[d for d in fixture['documents'] if d['workspace_id']==(public.get('workspace_id') or fixture['workspace_id'])]
        row['score']=scorer(row['id'],row['actual'],expected[row['id']],public,docs)
    report=dict(schema='temporal-graph-meeting-dev-report/v1',status='INCOMPLETE' if failure else 'COMPLETE',failure=failure,
        split='dev',authoring='外部起草，人工确认',confirmation_label='模拟人工确认',primary_group='D',
        input_hashes=inputs.verify(),configuration=config,preflight=ceiling,graph_exclusions=exclusions,
        planned_executions=ceiling['executions'],completed_executions=len(rows),cost_reserved_usd=budget.reserved,
        known_actual_cost_estimate_usd=sum(c['actual_usd'] or 0 for c in budget.rows),unknown_usage_calls=sum(c['actual_usd'] is None for c in budget.rows),
        **summarize(rows),rows=rows)
    _dump(output/'report.json',report)
    # Human-readable table preserves strata and the three repeated intervals.
    lines=['# L6 开发集对比','',f"状态：{report['status']}；执行 {len(rows)}/{ceiling['executions']}。仅开发集。",'',
      '确认：模拟人工确认。题目：外部起草，人工确认；冻结文件只读。',
      'D 为主要指标；B Meridian 为改写；A/B bounded_dev 仅旧路径回归。',
      '图排除25题：数据不满足图准入（缺生效时间），未计入图正确率或拒答率。','',
      '| 分层 | 路线 | 三轮正确率 均值[最小,最大] | 拒答率 | 错误关系数 | 延迟ms | 已知usage费用USD | 费用上界USD |',
      '|---|---|---|---|---|---|---|---|']
    def interval(metric):return f"{metric['mean']:.4f}[{metric['min']:.4f},{metric['max']:.4f}]"
    for row in report['aggregate']:
        m=row['metrics'];lines.append('| '+' | '.join([row['group'],row['route']]+[interval(m[k]) for k in ['accuracy','refusal_rate','error_relations','mean_ms','known_actual_cost_usd','cost_usd']])+' |')
    lines+=['',f"已知usage估算：${report['known_actual_cost_estimate_usd']:.6f}；累计预留上界：${budget.reserved:.6f}；usage不明调用：{report['unknown_usage_calls']}。未假设缓存命中折扣。",
        '每个配置三次固定顺序运行；不按结果换模型、改提示词、改题或重跑。逐题断言见 report.json。',
        'D 混合查询按冻结程序中的类型断言检查完整列表、时间状态、变更链和边界；无付费模型判分。']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf8');inputs.verify();return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','dataset_freeze','bindings','public_fixture','confirmed_database','configuration','runtime_freeze'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--freeze',action='store_true');p.add_argument('--preflight',action='store_true');p.add_argument('--run',action='store_true')
    p.add_argument('--output',type=Path);p.add_argument('--budget',type=Path)
    a=p.parse_args();inputs=FrozenMeetingDevInputs(a.dataset,a.dataset_freeze,a.bindings,public_fixture=a.public_fixture,confirmed_database=a.confirmed_database,configuration=a.configuration,runtime_freeze=a.runtime_freeze)
    if a.freeze:print(json.dumps(inputs.create_runtime_freeze(),ensure_ascii=False));return
    config=_json(a.configuration);price=PriceReference(config['model_id'],config['input_usd_per_million'],config['output_usd_per_million'],config['price_checked_on'],config['official_price_url'])
    if not a.budget:p.error('--budget required')
    budget=CampaignBudget(a.budget,price,validate_inputs=inputs.verify)
    if a.preflight:print(json.dumps(preflight(inputs,budget,config)));return
    if not a.run or not a.output:p.error('--run and --output required')
    report=run(inputs,budget,a.output);print(json.dumps(dict(status=report['status'],completed=report['completed_executions'],reserved_usd=budget.reserved)))

if __name__=='__main__':main()
