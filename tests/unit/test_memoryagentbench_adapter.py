from __future__ import annotations

from pathlib import Path

import pytest

pyarrow = pytest.importorskip("pyarrow")
import pyarrow.parquet as pq

from benchmarks.memoryagentbench.adapter import (
    BenchmarkQuestion,
    FactConsolidationCase,
    TARGET_SOURCE,
    load_factconsolidation_case,
    parse_ordered_facts,
)
from benchmarks.memoryagentbench.memory import (
    build_flat_index,
    build_temporal_memory,
    search_temporal_memory,
)
from linkloom.decision_memory.tool import DecisionMemorySearchTool


def _write_dataset(
    path: Path,
    *,
    target_context: str | None = None,
    target_source: str = TARGET_SOURCE,
) -> None:
    table = pyarrow.table(
        {
            "context": [
                "Header\n0. irrelevant fact.",
                target_context
                or (
                    "Here is a list of facts:\n0. The chairperson of Example Org is Person A.\n"
                    "1. The chairperson of Example Org is Person B."
                ),
            ],
            "questions": [["ignored question"], [f"question {index}" for index in range(12)]],
            "answers": [[["ignored gold"]], [[f"gold {index}"] for index in range(12)]],
            "metadata": [
                {"qa_pair_ids": ["other_no0"], "source": "other"},
                {
                    "qa_pair_ids": [f"{target_source}_no{index}" for index in range(12)],
                    "source": target_source,
                },
            ],
        }
    )
    pq.write_table(table, path)


def test_case_loader_reads_only_input_columns_and_freezes_first_ten_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "memoryagentbench.parquet"
    _write_dataset(dataset_path)
    read_columns: list[list[str]] = []
    actual_read_table = pq.read_table

    def read_table(path: object, *, columns: list[str]) -> object:
        read_columns.append(columns)
        return actual_read_table(path, columns=columns)

    monkeypatch.setattr(pq, "read_table", read_table)

    case = load_factconsolidation_case(dataset_path, question_limit=10)

    assert read_columns == [["context", "questions", "metadata.qa_pair_ids", "metadata.source"]]
    assert case.input_projection_columns == (
        "context",
        "questions",
        "metadata.qa_pair_ids",
        "metadata.source",
    )
    assert case.source == TARGET_SOURCE
    assert case.case_id.startswith(f"{TARGET_SOURCE}:")
    assert case.workspace_id.endswith(case.case_id.split(":", 1)[1])
    assert [question.question_id for question in case.questions] == [
        f"factconsolidation_sh_6k_no{index}" for index in range(10)
    ]
    assert [question.question for question in case.questions] == [
        f"question {index}" for index in range(10)
    ]
    assert not hasattr(case, "answers")
    assert "gold" not in repr(case).casefold()


def test_case_loader_can_select_an_offset_without_reading_gold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "memoryagentbench-offset.parquet"
    _write_dataset(dataset_path)
    read_columns: list[list[str]] = []
    actual_read_table = pq.read_table

    def read_table(path: object, *, columns: list[str]) -> object:
        read_columns.append(columns)
        return actual_read_table(path, columns=columns)

    monkeypatch.setattr(pq, "read_table", read_table)

    case = load_factconsolidation_case(
        dataset_path,
        question_offset=3,
        question_limit=4,
    )

    assert [question.question_id for question in case.questions] == [
        f"factconsolidation_sh_6k_no{index}" for index in range(3, 7)
    ]
    assert read_columns == [["context", "questions", "metadata.qa_pair_ids", "metadata.source"]]
    assert case.input_projection_columns == (
        "context",
        "questions",
        "metadata.qa_pair_ids",
        "metadata.source",
    )
    assert not hasattr(case, "answers")


