"""Independent offline scoring correction, frozen before reading campaign outputs.

Corrections arise from synthetic Reviewer counterexamples, never from campaign
answers. Apply identically to every case/route/repetition; no provider calls.
Inference/runtime freeze remains v1. Original questions/bindings remain read-only.
"""
from pathlib import Path
import argparse,hashlib,json
from copy import deepcopy
from meeting_graph_scoring import MeetingDevScorer,norm,triple,walk
from run_temporal_graph_dev import _json,_dump,summarize


class CorrectedScorer(MeetingDevScorer):
    def __call__(self,case_id,actual,expected,public,documents):
        result=super().__call__(case_id,actual,expected,public,documents)
        if case_id.startswith('B'):
            # Only structured selected values may satisfy a requested answer.
            # Typed relations establish its role; arbitrary answer/background
            # substrings are deliberately excluded.
            selected={norm(v) for v in actual.get('values',[]) if isinstance(v,(str,int,float))}
            observed={triple(r['subject'],r['edge_type'],r['target']) for r in actual['relations']}
            checks=[]
            for role,value in expected.get('values',{}).items():
                value=norm(value)
                if role in {'owner','intermediate_owner'}:
                    role_ok=any(s==value and k=='owner_of' for s,k,t in observed)
                elif role=='leader':role_ok=any(t==value and k=='reports_to' for s,k,t in observed)
                elif role in {'plan','successor','new_plan'}:role_ok=any(s==value and k=='supersedes' for s,k,t in observed)
                elif role=='project':role_ok=any(t==value and k=='alias_of' for s,k,t in observed)
                elif role=='effective_date':role_ok=True
                else:raise ValueError('UNKNOWN_EXTERNAL_EXPECTED_ROLE:'+role)
                checks.append(role_ok and (role=='intermediate_owner' or value in selected))
            result['checks']['values']=all(checks)
        if case_id.startswith('D') and not case_id.startswith('DEV-'):
            outputs=actual.get('structured_outputs',[]);steps=actual.get('result_steps',[])
            records=[r for row in steps for r in row.get('records',[])]
            selected=[r for output in outputs for value in output['values'] for r in walk(value)]
            consistent=True
            for record in records+[r for r in selected if r.get('kind') in {'task','decision_record'}]:
                if record['kind']=='decision_record':
                    identifier=self.decision_ids.get(record.get('record_id'));given=self.decisions.get(identifier)
                    if given is None:consistent=False;continue
                    meeting,line=given['evidence'].split('#')
                    if record.get('value')!=self.meetings[meeting]['lines'][int(line)-1]:consistent=False
                elif record['kind']=='task':
                    identifier=self.task_ids.get(record.get('task_id'));given=self.tasks.get(identifier)
                    if given is None or record.get('title')!=given['title']:consistent=False;continue
                    allowed=set()
                    for state in given['history']:
                        month,day=map(int,state['deadline'].split('/'));year=self.meetings[state['meeting']]['date'][:4]
                        allowed.add((state['owner'],f'{year}-{month:02d}-{day:02d}'))
                    states=record.get('states',[])+([record['current']] if record.get('current') else [])
                    for state in states:
                        fields=state['fields'];deadline=fields.get('deadline') or {}
                        if isinstance(deadline,dict):deadline=deadline.get('deadline')
                        if fields.get('title')!=given['title'] or (fields.get('owner'),deadline) not in allowed:consistent=False
            result['checks']['fixture_record_consistency']=consistent
            target={'D07':'T3','D08':'T3','D10':'T2','D14':'T1'}.get(case_id)
            if target:
                field_steps={o['step'] for o in outputs if o['field'] in {'owner','deadline','record','history'}}
                bound=[r for row in steps if row['id'] in field_steps for r in row.get('records',[]) if r.get('kind')=='task']
                result['checks']['requested_task_identity']=bool(bound) and {self.task_ids.get(r['task_id']) for r in bound}=={target}
            if case_id=='D11':
                result['checks']['channels']=any(self.decision_ids.get(r.get('record_id'))=='D2' and r.get('valid_to') is None and '微信支付' in r.get('value','') and '支付宝' in r.get('value','') for r in selected)
            if case_id=='D15':
                # Expected asks only names/owners, so no deadline requirement.
                candidates=set()
                for row in steps:
                    if row['status']!='AMBIGUOUS':continue
                    for r in row.get('records',[]):
                        if r.get('kind')=='task':candidates.add((self.task_ids.get(r.get('task_id')),r.get('current',{}).get('fields',{}).get('owner')))
                result['checks']['candidates']=candidates=={('T1','李明'),('T4','周宁')}
                # Candidate lists may legitimately omit unrequested deadlines.
                if not records:result['checks']['fixture_record_consistency']=False
                else:
                    result['checks']['fixture_record_consistency']=all(self.task_ids.get(r.get('task_id')) in self.tasks and r.get('title')==self.tasks[self.task_ids[r['task_id']]]['title'] for r in records if r.get('kind')=='task')
        result['correct']=all(result['checks'].values())
        return result


