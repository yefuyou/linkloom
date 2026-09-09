from __future__ import annotations

from pathlib import Path

import pytest

from linkloom.evaluation.trajectory.fixture import (
    EXPOSED_FIXTURE_PATHS,
    TrajectoryFixture,
    stable_heading_slug,
    validate_evidence_reference,
    validate_fixture_reference,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_fixture_exposes_only_six_named_synthetic_notes() -> None:
    fixture = TrajectoryFixture(REPO_ROOT)

    assert fixture.list_paths() == list(EXPOSED_FIXTURE_PATHS)
    assert fixture.list_paths() == [
        "n/agent-loop.md",
        "n/checkpoint.md",
        "n/empty.md",
        "n/memory-policy.md",
        "n/missing.md",
        "n/retrieval.md",
    ]
    assert fixture.root == (
        REPO_ROOT / "tests" / "fixtures" / "trajectory_kb_v1"
    ).resolve()
    for relative_path in fixture.list_paths():
        assert isinstance(fixture.read_text(relative_path), str)


@pytest.mark.parametrize(
    "path",
    [
        "../trajectory_kb_v1/n/retrieval.md",
        "n/../../relation_gold.yaml",
        "tests/eval/relation_gold.yaml",
        "tests/fixtures/relation_vault/note.md",
        "/etc/passwd",
        r"C:\\Users\\person\\vault\\note.md",
        "n/private.md",
        "https://example.com/note.md",
    ],
)
def test_fixture_rejects_traversal_absolute_gold_external_and_unexposed_paths(
    path: str,
) -> None:
    with pytest.raises(ValueError, match="fixture"):
        validate_fixture_reference(path)


def test_fixture_has_no_network_or_user_vault_surface() -> None:
    fixture = TrajectoryFixture(REPO_ROOT)

    assert not hasattr(fixture, "fetch")
    assert not hasattr(fixture, "request")
    assert not hasattr(fixture, "open_vault")
    assert "relation_gold" not in " ".join(fixture.list_paths()).casefold()


def test_evidence_reference_accepts_only_real_same_file_heading_slug() -> None:
    assert stable_heading_slug("Retrieval contract") == "retrieval-contract"
    assert (
        validate_evidence_reference("n/retrieval.md#retrieval-contract")
        == "n/retrieval.md#retrieval-contract"
    )
    assert (
        validate_evidence_reference("n/checkpoint.md#checkpoint-boundary")
        == "n/checkpoint.md#checkpoint-boundary"
    )


@pytest.mark.parametrize(
    "reference",
    [
        "n/retrieval.md",
        "n/retrieval.md#",
        "n/retrieval.md#unknown-heading",
        "n/retrieval.md#checkpoint-boundary",
        "n/checkpoint.md#retrieval-contract",
        "n/../retrieval.md#retrieval-contract",
        "n/empty.md#empty",
    ],
)
def test_evidence_reference_rejects_bare_empty_unknown_cross_file_and_headingless(
    reference: str,
) -> None:
    with pytest.raises(ValueError, match="evidence|anchor|fixture"):
        validate_evidence_reference(reference)
