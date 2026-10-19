"""Unchanged M1/P1-P3 on the same public Dev queries; never used by proposer."""
from argparse import ArgumentParser
from datetime import UTC,datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from tests.task_semantic_support import public_cases,context_for
from linkloom.semantic_ingestion.task_relation_models import ObservationRef,EvidenceQuote
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_relation_context import assess_relation
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision


def diagnose(case):
    c=context_for(case)
    refs=dict(c.endpoints)
    observations={ObservationRef.from_observation(o):o for o in c.observations}
    proofs=[EvidenceQuote(s,s.char_start,s.char_end,s.quote,'RELATION_PROPOSAL') for _,s in c.allowed_spans]
    kwargs=dict(artifacts=c.artifacts,source_registry=c.source_registry,context_observations=c.observations,
        assessed_at=datetime(2026,10,18,tzinfo=UTC))
    rows=[]
    for query in c.queries:
        a,b=refs[query.subject],refs[query.object]
        subject,object=observations.get(a,a),observations.get(b,b)
        comparison=None
        if query.predicate=='SAME_TASK':
            result=assess_same_task(subject,object,**kwargs)
        elif query.predicate=='REVISES_PLAN':
            identity=assess_same_task(subject,object,**kwargs)
            result=assess_plan_revision(subject,object,same_task=identity,evidence=proofs,**kwargs)
            comparison=compare_revision(subject,object,same_task=identity,revision=result,evidence=proofs,
                artifacts=c.artifacts,source_registry=c.source_registry,context_observations=c.observations).to_dict()
        else:
            result=assess_relation(query.predicate,subject,object,evidence=proofs,**kwargs)
        rows.append(dict(predicate=query.predicate,subject=query.subject,object=query.object,
            verdict=result.verdict,reason_codes=list(result.reason_codes),assessment=result.to_dict(),comparison=comparison))
    return dict(case_id=case['case_id'],scope='SAME_PUBLIC_FIXED_ENDPOINTS_FULL_BOUNDED_CONTEXT',relations=rows)


def main():
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=dict(schema='task-semantic-regex-diagnostic/v1',implementation='UNCHANGED_BASELINE_P1_P2_P3',
        gold_access=False,provider_calls=0,cases=[diagnose(c) for c in public_cases()])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps([{ 'case_id':c['case_id'],'relations':[(r['predicate'],r['verdict']) for r in c['relations']]}
        for c in result['cases']],ensure_ascii=False))


if __name__=='__main__':
    main()