def code_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def freeze(path):
    data=dict(schema='graph-eval-scorer-correction/v1',sha256=code_hash(),
        frozen_before_campaign_outputs_read=True,reason='Independent synthetic Reviewer counterexamples: B substring false positive; D08 identity; D11 content; D15 extra deadline',
        parent_scorer_sha256=hashlib.sha256(Path(__file__).with_name('meeting_graph_scoring.py').read_bytes()).hexdigest())
    with Path(path).open('x',encoding='utf8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    return data


def rescore(report_path,dataset,bindings,dataset_freeze,index,scorer_freeze,output):
    marker=_json(scorer_freeze)
    if marker['sha256']!=code_hash() or marker['parent_scorer_sha256']!=hashlib.sha256(Path(__file__).with_name('meeting_graph_scoring.py').read_bytes()).hexdigest():raise ValueError('SCORER_CHANGED')
    frozen=_json(dataset_freeze)
    for key,path in [('questions',dataset),('fixture_bindings',bindings)]:
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=frozen[key]['sha256']:raise ValueError('FROZEN_DATA_CHANGED')
    # This is the first place this correction reads any campaign output.
    report=deepcopy(_json(report_path));given=_json(dataset);expected={r['id']:r['expected'] for r in given['questions']}
    bound={r['question_id']:r['case_id'] for r in _json(bindings)['bindings']}
    dependencies=report['input_hashes']['dependencies']
    for name in ('public_fixture','confirmed_database','configuration','configuration:confirmation_index'):
        saved=dependencies[name]
        if hashlib.sha256(Path(saved['path']).read_bytes()).hexdigest()!=saved['sha256']:raise ValueError('SCORING_DEPENDENCY_CHANGED')
    if Path(index).resolve()!=Path(dependencies['configuration:confirmation_index']['path']).resolve():raise ValueError('CONFIRMATION_INDEX_MISMATCH')
    fixture_path=Path(dependencies['public_fixture']['path']);public_fixtures=_json(fixture_path)
    fixtures={r['case_id']:r for r in public_fixtures['cases']}
    public={r['id']:dict(fixture_id=bound[r['id']] if r['fixture']=='bounded_dev' else r['fixture']) for r in given['questions']}
    dev_path=Path(__file__).resolve().parents[2]/'tests/fixtures/bounded_multihop/dev.json'
    if hashlib.sha256(dev_path.read_bytes()).hexdigest()!=_json(bindings)['source_fixture_sha256']:raise ValueError('BOUND_DEV_CHANGED')
    for row in _json(dev_path)['cases']:
        expected[row['id']]=row['expected'];public[row['id']]=dict(fixture_id=row['id'],workspace_id=row['workspace_id'])
    scorer=CorrectedScorer(given['fixtures'],_json(index))
    for row in report['rows']:
        item=public[row['id']];fixture=fixtures[item['fixture_id']]
        docs=[d for d in fixture['documents'] if d['workspace_id']==(item.get('workspace_id') or fixture['workspace_id'])]
        row['score']=scorer(row['id'],row['actual'],expected[row['id']],item,docs)
    report.update(summarize(report['rows']))
    report['scoring_correction']=marker
    report['scoring_correction']['inference_unchanged']=True
    report['scoring_correction']['paid_reruns']=0
    output=Path(output);output.mkdir(exist_ok=False)
    _dump(output/'report.json',report)
    lines=['# L6 开发集对比（独立修正评分）','',f"固定执行：{report['completed_executions']}/{report['planned_executions']}；状态：{report['status']}。仅开发集。",'',
        'D 为主指标；B Meridian 为改写；A/B bounded_dev 为旧路径回归。25题图排除：数据不满足图准入（缺生效时间），不进入正确率或拒答率分母。',
        '模拟人工确认；题目外部起草、人工确认且原始哈希不变。',
        '评分修正来自独立合成反例，在读取运行结果前冻结；同一批全部路线/题目/重复统一重评，未调用模型。原始执行及旧报告均保留。','',
        '| 分层 | 路线 | 正确率 均值[最小,最大] | 拒答率 | 错误关系数 | 延迟ms | 已知usage费用USD | 费用上界USD |',
        '|---|---|---|---|---|---|---|---|']
    def interval(metric):return f"{metric['mean']:.4f}[{metric['min']:.4f},{metric['max']:.4f}]"
    for row in report['aggregate']:
        m=row['metrics'];lines.append('| '+' | '.join([row['group'],row['route']]+[interval(m[k]) for k in ['accuracy','refusal_rate','error_relations','mean_ms','known_actual_cost_usd','cost_usd']])+' |')
    lines+=['',f"已知usage按无缓存价格估算：${report['known_actual_cost_estimate_usd']:.6f}；累计预留上界：${report['cost_reserved_usd']:.6f}；usage不明调用：{report['unknown_usage_calls']}。",
        '三次固定配置；正确率是按人工预期转写的结构化断言及证据检查，不使用付费模型裁判，也不声称是完整的自然语言蕴含审查。',
        '推理运行的代码、配置、SQLite和原件哈希见report.json input_hashes；评分修正哈希另列scoring_correction。']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze',type=Path)
    for name in ('report','dataset','bindings','dataset_freeze','index','scorer_freeze','output'):p.add_argument('--'+name.replace('_','-'),type=Path)
    a=vars(p.parse_args());target=a.pop('freeze')
    if target:print(json.dumps(freeze(target)));return
    a['report_path']=a.pop('report');report=rescore(**a);print(json.dumps(dict(status=report['status'],completed=report['completed_executions'])))

if __name__=='__main__':main()
