import pytest
import subprocess
from linkloom.evaluation.real_provider import InferenceRequest, AgyCliAdapter, CommandRunner, ProviderUsage, ProviderResponse

class FakeRunner(CommandRunner):
    def __init__(self, stdout="", stderr="", returncode=0, timeout_expired=False, missing_executable=False):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self.timeout_expired = timeout_expired
        self.missing_executable = missing_executable
        self.last_cmd = None
        self.last_cwd = None

    def run(self, cmd, cwd, timeout):
        self.last_cmd = cmd
        self.last_cwd = cwd
        if self.missing_executable:
            raise FileNotFoundError("agy.exe not found")
        if self.timeout_expired:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output=self._stdout.encode() if self._stdout else b"", stderr=self._stderr.encode() if self._stderr else b"")
        return subprocess.CompletedProcess(args=cmd, returncode=self._returncode, stdout=self._stdout, stderr=self._stderr)

def test_inference_request_rejects_forbidden_keys():
    with pytest.raises(ValueError, match="FORBIDDEN_EVALUATION_FIELD"):
        InferenceRequest(
            source_id="a", target_id="b", prompt="prompt", documents={"expected_topic_ids": "forbidden"}
        )

def test_successful_response():
    runner = FakeRunner(stdout='{"relation_type": "foo", "direction": "forward", "evidence_refs": [], "should_link": true, "confidence": 0.9}')
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    
    response = adapter.predict(request)
    assert response.error_type is None
    assert response.record is not None
    assert response.record.relation_type == "foo"
    assert response.record.confidence == 0.9
    assert response.usage.provider_model == "gemini-3.1-pro-high"
    assert runner.last_cmd[:-1] == [
        adapter.executable,
        "--model", "gemini-3.1-pro-high",
        "--effort", "high",
        "--print-timeout", "5m",
        "--print",
    ]
    assert "Return ONLY a valid JSON object" in runner.last_cmd[-1]
    
def test_missing_executable():
    runner = FakeRunner(missing_executable=True)
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "missing_executable"
    
def test_timeout():
    runner = FakeRunner(timeout_expired=True)
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "timeout"
    
def test_non_zero_exit():
    runner = FakeRunner(returncode=1)
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "non_zero_exit"

def test_empty_output():
    runner = FakeRunner(stdout="   ")
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "empty_output"
    
def test_invalid_json():
    runner = FakeRunner(stdout="not json")
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "invalid_json"

def test_schema_invalid():
    runner = FakeRunner(stdout='{"confidence": "not-a-float"}')
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type == "schema_invalid"

def test_markdown_strip():
    runner = FakeRunner(stdout='```json\n{"relation_type": "foo"}\n```')
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    response = adapter.predict(request)
    assert response.error_type is None
    assert response.record.relation_type == "foo"

def test_batch_successful_response():
    runner = FakeRunner(stdout='[{"relation_type": "foo", "direction": "forward", "evidence_refs": [], "should_link": true, "confidence": 0.9}]')
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    
    responses = adapter.predict_batch([request])
    assert len(responses) == 1
    assert responses[0].error_type is None
    assert responses[0].record is not None
    assert responses[0].record.relation_type == "foo"
    assert responses[0].record.confidence == 0.9
    assert responses[0].usage.provider_model == "gemini-3.1-pro-high"

def test_batch_missing_executable():
    runner = FakeRunner(missing_executable=True)
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    responses = adapter.predict_batch([request, request])
    assert len(responses) == 2
    assert responses[0].error_type == "missing_executable"
    assert responses[1].error_type == "missing_executable"

def test_batch_empty_output():
    runner = FakeRunner(stdout="   ")
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    responses = adapter.predict_batch([request])
    assert len(responses) == 1
    assert responses[0].error_type == "empty_output"

def test_batch_invalid_json():
    runner = FakeRunner(stdout="not json")
    adapter = AgyCliAdapter(runner=runner)
    request = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    responses = adapter.predict_batch([request])
    assert len(responses) == 1
    assert responses[0].error_type == "invalid_json"

def test_batch_partial_schema_invalid():
    runner = FakeRunner(stdout='[{"relation_type": "foo", "confidence": 0.9}, {"confidence": "not-a-float"}]')
    adapter = AgyCliAdapter(runner=runner)
    request1 = InferenceRequest(source_id="a", target_id="b", prompt="test", documents={})
    request2 = InferenceRequest(source_id="c", target_id="d", prompt="test", documents={})
    responses = adapter.predict_batch([request1, request2])
    assert len(responses) == 2
    assert responses[0].error_type is None
    assert responses[0].record.relation_type == "foo"
    assert responses[1].error_type == "schema_invalid"
    assert responses[1].record is None
