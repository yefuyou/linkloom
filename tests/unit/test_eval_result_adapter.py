"""Unit tests for B1-6: Thin Evaluation Result Adapter & Index."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from linkloom.evaluation import EvalResultAdapter
from linkloom.evaluation.reporting import write_run_artifacts


class TestEvalResultAdapter(unittest.TestCase):
    def test_scan_index_and_compare_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            run_base = root / "run-001"
            run_cand = root / "run-002"

            write_run_artifacts(
                run_base,
                manifest={
                    "run_id": "run-001",
                    "dataset_version": "v2.0",
                    "provider": "mock",
                    "scenario": "baseline",
                    "status": "passed",
                    "run_timestamp": "2026-11-20T01:00:00Z",
                    "baseline_eligible": True,
                    "oracle": False,
                },
                predictions=[{"kind": "topic", "document_id": "a.md", "should_link": True}],
                metrics={"topic": {"f1": 0.80, "precision": 0.85}, "cost": {"avg_usd": 0.012}},
                bad_cases=[{"category": "false_positive", "severity": "low"}],
                trace_quality={"ok": True},
                safety_report={"passed": True, "violations": []},
                usage={"total_tokens": 1000},
            )

            write_run_artifacts(
                run_cand,
                manifest={
                    "run_id": "run-002",
                    "dataset_version": "v2.0",
                    "provider": "mock",
                    "scenario": "candidate",
                    "status": "passed",
                    "run_timestamp": "2026-11-20T02:00:00Z",
                    "baseline_eligible": False,
                    "oracle": False,
                },
                predictions=[{"kind": "topic", "document_id": "a.md", "should_link": True}],
                metrics={"topic": {"f1": 0.90, "precision": 0.85}, "cost": {"avg_usd": 0.008}},
                bad_cases=[],
                trace_quality={"ok": True},
                safety_report={"passed": True, "violations": []},
                usage={"total_tokens": 800},
            )

            index_path = EvalResultAdapter.write_index(root, root / "index.json")
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(index_data["total_runs"], 2)
            self.assertEqual([r["run_id"] for r in index_data["runs"]], ["run-001", "run-002"])

            baseline_only = EvalResultAdapter.build_index(root, baseline_only=True)
            self.assertEqual(baseline_only["total_runs"], 1)
            self.assertEqual(baseline_only["runs"][0]["run_id"], "run-001")

            cmp = EvalResultAdapter.compare_runs(run_base, run_cand)
            self.assertEqual(cmp.metric_deltas["topic.f1"], 0.1)
            self.assertEqual(cmp.metric_deltas["cost.avg_usd"], -0.004)
            self.assertEqual(cmp.bad_case_delta, -1)
            self.assertFalse(cmp.safety_regressed)

    def test_legacy_manifest_without_status_is_not_reported_as_passed(self) -> None:
        from linkloom.evaluation.models import EvaluationRunManifest, MetricResult, SafetyReport
        from linkloom.evaluation.reporting import write_report

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_report(
                EvaluationRunManifest(
                    dataset_version="v1",
                    run_timestamp="2026-11-20T00:00:00Z",
                    metrics=[MetricResult(name="f1", value=0.5)],
                    safety_report=SafetyReport(is_safe=True, violations=[]),
                    run_id="legacy-1",
                ),
                root / "legacy",
            )
            # Unrelated manifest.json files (e.g. fixture manifests) are not evaluation runs.
            (root / "fixture").mkdir()
            (root / "fixture" / "manifest.json").write_text('{"schema_version": 1, "results": []}', encoding="utf-8")
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

            runs = EvalResultAdapter.scan_runs(root)

            self.assertEqual([r.run_id for r in runs], ["legacy-1"])
            self.assertEqual(runs[0].status, "unknown")
            self.assertTrue(runs[0].safety_passed)
            self.assertEqual(runs[0].metrics, {"f1": 0.5})
            self.assertEqual({p: p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)
            with self.assertRaises(ValueError):
                EvalResultAdapter.load_run_summary(root / "fixture")

    def test_compare_rejects_different_datasets_and_reports_missing_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def write(name: str, dataset: str, metrics: dict) -> Path:
                return write_run_artifacts(
                    root / name,
                    manifest={"run_id": name, "dataset_version": dataset, "status": "completed"},
                    predictions=[],
                    metrics=metrics,
                    bad_cases=[],
                    trace_quality={},
                    safety_report={"passed": True},
                ).parent

            base = write("base", "v2.0", {"topic": {"f1": 0.8, "recall": 0.7}})
            cand = write("cand", "v2.0", {"topic": {"f1": 0.9}})
            other = write("other", "v3.0", {"topic": {"f1": 0.9}})

            cmp = EvalResultAdapter.compare_runs(base, cand)
            self.assertEqual(cmp.metric_deltas, {"topic.f1": 0.1})
            self.assertEqual(cmp.missing_metrics, ["topic.recall"])
            with self.assertRaises(ValueError):
                EvalResultAdapter.compare_runs(base, other)
