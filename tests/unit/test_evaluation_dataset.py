import os
import pytest
from pathlib import Path
from linkloom.evaluation.dataset import EvaluationDataset
from linkloom.evaluation.models import DatasetManifest

def test_evaluation_dataset_load():
    # repo root is expected to be the directory containing this test file up a few levels
    repo_root = Path(__file__).resolve().parent.parent.parent
    dataset = EvaluationDataset(str(repo_root))
    
    docs = dataset.get_inference_documents()
    pairs = dataset.get_inference_pairs()
    
    assert len(docs) == 10
    assert len(pairs) == 45
    
    # Check that frozen assert passes
    manifest = dataset.get_manifest()
    dataset.assert_frozen(manifest)
    
    # Check that altering the expected manifest raises error
    bad_manifest = DatasetManifest(
        dataset_version="bad",
        document_count=10,
        pair_count=45,
        fixture_sha256="bad_sha",
        gold_sha256=manifest.gold_sha256,
        frozen=True
    )
    with pytest.raises(ValueError, match="Fixture digest mismatch"):
        dataset.assert_frozen(bad_manifest)
