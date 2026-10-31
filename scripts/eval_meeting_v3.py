"""Eight-meeting development evaluation, with one shared 24/$0.60 counter.

Only explicitly allowed ES2002/ES2003 transcript files are read. Never read ES2004.
HIGH and HIGH+LOW are separately judged, with unchanged task/field match rules.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import random
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
import eval_tasks as ev
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_extraction import TaskExtractionConfig, build_meeting_extractor

ALLOWED=tuple(f'ES{team}{part}' for team in ('2002','2003') for part in 'abcd')


def load_inputs(data_by_team, archive_path):
    expected=(ROOT/'benchmarks/ami_tasks/ami_public_manual_1.6.2.zip.sha256').read_text('ascii').split()[0]
    with archive_path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:raise ValueError('AMI ZIP checksum mismatch')
    with zipfile.ZipFile(archive_path) as archive:
        metadata=ev.xml_member(archive,'corpusResources/meetings.xml')
    meetings=[]
    for mid in ALLOWED:
        meeting=json.loads((data_by_team[mid[:6]]/'transcripts'/f'{mid}.json').read_text('utf-8'))
        roster=ev.speaker_metadata(metadata,mid)
        if meeting['meeting_id']!=mid or meeting['speakers']!=roster:raise ValueError('AMI metadata mismatch')
        meetings.append({**meeting,'speakers':roster})
    return meetings


def predict(meeting, client, *, task_extraction_mode='legacy'):
    if task_extraction_mode in ('legacy','meeting_v2'):
        return ev.linkloom_predict(meeting,client,speaker_wiring=True,task_extraction_mode=task_extraction_mode)
    if task_extraction_mode!='meeting_v3':raise ValueError('Unknown task_extraction_mode')
    content,mapping='',[]
    for utterance in meeting['utterances']:
        stamp=(datetime(2000,1,1,tzinfo=UTC)+timedelta(seconds=utterance['start'])).isoformat(timespec='milliseconds').replace('+00:00','Z')
        content+=f'[{stamp}] {utterance["speaker"]}: '
        start=len(content);body=utterance['text']
        original=f'{utterance["speaker"]} [{utterance["start"]:.3f}-{utterance["end"]:.3f}]: '
        if meeting['text'][utterance['char_start']:utterance['char_end']]!=original+body+'\n':
            raise ValueError('AMI source conversion changed body')
        content+=body+'\n'
        mapping.append({'start':start,'end':start+len(body),'original_start':utterance['char_start']+len(original),
                        'original_end':utterance['char_end']-1})
    artifact=RawArtifact('ami-eval',meeting['meeting_id'],'timestamped_text',content,
                         ingestion_time=datetime(2000,1,1,tzinfo=UTC))
    mid=meeting['meeting_id']
    ev.new_json(client.output_dir/f'{mid}.v3-source.json',{'content':content,'mapping':mapping,
        'source_version':artifact.artifact_version_id,'content_hash':artifact.content_hash})
    extractor=build_meeting_extractor(client,model_id=ev.GEMINI,
        config=TaskExtractionConfig(task_extraction_mode='meeting_v3',max_input_bytes=client.gemini_max_input_bytes),
        request_guard=lambda r:True,on_request=lambda i,r:ev.new_json(client.output_dir/f'{mid}.v3-request-{i}.json',r.to_dict()))
    batch=extractor.extract(artifact,meeting['speakers'])
    ev.new_json(client.output_dir/f'{mid}.v3-discovery.json',batch.to_dict())
    roster={s['participant_id']:s for s in meeting['speakers']}
    predictions=[]
    for candidate in batch.candidates:
        if not candidate.verify(artifact):raise ValueError('V3 evidence no longer verifies')
        quotes=[]
        for evidence in candidate.evidence:
            start,end=ev.original_span(mapping,evidence.span.char_start,evidence.span.char_end)
            quote=meeting['text'][start:end]
            if quote!=evidence.span.quote:raise ValueError('V3 original AMI quote round-trip failed')
            if quote not in quotes:quotes.append(quote)
        predictions.append({'task':candidate.task,'owner':' and '.join(roster[o]['role'] for o in candidate.owner_ids) or ev.UNKNOWN,
            'deadline':candidate.deadline.raw_text or ev.UNKNOWN,'deadline_kind':candidate.deadline.kind,'resolved_at':None,
            'quote':quotes[0],'evidence_quotes':quotes,'confidence':candidate.confidence,
            'confidence_reason':candidate.confidence_reason,'member_ids':list(candidate.member_ids)})
    return predictions,batch.to_dict()


class Client(ev.Client):
    def generate(self, model,prompt,schema,max_output=4096):
        if model==ev.DEEPSEEK:
            if getattr(self,'resume_source',None) is not None:
                mid,system=self.resume_plan.pop(0)
                data=json.JSONDecoder().raw_decode(prompt.split('\nDATA:\n',1)[1])[0]
                if data['meeting']['meeting_id']!=mid:raise ValueError('Judge resume order mismatch')
                cached=self.resume_source/mid[:6]/f'{mid}.{system}.judge.json'
                if cached.is_file():return json.loads(cached.read_text('utf-8'))
                populations=self.saved_predictions[mid]
                # When every candidate is HIGH, these are the SAME population.
                # Reuse its validated current-meeting decisions, mapping opaque
                # IDs only; a different subset is always separately judged.
                all_cached=self.output_dir/f'{mid}.baseline.judge.json'
                if system=='linkloom' and populations['baseline']==populations['linkloom'] and all_cached.is_file():
                    payload=deepcopy(json.loads(all_cached.read_text('utf-8')))
                    left=random.Random(f'20261029:{mid}:baseline')
                    right=random.Random(f'20261029:{mid}:linkloom')
                    mapping={f'p-{left.getrandbits(64):016x}':f'p-{right.getrandbits(64):016x}' for _ in populations['baseline']}
                    for row in payload['judgments']:row['prediction_id']=mapping[row['prediction_id']]
                    self.identical_population_reuse.append(mid)
                    return payload
            prompt+='\nEach evidence_quotes item is a separate exact quote; never stitch quotes. Before returning, check every prediction appears once and no MATCH shares a gold_id. Confidence must not change task matching: use ONLY the fixed rules above.'
        return super().generate(model,prompt,schema,max_output)


def write_report(path, report, rows):
    def fraction(c):
        n,d=c['numerator'],c['denominator']
        return f'{n}/{d} ({n/d:.1%})' if d else f'{n}/{d}（不适用）'
    lines=['# meeting_v3 开发集评测','',
        'ES2002 和 ES2003 均为开发集，结果仅作开发参考；ES2004 未读取。没有重跑基线或 v2。',
        '全部八场 v3 预测先保存，然后才读取 gold。HIGH+LOW 和仅 HIGH 分别计分；两者不同时独立裁判，完全相同的候选集合可复用本场判断，不从含 LOW 的匹配中删除 LOW 来推算 HIGH 分数。',
        f'模型 {ev.GEMINI}，思考 low，输出上限 32768（含思考）；裁判 {ev.DEEPSEEK}。置信度不改变任务、负责人和期限评分规则。','']
    keys=[('待办准确率','precision'),('待办召回率','recall'),('负责人正确率（匹配任务）','owner_accuracy_on_matched'),
          ('期限正确率（匹配且 gold 明确）','deadline_accuracy_on_matched_stated_gold'),
          ('期限端到端正确率（全部明确期限 gold）','deadline_end_to_end_accuracy_on_stated_gold'),
          ('讨论误判数 / 预测数','discussion_false_positives')]
    for team, result in report['datasets'].items():
        lines += [f'## {team}','', '| 指标 | HIGH+LOW | 仅 HIGH |','| --- | ---: | ---: |']
        for label,key in keys:
            lines.append('| '+' | '.join([label,*[fraction(result['micro'][s]['metric_counts'][key]) for s in ('baseline','linkloom')]])+' |')
        for system,label in [('baseline','HIGH+LOW'),('linkloom','仅 HIGH')]:
            m=result['micro'][system]
            lines += ['',f'{label}：TP={m["tp"]}，FP={m["fp"]}，FN={m["fn"]}；期限弃答 {m["deadline_prediction_abstentions"]}/{m["tp"]+m["fp"]}。','', '漏掉的任务：','']
            subset=[r for r in rows if r['dataset']==team and r['system']==system]
            missed=[r for r in subset if r['category']=='FN']
            lines += [f'- {r["gold_id"]}：{json.loads(r["gold"])["task"]}；中文：{r["gold_task_zh"]}。' for r in missed] or ['无。']
            lines += ['', '多出的候选：','']
            extras=[r for r in subset if r['category'] not in {'MATCH','FN'}]
            lines += [f'- {r["meeting_id"]} / {r["prediction_id"]} [{r["category"]}]：{json.loads(r["prediction"])["task"]}；中文：{r["prediction_task_zh"]}。理由：{r["reason"]}' for r in extras] or ['无。']
    lines += ['', '## 用量与边界','',f'累计 {report["counter"]["requests"]}/24 次请求；API 用量估算费用 ${report["actual_cost_usd_from_usage"]:.9f}/$0.60。未自动重试。',
        '匹配规则沿用 meeting-task-match/v2：未解析代词负责人判错；编号按本场 AMI 元数据角色换算；明确指向同一次会议的相对时长和事件等价。期限只对 gold 明确写出的任务计分，未知期限单独统计。',
        'LOW 候选引用校验与 HIGH 相同；不确定承诺仅为待审核提议，不自动确认。产品审核页显示置信度和理由，人工核对后才标记本地确认/拒绝，不写入权威决策记忆。',
        '源码与配置冻结元数据见 v3_frozen.json；完整模型响应和转录留在被忽略的 work/。AMI Meeting Corpus / AMI Project / AMI Consortium，CC BY 4.0，来源 https://groups.inf.ed.ac.uk/ami/corpus/ 。','']
    if report.get('prior_failed_attempt'):
        prior=report['prior_failed_attempt']
        lines += ['## 评分传输中断与累计预算','',
            f'首次运行完成全部八场预测和六份有效裁判后，第 15 次请求（ES2002d 全候选裁判）发生 Provider transport/JSON failure。已知费用 ${prior["actual_cost_usd_from_usage"]:.9f}；未知请求保留的费用上界 ${prior["counter"]["reserved_upper_bound_usd"]-prior["actual_cost_usd_from_usage"]:.9f}，没有当作免费。',
            f'仅恢复评分，所有预测和已保存有效裁判复用；新旧累计 {report["counter"]["requests"]} 次，已知费用 ${report["actual_cost_usd_from_usage"]:.9f}，含未知请求的保守总上界 ${report["counter"]["reserved_upper_bound_usd"]:.9f}。真实账户费用不能从失败请求确定。',
            f'集合完全相同的 HIGH 口径复用本场全候选判断（仅重映射不透明预测 ID）：{report.get("identical_population_reuse",[])}。包含 LOW 的场次仍分别裁判；没有重新调用抽取模型或修改提示词。','']
    path.write_text('\n'.join(lines),encoding='utf-8')


def resume_manifest(meetings, predictions, gold_by_team):
    # Called only AFTER all predictions exist. Never read gold during extraction.
    def digest(value):
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
            separators=(',',':')).encode('utf-8')).hexdigest()
    from dataclasses import asdict
    from linkloom.semantic_ingestion import meeting_tasks_v3 as v3
    return {'version':'meeting-v3-resume/1',
        'meetings':{m['meeting_id']:digest(m) for m in meetings},
        'predictions':digest(predictions),
        'gold':{team:hashlib.sha256(path.read_bytes()).hexdigest() for team,path in gold_by_team.items()},
        'protocol':digest({'prompt':v3.PROMPT,'schema':v3.SCHEMA,'rules':ev.MATCH_RULES,
            'config':asdict(TaskExtractionConfig(task_extraction_mode='meeting_v3')),
            'models':[ev.GEMINI,ev.DEEPSEEK],
            'source':{name:hashlib.sha256((ROOT/name).read_text(encoding='utf-8').encode()).hexdigest()
                for name in ('scripts/eval_meeting_v3.py','scripts/eval_tasks.py',
                    'src/linkloom/semantic_ingestion/meeting_tasks_v3.py',
                    'src/linkloom/semantic_ingestion/meeting_tasks_v2.py')}})}


def validate_resume_manifest(source, current):
    stored=json.loads((source/'resume_manifest.json').read_text(encoding='utf-8'))
    if stored!=current:
        raise ValueError('Resume inputs or extraction/scoring protocol changed; cached judgments cannot be reused')


def run(data_by_team,archive,gold_by_team,out,*,live=False,mode='meeting_v3',resume=None):
    if not live:raise ValueError('Explicit --live required; no requests made')
    if mode!='meeting_v3':raise ValueError('Development run is explicitly meeting_v3 only')
    out.mkdir(parents=True,exist_ok=False)
    client=Client(ev.Counter(24,0.6),live=True,gemini_max_input_bytes=192*1024)
    prior=None
    if resume is not None:
        prior=json.loads((resume/'failure.json').read_text('utf-8'))
        client.counter.requests=prior['counter']['requests']
        client.counter.usd_upper_bound=prior['counter']['reserved_upper_bound_usd']
        client.actual_cost_usd=prior['actual_cost_usd_from_usage']
        client.usage_by_model=deepcopy(prior['usage_by_model'])
        client.resume_source=resume
        client.resume_plan=[(f'{team}{p}',s) for team in ('ES2002','ES2003') for p in 'abcd' for s in ('baseline','linkloom')]
        client.identical_population_reuse=[]
    client.output_dir=out
    try:
        meetings=load_inputs(data_by_team,archive)
        if resume is not None:
            predictions=json.loads((resume/'predictions.json').read_text('utf-8'))
            if set(predictions)!=set(ALLOWED):raise ValueError('Incomplete saved predictions')
            for populations in predictions.values():
                if populations['linkloom']!=[p for p in populations['baseline'] if p['confidence']=='HIGH']:
                    raise ValueError('Saved HIGH subset mismatch')
            client.saved_predictions=predictions
        else:
            predictions={}
            for meeting in meetings:
                items,batch=predict(meeting,client,task_extraction_mode=mode)
                predictions[meeting['meeting_id']]={'baseline':items,'linkloom':[p for p in items if p['confidence']=='HIGH']}
                print(json.dumps({'stage':'v3_prediction','meeting_id':meeting['meeting_id'],'all':len(items),
                    'high':sum(p['confidence']=='HIGH' for p in items),'rejected':len(batch['rejected'])}),flush=True)
        manifest=resume_manifest(meetings,predictions,gold_by_team)
        if resume is not None:validate_resume_manifest(resume,manifest)
        ev.new_json(out/'resume_manifest.json',manifest)
        ev.new_json(out/'predictions.json',predictions)
        report={'datasets':{},'mode':mode,'all_predictions_completed_before_gold':True,
                'populations':{'baseline':'HIGH+LOW','linkloom':'HIGH'}}
        all_rows=[]
        for team in ('ES2002','ES2003'):
            target=out/team;target.mkdir();client.output_dir=target
            subset=[m for m in meetings if m['meeting_id'].startswith(team)]
            ev.finish_evaluation(subset,gold_by_team[team],target,client,predictions)
            report['datasets'][team]=json.loads((target/'results.json').read_text('utf-8'))
            all_rows += [{**r,'dataset':team} for r in json.loads((target/'judgments.json').read_text('utf-8'))]
        report.update(counter=client.counter.summary(),actual_cost_usd_from_usage=client.actual_cost_usd,
                      usage_by_model=client.usage_by_model,prior_failed_attempt=prior,
                      identical_population_reuse=getattr(client,'identical_population_reuse',[]))
        ev.new_json(out/'results.json',report);ev.new_json(out/'judgments.json',all_rows)
        write_report(out/'results.md',report,all_rows)
    except Exception as error:
        ev.new_json(out/'failure.json',{'error_type':type(error).__name__,'message':str(error),
            'counter':client.counter.summary(),'actual_cost_usd_from_usage':client.actual_cost_usd,
            'usage_by_model':client.usage_by_model})
        raise
    finally:print(json.dumps({'counter':client.counter.summary(),'cost':client.actual_cost_usd}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('es2002-data','es2003-data','archive','es2002-gold','es2003-gold','out'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--task-extraction-mode',choices=('legacy','meeting_v2','meeting_v3'),default='legacy')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--resume-from',type=Path,help='Reuse all saved predictions and valid judgments; carry every request and unknown cost reservation')
    args=parser.parse_args()
    run({'ES2002':args.es2002_data,'ES2003':args.es2003_data},args.archive,
        {'ES2002':args.es2002_gold,'ES2003':args.es2003_gold},args.out,live=args.live,mode=args.task_extraction_mode,resume=args.resume_from)
