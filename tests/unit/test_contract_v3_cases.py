"""Contract tests for the frozen V3 offline runner.

These tests exercise the runner boundary itself.  They intentionally load only
the development fixture; the frozen Final fixture remains gated and unclaimed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.contract_v3_cases import (
    FinalAcceptanceRequired,
    FinalAlreadyClaimed,
    _claim_final,
    load_partition,
    materialize_case,
    model_input_projection,
    run_partition,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _dev_case(case_id: str = "RAC3-D02") -> dict:
    partition = load_partition(REPO_ROOT, partition="dev")
    return next(case for case in partition.payload["cases"] if case["id"] == case_id)


def test_dev_partition_is_frozen_and_does_not_open_final() -> None:
    partition = load_partition(REPO_ROOT, partition="dev")

    assert partition.partition == "dev"
    assert partition.fixture_path.name == "dev.json"
    assert partition.final_claim_path is None
    assert len(partition.payload["cases"]) == 10


def test_final_requires_acceptance_before_loading_or_claiming() -> None:
    with pytest.raises(FinalAcceptanceRequired):
        load_partition(REPO_ROOT, partition="final")


def test_final_claim_is_exclusive(tmp_path: Path) -> None:
    claim = _claim_final(tmp_path, freeze_sha256="freeze-sha")

    assert claim.is_file()
    assert json.loads(claim.read_text(encoding="utf-8"))["freeze_sha256"] == "freeze-sha"
    with pytest.raises(FinalAlreadyClaimed):
        _claim_final(tmp_path, freeze_sha256="freeze-sha")


def test_projection_excludes_gold_and_logical_source_identity() -> None:
    case = _dev_case()
    projected = model_input_projection(case)
    serialized = json.dumps(projected, ensure_ascii=False)

    assert "gold" not in projected
    assert "source" not in projected
    assert "synthetic-index-rac3-d02" not in serialized
    assert "idx-rac3-d02" not in serialized
    assert projected["documents"] == case["input"]["documents"]


def test_materialization_uses_scanner_owned_identity(tmp_path: Path) -> None:
    case = _dev_case()
    source = materialize_case(case, tmp_path)
    context = source.runtime_context()

    assert source.index_path.is_file()
    assert len(source.index_sha256) == 64
    assert source.index_sha256 != "synthetic-index-rac3-d02"
    assert context["index_sha256"] == source.index_sha256
    assert context["vault_root_fingerprint"] == source.vault_root_fingerprint
    assert context["index_path"] == source.index_path.name
    assert context["fixture_id"] is None


def test_dev_run_writes_auditable_per_case_report(tmp_path: Path) -> None:
    report = run_partition(
        repo_root=REPO_ROOT,
        partition="dev",
        output=tmp_path / "v3-dev-run",
    )

    assert report["partition"] == "dev"
    assert report["fake_model_only"] is True
    assert report["real_provider_requests"] == 0
    assert report["counts"]["total"] == 10
    assert len(report["cases"]) == 10
    for case in report["cases"]:
        assert set(case) >= {
            "id",
            "model_kind",
            "results",
            "error",
            "review",
            "verified_reads",
            "budget",
            "sha",
            "score",
        }
        assert case["model_kind"].startswith("FakeModelAdapter/")
        assert "fixture_input_sha256" in case["sha"]
        assert "gold_read_after_execution" in case["score"]