def test_case_loader_can_select_a_frozen_source_without_reading_gold(tmp_path: Path) -> None:
    source = "factconsolidation_mh_6k"
    dataset_path = tmp_path / "memoryagentbench-mh.parquet"
    _write_dataset(dataset_path, target_source=source)

    case = load_factconsolidation_case(dataset_path, source=source, question_limit=12)

    assert case.source == source
    assert case.case_id.startswith(f"{source}:")
    assert len(case.questions) == 12
    assert case.questions[-1].question_id == f"{source}_no11"
    assert not hasattr(case, "answers")


def test_distinct_source_contexts_receive_distinct_case_workspaces(tmp_path: Path) -> None:
    first_path = tmp_path / "first.parquet"
    second_path = tmp_path / "second.parquet"
    _write_dataset(
        first_path,
        target_context="0. The chairperson of Example Org is Person A.",
    )
    _write_dataset(
        second_path,
        target_context="0. The chairperson of Example Org is Person B.",
    )

    first = load_factconsolidation_case(first_path)
    second = load_factconsolidation_case(second_path)

    assert first.case_id != second.case_id
    assert first.workspace_id != second.workspace_id


def test_fact_parser_preserves_order_and_extracts_generic_relation_updates() -> None:
    facts = parse_ordered_facts(
        "Here is a list of facts:\n"
        "0. The chairperson of Example Org is Person A.\n"
        "1. The chairperson of Example Org is Person B."
    )

    assert [fact.ordinal for fact in facts] == [0, 1]
    assert [fact.fact_id for fact in facts] == ["fact:0", "fact:1"]
    assert facts[0].subject_key == facts[1].subject_key
    assert (facts[0].value, facts[1].value) == ("Person A", "Person B")


def test_fact_parser_handles_where_relation_without_entity_specific_rules() -> None:
    fact = parse_ordered_facts(
        "0. The university where Joan Didion was educated is University of Example."
    )[0]

    assert fact.subject_key == "university where joan didion was educated"
    assert fact.value == "University of Example"


@pytest.mark.parametrize(
    ("statement", "expected_subject", "expected_predicate", "expected_value"),
    [
        (
            "Imelda Marcos is affiliated with the religion of atheism.",
            "Imelda Marcos",
            "affiliated with the religion of",
            "atheism",
        ),
        (
            "Moshe Kahlon works in the field of politician.",
            "Moshe Kahlon",
            "works in the field of",
            "politician",
        ),
        (
            "Pedro Pierluisi worked in the city of Washington, D.C..",
            "Pedro Pierluisi",
            "worked in the city of",
            "Washington, D.C.",
        ),
        (
            "The company that produced Il-2 Shturmovik is Ilyushin.",
            "Il-2 Shturmovik",
            "produced by",
            "Ilyushin",
        ),
        (
            "Hines Ward plays the position of wide receiver.",
            "Hines Ward",
            "plays the position of",
            "wide receiver",
        ),
        (
            "Kokai speaks the language of Japanese.",
            "Kokai",
            "speaks the language of",
            "Japanese",
        ),
        (
            "Olga of Kiev died in the city of Kyiv.",
            "Olga of Kiev",
            "died in the city of",
            "Kyiv",
        ),
    ],
)
def test_fact_parser_extracts_lowercase_values_and_general_relational_constructions(
    statement: str,
    expected_subject: str,
    expected_predicate: str,
    expected_value: str,
) -> None:
    fact = parse_ordered_facts(f"0. {statement}")[0]

    assert fact.subject == expected_subject
    assert fact.predicate == expected_predicate
    assert fact.value == expected_value
    assert fact.subject_key is not None


def test_fronted_relation_parser_uses_last_subject_boundary_without_splitting_object_of() -> None:
    fact = parse_ordered_facts(
        "0. The name of the current head of state in United Kingdom is Elizabeth II."
    )[0]

    assert fact.subject == "United Kingdom"
    assert fact.value == "Elizabeth II"
    assert fact.subject_key is not None
    assert fact.subject_key.endswith("united kingdom")


