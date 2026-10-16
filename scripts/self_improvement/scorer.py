"""Oracle boundary: invoked only after the public-only worker exits."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/integration"), str(ROOT)]
from run_real_agent_reliability import _score_row


def score(raw, gold):
    expected = {row["id"]: row["expected"] for row in gold["cases"]}
    if set(expected) != {row["id"] for row in raw["cases"]}:
        raise ValueError("Gold/public case ids differ")
    scores, refusals = [], []
    for row in raw["cases"]:
        oracle = dict(expected[row["id"]])
        oracle["facts"] = oracle.pop("values")
        scores.append({"id": row["id"], **_score_row({"selected": "lab", "baselines": {
            "lab": {**row, "facts": row["values"]}}}, oracle)})
        scores[-1].update(calls=len(row["calls"]), model_calls=row["model_turns"],
                          context_bytes=row["context_bytes"], context_total_bytes=row["context_total_bytes"],
                          outcome=row["outcome"], reason=row["reason"],
                          bindings_verified=row["bindings_verified"], workspace_valid=row["workspace_valid"],
                          safety_violations=row["safety_violations"], run_status=row.get("run_status"),
                          error_code=(row.get("error") or {}).get("code"))
        if oracle["outcome"] != "complete":
            refusals.append(scores[-1]["pass"])
    rows = raw["cases"]
    metric = {"accuracy": sum(row["pass"] for row in scores) / len(rows),
              "grounding": sum(row["claim_supported"] and row["citation_valid"] and
                               row["bindings_verified"] for row in rows) / len(rows),
              "refusal_accuracy": sum(refusals) / len(refusals) if refusals else 1.,
              "calls": sum(len(row["calls"]) for row in rows),
              "model_calls": sum(row["model_turns"] for row in rows),
              "context_bytes": sum(row["context_bytes"] for row in rows),
              "context_total_bytes": sum(row["context_total_bytes"] for row in rows),
              "safety_violations": sum(row["safety_violations"] for row in rows),
              "complete": sum(row["outcome"] == "complete" for row in rows), "total": len(rows)}
    metric["runtime_failures"] = sum(row.get("run_status") not in {None, "completed"} for row in rows)
    return {"metrics": metric, "cases": scores}


def main():
    parser = argparse.ArgumentParser()
    for field in ("raw", "gold", "output"):
        parser.add_argument("--" + field, type=Path, required=True)
    args = parser.parse_args()
    result = score(json.loads(args.raw.read_text(encoding="utf-8")),
                   json.loads(args.gold.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
