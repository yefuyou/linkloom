"""Exercise acceptance wiring against the already exposed P4 Dev family only."""
from copy import deepcopy
import json
from pathlib import Path

from scripts.run_m2_final import build, run_case
from scripts.score_m2_final import proof_check, unique_predictions, grounded
import pytest


def dev_case():
    path = Path(__file__).parents[1]/'fixtures/task_relation_lifecycle/inputs/dev.json'
    case = next(c for c in json.loads(path.read_text(encoding='utf8'))['cases']
                if c['case_id']=='REDWOOD-version-retraction')
    case = deepcopy(case)
    # Select the lifecycle branch using a Dev-only source family. No holdout
    # input or annotation is loaded by this harness test.
    case['case_id']='H04'
    case['questions']=[dict(question_id='dev-s0',subject='B0',object='A0'),
                       dict(question_id='dev-s1',subject='B1',object='A0')]
    return case


def test_public_snapshot_has_complete_current_inventory_without_future():
    artifacts,observations,_,_,inventory=build(dev_case(),[0,1])
    assert len(artifacts)==2
    assert set(observations)=={'A0','B0'}
    assert len(inventory)==2


def test_lifecycle_harness_reopens_retracts_and_preserves_old_bytes(tmp_path):
    case=dev_case()
    result=run_case(case,tmp_path)
    life=result['lifecycle']
    assert life['s0']['old_revision']['current_supported']
    assert not life['s1']['old_revision']['current_supported']
    assert life['s2']['old_revision']['persistent_lifecycle']=='RETRACTED'
    assert not life['s2']['old_comparison']['current_valid']
    assert life['s2']['old_comparison']['historical_comparison']['delta_calendar_days']==2
    assert not life['s2']['duplicate_event_created']
    assessment=result['predictions'][0]['assessment']
    artifacts,*_=build(case,[0,1])
    assert proof_check(assessment,artifacts)
    forged=deepcopy(assessment)
    forged['supporting_evidence'][0]['quote']='forged proof'
    assert not proof_check(forged,artifacts)


@pytest.mark.parametrize('ids',[['one','one'],['extra'],[]])
def test_scoring_rejects_duplicates_extra_or_missing_rows(ids):
    with pytest.raises(ValueError,match='question IDs'):
        unique_predictions([dict(question_id=i) for i in ids],['one'])


def test_required_proof_is_bound_to_the_original_source():
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    a=RawArtifact('isolated','A.md','manual_text','shared exact words')
    b=RawArtifact('isolated','B.md','manual_text','shared exact words')
    quote=dict(workspace_id=b.workspace_id,artifact_id=b.artifact_id,artifact_version_id=b.artifact_version_id,
        content_hash=b.content_hash,quote=b.content)
    assert not grounded(dict(source_index=0,quote='shared exact words'),[quote],[a,b])
    assert grounded(dict(source_index=1,quote='shared exact words'),[quote],[a,b])