def test_fact_parser_preserves_internal_of_in_relation_value() -> None:
    fact = parse_ordered_facts(
        "0. Sable is a citizen of United States of America."
    )[0]

    assert fact.subject == "Sable"
    assert fact.predicate == "a citizen of"
    assert fact.value == "United States of America"
    assert fact.statement == "Sable is a citizen of United States of America."


def test_unmapped_multi_value_possessive_fact_retains_raw_predicate_without_temporal_key() -> None:
    fact = parse_ordered_facts(
        "0. Walter Chrysler's child is Walter Percy Chrysler Jr.."
    )[0]

    assert fact.subject_key is None
    assert fact.value is None
    assert fact.raw_predicate == "child"
    assert "Walter Chrysler's child" in fact.statement


def test_predicate_normalization_matches_derived_noun_query_to_stored_relation() -> None:
    context = (
        "Here is a list of facts:\n"
        "0. Sable is a citizen of United States of America.\n"
        "1. Sable is a citizen of Czech Republic."
    )
    case = FactConsolidationCase(
        source=TARGET_SOURCE,
        case_id="synthetic:predicate-normalization",
        workspace_id="memoryagentbench:synthetic:predicate-normalization",
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=(
            BenchmarkQuestion("q0", "What is Sable's country of citizenship?"),
        ),
    )
    memory = build_temporal_memory(case)
    try:
        result = search_temporal_memory(memory, case.questions[0].question)

        assert len(result.values) == 1
        assert result.values[0]["value"] == "Czech Republic"
        assert result.values[0]["source_evidence_refs"] == ["fact:1"]
    finally:
        memory.store.close()


@pytest.mark.parametrize(
    ("statement", "expected_subject", "expected_predicate", "expected_value"),
    [
        (
            "Meredith Grey was performed by Ellen Pompeo.",
            "Meredith Grey",
            "performed by",
            "Ellen Pompeo",
        ),
        (
            "Kingdom of Ireland is affiliated with the religion of Catholic Church.",
            "Kingdom of Ireland",
            "affiliated with the religion of",
            "Catholic Church",
        ),
        (
            "Windows Phone was developed by Microsoft.",
            "Windows Phone",
            "developed by",
            "Microsoft",
        ),
        (
            "Christianity was founded in the city of Jerusalem.",
            "Christianity",
            "founded in the city of",
            "Jerusalem",
        ),
        (
            "The treaty was ratified by Council B.",
            "The treaty",
            "ratified by",
            "Council B",
        ),
    ],
)
def test_fact_parser_extracts_general_subject_predicate_object_and_value(
    statement: str,
    expected_subject: str,
    expected_predicate: str,
    expected_value: str,
) -> None:
    fact = parse_ordered_facts(f"0. {statement}")[0]

    assert fact.subject == expected_subject
    assert fact.predicate == expected_predicate
    assert fact.value == expected_value
    assert fact.subject_key == f"{expected_subject} {expected_predicate}".casefold()
    assert fact.fact_id == "fact:0"
    assert fact.extraction_rule == "generic_copula_prepositional_object"


def test_general_relation_update_preserves_order_supersession_and_provenance() -> None:
    context = (
        "Here is a list of facts:\n"
        "0. The treaty was ratified by Council A.\n"
        "1. The treaty was ratified by Council B."
    )
    case = FactConsolidationCase(
        source=TARGET_SOURCE,
        case_id="synthetic:general-relation-update",
        workspace_id="memoryagentbench:synthetic:general-relation-update",
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=(BenchmarkQuestion("q0", "Who ratified the treaty?"),),
    )
    memory = build_temporal_memory(case)
    try:
        first, second = case.facts
        assert (first.subject, first.predicate) == (second.subject, second.predicate)
        assert first.subject_key == second.subject_key

        current = memory.store.get_current(case.workspace_id, second.subject_key or "")
        history = memory.store.get_history(case.workspace_id, second.subject_key or "")

        assert current is not None
        assert current.value == "Council B"
        assert current.source_evidence_refs == ("fact:1",)
        assert [item.status.value for item in history] == ["SUPERSEDED", "CURRENT"]
        assert [item.source_evidence_refs for item in history] == [("fact:0",), ("fact:1",)]
        assert [item.ordinal for item in case.facts] == [0, 1]
    finally:
        memory.store.close()


