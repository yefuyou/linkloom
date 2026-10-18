"""P3 fixed-candidate acceptance: predict before evaluator opens Gold."""
import json
from pathlib import Path
import pytest

from tests.task_revision_support import public_cases, run_case
from linkloom.semantic_ingestion.task_relation_models import RelationAssessment
from linkloom.semantic_ingestion.task_revision_comparison import RevisionComparison


@pytest.mark.parametrize('case',public_cases(),ids=lambda c:c['case_id'])
def test_revision_dev(case):
    prediction=run_case(case)
    gold=json.loads((Path(__file__).parents[1]/'fixtures/task_revisions/gold/dev.json').read_text(encoding='utf8'))
    expected={q['question_id']:q for c in gold['cases'] for q in c['questions']}
    for row in prediction['predictions']:
        target=expected[row['question_id']]
        rev,comp=row['revision'],row['comparison']
        assert rev['verdict']==target['verdict'],row['question_id']
        assert rev['changed_fields']==target['changed_fields'],row['question_id']
        assert comp['status']==target['comparison_status'],row['question_id']
        assert comp['delta_calendar_days']==target['delta_calendar_days'],row['question_id']
        for side in ('old','new'):
            if target.get(side+'_bound') is not None:
                assert comp[side+'_deadline']['bound']==target[side+'_bound']
        if target.get('asserted_effective_at'):
            assert rev['asserted_effective_at']==target['asserted_effective_at']
            assert comp['plan_currentness']==target['plan_currentness']
        assert RelationAssessment.from_dict(rev).to_dict()==rev
        assert RevisionComparison.from_dict(comp).to_dict()==comp
