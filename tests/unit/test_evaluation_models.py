import pytest
from linkloom.evaluation.models import InferenceDocument, PredictionRecord

def test_inference_document_forbids_gold_path():
    with pytest.raises(ValueError, match="Forbidden keyword 'gold'"):
        InferenceDocument(path="tests/eval/gold_test.md", content_sha256="abc", content="test")
        
def test_prediction_record_forbids_raw_bodies():
    with pytest.raises(ValueError, match="evidence_refs should contain short refs"):
        PredictionRecord(
            source_id="A", target_id="B", relation_type="rel", direction="forward",
            evidence_refs=["This is a very long string that acts like a raw body of the vault note. " * 10]
        )
