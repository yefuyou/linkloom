"""Plan state is reconstructed from verified, task-scoped Tool Ledger facts."""

import hashlib
from dataclasses import replace

import pytest

from linkloom.evidence_identity import passage_evidence_id
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.runtime.task_plan import (
    TaskPlan, assess_task_plan,
    validate_model_proposed_plan,
    verified_dependency_entities,
)
from linkloom.tools.contracts import ToolResult


def _plan():
    return TaskPlan.from_dict({
        "goal": "Who owns the successor to Meridian A?",
        "subgoals": [
            {"subgoal_id": "successor", "question": "Meridian A replacement",
             "depends_on": []},
            {"subgoal_id": "owner", "question": "accountable owner",
             "depends_on": ["successor"], "alternate_question": "plan owner"},
        ],
    })


WORKSPACE = "workspace-one"


def _evidence(ref, quote, *, workspace=WORKSPACE, path="decision.md"):
    quote_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    evidence_id = passage_evidence_id(
        workspace_id=workspace, resource_id=path, document_id=path,
        logical_path=f"/{path}", source_ref=path, content_hash="a" * 64,
        line_start=3, line_end=3, quote_hash=quote_hash,
    )
    return {"evidence_id": evidence_id, "relative_path": path,
            "content_sha256": "a" * 64, "line_start": 3, "line_end": 3,
            "quote": quote, "quote_sha256": quote_hash, "status": "verified"}


def _record(seq, tool, value, *, run="run_1", task="task_1", subgoal=None):
    call = f"call_{seq}_{run}"
    arguments = ({"query": "q", "source_context": {"subgoal_id": subgoal}, "limit": 3}
                 if tool == "search_notes" else {"note_ref": value["evidence_id"]})
    return ToolExecutionRecord(
        call_id=call, run_id=run, task_id=task, agent_id="retrieval_agent",
        tool_id=tool, sequence=seq, status="completed", arguments=arguments,
        result=ToolResult(call_id=call, tool_id=tool, status="ok", value=value).to_dict(),
    )


def test_plan_requires_a_verified_read_before_dependent_entity_can_advance():
    plan = _plan()
    hit = _evidence("ev_one", "Cobalt B replaced Meridian A on 2025-02-14.")
    search = _record(1, "search_notes", [hit], subgoal="successor")
    before = assess_task_plan(plan, [search], run_id="run_1", task_id="task_1",
                              agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert before.missing_subgoals == ("successor", "owner")
    assert before.status_by_subgoal == {"successor": "ready", "owner": "pending"}
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, before, "owner", {"successor": "Cobalt B"})

    read = _record(2, "read_verified_note", hit)
    after = assess_task_plan(plan, [search, read], run_id="run_1", task_id="task_1",
                             agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert after.missing_subgoals == ("owner",)
    assert after.status_by_subgoal == {"successor": "completed", "owner": "ready"}
    assert after.evidence_refs() == {hit["evidence_id"]}
    assert verified_dependency_entities(plan, after, "owner", {"successor": "Cobalt B"}) == ("Cobalt B",)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, after, "owner", {"successor": "invented owner"})


def test_nicknamed_alias_question_binds_project_then_manager_from_verified_sources():
    plan = TaskPlan.from_dict({"goal": "Trace the nickname to the accountable owner's direct manager.", "subgoals": [
        {"subgoal_id": "alias", "question": "What is the formal project name for the initiative nicknamed WP-Arc?",
         "depends_on": []},
        {"subgoal_id": "owner", "question": "Who is the current accountable owner of {verified_entity}?",
         "depends_on": ["alias"]},
        {"subgoal_id": "manager", "question": "Who does {verified_entity} report to as their direct manager?",
         "depends_on": ["owner"]},
    ]})
    alias = _evidence("alias", "WP-Arc is an internal alias for the Glacier Arcade Hub project.", path="alias.md")
    owner = _evidence("owner", "As of 2026-10-13 Ilan Reese is the accountable owner of Glacier Arcade Hub.", path="owner.md")
    records = [
        _record(1, "search_notes", [alias], subgoal="alias"),
        _record(2, "read_verified_note", alias),
        _record(3, "search_notes", [owner], subgoal="owner"),
        _record(4, "read_verified_note", owner),
    ]
    assessment = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                  agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert verified_dependency_entities(plan, assessment, "owner", {"alias": "Glacier Arcade Hub"}) == ("Glacier Arcade Hub",)
    assert verified_dependency_entities(plan, assessment, "manager", {"owner": "Ilan Reese"}) == ("Ilan Reese",)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessment, "manager", {"owner": "Wrong Reese"})


