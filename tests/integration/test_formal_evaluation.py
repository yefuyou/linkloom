from pathlib import Path

from linkloom.evaluation.dataset import EvaluationDataset
from linkloom.evaluation.inference import mock_inference
from linkloom.evaluation.runner import evaluate
from linkloom.evaluation.reporting import write_report


def test_runner_perfect_scenario(tmp_path: Path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    
    manifest = evaluate(
        repo_root=repo_root,
        inference_fn=lambda ds: mock_inference(ds, scenario="perfect"),
        provider="mock",
        scenario="perfect"
    )
    
    assert manifest.dataset_version == "relation_vault_v1"
    
    # Assert metrics for perfect mock inference
    link_f1 = next((m.value for m in manifest.metrics if m.name == "link_f1"), None)
    assert link_f1 == 1.0
    
    # Assert reporting writes correctly
    report_file = write_report(manifest, tmp_path)
    assert report_file.exists()
    assert (tmp_path / "manifest.json").exists()


def test_runner_noisy_scenario(tmp_path: Path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    
    manifest = evaluate(
        repo_root=repo_root,
        inference_fn=lambda ds: mock_inference(ds, scenario="noisy"),
        provider="mock",
        scenario="noisy"
    )
    
    assert manifest.dataset_version == "relation_vault_v1"
    
    # Noisy has extra false positives, recall should be 1.0, precision < 1.0
    link_recall = next((m.value for m in manifest.metrics if m.name == "link_recall"), None)
    link_precision = next((m.value for m in manifest.metrics if m.name == "link_precision"), None)
    
    assert link_recall == 1.0
    assert link_precision is not None and link_precision < 1.0
