"""Adversarial correctness tests, not an M3 capability evaluation."""
from copy import deepcopy

import pytest

from evals.m3_task_impact.fixtures import proof
from evals.m3_task_impact.scorer import load_dev, score_case


def sample(case_id):
    cases, annotations = load_dev()
    case = next(c for c in cases if c['case_id'] == case_id)
    gold = next(c for c in annotations if c['case_id'] == case_id)
    return case, gold


def evaluator_answer(case, gold):
    """Gold-shaped test input; never used by runner or labelled an Agent output."""
    answer = deepcopy(gold)
    for section in ('tasks', 'relations', 'revisions'):
        for row in answer[section]:
            row['evidence'] = [proof(case, ref) for ref in row['evidence']]
    refs = sorted({ref for section in ('tasks', 'relations', 'revisions')
                   for row in gold[section] for ref in row['evidence']})
    answer['answer_evidence'] = [proof(case, ref) for ref in refs]
    return answer


def test_missing_agent_is_not_task_failure_or_success():
    case, gold = sample('D01')
    result = score_case(case, gold, None)
    assert result['status'] == 'NOT_EVALUATED'
    assert result['metrics'] is None
    assert result['failure_stage'] == 'capability_gap'


def test_exact_annotated_answer_scores_all_dimensions():
    case, gold = sample('D05')
    result = score_case(case, gold, evaluator_answer(case, gold))
    assert result['metrics']['task_discovery_recall']['value'] == 1
    assert result['metrics']['relation_precision']['value'] == 1
    assert result['metrics']['relation_recall']['value'] == 1
    assert result['metrics']['deadline_accuracy']['value'] == 1
    assert result['metrics']['revision_accuracy']['value'] == 1
    assert result['metrics']['evidence_groundedness']['value'] == 1


def test_missing_discovered_task_remains_in_recall_denominator():
    case, gold = sample('D02')
    answer = evaluator_answer(case, gold)
    answer['tasks'].pop(0)
    answer['relations'].pop(0)
    result = score_case(case, gold, answer)
    assert result['metrics']['task_discovery_recall']['numerator'] == 2
    assert result['metrics']['task_discovery_recall']['denominator'] == 3
    assert result['metrics']['relation_recall']['value'] == .5
    assert result['failure_stage'] == 'discovery'


def test_wrong_positive_proof_costs_fp_and_fn():
    case, gold = sample('D05')
    answer = evaluator_answer(case, gold)
    answer['relations'][0]['evidence'] = [proof(case, 'b:1')]
    result = score_case(case, gold, answer)
    assert result['counts']['relation_fp'] == 1
    assert result['counts']['relation_fn'] == 1
    assert result['counts']['unsupported_merge'] == 1


def test_false_merge_and_revision_on_different_tasks():
    case, gold = sample('D04')
    answer = evaluator_answer(case, gold)
    answer['relations'][0]['verdict'] = 'SUPPORTED'
    answer['revisions'] = [dict(subject='b:1', object='a:1', status='COMPUTABLE',
        old={'date':'2026-11-11','bound':'BEFORE'}, new={'date':'2026-11-13','bound':'BEFORE'},
        delta=2, reason=None, evidence=[proof(case,'a:1'),proof(case,'b:1')])]
    result = score_case(case, gold, answer)
    assert result['counts']['hard_false_merge'] == 1
    assert result['counts']['false_revision'] == 1
    assert result['metrics']['relation_precision']['value'] == 0
    assert result['metrics']['relation_recall']['value'] is None


@pytest.mark.parametrize('field,value', [('workspace_id','other'), ('content_hash','0'*64),
    ('artifact_version_id','wrong'), ('char_start',0), ('quote','fake')])
def test_tampered_evidence_never_earns_grounded_credit(field, value):
    case, gold = sample('D01')
    answer = evaluator_answer(case, gold)
    answer['tasks'][0]['evidence'][0]['span'][field] = value
    result = score_case(case, gold, answer)
    assert result['metrics']['task_accuracy']['value'] == 0
    assert result['metrics']['evidence_groundedness']['value'] < 1


def test_stale_source_does_not_publish_current_revision():
    case, gold = sample('D09')
    answer = evaluator_answer(case, gold)
    stale_revision = next(r for r in answer['relations'] if r['predicate']=='REVISES_PLAN' and r['subject']=='b1:1')
    stale_revision['verdict'] = 'SUPPORTED'
    stale_revision['reason'] = None
    result = score_case(case, gold, answer)
    assert result['counts']['false_revision'] == 1
    assert result['counts']['relation_fp'] == 1


