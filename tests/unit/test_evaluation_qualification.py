"""Qualification rejects bad cases before a model is constructed."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from scripts.evaluation_qualification import qualify_bundle, public_projection


def sample():
    query = "Who is the direct leader of the owner of Beacon Lane?"
    return {
        "id": "EQ-D01", "partition": "dev", "target": "normal",
        "input": {"query": query, "workspace_id": "synthetic-eq",
            "documents": [{"path": "relations.md", "workspace_id": "synthetic-eq",
                "content": "Ada Reed is the accountable owner of Beacon Lane.\nAda Reed reports directly to Evan Fox.\n"}],
            "budget": {"max_steps": 12, "max_provider_requests": 12},
            "task_plan": {"schema_version": "task-plan/v2", "goal": query, "subgoals": [
                {"subgoal_id": "owner", "question": "Who owns Beacon Lane?", "depends_on": [], "relation_path": ["owner_of_project"]},
                {"subgoal_id": "leader", "question": "Who is the direct leader of {verified_entity}?", "depends_on": ["owner"], "relation_path": ["reports_to"]}]}},
        "protocol": {"runner": "qualified-runtime-v1", "fault": None},
        "gold": {"outcome": "complete", "facts": [
            {"subject": "Beacon Lane", "predicate": "owner_of_project", "object": "Ada Reed", "supports": [{"path": "relations.md", "line_start": 1, "line_end": 1, "quote": "Ada Reed is the accountable owner of Beacon Lane."}]},
            {"subject": "Ada Reed", "predicate": "reports_to", "object": "Evan Fox", "supports": [{"path": "relations.md", "line_start": 2, "line_end": 2, "quote": "Ada Reed reports directly to Evan Fox."}]}]}}


def report(*cases):
    return qualify_bundle({"schema_version": "qualification-cases/v1", "cases": list(cases)})


def codes(result):
    return {e["code"] for e in result["errors"]}


def test_valid_sources_and_plan_qualify_without_model():
    assert report(sample())["status"] == "QUALIFIED"


def test_d06_f07_goal_mismatch_is_explicit_not_skipped():
    for part, identifier in [("dev", "RAC3-D06"), ("final", "RAC3-F07")]:
        path = Path("tests/fixtures/real_agent_contract_v3") / (part + ".json")
        old = next(c for c in json.loads(path.read_text(encoding="utf-8"))["cases"] if c["id"] == identifier)
        result = report(old)
        assert result["status"] == "REJECTED"
        assert "QUERY_GOAL_MISMATCH" in codes(result)
        assert all(e.get("evidence") for e in result["errors"])


def test_gold_exact_span_and_outcome_are_checked():
    case = sample()
    case["gold"]["facts"][0]["supports"][0]["quote"] = "invented source"
    case["gold"]["outcome"] = "partial"
    assert {"GOLD_SPAN_MISMATCH", "OUTCOME_SOURCE_MISMATCH"} <= codes(report(case))


def test_unknown_observation_cannot_pass():
    case = sample()
    case["target"] = "imaginary_guard"
    assert "UNOBSERVABLE_TARGET" in codes(report(case))


def test_mechanically_renamed_cross_partition_case_rejected():
    first = sample()
    second = json.loads(json.dumps(first).replace("Beacon Lane", "Cloud Hill").replace("Ada Reed", "Mina Lane").replace("Evan Fox", "Rory West"))
    second.update(id="EQ-F01", partition="final")
    result = report(first, second)
    assert "MECHANICAL_PARTITION_LEAK" in codes(result)


def test_renaming_and_blank_padding_cannot_mint_a_new_partition():
    first = sample()
    second = deepcopy(first)
    second.update(id="PADDED", partition="final")
    second["input"]["documents"][0]["content"] = "\n\n" + first["input"]["documents"][0]["content"]
    for fact in second["gold"]["facts"]:
        for span in fact["supports"]:
            span["line_start"] += 2
            span["line_end"] += 2
    assert "MECHANICAL_PARTITION_LEAK" in codes(report(first, second))


def test_dependency_answers_and_foreign_identity_are_invalid():
    case = sample()
    case["input"]["task_plan"]["subgoals"][1]["question"] = "Who is the direct leader of Ada Reed?"
    case["input"]["documents"][0]["workspace_id"] = "foreign"
    result = report(case)
    assert {"PLAN_ADMISSION_INVALID", "SOURCE_WORKSPACE_MISMATCH"} <= codes(result)


def test_duplicate_data_and_ids_are_errors():
    case = sample()
    assert {"DUPLICATE_CASE_ID", "DUPLICATE_DATA_SHA"} <= codes(report(case, deepcopy(case)))


def test_projection_has_no_oracle_fields():
    projected = public_projection(sample())
    assert not {"gold", "target", "partition", "expected"} & set(projected)
    assert "Evan Fox" not in json.dumps(projected["task_plan"])


def test_fault_outcome_cannot_be_assumed_from_target_label():
    case = sample()
    case.update(target="provider_uncertain")
    case["protocol"]["fault"] = "provider_uncertain"
    assert "FAULT_OUTCOME_MISMATCH" in codes(report(case))


def test_missing_partition_is_not_a_valid_split():
    case = sample()
    case.pop("partition")
    assert "PARTITION_INVALID" in codes(report(case))


def test_gold_support_must_be_nonempty_even_with_matching_outcome():
    case = sample()
    case["gold"]["facts"][0]["supports"] = []
    assert "GOLD_SUPPORT_MISSING" in codes(report(case))


def test_exposed_reference_cannot_be_relabelled_as_new_final():
    old = sample()
    case = deepcopy(old)
    case.update(id="EQ-F-RENAMED", partition="final")
    result = qualify_bundle({"cases": [case]}, references=[old])
    assert "MECHANICAL_PARTITION_LEAK" in codes(result)
    assert result["reference_audit"][0]["status"] == "COMPARED"


def test_invalid_shape_reports_rejection_without_constructing_runner():
    case = sample()
    case["input"]["documents"] = "not a document array"
    assert "CASE_SHAPE_INVALID" in codes(report(case))


def test_builder_must_run_authoritative_preflight_before_writing(tmp_path, monkeypatch):
    import scripts.build_qualification_validation as builder
    case = sample()
    case["input"]["task_plan"]["goal"] = "mismatched goal"
    monkeypatch.setattr(builder, "_materialize", lambda: ({"cases": [case]}, {}))
    monkeypatch.setattr(builder, "validate", lambda *a: {"status": "PASS"})
    monkeypatch.setattr("sys.argv", ["builder", "--output-dir", str(tmp_path / "not-written")])
    assert builder.main() == 1
    assert not (tmp_path / "not-written").exists()


@pytest.mark.parametrize("field,value,expected", [("task_plan", {"subgoals": "invalid"}, "PLAN_ADMISSION_INVALID"), ("budget", {"max_steps": "12"}, "BUDGET_INVALID")])
def test_malformed_contracts_are_actionable_rejections(field, value, expected):
    case = sample()
    case["input"][field] = value
    assert expected in codes(report(case))


def test_runtime_rejects_oracle_input_before_materializing_or_constructing_model(tmp_path):
    from scripts.qualified_runtime import execute_public
    public = public_projection(sample())
    public["gold"] = "__gold_sentinel__"
    with pytest.raises(ValueError, match="forbidden keys"):
        execute_public(public, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_no_final_execution_command_is_exposed():
    import runpy
    with pytest.raises(SystemExit, match="Final execution is unavailable"):
        runpy.run_path("scripts/qualified_runtime.py", run_name="__main__")


def test_same_file_conflict_cannot_claim_observable_conflict():
    case = sample()
    case["target"] = "conflict"
    case["gold"]["outcome"] = "conflict"
    case["input"]["documents"][0]["content"] += "Ada Reed reports directly to Mara West.\n"
    result = report(case)
    assert result["status"] == "REJECTED"
    assert "CONFLICT_INDEPENDENT_SOURCES_REQUIRED" in codes(result)


@pytest.mark.parametrize("target,fault,limit,code", [
    ("source_freshness", "source_change", 2, "TARGET_BUDGET_UNREACHABLE"),
    ("invalid_tool", "invalid_tool", 1, "TARGET_BUDGET_UNREACHABLE"),
    ("budget_exhausted", "budget_exhausted", 12, "BUDGET_FAULT_NOT_REACHABLE"),
    ("cold_resume", "cold_resume", 12, "TARGET_SOURCE_NOT_REACHABLE"),
])
def test_fault_labels_do_not_bypass_boundary_reachability(target, fault, limit, code):
    case = sample()
    case.update(target=target)
    case["protocol"]["fault"] = fault
    case["input"]["budget"]["max_provider_requests"] = limit
    if target == "cold_resume":
        case["input"]["documents"][0]["content"] = "Ada Reed is the accountable owner of Beacon Lane.\n"
    assert code in codes(report(case))
