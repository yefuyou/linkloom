from pathlib import Path

from linkloom.evaluation.isolation import check_inference_payload, check_inference_module


def test_inference_payload_safety():
    # Safe payload
    report = check_inference_payload({
        "predictions": [
            {"source_id": "doc1", "target_id": "doc2", "relation": "discusses"}
        ]
    })
    assert report.is_safe
    assert len(report.violations) == 0

    # Unsafe payload with forbidden keyword
    report = check_inference_payload({
        "predictions": [],
        "ground_truth": "something"
    })
    assert not report.is_safe
    assert "FORBIDDEN_EVALUATION_FIELD" in report.violations


def test_inference_module_safety():
    repo_root = Path(__file__).resolve().parent.parent.parent
    inference_path = repo_root / "src" / "linkloom" / "evaluation" / "inference.py"
    
    report = check_inference_module(str(inference_path))
    assert report.is_safe, f"inference.py failed safety check: {report.violations}"
