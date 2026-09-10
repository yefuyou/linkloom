"""Offline runner for the synthetic Agent trajectory evaluation dataset."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from .capabilities import (
    assess_case_capabilities,
    capability_matrix_payload,
    partition_case_ids,
)
from .dataset import TrajectoryDataset
from .fixture import EXPOSED_FIXTURE_PATHS, TrajectoryFixture
from .grader import grade_case
from .harness import TrajectoryHarness
from .metrics import compute_trajectory_metrics
from .models import CaseResult, TrajectoryObservation
from .reporting import write_trajectory_artifacts


@dataclass(frozen=True)
class TrajectoryEvaluationRun:
    manifest: dict[str, Any]
    case_results: tuple[CaseResult, ...]
    metrics: dict[str, Any]
    failure_summary: dict[str, Any]
    capability_matrix: dict[str, Any]
    observations: dict[str, TrajectoryObservation]
    executed_case_ids: tuple[str, ...]
    denied_side_effect_executor_counts: dict[str, int]
    artifact_paths: tuple[Path, ...]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_hash(fixture: TrajectoryFixture) -> str:
    digest = hashlib.sha256()
    for note_ref in EXPOSED_FIXTURE_PATHS:
        digest.update(note_ref.encode("utf-8"))
        digest.update(b"\0")
        digest.update(fixture.resolve(note_ref).read_bytes())
    return digest.hexdigest()


def _repo_relative(path: Path, repo_root: Path, field_name: str) -> str:
    try:
        relative = path.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be inside the repository") from exc
    return relative.as_posix()


def _failure_summary(results: list[CaseResult]) -> dict[str, Any]:
    counts = Counter(result.status for result in results)
    missing: dict[str, list[str]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    for result in results:
        if result.status == "FAIL":
            failures.append(
                {
                    "case_id": result.case_id,
                    "category": result.category,
                    "failed_assertion": result.failed_assertion,
                    "expected": result.expected,
                    "actual": result.actual,
                    "primary_failure_attribution": result.primary_failure_attribution,
                }
            )
        if result.status == "NOT_IMPLEMENTED" and result.missing_capability:
            for capability_id in result.missing_capability.split(","):
                missing[capability_id].append(result.case_id)
    return {
        "schema_version": "trajectory-failure-summary/v1",
        "counts": {
            "PASS": counts["PASS"],
            "FAIL": counts["FAIL"],
            "NOT_IMPLEMENTED": counts["NOT_IMPLEMENTED"],
        },
        "failures": failures,
        "missing_capabilities": {
            capability_id: sorted(case_ids)
            for capability_id, case_ids in sorted(missing.items())
        },
    }


class TrajectoryEvaluationRunner:
    """Execute only matrix-supported cases against production tool contracts."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        cases_path: str | Path,
        output_dir: str | Path,
        fixture_root: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        raw_cases = Path(cases_path)
        self.cases_path = (
            raw_cases if raw_cases.is_absolute() else self.repo_root / raw_cases
        ).resolve()
        self.output_dir = Path(output_dir).resolve()
        expected_fixture = (
            self.repo_root / "tests" / "fixtures" / "trajectory_kb_v1"
        ).resolve()
        self.fixture_root = (
            expected_fixture
            if fixture_root is None
            else Path(fixture_root).resolve()
        )
        if self.fixture_root != expected_fixture:
            raise ValueError("fixture override must resolve to synthetic trajectory_kb_v1")
        _repo_relative(self.cases_path, self.repo_root, "cases path")
        _repo_relative(self.fixture_root, self.repo_root, "fixture path")

    def run(self, *, run_id: str | None = None) -> TrajectoryEvaluationRun:
        started_at = _utc_timestamp()
        dataset = TrajectoryDataset.from_path(self.cases_path)
        executable_ids, future_ids = partition_case_ids(dataset.cases)
        harness = TrajectoryHarness(self.repo_root, self.fixture_root)

        observations: dict[str, TrajectoryObservation] = {}
        results: list[CaseResult] = []
        for case in dataset.cases:
            support = assess_case_capabilities(case)
            if support.executable:
                observation = harness.execute_case(case)
                observations[case.case_id] = observation
                results.append(grade_case(case, observation))
            else:
                results.append(grade_case(case, None))

        metrics = compute_trajectory_metrics(dataset.cases, results)
        failure_summary = _failure_summary(results)
        matrix_payload = {
            "schema_version": "trajectory-capability-matrix/v1",
            "definitions": capability_matrix_payload(),
            "derived": {
                "executable_case_ids": sorted(executable_ids),
                "future_case_ids": sorted(future_ids),
                "executable_case_count": len(executable_ids),
                "future_case_count": len(future_ids),
            },
        }
        fixture = TrajectoryFixture(self.repo_root)
        manifest = {
            "schema_version": "trajectory-run-manifest/v1",
            "run_id": run_id or f"trajectory-{uuid4()}",
            "started_at": started_at,
            "finished_at": _utc_timestamp(),
            "status": "FAIL" if failure_summary["counts"]["FAIL"] else "PASS",
            "case_sha256": _sha256_file(self.cases_path),
            "fixture_sha256": _fixture_hash(fixture),
            "paths": {
                "cases": _repo_relative(self.cases_path, self.repo_root, "cases path"),
                "fixture": _repo_relative(
                    self.fixture_root, self.repo_root, "fixture path"
                ),
            },
            "nondeterministic_fields": ["run_id", "started_at", "finished_at"],
            "counts": {
                "total": len(dataset.cases),
                "executable": len(executable_ids),
                "future": len(future_ids),
            },
            "provider": "none",
            "judge": "NOT_EVALUATED",
            "safety": {
                "network_access": False,
                "gold_access": False,
                "real_vault_access": False,
                "provider_call": False,
                "judge_call": False,
            },
        }
        artifacts = write_trajectory_artifacts(
            self.output_dir,
            manifest=manifest,
            case_results=results,
            metrics=metrics,
            failure_summary=failure_summary,
            capability_matrix=matrix_payload,
        )
        return TrajectoryEvaluationRun(
            manifest=manifest,
            case_results=tuple(results),
            metrics=metrics,
            failure_summary=failure_summary,
            capability_matrix=matrix_payload,
            observations=observations,
            executed_case_ids=tuple(harness.executed_case_ids),
            denied_side_effect_executor_counts=dict(
                harness.denied_side_effect_executor_counts
            ),
            artifact_paths=artifacts,
        )


__all__ = ["TrajectoryEvaluationRun", "TrajectoryEvaluationRunner"]
