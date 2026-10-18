"""python -m evals.m3_task_impact {run,score,validate}. Dev only."""
from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path


def write_new(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    run = commands.add_parser('run')
    run.add_argument('--mode',choices=['UNAVAILABLE','SCRIPTED'],default='UNAVAILABLE')
    run.add_argument('--case',action='append',dest='case_ids')
    run.add_argument('--output',type=Path,required=True)
    score = commands.add_parser('score')
    score.add_argument('--predictions',type=Path,required=True)
    score.add_argument('--output',type=Path,required=True)
    commands.add_parser('validate')
    args = parser.parse_args()
    if args.command=='run':
        # Do not even import the offline evaluator in this process.
        from .runner import run_dev
        records = run_dev(args.case_ids,args.mode)
        write_new(args.output,dict(schema='m3-predictions/v1',split='dev',records=records))
        print(json.dumps(dict(records=len(records),statuses=dict(Counter(r['status'] for r in records)))))
    elif args.command=='score':
        from .fixtures import digest
        from .scorer import load_dev,score_run
        from .reporting import summarize
        cases,gold = load_dev()
        payload = json.loads(args.predictions.read_text(encoding='utf8'))
        if payload.get('schema')!='m3-predictions/v1' or payload.get('split')!='dev':
            raise ValueError('only Dev prediction files can be scored this round')
        records = payload['records']
        ids = [r['case_id'] for r in records]
        if len(ids)!=len(set(ids)) or set(ids)-{c['case_id'] for c in cases}:
            raise ValueError('duplicate or unknown case IDs')
        by_id = {r['case_id']:r for r in records}
        annotations = {g['case_id']:g for g in gold}
        results = [score_run(c,annotations[c['case_id']],by_id.get(c['case_id'])) for c in cases]
        report = dict(schema='m3-score/v1',split='dev',results=results,
            statuses=dict(Counter(r['status'] for r in results)),
            summary=summarize(results),
            input_sha256=digest(cases),gold_sha256=digest(gold),predictions_sha256=digest(payload),
            model_evaluation=('NOT_RUN' if all(r.get('adapter_kind') in {'UNAVAILABLE','SCRIPTED','TEST'}
                              for r in records) else 'IMPORTED_UNVERIFIED'),holdout_evaluation='NOT_RUN',
            qualification='Preparation only; scripted scores do not establish M3 Agent capability.')
        write_new(args.output,report)
        print(json.dumps(report['statuses']))
    else:
        from .validation import validate_dataset
        print(json.dumps(validate_dataset(),ensure_ascii=False))


if __name__=='__main__':
    main()
