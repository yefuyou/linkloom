"""Export synthetic, portable evidence without private paths or Provider payloads."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.self_improvement.run import digest, write
from scripts.self_improvement.contracts import BASELINE


def export(run, destination):
    run = run.resolve()
    if not run.is_relative_to((ROOT / "work/self-improvement-lab").resolve()):
        raise ValueError("Not an experiment in this worktree")
    report_path = run / "report.review.json" if (run / "report.review.json").exists() else run / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cache = report.pop("final_baseline_cache", None)
    if cache:
        report["final_baseline_cache"] = Path(cache).name
    protocol = json.loads((run / "protocol.json").read_text(encoding="utf-8"))
    for trial in report["trials"]:
        trial["config_diff"] = {field: {"before": BASELINE.to_dict()[field], "after": trial["candidate"][field]}
                                for field in ("query_mode", "read_mode", "limit")
                                if BASELINE.to_dict()[field] != trial["candidate"][field]}
    artifacts = {}
    folders = [run]
    if cache:
        folders.append(ROOT / cache)
    for folder in folders:
        for path in sorted(folder.rglob("raw.json")):
            if folder != run and path.parent != folder / "baseline/final":
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
            for row in value["cases"]:
                row.pop("artifact_root", None)
            key = ("cached-baseline/" if folder != run else "") + path.relative_to(folder).as_posix()
            artifacts[key] = {"original_sha256": digest(path), "execution": value}
    feedback = {path.relative_to(run).as_posix(): json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(run.glob("round-*/feedback.json"))}
    proposals = {path.relative_to(run).as_posix(): json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted(run.glob("round-*/proposals.json"))}
    evidence = {"experiment": run.name, "report_sha256": digest(report_path),
                "protocol": protocol, "report": report, "feedback": feedback,
                "proposals": proposals, "artifacts": artifacts,
                "regression": report["regression"],
                "implementation_sha256": {path.name: digest(path) for path in Path(__file__).parent.glob("*.py")}}
    live = run / "live_verification.json"
    if live.exists():
        evidence["live_verification"] = json.loads(live.read_text(encoding="utf-8"))
    write(destination, evidence)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.run, args.output)