def test_structured_relation_path_binds_unfamiliar_alias_plan_without_question_phrases():
    plan = TaskPlan.from_dict({"schema_version": "task-plan/v2", "goal": "Find the owner and leader behind Silver Finch.", "subgoals": [
        {"subgoal_id": "alias", "question": "Resolve the initiative referred to as Silver Finch.",
         "depends_on": [], "relation_path": ["alias_of_project"]},
        {"subgoal_id": "owner", "question": "Find the accountable person for {verified_entity}.",
         "depends_on": ["alias"], "relation_path": ["owner_of_project"]},
        {"subgoal_id": "leader", "question": "Find the manager to whom {verified_entity} reports.",
         "depends_on": ["owner"], "relation_path": ["reports_to"]},
    ]})
    alias = _evidence("alias", "Silver Finch is an internal alias for the Kestrel Junction project.", path="alias.md")
    owner = _evidence("owner", "As of 2026-10-13 Priya Han is the accountable owner of Kestrel Junction.", path="owner.md")
    records = [_record(1, "search_notes", [alias], subgoal="alias"), _record(2, "read_verified_note", alias),
               _record(3, "search_notes", [owner], subgoal="owner"), _record(4, "read_verified_note", owner)]
    first = assess_task_plan(plan, records[:2], run_id="run_1", task_id="task_1",
                             agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert verified_dependency_entities(plan, first, "owner", {"alias": "Kestrel Junction"}) == ("Kestrel Junction",)
    second = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                              agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert verified_dependency_entities(plan, second, "leader", {"owner": "Priya Han"}) == ("Priya Han",)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, second, "leader", {"owner": "Other Han"})


def test_v2_relation_path_rejects_ambiguous_two_parent_binding():
    with pytest.raises(ValidationError, match="one verified parent"):
        TaskPlan.from_dict({"schema_version": "task-plan/v2", "goal": "Find the owner.", "subgoals": [
            {"subgoal_id": "a", "question": "Find project A.", "depends_on": [],
             "relation_path": []},
            {"subgoal_id": "b", "question": "Find project B.", "depends_on": [],
             "relation_path": []},
            {"subgoal_id": "owner", "question": "Who owns {verified_entity}?",
             "depends_on": ["a", "b"], "relation_path": ["owner_of_project"]},
        ]})


def test_one_verified_read_satisfies_independent_subgoals_that_found_same_passage():
    plan = TaskPlan.from_dict({
        "goal": "Who replaced Meridian A and when did it take effect?",
        "subgoals": [
            {"subgoal_id": "successor", "question": "Which plan replaced Meridian A?",
             "depends_on": []},
            {"subgoal_id": "effective_date", "question": "When was Meridian A replaced?",
             "depends_on": []},
        ],
    })
    hit = _evidence("ev_shared", "On 2025-02-14 Cobalt B replaced Meridian A.")
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [hit], subgoal="successor"),
         _record(2, "search_notes", [hit], subgoal="effective_date"),
         _record(3, "read_verified_note", hit)],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    assert assessed.complete
    assert assessed.evidence_by_subgoal["successor"] == (hit,)
    assert assessed.evidence_by_subgoal["effective_date"] == (hit,)
    assert assessed.evidence_refs() == {hit["evidence_id"]}


def test_plan_ignores_cross_run_and_mismatched_read_passages():
    plan = _plan()
    hit = _evidence("ev_one", "Cobalt B replaced Meridian A.")
    wrong_read = {**hit, "quote_sha256": "c" * 64}
    records = [
        _record(1, "search_notes", [hit], subgoal="successor"),
        _record(2, "read_verified_note", wrong_read),
        _record(3, "read_verified_note", hit, run="run_other"),
    ]
    assessed = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert assessed.evidence_refs() == set()
    assert assessed.missing_subgoals == ("successor", "owner")


def test_plan_rejects_forward_dependency_and_answer_fields():
    with pytest.raises(ValidationError):
        TaskPlan.from_dict({"goal": "g", "subgoals": [
            {"subgoal_id": "a", "question": "q", "depends_on": ["b"]},
            {"subgoal_id": "b", "question": "q", "depends_on": []},
        ]})
    with pytest.raises(ValidationError):
        TaskPlan.from_dict({"goal": "g", "subgoals": [
            {"subgoal_id": "a", "question": "q", "depends_on": [], "answer": "Gold"},
            {"subgoal_id": "b", "question": "q", "depends_on": ["a"]},
        ]})


