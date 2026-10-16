"""A small, serial, budgeted boundary for the authorized Gemini experiment.

The caller supplies an already configured SDK client.  This module owns only
the fixed model policy, request admission, bounded accounting, and a new JSONL
ledger.  It never creates credentials, retries a call, or reads LinkLoom's
other ledgers.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any

from linkloom.agents.providers.gemini_api import _exception_code
from linkloom.agents.providers.retry_policy import classify_retryable_failure
from linkloom.runtime.errors import ProviderOutcomeUnknownError


MODEL = "gemini-3.8-flash"
INPUT_PAYLOAD_BYTE_BOUND = 49_152
PROTOCOL_TOKEN_ALLOWANCE = 16_384
INPUT_TOKEN_BOUND = INPUT_PAYLOAD_BYTE_BOUND + PROTOCOL_TOKEN_ALLOWANCE
MAX_OUTPUT_TOKENS = 8_192
REQUEST_TIMEOUT_SECONDS = 130
INPUT_PRICE_USD_PER_MILLION = 0.75
OUTPUT_PRICE_USD_PER_MILLION = 3.75
RESERVATION_USD = round(
    (INPUT_TOKEN_BOUND * INPUT_PRICE_USD_PER_MILLION
     + MAX_OUTPUT_TOKENS * OUTPUT_PRICE_USD_PER_MILLION) / 1_000_000,
    12,
)
SCHEMA_VERSION = "adaptive-provider-ledger/v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ERROR_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_RESPONSE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_EPSILON = 1e-12
_PHASES = {"A", "B", "C"}
_FORBIDDEN = {
    re.sub(r"[^a-z0-9]", "", name.casefold())
    for name in (
        "inline_data", "file_data", "media", "media_resolution", "video_metadata",
        "audio", "video", "image", "image_url", "google_search",
        "google_search_retrieval", "url_context", "code_execution", "computer_use",
        "file_search", "hosted_tool", "hosted_tools", "cached_content", "cache",
        "grounding",
    )
}
_ALLOWED_FUNCTIONS = {"search_notes", "read_verified_note"}


class AdaptiveProviderError(RuntimeError):
    """A safe boundary error with a stable code and no provider details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderStopped(AdaptiveProviderError):
    pass


class BudgetExceeded(AdaptiveProviderError):
    pass


class DeadlineExceeded(AdaptiveProviderError):
    pass


class TrialLimitExceeded(AdaptiveProviderError):
    pass


class ConcurrentRequestError(AdaptiveProviderError):
    pass


class LedgerError(AdaptiveProviderError):
    pass


class StrictProviderOutcomeUnknown(ProviderOutcomeUnknownError):
    """Typed stop understood by the runtime when a result is not trustworthy."""

    def __init__(self, code: str = "PROVIDER_OUTCOME_UNCERTAIN") -> None:
        super().__init__()
        self.code = code


_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


