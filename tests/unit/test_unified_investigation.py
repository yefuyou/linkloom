import pytest

from linkloom.agents.claim_grounding import verified_subgoal_conflict_refs
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.runtime.task_plan import verified_plan_conflict_refs


def source(ref, text, *, verified=True):
    return {"evidence_id": ref, "relative_path": ref + ".md", "quote": text,
            "status": "verified" if verified else "unverified"}


@pytest.mark.parametrize("second,verified,conflict", [
    ("As of 2026-10-06 Lin Zhou reports directly to Chen Ning.", True, False),
    ("As of 2026-10-06 Lin Zhou reports directly to Zhou Yuan.", True, True),
    ("As of 2026-10-15 Lin Zhou reports directly to Zhou Yuan.", True, False),
    ("As of 2026-10-06 Other Person reports directly to Zhou Yuan.", True, False),
    ("As of 2026-10-06 Lin Zhou reports directly to Zhou Yuan.", False, False),
])
def test_reporting_conflicts_require_same_verified_subject_and_time(second, verified, conflict):
    facts = [source("a", "As of 2026-10-06 Lin Zhou reports directly to Chen Ning."),
             source("b", second, verified=verified)]
    assert bool(verified_subgoal_conflict_refs("As of 2026-10-06 Who is the direct leader of Lin Zhou?", facts)) == conflict


def test_unrelated_date_conflict_does_not_pollute_requested_snapshot():
    facts = [source("a", "As of 2026-10-15 Lin Zhou reports directly to Chen Ning."),
             source("b", "As of 2026-10-15 Lin Zhou reports directly to Zhou Yuan.")]
    assert not verified_subgoal_conflict_refs("As of 2026-10-06 Who is the direct leader of Lin Zhou?", facts)


def test_product_chinese_owner_chain_keeps_the_root_entity_only():
    plan = plan_relation_investigation("Orion 项目负责人的直属领导是谁？")
    assert plan.subgoals[0].question == "Who owns Orion?"
    assert plan.subgoals[1].question == "Who is the direct leader of {verified_entity}?"


def test_reporting_plan_conflict_inherits_the_verified_project_scope():
    plan = plan_relation_investigation("As of 2026-10-06, Who is the direct leader of the owner of Orion?")
    facts = {"owner": (source("o", "As of 2026-10-06 Lin Zhou is the accountable owner of Orion."),),
        "leader": (source("a", "As of 2026-10-06 Lin Zhou reports directly to Chen Ning in the Orion project."),
                   source("b", "As of 2026-10-06 Lin Zhou reports directly to Zhou Yuan in the Other project."))}
    assert not verified_plan_conflict_refs(plan, "leader", facts)
    facts["leader"][1]["quote"] = "As of 2026-10-06 Lin Zhou reports directly to Zhou Yuan in the Orion project."
    assert verified_plan_conflict_refs(plan, "leader", facts) == ("a", "b")
