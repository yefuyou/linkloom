import os
import shutil
from pathlib import Path
import pytest
import subprocess
import json

PROJECT_ROOT = Path("d:/webproject/vault-steward")
OUTPUTS_ROOT = PROJECT_ROOT / "outputs"

def test_runner_perfect():
    # Run the perfect mock evaluation command
    cmd = [
        "python", "-m", "relation_eval.runner.run_evaluation",
        "--config", "configs/eval.mock.yaml",
        "--mock-scenario", "perfect"
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Evaluation complete!" in result.stdout

    # Verify output directory is created and files are generated
    output_dirs = sorted([d for d in OUTPUTS_ROOT.iterdir() if d.is_dir()])
    assert len(output_dirs) > 0
    latest_run_dir = output_dirs[-1]
    
    assert (latest_run_dir / "run_manifest.json").exists()
    assert (latest_run_dir / "topic_predictions.jsonl").exists()
    assert (latest_run_dir / "relation_predictions.jsonl").exists()
    assert (latest_run_dir / "metrics.json").exists()
    assert (latest_run_dir / "bad_cases.json").exists()
    assert (latest_run_dir / "report.md").exists()

    # Load metrics and check perfect score
    with open(latest_run_dir / "metrics.json", "r", encoding="utf-8") as f:
        metrics = json.load(f)
    
    assert metrics["topic_classification"]["metrics"]["accuracy"] == 1.0
    assert metrics["relation_classification"]["metrics"]["precision"] == 1.0
    assert metrics["relation_classification"]["metrics"]["recall"] == 1.0
    
    with open(latest_run_dir / "bad_cases.json", "r", encoding="utf-8") as f:
        bad_cases = json.load(f)
    assert len(bad_cases) == 0

def test_runner_noisy():
    cmd = [
        "python", "-m", "relation_eval.runner.run_evaluation",
        "--config", "configs/eval.mock.yaml",
        "--mock-scenario", "noisy"
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0

    output_dirs = sorted([d for d in OUTPUTS_ROOT.iterdir() if d.is_dir()])
    latest_run_dir = output_dirs[-1]

    with open(latest_run_dir / "bad_cases.json", "r", encoding="utf-8") as f:
        bad_cases = json.load(f)
    assert len(bad_cases) > 0

    # Ensure various case types are caught
    case_types = {bc["case_type"] for bc in bad_cases}
    assert "TOPIC_FALSE_POSITIVE" in case_types
    assert "TOPIC_FALSE_NEGATIVE" in case_types
    assert "RELATION_FALSE_POSITIVE" in case_types
    assert "RELATION_FALSE_NEGATIVE" in case_types
    assert "RELATION_DIRECTION_ERROR" in case_types
    assert "RELATION_TYPE_ERROR" in case_types
    assert "EVIDENCE_NOT_IN_SOURCE" in case_types

def test_runner_malformed():
    cmd = [
        "python", "-m", "relation_eval.runner.run_evaluation",
        "--config", "configs/eval.mock.yaml",
        "--mock-scenario", "malformed"
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    # Malformed predictions are handled gracefully (bad cases logged), runner should still exit 0
    assert result.returncode == 0

    output_dirs = sorted([d for d in OUTPUTS_ROOT.iterdir() if d.is_dir()])
    latest_run_dir = output_dirs[-1]

    with open(latest_run_dir / "bad_cases.json", "r", encoding="utf-8") as f:
        bad_cases = json.load(f)
    assert len(bad_cases) > 0

    case_types = {bc["case_type"] for bc in bad_cases}
    assert "INVALID_PREDICTION_SCHEMA" in case_types
