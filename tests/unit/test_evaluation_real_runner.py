import pytest
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json

from linkloom.evaluation.dataset import InferenceDataset
from linkloom.evaluation.real_execution import RealProviderRunner
from linkloom.evaluation.real_provider import AgyCliAdapter, InferenceRequest, ProviderResponse, ProviderUsage
from linkloom.evaluation.models import PredictionRecord

class FakeAdapter(AgyCliAdapter):
    def __init__(self, mode: str = "perfect"):
        super().__init__(model="test-model", runner=None)
        self.mode = mode
        self.requests: List[InferenceRequest] = []
        self.batch_calls = 0

    def predict(self, request: InferenceRequest) -> ProviderResponse:
        raise NotImplementedError("Tests should use predict_batch")

    def predict_batch(self, requests: List[InferenceRequest]) -> List[ProviderResponse]:
        self.batch_calls += 1
        self.requests.extend(requests)
        usage = ProviderUsage(request_count=1, duration_ms=10, provider_model="test-model", prompt_tokens=100, completion_tokens=20)
        
        if self.mode == "error":
            return [ProviderResponse(None, usage, error_type="timeout") for _ in requests]
            
        responses = []
        for i, request in enumerate(requests):
            if self.mode == "malformed":
                # Make the first item in each batch malformed, the rest valid
                if i == 0:
                    responses.append(ProviderResponse(None, usage, error_type="schema_invalid"))
                    continue

            is_topic = request.source_id == request.target_id
            
            evidence_quote = ""
            content = request.documents[request.source_id]
            for line in content.splitlines():
                if len(line) >= 10:
                    evidence_quote = line[:10]
                    break
                    
            record = PredictionRecord(
                source_id=request.source_id,
                target_id=request.target_id,
                should_link=False,
                confidence=0.9,
                evidence_refs=[evidence_quote] if evidence_quote else [],
                relation_type="concept-to-practice",
                direction="forward"
            )
            responses.append(ProviderResponse(record, usage))
            
        return responses

def test_real_runner_perfect(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    dataset = InferenceDataset(repo_root)
    adapter = FakeAdapter("perfect")
    runner = RealProviderRunner(adapter)
    
    predictions, bad_cases, evidence_validations, usage = runner.run_inference(dataset, run_id="test_run_id")
    
    assert len(predictions) == 55
    assert len(bad_cases) == 0
    assert len(adapter.requests) == 55
    assert adapter.batch_calls > 2  # Proves chunking happened
    assert usage["request_count"] == adapter.batch_calls
    assert usage["input_tokens"] == adapter.batch_calls * 100
    
    # check valid evidence
    for valid in evidence_validations:
        assert valid["valid"] is True
        assert valid["reason"] == "exact_match"

def test_real_runner_error(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    dataset = InferenceDataset(repo_root)
    adapter = FakeAdapter("error")
    runner = RealProviderRunner(adapter)
    
    predictions, bad_cases, evidence_validations, usage = runner.run_inference(dataset, run_id="test_run_id")
    
    assert len(predictions) == 55
    assert predictions[0]["schema_status"] == "invalid"
    assert len(bad_cases) == 55
    assert bad_cases[0].category == "provider_error"

def test_real_runner_malformed(tmp_path):
    repo_root = Path(__file__).parent.parent.parent
    dataset = InferenceDataset(repo_root)
    adapter = FakeAdapter("malformed")
    runner = RealProviderRunner(adapter)
    
    predictions, bad_cases, evidence_validations, usage = runner.run_inference(dataset, run_id="test_run_id")
    
    assert len(predictions) == 55
    # The first item in each chunk is malformed
    invalid_count = sum(1 for p in predictions if p["schema_status"] == "invalid")
    assert invalid_count == adapter.batch_calls
    assert len(bad_cases) == adapter.batch_calls
    assert bad_cases[0].category == "malformed_output"
