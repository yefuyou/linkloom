import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from linkloom.evaluation.models import PredictionRecord
from linkloom.evaluation.isolation import check_inference_payload

@dataclass(frozen=True)
class InferenceRequest:
    source_id: str
    target_id: str
    prompt: str
    documents: Dict[str, str]

    def __post_init__(self) -> None:
        payload = {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "prompt": self.prompt,
            "documents": self.documents
        }
        report = check_inference_payload(payload)
        if not report.is_safe:
            raise ValueError(f"InferenceRequest payload contains forbidden terms: {report.violations}")

@dataclass(frozen=True)
class ProviderUsage:
    request_count: int = 1
    duration_ms: int = 0
    stdout_chars: int = 0
    stderr_chars: int = 0
    retry_count: int = 0
    provider_model: str = "gemini-3.1-pro-high"
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

@dataclass(frozen=True)
class ProviderResponse:
    record: Optional[PredictionRecord]
    usage: ProviderUsage
    error_type: Optional[str] = None  # missing_executable, timeout, non_zero_exit, empty_output, invalid_json, schema_invalid

class CommandRunner:
    def run(self, cmd: List[str], cwd: str, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)

class AgyCliAdapter:
    def __init__(
        self,
        executable: str = r"C:\Users\18019\AppData\Local\agy\bin\agy.exe",
        model: str = "gemini-3.1-pro-high",
        effort: str = "high",
        default_timeout: int = 300,
        runner: Optional[CommandRunner] = None
    ):
        self.executable = executable
        self.model = model
        self.effort = effort
        self.default_timeout = default_timeout
        self.runner = runner or CommandRunner()

    def predict(self, request: InferenceRequest) -> ProviderResponse:
        start_time = time.time()
        
        payload = {
            "task": "topic" if request.source_id == request.target_id else "relation",
            "source_id": request.source_id,
            "target_id": request.target_id,
            "documents": request.documents
        }
        context_json = json.dumps(payload, ensure_ascii=False)

        schema_prompt = (
            request.prompt + "\n\n"
            f"Context:\n```json\n{context_json}\n```\n\n"
            "Return ONLY a valid JSON object matching this schema. Do not add markdown blocks.\n"
            "{\n"
            '  "relation_type": "string or null",\n'
            '  "direction": "string or null",\n'
            '  "evidence_refs": ["string"],\n'
            '  "should_link": true or false or null,\n'
            '  "confidence": float 0.0-1.0 or null\n'
            "}"
        )
        
        cmd = [
            self.executable,
            "--model", self.model,
            "--effort", self.effort,
            "--print-timeout", "5m",
            "--print",
            schema_prompt
        ]
        
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as safe_cwd:
            try:
                proc = self.runner.run(cmd, cwd=safe_cwd, timeout=self.default_timeout)
            except OSError as exc:
                duration_ms = int((time.time() - start_time) * 1000)
                usage = ProviderUsage(duration_ms=duration_ms, provider_model=self.model)
                err_type = "missing_executable" if isinstance(exc, FileNotFoundError) else "provider_process"
                return ProviderResponse(None, usage, error_type=err_type)
            except subprocess.TimeoutExpired as exc:
                duration_ms = int((time.time() - start_time) * 1000)
                stdout_len = len(exc.stdout) if exc.stdout and isinstance(exc.stdout, str) else (len(exc.stdout) if exc.stdout else 0)
                stderr_len = len(exc.stderr) if exc.stderr and isinstance(exc.stderr, str) else (len(exc.stderr) if exc.stderr else 0)
                usage = ProviderUsage(
                    duration_ms=duration_ms,
                    stdout_chars=stdout_len,
                    stderr_chars=stderr_len,
                    provider_model=self.model
                )
                return ProviderResponse(None, usage, error_type="timeout")

            duration_ms = int((time.time() - start_time) * 1000)
            usage = ProviderUsage(
                duration_ms=duration_ms,
                stdout_chars=len(proc.stdout),
                stderr_chars=len(proc.stderr),
                provider_model=self.model
            )

            if proc.returncode != 0:
                return ProviderResponse(None, usage, error_type="non_zero_exit")
            
            stdout = proc.stdout.strip()
            if not stdout:
                return ProviderResponse(None, usage, error_type="empty_output")
                
            if stdout.startswith("```json"):
                stdout = stdout[7:].rsplit("```", 1)[0].strip()
            elif stdout.startswith("```"):
                stdout = stdout[3:].rsplit("```", 1)[0].strip()

            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                return ProviderResponse(None, usage, error_type="invalid_json")
                
            if not isinstance(data, dict):
                return ProviderResponse(None, usage, error_type="invalid_json")
                
            try:
                record = PredictionRecord(
                    source_id=request.source_id,
                    target_id=request.target_id,
                    relation_type=data.get("relation_type"),
                    direction=data.get("direction"),
                    evidence_refs=data.get("evidence_refs", []),
                    should_link=data.get("should_link"),
                    confidence=data.get("confidence")
                )
                return ProviderResponse(record, usage)
            except Exception:
                return ProviderResponse(None, usage, error_type="schema_invalid")

    def predict_batch(self, requests: List[InferenceRequest]) -> List[ProviderResponse]:
        start_time = time.time()
        if not requests:
            return []

        payload_list = []
        for req in requests:
            payload_list.append({
                "task": "topic" if req.source_id == req.target_id else "relation",
                "source_id": req.source_id,
                "target_id": req.target_id,
                "documents": req.documents
            })

        context_json = json.dumps(payload_list, ensure_ascii=False)
        # Using the same prompt logic from the first item since they are homogeneous in a batch (topic or relation)
        base_prompt = requests[0].prompt

        schema_prompt = (
            base_prompt + "\n\n"
            f"Context:\n```json\n{context_json}\n```\n\n"
            "Return ONLY a valid JSON array of objects matching this schema. The array must correspond exactly to the order of the input context. Do not add markdown blocks.\n"
            "[\n"
            "  {\n"
            '    "relation_type": "string or null",\n'
            '    "direction": "string or null",\n'
            '    "evidence_refs": ["string"],\n'
            '    "should_link": true or false or null,\n'
            '    "confidence": float 0.0-1.0 or null\n'
            "  }\n"
            "]"
        )
        
        cmd = [
            self.executable,
            "--model", self.model,
            "--effort", self.effort,
            "--print-timeout", "5m",
            "--print",
            schema_prompt
        ]

        def make_error_responses(error_type: str, usage: ProviderUsage) -> List[ProviderResponse]:
            return [ProviderResponse(None, usage, error_type=error_type) for _ in requests]

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as safe_cwd:
            try:
                proc = self.runner.run(cmd, cwd=safe_cwd, timeout=self.default_timeout)
            except OSError as exc:
                duration_ms = int((time.time() - start_time) * 1000)
                usage = ProviderUsage(duration_ms=duration_ms, provider_model=self.model)
                err_type = "missing_executable" if isinstance(exc, FileNotFoundError) else "provider_process"
                return make_error_responses(err_type, usage)
            except subprocess.TimeoutExpired as exc:
                duration_ms = int((time.time() - start_time) * 1000)
                stdout_len = len(exc.stdout) if exc.stdout and isinstance(exc.stdout, str) else (len(exc.stdout) if exc.stdout else 0)
                stderr_len = len(exc.stderr) if exc.stderr and isinstance(exc.stderr, str) else (len(exc.stderr) if exc.stderr else 0)
                usage = ProviderUsage(
                    duration_ms=duration_ms,
                    stdout_chars=stdout_len,
                    stderr_chars=stderr_len,
                    provider_model=self.model
                )
                return make_error_responses("timeout", usage)

            duration_ms = int((time.time() - start_time) * 1000)
            usage = ProviderUsage(
                duration_ms=duration_ms,
                stdout_chars=len(proc.stdout),
                stderr_chars=len(proc.stderr),
                provider_model=self.model
            )

            if proc.returncode != 0:
                return make_error_responses("non_zero_exit", usage)
            
            stdout = proc.stdout.strip()
            if not stdout:
                return make_error_responses("empty_output", usage)
                
            if stdout.startswith("```json"):
                stdout = stdout[7:].rsplit("```", 1)[0].strip()
            elif stdout.startswith("```"):
                stdout = stdout[3:].rsplit("```", 1)[0].strip()

            try:
                data_list = json.loads(stdout)
            except json.JSONDecodeError:
                return make_error_responses("invalid_json", usage)
                
            if not isinstance(data_list, list):
                return make_error_responses("invalid_json", usage)
                
            # If the provider gave us fewer or more items, we map what we can. 
            # A completely malformed batch creates one safe invalid prediction per expected task.
            responses = []
            for i, req in enumerate(requests):
                if i < len(data_list):
                    data = data_list[i]
                    if not isinstance(data, dict):
                        responses.append(ProviderResponse(None, usage, error_type="schema_invalid"))
                        continue
                    try:
                        record = PredictionRecord(
                            source_id=req.source_id,
                            target_id=req.target_id,
                            relation_type=data.get("relation_type"),
                            direction=data.get("direction"),
                            evidence_refs=data.get("evidence_refs", []),
                            should_link=data.get("should_link"),
                            confidence=data.get("confidence")
                        )
                        responses.append(ProviderResponse(record, usage))
                    except Exception:
                        responses.append(ProviderResponse(None, usage, error_type="schema_invalid"))
                else:
                    responses.append(ProviderResponse(None, usage, error_type="schema_invalid"))

            return responses
