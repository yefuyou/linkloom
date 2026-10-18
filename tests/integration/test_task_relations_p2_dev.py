"""Predict public Dev first; Gold is used only by the separate evaluator."""
import json
from pathlib import Path
import pytest

from linkloom.semantic_ingestion.task_relation_models import RelationAssessment
from tests.task_relations_support import public_cases, run_case


@pytest.mark.parametrize('case', public_cases(), ids=lambda c:c['case_id'])
def test_p2_dev(case, tmp_path):
    result = run_case(case, tmp_path)
    # No reference answers reach run_case or any production validator.
    gold = json.loads((Path(__file__).parents[1]/'fixtures/task_relations_p2/gold/dev.json').read_text(encoding='utf8'))
    expected = next(c for c in gold['cases'] if c['case_id'] == case['case_id'])
    answers = {q['question_id']:q for q in expected['questions']}
    for row in result['predictions']:
        assessment = row['assessment']
        assert assessment['verdict'] == answers[row['question_id']]['verdict'], row['question_id']
        assert RelationAssessment.from_dict(assessment).to_dict() == assessment
        if assessment['verdict'] != 'NOT_ASSESSABLE':
            assert assessment['evidence_validity']
        for dependency in assessment['dependencies']:
            if dependency['readback_ok']:
                span = dependency['span']
                source = next(s for s in result['sources'] if s['artifact_version_id'] == span['artifact_version_id'])
                assert source['content'][span['char_start']:span['char_end']] == span['quote']
