"""Manual Gate A boundary. Offline tests here never read host credentials."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.observability.reader import TraceReader
from linkloom.scanner import scan_vault


SDK_VERSION = "2.22.0"
MODEL = "gemini-3.8-flash"
# Hyphens tokenize into common terms (including "a") in lexical retrieval.
QUERY = "zzzz_gate_a_no_match"
REPO = Path(__file__).resolve().parents[2]
PROMPT = (
    '请调用 search_notes，在 synthetic notes 中搜索精确字符串 '
    'zzzz_gate_a_no_match。query 参数只能是该字符串。'
    '如果 ToolResult 为空，请直接回答“未找到匹配笔记”并结束。不要调用其他工具。'
)


class SmokeBlocked(Exception):
    """A constant safe message; never include SDK exceptions or payloads."""


_SAFE_EXCEPTION_TYPES = frozenset({
    "TimeoutError", "ConnectionError", "OSError", "APIError", "ClientError",
    "ServerError", "HTTPError", "RuntimeError", "TypeError", "ValueError",
})
_PERSISTED_ERROR_CATEGORIES = {
    "MODEL_AUTH_REQUIRED": "AUTH",
    "MODEL_RATE_LIMITED": "RATE_LIMIT",
    "MODEL_INVALID_REQUEST": "SDK_REQUEST_SHAPE",
    "MODEL_RESPONSE_MALFORMED": "PROVIDER_RESPONSE_NORMALIZATION",
    "TOOL_INVALID_ARGUMENTS": "TOOL_CALL_INVALID",
    "TOOL_EXECUTION_FAILED": "TOOL_RUNTIME",
}


@dataclass
class SmokeDiagnostics:
    """Safe, fixed-shape failure fields for a future user-authorized run."""

    model: str
    stage: str = "client_construction"
    provider_calls: int = 0
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    normalized_error_code: str = "unavailable"
    exception_type: str = "unavailable"
    http_status: str = "unavailable"
    credential_leak_check: str = "PASS"
    provider_failed: bool = False


def _safe_http_status(exception):
    for attribute in ("code", "status_code"):
        value = getattr(exception, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return str(value)
    return "unavailable"


def _safe_provider_category(exception, status):
    if status == "401":
        return "AUTH"
    if status == "403":
        return "PERMISSION"
    if status == "404":
        return "MODEL_NOT_FOUND"
    if status == "429":
        return "RATE_LIMIT"
    if status in {"400", "422"}:
        return "SDK_REQUEST_SHAPE"
    if isinstance(exception, (TimeoutError, ConnectionError, OSError)):
        return "NETWORK"
    return "UNKNOWN_SAFE"


def _record_provider_exception(diagnostics, exception):
    if diagnostics is None:
        return
    status = _safe_http_status(exception)
    diagnostics.http_status = status
    diagnostics.normalized_error_code = _safe_provider_category(exception, status)
    name = type(exception).__name__
    diagnostics.exception_type = name if name in _SAFE_EXCEPTION_TYPES else "unavailable"
    diagnostics.provider_failed = True


def _capture_runtime_progress(diagnostics, state):
    if diagnostics is None or state is None:
        return
    ledger = list(state.tool_ledger)
    diagnostics.tool_calls = len(ledger)
    diagnostics.tool_names = sorted({
        record.tool_id for record in ledger
        if isinstance(record.tool_id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", record.tool_id)
    })
    for record in state.model_executions:
        error = getattr(record, "error", None)
        code = error.get("code") if isinstance(error, dict) else None
        category = _PERSISTED_ERROR_CATEGORIES.get(code)
        if category and diagnostics.normalized_error_code in {"unavailable", "UNKNOWN_SAFE"}:
            diagnostics.normalized_error_code = category


def format_failure_summary(diagnostics):
    """Return only fixed diagnostic fields; never render an exception or payload."""
    tool_names = ",".join(diagnostics.tool_names) if diagnostics.tool_names else "[]"
    return "\n".join((
        "gate_a_smoke=FAIL",
        f"stage={diagnostics.stage}",
        "provider=gemini",
        f"model={diagnostics.model}",
        f"provider_calls={diagnostics.provider_calls}",
        f"tool_calls={diagnostics.tool_calls}",
        f"tool_names={tool_names}",
        f"normalized_error_code={diagnostics.normalized_error_code}",
        f"exception_type={diagnostics.exception_type}",
        f"http_status={diagnostics.http_status}",
        f"credential_leak_check={diagnostics.credential_leak_check}",
        "automatic_retry=0",
    ))


def settings(environ):
    __tracebackhide__ = True
    if environ.get("LINKLOOM_RUN_REAL_PROVIDER_SMOKE") != "1":
        pytest.skip("Gate A real smoke requires explicit opt-in.")
    model = environ.get("LINKLOOM_GATE_A_MODEL", MODEL)
    if not isinstance(model, str) or not re.fullmatch(r"gemini-[a-z0-9][a-z0-9.-]{0,90}", model):
        raise SmokeBlocked("Gate A model configuration is invalid.")
    key = environ.get("GEMINI_API_KEY")
    if not isinstance(key, str) or not key.strip():
        pytest.skip("Gate A requires GEMINI_API_KEY after opt-in.")
    return model, key


@contextmanager
def quiet_sdk():
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def official_client(key, factory=None, diagnostics=None):
    __tracebackhide__ = True
    try:
        if importlib.metadata.version("google-genai") != SDK_VERSION:
            raise SmokeBlocked("Gate A SDK version mismatch.")
        from google import genai
        from google.genai import types

        options = types.HttpOptions(
            timeout=30000, retry_options=types.HttpRetryOptions(attempts=1),
        )
        # 2.22.0 reads GOOGLE_API_KEY even with an explicit api_key. Disable
        # that fallback during construction without reading any host key.
        with quiet_sdk(), patch("google.genai._api_client.get_env_api_key", return_value=None):
            return (factory or genai.Client)(api_key=key, vertexai=False, http_options=options)
    except Exception as exception:
        _record_provider_exception(diagnostics, exception)
        raise SmokeBlocked("Gate A SDK client could not be constructed.") from None


class SmokeGeminiClient:
    """One synchronous SDK call per adapter call, with an absolute two-call cap."""

    def __init__(self, sdk, key, diagnostics=None):
        self._sdk = sdk
        self._key = key
        self._diagnostics = diagnostics
        self._native_model_content = None
        self.calls = 0

    def __repr__(self):
        return "SmokeGeminiClient(<private>)"

    @staticmethod
    def _first_native_model_content(response):
        """Keep the first SDK-native model turn only for this smoke transport."""
        candidates = getattr(response, "candidates", None)
        if not isinstance(candidates, (list, tuple)) or not candidates:
            return None
        return getattr(candidates[0], "content", None)

    def _second_turn_contents(self, contents):
        """Translate neutral context into Gemini's manual function-call history.

        LinkLoom persists only neutral ToolCall/ToolResult facts.  The smoke
        client retains the first SDK-native model Content in memory so the
        real SDK receives its required provider-native conversation turn.
        """
        if self._native_model_content is None:
            # Lightweight offline fakes intentionally need no SDK object.
            return contents
        try:
            from google.genai import types
            initial, neutral_call, neutral_result = contents
            neutral_function_call = neutral_call["parts"][0]["function_call"]
            neutral_function_response = neutral_result["parts"][0]["function_response"]
            native_calls = [
                part.function_call
                for part in self._native_model_content.parts
                if getattr(part, "function_call", None) is not None
            ]
            if len(native_calls) != 1:
                raise ValueError("expected one native function call")
            native_call = native_calls[0]
            if native_call.name != neutral_function_call["name"]:
                raise ValueError("native and neutral function names differ")
            native_id = native_call.id
            if native_id is not None and native_id != neutral_function_call["id"]:
                raise ValueError("native and neutral function call IDs differ")
            if neutral_function_response["name"] != native_call.name:
                raise ValueError("function response name differs")
            if native_id is not None and neutral_function_response["id"] != native_id:
                raise ValueError("function response ID differs")
            response = neutral_function_response["response"]
            if not isinstance(response, dict):
                raise ValueError("function response is not a JSON object")
            function_response = types.FunctionResponse(
                name=native_call.name,
                id=native_id,
                response=response,
            )
            return [
                types.Content.model_validate(initial),
                self._native_model_content,
                types.Content(
                    role="user",
                    parts=[types.Part(function_response=function_response)],
                ),
            ]
        except Exception:
            raise SmokeBlocked("Gate A second-turn history could not be reconstructed.") from None

    def generate_content(self, *, model, contents, config):
        __tracebackhide__ = True
        if self.calls >= 2:
            raise SmokeBlocked("Gate A provider call limit reached.")
        self.calls += 1
        if self._diagnostics is not None:
            self._diagnostics.provider_calls = self.calls
            self._diagnostics.stage = f"provider_call_{self.calls}_sent"
        try:
            sdk_contents = self._second_turn_contents(contents) if self.calls == 2 else contents
            with quiet_sdk():
                response = self._sdk.models.generate_content(
                    model=model, contents=sdk_contents,
                    config={**config, "max_output_tokens": 1024,
                            "automatic_function_calling": {"disable": True}},
                )
            data = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
            if self._key in json.dumps(data, ensure_ascii=False):
                if self._diagnostics is not None:
                    self._diagnostics.credential_leak_check = "FAIL"
                raise SmokeBlocked("Gate A credential leak blocked.")
            if self._diagnostics is not None:
                self._diagnostics.stage = f"provider_call_{self.calls}_response_received"
            if self.calls == 1:
                self._native_model_content = self._first_native_model_content(response)
            return response
        except Exception as exception:
            _record_provider_exception(self._diagnostics, exception)
            # Do not forward SDK errors/IDs containing unknown provider text.
            raise SmokeBlocked("Gate A provider attempt failed safely.") from None


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def run_scenario(client, model, diagnostics=None):
    __tracebackhide__ = True
    source = REPO / "tests" / "fixtures" / "sample_vault"
    original = snapshot(source)
    root = REPO / ".tmp" / ("gate-a-" + uuid4().hex)
    vault = root / "vault"
    shutil.copytree(source, vault)
    index = scan_vault(vault, root / "scan").index_path
    adapter = GeminiProviderAdapter(client, model_id=model)
    runtime = RuntimeEngine(vault, index, root / "checkpoints", trace_dir=root / "traces", model=adapter)
    status = runtime.start_multi_agent(RunRequest(
        request_id="gate-a", thread_id="gate-a", workflow="ask", query=PROMPT,
        max_provider_requests=2, max_steps=4, dry_run=True,
    ))
    state = runtime.checkpointer.get_latest("gate-a")
    _capture_runtime_progress(diagnostics, state)
    if state is None or status.status != "completed" or not state.result_ref:
        if diagnostics is not None and not diagnostics.provider_failed:
            diagnostics.stage = "runtime_finalization"
        raise SmokeBlocked("Gate A production execution did not complete.")
    result = json.loads((root / "checkpoints" / state.result_ref).read_text(encoding="utf-8"))
    records, ledger = state.model_executions, state.tool_ledger
    checks = [
        isinstance(runtime.model, GeminiProviderAdapter), client.calls == 2,
        state.status == result["status"] == "completed", len(records) == 2,
        len(ledger) == 1, not result["fallback_used"],
        not state.policy.write_capability, not result["evidence"],
        snapshot(source) == original, snapshot(vault) == original,
        result["tool_ledger"] == [r.to_dict() for r in ledger],
        result["agent_tasks"][0]["status"] == "completed",
    ]
    if not all(checks):
        raise SmokeBlocked("Gate A trajectory or read-only invariant failed.")
    first, final = records
    tool = ledger[0]
    if not (first.normalized_action["kind"] == "tool_call"
            and final.normalized_action["kind"] == "final"
            and final.status == "completed" and tool.tool_id == "search_notes"
            and tool.arguments.get("query") == QUERY and tool.status == "completed"
            and tool.result["status"] == "ok" and tool.result["value"] == []):
        raise SmokeBlocked("Gate A model did not follow the single no-match scenario.")
    artifacts = ModelArtifactStore(root / "checkpoints" / "models")
    for record in records:
        identity = {k: getattr(record, k) for k in ("run_id", "turn_id", "task_id", "agent_id", "sequence")}
        if (record.response_origin != "provider" or record.provider_metadata.get("provider") != "gemini"
                or record.provider_metadata.get("model") != model
                or (record.run_id, record.task_id, record.agent_id) != (tool.run_id, tool.task_id, tool.agent_id)):
            raise SmokeBlocked("Gate A provider identity mismatch.")
        for kind in ("request", "response", "observation"):
            ref, sha = getattr(record, kind + "_ref"), getattr(record, kind + "_sha256")
            if ref and sha and artifacts.read(ref, expected_sha256=sha)["runtime_identity"] != identity:
                raise SmokeBlocked("Gate A artifact identity mismatch.")
    request = artifacts.read(final.request_ref, expected_sha256=final.request_sha256)["model_request"]
    if (request["observation"] != tool.result or request["previous_tool_call"]["call_id"] != tool.call_id
            or first.normalized_action["tool_call"]["call_id"] != tool.call_id):
        raise SmokeBlocked("Gate A observation identity mismatch.")
    events = TraceReader(root / "traces").read_events(state.run_id)
    types = Counter(e.event_type for e in events if e.attributes.get("call_id") == tool.call_id)
    if types["tool.called"] != 1 or types["tool.completed"] != 1 or types["tool.failed"]:
        raise SmokeBlocked("Gate A trace correlation mismatch.")
    return root, {
        "provider": "gemini", "model": model, "provider_calls": client.calls,
        "tool_calls": len(ledger), "tool_names": [tool.tool_id],
        "agent_status": "completed", "response_origin": "provider",
        "usage_available": any(r.usage.get("input_tokens") is not None for r in records),
    }


def test_real_provider_smoke():
    __tracebackhide__ = True
    model, key = settings(os.environ)
    diagnostics = SmokeDiagnostics(model)
    sdk = None
    try:
        sdk = official_client(key, diagnostics=diagnostics)
        client = SmokeGeminiClient(sdk, key, diagnostics)
        root, summary = run_scenario(client, model, diagnostics)
        if any(key.encode() in data for data in snapshot(root).values()):
            raise SmokeBlocked("Gate A credential leak check failed.")
        summary["credential_leak_check"] = "PASS"
        print(json.dumps(summary, ensure_ascii=False))
    except Exception:
        print(format_failure_summary(diagnostics))
        pytest.fail("Gate A smoke failed; no automatic retry was attempted.", pytrace=False)
    finally:
        if sdk is not None:
            try:
                with quiet_sdk():
                    sdk.close()
            except Exception:
                pass


def test_offline_default_skip_never_reads_credentials():
    class FlagOnly(dict):
        def get(self, name, default=None):
            if name != "LINKLOOM_RUN_REAL_PROVIDER_SMOKE":
                raise AssertionError("Credential/config read before opt-in")
            return None
    with pytest.raises(pytest.skip.Exception):
        settings(FlagOnly())


def test_offline_missing_key_skips():
    with pytest.raises(pytest.skip.Exception):
        settings({"LINKLOOM_RUN_REAL_PROVIDER_SMOKE": "1"})


@pytest.mark.parametrize("model", ["", None, "bad/model", "gemini-x\n"])
def test_offline_invalid_model_fails_safely(model):
    with pytest.raises(SmokeBlocked, match="configuration is invalid"):
        settings({"LINKLOOM_RUN_REAL_PROVIDER_SMOKE": "1", "LINKLOOM_GATE_A_MODEL": model})


def test_offline_default_model():
    assert settings({"LINKLOOM_RUN_REAL_PROVIDER_SMOKE": "1", "GEMINI_API_KEY": "synthetic-key"})[0] == MODEL


def test_offline_client_retry_and_construction_error():
    pytest.importorskip("google.genai")
    seen = {}
    def factory(**kwargs):
        seen.update(kwargs)
        return object()
    official_client("synthetic-key", factory)
    assert seen["http_options"].retry_options.attempts == 1
    assert seen["vertexai"] is False
    def failing(**kwargs):
        raise RuntimeError("synthetic-key")
    with pytest.raises(SmokeBlocked) as caught:
        official_client("synthetic-key", failing)
    assert "synthetic-key" not in str(caught.value)


def test_offline_production_composition_and_fixture_copy():
    pytest.importorskip("google.genai")
    from google.genai import types

    def generate_content(*, model, contents, config):
        assert config["automatic_function_calling"]["disable"] is True
        # Validate the installed SDK's real typed input/output surface offline.
        types.GenerateContentConfig.model_validate(config)
        for content in contents:
            types.Content.model_validate(content)
        if len(contents) == 1:
            part = {"function_call": {"name": "search_notes", "args": {
                "query": QUERY, "source_context": {}, "limit": 1,
            }}}
        else:
            part = {"text": "未找到匹配笔记"}
        return types.GenerateContentResponse.model_validate({
            "candidates": [{"content": {"role": "model", "parts": [part]}, "finish_reason": "STOP"}],
            "response_id": "synthetic-response", "model_version": MODEL,
            "usage_metadata": {"prompt_token_count": 10, "candidates_token_count": 5, "total_token_count": 15},
        })
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    client = SmokeGeminiClient(sdk, "synthetic-key")
    _, summary = run_scenario(client, MODEL)
    assert summary["tool_calls"] == 1
    assert summary["provider_calls"] == 2
    assert summary["usage_available"] is True
    with pytest.raises(SmokeBlocked, match="limit"):
        client.generate_content(model=MODEL, contents=[], config={})


def test_offline_second_turn_rebuilds_native_manual_function_history():
    """The SDK boundary must receive Gemini's manual function-call history."""
    pytest.importorskip("google.genai")
    from google.genai import types

    captured: list[tuple[list[object], dict]] = []
    provider_call_id = "gemini-function-call-7"

    def generate_content(*, model, contents, config):
        captured.append((contents, config))
        if len(captured) == 1:
            return types.GenerateContentResponse.model_validate({
                "candidates": [{
                    "content": {
                        "role": "model",
                        "parts": [{"function_call": {
                            "id": provider_call_id,
                            "name": "search_notes",
                            "args": {"query": QUERY, "source_context": {}, "limit": 1},
                        }}],
                    },
                    "finish_reason": "STOP",
                }],
                "response_id": "synthetic-function-response",
                "model_version": MODEL,
            })
        return types.GenerateContentResponse.model_validate({
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": "未找到匹配笔记"}]},
                "finish_reason": "STOP",
            }],
            "response_id": "synthetic-final-response",
            "model_version": MODEL,
        })

    client = SmokeGeminiClient(
        SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
        "synthetic-key",
    )
    _, summary = run_scenario(client, MODEL)

    assert summary["provider_calls"] == 2
    second, second_config = captured[1]
    assert len(second) == 3
    assert all(isinstance(content, types.Content) for content in second)
    assert second[0].role == "user"
    assert second[1].role == "model"
    assert second[1].parts[0].function_call.id == provider_call_id
    assert second[1].parts[0].function_call.name == "search_notes"
    assert second[2].role == "user"
    function_response = second[2].parts[0].function_response
    assert function_response.id == provider_call_id
    assert function_response.name == "search_notes"
    assert function_response.response == {"status": "ok", "value": []}
    assert second_config["tools"][0]["function_declarations"][0]["name"] == "search_notes"


