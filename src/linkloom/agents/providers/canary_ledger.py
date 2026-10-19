"""Durable accounting boundary for the offline M3-L canary.

This module has one job: atomically reserve and account for bounded provider
requests.  It has no provider, retry, runtime, or vault dependencies.  The
JSONL ledger is append-only; an immutable marker binds one campaign identity,
manifest, and accounting configuration to its canonical path.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterator


SCHEMA_VERSION = "linkloom-canary-ledger/v1"
INPUT_TOKEN_CAP = 65_536
OUTPUT_TOKEN_CAP = 8_192
INPUT_PRICE_USD_PER_MILLION = 0.75
OUTPUT_PRICE_USD_PER_MILLION = 3.75
RESERVATION_USD = 0.079872
MAX_TOTAL_REQUESTS = 34
MAX_TOTAL_COST_USD = 3.0
CAMPAIGN_TOTAL_SECONDS = 1_800
CASE_TOTAL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 130

# The three frozen canary cases partition the four shared stage budgets.
CASE_IDS = ("C1", "C2", "C3")
CASE_TOTAL_CAPS = {"C1": 11, "C2": 11, "C3": 12}
STAGE_PER_CASE_CAPS = {
    "discovery": {"C1": 2, "C2": 2, "C3": 3},  # F
    "planning": {"C1": 2, "C2": 2, "C3": 2},  # G2
    "semantic": {"C1": 1, "C2": 1, "C3": 1},
    "runtime": {"C1": 6, "C2": 6, "C3": 6},
}
STAGE_TOTAL_CAPS = {name: sum(values.values()) for name, values in STAGE_PER_CASE_CAPS.items()}
STAGES = tuple(STAGE_PER_CASE_CAPS)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_HASH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_EPSILON = 1e-12


class CanaryLedgerError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LedgerNotInitializedError(CanaryLedgerError):
    pass


class LedgerCorruptError(CanaryLedgerError):
    pass


class ManifestMismatchError(CanaryLedgerError):
    pass


class CampaignStoppedError(CanaryLedgerError):
    pass


class PendingReservationError(CampaignStoppedError):
    pass


class BudgetExceededError(CanaryLedgerError):
    pass


class DeadlineExceededError(CanaryLedgerError):
    pass


class DuplicateRequestError(CanaryLedgerError):
    pass


class ReservationNotFoundError(CanaryLedgerError):
    pass


class ReservationCompletedError(CanaryLedgerError):
    pass


_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


class _OSLease:
    """Blocking process/thread lease, following adaptive_provider's pattern."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.thread_lock = _lock_for(path)
        self.fd: int | None = None

    def acquire(self) -> None:
        self.thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
        except BaseException:
            self.thread_lock.release()
            raise

    def release(self) -> None:
        fd, self.fd = self.fd, None
        if fd is None:
            return
        try:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        finally:
            self.thread_lock.release()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        value = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def _json(value: Any, field: str) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_NOT_JSON") from exc


def _copy_json(value: Any, field: str) -> Any:
    return json.loads(_json(value, field).decode())


def _digest(value: Any, field: str) -> str:
    return hashlib.sha256(_json(value, field)).hexdigest()


def _digest_matches(value: Any, expected: Any, field: str) -> bool:
    try:
        return isinstance(expected, str) and _digest(value, field) == expected
    except (TypeError, ValueError):
        return False


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{field}_INVALID")
    return value