@pytest.mark.parametrize("goal, root_question", [
    ("Find Cobalt B's owner and direct leader.", "Who owns Cobalt B?"),
    ("Find Cobalt B's owner and direct leader.", "Who is responsible for Cobalt B these days?"),
    ("谁负责青岚计划？他的直属领导是谁？", "谁负责青岚计划？"),
    ("谁负责青岚计划？他的直属领导是谁？", "青岚计划现在归谁管？"),
])
def test_model_plan_is_validated_by_structure_not_wording(goal, root_question):
    # The Host never re-reads plan wording; claims are bound to the question at Final.
    proposal = {"goal": goal, "subgoals": [
        {"subgoal_id": "owner", "question": root_question, "depends_on": []},
        {"subgoal_id": "leader", "question": "Who leads {verified_entity}?",
         "depends_on": ["owner"]},
    ]}
    assert validate_model_proposed_plan(proposal, user_query=goal, max_steps=5).goal == goal
    proposal["subgoals"][1]["question"] = "Who leads {person}?"
    with pytest.raises(ValidationError, match="placeholder"):
        validate_model_proposed_plan(proposal, user_query=goal, max_steps=5)


def test_model_plan_accepts_current_workspace_scope():
    goal = (
        "In this Finance Operations workspace, identify Cedar Grid's owner "
        "and that person's direct leader. Use this workspace's own records."
    )
    proposal = {"goal": goal, "subgoals": [
        {"subgoal_id": "owner", "question":
         "Who owns Cedar Grid in this Finance Operations workspace?",
         "depends_on": []},
        {"subgoal_id": "leader", "question":
         "Who is the direct leader of {verified_entity} in this workspace?",
         "depends_on": ["owner"]},
    ]}
    assert validate_model_proposed_plan(
        proposal, user_query=goal, max_steps=5,
    ).goal == goal


def test_empty_primary_and_alternate_mark_only_current_subgoal_blocked():
    plan = _plan()
    successor = _evidence("ev_one", "Cobalt B replaced Meridian A.")
    records = [
        _record(1, "search_notes", [successor], subgoal="successor"),
        _record(2, "read_verified_note", successor),
        _record(3, "search_notes", [], subgoal="owner"),
        _record(4, "search_notes", [], subgoal="owner"),
    ]

    assessed = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)

    assert assessed.status_by_subgoal == {"successor": "completed", "owner": "blocked"}
    assert assessed.evidence_refs() == {successor["evidence_id"]}


def test_plan_rejects_same_quote_from_another_workspace_or_source():
    plan = _plan()
    quote = "Cobalt B replaced Meridian A."
    local = _evidence("local", quote)
    foreign = _evidence("foreign", quote, workspace="workspace-two")
    other_source = _evidence("other", quote, path="other.md")
    for bad_read in (foreign, other_source):
        assessed = assess_task_plan(
            plan,
            [_record(1, "search_notes", [local], subgoal="successor"),
             _record(2, "read_verified_note", bad_read)],
            run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
            expected_workspace_id=WORKSPACE,
        )
        assert assessed.missing_subgoals == ("successor", "owner")


def test_dependency_entity_requires_full_phrase_boundary():
    plan = _plan()
    hit = _evidence("local", "Cobalt B replaced Meridian A.")
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [hit], subgoal="successor"),
         _record(2, "read_verified_note", hit)],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed, "owner", {"successor": "obalt"})


def test_failed_search_attempts_are_bounded_and_keep_parent_evidence():
    plan = _plan()
    successor = _evidence("local", "Cobalt B replaced Meridian A.")
    failed = ToolExecutionRecord(
        call_id="call_failed", run_id="run_1", task_id="task_1",
        agent_id="retrieval_agent", tool_id="search_notes", sequence=3,
        status="failed",
        arguments={"query": "q", "source_context": {"subgoal_id": "owner"}, "limit": 3},
        error={"code": "TOOL_INVALID_ARGUMENTS"},
    )
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [successor], subgoal="successor"),
         _record(2, "read_verified_note", successor), failed,
         _record(4, "search_notes", [], subgoal="owner")],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )
    assert assessed.status_by_subgoal == {"successor": "completed", "owner": "blocked"}
    assert assessed.evidence_refs() == {successor["evidence_id"]}


def test_plan_does_not_trust_a_result_with_the_wrong_tool_call_identity():
    plan = _plan()
    hit = _evidence("local", "Cobalt B replaced Meridian A.")
    search = _record(1, "search_notes", [hit], subgoal="successor")
    read = _record(2, "read_verified_note", hit)
    forged = replace(
        read,
        result=ToolResult(
            call_id="other_call", tool_id="read_verified_note", status="ok", value=hit,
        ).to_dict(),
    )
    assessed = assess_task_plan(
        plan, [search, forged], run_id="run_1", task_id="task_1",
        agent_id="retrieval_agent", expected_workspace_id=WORKSPACE,
    )
    assert assessed.missing_subgoals == ("successor", "owner")
