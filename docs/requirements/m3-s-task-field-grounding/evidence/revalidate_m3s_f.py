import json,sys
from pathlib import Path
from datetime import datetime
from collections import Counter
sys.path[:0]=['src','.']
sys.stdout.reconfigure(encoding='utf-8')
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.task_observation_discovery import _project
from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
base=Path('work/m3s-live-20261022')
rows=[];new=Counter();old=Counter();model_errors=[]
for p in sorted(base.glob('f-batch-*.json')):
    row=json.loads(p.read_text(encoding='utf8'))
    artifact=RawArtifact('m3s-public-f','f-'+str(row['batch'])+'.txt','timestamped_text',row['source'])
    current=[]
    for extraction in row['discovery']['extractions']:
        result=SemanticExtractionResult.from_dict(extraction)
        found,_,failures=_project(result,artifact,parse_source_text(artifact).segments,task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2)
        current.extend(o.to_dict() for o in found)
        assert all(o.verify(artifact) for o in found)
    for i,case in enumerate(row['new_evaluation']):
        for expected in case['results']:
            preds=[o for o in current if o['provenance']['ordinal']==i and expected['anchor'] in o['raw_text']]
            pred=preds[0] if len(preds)==1 else None
            for name,field in expected['fields'].items():
                wanted=field['expected']
                actual=[pred['deadline_text'],pred['deadline'],pred['deadline_precision'],pred['deadline_role']] if name=='deadline' and pred else pred.get(name) if pred else None
                unknown=(wanted[0] is None) if name=='deadline' else wanted is None
                absent=pred is not None and (pred['deadline_text'] is None if name=='deadline' else actual is None)
                outcome='MISSING_OBSERVATION' if pred is None else 'CORRECT_UNKNOWN' if unknown and absent else 'CORRECT_ACCEPT' if not unknown and actual==wanted else 'FALSE_ACCEPT' if not absent else 'REJECTED'
                new[outcome]+=1
                rows.append(dict(case_id=case['case_id'],field=name,expected=wanted,actual=actual,outcome=outcome))
    for case in row['legacy_evaluation']:
        for result in case['results']:
            old.update(f['outcome'] for f in result['fields'].values())
summary=dict(evaluation='OFFLINE_REVALIDATION_OF_UNCHANGED_REAL_F_RESPONSES; NOT_NEW_MODEL_CALLS',field_slots=sum(new.values()),
    final_binding_outcomes=dict(new),legacy_outcomes=dict(old),fields=rows,
    notes=['Missing F04 observation is a model discovery omission, not a correct UNKNOWN.',
           'H02 deadline_text included a boundary word; format rejection is retained, not scored as a false accept.'])
(base/'f-revalidated.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in summary.items() if k!='fields'},ensure_ascii=False,indent=2))
