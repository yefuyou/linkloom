import pytest
from linkloom.evaluation.real_provider import InferenceRequest


def test_inference_request_rejects_forbidden_keys():
    with pytest.raises(ValueError, match="FORBIDDEN_EVALUATION_FIELD"):
        InferenceRequest(
            source_id="a", target_id="b", prompt="prompt", documents={"expected_topic_ids": "forbidden"}
        )
