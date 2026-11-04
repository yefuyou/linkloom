"""Bounded local cross-encoder inference isolated from the retrieval process."""
from __future__ import annotations
import atexit
from collections.abc import Sequence
import hashlib
import importlib.util
import multiprocessing as mp
import os
import re
import threading
import time
from typing import Any
from .base import RerankCandidate, RerankError, score_candidates
from .model_cache import BASE_MODEL, M3_MODEL, REVISIONS

_STOP = frozenset("a an and are as at be by did do does for from how in is it of on or the to was were what when which why with".split())
_JEV_TOKENIZER: Any = None
_JEV_PREPARE_LOCK = threading.Lock()
_JEV_PREPARE_THREAD: Any = None
_JEV_PREPARE_FAILURE = ""
_JEV_PREPARE_FAILED_AT = 0.0


def _window(query: str, text: str, tokenizer: Any, budget: int) -> tuple[str, int]:
    if len(tokenizer.encode(text, add_special_tokens=False)) <= budget:
        return text, 0
    terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", query.casefold())) - _STOP
    lines = text.splitlines(keepends=True)
    offset = 0
    best = (0, 0)
    for line in lines:
        overlap = len(terms.intersection(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", line.casefold())))
        if overlap > best[0]:
            best = overlap, offset
        offset += len(line)
    start = max(0, text.rfind("\n", 0, max(0, best[1] - 80)) + 1)
    return text[start:], start


def prepare_texts(query: str, texts: Sequence[str], tokenizer: Any) -> tuple[str, list[str], list[dict[str, Any]]]:
    """Use actual tokenizer limits, preserving the source text outside scoring."""
    query_ids = tokenizer.encode(query, add_special_tokens=False)[:64]
    bounded_query = tokenizer.decode(query_ids, skip_special_tokens=True)
    while query_ids and len(tokenizer.encode(bounded_query, add_special_tokens=False)) > 64:
        query_ids = query_ids[:-1]
        bounded_query = tokenizer.decode(query_ids, skip_special_tokens=True)
    query_ids = tokenizer.encode(bounded_query, add_special_tokens=False)
    budget = 256 - len(query_ids) - tokenizer.num_special_tokens_to_add(pair=True)
    if budget <= 0:
        raise RerankError("input_budget_unavailable")
    fragments, details = [], []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise RerankError("missing_text")
        full_ids = tokenizer.encode(text, add_special_tokens=False)
        selected, start = _window(bounded_query, text, tokenizer, budget)
        ids = tokenizer.encode(selected, add_special_tokens=False)[:budget]
        fragment = tokenizer.decode(ids, skip_special_tokens=True)
        # Decode/encode need not be perfectly reversible for every tokenizer.
        while ids and len(tokenizer(bounded_query, fragment, truncation=False)["input_ids"]) > 256:
            ids = ids[:-1]
            fragment = tokenizer.decode(ids, skip_special_tokens=True)
        pair_tokens = len(tokenizer(bounded_query, fragment, truncation=False)["input_ids"])
        if not fragment or pair_tokens > 256:
            raise RerankError("input_budget_unavailable")
        fragments.append(fragment)
        details.append({"char_start": start, "original_tokens": len(full_ids), "fragment_tokens": len(ids), "pair_tokens": pair_tokens, "truncated": len(full_ids) > len(ids), "fragment_sha256": hashlib.sha256(fragment.encode("utf-8")).hexdigest()})
    return bounded_query, fragments, details


def prepare_jev_inputs(query: str, candidates: Sequence[RerankCandidate]) -> tuple[str, list[str]]:
    global _JEV_TOKENIZER, _JEV_PREPARE_THREAD, _JEV_PREPARE_FAILURE, _JEV_PREPARE_FAILED_AT
    if _JEV_TOKENIZER is None:
        # A first remote-mode retrieval never waits for a download or heavy import.
        with _JEV_PREPARE_LOCK:
            if _JEV_PREPARE_FAILURE and time.monotonic() - _JEV_PREPARE_FAILED_AT < 60:
                raise RerankError(_JEV_PREPARE_FAILURE)
            if _JEV_PREPARE_THREAD is None or not _JEV_PREPARE_THREAD.is_alive():
                def prepare_tokenizer():
                    global _JEV_TOKENIZER, _JEV_PREPARE_FAILURE, _JEV_PREPARE_FAILED_AT
                    try:
                        from .model_cache import load_tokenizer
                        _JEV_TOKENIZER = load_tokenizer()
                        _JEV_PREPARE_FAILURE = ""
                    except Exception:
                        _JEV_PREPARE_FAILURE = "tokenizer_unavailable"
                        _JEV_PREPARE_FAILED_AT = time.monotonic()
                _JEV_PREPARE_THREAD = threading.Thread(target=prepare_tokenizer, name="linkloom-rerank-tokenizer", daemon=True)
                _JEV_PREPARE_THREAD.start()
        if _JEV_TOKENIZER is None:
            raise RerankError("model_preparing")
    bounded_query, fragments, _ = prepare_texts(query, [c.text for c in candidates], _JEV_TOKENIZER)
    return bounded_query, fragments


def _worker(connection: Any, options: dict[str, Any]) -> None:
    """No caller evidence or credentials cross this worker's input boundary."""
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "8")
    os.environ.setdefault("MKL_NUM_THREADS", "8")
    stage = "runtime_import"
    try:
        started = time.perf_counter()
        import numpy as np
        # Load the CPU provider before torch adds its Windows DLL directories.
        import onnxruntime as ort
        import torch
        from .model_cache import ensure_onnx, load_tokenizer, resolve_snapshot
        stage = "runtime_configuration"
        torch.set_num_threads(8)
        torch.set_num_interop_threads(1)
        model_name, device = options["model_name"], options["device"]
        attempts = []
        if device == "auto":
            device = "cpu"
            try:
                if torch.cuda.is_available():
                    torch.cuda.get_device_name(0)
                    probe = torch.ones((16, 16), device="cuda", dtype=torch.float16)
                    _ = probe @ probe
                    torch.cuda.synchronize()
                    del probe, _
                    device = "cuda"
                else:
                    attempts.append({"backend": "cuda", "reason": "cuda_unavailable"})
            except Exception:
                attempts.append({"backend": "cuda", "reason": "cuda_unavailable"})
        engine = options["engine"]
        if engine == "auto":
            engine = "torch" if device == "cuda" or model_name == M3_MODEL else "onnx"
        stage = "tokenizer_load"
        tokenizer = load_tokenizer(model_name, options["model_dir"])
        model = session = None
        if engine == "torch":
            stage = "torch_model_load"
            from transformers import AutoModelForSequenceClassification
            snapshot = resolve_snapshot(model_name, options["model_dir"])
            try:
                model = AutoModelForSequenceClassification.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=False, dtype=torch.float16 if device == "cuda" else torch.float32).to(device).eval()
            except Exception:
                if device != "cuda" or model_name != BASE_MODEL:
                    raise
                attempts.append({"backend": "cuda", "reason": "model_load_failed"})
                torch.cuda.empty_cache()
                device, engine = "cpu", "onnx"
        if engine == "onnx":
            stage = "onnx_preparation"
            import onnxruntime as ort
            path = options["onnx_path"] or str(ensure_onnx(model_name, options["model_dir"]))
            settings = ort.SessionOptions()
            settings.intra_op_num_threads = 8
            settings.inter_op_num_threads = 1
            settings.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            settings.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            settings.add_session_config_entry("session.intra_op.allow_spinning", "0")
            stage = "onnx_session_load"
            session = ort.InferenceSession(path, sess_options=settings, providers=["CPUExecutionProvider"])
        detail = {"model": model_name, "revision": REVISIONS[model_name], "device": device, "engine": engine, "first_load_ms": (time.perf_counter() - started) * 1000, "attempts": attempts}
        connection.send({"kind": "ready", "detail": detail})
    except RerankError as error:
        connection.send({"kind": "error", "reason": error.reason})
        connection.close()
        return
    except ImportError:
        connection.send({"kind": "error", "reason": "missing_dependency"})
        connection.close()
        return
    except OSError as error:
        reason = "cache_space_insufficient" if error.errno == 28 else "cache_io_failed"
        connection.send({"kind": "error", "reason": reason, "stage": stage, "error_type": type(error).__name__})
        connection.close()
        return
    except Exception as error:
        connection.send({"kind": "error", "reason": "model_load_failed", "stage": stage, "error_type": type(error).__name__, "errno": getattr(error, "errno", None), "winerror": getattr(error, "winerror", None)})
        connection.close()
        return
    while True:
        try:
            message = connection.recv()
        except (EOFError, OSError):
            break
        if message.get("kind") == "close":
            break
        try:
            query, fragments, input_details = prepare_texts(message["query"], message["texts"], tokenizer)
            values = []
            for offset in range(0, len(fragments), 8):
                batch = fragments[offset:offset + 8]
                if engine == "onnx":
                    encoded = tokenizer([query] * len(batch), batch, padding=True, truncation="only_second", max_length=256, return_tensors="np")
                    names = {item.name for item in session.get_inputs()}
                    logits = session.run(None, {key: value for key, value in encoded.items() if key in names})[0]
                    values.extend(np.asarray(logits).reshape(-1).astype(float).tolist())
                else:
                    encoded = tokenizer([query] * len(batch), batch, padding=True, truncation="only_second", max_length=256, return_tensors="pt").to(device)
                    with torch.inference_mode():
                        logits = model(**encoded).logits
                    if device == "cuda":
                        torch.cuda.synchronize()
                    values.extend(logits.float().cpu().reshape(-1).tolist())
            connection.send({"kind": "scores", "scores": values, "input_details": input_details})
        except Exception as error:
            reason = error.reason if isinstance(error, RerankError) else "inference_failed"
            if device == "cuda" and "out of memory" in str(error).lower():
                reason = "cuda_oom"
            connection.send({"kind": "error", "reason": reason})
    connection.close()


