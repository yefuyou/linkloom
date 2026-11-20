"""Thin Result Adapter & Index over formal evaluation run directories."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_numeric_metrics(metrics: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    for key, value in sorted(metrics.items()):
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            flat[full_key] = float(value)
        elif isinstance(value, Mapping):
            flat.update(_flatten_numeric_metrics(value, prefix=full_key))
    return flat


@dataclass(frozen=True)
class EvalRunSummary:
    run_id: str
    run_dir: str
    dataset_version: str
    provider: str
    scenario: str
    status: str
    run_timestamp: str
    baseline_eligible: bool
    oracle: bool
    safety_passed: bool
    bad_case_count: int
    bad_case_by_category: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalRunComparison:
    baseline_run_id: str
    candidate_run_id: str
    metric_deltas: dict[str, float]
    bad_case_delta: int
    safety_regressed: bool


class EvalResultAdapter:
    """Read-only adapter that normalizes and indexes evaluation run directories."""

    @staticmethod
    def load_run_summary(run_dir: str | Path) -> EvalRunSummary:
        root = Path(run_dir)
        manifest_path = root / "run_manifest.json"
        if manifest_path.exists():
            manifest = _read_json(manifest_path)
            metrics_raw = _read_json(root / "metrics.json") if (root / "metrics.json").exists() else {}
            bad_cases = _read_json(root / "bad_cases.json") if (root / "bad_cases.json").exists() else []
            safety = _read_json(root / "safety_report.json") if (root / "safety_report.json").exists() else {}
            usage = _read_json(root / "usage.json") if (root / "usage.json").exists() else {}
            safety_passed = bool(safety.get("passed", safety.get("is_safe", False)))
        elif (root / "manifest.json").exists():
            manifest = _read_json(root / "manifest.json")
            metrics_raw = {
                m["name"]: m["value"]
                for m in manifest.get("metrics", [])
                if isinstance(m, Mapping) and "name" in m and "value" in m
            }
            bad_cases = []
            safety = manifest.get("safety_report", {})
            usage = {}
            safety_passed = bool(safety.get("is_safe", False))
        else:
            raise FileNotFoundError(f"No run_manifest.json or manifest.json found in {root}")

        by_cat: dict[str, int] = {}
        for case in bad_cases:
            if isinstance(case, Mapping):
                cat = str(case.get("category") or "unknown")
                by_cat[cat] = by_cat.get(cat, 0) + 1

        return EvalRunSummary(
            run_id=str(manifest.get("run_id") or root.name),
            run_dir=str(root),
            dataset_version=str(manifest.get("dataset_version") or ""),
            provider=str(manifest.get("provider") or ""),
            scenario=str(manifest.get("scenario") or ""),
            status=str(manifest.get("status") or ("passed" if safety_passed else "failed")),
            run_timestamp=str(manifest.get("run_timestamp") or ""),
            baseline_eligible=bool(manifest.get("baseline_eligible", False)),
            oracle=bool(manifest.get("oracle", False)),
            safety_passed=safety_passed,
            bad_case_count=len(bad_cases),
            bad_case_by_category=by_cat,
            metrics=_flatten_numeric_metrics(metrics_raw),
            usage=dict(usage) if isinstance(usage, Mapping) else {},
        )

    @classmethod
    def scan_runs(cls, root_dir: str | Path) -> list[EvalRunSummary]:
        root = Path(root_dir)
        if not root.exists():
            return []
        run_dirs: set[Path] = set()
        for name in ("run_manifest.json", "manifest.json"):
            for match in root.rglob(name):
                run_dirs.add(match.parent)
        summaries = [cls.load_run_summary(d) for d in run_dirs]
        return sorted(summaries, key=lambda s: (s.run_timestamp, s.run_id))

    @classmethod
    def build_index(
        cls,
        root_dir: str | Path,
        *,
        provider: str | None = None,
        scenario: str | None = None,
        baseline_only: bool = False,
    ) -> dict[str, Any]:
        runs = cls.scan_runs(root_dir)
        filtered = [
            r
            for r in runs
            if (provider is None or r.provider == provider)
            and (scenario is None or r.scenario == scenario)
            and (not baseline_only or r.baseline_eligible)
        ]
        return {
            "schema_version": "eval-result-index/v1",
            "total_runs": len(filtered),
            "runs": [asdict(r) for r in filtered],
        }

    @classmethod
    def write_index(cls, root_dir: str | Path, output_path: str | Path, **filters: Any) -> Path:
        index = cls.build_index(root_dir, **filters)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return out

    @classmethod
    def compare_runs(cls, baseline_dir: str | Path, candidate_dir: str | Path) -> EvalRunComparison:
        base = cls.load_run_summary(baseline_dir)
        cand = cls.load_run_summary(candidate_dir)
        shared_keys = sorted(set(base.metrics) & set(cand.metrics))
        deltas = {k: round(cand.metrics[k] - base.metrics[k], 6) for k in shared_keys}
        return EvalRunComparison(
            baseline_run_id=base.run_id,
            candidate_run_id=cand.run_id,
            metric_deltas=deltas,
            bad_case_delta=cand.bad_case_count - base.bad_case_count,
            safety_regressed=base.safety_passed and not cand.safety_passed,
        )
