"""Append-only local JSONL trace sink."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonlEventSink:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file_path = self.directory / "events.jsonl"

    def append(self, event: Any) -> None:
        data = event.to_dict() if hasattr(event, "to_dict") else event
        if not isinstance(data, dict):
            raise TypeError("trace sink accepts a TraceEvent or JSON object")
        line = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.file_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_lines(self) -> list[str]:
        if not self.file_path.exists():
            return []
        return self.file_path.read_text(encoding="utf-8").splitlines()
