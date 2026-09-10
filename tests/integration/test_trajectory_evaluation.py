from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import socket

from linkloom.evaluation.trajectory.__main__ import main
from linkloom.evaluation.trajectory.models import CaseResult
from linkloom.evaluation.trajectory.reporting import ARTIFACT_FILENAMES
from linkloom.evaluation.trajectory.runner import TrajectoryEvaluationRunner


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "eval" / "trajectory_eval_v1" / "cases.jsonl"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "trajectory_kb_v1"
GOLD_PATH = REPO_ROOT / "tests" / "eval" / "relation_gold.yaml"


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_actual_runtime_run_produces_23_pass_0_fail_18_not_implemented_and_six_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture_hash_before = _tree_hash(FIXTURE_ROOT)
    gold_hash_before = hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path.name == "relation_gold.yaml":
            raise AssertionError("trajectory evaluator must not open relation Gold")
        return original_open(path, *args, **kwargs)

    def blocked_network(*args, **kwargs):
        raise AssertionError("trajectory evaluator must not open a network connection")

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(socket, "create_connection", blocked_network)

    output = tmp_path / "actual-run"
    run = TrajectoryEvaluationRunner(
        repo_root=REPO_ROOT,
        cases_path=CASES_PATH,
        output_dir=output,
    ).run(run_id="integration-run")

    assert run.failure_summary["counts"] == {
        "PASS": 23,
        "FAIL": 0,
        "NOT_IMPLEMENTED": 18,
    }
    assert len(run.observations) == 23
    assert len(run.executed_case_ids) == 23
    assert run.denied_side_effect_executor_counts == {
        "write_file": 0,
        "read_gold": 0,
        "raw_filesystem": 0,
    }

    assert run.observations["EXEC-03"].tool_results[0]["error"]["code"] == (
        "TOOL_TERMINAL_CHECKPOINT_FAILED"
    )
    assert run.observations["EXEC-03"].execution["ledger"]["history"][0][
        "states"
    ] == ["pending", "completed"]
    assert run.observations["REP-02"].tool_results[1]["error"]["code"] == (
        "TOOL_LEDGER_CONFLICT"
    )
    assert run.observations["NF-01"].tool_results[0]["business_status"] == "NOT_FOUND"
    assert run.observations["CP-04"].execution["recovery_decisions"][0][
        "decision"
    ] == "requires_manual_decision"

    assert {path.name for path in output.iterdir()} == set(ARTIFACT_FILENAMES)
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"total": 41, "executable": 23, "future": 18}
    assert manifest["provider"] == "none"
    assert manifest["judge"] == "NOT_EVALUATED"
    assert manifest["safety"] == {
        "network_access": False,
        "gold_access": False,
        "real_vault_access": False,
        "provider_call": False,
        "judge_call": False,
    }
    assert all(not Path(value).is_absolute() for value in manifest["paths"].values())

    result_lines = (output / "case_results.jsonl").read_text(encoding="utf-8").splitlines()
    parsed_results = [CaseResult.from_dict(json.loads(line)) for line in result_lines]
    assert len(parsed_results) == 41
    assert sum(result.status == "PASS" for result in parsed_results) == 23
    assert sum(result.status == "FAIL" for result in parsed_results) == 0
    assert sum(result.status == "NOT_IMPLEMENTED" for result in parsed_results) == 18

    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert len(metrics["metrics"]) == 9
    assert all(item["status"] == "EVALUATED" for item in metrics["metrics"].values())
    assert all(item["value"] == 1.0 for item in metrics["metrics"].values())
    matrix = json.loads((output / "capability_matrix.json").read_text(encoding="utf-8"))
    assert matrix["derived"]["executable_case_count"] == 23
    assert matrix["derived"]["future_case_count"] == 18

    artifact_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(output.iterdir())
    )
    assert str(REPO_ROOT) not in artifact_text
    assert "C:\\Users\\" not in artifact_text
    for note_path in sorted(FIXTURE_ROOT.rglob("*.md")):
        note_body = note_path.read_text(encoding="utf-8").strip()
        if note_body:
            assert note_body not in artifact_text
    assert "aggregate score" not in artifact_text.casefold()

    monkeypatch.undo()
    assert _tree_hash(FIXTURE_ROOT) == fixture_hash_before
    assert hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest() == gold_hash_before


def test_cli_runs_offline_and_module_has_no_provider_judge_or_network_surface(
    tmp_path: Path,
) -> None:
    output = tmp_path / "cli-run"

    exit_code = main(
        [
            "--repo",
            str(REPO_ROOT),
            "--cases",
            str(CASES_PATH.relative_to(REPO_ROOT)),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert {path.name for path in output.iterdir()} == set(ARTIFACT_FILENAMES)

    import linkloom.evaluation.trajectory.harness as harness_module
    import linkloom.evaluation.trajectory.runner as runner_module

    combined_source = inspect.getsource(harness_module) + inspect.getsource(runner_module)
    for forbidden_import in ("openai", "requests", "httpx", "urllib.request"):
        assert forbidden_import not in combined_source
    assert "judge(" not in combined_source
