"""Manual, zero-provider replay of saved graph plans through the verified route.

Reads an explicit archive; never imports a provider adapter or rewrites a seal.
D3 is a development validation set after this replay, not an independent holdout.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests/integration'))

from linkloom.indexing import SentenceTransformerEmbedder
from linkloom.temporal_graph.entity_locator import LocalMentionLocator, LocatorConfig
from linkloom.temporal_graph.mention_planning import compile_plan
from linkloom.temporal_graph.models import GraphLimits
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.ui.graph_investigation import GraphInvestigationService
from tests.meeting_graph_eval_support import task_candidate_source
from temporal_graph_routes import _label, _relation, _empty
from graph_eval_date_score import DateParityScorer
from graph_acceptance_v2 import AcceptanceEvaluator
from graph_acceptance_extensions import AcceptanceEvaluator as D3Evaluator
from run_graph_continuation import unknown_positive


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def blank(actual):return not (actual.get('answer') or '').strip() or '未找到已确认' in (actual.get('answer') or '')


def verify_scorers(archive):
    names={'graph_eval_date_score.py','rescore_meeting_graph_dev.py','meeting_graph_scoring.py',
           'graph_acceptance_v2.py','holdout_graph_acceptance.py','graph_acceptance_extensions.py'}
    hashes={}
    for seal in ['graph-contract-repair/runtime-freeze.json','graph-contract-repair/D3/rules-freeze.json',
                 'graph-opt-r3/scorer-v2-freeze.json']:
        for path,expected in read(archive/seal)['hashes'].items():
            name=Path(path).name
            if name in names:
                local=ROOT/'tests/integration'/name
                if sha(local)!=expected or sha(path)!=expected:raise ValueError('FROZEN_SCORER_CHANGED:'+name)
                hashes[name]=expected
    if set(hashes)!=names:raise ValueError('INCOMPLETE_SCORER_SEAL')
    return hashes


def scoring(archive):
    dev=read(archive/'graph-eval-questions.json');d2=read(archive/'graph-eval-holdout-D2.json')
    d3=read(archive/'graph-eval-holdout-D3.json');index=read(archive/'graph-eval-confirmations-v1.json')
    expected={q['id']:q['expected'] for data in (dev,d2,d3) for q in data['questions']}
    questions={q['id']:q for data in (dev,d2,d3) for q in data['questions']}
    evaluators={'D':DateParityScorer(dev['fixtures'],index),
                'D2':AcceptanceEvaluator(next(iter(d2['fixtures'].values())),read(archive/'graph-holdout-D2/confirmation-index.json')),
                'D3':D3Evaluator(next(iter(d3['fixtures'].values())),read(archive/'graph-contract-repair/D3/adapter-confirmation-index.json'))}
    assertions={row['id']:row['assertions'] for path in ['graph-holdout-D2/assertions.json','graph-contract-repair/D3/assertions.json'] for row in read(archive/path)['rows']}
    def score(row,actual):
        identifier=row['id'];group=row['group']
        if group=='D':
            question=questions[identifier]
            public=dict(question=question['question'],fixture_id=question['fixture'],as_of=question.get('as_of'))
            # Identical public-source construction to the frozen RAG comparison.
            fixtures=read(archive/'graph-opt-r3/public-fixtures.json')['cases']
            fixture=next(f for f in fixtures if f['case_id']==question['fixture'])
            docs=[d for d in fixture['documents'] if d['workspace_id']==fixture['workspace_id']]
            result=evaluators[group](identifier,actual,expected[identifier],public,docs)
        else:result=evaluators[group](actual,assertions[identifier])
        return result,unknown_positive(actual,expected[identifier])
    return questions,score


def actual_from_result(result,database,workspace):
    if 'source_investigation' not in result:return _empty('PLANNING_FAILED',result.get('reason',result['status']))
    source=result['source_investigation'];payload=source['graph_query']
    refs={e['evidence_ref']:e for e in result['evidence']};evidence=[]
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        for item in refs.values():
            row=db.execute('SELECT name FROM ll_source WHERE workspace_id=? AND source_id=?',(workspace,item['source_id'])).fetchone()
            if row:evidence.append(row[0])
    return dict(status=payload['status'],
        values=[_label(v['label']) if isinstance(v,dict) else _label(v) for o in payload['outputs'] for v in o['values'] if isinstance(v,str) or isinstance(v,dict) and 'label' in v],
        relations=[_relation(f['subject'],f['predicate'],f['value']) for f in source['facts']],
        evidence=sorted(set(evidence)),quote_hashes=sorted(e['quote_sha256'] for e in refs.values()),
        verified_by='graph-receipt-reviewer',structured_outputs=payload['outputs'],result_steps=payload['steps'],
        answer=source['answer'],facts=source['facts'])


def replay(archive,output):
    hashes=verify_scorers(archive);questions,score=scoring(archive);base=archive/'graph-contract-repair'
    dbs={'D':archive/'L6-eval-dev-v1.sqlite','D2':archive/'graph-holdout-D2/confirmed.sqlite','D3':base/'D3/confirmed.sqlite'}
    db_hashes={g:sha(p) for g,p in dbs.items()}
    rows=[];locators={}
    try:
        for stage in ['DEV','D3']:
            config=read(base/('config.json' if stage=='DEV' else 'D3/runtime-config.json'))
            models=config['local_models']
            if not locators:
                locator=LocalMentionLocator(LocatorConfig(**config['locator']),
                    embedder=SentenceTransformerEmbedder(models['embedding_model_dir'],local_files_only=True),
                    model_dir=models['rerank_model_dir'],onnx_path=models['rerank_onnx_path'])
                locators['shared']=locator
                locator.prepare()
                resource_config=(models,config['locator'])
            else:
                if resource_config!=(models,config['locator']):raise ValueError('LOCAL_RETRIEVAL_CONFIG_CHANGED')
                locator=locators['shared']
            plans=defaultdict(list)
            for path in sorted((base/stage/'provider-calls').glob('*.json')):
                saved=read(path);request=saved['request']
                if request.get('task_id')!='graph-investigation':continue
                payload=json.loads(request['user_input'].rsplit('\nINPUT_JSON\n',1)[1])
                calls=saved['response']['proposal']['tool_calls']
                if len(calls)!=1 or calls[0]['runtime_call']['tool_id']!='emit_graph_plan':raise ValueError('INVALID_SAVED_PLAN')
                plans[payload['question']].append((calls[0]['runtime_call']['arguments'],payload,str(path)))
            for row in read(base/stage/'report.json')['rows']:
                group=row['group']
                if row['route']!='graph_only' or group not in dbs:continue
                old_score,old_invented=score(row,row['actual'])
                if old_score!=row['score'] or old_invented!=row['unknown_positive_answer']:raise ValueError('BASELINE_SCORE_MISMATCH')
                question=questions[row['id']]['question'];saved,payload,path=plans[question][row['repeat']-1]
                workspace=payload['workspace_id'];database=dbs[group]
                store=SQLiteGraphStore(database,authorized_workspace=workspace,authorize_source=lambda ws,s,v:ws==workspace,
                    task_candidate_source=task_candidate_source(),mention_locator=locator)
                class SavedPlanner:
                    def plan(self,ctx):
                        try:return dict(status='PLANNED',plan=compile_plan(deepcopy(saved),ctx))
                        except ValueError as error:return dict(status='OUTPUT_INVALID',reason=str(error))
                worker=GraphInvestigationService(lambda ws:store,lambda ctx:SavedPlanner(),limits=GraphLimits(**config.get('graph_limits',{})),
                    query_clock=lambda:datetime.fromisoformat(payload['query_time']))
                result=worker.run(question,workspace,as_of=payload['as_of'])
                actual=actual_from_result(result,database,workspace)
                if any(s.get('reason_code')=='LOCAL_ENTITY_RETRIEVAL_UNAVAILABLE' for s in actual.get('result_steps',[])):
                    raise RuntimeError('LOCAL_RETRIEVAL_FAILED:'+row['id'])
                after,invented=score(row,actual)
                item=dict(id=row['id'],group=group,repeat=row['repeat'],provider_file=path,provider_sha256=sha(path),
                    before_actual=row['actual'],actual=actual,before_score=old_score,score=after,
                    unknown_positive_answer=invented,recovered=blank(row['actual']) and not blank(actual),
                    regression=old_score['correct'] and not after['correct'])
                rows.append(item)
                print(json.dumps({k:item[k] for k in ['id','group','repeat','recovered','regression']},ensure_ascii=False),flush=True)
        if len(rows)!=120:raise ValueError('INCOMPLETE_REPLAY')
        if verify_scorers(archive)!=hashes or {g:sha(p) for g,p in dbs.items()}!=db_hashes:raise ValueError('READ_ONLY_INPUT_CHANGED')
        result=dict(paid_provider_calls=0,paid_cost_usd=0,D3_independent_holdout=False,
            scorer_hashes=hashes,database_hashes=db_hashes,thresholds=read(base/'config.json')['locator'],
            rows=rows,summary={g:dict(total=len(rs),empty_before=sum(blank(r['before_actual']) for r in rs),
                empty_after=sum(blank(r['actual']) for r in rs),recovered=sum(r['recovered'] for r in rs),
                strict_before=sum(r['before_score']['correct'] for r in rs),strict_after=sum(r['score']['correct'] for r in rs),
                regressions=sum(r['regression'] for r in rs),error_relations=sum(r['score']['error_relations'] for r in rs),
                invented=sum(r['unknown_positive_answer'] for r in rs)) for g in dbs if (rs:=[r for r in rows if r['group']==g])})
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
        print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
    finally:
        for locator in locators.values():locator.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True,help='Directory containing graph-contract-repair')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();replay(args.archive.resolve(),args.output.resolve())
