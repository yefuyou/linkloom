from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.memoryagentbench.g31_heldout import (
    HELD_OUT_METHODS,
    HeldOutPreflightError,
    build_method_case_plan,
    build_subset_manifest,
    canonical_sha256,
    estimate_static_cost,
    file_bundle_fingerprint,
    select_clean_contiguous_subset,
)


def _qa_ids(start: int, stop: int) -> list[str]:
    return [f"factconsolidation_sh_6k_no{index}" for index in range(start, stop)]


def test_heldout_selection_uses_next_contiguous_fifty_without_reuse() -> None:
    selected = select_clean_contiguous_subset(
        _qa_ids(0, 100),
        _qa_ids(0, 50),
        start_index=50,
        requested_count=50,
    )

    assert selected["status"] == "READY"
    assert selected["selected_qa_ids"] == _qa_ids(50, 100)
    assert selected["remaining_unseen_count_after_start"] == 50
    assert selected["previously_used_overlap"] == []


def test_heldout_selection_fails_closed_on_overlap_or_short_supply() -> None:
    overlap = select_clean_contiguous_subset(
        _qa_ids(0, 100),
        ["factconsolidation_sh_6k_no70"],
        start_index=50,
        requested_count=50,
    )
    short = select_clean_contiguous_subset(
        _qa_ids(0, 63),
        _qa_ids(0, 50),
        start_index=50,
        requested_count=50,
    )

    assert overlap["status"] == "NOT_READY"
    assert overlap["selected_qa_ids"] == _qa_ids(50, 100)
    assert overlap["previously_used_overlap"] == ["factconsolidation_sh_6k_no70"]
    assert short["status"] == "NOT_READY"
    assert short["available_count_after_start"] == 13
    assert short["selected_count"] == 13


def test_method_case_plan_is_deterministic_and_pairs_each_question() -> None:
    plan = build_method_case_plan(_qa_ids(50, 52))

    assert len(plan) == 4
    assert [row["sequence"] for row in plan] == [1, 2, 3, 4]
    assert [(row["qa_id"], row["method"]) for row in plan] == [
        ("factconsolidation_sh_6k_no50", HELD_OUT_METHODS[0]),
        ("factconsolidation_sh_6k_no50", HELD_OUT_METHODS[1]),
        ("factconsolidation_sh_6k_no51", HELD_OUT_METHODS[0]),
        ("factconsolidation_sh_6k_no51", HELD_OUT_METHODS[1]),
    ]


def test_subset_manifest_freezes_hashes_and_requires_all_fingerprints() -> None:
    qa_ids = _qa_ids(50, 52)
    hashes = {qa_id: canonical_sha256({"question": qa_id}) for qa_id in qa_ids}
    fingerprints = {
        key: f"fingerprint-{key}"
        for key in (
            "adapter_fingerprint",
            "temporal_memory_fingerprint",
            "retrieval_fingerprint",
            "scoring_fingerprint",
            "prompt_fingerprint",
            "gemini_config_fingerprint",
        )
    }
    manifest = build_subset_manifest(
        dataset_revision="revision",
        dataset_sha256="a" * 64,
        dataset_split="Conflict_Resolution",
        dataset_configuration="FactConsolidation single-hop / 6k",
        case_id="case",
        workspace_id="workspace",
        qa_ids=qa_ids,
        question_sha256_by_id=hashes,
        fingerprints=fingerprints,
        execution_plan=build_method_case_plan(qa_ids),
    )

    assert manifest["qa_ids"] == qa_ids
    assert manifest["gold_columns_read"] is False
    assert len(manifest["subset_hash"]) == 64
    assert manifest["subset_manifest_sha256"] == canonical_sha256(
        {key: value for key, value in manifest.items() if key != "subset_manifest_sha256"}
    )

    with pytest.raises(HeldOutPreflightError, match="fingerprints are incomplete"):
        build_subset_manifest(
            dataset_revision="revision",
            dataset_sha256="a" * 64,
            dataset_split="Conflict_Resolution",
            dataset_configuration="FactConsolidation single-hop / 6k",
            case_id="case",
            workspace_id="workspace",
            qa_ids=qa_ids,
            question_sha256_by_id=hashes,
            fingerprints={},
            execution_plan=build_method_case_plan(qa_ids),
        )


def test_cost_estimate_charges_count_and_generation_input_and_checks_caps() -> None:
    estimate = estimate_static_cost([1_000, 2_000], max_attempts_per_method_case=3)

    assert estimate["one_attempt_total_cost_upper_bound_usd"] == pytest.approx(0.00834)
    assert estimate["all_allowed_attempts_cost_upper_bound_usd"] == pytest.approx(0.02502)
    assert estimate["dynamic_cost_guard_required"] is False
    with pytest.raises(HeldOutPreflightError, match="exceeds the frozen per-request cap"):
        estimate_static_cost([10_001])


def test_file_bundle_fingerprint_is_stable_and_tracks_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "adapter.py"
    source.write_text("subject = 'A'\n", encoding="utf-8")
    first = file_bundle_fingerprint(tmp_path, ["adapter.py"])
    repeated = file_bundle_fingerprint(tmp_path, ["adapter.py"])

    source.write_text("subject = 'B'\n", encoding="utf-8")
    changed = file_bundle_fingerprint(tmp_path, ["adapter.py"])

    assert first == repeated
    assert first["sha256"] != changed["sha256"]
