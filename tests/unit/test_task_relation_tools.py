from dataclasses import asdict, replace

import pytest

from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools
from tests.task_revision_support import build_case, public_cases


def snapshot(case_id="D04"):
    case = next(c for c in public_cases() if c["case_id"] == case_id)
    observations, artifacts, _, registry = build_case(case)
    return TaskContextSnapshot(case["workspace_id"], tuple(observations.values()),
                               tuple(artifacts.values()), registry)


def test_context_returns_only_verified_observations_and_unconfirmed_candidates(tmp_path):
    current = snapshot()
    tools = TaskRelationTools(current.workspace_id, lambda: current, relation_path=tmp_path / "missing.sqlite")
    result = tools.inspect_task_context(current.workspace_id, max_candidates=1)
    assert result["observations"]
    assert result["observations"][0]["ref"]["observation_id"]
    assert result["observations"][0]["observation"]["provenance"]
    assert all(c["relation_status"] == "UNCONFIRMED" for c in result["discovery"]["candidates"])
    assert result["discovery"]["limits"]["max_candidates"] == 1
    assert result["discovery"]["truncated"]
    assert result["semantic_proposals"]["status"] == "NOT_CONNECTED"
    query = tools.query_task_relations(current.workspace_id)
    assert query["status"] == "NO_VERIFIED_RELATIONS"
    assert query["returned_relations"] == query["total_matching_relations"] == 0
    assert not (tmp_path / "missing.sqlite").exists()


def test_wrong_workspace_rejected_before_snapshot_read():
    def forbidden():
        pytest.fail("must not read an unauthorized snapshot")
    tools = TaskRelationTools("authorized", forbidden)
    with pytest.raises(ValueError, match="workspace"):
        tools.inspect_task_context("other")
    with pytest.raises(ValueError, match="workspace"):
        tools.query_task_relations("other")


def test_stale_source_and_snapshot_overflow_fail_closed():
    current = snapshot()
    span = current.observations[0].provenance
    current.source_registry.register_evidence(current.workspace_id, span.evidence_ref,
        content_hash=span.content_hash, source_version="replacement")
    tools = TaskRelationTools(current.workspace_id, lambda: current)
    result = tools.inspect_task_context(current.workspace_id)
    assert span.evidence_ref not in [r["observation"]["provenance"]["evidence_ref"] for r in result["observations"]]
    assert result["discovery"]["rejected"]
    huge = replace(current, observations=current.observations * 129)
    with pytest.raises(ValueError, match="budget"):
        TaskRelationTools(current.workspace_id, lambda: huge).inspect_task_context(current.workspace_id)


def test_relation_query_keeps_rule_proof_date_comparison_unknown_and_history(tmp_path):
    from tests.unit.test_task_relation_query import seed, retract
    for case_id in ("D04", "D07"):
        path = tmp_path / (case_id + ".sqlite")
        case, obs, artifacts, registry, rows, _ = seed(path, case_id)
        current = TaskContextSnapshot(case["workspace_id"], tuple(obs.values()), tuple(artifacts.values()), registry)
        tools = TaskRelationTools(current.workspace_id, lambda: current, relation_path=path)
        before = path.read_bytes()
        result = tools.query_task_relations(current.workspace_id, include_history=True, as_of="2026-10-18")
        if case_id == "D04":
            assert any(r["current_supported"] for r in result["relations"])
            assert any(c["current_comparison"]["delta_calendar_days"] == 4
                       for p in result["plans"] for c in p["comparisons"])
        else:
            assert any(r["assessment"]["verdict"] == "UNKNOWN" for r in result["relations"])
        assert path.read_bytes() == before
        retract(path, current.workspace_id, rows[0]["same_task"]["assessment_id"])
        after = tools.query_task_relations(current.workspace_id, include_history=True)
        assert any(r["persistent_lifecycle"] == "RETRACTED" and not r["current_supported"]
                   for r in after["relations"])


def test_query_wire_catalogs_preserve_exact_p4_evidence_and_payloads():
    from linkloom.tools.task_relations import _bounded_query_view
    from linkloom.semantic_ingestion.task_relation_models import ObservationRef
    current = snapshot()
    observation = current.observations[0]
    raw = {"relations": [{"left": ObservationRef.from_observation(observation).to_dict(),
                          "observations": [observation.to_dict(), observation.to_dict()],
                          "span": asdict(observation.provenance)}],
           "plans": [], "missing_context": [], "truncated": False}
    wire = _bounded_query_view(raw)
    def expand(value):
        if isinstance(value, list):
            return [expand(v) for v in value]
        if not isinstance(value, dict):
            return value
        for key, catalog in (("span_ref", "evidence_spans"),
                             ("observation_snapshot_ref", "observation_snapshots"),
                             ("observation_ref", "observation_refs")):
            if set(value) == {key}:
                return expand(wire[catalog][value[key]])
        return {key: expand(v) for key, v in value.items()}
    assert expand(wire["relations"]) == raw["relations"]
    assert len(wire["observation_snapshots"]) == 1


