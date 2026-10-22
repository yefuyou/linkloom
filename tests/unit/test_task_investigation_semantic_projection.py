import pytest

from linkloom.semantic_ingestion.task_relation_question_models import InvestigationPlan, InvestigationStep
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.ui.task_investigation import _proposal_queries


def source_bound_relation():
    return InvestigationStep(
        id='step_relation', kind='relation', question='same task?',
        handles=('task_new', 'task_old'), endpoints=(),
        source_handles=('source_0:1', 'source_1:1'), depends_on=(),
        predicate='SAME_TASK', field='', target_kind='', missing_entities=(),
        workspace_id='ws_test', time_scope=('', ''), evidence_gap='evidence gap')


def test_source_bound_relation_is_available_to_semantic_proposal_but_not_p4_query():
    step = source_bound_relation()
    with pytest.raises(ValueError, match='SOURCE_SCOPED_OPERATION_REQUIRES_HOST_BINDING'):
        step.to_semantic_query()
    plan = InvestigationPlan('question', 'ws_test', 'a' * 64, (step,), (), ())
    queries = _proposal_queries(plan)
    assert len(queries) == 1
    assert queries[0].predicate == 'SAME_TASK'
    assert (queries[0].subject, queries[0].object) == ('task_new', 'task_old')


def test_task_relation_prompt_does_not_treat_empty_p4_as_reason_to_skip_semantics():
    prompt = RetrievalAgent._retrieval_instruction(
        'Did the task plan change?',
        {'workspace_id': 'ws_test', 'semantic_analysis_enabled': True},
        workflow='task_relations')
    assert 'empty P4 result is not a reason to skip semantic analysis' in prompt
