"""Write the fixed trajectory evaluation artifact set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import CaseResult


ARTIFACT_FILENAMES = frozenset(
    {
        "run_manifest.json",
        "case_results.jsonl",
        "metrics.json",
        "failure_summary.json",
        "capability_matrix.json",
        "report.md",
    }
)


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _report_text(
    manifest: dict[str, Any],
    failure_summary: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    counts = failure_summary["counts"]
    lines = [
        "# Agent trajectory evaluation",
        "",
        f"Run status: {manifest['status']}",
        (
            "Cases: "
            f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, "
            f"{counts['NOT_IMPLEMENTED']} NOT_IMPLEMENTED"
        ),
        "",
        "## Deterministic metrics",
        "",
    ]
    for name, metric in metrics["metrics"].items():
        value = "n/a" if metric["value"] is None else f"{metric['value']:.4f}"
        lines.append(
            f"- {name}: {metric['status']} "
            f"({metric['numerator']}/{metric['denominator']}, {value})"
        )
    lines.extend(
        [
            "",
            "Synthetic fixture references only. Provider and judge execution are disabled.",
            "",
        ]
    )
    return "\n".join(lines)


def write_trajectory_artifacts(
    output_dir: str | Path,
    *,
    manifest: dict[str, Any],
    case_results: Iterable[CaseResult],
    metrics: dict[str, Any],
    failure_summary: dict[str, Any],
    capability_matrix: dict[str, Any],
) -> tuple[Path, ...]:
    """Write exactly six artifacts, refusing to disturb unrelated files."""

    output = Path(output_dir)
    if output.exists() and not output.is_dir():
        raise ValueError("output path must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    unknown = sorted(path.name for path in output.iterdir() if path.name not in ARTIFACT_FILENAMES)
    if unknown:
        raise ValueError(f"output directory contains unexpected files: {unknown}")

    normalized_results: list[CaseResult] = []
    for result in case_results:
        if not isinstance(result, CaseResult):
            raise TypeError("case_results must contain CaseResult records")
        normalized_results.append(CaseResult.from_dict(result.to_dict()))

    payloads = {
        "run_manifest.json": _json_text(manifest),
        "case_results.jsonl": "".join(
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
            for result in normalized_results
        ),
        "metrics.json": _json_text(metrics),
        "failure_summary.json": _json_text(failure_summary),
        "capability_matrix.json": _json_text(capability_matrix),
        "report.md": _report_text(manifest, failure_summary, metrics),
    }
    written: list[Path] = []
    for filename in sorted(ARTIFACT_FILENAMES):
        target = output / filename
        target.write_text(payloads[filename], encoding="utf-8")
        written.append(target)
    return tuple(written)


__all__ = ["ARTIFACT_FILENAMES", "write_trajectory_artifacts"]
