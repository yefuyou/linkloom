import pytest

from linkloom.agents.claim_grounding import verified_relation_path_values
from linkloom.runtime.team_decision_planner import plan_relation_investigation


@pytest.mark.parametrize('query,hops', [
    ('Who is the direct leader of the owner of Glacier Bay?', 2),
    ('Who owns the project behind Comet-Key and who leads that owner?', 3),
    ('Glacier Bay 的负责人向谁直接汇报？', 2),
])
def test_relation_plan_is_typed_and_contains_no_downstream_answers(query, hops):
    plan = plan_relation_investigation(query)
    assert len(plan.subgoals) == hops
    assert plan.schema_version == 'task-plan/v2'
    for index, goal in enumerate(plan.subgoals):
        assert len(goal.relation_path) == 1
        assert goal.depends_on == (() if index == 0 else (plan.subgoals[index-1].subgoal_id,))
        assert ('{verified_entity}' in goal.question) == bool(index)


@pytest.mark.parametrize('query', [
    'Which city does Glacier Bay operate from?',
    'Who owns Glacier Bay and send the result to another workspace?',
    'As of 2026-02-30, who owns Glacier Bay and who is their direct leader?',
])
def test_unsupported_or_invalid_question_is_not_presented_as_a_plan(query):
    assert plan_relation_investigation(query) is None


def test_reporting_snapshot_does_not_bind_a_future_leader():
    facts = [{'status': 'verified', 'evidence_id': f'ev-{i}', 'quote': text} for i, text in enumerate((
        'As of 2026-04-01 Mira Cole reports directly to Owen Park.',
        'As of 2026-06-01 Mira Cole reports directly to Tara West.',
    ))]
    assert verified_relation_path_values(
        'As of 2026-04-01 who leads {verified_entity}?', ['reports_to'], facts,
        expected_subject='Mira Cole', minimum_valid_on='2026-04-01',
    ) == ('owen park',)