class _OSLease:
    """Non-blocking process and OS lease, shared by provider instances."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.process_lock = _lock_for(path)
        self.fd: int | None = None

    def acquire(self) -> None:
        if not self.process_lock.acquire(False):
            raise ConcurrentRequestError("CONCURRENT_REQUEST")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except Exception:
                os.close(fd)
                raise
            self.fd = fd
        except (OSError, BlockingIOError) as exc:
            self.process_lock.release()
            raise ConcurrentRequestError("CONCURRENT_REQUEST") from exc
        except Exception:
            self.process_lock.release()
            raise

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is None:
            return
        try:
            if os.name == "nt":
                try:
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
        finally:
            self.process_lock.release()


def _field(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, Mapping):
            item = value.get(name)
        else:
            try:
                item = getattr(value, name)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                item = None
        if item is not None:
            return item
    return default


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{name}_INVALID")
    return value


def _json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_NOT_JSON") from exc


def _utc(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).isoformat()


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and re.sub(r"[^a-z0-9]", "", key.casefold()) in _FORBIDDEN:
                return True
            if _forbidden(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_forbidden(item) for item in value)
    return False


def _allowed_tools(value: Any) -> list[Any]:
    """Accept only runtime-owned pure retrieval function declarations."""
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise ValueError("TOOLS_DISABLED")
    wrapper = value[0]
    declarations = wrapper.get("function_declarations")
    if set(wrapper) != {"function_declarations"} or not isinstance(declarations, list):
        raise ValueError("HOSTED_TOOLS_DISABLED")
    for declaration in declarations:
        if not isinstance(declaration, Mapping) or declaration.get("name") not in _ALLOWED_FUNCTIONS:
            raise ValueError("TOOL_NOT_ALLOWED")
        if _forbidden(declaration):
            raise ValueError("HOSTED_TOOLS_DISABLED")
    return deepcopy(value)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(payload, "REQUEST").encode("utf-8")).hexdigest()


def _response_id(response: Any) -> str | None:
    value = _field(response, "response_id", "responseId", "id")
    return value if isinstance(value, str) and _RESPONSE_ID.fullmatch(value) else None


def _usage(response: Any) -> tuple[dict[str, Any] | None, str]:
    metadata = _field(response, "usage_metadata", "usageMetadata", "usage")
    if metadata is None:
        return None, "unknown"
    direct = _finite(_field(metadata, "estimated_cost_usd", "cost_usd", "total_cost_usd"))
    inputs = _nonnegative_int(_field(metadata, "prompt_token_count", "promptTokenCount", "input_tokens"))
    outputs = _nonnegative_int(_field(metadata, "candidates_token_count", "candidatesTokenCount", "output_tokens"))
    thoughts_raw = _field(metadata, "thoughts_token_count", "thoughtsTokenCount", "thinking_tokens")
    thoughts = _nonnegative_int(thoughts_raw) if thoughts_raw is not None else 0
    total = _nonnegative_int(_field(metadata, "total_token_count", "totalTokenCount", "total_tokens"))
    if inputs is None or outputs is None:
        if direct is None:
            return None, "unknown"
        return ({"cost_usd": round(direct, 12)}, "known") if direct <= RESERVATION_USD + _EPSILON else (None, "out_of_bounds")
    billable_output = outputs + thoughts
    if total is not None and total != inputs + billable_output:
        return None, "unknown"
    if inputs > INPUT_TOKEN_BOUND or billable_output > MAX_OUTPUT_TOKENS:
        return None, "out_of_bounds"
    actual = (inputs * INPUT_PRICE_USD_PER_MILLION + billable_output * OUTPUT_PRICE_USD_PER_MILLION) / 1_000_000
    if direct is not None and abs(direct - actual) > max(_EPSILON, actual * 0.02):
        return None, "unknown"
    if actual > RESERVATION_USD + _EPSILON:
        return None, "out_of_bounds"
    return {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "thinking_tokens": thoughts,
        "billable_output_tokens": billable_output,
        "total_tokens": total if total is not None else inputs + billable_output,
        "cost_usd": round(actual, 12),
    }, "known"


def _exception_chain(error: BaseException) -> list[BaseException]:
    result: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while isinstance(current, BaseException) and id(current) not in seen and len(result) < 8:
        seen.add(id(current))
        result.append(current)
        current = current.__cause__ or (current.__context__ if not current.__suppress_context__ else None)
    return result


def _classify(error: BaseException) -> tuple[str, str, bool]:
    try:
        code, outcome = _exception_code(error)
    except Exception:
        code, outcome = "MODEL_UNKNOWN_FAILURE", "unknown_provider_outcome"
    try:
        transport = classify_retryable_failure(error, {})
    except Exception:
        transport = None
    names = {type(item).__name__ for item in _exception_chain(error)}
    safe_code = code if isinstance(code, str) and _ERROR_ID.fullmatch(code) else "MODEL_UNKNOWN_FAILURE"
    if transport in {"TRANSPORT_DNS", "TRANSPORT_PROXY_CONNECT"}:
        return safe_code, transport, False
    if transport == "TRANSPORT_CONNECT" and not names.intersection(
        {"ConnectionResetError", "RemoteDisconnected", "ReadError", "WriteError"}
    ):
        return safe_code, transport, False
    strict = outcome == "unknown_provider_outcome"
    if transport == "TRANSPORT_TIMEOUT" or names.intersection(
        {"ReadError", "ReadTimeout", "WriteError", "WriteTimeout", "RemoteProtocolError", "ConnectionResetError"}
    ):
        strict = True
    return safe_code, transport or "UNKNOWN", strict


class _Task(AbstractContextManager["AdaptiveProvider"]):
    def __init__(self, provider: "AdaptiveProvider", case: str, trial: int, phase: str) -> None:
        self.provider, self.case, self.trial, self.phase = provider, case, trial, phase
        self.entered = False

    def __enter__(self) -> "AdaptiveProvider":
        self.provider._begin_task(self.case, self.trial, self.phase)
        self.entered = True
        return self.provider

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self.entered:
            self.provider._end_task()
            self.entered = False
        return False


class AdaptiveProvider:
    """Serial Gemini 3.8 Flash generation with hard budget accounting."""

    model = MODEL

    def __init__(
        self,
        sdk: Any,
        ledger_path: str | os.PathLike[str],
        experiment_id: str = "adaptive-provider",
        max_requests: int = 400,
        max_usd: float = 15,
        max_seconds: float = 10_800,
    ) -> None:
        if sdk is None:
            raise ValueError("SDK_REQUIRED")
        if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
            raise ValueError("MAX_REQUESTS_INVALID")
        if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or not math.isfinite(float(max_usd)) or max_usd < 0:
            raise ValueError("MAX_USD_INVALID")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(float(max_seconds)) or max_seconds <= 0:
            raise ValueError("MAX_SECONDS_INVALID")
        self.sdk, self.models = sdk, self
        self.experiment_id = _identifier(experiment_id, "EXPERIMENT_ID")
        self.max_requests, self.max_usd, self.max_seconds = max_requests, float(max_usd), float(max_seconds)
        self.reservation_usd = RESERVATION_USD
        self.request_timeout_seconds = REQUEST_TIMEOUT_SECONDS
        self.sdk_retry_attempts = 1
        self.ledger_path = Path(ledger_path)
        if self.ledger_path.name in {"", ".", ".."}:
            raise ValueError("LEDGER_PATH_INVALID")
        self._lease = _OSLease(self.ledger_path.with_name(self.ledger_path.name + ".lease"))
        self._generation_lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._active: tuple[str, int, str, int] | None = None
        self._task_terminal = False
        self._stopped = False
        self._stop_reason: str | None = None
        self._load_ledger()
        self._rebuild()
        if self._pending:
            self._stopped, self._stop_reason = True, "UNCLOSED_REQUEST_STOP"
            raise ProviderStopped(self._stop_reason)
        if self._strict_streak >= 2 or self._ledger_stop:
            self._stopped = True
            self._stop_reason = self._ledger_stop or "TWO_STRICT_UNCERTAIN_STOP"
            raise ProviderStopped(self._stop_reason)

    def _load_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.ledger_path.exists():
            try:
                self.ledger_path.open("x", encoding="utf-8").close()
            except FileExistsError:
                pass
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise LedgerError("LEDGER_READ_FAILED") from exc
        if not lines:
            self._append("experiment_created", experiment_id=self.experiment_id, model=MODEL,
                         max_requests=self.max_requests, max_usd=round(self.max_usd, 12),
                         max_seconds=self.max_seconds, input_payload_byte_bound=INPUT_PAYLOAD_BYTE_BOUND,
                         protocol_token_allowance=PROTOCOL_TOKEN_ALLOWANCE,
                         input_token_bound=INPUT_TOKEN_BOUND, max_output_tokens=MAX_OUTPUT_TOKENS,
                         request_timeout_seconds=REQUEST_TIMEOUT_SECONDS, sdk_retry_attempts=1)
            return
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, ValueError) as exc:
                raise LedgerError("LEDGER_CORRUPT") from exc
            if not isinstance(row, dict) or not isinstance(row.get("event"), str):
                raise LedgerError("LEDGER_CORRUPT")
            records.append(row)
        first = records[0]
        if first.get("event") != "experiment_created" or first.get("schema_version") != SCHEMA_VERSION:
            raise LedgerError("LEDGER_NOT_ADAPTIVE")
        if first.get("experiment_id") != self.experiment_id or first.get("model") != MODEL:
            raise LedgerError("LEDGER_EXPERIMENT_MISMATCH")
        for key, expected in (("max_requests", self.max_requests), ("max_usd", round(self.max_usd, 12)), ("max_seconds", self.max_seconds)):
            if first.get(key) != expected:
                raise LedgerError("LEDGER_BUDGET_MISMATCH")
        self._records = records

    def _append(self, event: str, **fields: Any) -> dict[str, Any]:
        row = {"schema_version": SCHEMA_VERSION, "event": event, "utc": _utc(), **fields}
        try:
            with self.ledger_path.open("a", encoding="utf-8") as stream:
                stream.write(_json(row, "LEDGER") + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise LedgerError("LEDGER_APPEND_FAILED") from exc
        self._records.append(row)
        return row

    def _rebuild(self) -> None:
        reservations: dict[str, dict[str, Any]] = {}
        finishes: dict[str, dict[str, Any]] = {}
        self._pending: set[str] = set()
        self._trials: dict[str, set[int]] = {}
        self._terminals: set[tuple[str, int]] = set()
        self._completed_phases: set[tuple[str, int, str]] = set()
        self._first_at: datetime | None = None
        self._strict_count = self._strict_streak = 0
        self._last_outcome: str | None = None
        self._ledger_stop: str | None = None
        for row in self._records:
            event, request_id = row.get("event"), row.get("request_id")
            if event == "request_reserved":
                if not isinstance(request_id, str) or request_id in reservations:
                    raise LedgerError("LEDGER_CORRUPT")
                reservation = _finite(row.get("reservation_usd"))
                if reservation is None or reservation > self.reservation_usd + _EPSILON:
                    raise LedgerError("LEDGER_CORRUPT")
                reservations[request_id] = row
                self._pending.add(request_id)
                timestamp = _parse_utc(row.get("utc"))
                if timestamp and (self._first_at is None or timestamp < self._first_at):
                    self._first_at = timestamp
                case, trial = row.get("case_id"), row.get("trial")
                if isinstance(case, str) and isinstance(trial, int) and not isinstance(trial, bool):
                    self._trials.setdefault(case, set()).add(trial)
            elif event == "request_finished":
                if not isinstance(request_id, str) or request_id not in reservations or request_id in finishes:
                    raise LedgerError("LEDGER_CORRUPT")
                finishes[request_id] = row
                self._pending.discard(request_id)
                outcome = row.get("outcome")
                self._last_outcome = outcome if isinstance(outcome, str) else None
                case, trial, phase = row.get("case_id"), row.get("trial"), row.get("phase")
                if isinstance(case, str) and isinstance(trial, int) and isinstance(phase, str):
                    self._completed_phases.add((case, trial, phase))
                if outcome == "provider_outcome_uncertain":
                    self._strict_count += 1
                    self._strict_streak += 1
                    if isinstance(case, str) and isinstance(trial, int):
                        self._terminals.add((case, trial))
                else:
                    self._strict_streak = 0
            elif event == "experiment_stopped":
                reason = row.get("reason")
                self._ledger_stop = reason if isinstance(reason, str) else "EXPERIMENT_STOPPED"
        self._reservations, self._finishes = reservations, finishes
        self._request_count = len(reservations)
        self._committed = self._released = 0.0
        self._usage_known = 0
        for request_id, row in reservations.items():
            reserved = float(row["reservation_usd"])
            finished = finishes.get(request_id)
            actual = _finite(finished.get("actual_cost_usd")) if finished else None
            if finished and finished.get("usage_status") == "known" and actual is not None:
                self._committed += actual
                self._released += max(0.0, reserved - actual)
                self._usage_known += 1
            else:
                self._committed += reserved

    def _elapsed(self) -> float:
        return max(0.0, (datetime.now(UTC) - self._first_at).total_seconds()) if self._first_at else 0.0

    def _deadline(self) -> None:
        if self._first_at is None:
            if self.max_seconds <= REQUEST_TIMEOUT_SECONDS:
                raise DeadlineExceeded("DEADLINE_TOO_SHORT")
        elif self.max_seconds - self._elapsed() <= REQUEST_TIMEOUT_SECONDS:
            raise DeadlineExceeded("DEADLINE_REQUEST_TIMEOUT")

    def _running(self) -> None:
        if self._stopped:
            raise ProviderStopped(self._stop_reason or "PROVIDER_STOPPED")

    def _begin_task(self, case: str, trial: int, phase: str) -> None:
        self._running()
        case = _identifier(case, "CASE_ID")
        if isinstance(trial, bool) or not isinstance(trial, int) or not 1 <= trial <= 3:
            raise TrialLimitExceeded("CASE_TRIAL_LIMIT")
        if not isinstance(phase, str) or phase not in _PHASES:
            raise ValueError("PHASE_INVALID")
        if (case, trial) in self._terminals:
            raise StrictProviderOutcomeUnknown("TASK_PROVIDER_OUTCOME_UNCERTAIN")
        if (case, trial, phase) in self._completed_phases:
            raise TrialLimitExceeded("TASK_PHASE_ALREADY_COMPLETED")
        trials = self._trials.get(case, set())
        if trial not in trials and len(trials) >= 3:
            raise TrialLimitExceeded("CASE_TRIAL_LIMIT")
        if self._active is not None:
            raise ConcurrentRequestError("CONCURRENT_REQUEST")
        self._lease.acquire()
        if self._active is not None:
            self._lease.release()
            raise ConcurrentRequestError("CONCURRENT_REQUEST")
        self._active = (case, trial, phase, threading.get_ident())
        self._task_terminal = False

    def _end_task(self) -> None:
        self._active, self._task_terminal = None, False
        self._lease.release()

    def task(self, case_id: str, trial: int, phase: str) -> _Task:
        return _Task(self, case_id, trial, phase)

    def _normalize(self, model: str, contents: Any, config: Any) -> dict[str, Any]:
        if model != MODEL:
            raise ValueError("MODEL_PINNED")
        if not isinstance(config, Mapping):
            raise ValueError("CONFIG_INVALID")
        if _forbidden(contents) or _forbidden(config):
            raise ValueError("TEXT_ONLY")
        normalized = deepcopy(dict(config))
        output = normalized.get("max_output_tokens", MAX_OUTPUT_TOKENS)
        if isinstance(output, bool) or not isinstance(output, int) or not 1 <= output <= MAX_OUTPUT_TOKENS:
            raise ValueError("OUTPUT_TOKEN_BOUND")
        tools = _allowed_tools(normalized.get("tools", []))
        automatic = normalized.get("automatic_function_calling", {"disable": True})
        if not isinstance(automatic, Mapping) or automatic.get("disable") is not True:
            raise ValueError("AUTOMATIC_FUNCTION_CALLING_DISABLED")
        normalized.update({"max_output_tokens": output, "tools": tools,
                           "automatic_function_calling": {"disable": True}})
        payload = {"model": MODEL, "contents": deepcopy(contents), "config": normalized}
        if len(_json(payload, "REQUEST").encode("utf-8")) > INPUT_PAYLOAD_BYTE_BOUND:
            raise ValueError("INPUT_BYTE_BOUND")
        return payload

    def _admit(self, payload: Mapping[str, Any]) -> tuple[str, str | None, int | None, str | None]:
        self._running()
        if self._task_terminal:
            raise StrictProviderOutcomeUnknown("TASK_PROVIDER_OUTCOME_UNCERTAIN")
        if self._request_count >= self.max_requests:
            raise BudgetExceeded("REQUEST_LIMIT")
        self._deadline()
        if self._committed + self.reservation_usd > self.max_usd + _EPSILON:
            raise BudgetExceeded("COST_LIMIT")
        sequence = self._request_count + 1
        request_id = f"{self.experiment_id}-{sequence:06d}"
        case = self._active[0] if self._active else None
        trial = self._active[1] if self._active else None
        phase = self._active[2] if self._active else None
        self._append("request_reserved", request_id=request_id, sequence=sequence,
                     case_id=case, trial=trial, phase=phase,
                     request_fingerprint=_fingerprint(payload), reservation_usd=self.reservation_usd)
        self._rebuild()
        return request_id, case, trial, phase

    def _finish(self, request_id: str, case: str | None, trial: int | None, phase: str | None,
                started: float, outcome: str, usage: dict[str, Any] | None,
                usage_status: str, error_code: str | None = None,
                transport: str | None = None, response_id: str | None = None) -> None:
        reservation = self._reservations[request_id]
        actual = usage.get("cost_usd") if usage_status == "known" and usage else None
        released = max(0.0, float(reservation["reservation_usd"]) - float(actual)) if actual is not None else 0.0
        try:
            self._append("request_finished", request_id=request_id, sequence=reservation.get("sequence"),
                         case_id=case, trial=trial, phase=phase,
                         request_fingerprint=reservation.get("request_fingerprint"), outcome=outcome,
                         strict_uncertain=outcome == "provider_outcome_uncertain", usage_status=usage_status,
                         usage=usage, reservation_usd=reservation.get("reservation_usd"),
                         actual_cost_usd=round(float(actual), 12) if actual is not None else None,
                         released_usd=round(released, 12), latency_ms=round((time.perf_counter() - started) * 1000, 3),
                         response_id=response_id, error_code=error_code, transport_classification=transport)
        except LedgerError:
            self._stopped, self._stop_reason = True, "LEDGER_ERROR"
            raise
        self._rebuild()

    def _strict_stop(self, case: str | None, trial: int | None) -> None:
        self._task_terminal = True
        if case is not None and trial is not None:
            self._terminals.add((case, trial))
        if self._strict_streak >= 2:
            self._stopped, self._stop_reason = True, "TWO_STRICT_UNCERTAIN_STOP"
            self._append("experiment_stopped", reason=self._stop_reason,
                         strict_uncertain_count=self._strict_count)

    def _sdk_call(self, payload: Mapping[str, Any]) -> Any:
        models = getattr(self.sdk, "models", None)
        method = getattr(models, "generate_content", None) if models is not None else None
        method = method if callable(method) else getattr(self.sdk, "generate_content", None)
        if not callable(method):
            raise ValueError("SDK_GENERATE_CONTENT_REQUIRED")
        # One invocation only.  The official client must be constructed by the
        # caller with retry_options.attempts=1; this wrapper never replays it.
        return method(model=payload["model"], contents=payload["contents"], config=payload["config"])

    def generate_content(self, model: str, contents: Any, config: Any) -> Any:
        """Make exactly one bounded generation request, without automatic retry."""
        payload = self._normalize(model, contents, config)
        if not self._generation_lock.acquire(False):
            raise ConcurrentRequestError("CONCURRENT_REQUEST")
        temporary = False
        try:
            if self._active is None:
                self._lease.acquire()
                temporary = True
            request_id, case, trial, phase = self._admit(payload)
            started = time.perf_counter()
            try:
                response = self._sdk_call(payload)
            except BaseException as error:
                code, transport, strict = _classify(error)
                outcome = "provider_outcome_uncertain" if strict else "known_failure"
                self._finish(request_id, case, trial, phase, started, outcome, None, "unknown", code, transport)
                if strict:
                    self._strict_stop(case, trial)
                    raise StrictProviderOutcomeUnknown() from None
                raise
            usage, status = _usage(response)
            outcome = "success" if status != "out_of_bounds" else "known_failure"
            if status == "out_of_bounds":
                self._stopped, self._stop_reason = True, "USAGE_OUT_OF_BOUNDS"
            self._finish(request_id, case, trial, phase, started, outcome, usage, status,
                         response_id=_response_id(response))
            if status == "out_of_bounds":
                self._append("experiment_stopped", reason=self._stop_reason)
                raise ProviderStopped(self._stop_reason or "USAGE_OUT_OF_BOUNDS")
            return response
        finally:
            if temporary:
                self._lease.release()
            self._generation_lock.release()

    def summary(self) -> dict[str, Any]:
        self._rebuild()
        if self._stopped:
            status = "STOPPED"
        elif self._request_count >= self.max_requests or self._committed + self.reservation_usd > self.max_usd + _EPSILON:
            status = "EXHAUSTED"
        elif self._request_count:
            status = "ACTIVE"
        else:
            status = "READY"
        deadline = _utc(self._first_at + timedelta(seconds=self.max_seconds)) if self._first_at else None
        return {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "model": MODEL,
            "request_count": self._request_count,
            "attempts": self._request_count,
            "max_requests": self.max_requests,
            "max_usd": self.max_usd,
            "reservation_usd": self.reservation_usd,
            "committed_usd": round(self._committed, 12),
            "reserved_usd": round(self._committed, 12),
            "released_usd": round(self._released, 12),
            "remaining_usd": round(max(0.0, self.max_usd - self._committed), 12),
            "usage_known_count": self._usage_known,
            "pending_request_count": len(self._pending),
            "strict_uncertain_count": self._strict_count,
            "strict_uncertain_streak": self._strict_streak,
            "last_outcome": self._last_outcome,
            "first_request_at": _utc(self._first_at) if self._first_at else None,
            "elapsed_seconds": round(self._elapsed(), 3),
            "deadline_at": deadline,
            "stopped": self._stopped,
            "stop_reason": self._stop_reason,
            "status": status,
            "trials_by_case": {case: sorted(trials) for case, trials in sorted(self._trials.items())},
        }


__all__ = [
    "AdaptiveProvider", "AdaptiveProviderError", "BudgetExceeded", "ConcurrentRequestError",
    "DeadlineExceeded", "INPUT_PAYLOAD_BYTE_BOUND", "INPUT_TOKEN_BOUND", "LedgerError",
    "MAX_OUTPUT_TOKENS", "MODEL", "ProviderOutcomeUnknownError", "ProviderStopped",
    "PROTOCOL_TOKEN_ALLOWANCE", "REQUEST_TIMEOUT_SECONDS", "RESERVATION_USD",
    "StrictProviderOutcomeUnknown", "TrialLimitExceeded",
]
