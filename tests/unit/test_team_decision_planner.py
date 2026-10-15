from __future__ import annotations

from linkloom.runtime.team_decision_planner import plan_team_decision_query


def test_two_hop_owner_then_direct_leader_plan_uses_query_entity_only() -> None:
    query = "Who owns Cobalt B and who is their direct leader?"

    plan = plan_team_decision_query(query)

    assert plan is not None
    assert plan.goal == query
    assert [item.subgoal_id for item in plan.subgoals] == ["owner", "leader"]
    assert [item.depends_on for item in plan.subgoals] == [(), ("owner",)]
    assert "Cobalt B" in plan.subgoals[0].question
    assert "Mira Chen" not in str(plan.to_dict())


def test_two_hop_replacement_then_owner_plan_is_bounded_and_answer_free() -> None:
    query = "Which plan replaced Meridian A and who owns that new plan?"

    plan = plan_team_decision_query(query)

    assert plan is not None
    assert [item.subgoal_id for item in plan.subgoals] == ["successor", "owner"]
    assert [item.depends_on for item in plan.subgoals] == [(), ("successor",)]
    assert "Meridian A" in plan.subgoals[0].question
    assert "Cobalt B" not in str(plan.to_dict())


def test_three_hop_replacement_owner_and_leader_plan_is_generated_from_natural_language() -> None:
    query = "Which plan replaced Meridian A, who owns it, and who is that owner's direct leader?"

    plan = plan_team_decision_query(query)

    assert plan is not None
    assert [item.subgoal_id for item in plan.subgoals] == [
        "successor", "owner", "leader"
    ]
    assert [item.depends_on for item in plan.subgoals] == [
        (), ("successor",), ("owner",)
    ]
    assert "Mira Chen" not in str(plan.to_dict())
    assert "Kai Wu" not in str(plan.to_dict())


def test_four_subgoal_replacement_owner_leader_and_effective_date_plan_is_bounded() -> None:
    query = (
        "Which plan replaced Meridian A, who owns it, who is that owner's direct leader, "
        "and when did the replacement take effect?"
    )

    plan = plan_team_decision_query(query)

    assert plan is not None
    assert [item.subgoal_id for item in plan.subgoals] == [
        "successor", "owner", "leader", "effective_date",
    ]
    assert [item.depends_on for item in plan.subgoals] == [
        (), ("successor",), ("owner",), (),
    ]
    assert "Meridian A" in plan.subgoals[-1].question
    assert len(plan.subgoals) == 4
    assert "2025-02-14" not in str(plan.to_dict())


def test_planner_handles_a_different_entity_without_fixture_specific_rules() -> None:
    plan = plan_team_decision_query(
        "Which plan replaced Delta Q and who owns the replacement?"
    )

    assert plan is not None
    assert "Delta Q" in plan.subgoals[0].question


def test_single_hop_and_unrecognized_multi_hop_queries_do_not_get_fake_plans() -> None:
    assert plan_team_decision_query("Who owns Cobalt B?") is None
    assert plan_team_decision_query("Compare every project and list all risks and owners") is None
