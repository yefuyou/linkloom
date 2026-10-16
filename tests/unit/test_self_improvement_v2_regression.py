from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.self_improvement.v2_regression import (
    BASE_COMMIT,
    CORE_TEST_PATHS,
    HISTORICAL_TEST_PATHS,
    STAGE_CORE,
    STAGE_DIFFERENTIAL,
    STAGE_FULL,
    STAGE_HISTORICAL,
    ProcessResult,
    classify_pair,
    create_immutable_baseline_archive,
    environment_fingerprint,
    sanitized_environment,
    stage_specs,
    verify_immutable_baseline_archive,
)


def _process(mode: str, report: dict, *, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(
        mode=mode,
        command=["pytest"],
        environment={"values": {}, "sha256": ""},
        log_path=f"{mode}.log",
        plugin_report_path=f"{mode}.plugin.json",
        junit_path=f"{mode}.xml",
        returncode=1 if timed_out else 0,
        timed_out=timed_out,
        duration_seconds=0.01,
        report=report,
    )


def test_paired_attribution_uses_observed_node_outcomes() -> None:
    baseline = _process(
        "baseline",
        {
            "collected": ["tests/unit/test_a.py::test_ok", "tests/unit/test_b.py::test_old"],
            "results": [
                {"nodeid": "tests/unit/test_a.py::test_ok", "status": "passed"},
                {
                    "nodeid": "tests/unit/test_b.py::test_old",
                    "status": "failed",
                    "longrepr": "assert old contract",
                },
            ],
            "collection_errors": [],
        },
    )
    candidate = _process(
        "candidate",
        {
            "collected": ["tests/unit/test_a.py::test_ok", "tests/unit/test_b.py::test_old"],
            "results": [
                {
                    "nodeid": "tests/unit/test_a.py::test_ok",
                    "status": "failed",
                    "longrepr": "assert candidate contract",
                },
                {
                    "nodeid": "tests/unit/test_b.py::test_old",
                    "status": "failed",
                    "longrepr": "assert old contract",
                },
            ],
            "collection_errors": [],
        },
    )
    pairs = {row["nodeid"]: row for row in classify_pair(baseline, candidate)}
    assert pairs["tests/unit/test_a.py::test_ok"]["classification"] == "candidate_caused"
    assert pairs["tests/unit/test_b.py::test_old"]["classification"] == "baseline_existing"


def test_pairing_retains_collection_and_not_executed_evidence() -> None:
    baseline = _process(
        "baseline",
        {
            "collected": ["tests/unit/test_missing.py::test_x", "tests/unit/test_skip.py::test_y"],
            "results": [],
            "collection_errors": [
                {"nodeid": "tests/unit/test_missing.py", "message": "No module named scripts.run_old"}
            ],
        },
    )
    candidate = _process(
        "candidate",
        {
            "collected": ["tests/unit/test_skip.py::test_y"],
            "results": [],
            "collection_errors": [],
        },
    )
    pairs = {row["nodeid"]: row for row in classify_pair(baseline, candidate)}
    assert pairs["tests/unit/test_missing.py"]["classification"] == "collection"
    assert pairs["tests/unit/test_missing.py::test_x"]["classification"] == "not_executed"
    assert pairs["tests/unit/test_skip.py::test_y"]["classification"] == "not_executed"


def test_environment_failures_are_not_called_baseline_failures() -> None:
    baseline = _process(
        "baseline",
        {
            "collected": ["tests/unit/test_env.py::test_x"],
            "results": [
                {
                    "nodeid": "tests/unit/test_env.py::test_x",
                    "status": "error",
                    "longrepr": "ModuleNotFoundError: No module named old_dependency",
                }
            ],
            "collection_errors": [],
        },
    )
    candidate = _process(
        "candidate",
        {
            "collected": ["tests/unit/test_env.py::test_x"],
            "results": [
                {
                    "nodeid": "tests/unit/test_env.py::test_x",
                    "status": "error",
                    "longrepr": "ModuleNotFoundError: No module named old_dependency",
                }
            ],
            "collection_errors": [],
        },
    )
    pair = classify_pair(baseline, candidate)[0]
    assert pair["classification"] == "environment"


def test_offline_environment_removes_credentials_and_live_flags() -> None:
    env = sanitized_environment(
        Path("D:/baseline"),
        Path("D:/lab"),
        mode="baseline",
        candidate_config=None,
        base_environment={
            "GEMINI_API_KEY": "secret",
            "LINKLOOM_RUN_LIVE": "1",
            "LINKLOOM_LIVE_PROVIDER": "1",
            "SAFE_FLAG": "yes",
        },
    )
    assert "GEMINI_API_KEY" not in env
    assert "LINKLOOM_RUN_LIVE" not in env
    assert "LINKLOOM_LIVE_PROVIDER" not in env
    assert env["LINKLOOM_RETRIEVAL_MODE"] == "bm25"
    assert env["SAFE_FLAG"] == "yes"
    assert str(Path("D:/baseline/src")) in env["PYTHONPATH"]
    assert environment_fingerprint(env)["sha256"]


def test_stage_inventory_is_explicit_and_full_attempt_is_not_green() -> None:
    specs = stage_specs()
    assert set(specs) == {STAGE_CORE, STAGE_HISTORICAL, STAGE_FULL}
    assert set(CORE_TEST_PATHS).issubset(specs[STAGE_CORE].paths)
    assert set(HISTORICAL_TEST_PATHS).issubset(specs[STAGE_HISTORICAL].paths)
    assert specs[STAGE_FULL].collect_only is True
    assert STAGE_DIFFERENTIAL not in specs  # derived only from paired evidence


def test_archive_is_exact_and_digest_tampering_is_detected(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    archive = create_immutable_baseline_archive(
        project_root,
        base_commit=BASE_COMMIT,
        work_root=tmp_path / "b92",
    )
    manifest = verify_immutable_baseline_archive(archive)
    assert manifest["commit"] == BASE_COMMIT
    assert (archive.source_root / "src").is_dir()
    assert (archive.source_root / "tests").is_dir()
    assert archive.file_count > 10
    archive.archive_path.write_bytes(archive.archive_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="digest changed"):
        verify_immutable_baseline_archive(archive)


def test_archive_creation_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = Path(__file__).resolve().parents[2]
    first = create_immutable_baseline_archive(project_root, work_root=tmp_path / "b92")
    assert first.archive_root.exists()
    # The helper creates a fresh tokenized directory.  A caller can therefore
    # retain the first archive and rerun without cleanup or overwriting it.
    second = create_immutable_baseline_archive(project_root, work_root=tmp_path / "b92")
    assert first.archive_root != second.archive_root
    assert first.archive_root.exists() and second.archive_root.exists()


def test_extracted_source_mutation_is_detected_before_candidate_run(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    archive = create_immutable_baseline_archive(project_root,work_root=tmp_path/"b92")
    target = archive.source_root/"scripts/self_improvement/contracts.py"
    target.write_text(target.read_text(encoding="utf-8")+"\n# unexpected mutation\n",encoding="utf-8")
    with pytest.raises(ValueError,match="extracted source changed"):
        verify_immutable_baseline_archive(archive)
