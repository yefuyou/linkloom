"""Checks for benchmark ground truth and measurement semantics."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from bench_scale_data import (fixture_digest, retrieval_fixture, routing_fixture,
                              temporal_cases, temporal_rows)
from bench_scale import benchmark_routing, explain_calls, percentiles


def test_retrieval_corpus_is_nested_and_answer_keys_are_unambiguous():
    small, questions = retrieval_fixture(1_000)
    again, repeated = retrieval_fixture(1_000)
    large, large_questions = retrieval_fixture(10_000)
    assert fixture_digest(small, questions) == fixture_digest(again, repeated)
    assert small == large[:1_000]
    assert questions == large_questions
    assert len(small) == 1_000
    by_id = {row["resource_id"]: row for row in small}
    for query in questions:
        assert len(query["relevant_ids"]) == 1
        answer = by_id[query["relevant_ids"][0]]
        assert answer["metadata"]["approved"]
        assert answer["metadata"]["value"] == query["expected_value"]
        assert answer["metadata"]["kind"] == ("superseded" if query["kind"] == "historical" else "current")
        assert query["expected_value"] not in query["query"]
    group = small[:10]
    assert sum(row["metadata"]["kind"] == "near_duplicate" for row in group) == 6
    assert sum(row["metadata"]["kind"] == "same_name_other_project" for row in group) == 2


def test_temporal_half_open_boundaries_have_one_gold_record():
    from datetime import datetime
    records = list(temporal_rows(100))
    for case in temporal_cases(count=100, query_count=60):
        active = [row[0] for row in records
                  if datetime.fromisoformat(row[10]) <= case["as_of"]
                  and (row[11] is None or datetime.fromisoformat(row[11]) > case["as_of"])]
        assert active == ([case["expected_id"]] if case["expected_id"] else [])


def test_routing_includes_negative_and_ambiguous_questions_without_auto_binding():
    slots, cases = routing_fixture(4)
    assert len(slots) == 4
    assert {case["kind"] for case in cases} == {
        "exact_project", "old_value", "same_name",
        "shared_current_value", "cross_project", "unknown_project"}
    assert any(not case["eligible"] for case in cases)
    assert all(case["must_abstain"] for case in cases if len(case["eligible"]) != 1)
    result = benchmark_routing(4)
    assert result["wrong_bindings"] == 0
    assert result["unique_target_recall_at_5"] == 1
    assert result["by_kind"]["unknown_project"]["recall_at_5"] is None


def test_explain_captures_the_executed_sql_and_reports_real_index_use():
    import sqlite3
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE sample (id INT, value TEXT); CREATE INDEX by_id ON sample(id);")
    plans = explain_calls(connection, lambda: connection.execute("SELECT value FROM sample WHERE id=?", (42,)).fetchall())
    assert len(plans) == 1
    assert "42" in plans[0]["sql"]
    assert any("USING INDEX by_id" in detail for detail in plans[0]["plan"])
    connection.close()


def test_percentiles_use_linear_interpolation():
    assert percentiles([1, 2, 3, 4]) == {"p50_ms": 2.5, "p95_ms": 3.8499999999999996, "samples": 4}
