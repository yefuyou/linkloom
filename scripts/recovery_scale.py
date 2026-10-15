"""Detailed offline recovery-path scale measurements.

This module deliberately delegates scenario construction and execution to the
existing ``scripts.reliability_scale`` harness.  It adds only timing probes for
the verified-read boundary and converts the existing timing buckets into an
additive report suitable for the recovery optimization milestone.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]

from scripts import reliability_scale as baseline  # noqa: E402

DEFAULT_FAKE_ONLY_DEADLINE_S = 30.0


def _detailed_measure(
    count: int,
    root: Path,
    *,
    fake_only_deadline_s: float = DEFAULT_FAKE_ONLY_DEADLINE_S,
) -> dict:
    """Run the original three-repeat measurement and add verified-read timing.

    ``reliability_scale.measure`` owns the synthetic fixture, no-Provider model,
    and the existing snapshot/scan/read/retrieval probes.  This wrapper observes
    only ``RuntimeAgentAdapter._read_verified_note_for_tool`` and the nested
    ``VaultReader.read_notes`` calls so the additional bucket is derived from
    runtime evidence rather than an estimated constant.
    """

    if fake_only_deadline_s <= 0:
        raise ValueError("fake_only_deadline_s must be positive")

    from run_bounded_multihop import SourceOnlyController
    from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
    from linkloom.loader import VaultReader
    from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
    from linkloom.ui.investigation import ProductInvestigationService

    original_start = ProductInvestigationService.start
    original_inspect = ProductInvestigationService.inspect
    original_controller_init = SourceOnlyController.__init__
    original_verified_read = RuntimeAgentAdapter._read_verified_note_for_tool
    original_read_notes = VaultReader.read_notes
    original_search = RuntimeRetrievalBackend.search
    states: list[dict] = []
    active: list[dict | None] = [None]
    active_lock = threading.Lock()
    local = threading.local()

    def observed_start(service, *args, **kwargs):
        state = {
            "verifiedread_count": 0,
            "verifiedread_inclusive_ms": 0.0,
            "verifiedread_nested_read_notes_ms": 0.0,
            "verifiedread_ms": 0.0,
            "retrieval_inclusive_ms": 0.0,
            "retrieval_nested_read_notes_ms": 0.0,
            "final_kind": None,
            "error": None,
        }
        with active_lock:
            states.append(state)
            active[0] = state
        return original_start(service, *args, **kwargs)

    def extended_controller_init(controller, *args, **kwargs):
        original_controller_init(controller, *args, **kwargs)
        # SourceOnlyController is synthetic-only. Move its local stopwatch
        # backwards so its existing 30-second check behaves as the requested
        # experimental deadline without changing production max_steps/policy.
        controller.started += fake_only_deadline_s - DEFAULT_FAKE_ONLY_DEADLINE_S

    def observed_inspect(service, *args, **kwargs):
        result = original_inspect(service, *args, **kwargs)
        if result is not None and result.get("kind") != "running":
            with active_lock:
                state = active[0]
                if state is not None:
                    state["final_kind"] = result.get("kind")
                    error = result.get("error")
                    state["error"] = error if isinstance(error, dict) else None
        return result

    def observed_read_notes(reader, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_read_notes(reader, *args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            for frame in getattr(local, "verified_frames", ()):
                frame["nested_read_notes_ms"] += elapsed_ms
            for frame in getattr(local, "retrieval_frames", ()):
                frame["nested_read_notes_ms"] += elapsed_ms

    def observed_search(backend, *args, **kwargs):
        frame = {"nested_read_notes_ms": 0.0}
        frames = getattr(local, "retrieval_frames", None)
        if frames is None:
            frames = []
            local.retrieval_frames = frames
        frames.append(frame)
        started = time.perf_counter()
        try:
            return original_search(backend, *args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            frames.pop()
            with active_lock:
                state = active[0]
                if state is not None:
                    state["retrieval_inclusive_ms"] += elapsed_ms
                    state["retrieval_nested_read_notes_ms"] += frame["nested_read_notes_ms"]

    def observed_verified_read(adapter, *args, **kwargs):
        frame = {"nested_read_notes_ms": 0.0}
        frames = getattr(local, "verified_frames", None)
        if frames is None:
            frames = []
            local.verified_frames = frames
        frames.append(frame)
        started = time.perf_counter()
        try:
            return original_verified_read(adapter, *args, **kwargs)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            frames.pop()
            exclusive_ms = max(0.0, elapsed_ms - frame["nested_read_notes_ms"])
            with active_lock:
                state = active[0]
                if state is not None:
                    state["verifiedread_count"] += 1
                    state["verifiedread_inclusive_ms"] += elapsed_ms
                    state["verifiedread_nested_read_notes_ms"] += frame["nested_read_notes_ms"]
                    state["verifiedread_ms"] += exclusive_ms

    # The original harness installs its own timing wrapper around
    # VaultReader.read_notes.  Installing this probe outside that wrapper keeps
    # its stage totals intact while allowing us to attribute nested read time.
    with ExitStack() as stack:
        stack.enter_context(patch.object(ProductInvestigationService, "start", observed_start))
        stack.enter_context(patch.object(ProductInvestigationService, "inspect", observed_inspect))
        stack.enter_context(patch.object(SourceOnlyController, "__init__", extended_controller_init))
        stack.enter_context(
            patch.object(RuntimeAgentAdapter, "_read_verified_note_for_tool", observed_verified_read)
        )
        stack.enter_context(patch.object(VaultReader, "read_notes", observed_read_notes))
        stack.enter_context(patch.object(RuntimeRetrievalBackend, "search", observed_search))
        result = baseline.measure(count, root)

    runs = result["runs"]
    if len(states) != len(runs):
        raise RuntimeError(
            f"measurement observer mismatch: observed {len(states)} starts for {len(runs)} runs"
        )

    detailed_runs = []
    for run, state in zip(runs, states):
        raw = run["stages"]
        stages = {
            "snapshot": {
                "count": raw["snapshot_ms"]["count"],
                "total_ms": raw["snapshot_ms"]["total_ms"],
            },
            "scan": {
                "count": raw["scan_vault_ms"]["count"],
                "total_ms": raw["scan_vault_ms"]["total_ms"],
            },
            "read_notes": {
                "count": raw["read_notes_ms"]["count"],
                "total_ms": raw["read_notes_ms"]["total_ms"],
            },
            "retrieval": {
                "count": raw["retrieval_ms"]["count"],
                "total_ms": max(0.0, state["retrieval_inclusive_ms"]
                                - state["retrieval_nested_read_notes_ms"]),
                "inclusive_ms": state["retrieval_inclusive_ms"],
                "nested_read_notes_ms": state["retrieval_nested_read_notes_ms"],
            },
            "verifiedread": {
                "count": state["verifiedread_count"],
                "total_ms": state["verifiedread_ms"],
                "inclusive_ms": state["verifiedread_inclusive_ms"],
                "nested_read_notes_ms": state["verifiedread_nested_read_notes_ms"],
            },
        }
        accounted_ms = sum(value["total_ms"] for value in stages.values())
        other_ms = max(0.0, run["elapsed_ms"] - accounted_ms)
        stages["other"] = {"count": 1, "total_ms": other_ms}
        stages["e2e"] = {"count": 1, "total_ms": run["elapsed_ms"]}
        detailed_runs.append({
            **run,
            "stages": stages,
            "error": state["error"],
        })

    result["runs"] = detailed_runs
    result["stage_definition"] = {
        "snapshot": "ProductApplication.snapshot_investigation_sources",
        "scan": "linkloom.ui.investigation.scan_vault",
        "read_notes": "all VaultReader.read_notes calls, including nested calls",
        "retrieval": "exclusive RuntimeRetrievalBackend.search after nested read_notes",
        "verifiedread": "exclusive time in _read_verified_note_for_tool after nested read_notes",
        "other": "e2e minus the five exclusive buckets, clamped at zero",
        "e2e": "ProductInvestigationService.start through final successful inspect",
    }
    result["fake_only_controller_deadline_s"] = fake_only_deadline_s
    result["median_stage_ms"] = {
        name: statistics.median(run["stages"][name]["total_ms"] for run in detailed_runs)
        for name in ("snapshot", "scan", "read_notes", "retrieval", "verifiedread", "other", "e2e")
    }
    return result


def run(
    output: Path,
    *,
    fake_only_deadline_s: float = DEFAULT_FAKE_ONLY_DEADLINE_S,
) -> list[dict]:
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for count in (10, 100, 500):
        row = _detailed_measure(
            count,
            output / str(count),
            fake_only_deadline_s=fake_only_deadline_s,
        )
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (output / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return rows


if __name__ == "__main__":
    import os

    os.environ["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "work/agent-recovery/scale-before",
    )
    parser.add_argument(
        "--fake-only-deadline",
        type=float,
        default=DEFAULT_FAKE_ONLY_DEADLINE_S,
        help="Synthetic SourceOnlyController deadline; does not change production limits.",
    )
    args = parser.parse_args()
    run(args.output, fake_only_deadline_s=args.fake_only_deadline)
