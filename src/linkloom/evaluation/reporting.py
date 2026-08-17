"""Stable seven-artifact writer for formal evaluations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import EvaluationRunManifest


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) for record in records]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def write_run_artifacts(
    run_dir: str | Path,
    *,
    manifest: Mapping[str, Any],
    predictions: Iterable[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    bad_cases: Iterable[Mapping[str, Any]],
    trace_quality: Mapping[str, Any],
    safety_report: Mapping[str, Any],
    evidence_validations: Iterable[Mapping[str, Any]] | None = None,
    usage: Mapping[str, Any] | None = None,
) -> Path:
    output = Path(run_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "run_manifest.json", manifest)
    _write_jsonl(output / "predictions.jsonl", predictions)
    _write_json(output / "metrics.json", metrics)
    _write_json(output / "bad_cases.json", list(bad_cases))
    _write_json(output / "trace_quality.json", trace_quality)
    _write_json(output / "safety_report.json", safety_report)
    if evidence_validations is not None:
        _write_json(output / "evidence_validation.json", list(evidence_validations))
    if usage is not None:
        _write_json(output / "usage.json", usage)

    safety_status = "PASS" if safety_report.get("passed", safety_report.get("is_safe", False)) else "FAIL"
    lines = [
        "# LinkLoom Evaluation Report",
        "",
        f"- Run: `{manifest.get('run_id', '')}`",
        f"- Dataset: `{manifest.get('dataset_version', '')}`",
        f"- Provider/scenario: `{manifest.get('provider', '')}` / `{manifest.get('scenario', '')}`",
        f"- Status: `{manifest.get('status', '')}`",
        f"- Oracle plumbing: `{bool(manifest.get('oracle', False))}`",
        f"- Baseline eligible: `{bool(manifest.get('baseline_eligible', False))}`",
        f"- Safety: `{safety_status}`",
        "",
        "## Metrics",
        "",
    ]
    for group, values in metrics.items():
        if isinstance(values, Mapping):
            compact = ", ".join(f"{key}={value}" for key, value in sorted(values.items()) if isinstance(value, (int, float, bool)))
            lines.append(f"- {group}: {compact}")
    lines.extend([
        "",
        "Evidence and trace outputs contain refs, hashes, and counts only; raw Vault text is excluded.",
        "",
    ])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return output / "report.md"


def write_report(manifest: EvaluationRunManifest, output_dir: str | Path) -> Path:
    """Backward-compatible compact writer used by the P6-A contract tests."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    data = {
        "dataset_version": manifest.dataset_version,
        "run_timestamp": manifest.run_timestamp,
        "metrics": [{"name": metric.name, "value": metric.value, "metadata": metric.metadata} for metric in manifest.metrics],
        "provider": manifest.provider,
        "scenario": manifest.scenario,
        "baseline_eligible": manifest.baseline_eligible,
        "run_id": manifest.run_id,
        "safety_report": {
            "is_safe": manifest.safety_report.is_safe,
            "violations": manifest.safety_report.violations,
        },
    }
    _write_json(output / "manifest.json", data)
    (output / "report.md").write_text(
        "# Evaluation Report\n\n"
        f"Run ID: {manifest.run_id}\n"
        f"Provider: {manifest.provider}\n"
        f"Scenario: {manifest.scenario}\n",
        encoding="utf-8",
    )
    return output / "report.md"