def _manifest(value: Any) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("MANIFEST_HASH_INVALID")
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _lookup(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _stage(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("STAGE_INVALID")
    result = value.strip().casefold().replace("-", "_")
    if result not in STAGES:
        raise ValueError("STAGE_INVALID")
    return result


def _config() -> dict[str, Any]:
    return {
        "input_token_cap": INPUT_TOKEN_CAP,
        "output_token_cap": OUTPUT_TOKEN_CAP,
        "input_price_usd_per_million": INPUT_PRICE_USD_PER_MILLION,
        "output_price_usd_per_million": OUTPUT_PRICE_USD_PER_MILLION,
        "reservation_usd": RESERVATION_USD,
        "max_total_requests": MAX_TOTAL_REQUESTS,
        "max_total_cost_usd": MAX_TOTAL_COST_USD,
        "campaign_total_seconds": CAMPAIGN_TOTAL_SECONDS,
        "case_total_seconds": CASE_TOTAL_SECONDS,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "case_total_caps": dict(CASE_TOTAL_CAPS),
        "stage_per_case_caps": {key: dict(value) for key, value in STAGE_PER_CASE_CAPS.items()},
        "stage_total_caps": dict(STAGE_TOTAL_CAPS),
    }


def _config_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json(value, "CONFIG")).hexdigest()


def _normalize_usage(raw: Any, estimate: Any = None, outcome: str = "success") -> tuple[str, dict[str, Any] | None, float | None, str]:
    outcome_key = outcome.casefold() if isinstance(outcome, str) else "unknown"
    raw_copy: dict[str, Any] | None = None
    if isinstance(raw, Mapping):
        try:
            raw_copy = _copy_json(dict(raw), "USAGE")
        except (TypeError, ValueError):
            raw_copy = None
    if any(word in outcome_key for word in ("unknown", "uncertain", "timeout", "error", "exception")):
        return "unknown", raw_copy, None, "OUTCOME_UNKNOWN"
    if not isinstance(raw, Mapping):
        return "unknown", None, None, "USAGE_MISSING"
    if raw_copy is None:
        return "unknown", None, None, "USAGE_INVALID"
    inputs = _integer(_lookup(raw_copy, "input_tokens", "prompt_token_count", "promptTokenCount"))
    outputs = _integer(_lookup(raw_copy, "output_tokens", "candidates_token_count", "candidatesTokenCount"))
    thinking_raw = _lookup(raw_copy, "thinking_tokens", "thoughts_token_count", "thoughtsTokenCount")
    thinking = _integer(thinking_raw)
    total_raw = _lookup(raw_copy, "total_tokens", "total_token_count", "totalTokenCount")
    total = None if total_raw is None else _integer(total_raw)
    if inputs is None or outputs is None or thinking is None:
        return "unknown", raw_copy, None, "USAGE_INCOMPLETE"
    billable_output = outputs + thinking
    if inputs > INPUT_TOKEN_CAP or billable_output > OUTPUT_TOKEN_CAP:
        return "unknown", raw_copy, None, "USAGE_OUT_OF_BOUNDS"
    expected_total = inputs + billable_output
    if total is not None and total != expected_total:
        return "unknown", raw_copy, None, "USAGE_TOTAL_MISMATCH"
    calculated = (inputs * INPUT_PRICE_USD_PER_MILLION + billable_output * OUTPUT_PRICE_USD_PER_MILLION) / 1_000_000
    if estimate is None:
        estimate = _lookup(raw_copy, "actual_cost_usd", "estimated_cost_usd", "cost_usd")
    if isinstance(estimate, Mapping):
        estimate = _lookup(estimate, "actual_cost_usd", "estimated_cost_usd", "cost_usd")
    cost = _number(estimate)
    if cost is None:
        cost = calculated
    if calculated > RESERVATION_USD + _EPSILON or cost > RESERVATION_USD + _EPSILON:
        return "unknown", raw_copy, None, "COST_OUT_OF_BOUNDS"
    if estimate is not None and abs(cost - calculated) > max(_EPSILON, calculated * 0.02):
        return "unknown", raw_copy, None, "COST_MISMATCH"
    normalized = dict(raw_copy)
    normalized.update({
        "input_tokens": inputs,
        "output_tokens": outputs,
        "thinking_tokens": thinking,
        "billable_output_tokens": billable_output,
        "total_tokens": total if total is not None else expected_total,
        "cost_usd": round(cost, 12),
    })
    return "known", normalized, round(cost, 12), "KNOWN"


class CanaryLedger:
    """One fixed, append-only budget ledger for one frozen campaign."""

    def __init__(self, root: str | os.PathLike[str], campaign_id: str, manifest_hash: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.campaign_id = _id(campaign_id, "CAMPAIGN_ID")
        self.manifest_hash = _manifest(manifest_hash)
        # ``root`` is the host's already-fixed canary state root; the campaign
        # id is the sole path selector and cannot be changed to resume work.
        self.campaign_dir = self.root / self.campaign_id
        self.ledger_path = self.campaign_dir / "ledger.jsonl"
        self.path = self.ledger_path
        self.marker_path = self.campaign_dir / "initialized.json"
        self.lock_path = self.campaign_dir / "ledger.lock"
        self._lease = _OSLease(self.lock_path)
        self._records: list[dict[str, Any]] = []
        self._state: dict[str, Any] = {}
        self._owned_pending: set[str] = set()
        with self._locked():
            marker, ledger = self.marker_path.exists(), self.ledger_path.exists()
            if marker != ledger:
                raise LedgerNotInitializedError("PARTIAL_INITIALIZATION")
            if not marker:
                self._initialize_unlocked()
            self._refresh_unlocked(recover=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._lease.acquire()
        try:
            yield
        finally:
            self._lease.release()

    def _event(self, event: str, **values: Any) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "utc": _now(),
            "campaign_id": self.campaign_id,
            "manifest_hash": self.manifest_hash,
            **values,
        }

    def _initialize_unlocked(self) -> None:
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        marker_exists, ledger_exists = self.marker_path.exists(), self.ledger_path.exists()
        if marker_exists and ledger_exists:
            self._load_unlocked()
            return
        if marker_exists != ledger_exists:
            raise LedgerNotInitializedError("PARTIAL_INITIALIZATION")
        config = _config()
        created_at = _now()
        marker = {
            "schema_version": SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "manifest_hash": self.manifest_hash,
            "created_at": created_at,
            "config": config,
            "config_sha256": _config_hash(config),
        }
        try:
            with self.marker_path.open("xb") as stream:
                stream.write(_json(marker, "MARKER") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            header = self._event("campaign_initialized", started_at=created_at, config=config, config_sha256=_config_hash(config))
            with self.ledger_path.open("xb") as stream:
                stream.write(_json(header, "LEDGER") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            # A second constructor can have observed an empty directory before
            # the first one acquired the lease.  Re-read the now-complete pair.
            if self.marker_path.exists() and self.ledger_path.exists():
                self._load_unlocked()
                return
            raise LedgerNotInitializedError("INITIALIZATION_RACE")
        except (OSError, TypeError, ValueError) as exc:
            raise CanaryLedgerError("LEDGER_INITIALIZE_FAILED") from exc

    def _read_marker_unlocked(self) -> None:
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise LedgerCorruptError("MARKER_CORRUPT") from exc
        config = _config()
        if not isinstance(marker, dict):
            raise LedgerCorruptError("MARKER_CORRUPT")
        if marker.get("campaign_id") != self.campaign_id or marker.get("schema_version") != SCHEMA_VERSION:
            raise LedgerCorruptError("MARKER_BINDING_MISMATCH")
        if marker.get("manifest_hash") != self.manifest_hash:
            raise ManifestMismatchError("MANIFEST_MISMATCH")
        if marker.get("config") != config or marker.get("config_sha256") != _config_hash(config):
            raise LedgerCorruptError("CONFIG_BINDING_MISMATCH")
        if _parse_time(marker.get("created_at")) is None:
            raise LedgerCorruptError("CAMPAIGN_START_TIME_CORRUPT")

    def _read_records_unlocked(self) -> list[dict[str, Any]]:
        try:
            content = self.ledger_path.read_bytes()
        except OSError as exc:
            raise CanaryLedgerError("LEDGER_READ_FAILED") from exc
        if not content or not content.endswith(b"\n"):
            raise LedgerCorruptError("LEDGER_TRUNCATED")
        rows: list[dict[str, Any]] = []
        for line in content.splitlines():
            try:
                row = json.loads(line.decode("utf-8"))
            except (UnicodeError, TypeError, ValueError) as exc:
                raise LedgerCorruptError("LEDGER_CORRUPT") from exc
            if not isinstance(row, dict) or row.get("schema_version") != SCHEMA_VERSION or row.get("campaign_id") != self.campaign_id:
                raise LedgerCorruptError("LEDGER_BINDING_MISMATCH")
            if row.get("manifest_hash") != self.manifest_hash:
                raise ManifestMismatchError("MANIFEST_MISMATCH")
            rows.append(row)
        return rows

    def _load_unlocked(self) -> None:
        self._read_marker_unlocked()
        records = self._read_records_unlocked()
        config = _config()
        if not records or records[0].get("event") != "campaign_initialized":
            raise LedgerCorruptError("LEDGER_HEADER_MISSING")
        if records[0].get("config") != config or records[0].get("config_sha256") != _config_hash(config):
            raise LedgerCorruptError("CONFIG_BINDING_MISMATCH")
        if _parse_time(records[0].get("started_at") or records[0].get("utc")) is None:
            raise LedgerCorruptError("CAMPAIGN_START_TIME_CORRUPT")
        self._records = records

    def _append_unlocked(self, row: Mapping[str, Any]) -> None:
        try:
            with self.ledger_path.open("ab") as stream:
                stream.write(_json(dict(row), "LEDGER") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise CanaryLedgerError("LEDGER_APPEND_FAILED") from exc

    def _rebuild(self) -> dict[str, Any]:
        reservations: dict[str, dict[str, Any]] = {}
        completions: dict[str, dict[str, Any]] = {}
        identities: dict[str, str] = {}
        pending: set[str] = set()
        stop_reason: str | None = None
        inferred_stop: str | None = None
        campaign_started_at: str | None = None
        case_started_at: dict[str, str] = {}
        source_bindings: dict[str, Any] = {}
        next_sequence = 1
        for index, row in enumerate(self._records):
            event = row.get("event")
            if not isinstance(event, str):
                raise LedgerCorruptError("LEDGER_CORRUPT")
            if event == "campaign_initialized":
                if index or campaign_started_at is not None:
                    raise LedgerCorruptError("LEDGER_HEADER_DUPLICATE")
                campaign_started_at = row.get("started_at") or row.get("utc")
            elif event == 'sources_bound':
                case = row.get('case_id')
                if (case not in CASE_IDS or case in source_bindings
                        or not _digest_matches(row.get('source_snapshot'), row.get('source_snapshot_hash'), 'SOURCE_SNAPSHOT')):
                    raise LedgerCorruptError('SOURCE_BINDING_CORRUPT')
                source_bindings[case] = row['source_snapshot']
            elif event == "request_reserved":
                rid, seq, case, stage, identity = (row.get(key) for key in ("reservation_id", "sequence", "case_id", "stage", "request_identity_hash"))
                if (_parse_time(row.get("utc")) is None or rid != f"{self.campaign_id}-{next_sequence:06d}" or rid in reservations or seq != next_sequence or case not in CASE_IDS or stage not in STAGES or not isinstance(identity, str) or identity in identities or not _digest_matches(row.get("request_identity"), identity, "REQUEST_IDENTITY") or not _digest_matches(row.get("source_snapshot"), row.get("source_snapshot_hash"), "SOURCE_SNAPSHOT")):
                    raise LedgerCorruptError("LEDGER_CORRUPT")
                if row.get("reservation_usd") != RESERVATION_USD or row.get("reserved_input_tokens") != INPUT_TOKEN_CAP or row.get("reserved_output_tokens") != OUTPUT_TOKEN_CAP:
                    raise LedgerCorruptError("RESERVATION_BINDING_MISMATCH")
                reservations[rid] = row
                identities[identity] = rid
                pending.add(rid)
                case_started_at.setdefault(case, row.get("utc"))
                next_sequence += 1
            elif event == "request_completed":
                rid = row.get("reservation_id")
                status, charged = row.get("usage_status"), _number(row.get("charged_cost_usd"))
                reservation = reservations.get(rid)
                if (_parse_time(row.get("utc")) is None or not isinstance(rid, str) or reservation is None or rid in completions or status not in {"known", "unknown"} or charged is None or row.get("sequence") != reservation.get("sequence") or row.get("case_id") != reservation.get("case_id") or row.get("stage") != reservation.get("stage") or row.get("request_identity_hash") != reservation.get("request_identity_hash")):
                    raise LedgerCorruptError("LEDGER_CORRUPT")
                if status == "unknown" and abs(charged - RESERVATION_USD) > _EPSILON:
                    raise LedgerCorruptError("UNKNOWN_COST_NOT_RETAINED")
                if status == "known" and charged > RESERVATION_USD + _EPSILON:
                    raise LedgerCorruptError("KNOWN_COST_OUT_OF_BOUNDS")
                completions[rid] = row
                pending.discard(rid)
                if status == "unknown":
                    inferred_stop = inferred_stop or row.get("stop_reason") or "PROVIDER_OUTCOME_UNKNOWN"
            elif event == "campaign_stopped":
                reason = row.get("reason")
                if _parse_time(row.get("utc")) is None or not isinstance(reason, str) or not reason:
                    raise LedgerCorruptError("LEDGER_CORRUPT")
                stop_reason = stop_reason or reason
            else:
                raise LedgerCorruptError("LEDGER_EVENT_UNKNOWN")
        if pending and stop_reason is None:
            inferred_stop = inferred_stop or "PENDING_RESERVATION"

        stage: dict[str, dict[str, Any]] = {}
        for name in STAGES:
            maximum = STAGE_TOTAL_CAPS[name]
            stage[name] = {"count": 0, "completed_count": 0, "pending_count": 0, "max_count": maximum, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "reserved_input_tokens": 0, "reserved_output_tokens": 0, "reserved_cost_usd": 0.0, "max_input_tokens": maximum * INPUT_TOKEN_CAP, "max_output_tokens": maximum * OUTPUT_TOKEN_CAP, "max_cost_usd": round(maximum * RESERVATION_USD, 12)}
        cases: dict[str, dict[str, Any]] = {
            case: {"count": 0, "completed_count": 0, "pending_count": 0, "max_count": CASE_TOTAL_CAPS[case], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "reserved_input_tokens": 0, "reserved_output_tokens": 0, "reserved_cost_usd": 0.0, "stages": {stage_name: {"count": 0, "max_count": STAGE_PER_CASE_CAPS[stage_name][case], "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0} for stage_name in STAGES}} for case in CASE_IDS
        }
        used = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        reserved = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        for rid, reservation in reservations.items():
            case, stage_name = reservation["case_id"], reservation["stage"]
            completion = completions.get(rid)
            if completion is None:
                inputs, outputs, cost = INPUT_TOKEN_CAP, OUTPUT_TOKEN_CAP, RESERVATION_USD
            else:
                usage = completion.get("usage")
                if completion["usage_status"] == "known" and isinstance(usage, Mapping):
                    inputs, outputs = usage.get("input_tokens"), usage.get("billable_output_tokens", usage.get("output_tokens"))
                    if not isinstance(inputs, int) or not isinstance(outputs, int):
                        raise LedgerCorruptError("COMPLETION_USAGE_CORRUPT")
                else:
                    inputs, outputs = INPUT_TOKEN_CAP, OUTPUT_TOKEN_CAP
                cost = float(completion["charged_cost_usd"])
            stage_row, case_row = stage[stage_name], cases[case]
            case_stage = case_row["stages"][stage_name]
            stage_row["count"] += 1
            case_row["count"] += 1
            case_stage["count"] += 1
            if completion is None:
                stage_row["pending_count"] += 1
                case_row["pending_count"] += 1
                stage_row["reserved_input_tokens"] += inputs
                stage_row["reserved_output_tokens"] += outputs
                stage_row["reserved_cost_usd"] += cost
                case_row["reserved_input_tokens"] += inputs
                case_row["reserved_output_tokens"] += outputs
                case_row["reserved_cost_usd"] += cost
                reserved["input_tokens"] += inputs
                reserved["output_tokens"] += outputs
                reserved["cost_usd"] += cost
            else:
                stage_row["completed_count"] += 1
                case_row["completed_count"] += 1
                stage_row["input_tokens"] += inputs
                stage_row["output_tokens"] += outputs
                stage_row["cost_usd"] += cost
                case_row["input_tokens"] += inputs
                case_row["output_tokens"] += outputs
                case_row["cost_usd"] += cost
                case_stage["input_tokens"] += inputs
                case_stage["output_tokens"] += outputs
                case_stage["cost_usd"] += cost
                used["input_tokens"] += inputs
                used["output_tokens"] += outputs
                used["cost_usd"] += cost
        rows_to_round = [*stage.values(), *cases.values(), *[item for case in cases.values() for item in case["stages"].values()]]
        for row in rows_to_round:
            for key in ("cost_usd", "reserved_cost_usd", "max_cost_usd"):
                if key in row:
                    row[key] = round(float(row[key]), 12)
        used["cost_usd"], reserved["cost_usd"] = round(used["cost_usd"], 12), round(reserved["cost_usd"], 12)
        return {"reservations": reservations, "completions": completions, "identities": identities, "pending": pending, "stop_reason": stop_reason, "inferred_stop_reason": inferred_stop, "campaign_started_at": campaign_started_at, "case_started_at": case_started_at, "stage": stage, "cases": cases, "used": used, "reserved": reserved, "count": len(reservations), 'source_bindings': source_bindings}

    def _refresh_unlocked(self, *, recover: bool, allow_pending: set[str] | None = None) -> None:
        self._load_unlocked()
        state = self._rebuild()
        external_pending = state["pending"].difference(allow_pending or set())
        reason = state["inferred_stop_reason"]
        if external_pending and state["stop_reason"] is None:
            reason = reason or "PENDING_RESERVATION"
        elif reason == "PENDING_RESERVATION":
            reason = None
        if recover and reason and state["stop_reason"] is None:
            self._append_unlocked(self._event("campaign_stopped", reason=reason))
            self._load_unlocked()
            state = self._rebuild()
        self._state = state

    def _open_check(self, state: Mapping[str, Any], *, allow_pending: set[str] | None = None, admission: bool = False) -> None:
        if state["stop_reason"]:
            if state["stop_reason"] == "PENDING_RESERVATION":
                raise PendingReservationError("PENDING_RESERVATION")
            raise CampaignStoppedError(str(state["stop_reason"]))
        if state["pending"].difference(allow_pending or set()):
            raise PendingReservationError("PENDING_RESERVATION")
        if not admission:
            return
        started = _parse_time(state["campaign_started_at"])
        if started is None or CAMPAIGN_TOTAL_SECONDS - max(0, (datetime.now(UTC) - started).total_seconds()) <= REQUEST_TIMEOUT_SECONDS:
            raise DeadlineExceededError("CAMPAIGN_DEADLINE")
        if state["count"] >= MAX_TOTAL_REQUESTS or state["used"]["cost_usd"] + state["reserved"]["cost_usd"] + RESERVATION_USD > MAX_TOTAL_COST_USD + _EPSILON:
            raise BudgetExceededError("GLOBAL_BUDGET")

    def check_open(self) -> bool:
        with self._locked():
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            self._open_check(self._state, allow_pending=self._owned_pending)
            return True

    def bind_sources(self, case: str, source_snapshot: Any) -> None:
        """Persist first admission, so restarting a Host cannot rebind sources."""
        if case not in CASE_IDS:
            raise ValueError('CASE_ID_INVALID')
        source = _copy_json(source_snapshot, 'SOURCE_SNAPSHOT')
        with self._locked():
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            self._open_check(self._state, allow_pending=self._owned_pending)
            bound = self._state['source_bindings'].get(case)
            if bound is not None and bound != source:
                self._append_unlocked(self._event('campaign_stopped', reason='SOURCE_SNAPSHOT_CHANGED'))
                raise CampaignStoppedError('SOURCE_SNAPSHOT_CHANGED')
            if bound is None:
                if self._state['cases'][case]['count']:
                    self._append_unlocked(self._event('campaign_stopped', reason='SOURCE_BINDING_MISSING'))
                    raise CampaignStoppedError('SOURCE_BINDING_MISSING')
                self._append_unlocked(self._event('sources_bound', case_id=case,
                    source_snapshot=source, source_snapshot_hash=_digest(source, 'SOURCE_SNAPSHOT')))

    def reserve(self, case: str, stage: str, request_identity: Any, source_snapshot: Any, *, metadata: Any = None) -> str:
        case_id, stage_id = _id(case, "CASE_ID"), _stage(stage)
        if case_id not in CASE_IDS:
            raise ValueError("CASE_ID_INVALID")
        identity = _copy_json(request_identity, "REQUEST_IDENTITY")
        source = _copy_json(source_snapshot, "SOURCE_SNAPSHOT")
        metadata_copy = _copy_json(metadata, "METADATA") if metadata is not None else None
        identity_hash = _digest(identity, "REQUEST_IDENTITY")
        source_hash = _digest(source, "SOURCE_SNAPSHOT")
        with self._locked():
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            self._open_check(self._state, allow_pending=self._owned_pending, admission=True)
            bound = self._state['source_bindings'].get(case_id)
            if bound is not None and bound != source:
                self._append_unlocked(self._event('campaign_stopped', reason='SOURCE_SNAPSHOT_CHANGED'))
                raise CampaignStoppedError('SOURCE_SNAPSHOT_CHANGED')
            if identity_hash in self._state["identities"]:
                raise DuplicateRequestError("REQUEST_IDENTITY_REPLAY")
            stage_row, case_row = self._state["stage"][stage_id], self._state["cases"][case_id]
            if stage_row["count"] >= STAGE_TOTAL_CAPS[stage_id]:
                raise BudgetExceededError(f"{stage_id.upper()}_TOTAL_CAP")
            if case_row["stages"][stage_id]["count"] >= STAGE_PER_CASE_CAPS[stage_id][case_id]:
                raise BudgetExceededError(f"{stage_id.upper()}_CASE_CAP")
            if case_row["count"] >= CASE_TOTAL_CAPS[case_id]:
                raise BudgetExceededError(f"{case_id}_TOTAL_CAP")
            started = _parse_time(self._state["campaign_started_at"])
            now = datetime.now(UTC)
            if started is None or CAMPAIGN_TOTAL_SECONDS - max(0, (now - started).total_seconds()) <= REQUEST_TIMEOUT_SECONDS:
                raise DeadlineExceededError("CAMPAIGN_DEADLINE")
            first_case = _parse_time(self._state["case_started_at"].get(case_id))
            if first_case is not None and CASE_TOTAL_SECONDS - max(0, (now - first_case).total_seconds()) <= REQUEST_TIMEOUT_SECONDS:
                raise DeadlineExceededError("CASE_DEADLINE")
            sequence = self._state["count"] + 1
            rid = f"{self.campaign_id}-{sequence:06d}"
            self._append_unlocked(self._event("request_reserved", reservation_id=rid, sequence=sequence, case_id=case_id, stage=stage_id, request_identity=identity, request_identity_hash=identity_hash, source_snapshot=source, source_snapshot_hash=source_hash, metadata=metadata_copy, reserved_input_tokens=INPUT_TOKEN_CAP, reserved_output_tokens=OUTPUT_TOKEN_CAP, reservation_usd=RESERVATION_USD))
            self._owned_pending.add(rid)
            self._refresh_unlocked(recover=False, allow_pending=self._owned_pending)
            return rid

    def complete(self, reservation_id: str, usage: Mapping[str, Any] | None = None, actual_estimate: Any = None, *, outcome: str = "success") -> dict[str, Any]:
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("RESERVATION_ID_INVALID")
        with self._locked():
            # One host may have several in-flight requests.  Keep all of that
            # host's reservations alive while a completion is being accounted;
            # a fresh host still sees them as an unrecoverable pending crash.
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            reservation = self._state["reservations"].get(reservation_id)
            if reservation is None:
                raise ReservationNotFoundError("RESERVATION_NOT_FOUND")
            if reservation_id in self._state["completions"]:
                raise ReservationCompletedError("RESERVATION_ALREADY_COMPLETED")
            status, normalized, cost, reason = _normalize_usage(usage, actual_estimate, outcome)
            unknown = status == "unknown"
            charged = RESERVATION_USD if unknown else float(cost or 0.0)
            stop_reason = None
            if unknown:
                stop_reason = {"USAGE_MISSING": "USAGE_UNKNOWN", "USAGE_INCOMPLETE": "USAGE_UNKNOWN", "USAGE_OUT_OF_BOUNDS": "USAGE_OUT_OF_BOUNDS", "USAGE_TOTAL_MISMATCH": "USAGE_UNKNOWN", "COST_OUT_OF_BOUNDS": "USAGE_OUT_OF_BOUNDS", "COST_MISMATCH": "USAGE_UNKNOWN"}.get(reason, "PROVIDER_OUTCOME_UNKNOWN")
            row = self._event("request_completed", reservation_id=reservation_id, sequence=reservation["sequence"], case_id=reservation["case_id"], stage=reservation["stage"], request_identity_hash=reservation["request_identity_hash"], outcome="unknown" if unknown else outcome, usage_status=status, usage_reason=reason, usage=normalized, charged_cost_usd=round(charged, 12), actual_cost_usd=None if unknown else round(charged, 12), accounted_cost_usd=round(charged, 12), reservation_usd=RESERVATION_USD, released_usd=round(max(0.0, RESERVATION_USD - charged), 12), stop_reason=stop_reason)
            self._append_unlocked(row)
            if stop_reason:
                self._append_unlocked(self._event("campaign_stopped", reason=stop_reason))
            self._owned_pending.discard(reservation_id)
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            return deepcopy(row)

    def stop(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("STOP_REASON_INVALID")
        with self._locked():
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            if self._state["stop_reason"] is None:
                self._append_unlocked(self._event("campaign_stopped", reason=reason.strip()))
                self._refresh_unlocked(recover=False, allow_pending=self._owned_pending)

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            self._refresh_unlocked(recover=True, allow_pending=self._owned_pending)
            state, used, reserved = self._state, self._state["used"], self._state["reserved"]
            status = "STOPPED" if state["stop_reason"] else ("EXHAUSTED" if state["count"] >= MAX_TOTAL_REQUESTS else ("ACTIVE" if state["count"] else "READY"))
            result = {
                "schema_version": SCHEMA_VERSION, "campaign_id": self.campaign_id, "manifest_hash": self.manifest_hash,
                "ledger_path": str(self.ledger_path), "marker_path": str(self.marker_path), "status": status,
                "stopped": state["stop_reason"] is not None, "stop_reason": state["stop_reason"],
                "request_count": state["count"], "total_count": state["count"], "max_requests": MAX_TOTAL_REQUESTS,
                "pending_request_count": len(state["pending"]), "pending": sorted(state["pending"]),
                "reservation_usd": RESERVATION_USD, "committed_cost_usd": used["cost_usd"], "used_cost_usd": used["cost_usd"],
                "reserved_cost_usd": reserved["cost_usd"], "remaining_cost_usd": round(max(0.0, MAX_TOTAL_COST_USD - used["cost_usd"] - reserved["cost_usd"]), 12),
                "max_total_cost_usd": MAX_TOTAL_COST_USD, "input_tokens": used["input_tokens"], "output_tokens": used["output_tokens"],
                "reserved_input_tokens": reserved["input_tokens"], "reserved_output_tokens": reserved["output_tokens"],
                "input_token_cap": INPUT_TOKEN_CAP, "output_token_cap": OUTPUT_TOKEN_CAP,
                "stage_caps": {name: {"per_case": dict(STAGE_PER_CASE_CAPS[name]), "total": STAGE_TOTAL_CAPS[name]} for name in STAGES},
                "case_caps": dict(CASE_TOTAL_CAPS), "stages": deepcopy(state["stage"]), "cases": deepcopy(state["cases"]),
                "campaign_started_at": state["campaign_started_at"], "case_started_at": dict(state["case_started_at"]),
                "campaign_total_seconds": CAMPAIGN_TOTAL_SECONDS, "case_total_seconds": CASE_TOTAL_SECONDS, "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                "config": _config(),
                'source_bindings': deepcopy(state['source_bindings']),
            }
            unknown = sum(c['usage_status'] == 'unknown' for c in state['completions'].values())
            result['unknown_usage_count'] = unknown
            result['estimated_cost_usd'] = None if unknown or state['pending'] else used['cost_usd']
            result['thinking_tokens'] = (None if unknown or state['pending'] else
                sum(c['usage']['thinking_tokens'] for c in state['completions'].values()))
            result["global"] = {"count": state["count"], "max_count": MAX_TOTAL_REQUESTS, "input_tokens": used["input_tokens"], "output_tokens": used["output_tokens"], "cost_usd": used["cost_usd"], "pending_count": len(state["pending"]), "reserved_cost_usd": reserved["cost_usd"], "max_cost_usd": MAX_TOTAL_COST_USD}
            return result


__all__ = [
    "CanaryLedger", "CanaryLedgerError", "LedgerNotInitializedError", "LedgerCorruptError", "ManifestMismatchError", "CampaignStoppedError", "PendingReservationError", "BudgetExceededError", "DeadlineExceededError", "DuplicateRequestError", "ReservationNotFoundError", "ReservationCompletedError", "SCHEMA_VERSION", "INPUT_TOKEN_CAP", "OUTPUT_TOKEN_CAP", "RESERVATION_USD", "MAX_TOTAL_REQUESTS", "MAX_TOTAL_COST_USD", "CAMPAIGN_TOTAL_SECONDS", "CASE_TOTAL_SECONDS", "REQUEST_TIMEOUT_SECONDS", "CASE_IDS", "CASE_TOTAL_CAPS", "STAGE_PER_CASE_CAPS", "STAGE_TOTAL_CAPS",
]