class LocalCrossEncoder:
    backend = "local"
    enabled = True

    def __init__(self, model_name: str = BASE_MODEL, *, model_dir: str | None = None, onnx_path: str | None = None, device: str = "auto", engine: str = "auto", timeout_s: float = 5.0, preparation_timeout_s: float = 600.0) -> None:
        if model_name not in REVISIONS or device not in {"auto", "cpu", "cuda"} or engine not in {"auto", "onnx", "torch"}:
            raise ValueError("unsupported local reranker configuration")
        if timeout_s <= 0 or preparation_timeout_s <= 0:
            raise ValueError("timeouts must be positive")
        self.options = {"model_name": model_name, "model_dir": model_dir, "onnx_path": onnx_path, "device": device, "engine": engine}
        self.timeout_s, self.preparation_timeout_s = timeout_s, preparation_timeout_s
        self._lock = threading.RLock()
        self._process = self._connection = None
        self._ready = False
        self._preparation_timer = None
        self._started = self._failed_at = 0.0
        self._failure = ""
        self.last_trace: dict[str, Any] = {}
        atexit.register(self.close)

    def _start(self) -> None:
        for package in ("torch", "transformers", "onnxruntime", "filelock", "psutil"):
            if importlib.util.find_spec(package) is None:
                raise RerankError("missing_dependency")
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(child, self.options), daemon=True)
        try:
            process.start()
        except Exception:
            parent.close()
            child.close()
            raise RerankError("worker_exited") from None
        child.close()
        self._connection, self._process = parent, process
        self._started = time.monotonic()
        self._preparation_timer = threading.Timer(self.preparation_timeout_s, self._expire_preparation)
        self._preparation_timer.daemon = True
        self._preparation_timer.start()

    def _expire_preparation(self) -> None:
        # Reap a stalled download/export even if no further retrieval arrives.
        with self._lock:
            if self._process is None or self._ready:
                return
            try:
                self._ready_or_raise()
            except RerankError as error:
                if error.reason == "model_preparing":
                    try:
                        self._fail("model_prepare_timeout")
                    except RerankError:
                        pass

    def _ready_or_raise(self, wait: float = 0.0) -> None:
        if self._failed_at and time.monotonic() - self._failed_at < 60:
            raise RerankError(self._failure)
        if self._process is None:
            self._start()
        if self._ready:
            return
        if self._connection.poll(wait):
            try:
                message = self._connection.recv()
            except (EOFError, OSError):
                self._fail("worker_exited")
            if message.get("kind") == "ready":
                self.last_trace = dict(message["detail"])
                self._ready = True
                if self._preparation_timer is not None:
                    self._preparation_timer.cancel()
                return
            self.last_trace.update(failure_stage=message.get("stage"), error_type=message.get("error_type"), errno=message.get("errno"), winerror=message.get("winerror"))
            self._fail(message.get("reason", "model_load_failed"))
        if not self._process.is_alive():
            self._fail("worker_exited")
        if time.monotonic() - self._started > self.preparation_timeout_s:
            self._fail("model_prepare_timeout")
        raise RerankError("model_preparing")

    def _fail(self, reason: str) -> None:
        self.close()
        self._failure, self._failed_at = reason, time.monotonic()
        raise RerankError(reason)

    def prepare(self, timeout_s: float | None = None, *, cancel: threading.Event | None = None) -> dict[str, Any]:
        """Explicit blocking preparation for startup tools and benchmark preflight."""
        deadline = time.monotonic() + (timeout_s or self.preparation_timeout_s)
        with self._lock:
            while True:
                if cancel is not None and cancel.is_set():
                    self.close()
                    raise RerankError("preparation_cancelled")
                try:
                    self._ready_or_raise(min(0.1, max(0, deadline - time.monotonic())))
                    return dict(self.last_trace)
                except RerankError as error:
                    if error.reason != "model_preparing":
                        raise
                    if time.monotonic() >= deadline:
                        self._fail("model_prepare_timeout")

    def rerank(self, query: str, candidates: Sequence[RerankCandidate]):
        if not candidates or not query.strip():
            return [c.evidence for c in candidates]
        if len(candidates) > 20:
            raise RerankError("candidate_limit")
        deadline = time.monotonic() + self.timeout_s
        if not self._lock.acquire(timeout=self.timeout_s):
            raise RerankError("inference_timeout")
        try:
            self._ready_or_raise()
            try:
                self._connection.send({"kind": "score", "query": query, "texts": [c.text for c in candidates]})
                if not self._connection.poll(max(0.0, deadline - time.monotonic())):
                    self._fail("inference_timeout")
                message = self._connection.recv()
            except (EOFError, OSError):
                self._fail("worker_exited")
            if message.get("kind") != "scores":
                reason = message.get("reason", "inference_failed")
                # A CUDA runtime/OOM failure cannot poison the following CPU attempt.
                if self.options["device"] != "cpu" and reason in {"cuda_oom", "inference_failed"}:
                    self.close()
                    self.options.update(device="cpu", engine="onnx")
                    self._failure = ""
                raise RerankError(reason)
            details = message["input_details"]
            self.last_trace.update(token_counts=[d["pair_tokens"] for d in details], truncated_count=sum(d["truncated"] for d in details), input_details=details)
            return score_candidates(candidates, message["scores"], backend="local")
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            process, connection = self._process, self._connection
            if self._preparation_timer is not None:
                self._preparation_timer.cancel()
                self._preparation_timer = None
            self._process = self._connection = None
            self._ready = False
            if connection is not None:
                try:
                    connection.send({"kind": "close"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
                connection.close()
            if process is not None:
                pid = getattr(process, "pid", None)
                if pid is not None:
                    try:
                        import psutil
                        # Only descendants of this owned worker can be terminated.
                        for child in psutil.Process(pid).children(recursive=True):
                            try:
                                child.kill()
                            except psutil.Error:
                                pass
                    except psutil.Error:
                        pass
                process.join(0.05)
                if process.is_alive():
                    process.terminate()
                    process.join(1.0)
                if process.is_alive():
                    process.kill()
                    process.join(1.0)

_DEFAULT_LOCK = threading.Lock()
_DEFAULT_LOCAL: dict[str, LocalCrossEncoder] = {}


def get_default_local_reranker() -> LocalCrossEncoder:
    """Reuse one bounded inference worker across workspace/runtime instances."""
    key = os.environ.get("LINKLOOM_RERANK_CACHE_DIR", "")
    with _DEFAULT_LOCK:
        if key not in _DEFAULT_LOCAL:
            _DEFAULT_LOCAL[key] = LocalCrossEncoder()
        return _DEFAULT_LOCAL[key]