def test_offline_response_secret_is_blocked():
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: {"text": "synthetic-key"}))
    client = SmokeGeminiClient(sdk, "synthetic-key")
    with pytest.raises(SmokeBlocked) as caught:
        client.generate_content(model=MODEL, contents=[], config={})
    assert "synthetic-key" not in str(caught.value)


def test_offline_sdk_constructor_does_not_read_environment_credentials():
    pytest.importorskip("google.genai")
    # No host environment values are read by this probe; only credential-key
    # access is intercepted, and all other get() lookups return their default.
    def guarded_get(name, default=None):
        if name in {"GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"}:
            raise AssertionError("SDK attempted credential fallback")
        return default
    with patch("os.environ.get", side_effect=guarded_get):
        sdk = official_client("synthetic-key")
        sdk.close()


def test_offline_failure_summary_classifies_status_without_secret_leak():
    class SecretRateLimit(Exception):
        status_code = 429

    diagnostics = SmokeDiagnostics(MODEL)

    def generate_content(**kwargs):
        raise SecretRateLimit("synthetic-key must never appear in diagnostics")

    client = SmokeGeminiClient(
        SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
        "synthetic-key", diagnostics,
    )
    with pytest.raises(SmokeBlocked):
        client.generate_content(model=MODEL, contents=[], config={})

    summary = format_failure_summary(diagnostics)
    assert "stage=provider_call_1_sent" in summary
    assert "provider_calls=1" in summary
    assert "normalized_error_code=RATE_LIMIT" in summary
    assert "http_status=429" in summary
    assert "exception_type=unavailable" in summary
    assert "automatic_retry=0" in summary
    assert "synthetic-key" not in summary


