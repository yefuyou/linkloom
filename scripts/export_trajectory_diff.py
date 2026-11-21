"""Export a single-file Trajectory Diff HTML from two persisted runs (offline, read-only).

  python scripts/export_trajectory_diff.py demo --out OUT_DIR
      Execute two runs through RuntimeEngine.start_multi_agent with the offline
      scripted test provider (a mock model, not real inference) on a synthetic
      vault, then export OUT_DIR/trajectory_diff.html.

  python scripts/export_trajectory_diff.py export --out FILE \
      --a TRACE_ROOT RUN_ID CHECKPOINT_DIR --b TRACE_ROOT RUN_ID CHECKPOINT_DIR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from linkloom.observability.trajectory_diff import diff_runs, load_run  # noqa: E402
from linkloom.observability.trajectory_diff_html import render_html  # noqa: E402

DEMO_MODE = "RuntimeEngine.start_multi_agent, scripted offline PolicyProvider (mock model, no real inference)"
DEMO_SOURCE = "tests/integration/test_m04_production_e2e_resume.py PolicyProvider on a 2-note synthetic vault"


def run_demo(out_dir: Path) -> list[dict]:
    from linkloom.runtime.graph import RuntimeEngine
    from linkloom.runtime.models import RunRequest
    from linkloom.scanner import scan_vault
    from tests.integration.test_m04_production_e2e_resume import PolicyProvider

    vault = out_dir / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    for name in ("alpha", "beta"):
        (vault / f"{name}.md").write_text(f"# {name}\n\nShared evidence for {name}.\n", encoding="utf-8")
    index = scan_vault(vault, out_dir / "scan").index_path
    runs = []
    for label, read in (("A", False), ("B", True)):
        checkpoints = out_dir / f"checkpoints_{label}"
        engine = RuntimeEngine(
            vault_root=vault, index_path=index, checkpoint_dir=checkpoints,
            trace_dir=out_dir / "traces", model=PolicyProvider(read=read),
        )
        state = engine.start_multi_agent(RunRequest(
            request_id=f"traj-diff-{label}", thread_id=f"traj-diff-{label}", workflow="ask",
            query="shared", max_steps=12, max_provider_requests=8,
        ))
        print(f"Run {label}: {state.status} run_id={state.run_id} read_note_tool={read}")
        runs.append(load_run(label, out_dir / "traces", state.run_id, checkpoints,
                             execution_mode=DEMO_MODE, data_source=DEMO_SOURCE, is_mock=True))
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--out", type=Path, required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--out", type=Path, required=True)
    for flag in ("--a", "--b"):
        exp.add_argument(flag, nargs=3, metavar=("TRACE_ROOT", "RUN_ID", "CHECKPOINT_DIR"), required=True)
    args = parser.parse_args()
    if args.cmd == "demo":
        args.out.mkdir(parents=True, exist_ok=True)
        runs = run_demo(args.out)
        target = args.out / "trajectory_diff.html"
    else:
        runs = [
            load_run(label, *spec, execution_mode="user supplied", data_source="user supplied")
            for label, spec in (("A", args.a), ("B", args.b))
        ]
        target = args.out
    target.write_text(render_html(diff_runs(*runs)), encoding="utf-8")
    print(f"HTML: {target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
