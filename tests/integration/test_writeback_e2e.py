import json
import pytest
from pathlib import Path

from linkloom.cli import main
from linkloom.mutations.e2e import E2EWritebackRunner
from linkloom.evaluation.writeback_eval import run_evaluation
from linkloom.mutations.backup import BackupManifest


def test_writeback_e2e_successful_path_and_rollback(tmp_path):
    output_dir = tmp_path / "output"
    target_root = tmp_path / "target"
    backup_root = tmp_path / "backup"
    
    runner = E2EWritebackRunner(output_dir)
    manifest = runner.run_e2e(
        target_root, backup_root, "APPROVE_DEFAULT"
    )
    
    assert manifest["status"] == "applied", manifest.get("error_code")
    assert "p4_run_id" in manifest
    assert "eval_run_id" in manifest
    assert "writeback_run_id" in manifest
    assert manifest["safety_flags"]["synthetic_only"] is True
    
    # Prove byte-for-byte rollback
    rollback_ok = runner.execute_rollback_for_run(manifest["backup_id"], target_root, backup_root)
    assert rollback_ok is True
    
    # Check that it actually rolled back
    note_path = target_root / "note1.md"
    content = note_path.read_text("utf-8")
    assert "Additional line." not in content


def test_cli_writeback_demo(tmp_path, capsys):
    output_dir = tmp_path / "output"
    # Run the demo
    exit_code = main(["writeback", "demo", "--output", str(output_dir)])
    
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Status: completed" in captured.out
    
    artifact_path = output_dir / ".artifacts" / "p7" / "run_manifest.json"
    assert artifact_path.exists()
    
    with open(artifact_path, "r") as f:
        manifest = json.load(f)
    assert manifest["status"] == "applied"
    
    # Assert real-root write is not allowed by policy
    target_root = Path.cwd()
    runner = E2EWritebackRunner(output_dir)
    with pytest.raises(Exception) as excinfo:
        runner.run_e2e(target_root, output_dir / "backup", "APPROVE plan_123 root=123 ops=1 sha=abc")
    assert "Synthetic target root required" in str(excinfo.value) or "unsupported" in str(excinfo.value) or "Validation" in str(excinfo.value) or "Permission" in str(excinfo.value) or "Must use synthetic root" in str(excinfo.value) or "Target root must be temporary" in str(excinfo.value) or "synthetic_only is True" in str(excinfo.value) or "Real repository" in str(excinfo.value)


def test_writeback_failure_matrix(tmp_path):
    output_dir = tmp_path / "output"
    metrics = run_evaluation(output_dir)
    
    assert metrics["cases_total"] == 8
    assert metrics["passed"] is True
    assert metrics["unauthorized_writes"] == 0


def test_writeback_failure_matrix_redaction_failure(tmp_path):
    output_dir = tmp_path / "output"
    # Run the evaluation first so it builds traces
    run_evaluation(output_dir)
    
    # create a fake file that evaluator will see with forbidden word
    (output_dir / "traces").mkdir(parents=True, exist_ok=True)
    (output_dir / "traces" / "fake.txt").write_text("# Note 1 leak")
    
    metrics = run_evaluation(output_dir)
    assert metrics["redaction_passed"] is False
    assert metrics["passed"] is False
