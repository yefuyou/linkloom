"""Plan state is reconstructed from verified, task-scoped Tool Ledger facts."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from linkloom.evidence_identity import passage_evidence_id
from linkloom.agents.claim_grounding import (
    verified_dependency_values, verified_dependent_leader_values,
    verified_subgoal_values,
    verified_relation_conflict_refs,
)
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.models import ToolExecutionRecord
from linkloom.runtime.models import RuntimeState, SourceContext
from linkloom.runtime.progress import assess_progress
from linkloom.runtime.recovery_policy import decide_recovery
from linkloom.runtime.task_plan import (
    TaskPlan, assess_task_plan, conflicted_subgoals, refusal_missing_subgoals,
    validate_model_proposed_plan,
    verified_dependency_entities,
)
from linkloom.tools.contracts import ToolResult


def test_model_plan_accepts_literal_hyphenated_alias_anchor_but_not_foreign_alias():
    goal = (
        "QF-Link 这个别名对应哪个现行项目？先确认项目，再找现任负责人，"
        "最后找这个人的直属领导；每一跳都给出来源。"
    )
    plan = {"goal": goal, "subgoals": [
        {"subgoal_id": "alias", "question": "What project is QF-Link an alias for?",
         "depends_on": []},
        {"subgoal_id": "owner", "question": "Who owns {verified_entity}?",
         "depends_on": ["alias"]},
        {"subgoal_id": "leader", "question": "Who is {verified_entity}'s direct leader?",
         "depends_on": ["owner"]},
    ]}
    assert len(validate_model_proposed_plan(plan, user_query=goal, max_steps=18).subgoals) == 3
    foreign = deepcopy(plan)
    foreign["subgoals"][0]["question"] = "What project is ZZ-Link an alias for?"
    with pytest.raises(ValidationError):
        validate_model_proposed_plan(foreign, user_query=goal, max_steps=18)


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


def test_verified_alias_binds_project_then_owner_without_guessing():
    plan = TaskPlan.from_dict({
        "goal": "Who owns the project behind Nimbus-Launch and who leads that owner?",
        "subgoals": [
            {"subgoal_id": "alias", "question": "Which project does Nimbus-Launch alias?", "depends_on": []},
            {"subgoal_id": "owner", "question": "Who owns {verified_entity}?", "depends_on": ["alias"]},
            {"subgoal_id": "leader", "question": "Who is {verified_entity}'s direct leader?", "depends_on": ["owner"]},
        ],
    })
    alias = _evidence("alias", "Nimbus-Launch is an internal alias for the Cedar Harbor project.", path="alias.md")
    owner = _evidence("owner", "As of 2026-10-01 Mira Chen is the accountable owner of Cedar Harbor.", path="owner.md")
    records = [
        _record(1, "search_notes", [alias], subgoal="alias"),
        _record(2, "read_verified_note", alias),
        _record(3, "search_notes", [owner], subgoal="owner"),
        _record(4, "read_verified_note", owner),
    ]
    after_alias = assess_task_plan(plan, records[:2], run_id="run_1", task_id="task_1",
                                   agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert verified_dependency_entities(plan, after_alias, "owner", {"alias": "Cedar Harbor"}) == ("Cedar Harbor",)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, after_alias, "owner", {"alias": "Other Harbor"})
    after_owner = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                   agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert verified_dependency_entities(plan, after_owner, "leader", {"owner": "Mira Chen"}) == ("Mira Chen",)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, after_owner, "leader", {"owner": "Cedar Harbor"})
    wrong_workspace = _evidence("other", alias["quote"], workspace="other-workspace", path="alias.md")
    wrong = assess_task_plan(plan, [_record(1, "search_notes", [wrong_workspace], subgoal="alias"),
                                    _record(2, "read_verified_note", wrong_workspace)],
                             run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
                             expected_workspace_id=WORKSPACE)
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, wrong, "owner", {"alias": "Cedar Harbor"})


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


def test_structured_owner_relation_marks_irrelevant_verified_read_as_missing():
    plan = TaskPlan.from_dict({"schema_version": "task-plan/v2", "goal": "Who owns Nacre Harbor?", "subgoals": [
        {"subgoal_id": "records", "question": "Find records for Nacre Harbor.",
         "depends_on": [], "relation_path": []},
        {"subgoal_id": "owner", "question": "Find the current owner of {verified_entity}.",
         "depends_on": ["records"], "relation_path": ["owner_of_project"]},
    ]})
    brief = _evidence("brief", "Nacre Harbor is a project awaiting an owner assignment.")
    rows = [_record(1, "search_notes", [brief], subgoal="records"), _record(2, "read_verified_note", brief),
            _record(3, "search_notes", [brief], subgoal="owner"), _record(4, "read_verified_note", brief)]
    assessed = assess_task_plan(plan, rows, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert refusal_missing_subgoals(plan, assessed) == ("owner",)


def test_structured_reporting_depends_on_owner_project_and_snapshot():
    plan = TaskPlan.from_dict({"schema_version": "task-plan/v2", "goal": "Who owns Harbor Kite and who leads them?", "subgoals": [
        {"subgoal_id": "owner", "question": "Who owns Harbor Kite?", "depends_on": [],
         "relation_path": ["owner_of_project"]},
        {"subgoal_id": "leader", "question": "Who does {verified_entity} report to?",
         "depends_on": ["owner"], "relation_path": ["reports_to"]},
    ]})
    owner = _evidence("owner", "As of 2026-10-01 Ava Morgan is the accountable owner of Harbor Kite.", path="owner.md")
    wrong_project = _evidence("other", "Ava Morgan reports directly to Calvin Moss in the Delta Bay project.", path="other.md")
    old = _evidence("old", "As of 2024-04-01 Ava Morgan reports directly to Calvin Moss in the Harbor Kite project.", path="old.md")
    correct = _evidence("current", "As of 2026-10-01 Ava Morgan reports directly to Sam Reed in the Harbor Kite project.", path="current.md")
    prefix = [_record(1, "search_notes", [owner], subgoal="owner"),
              _record(2, "read_verified_note", owner)]
    for candidate, missing in ((wrong_project, True), (old, True), (correct, False)):
        rows = prefix + [_record(3, "search_notes", [candidate], subgoal="leader"),
                         _record(4, "read_verified_note", candidate)]
        assessed = assess_task_plan(plan, rows, run_id="run_1", task_id="task_1",
                                    agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
        assert ("leader" in refusal_missing_subgoals(plan, assessed)) is missing


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


def test_compound_nickname_and_owner_root_requires_verified_alias_owner_join():
    plan = TaskPlan.from_dict({"goal": "Find the named initiative's owner and manager.", "subgoals": [
        {"subgoal_id": "root",
         "question": "What is the formal project name and current accountable owner for the initiative nicknamed BQ-Stream?",
         "depends_on": []},
        {"subgoal_id": "manager",
         "question": "Who is the direct manager that {verified_entity} reports to?",
         "depends_on": ["root"]},
    ]})
    alias = _evidence("alias", "BQ-Stream is an internal alias for the Harbor Meridian Hub project.", path="alias.md")
    owner = _evidence("owner", "As of 2026-10-13 Elena Fox is the accountable owner of Harbor Meridian Hub.", path="owner.md")

    def assessed(other_owner):
        records = [_record(1, "search_notes", [alias, other_owner], subgoal="root"),
                   _record(2, "read_verified_note", alias),
                   _record(3, "read_verified_note", other_owner)]
        return assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)

    assert verified_dependency_entities(plan, assessed(owner), "manager", {"root": "Elena Fox"}) == ("Elena Fox",)
    unrelated = _evidence("other", "As of 2026-10-13 Elena Fox is the accountable owner of Other Meridian Hub.",
                          path="other-owner.md")
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed(unrelated), "manager", {"root": "Elena Fox"})
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed(owner), "manager", {"root": "Wrong Person"})


def test_reporting_question_binds_only_verified_owner_and_allows_missing_last_hop():
    parent = "Who is currently responsible for Bluehaven Relay?"
    child = "To whom does {verified_entity} report?"
    owner = _evidence("owner", "As of 2026-10-10 Cora Ellis is the accountable owner of Bluehaven Relay.")
    unrelated = _evidence("calendar", "Bluehaven Relay has a review meeting next week.", path="calendar.md")
    assert verified_dependency_values(parent, child, [owner]) == ("cora ellis",)
    assert verified_dependency_values(parent, "{verified_entity}向谁汇报？", [owner]) == ("cora ellis",)
    assert verified_dependency_values(parent, "{verified_entity}的直属上级是谁？", [owner]) == ("cora ellis",)
    assert verified_dependent_leader_values(parent, child, [owner], [unrelated]) == ()
    leader = _evidence("leader", "As of 2026-10-11 Cora Ellis reports directly to Nadia Fox.", path="leader.md")
    assert verified_dependent_leader_values(parent, child, [owner], [leader]) == ("nadia fox",)
    assert verified_dependency_values(parent, "Who reports to {verified_entity}?", [owner]) is None
    wrong_project = _evidence("wrong", "As of 2026-10-10 Cora Ellis is the accountable owner of Other Relay.")
    assert verified_dependency_values(parent, child, [wrong_project]) == ()


def test_owner_dependency_uses_unique_literally_named_verified_project():
    parent = "Who is currently responsible for the Juniper Tidal Mesh task and what is the source?"
    child = "Who is the direct supervisor that {verified_entity} reports to?"
    owner = _evidence("owner", "As of 2026-10-13 Aria Bloom is the accountable owner of Juniper Tidal Mesh.")
    other = _evidence("other", "As of 2026-10-13 Nora Vale is the accountable owner of Cedar Reach.", path="other.md")
    assert verified_dependency_values(parent, child, [owner]) == ("aria bloom",)
    assert verified_dependency_values(parent, child, [other]) == ()
    assert verified_dependency_values(parent, child, [owner, other]) == ("aria bloom",)
    ambiguous = parent + " Compare Cedar Reach too."
    assert verified_dependency_values(ambiguous, child, [owner, other]) == ()


def test_dated_owner_dependency_uses_only_exact_snapshot_date():
    old = _evidence("old", "As of 2024-10-13 Lucian Park is the accountable owner of Ember Relay.", path="old.md")
    current = _evidence("current", "As of 2026-10-13 Hana Brooks is the accountable owner of Ember Relay.", path="current.md")
    conflicting = _evidence("conflicting", "As of 2026-10-13 Nora Vale is the accountable owner of Ember Relay.", path="conflict.md")
    child = "截至 2026-10-13，{verified_entity} 向谁汇报？"
    assert verified_dependency_values("截至 2026-10-13，Ember Relay 的现任负责人是谁？", child, [old, current]) == ("hana brooks",)
    assert verified_subgoal_values("截至 2026-10-13，Ember Relay 的现任负责人是谁？", [old, current]) == ("hana brooks",)
    assert verified_dependency_values("截至 2024-10-13，Ember Relay 的负责人是谁？", child, [old, current]) == ("lucian park",)
    assert verified_dependency_values("Ember Relay 的负责人是谁？", child, [old, current]) == ()
    assert verified_dependency_values("截至 2026-10-13，Ember Relay 的现任负责人是谁？", child, [old, current, conflicting]) == ()


def test_verified_current_conflict_guides_final_only_after_both_source_reads():
    plan = TaskPlan.from_dict({"goal": "Who owns Maple Spire and who leads the owner?", "subgoals": [
        {"subgoal_id": "owner", "question": "Who owns Maple Spire?", "depends_on": []},
        {"subgoal_id": "leader", "question": "Who leads {verified_entity}?", "depends_on": ["owner"]},
    ]})
    first = _evidence("first", "As of 2026-10-10 Lena Ortiz is the accountable owner of Maple Spire.", path="owner-a.md")
    second = _evidence("second", "As of 2026-10-10 Evan Shaw is the accountable owner of Maple Spire.", path="owner-b.md")
    records = [_record(1, "search_notes", [first, second], subgoal="owner"),
               _record(2, "read_verified_note", first), _record(3, "read_verified_note", second)]

    def progress(rows):
        state = RuntimeState(
            schema_version=1, run_id="run_1", thread_id="thread_1", workflow="team_decision",
            status="running", step_seq=1, request_ref="request.json",
            source=SourceContext(index_path="index.json", index_sha256="e" * 64,
                                 vault_root_fingerprint=WORKSPACE),
            tool_ledger=rows,
        )
        return assess_progress(state, task_id="task_1", agent_id="retrieval_agent", task_plan=plan)

    assert decide_recovery(progress(records[:2])).action == "CONTINUE"
    conflict = decide_recovery(progress(records))
    assert conflict.action == "FINAL_CONFLICT"
    assert first["evidence_id"] in conflict.hint and second["evidence_id"] in conflict.hint
    assert "owner" in conflict.hint

    consistent = _evidence("same", first["quote"], path="owner-b.md")
    consistent_records = [_record(1, "search_notes", [first, consistent], subgoal="owner"),
                          _record(2, "read_verified_note", first),
                          _record(3, "read_verified_note", consistent)]
    assert decide_recovery(progress(consistent_records)).action == "CONTINUE"
    historical = _evidence("old", "As of 2026-09-10 Evan Shaw is the accountable owner of Maple Spire.", path="owner-b.md")
    historical_records = [_record(1, "search_notes", [first, historical], subgoal="owner"),
                          _record(2, "read_verified_note", first),
                          _record(3, "read_verified_note", historical)]
    assert decide_recovery(progress(historical_records)).action == "CONTINUE"


def test_current_owner_conflict_in_chinese_plan_blocks_dependent_identity_without_extra_search():
    plan = TaskPlan.from_dict({"goal": "Find the current owner and report conflicts.", "subgoals": [
        {"subgoal_id": "step_1", "question": "根据现有有效记录，Silver Creek Node 的现任负责人是谁？", "depends_on": []},
        {"subgoal_id": "step_2", "question": "除 {verified_entity} 之外，是否存在相互矛盾的有效来源记录，关于 Silver Creek Node 现任负责人的各方陈述分别为何？", "depends_on": ["step_1"]},
    ]})
    first = _evidence("first", "As of 2026-10-12 Iris Holt is the accountable owner of Silver Creek Node.", path="owner-a.md")
    second = _evidence("second", "As of 2026-10-12 Noah Vega is the accountable owner of Silver Creek Node.", path="owner-b.md")

    def assess(other):
        rows = [_record(1, "search_notes", [first, other], subgoal="step_1"),
                _record(2, "read_verified_note", first), _record(3, "read_verified_note", other)]
        return assess_task_plan(plan, rows, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)

    conflict = assess(second)
    # The natural-language plan question is not a trusted relation descriptor.
    assert conflicted_subgoals(plan, conflict) == ()
    assert refusal_missing_subgoals(plan, conflict) == ("step_2",)
    expected = {first["evidence_id"], second["evidence_id"]}
    assert set(verified_relation_conflict_refs(
        "owner_of", "silver creek node", conflict.evidence_by_subgoal["step_1"],
    )) == expected
    for other in (
        _evidence("same", first["quote"], path="owner-b.md"),
        _evidence("old", "As of 2026-09-12 Noah Vega is the accountable owner of Silver Creek Node.", path="owner-b.md"),
        _evidence("wrong", "As of 2026-10-12 Noah Vega is the accountable owner of Other Creek Node.", path="owner-b.md"),
    ):
        assert verified_relation_conflict_refs(
            "owner_of", "silver creek node", assess(other).evidence_by_subgoal["step_1"],
        ) == ()


def test_later_dependent_search_reuses_only_matching_prior_verified_read():
    plan = TaskPlan.from_dict({"goal": "Resolve alias and owner", "subgoals": [
        {"subgoal_id": "alias", "question": "Which project does Nimbus-Launch alias?", "depends_on": []},
        {"subgoal_id": "owner", "question": "Who owns {verified_entity}?", "depends_on": ["alias"]},
    ]})
    passage = _evidence("shared", "Nimbus-Launch is an internal alias for the Cedar Harbor project. "
                        "Mira Chen is the accountable owner of Cedar Harbor.", path="shared.md")
    search_owner = replace(_record(3, "search_notes", [passage], subgoal="owner"),
                           arguments={"query": "Cedar Harbor owner", "limit": 3,
                                      "source_context": {"subgoal_id": "owner",
                                                         "dependency_entities": {"alias": "Cedar Harbor"}}})
    records = [_record(1, "search_notes", [passage], subgoal="alias"),
               _record(2, "read_verified_note", passage), search_owner]
    assessed = assess_task_plan(plan, records, run_id="run_1", task_id="task_1",
                                agent_id="retrieval_agent", expected_workspace_id=WORKSPACE)
    assert assessed.complete
    assert assessed.evidence_by_subgoal["owner"] == (passage,)

    wrong_relation = _evidence("wrong", "Nimbus-Launch is an internal alias for the Cedar Harbor project. "
                               "Mira Chen is the accountable owner of Another Harbor.", path="wrong.md")
    wrong_search = replace(search_owner, result=ToolResult(
        search_owner.call_id, "search_notes", "ok", value=[wrong_relation]).to_dict())
    wrong = assess_task_plan(plan, [_record(1, "search_notes", [wrong_relation], subgoal="alias"),
                                    _record(2, "read_verified_note", wrong_relation), wrong_search],
                             run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
                             expected_workspace_id=WORKSPACE)
    assert wrong.evidence_by_subgoal["owner"] == ()

    unbound = replace(search_owner, arguments={"query": "Cedar Harbor owner", "limit": 3,
                                               "source_context": {"subgoal_id": "owner",
                                                                  "dependency_entities": {"alias": "Other Harbor"}}})
    unbound_assessment = assess_task_plan(plan, records[:2] + [unbound], run_id="run_1",
                                          task_id="task_1", agent_id="retrieval_agent",
                                          expected_workspace_id=WORKSPACE)
    assert unbound_assessment.evidence_by_subgoal["owner"] == ()


def test_phase14_handoff_question_binds_only_a_unique_verified_subject():
    parent = "Atlas Meridian A 退场后由哪项方案接棒？"
    child = "Atlas 中 {verified_entity} 方案的实际负责人是谁？"
    evidence = _evidence(
        "handoff",
        "On 2025-02-14 Cobalt B replaced Meridian A as the current Atlas rollout plan.",
    )

    assert verified_dependency_values(parent, child, [evidence]) == ("cobalt b",)

    ambiguous = _evidence(
        "ambiguous-handoff",
        "On 2025-02-14 Cobalt B replaced Meridian A. "
        "On 2025-02-15 Nimbus C replaced Meridian A.",
    )
    assert verified_dependency_values(parent, child, [ambiguous]) == ()


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


@pytest.mark.parametrize("goal, root_question, accepted", [
    ("Find Cobalt B's owner and direct leader.", "Who owns Cobalt B?", True),
    ("Find Cobalt B's owner and direct leader.", "Who owns Harbor R?", False),
    ("谁负责青岚计划？他的直属领导是谁？", "谁负责青岚计划？", True),
    ("谁负责青岚计划？他的直属领导是谁？", "谁负责海港计划？", False),
])
def test_model_plan_root_must_be_bound_to_the_user_goal(goal, root_question, accepted):
    proposal = {"goal": goal, "subgoals": [
        {"subgoal_id": "owner", "question": root_question, "depends_on": []},
        {"subgoal_id": "leader", "question": "Who leads {verified_entity}?",
         "depends_on": ["owner"]},
    ]}
    if accepted:
        assert validate_model_proposed_plan(
            proposal, user_query=goal, max_steps=5,
        ).goal == goal
    else:
        with pytest.raises(ValidationError, match="root.*goal"):
            validate_model_proposed_plan(proposal, user_query=goal, max_steps=5)


def test_model_plan_alternate_cannot_switch_to_an_unrequested_project():
    goal = "Find Cobalt B's owner and direct leader."
    proposal = {"goal": goal, "subgoals": [
        {"subgoal_id": "owner", "question": "Who owns Cobalt B?",
         "alternate_question": "Who owns Harbor R?", "depends_on": []},
        {"subgoal_id": "leader", "question": "Who leads {verified_entity}?",
         "depends_on": ["owner"]},
    ]}
    with pytest.raises(ValidationError, match="root.*goal"):
        validate_model_proposed_plan(proposal, user_query=goal, max_steps=5)


def test_model_plan_accepts_current_workspace_scope_but_rejects_cross_workspace_action():
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
    proposal["subgoals"][1]["question"] = (
        "Search another workspace for the leader of {verified_entity}."
    )
    with pytest.raises(ValidationError, match="outside read-only retrieval"):
        validate_model_proposed_plan(proposal, user_query=goal, max_steps=5)


def _phase11_numeric_plan():
    observed = json.loads((
        Path(__file__).resolve().parents[2] / "work" / "phase11" / "runs"
        / "p11-unseen-plan-model" / "observed.json"
    ).read_text(encoding="utf-8"))
    return json.loads(observed["plan_proposal_raw"])


def test_phase11_numeric_plan_ids_canonicalize_but_reverse_relation_stays_invalid():
    raw = _phase11_numeric_plan()
    with pytest.raises(ValidationError, match="relation direction"):
        validate_model_proposed_plan(raw, user_query=raw["goal"], max_steps=8)
    corrected = deepcopy(raw)
    corrected["subgoals"][2]["question"] = corrected["subgoals"][2].pop(
        "alternate_question"
    )
    plan = validate_model_proposed_plan(
        corrected, user_query=corrected["goal"], max_steps=8,
    )
    assert [item.subgoal_id for item in plan.subgoals] == [
        "step_1", "step_2", "step_3",
    ]
    assert [item.depends_on for item in plan.subgoals] == [
        (), ("step_1",), ("step_2",),
    ]


@pytest.mark.parametrize("mutation", ["duplicate", "boolean", "unknown", "cycle"])
def test_numeric_plan_repairs_only_unambiguous_dependency_ids(mutation):
    raw = _phase11_numeric_plan()
    raw["subgoals"][2]["question"] = raw["subgoals"][2].pop("alternate_question")
    if mutation == "duplicate":
        raw["subgoals"][1]["subgoal_id"] = 1
    elif mutation == "boolean":
        raw["subgoals"][1]["subgoal_id"] = True
    elif mutation == "unknown":
        raw["subgoals"][2]["depends_on"] = [99]
    elif mutation == "cycle":
        raw["subgoals"][0]["depends_on"] = [3]
    with pytest.raises(ValidationError):
        validate_model_proposed_plan(raw, user_query=raw["goal"], max_steps=8)


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


@pytest.mark.parametrize(
    ("run_id", "expected_ids"),
    [
        ("P14-PLAN-DEFAULT", ["step_1", "step_2", "step_3"]),
        ("P14-PLAN-REPAIR", ["s1", "s2", "s3"]),
    ],
)
def test_phase14_real_plan_proposals_normalize_only_unambiguous_shape_errors(
    run_id: str, expected_ids: list[str],
):
    observed_path = (
        Path(__file__).resolve().parents[2] / "work" / "phase14_gemini" / "runs"
        / run_id / "observed.json"
    )
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    raw = json.loads(observed["plan_proposal_raw"])

    from linkloom.runtime.task_plan import validate_model_proposed_plan

    plan = validate_model_proposed_plan(
        raw, user_query=raw["goal"], max_steps=9,
    )

    assert [item.subgoal_id for item in plan.subgoals] == expected_ids
    assert [item.depends_on for item in plan.subgoals] == [
        (), (expected_ids[0],), (expected_ids[1],),
    ]
    assert all(item.alternate_question for item in plan.subgoals)


def test_model_plan_normalization_does_not_guess_a_missing_dependent_edge():
    observed_path = (
        Path(__file__).resolve().parents[2] / "work" / "phase14_gemini" / "runs"
        / "P14-PLAN-REPAIR" / "observed.json"
    )
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    raw = json.loads(observed["plan_proposal_raw"])
    raw["subgoals"][1].pop("depends_on")

    from linkloom.runtime.task_plan import validate_model_proposed_plan

    with pytest.raises(ValidationError):
        validate_model_proposed_plan(raw, user_query=raw["goal"], max_steps=9)


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


def test_dependency_entity_must_be_the_verified_replacement_not_just_any_mentioned_name():
    plan = _plan()
    hit = _evidence(
        "local",
        "Cobalt B replaced Meridian A as the current Atlas plan. "
        "Zephyr C replaced Vega V.",
    )
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [hit], subgoal="successor"),
         _record(2, "read_verified_note", hit)],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    assert verified_dependency_entities(plan, assessed, "owner", {"successor": "Cobalt B"}) == (
        "Cobalt B",
    )
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed, "owner", {"successor": "Meridian A"})
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed, "owner", {"successor": "Zephyr C"})


def test_leader_hop_uses_the_verified_owner_entity():
    plan = TaskPlan.from_dict({
        "goal": "Who owns Cobalt B and who is their direct leader?",
        "subgoals": [
            {"subgoal_id": "owner", "question": "Who owns Cobalt B?", "depends_on": []},
            {"subgoal_id": "leader", "question": "Who is the direct leader of the owner?",
             "depends_on": ["owner"]},
        ],
    })
    hit = _evidence("local", "Mira Chen is the accountable owner of Cobalt B for Atlas.")
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [hit], subgoal="owner"),
         _record(2, "read_verified_note", hit)],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    assert verified_dependency_entities(plan, assessed, "leader", {"owner": "Mira Chen"}) == (
        "Mira Chen",
    )
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, assessed, "leader", {"owner": "Cobalt B"})


def test_two_character_cjk_name_can_advance_only_from_verified_owner_evidence():
    plan = TaskPlan.from_dict({
        "goal": "谁负责青岚计划，直属领导是谁？",
        "subgoals": [
            {"subgoal_id": "owner", "question": "谁负责青岚计划？", "depends_on": []},
            {"subgoal_id": "leader", "question": "负责人的直属领导是谁？",
             "depends_on": ["owner"]},
        ],
    })
    hit = _evidence("local", "李明是青岚计划的负责人。")
    assessed = assess_task_plan(
        plan,
        [_record(1, "search_notes", [hit], subgoal="owner"),
         _record(2, "read_verified_note", hit)],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    assert verified_dependency_entities(plan, assessed, "leader", {"owner": "李明"}) == (
        "李明",
    )


def test_templated_owner_hop_is_bound_to_verified_replacement_subject():
    plan = TaskPlan.from_dict({
        "goal": "Who replaced Meridian A, who owns the replacement, and who leads that owner?",
        "subgoals": [
            {"subgoal_id": "successor", "question": "Which plan replaced Meridian A?",
             "depends_on": []},
            {"subgoal_id": "owner", "question": "Who owns {verified_entity}?",
             "depends_on": ["successor"]},
            {"subgoal_id": "leader", "question": "Who is the direct leader of {verified_entity}?",
             "depends_on": ["owner"]},
        ],
    })
    successor = _evidence("successor", "Cobalt B replaced Meridian A on 2025-02-14.")

    def assess_owner(quote):
        owner = _evidence("owner", quote, path="owner.md")
        records = [
            _record(1, "search_notes", [successor], subgoal="successor"),
            _record(2, "read_verified_note", successor),
            _record(3, "search_notes", [owner], subgoal="owner"),
            _record(4, "read_verified_note", owner),
        ]
        return assess_task_plan(
            plan,
            records,
            run_id="run_1",
            task_id="task_1",
            agent_id="retrieval_agent",
            expected_workspace_id=WORKSPACE,
        )

    matching = assess_owner("Mira Chen is the accountable owner of Cobalt B for Atlas.")
    assert verified_dependency_entities(
        plan, matching, "leader", {"owner": "Mira Chen"}
    ) == ("Mira Chen",)

    unrelated = assess_owner("Mira Chen is the accountable owner of Harbor for Atlas.")
    with pytest.raises(ValidationError):
        verified_dependency_entities(plan, unrelated, "leader", {"owner": "Mira Chen"})


def test_cjk_anaphoric_parent_subject_binds_to_verified_upstream_plan():
    plan = TaskPlan.from_dict({
        "goal": "哪个方案替代了 Meridian A，谁负责该方案，负责人的直属领导是谁？",
        "subgoals": [
            {"subgoal_id": "successor", "question": "哪个方案替代了 Meridian A？",
             "depends_on": []},
            {"subgoal_id": "owner", "question": "谁负责该方案？",
             "depends_on": ["successor"]},
            {"subgoal_id": "leader", "question": "负责人的直属领导是谁？",
             "depends_on": ["owner"]},
        ],
    })
    successor = _evidence("successor", "青岚计划替代了 Meridian A。", path="successor.md")
    owner = _evidence("owner", "李明是青岚计划的负责人。", path="owner.md")
    assessed = assess_task_plan(
        plan,
        [
            _record(1, "search_notes", [successor], subgoal="successor"),
            _record(2, "read_verified_note", successor),
            _record(3, "search_notes", [owner], subgoal="owner"),
            _record(4, "read_verified_note", owner),
        ],
        run_id="run_1", task_id="task_1", agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    assert verified_dependency_entities(plan, assessed, "leader", {"owner": "李明"}) == (
        "李明",
    )


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

@pytest.mark.parametrize(
    "child_question",
    [
        "Who is that owner's supervisor?",
        "Who supervises that owner?",
        "Who leads that owner?",
        "谁是该负责人的直属上司？",
    ],
)
def test_supervisor_paraphrases_reject_co_mentioned_non_owner(child_question):
    plan = TaskPlan.from_dict({
        "goal": "Who owns Cobalt B and who is that owner's supervisor?",
        "subgoals": [
            {"subgoal_id": "owner", "question": "Who owns Cobalt B?",
             "depends_on": []},
            {"subgoal_id": "supervisor", "question": child_question,
             "depends_on": ["owner"]},
        ],
    })
    owner = _evidence(
        "owner",
        "Mira Chen is the accountable owner of Cobalt B; Kai Wu is a project analyst.",
        path="owner.md",
    )
    assessed = assess_task_plan(
        plan,
        [
            _record(1, "search_notes", [owner], subgoal="owner"),
            _record(2, "read_verified_note", owner),
        ],
        run_id="run_1",
        task_id="task_1",
        agent_id="retrieval_agent",
        expected_workspace_id=WORKSPACE,
    )

    with pytest.raises(ValidationError, match="verified relation object"):
        verified_dependency_entities(
            plan, assessed, "supervisor", {"owner": "Kai Wu"}
        )


def test_dependent_leader_requires_bound_person_and_current_temporal_scope():
    owner = _evidence("owner", "As of 2026-10-01 Ava Morgan is the accountable owner of Harbor Kite.")
    old = _evidence("old", "As of 2024-04-01 Ava Morgan reports directly to Calvin Moss.")
    current = _evidence("current", "As of 2026-10-01 Ava Morgan reports directly to Sam Reed.")
    wrong_person = _evidence("wrong", "As of 2026-10-01 Leo Finch reports directly to Calvin Moss.")
    parent = "Who owns Harbor Kite?"
    child = "Who does {verified_entity} report to now?"
    assert verified_dependent_leader_values(parent, child, [owner], [old]) == ()
    assert verified_dependent_leader_values(parent, child, [owner], [wrong_person]) == ()
    assert verified_dependent_leader_values(parent, child, [owner], [current]) == ("sam reed",)
    assert verified_dependent_leader_values(parent, "What is the person's location?", [owner],
                                            [current]) is None
