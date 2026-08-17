import json
from pathlib import Path

from linkloom.evaluation.isolation import snapshot_tree
from linkloom.evaluation.runner import FormalEvaluationRunner


def test_formal_runner_artifacts_scenarios_and_frozen_inputs(tmp_path: Path):
    repo = Path(__file__).resolve().parents[2]
    fixture_root = repo / "tests" / "fixtures" / "relation_vault"
    gold_path = repo / "tests" / "eval" / "relation_gold.yaml"
    before_fixture = snapshot_tree(fixture_root)
    before_gold = gold_path.read_bytes()
    runner = FormalEvaluationRunner(repo, tmp_path)

    perfect = runner.run("mock", "perfect", "run_perfect_contract")
    noisy = runner.run("mock", "noisy", "run_noisy_contract")
    malformed = runner.run("mock", "malformed", "run_malformed_contract")

    expected = {
        "run_manifest.json",
        "predictions.jsonl",
        "metrics.json",
        "bad_cases.json",
        "trace_quality.json",
        "safety_report.json",
        "report.md",
    }
    for result in (perfect, noisy, malformed):
        run_dir = Path(result["run_dir"])
        assert {path.name for path in run_dir.iterdir()} == expected
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["document_count"] == 10
        assert manifest["candidate_pair_count"] == 45
        assert manifest["oracle"] is True
        assert manifest["baseline_eligible"] is False
        records = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(records) == 55
        forbidden = ("gold", "expected", "label", "ground_truth")
        assert all(not any(term in key.casefold() for term in forbidden) for record in records for key in record)
        assert all("content" not in json.dumps(record).casefold() for record in records)

    assert json.loads((Path(perfect["run_dir"]) / "metrics.json").read_text(encoding="utf-8"))["relation"]["link_f1"] == 1.0
    assert json.loads((Path(noisy["run_dir"]) / "bad_cases.json").read_text(encoding="utf-8"))
    assert json.loads((Path(malformed["run_dir"]) / "run_manifest.json").read_text(encoding="utf-8"))["status"] == "invalid"
    assert snapshot_tree(fixture_root) == before_fixture
    assert gold_path.read_bytes() == before_gold


def test_formal_runner_metrics_are_reproducible_and_non_mock_is_blocked(tmp_path: Path):
    repo = Path(__file__).resolve().parents[2]
    runner = FormalEvaluationRunner(repo, tmp_path)
    first = runner.run("mock", "noisy", "run_noisy_one")
    second = runner.run("mock", "noisy", "run_noisy_two")
    assert first["metrics"] == second["metrics"]

    invalid = runner.run("openai", "perfect", "run_real_provider_blocked")
    assert invalid["manifest"]["status"] == "invalid"
    assert invalid["manifest"]["baseline_eligible"] is False
    assert invalid["safety"]["passed"] is False
