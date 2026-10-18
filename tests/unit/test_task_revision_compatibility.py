"""P1 v1 and P2 v2 frozen outputs must retain their exact IDs and JSON."""
import json
from pathlib import Path

from linkloom.semantic_ingestion.task_relation_models import RelationAssessment
from tests.task_revision_support import invalid_premise_trials


def test_p1_and_p2_published_assessment_payloads_remain_unchanged():
    folder=Path(__file__).parents[2]/'docs/requirements/task-relations-p2/evidence'
    count=0
    for name in ('D03','D06','D07','D08','V06'):
        data=json.loads((folder/(name+'.json')).read_text(encoding='utf8'))
        for row in data['predictions']:
            original=row['assessment']
            assert RelationAssessment.from_dict(original).to_dict()==original
            count+=1
    assert count==12


def test_all_invalid_or_expired_identity_trials_rejected():
    trials=invalid_premise_trials()
    assert len(trials)==5
    for row in trials:
        assert not row['released'],row['variant']
        assert row['revision']['verdict']=='NOT_ASSESSABLE'
        assert row['comparison']['status']=='BLOCKED'
        assert row['comparison']['delta_calendar_days'] is None