def test_conflict_requires_counterevidence_and_no_delta():
    case, gold = sample('D08')
    answer = evaluator_answer(case, gold)
    answer['relations'][1]['evidence'] = [proof(case,'a:1'),proof(case,'b:1')]
    answer['revisions'][0]['delta'] = 4
    result = score_case(case, gold, answer)
    assert result['metrics']['relation_state_accuracy']['value'] == 0
    assert result['metrics']['revision_accuracy']['value'] == 0
    assert result['counts']['false_revision'] == 1


def test_unsupported_negative_and_authority_abstention():
    case, gold = sample('D11')
    answer = evaluator_answer(case, gold)
    answer['relations'][2]['verdict'] = 'REFUTED'
    answer['answer_status'] = 'COMPLETE'
    answer['abstentions'] = []
    result = score_case(case, gold, answer)
    assert result['counts']['unsupported_negative'] == 1
    assert result['metrics']['abstention_accuracy']['value'] == 0
    case, gold = sample('D10')
    answer = evaluator_answer(case, gold)
    answer['abstentions'] = []
    assert score_case(case,gold,answer)['metrics']['abstention_accuracy']['value'] == 0


def test_zero_delta_is_not_missing_revision_and_extra_claims_are_scored():
    case, gold = sample('D12')
    answer = evaluator_answer(case, gold)
    assert score_case(case,gold,answer)['metrics']['revision_accuracy']['value'] == 1
    answer['revisions'][0]['delta'] = None
    answer['relations'].append(dict(predicate='AFFECTED_BY',subject='b:1',object='invented:1',
        verdict='SUPPORTED',reason=None,evidence=[]))
    result = score_case(case,gold,answer)
    assert result['counts']['relation_fp'] == 1
    assert result['metrics']['revision_accuracy']['value'] == 0


def test_duplicate_keys_are_contract_errors_instead_of_overwriting():
    case, gold = sample('D01')
    answer = evaluator_answer(case,gold)
    answer['tasks'].append(deepcopy(answer['tasks'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        score_case(case,gold,answer)


def test_gold_binding_does_not_follow_edited_source_with_same_locator():
    case,gold = sample('D01')
    changed = deepcopy(case)
    changed['sources'][0]['content'] = changed['sources'][0]['content'].replace('我负责','我不负责')
    answer = evaluator_answer(changed,gold)
    result = score_case(changed,gold,answer)
    assert result['metrics']['evidence_readback']['value']==1
    assert result['metrics']['evidence_groundedness']['value']==0
    assert result['metrics']['task_discovery_recall']['value']==0


def test_semantically_wrong_field_is_not_a_grounded_claim():
    case,gold = sample('D01')
    answer = evaluator_answer(case,gold)
    answer['tasks'][0]['owner']='虚构负责人'
    answer['answer_status']='ABSTAIN'
    result = score_case(case,gold,answer)
    assert result['metrics']['evidence_readback']['value']==1
    assert result['metrics']['evidence_groundedness']['value']==0
    assert result['metrics']['owner_accuracy']['value']==0


def test_boolean_is_not_a_valid_calendar_delta():
    case,gold = sample('D12')
    answer = evaluator_answer(case,gold)
    answer['revisions'][0]['delta']=False
    with pytest.raises(ValueError,match='delta'):
        score_case(case,gold,answer)


def test_symmetric_identity_is_one_merge_but_direction_is_scored():
    case,gold = sample('D03')
    answer = evaluator_answer(case,gold)
    extra = deepcopy(answer['relations'][0])
    extra['subject'],extra['object']=extra['object'],extra['subject']
    answer['relations'].append(extra)
    result = score_case(case,gold,answer)
    assert result['counts']['published_merges']==1
    assert result['counts']['unsupported_merge']==0
    assert result['counts']['relation_fp']==1


def test_blocked_revision_also_requires_relation_assessments():
    case,gold = sample('D03')
    answer = evaluator_answer(case,gold)
    answer['relations']=[]
    assert score_case(case,gold,answer)['metrics']['revision_accuracy']['value']==0


def test_current_discovery_excludes_readable_history_but_preserves_required_assessments():
    case,gold = sample('D09')
    assert gold['discovery_scope']=='CURRENT_OBSERVATIONS'
    assert gold['discovery_exclusions']==['b1:1']
    answer = evaluator_answer(case,gold)
    result = score_case(case,gold,answer)
    assert result['metrics']['task_discovery_recall']['denominator']==2
    assert result['metrics']['revision_accuracy']['value']==1
    assert any(r['subject']=='b1:1' for r in answer['relations'])


def test_change_and_task_endpoints_remain_distinct_within_one_message():
    case,gold = sample('D05')
    impact = next(r for r in gold['relations'] if r['predicate']=='AFFECTED_BY')
    assert impact['subject']=='b:1' and impact['object']=='change:b:1'
