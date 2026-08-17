"""Validated readers and local trace metadata contracts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from linkloom.observability.events import TraceEvent


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SPAN_KINDS = frozenset({"internal", "tool", "provider", "human_wait"})
_SPAN_STATUSES = frozenset({"ok", "error", "paused"})


class TraceReadError(ValueError):
    """A trace could not be read or failed its persisted contract."""

    def __init__(self, message: str, path: str | None = None, line_number: int | None = None) -> None:
        self.path = path
        self.line_number = line_number
        location = f"{path}:{line_number}: " if path is not None and line_number is not None else ""
        super().__init__(location + message)


@dataclass(frozen=True)
class TraceManifest:
    trace_schema_version: int
    run_id: str
    event_count: int
    first_seq: int | None
    last_seq: int | None
    event_log: str
    summary: str
    source_index_sha256: str
    redaction_policy_version: str
    complete: bool
    incomplete_reason: str | None = None

    def __post_init__(self) -> None:
        if self.trace_schema_version != 1:
            raise ValueError("trace_schema_version must be 1")
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_id must be a non-empty string")
        if isinstance(self.event_count, bool) or not isinstance(self.event_count, int) or self.event_count < 0:
            raise ValueError("event_count must be a non-negative integer")
        if self.event_log != "events.jsonl":
            raise ValueError("event_log must be 'events.jsonl'")
        if self.summary != "trace_summary.md":
            raise ValueError("summary must be 'trace_summary.md'")
        if not isinstance(self.source_index_sha256, str) or not _SHA256_RE.fullmatch(self.source_index_sha256):
            raise ValueError("source_index_sha256 must be a valid 64-character lowercase hex string")
        if self.redaction_policy_version != "trace-redaction-v1":
            raise ValueError("redaction_policy_version must be 'trace-redaction-v1'")
        if not isinstance(self.complete, bool):
            raise ValueError("complete must be a boolean")
        if self.incomplete_reason is not None and not isinstance(self.incomplete_reason, str):
            raise ValueError("incomplete_reason must be a string or null")
        if self.event_count == 0:
            if self.first_seq is not None or self.last_seq is not None:
                raise ValueError("empty traces must have null first_seq and last_seq")
        else:
            if (
                isinstance(self.first_seq, bool)
                or isinstance(self.last_seq, bool)
                or not isinstance(self.first_seq, int)
                or not isinstance(self.last_seq, int)
                or self.first_seq < 1
                or self.last_seq < self.first_seq
                or self.last_seq - self.first_seq + 1 != self.event_count
            ):
                raise ValueError("first_seq/last_seq must describe event_count contiguous events")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceManifest":
        if not isinstance(data, Mapping):
            raise ValueError("TraceManifest must be a JSON object")
        required = {
            "trace_schema_version",
            "run_id",
            "event_count",
            "first_seq",
            "last_seq",
            "event_log",
            "summary",
            "source_index_sha256",
            "redaction_policy_version",
            "complete",
            "incomplete_reason",
        }
        missing = sorted(required - data.keys())
        if missing:
            raise ValueError(f"TraceManifest is missing required fields: {', '.join(missing)}")
        return cls(
            trace_schema_version=data["trace_schema_version"],
            run_id=data["run_id"],
            event_count=data["event_count"],
            first_seq=data["first_seq"],
            last_seq=data["last_seq"],
            event_log=data["event_log"],
            summary=data["summary"],
            source_index_sha256=data["source_index_sha256"],
            redaction_policy_version=data["redaction_policy_version"],
            complete=data["complete"],
            incomplete_reason=data["incomplete_reason"],
        )


@dataclass(frozen=True)
class SpanRecord:
    span_id: str
    run_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_event_id: str
    end_event_id: str
    status: str
    duration_ms: int
    input_sha256: str | None = None
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("span_id", "run_id", "name", "start_event_id", "end_event_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if self.parent_span_id is not None and not isinstance(self.parent_span_id, str):
            raise ValueError("parent_span_id must be a string or null")
        if self.kind not in _SPAN_KINDS:
            raise ValueError(f"Invalid kind: {self.kind}")
        if self.status not in _SPAN_STATUSES:
            raise ValueError(f"Invalid status: {self.status}")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative integer")
        for field_name in ("input_sha256", "output_sha256"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not _SHA256_RE.fullmatch(value)):
                raise ValueError(f"{field_name} must be a 64-character lowercase SHA-256 or null")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpanRecord":
        if not isinstance(data, Mapping):
            raise ValueError("SpanRecord must be a JSON object")
        return cls(**dict(data))


class TraceReader:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
            raise ValueError("run_id must be a non-empty directory-safe string")
        if "/" in run_id or "\\" in run_id or Path(run_id).is_absolute():
            raise ValueError("run_id must not contain path separators or be absolute")

    def _get_run_dir(self, run_id: str) -> Path:
        self._validate_run_id(run_id)
        return self.root_dir / run_id

    def iter_events(self, run_id: str) -> Iterator[TraceEvent]:
        events_path = self._get_run_dir(run_id) / "events.jsonl"
        if not events_path.exists():
            return

        expected_seq = 1
        expected_thread_id: str | None = None
        seen_event_ids: set[str] = set()
        try:
            handle = events_path.open("r", encoding="utf-8")
        except OSError as exc:
            raise TraceReadError(str(exc), str(events_path), 0) from exc

        with handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.rstrip("\r\n")
                if not line.strip():
                    raise TraceReadError("empty JSONL line", str(events_path), line_number)
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TraceReadError(f"Corrupted JSON: {exc.msg}", str(events_path), line_number) from exc
                try:
                    event = TraceEvent.from_dict(data)
                except Exception as exc:
                    raise TraceReadError(f"invalid event: {exc}", str(events_path), line_number) from exc
                if event.run_id != run_id:
                    raise TraceReadError(
                        f"run_id mismatch: expected {run_id}, got {event.run_id}",
                        str(events_path),
                        line_number,
                    )
                if expected_thread_id is None:
                    expected_thread_id = event.thread_id
                elif event.thread_id != expected_thread_id:
                    raise TraceReadError(
                        f"thread_id mismatch: expected {expected_thread_id}, got {event.thread_id}",
                        str(events_path),
                        line_number,
                    )
                if event.seq != expected_seq:
                    raise TraceReadError(
                        f"seq jump or mismatch: expected {expected_seq}, got {event.seq}",
                        str(events_path),
                        line_number,
                    )
                if event.parent_event_id is not None and event.parent_event_id not in seen_event_ids:
                    raise TraceReadError(
                        f"missing parent_event_id: {event.parent_event_id}",
                        str(events_path),
                        line_number,
                    )
                if event.event_id in seen_event_ids:
                    raise TraceReadError(f"duplicate event_id: {event.event_id}", str(events_path), line_number)
                seen_event_ids.add(event.event_id)
                expected_seq += 1
                yield event

    def read_events(self, run_id: str) -> list[TraceEvent]:
        return list(self.iter_events(run_id))

    def read_manifest(self, run_id: str) -> TraceManifest:
        manifest_path = self._get_run_dir(run_id) / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise TraceReadError("manifest not found", str(manifest_path), 0) from exc
        except json.JSONDecodeError as exc:
            raise TraceReadError(f"corrupted manifest JSON: {exc.msg}", str(manifest_path), 1) from exc
        except OSError as exc:
            raise TraceReadError(str(exc), str(manifest_path), 0) from exc
        try:
            manifest = TraceManifest.from_dict(data)
        except Exception as exc:
            raise TraceReadError(f"invalid manifest: {exc}", str(manifest_path), 1) from exc
        if manifest.run_id != run_id:
            raise TraceReadError(f"manifest run_id mismatch: expected {run_id}, got {manifest.run_id}", str(manifest_path), 1)
        return manifest

    def write_manifest(self, manifest: TraceManifest) -> None:
        run_dir = self._get_run_dir(manifest.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
