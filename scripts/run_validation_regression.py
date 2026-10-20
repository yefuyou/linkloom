"""Run the frozen local regression set for Retrieval V2 and benchmark stages.

Canonical command (from the repository root):
    python scripts/run_validation_regression.py

The runner never selects provider-smoke tests. Pytest scratch is isolated in a
fresh, dedicated work/ directory per run so an existing directory is never
cleared by pytest's --basetemp handling.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_FILES = (
    "tests/unit/test_retrieval_benchmark_v2.py",
    "tests/unit/test_retrieval_metrics_v2.py",
    "tests/unit/test_retrieval_characterization.py",
    "tests/unit/test_retrieval_v2.py",
    "tests/unit/test_retrieval_v2_downstream_comparison.py",
    "tests/unit/test_index_update_coordinator.py",
    "tests/unit/test_temporal_decision_memory.py",
    "tests/unit/test_decision_memory_write_policy.py",
    "tests/unit/test_decision_memory_reconciliation.py",
    "tests/unit/test_context_assembler.py",
    "tests/unit/test_agent_retrieval_v2_integration.py",
    "tests/unit/test_runtime_v2_grounding_visibility.py",
    "tests/unit/test_deepseek_api_adapter.py",
    "tests/unit/test_deepseek_smoke_evaluation_states.py",
    "tests/unit/test_smoke_transport_observability.py",
    "tests/unit/test_memoryagentbench_adapter.py",
    "tests/unit/test_public_memory_profile.py",
    "tests/unit/test_memoryagentbench_g3_pilot.py",
    "tests/unit/test_memoryagentbench_g31_fixed50.py",
    "tests/unit/test_memoryagentbench_g31_validation_closure.py",
    "tests/unit/test_memoryagentbench_g31_heldout.py",
    "tests/unit/test_memoryagentbench_g32_heldout.py",
    "tests/unit/test_memoryagentbench_g32_finalization.py",
    "tests/unit/test_gemini_count_tokens_compat.py",
    "tests/integration/test_retrieval_v2_agent_integration.py",
    "tests/integration/test_m12b_memory_live_001.py",
    "tests/integration/test_stage_c_end_to_end_loop.py",
)


def main() -> int:
    work_dir = ROOT / "work"
    if not work_dir.is_dir():
        raise SystemExit(f"Expected project work directory is missing: {work_dir}")

    basetemp = work_dir / f"validation-closure-pytest-{uuid.uuid4().hex}"
    if basetemp.exists():
        raise SystemExit(f"Refusing to reuse an existing pytest temp directory: {basetemp}")

    report = Path(
        os.environ.get(
            "LINKLOOM_VALIDATION_REGRESSION_REPORT",
            str(ROOT / "docs" / "evaluation" / "validation_regression.xml"),
        )
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TEST_FILES,
        "-q",
        "-p",
        "pytest_windows_tempdir",
        "-p",
        "no:cacheprovider",
        "--tb=short",
        "--basetemp",
        str(basetemp),
        "--junitxml",
        str(report),
    ]
    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    scripts_path = str(ROOT / "scripts")
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (scripts_path, source_path, environment.get("PYTHONPATH", ""))
        if part
    )
    environment["LINKLOOM_PYTEST_BASETEMP"] = str(basetemp.resolve(strict=False))

    print("Canonical Stage A-D regression:")
    print(" ".join(command))
    print(f"pytest basetemp: {basetemp}")
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
