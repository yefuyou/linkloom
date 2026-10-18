"""Freeze synthetic predictions, then score endpoint relevance. No Provider I/O."""
from argparse import ArgumentParser
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.task_candidate_support import INPUT, public_cases, run_case, version_trials, five_line_safety_trial


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8", newline="\n")


def text_hash(path):
    """UTF-8/LF digest, stable across Git's Windows checkout newline conversion."""
    return hashlib.sha256(path.read_text(encoding="utf8").encode("utf8")).hexdigest()


def score(predictions, gold, k):
    metrics, failures = {}, []
    for method in ("baseline", "discovery"):
        counts = Counter()
        case_rows = []
        for prediction in predictions:
            case_id = prediction["case_id"]
            expected = set(gold["cases"][case_id]["targets"])
            explicit = set(gold["cases"][case_id]["explicit_references"])
            rows = prediction[method][:k]
            targets = {r["target"] for r in rows}
            counts.update(gold_targets=len(expected), retrieved_gold=len(targets & expected),
                          candidates=len(rows), unique_targets=len(targets),
                          noise=sum(r["target"] not in expected for r in rows),
                          explicit_total=len(explicit), explicit_missed=len(explicit - targets),
                          empty_queries=not expected, contaminated_empty_queries=not expected and bool(rows))
            row = dict(case_id=case_id, returned=[r["target"] for r in rows], missed=sorted(expected - targets),
                       noise=sorted(targets - expected), explicit_missed=sorted(explicit - targets))
            case_rows.append(row)
            if row["missed"] or row["noise"]:
                failures.append(dict(method=method, k=k, **row))
        metrics[method] = dict(counts= dict(counts), recall_at_k=counts["retrieved_gold"] / counts["gold_targets"],
                               noise_rate=counts["noise"] / counts["candidates"] if counts["candidates"] else 0,
                               mean_candidates=counts["candidates"] / len(predictions), cases=case_rows)
    return metrics, failures


def evaluate(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / "work"):
        raise ValueError("use a fresh task-owned work/ output directory")
    output.mkdir(parents=True, exist_ok=False)
    predictions = [run_case(case, k=5) for case in public_cases()]
    prediction_path = output / "predictions.json"
    write(prediction_path, predictions)
    # Only the scorer can see Gold, after public-input predictions are on disk.
    gold_path = ROOT / "tests/fixtures/task_candidate_discovery/gold/relevance.json"
    gold = json.loads(gold_path.read_text(encoding="utf8"))
    comparisons, bad_cases = {}, []
    for k in (1, 3, 5):
        comparisons[str(k)], failures = score(predictions, gold, k)
        bad_cases.extend(failures)
    report = dict(schema="task-candidate-evaluation/v1", queries=len(predictions),
                  metric="micro endpoint Candidate Recall@K; candidates consume hint-specific slots",
                  scope="synthetic discovery relevance only; not relation truth or end-to-end Agent success",
                  real_provider_calls=0, old_heldout_executed=False,
                  hash_encoding="UTF-8 with LF newlines", input_sha256=text_hash(INPUT),
                  prediction_sha256=text_hash(prediction_path), gold_sha256=text_hash(gold_path), comparisons=comparisons)
    write(output / "metrics.json", report)
    write(output / "bad-cases.json", bad_cases)
    write(output / "version-trials.json", version_trials())
    write(output / "original-five-lines.json", five_line_safety_trial())
    return report


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.output)
    print(json.dumps({"queries": result["queries"], "at_5": {
        method: {k: v for k, v in metrics.items() if k != "cases"}
        for method, metrics in result["comparisons"]["5"].items()}}, indent=2))
