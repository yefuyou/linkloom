"""Independent contracts and fixtures for Agent trajectory evaluation."""

from .capabilities import (
    CAPABILITY_MATRIX,
    FUTURE,
    SUPPORTED,
    CapabilityDefinition,
    CaseCapabilitySupport,
    assess_case_capabilities,
    capability_matrix_payload,
    partition_case_ids,
)
from .dataset import TrajectoryDataset, load_cases_jsonl
from .fixture import (
    EXPOSED_FIXTURE_PATHS,
    TrajectoryFixture,
    stable_heading_slug,
    validate_evidence_reference,
    validate_fixture_reference,
)
from .grader import DETERMINISTIC_ASSERTION_HANDLERS, grade_case
from .harness import TrajectoryHarness
from .metrics import REQUIRED_METRIC_NAMES, compute_trajectory_metrics
from .models import (
    CASE_SCHEMA_VERSION,
    OBSERVATION_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    TRAJECTORY_CATEGORIES,
    CaseResult,
    TrajectoryCase,
    TrajectoryObservation,
    canonicalize,
)
from .reporting import ARTIFACT_FILENAMES, write_trajectory_artifacts
from .runner import TrajectoryEvaluationRun, TrajectoryEvaluationRunner

__all__ = [
    "CASE_SCHEMA_VERSION",
    "ARTIFACT_FILENAMES",
    "CAPABILITY_MATRIX",
    "DETERMINISTIC_ASSERTION_HANDLERS",
    "FUTURE",
    "OBSERVATION_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "REQUIRED_METRIC_NAMES",
    "SUPPORTED",
    "TRAJECTORY_CATEGORIES",
    "CapabilityDefinition",
    "CaseCapabilitySupport",
    "CaseResult",
    "EXPOSED_FIXTURE_PATHS",
    "TrajectoryCase",
    "TrajectoryDataset",
    "TrajectoryFixture",
    "TrajectoryHarness",
    "TrajectoryObservation",
    "TrajectoryEvaluationRun",
    "TrajectoryEvaluationRunner",
    "assess_case_capabilities",
    "capability_matrix_payload",
    "canonicalize",
    "compute_trajectory_metrics",
    "grade_case",
    "load_cases_jsonl",
    "partition_case_ids",
    "stable_heading_slug",
    "validate_evidence_reference",
    "validate_fixture_reference",
    "write_trajectory_artifacts",
]