@pytest.mark.parametrize(
    "context",
    [
        "0. first fact\n2. skipped ordinal",
        "1. context starts after zero",
        "0. duplicate\n0. duplicate",
    ],
)
def test_fact_parser_rejects_missing_or_ambiguous_ordinals(context: str) -> None:
    with pytest.raises(ValueError):
        parse_ordered_facts(context)


def test_temporal_adapter_supersedes_old_fact_and_keeps_source_provenance() -> None:
    context = (
        "Here is a list of facts:\n"
        "0. The chairperson of Example Org is Person A.\n"
        "1. The chairperson of Example Org is Person B."
    )
    case = FactConsolidationCase(
        source=TARGET_SOURCE,
        case_id="synthetic:test-case",
        workspace_id="memoryagentbench:synthetic:test-case",
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=(
            BenchmarkQuestion("q0", "Who is the chairperson of Example Org?"),
        ),
    )
    flat_index = build_flat_index(case)
    memory = build_temporal_memory(case)
    try:
        flat_hits = flat_index.search(
            case.questions[0].question,
            workspace_id=case.workspace_id,
            top_k=5,
        )
        temporal = search_temporal_memory(memory, case.questions[0].question)

        assert {hit.resource_id for hit in flat_hits} == {"fact:0", "fact:1"}
        assert len(temporal.selected_subject_keys) == 1
        assert len(temporal.values) == 1
        assert temporal.values[0]["value"] == "Person B"
        assert temporal.values[0]["status"] == "CURRENT"
        assert temporal.values[0]["source_evidence_refs"] == ["fact:1"]

        history = memory.store.get_history(
            case.workspace_id,
            temporal.selected_subject_keys[0],
        )
        assert [item.status.value for item in history] == ["SUPERSEDED", "CURRENT"]
        assert history[-1].supersedes_id == history[0].decision_id
        assert memory.candidate_lifecycle[0].candidate_state == "ACTIVE"
        assert memory.candidate_lifecycle[0].decision_state == "SUPERSEDED"
        assert memory.candidate_lifecycle[1].candidate_state == "ACTIVE"
        assert memory.candidate_lifecycle[1].decision_state == "ACTIVE"
        source = flat_index.get_document(case.workspace_id, "fact:1")
        assert source is not None
        assert source.evidence_id == temporal.values[0]["source_evidence_refs"][0]
        assert source.content == case.facts[1].statement

        memory_tool = DecisionMemorySearchTool(
            memory.store,
            authorized_workspace_id=case.workspace_id,
        )
        with pytest.raises(PermissionError):
            memory_tool.search(
                workspace="memoryagentbench:another-case",
                query=temporal.selected_subject_keys[0],
                as_of=None,
                limit=5,
            )
        assert memory.store.search(
            "memoryagentbench:another-case",
            temporal.selected_subject_keys[0],
        ) == ()
    finally:
        memory.store.close()


def test_memory_query_abstains_on_a_single_common_word_match() -> None:
    context = (
        "Here is a list of facts:\n"
        "0. The chairperson of Harvard University is Person A."
    )
    case = FactConsolidationCase(
        source=TARGET_SOURCE,
        case_id="synthetic:abstention-case",
        workspace_id="memoryagentbench:synthetic:abstention-case",
        context=context,
        facts=tuple(parse_ordered_facts(context)),
        questions=(BenchmarkQuestion("q0", "Which university educated Joan Didion?"),),
    )
    memory = build_temporal_memory(case)
    try:
        result = search_temporal_memory(memory, case.questions[0].question)
        assert result.selected_subject_keys == ()
        assert result.values == ()
    finally:
        memory.store.close()