def test_final_rejects_unseen_receipts_and_free_text_claims():
    from types import SimpleNamespace
    from linkloom.agents.task_relation_result import project_task_relation_result
    from linkloom.runtime.errors import ValidationError
    import json
    records = [SimpleNamespace(call_id=tool, tool_id=tool, status="completed", sequence=index,
        result={"status": "ok", "value": {"workspace_id": "w"}}, arguments={"workspace_id": "w"})
        for index, tool in enumerate(("inspect_task_context", "query_task_relations"))]
    final = {"context_call_id": records[0].call_id, "relations_call_id": records[1].call_id}
    with pytest.raises(ValidationError):
        project_task_relation_result(json.dumps(final), records, [], "w")
    with pytest.raises(ValidationError):
        project_task_relation_result(json.dumps({**final, "claim": "SUPPORTED"}), records,
                                     [r.result for r in records], "w")


def test_truncated_results_do_not_claim_absence_of_verified_relations(tmp_path, monkeypatch):
    import linkloom.tools.task_relations as module
    from linkloom.semantic_ingestion.task_relation_query import RelationQuery
    from tests.unit.test_task_relation_query import seed
    path = tmp_path / "truncation.sqlite"
    case, obs, arts, registry, _, args = seed(path)
    with RelationQuery(path, **args) as query:
        supported = next(r for r in query.list(case["workspace_id"])
                         if r.current_supported and r.assessment.predicate == "SAME_TASK")
    class DiagnosticHeavyQuery:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def list(self, *_args, **_kwargs):
            return (*([replace(supported, current_supported=False)] * 100), supported)
    monkeypatch.setattr(module, "RelationQuery", DiagnosticHeavyQuery)
    current = TaskContextSnapshot(case["workspace_id"], tuple(obs.values()), tuple(arts.values()), registry)
    tools = TaskRelationTools(current.workspace_id, lambda: current, relation_path=path)
    output = tools.query_task_relations(current.workspace_id)
    assert output["truncated"]
    assert not any(r["current_supported"] for r in output["relations"])
    assert output["status"] == "VERIFIED_RELATIONS_AVAILABLE"
    assert output["current_supported_in_snapshot"] == 1
    assert output["total_matching_relations"] == 101


def test_history_filter_applies_to_plan_chain_extensions(tmp_path):
    from tests.unit.test_task_relation_query import seed, retract
    path = tmp_path / "history.sqlite"
    case, obs, arts, registry, rows, _ = seed(path)
    current = TaskContextSnapshot(case["workspace_id"], tuple(obs.values()), tuple(arts.values()), registry)
    tools = TaskRelationTools(current.workspace_id, lambda: current, relation_path=path)
    retract(path, current.workspace_id, rows[0]["same_task"]["assessment_id"])
    visible = tools.query_task_relations(current.workspace_id, include_history=False)
    historical = tools.query_task_relations(current.workspace_id, include_history=True)
    assert all(r["category"] != "HISTORICAL" for r in visible["relations"])
    assert all(r["category"] != "HISTORICAL" for p in visible["plans"] for r in p["revisions"])
    assert any(r["category"] == "HISTORICAL" for r in historical["relations"])


def test_plan_extensions_share_the_relation_count_budget(tmp_path, monkeypatch):
    from copy import deepcopy
    import linkloom.tools.task_relations as module
    from linkloom.semantic_ingestion.task_relation_query import RelationQuery
    from tests.unit.test_task_relation_query import seed
    path = tmp_path / "plan-budget.sqlite"
    case, obs, arts, registry, _, args = seed(path)
    with RelationQuery(path, **args) as query:
        revision = next(r for r in query.list(case["workspace_id"])
                        if r.current_supported and r.assessment.predicate == "REVISES_PLAN")
        plan = query.plan(case["workspace_id"], revision.assessment.subject.observation_id)
    base = revision.to_dict()
    plan["revisions"] = []
    for index in range(70):
        row = deepcopy(base)
        row["assessment"]["assessment_id"] = "synthetic-budget-" + str(index)
        plan["revisions"].append(row)
    class LargePlanQuery:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def list(self, *_args, **_kwargs): return (revision,)
        def plan(self, *_args, **_kwargs): return deepcopy(plan)
    monkeypatch.setattr(module, "RelationQuery", LargePlanQuery)
    current = TaskContextSnapshot(case["workspace_id"], tuple(obs.values()), tuple(arts.values()), registry)
    output = TaskRelationTools(current.workspace_id, lambda: current, relation_path=path).query_task_relations(current.workspace_id)
    assert output["truncated"] and "RELATION_RESULT_BUDGET" in output["missing_context"]
    assert output["returned_relations"] <= output["max_relations"] == 64
