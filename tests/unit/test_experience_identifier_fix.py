"""Regression tests for the full Experience source-identifier boundary."""

import pytest

from linkloom.experience.policy import (
    ExperiencePolicyViolation,
    validate_gold_safe_payload,
    validate_source_identifiers,
)


@pytest.mark.parametrize(
    ("run_id", "refs"),
    [
        ("run_authority_a1", ("run_authority_a1:ev_real",)),
        ("run_p4_hash", ("run_p4_hash:ev_p1_0016", "run_p4_hash:ev_second")),
    ],
)
def test_opaque_run_and_qualified_evidence_identifiers_are_allowed(
    run_id: str, refs: tuple[str, ...]
) -> None:
    validate_source_identifiers(run_id, refs)


@pytest.mark.parametrize(
    "run_id",
    [
        "run_mps-001",
        "run_authority_a1_mps-001",
        "run_safe:/private/gold.json",
        "run_authority_a1_expected_answer",
        "run_authority_a1_gold_answer",
    ],
)
def test_source_identifier_grammar_rejects_embedded_benchmark_and_sensitive_hints(
    run_id: str,
) -> None:
    with pytest.raises(ExperiencePolicyViolation):
        validate_source_identifiers(run_id, ())


@pytest.mark.parametrize(
    "ref",
    [
        "run_authority_a1:ev_mps-001",
        "run_authority_a1:ev_safe:/private/gold.json",
        "run_authority_a1:ev_expected_answer",
        "run_authority_a1:ev_gold_answer",
        "run_authority_a1:/private/gold.json",
    ],
)
def test_qualified_evidence_grammar_rejects_embedded_benchmark_paths_and_hints(
    ref: str,
) -> None:
    with pytest.raises(ExperiencePolicyViolation):
        validate_source_identifiers("run_authority_a1", (ref,))


@pytest.mark.parametrize(
    "value",
    [
        "run_mps-001",
        "run_safe:/private/gold.json",
        "copied expected answer: target",
        "Gold: /private/gold.json",
        "prefix /private/note.md",
        "artifact=C:\\private\\gold.json",
    ],
)
def test_gold_safe_payload_rejects_embedded_identifier_and_sensitive_hints(
    value: str,
) -> None:
    with pytest.raises(ExperiencePolicyViolation):
        validate_gold_safe_payload({"value": value})
