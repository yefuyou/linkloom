"""Guidance wording follows the graph reason_code without changing graph rules."""
import pytest

from linkloom.agents.graph_result import project_meeting_result


def _payload(status, reason_code, steps=1):
    rows = [
        {"id": f"s{i}", "status": status, "reason_code": reason_code, "output_nodes": [],
         "edges": [], "candidates": [], "records": []}
        for i in range(steps)
    ]
    return {"status": status, "steps": rows, "outputs": [],
            "plan": {"steps": [{"id": row["id"], "operation": "inspect"} for row in rows]}}


@pytest.mark.parametrize("reason_code", ["CONFIRMED_COUNTER_EVIDENCE", "MULTIPLE_CONFIRMED_VALUES"])
def test_fact_conflict_asks_to_verify_formal_basis(reason_code):
    answer = project_meeting_result(_payload("CONFLICT", reason_code))[0]["answer"]
    assert "请核实正式依据" in answer
    assert "请明确对象" not in answer and "补充更精确的时间" not in answer


def test_time_precision_conflict_asks_for_time():
    answer = project_meeting_result(_payload("CONFLICT", "TIME_PRECISION_REQUIRES_SCOPE"))[0]["answer"]
    assert "请补充更精确的时间" in answer
    assert "请核实正式依据" not in answer


def test_unspecific_time_or_field_conflict_keeps_original_guidance():
    answer = project_meeting_result(_payload("CONFLICT", "TIME_OR_FIELD_CONFLICT"))[0]["answer"]
    assert answer.endswith("请明确对象或更细的时间。")


def test_entity_ambiguity_asks_to_specify_entity():
    answer = project_meeting_result(_payload("AMBIGUOUS", "ROOT_NEEDS_CLARIFICATION"))[0]["answer"]
    assert "请明确所指的具体对象" in answer
    assert "请核实正式依据" not in answer
