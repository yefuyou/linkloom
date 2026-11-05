"""Bounded L6 optimization rounds; immutable dev scorer and separate $1 ledger."""
from pathlib import Path
from collections import defaultdict
import json
from run_graph_profile_revision import run as run_revision
from run_temporal_graph_dev import _json,_dump


def case_summary(report):
    grouped=defaultdict(list)
    for row in report['rows']:
        if row['route']=='graph_only' and row['group'] in {'D','B_MERIDIAN'}:grouped[row['id']].append(row)
    if len(grouped)!=28 or any(sorted(r['repeat'] for r in rows)!=[1,2,3] for rows in grouped.values()):raise ValueError('THREE_REPEATS_REQUIRED')
    return {key:dict(correct=sum(r['score']['correct'] for r in rows),statuses=[r['actual']['status'] for r in sorted(rows,key=lambda r:r['repeat'])],
        errors=sum(r['score']['error_relations'] for r in rows),failed_checks=sorted({k for r in rows for k,v in r['score'].get('checks',{}).items() if not v})) for key,rows in grouped.items()}


def add_comparison(final,previous,*,config,output):
    before=case_summary(previous);after=case_summary(final)
    changes=[dict(id=key,previous=before[key],current=after[key],delta_correct=after[key]['correct']-before[key]['correct']) for key in sorted(after)]
    better=[r['id'] for r in changes if r['delta_correct']>0];worse=[r['id'] for r in changes if r['delta_correct']<0]
    primary=next(r for r in final['aggregate'] if r['group']=='D' and r['route']=='graph_only')
    target=primary['metrics']['accuracy']['mean']>=.75 and primary['metrics']['error_relations']['max']==0
    final['optimization']=dict(stage='L6-optimization',round=config['optimization_round'],stage_cap_usd=1,
        historical_costs_excluded=True,baseline='v3 single-call planning; intent review disabled',previous_report=config['previous_round_report'],
        stage_reserved_usd=final['cost_reserved_usd'],stage_remaining_usd=1-final['cost_reserved_usd'],
        better=better,worse=worse,per_case=changes,target_met=target,stop=target or config['optimization_round']>=6,
        fix_category=config['fix_category'],generic_fix=config['generic_fix'],regressions_require_explanation=bool(worse))
    _dump(Path(output)/'scored/report.json',final)
    diff=Path(output)/'case-comparison.md'
    lines=['# 本轮逐题比较','',f"优化第{config['optimization_round']}轮；相对 {config['previous_round_report']}。三次正确次数，不挑单次结果。",'',
        '|题号|上轮正确/3|本轮正确/3|变化|上轮状态|本轮状态|','|---|---:|---:|---:|---|---|']
    for row in changes:lines.append('| '+ ' | '.join([row['id'],str(row['previous']['correct']),str(row['current']['correct']),str(row['delta_correct']),','.join(row['previous']['statuses']),','.join(row['current']['statuses'])])+' |')
    lines+=['', '变好：'+(', '.join(better) or '无'),'变坏：'+(', '.join(worse) or '无'),f"本阶段累计预留${final['cost_reserved_usd']:.6f}；剩余${1-final['cost_reserved_usd']:.6f}。此前阶段费用不在本阶段重复计费。",f"停止目标达到：{target}。"]
    diff.write_text('\n'.join(lines)+'\n',encoding='utf8')
    with (Path(output)/'scored/report.md').open('a',encoding='utf8') as f:
        f.write(f"\n本报告累计费用仅为新优化阶段，之前L6阶段另账；第{config['optimization_round']}轮，最多6轮。模型采样配置保持v3单次规划，无意图复核。逐题比较见{diff.name}。变好：{', '.join(better) or '无'}；变坏：{', '.join(worse) or '无'}。目标达到：{target}。\n")
    return final


def run_round(inputs,output,budget_path):
    config=_json(inputs.dependencies['configuration']);number=config['optimization_round'];root=Path('work/graph-opt')
    if type(number) is not int or not 1<=number<=6:raise ValueError('MAX_SIX_ROUNDS')
    expected=[str(root/f'budget-r{i}.json') for i in range(1,number)]
    if [Path(p).resolve() for p in config['previous_budget_ledgers']]!=[Path(p).resolve() for p in expected]:raise ValueError('STAGE_LEDGER_SEQUENCE_REQUIRED')
    existing={p.name for p in root.glob('budget-r*.json')}
    if existing!={Path(p).name for p in expected}:raise ValueError('EXISTING_LEDGER_CANNOT_BE_SKIPPED_OR_RETRIED')
    if Path(budget_path)!=root/f'budget-r{number}.json':raise ValueError('STAGE_LEDGER_PATH_REQUIRED')
    if config.get('graph_alignment_review') is not False or config.get('graph_max_provider_calls')!=1:raise ValueError('SINGLE_CALL_V3_BASELINE_REQUIRED')
    previous=_json(config['previous_round_report'])
    if previous['status']!='COMPLETE':raise ValueError('PREVIOUS_ROUND_INCOMPLETE')
    if number>1 and previous.get('optimization',{}).get('stop'):raise ValueError('OPTIMIZATION_STOP_REQUIRED')
    final=run_revision(inputs,output,budget_path)
    return add_comparison(final,previous,config=config,output=output)
