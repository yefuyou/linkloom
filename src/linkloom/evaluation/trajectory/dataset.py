"""JSONL parsing for the synthetic trajectory evaluation dataset."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .models import TrajectoryCase


def load_cases_jsonl(path: str | Path) -> list[TrajectoryCase]:
    source = Path(path)
    if "gold" in source.name.casefold():
        raise ValueError("trajectory dataset loader must not read Gold")
    if not source.is_file():
        raise FileNotFoundError(f"trajectory case file is missing: {source}")

    cases: list[TrajectoryCase] = []
    seen_case_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        source.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid trajectory JSONL at line {line_number}: {exc.msg}"
            ) from exc
        try:
            case = TrajectoryCase.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid trajectory case at line {line_number}: {exc}"
            ) from exc
        if case.case_id in seen_case_ids:
            raise ValueError(
                f"duplicate case_id at line {line_number}: {case.case_id}"
            )
        seen_case_ids.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("trajectory case file contains no cases")
    return cases


@dataclass(frozen=True)
class TrajectoryDataset:
    cases: tuple[TrajectoryCase, ...]
    source_path: Path

    @classmethod
    def from_path(cls, path: str | Path) -> "TrajectoryDataset":
        source = Path(path).resolve()
        return cls(cases=tuple(load_cases_jsonl(source)), source_path=source)

    def by_id(self, case_id: str) -> TrajectoryCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


__all__ = ["TrajectoryDataset", "load_cases_jsonl"]
