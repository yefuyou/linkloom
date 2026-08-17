import hashlib
import json
import subprocess
import sys
from pathlib import Path

def test_eval_perfect_scenario(tmp_path: Path):
    repo_root = Path.cwd()
    output_dir = tmp_path / "eval_out"
    
    gold_path = repo_root / "tests" / "eval" / "relation_gold.yaml"
    
    before_gold = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    
    cmd = [
        sys.executable, "-m", "linkloom", "eval",
        "--dataset", "relation_vault_v1",
        "--provider", "mock",
        "--scenario", "perfect",
        "--output", str(output_dir),
        "--repo-root", str(repo_root)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    assert result.returncode == 0
    assert "Status: completed" in result.stdout
    assert "Baseline Eligible: False" in result.stdout
    assert "Topic F1: 1.000" in result.stdout
    assert "Relation Link F1: 1.000" in result.stdout
    
    run_dirs = list(output_dir.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    
    artifacts = {p.name for p in run_dir.iterdir() if p.is_file()}
    assert len(artifacts) == 7
    
    after_gold = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    assert before_gold == after_gold

def test_eval_noisy_scenario(tmp_path: Path):
    repo_root = Path.cwd()
    output_dir = tmp_path / "eval_out"
    cmd = [
        sys.executable, "-m", "linkloom", "eval",
        "--dataset", "relation_vault_v1",
        "--provider", "mock",
        "--scenario", "noisy",
        "--output", str(output_dir),
        "--repo-root", str(repo_root)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Status: completed" in result.stdout
    
    run_dirs = list(output_dir.iterdir())
    run_dir = run_dirs[0]
    bad_cases_file = run_dir / "bad_cases.json"
    bad_cases = json.loads(bad_cases_file.read_text())
    assert len(bad_cases) > 0

def test_eval_malformed_scenario(tmp_path: Path):
    repo_root = Path.cwd()
    output_dir = tmp_path / "eval_out"
    cmd = [
        sys.executable, "-m", "linkloom", "eval",
        "--dataset", "relation_vault_v1",
        "--provider", "mock",
        "--scenario", "malformed",
        "--output", str(output_dir),
        "--repo-root", str(repo_root)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Status: invalid" in result.stdout
    
    run_dirs = list(output_dir.iterdir())
    run_dir = run_dirs[0]
    bad_cases_file = run_dir / "bad_cases.json"
    bad_cases = json.loads(bad_cases_file.read_text())
    assert len(bad_cases) > 0
    
    # Ensure no raw note content is output
    # If it was output, it would either be in stdout or bad_cases.
    assert "raw_content" not in result.stdout
    assert "exception repr" not in result.stdout

def test_eval_unsupported_provider(tmp_path: Path):
    repo_root = Path.cwd()
    output_dir = tmp_path / "eval_out"
    cmd = [
        sys.executable, "-m", "linkloom", "eval",
        "--dataset", "relation_vault_v1",
        "--provider", "openai",
        "--scenario", "perfect",
        "--output", str(output_dir),
        "--repo-root", str(repo_root)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Error: unsupported provider" in result.stdout

def test_eval_unsupported_dataset(tmp_path: Path):
    repo_root = Path.cwd()
    output_dir = tmp_path / "eval_out"
    cmd = [
        sys.executable, "-m", "linkloom", "eval",
        "--dataset", "unknown_vault",
        "--provider", "mock",
        "--scenario", "perfect",
        "--output", str(output_dir),
        "--repo-root", str(repo_root)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1
    assert "Error: unsupported dataset" in result.stdout