def test_offline_second_turn_failure_summary_preserves_tool_progress_without_secret_leak():
    class SecretRequestShape(Exception):
        status_code = 400

    def generate_content(*, model, contents, config):
        if len(contents) == 1:
            return {"function_calls": [{"name": "search_notes", "args": {
                "query": QUERY, "source_context": {}, "limit": 1,
            }}], "finish_reason": "STOP"}
        raise SecretRequestShape("synthetic-key must never appear in diagnostics")

    diagnostics = SmokeDiagnostics(MODEL)
    client = SmokeGeminiClient(
        SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)),
        "synthetic-key", diagnostics,
    )
    with pytest.raises(SmokeBlocked):
        run_scenario(client, MODEL, diagnostics)

    summary = format_failure_summary(diagnostics)
    assert "stage=provider_call_2_sent" in summary
    assert "provider_calls=2" in summary
    assert "tool_calls=1" in summary
    assert "tool_names=search_notes" in summary
    assert "normalized_error_code=SDK_REQUEST_SHAPE" in summary
    assert "http_status=400" in summary
    assert "credential_leak_check=PASS" in summary
    assert "synthetic-key" not in summary


@pytest.mark.parametrize("behavior", ["direct_final", "wrong_tool", "invalid_args", "continues"])
def test_offline_wrong_model_behavior_fails_without_retry(behavior):
    def generate_content(**kwargs):
        if behavior == "direct_final":
            return {"text": "未找到匹配笔记", "finish_reason": "STOP"}
        return {"function_calls": [{
            "name": "read_verified_note" if behavior == "wrong_tool" else "search_notes",
            "args": ({"note_ref": "missing"} if behavior == "wrong_tool" else
                     {"query": QUERY, "source_context": {}, "limit": 0 if behavior == "invalid_args" else 1}),
        }], "finish_reason": "STOP"}
    client = SmokeGeminiClient(SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), "synthetic-key")
    with pytest.raises(SmokeBlocked):
        run_scenario(client, MODEL)
    assert client.calls <= 2
